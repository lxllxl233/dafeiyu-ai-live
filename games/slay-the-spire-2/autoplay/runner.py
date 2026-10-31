"""让她玩：启动游戏后自动跑一局。

两种模式共用同一个循环，区别只在「谁做决定」：

- ``--mode llm``：**所有玩家决策都交给 LLM**——战斗出牌（`seq` → act.py）和界面点击
  （`act`/`i` → decision/actions.py）。本地启发式只在 LLM 超时/输出不可执行时兜底，
  并写 ``llm_fallback`` 进日志；主菜单/角色选择这类过场仍是本地管道，不算决策。
- ``--mode local``：全走本地（离线验收用），战斗用 `decision/advisor.py` 算账。

所有进展由核心 ``AutoplayHost`` 写 `logs/autoplay.json`，桌面右键和管理端都能看；
停手通过 `logs/autoplay.stop` 跨进程通知，不依赖同一个 Python 进程。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from dafeiyu import paths
from dafeiyu.agent.host import AutoplayHost
from dafeiyu.recording import policy as recording_policy

from ..decision.speech import tidy_say as _tidy_say
from ..decision.prompts import (NONCOMBAT_PROMPT, SYSTEM_PROMPT, combat_goal,
                                noncombat_goal, screen_label)
from ..start_options import FALLBACK as _START_FALLBACK
from ..start_options import load as _load_start_options

GAME_ID = "slay-the-spire-2"

_host = AutoplayHost(GAME_ID)
# 兼容旧测试/调试脚本的模块级入口；真正状态在 host 里。
STATUS_PATH = _host.status_path
STOP_PATH = _host.stop_path
LOG_PATH = _host.log_path
_lock = _host.lock
_stop = _host.stop_event
_state = _host.state
_speech_until = 0.0
_thread: threading.Thread | None = None
# 牌组多选屏已经点到第几张：mod 每点一次选一张，重复点同一张会取消，
# 所以必须 0、1、2… 顺序点（run-0113 的 6 小时空转就是一直点 0）。
_deck_clicks: dict[str, int] = {}
_timeline_prev: dict = {"screen": "", "floor": 0, "run_id": ""}
# 最近说过的台词（文本 → 时间）；同句 4 分钟内不再重复推给 overlay。
_recent_speech: dict[str, float] = {}
# 最近说过的开头（前 12 字 → 时间）；同一开场白 3 分钟内不再重复。
_recent_openers: dict[str, float] = {}


def bind_host(host: AutoplayHost) -> AutoplayHost:
    """由核心 facade/插件显式注入 host；替换时同步旧测试用的模块别名。

    已经有一局在跑时拒绝换 host：``play.status()`` 之类的只读调用以前会
    新建 host 再 bind，模块级 ``_host`` 被换成默认 state（running=False/pid=0），
    下一笔状态就把直播状态写成「已停手」（run 16:18 实测）。
    """
    global _host, _lock, _stop, _state, _speech_until
    global STATUS_PATH, STOP_PATH, LOG_PATH
    if _thread is not None and _thread.is_alive() and host is not _host:
        _log("bind_host_ignored", reason="autoplay thread alive")
        return _host
    _host = host
    _lock = host.lock
    _stop = host.stop_event
    _state = host.state
    _speech_until = host.speech_until
    STATUS_PATH = host.status_path
    STOP_PATH = host.stop_path
    LOG_PATH = host.log_path
    return host


def _with_liveness(updates: dict) -> dict:
    """给状态补上 running/pid（只补缺省，显式传的不改）。

    `AutoplayHost.status()` 会把磁盘上的快照并回内存（文件说 false 就跟着 false），
    于是一次「半写」的 running=false/pid=0 快照会被后续每次 status() 采纳，
    之后所有写入都不带 running，直播看板一直显示已停手、CLI 等 600 秒退出。
    这里每次写都兜底补上，任何一次 clobber 都会在下一步自动修回来。
    """
    out = dict(updates or {})
    try:
        alive = _thread is not None and _thread.is_alive()
    except Exception:  # noqa: BLE001
        alive = False
    if alive and "running" not in out and not _stopping():
        out["running"] = True
        out.setdefault("pid", os.getpid())
    # 别的进程/控制台 reset 会把 recording 清成 false/空路径；本进程还在录就补回来，
    # 免得看板显示没录、实际 OBS 一直在写。
    if alive and _rec_active:
        out.setdefault("recording", True)
        if _rec_path:
            out.setdefault("record_path", _rec_path)
    return out


def _write_status(**updates) -> dict:
    return _host.set_status(**_with_liveness(updates))


def _log(msg: str, **data) -> None:
    _host.log(msg, **data)


def _stopping() -> bool:
    return _host.stopping() or _stop.is_set() or STOP_PATH.exists()


def _add_llm_usage(usage: dict | None, run_id: str = "", note: str = "") -> dict:
    """记一笔 API token；note 说明是哪一步（战斗/地图/事件…），控制台按它出账单。"""
    return _host.add_usage(usage, run_id, note=note,
                           step=int(_state.get("step") or 0) + 1)


def _pid_alive(pid: int | None) -> bool:
    return _host.pid_alive(pid)


def _import_tools():
    """延迟导入：桌宠启动时不加载游戏兼容层。"""
    decision = str(Path(__file__).resolve().parents[1] / "decision")
    if decision in sys.path:
        sys.path.remove(decision)
    sys.path.insert(0, decision)
    import advisor  # noqa: PLC0415
    import sts2  # noqa: PLC0415
    return sts2, advisor


def status() -> dict:
    return _host.status()


def _say(text: str) -> None:
    def _sender(line: str) -> None:
        sts2, _ = _import_tools()
        sts2.overlay_say("思路", line, "")

    _host.say(text, sender=_sender)


# 录像是否在录，以这里为准：状态文件可能被别的进程 reset 掉（实机见过
# recording=False/record_path="" 但 OBS 还在录），拿它做切段判断会漏停。
_rec_active = False
_rec_path = ""


def _start_autoplay_recording(game: str, mode: str) -> dict:
    global _rec_active, _rec_path
    res = _host.start_recording(note=f"自动玩 · {mode}")
    _rec_active = bool((res or {}).get("ok"))
    if _rec_active:
        _rec_path = str((res or {}).get("path") or "")
    return res


def _recording_retryable(res: dict) -> bool:
    """策略关闭 / 总开关关闭不是 OBS 故障，别每 6 秒重试一次。"""
    return not (res.get("skipped") or res.get("disabled"))


def _finish_autoplay_recording(result: str = "") -> dict:
    global _rec_active, _rec_path
    res = _host.stop_recording(result)
    if (res or {}).get("ok"):
        _rec_active = False
        _rec_path = ""
    return res


# 每局一个视频：OBS 一直录的话，一晚上连播十几局会全塞进同一个文件。
# 换 run_id 时收尾旧录像、开新的一段；OBS 万一开不了，隔几秒重试一次。
_rec_run_id: str | None = None
_rec_game = ""
_rec_mode = ""
_rec_pending = 0.0
_rec_split = "run"


def _maybe_split_recording(state: dict) -> None:
    global _rec_run_id, _rec_pending, _rec_active
    # 开录失败（OBS 500 / 还没落盘）不阻断自动玩，隔几秒再试。
    if _rec_pending and time.time() >= _rec_pending and not _rec_active:
        _rec_pending = 0.0
        res = _start_autoplay_recording(_rec_game, _rec_mode) or {}
        _log("record_retry", ok=bool(res.get("ok")),
             error=str(res.get("error") or "")[:120])
        if not res.get("ok") and _recording_retryable(res):
            _rec_pending = time.time() + 6.0
    # 策略是「不分集」：整段自动玩只保留一个录像文件，run_id/GAME_OVER 都不切。
    if _rec_split == "none":
        return
    rid = str((state.get("run") or {}).get("run_id") or "")
    if not rid:
        # STS2 mod 的状态里没有 run_id（只有 floor/角色/卡组）。切段以
        # GAME_OVER 为界，在死亡重开那一支里换新视频，别指望这个字段。
        return
    floor = _num((state.get("run") or {}).get("floor"), 0)
    if _rec_run_id is None:
        _rec_run_id = rid          # 启动时接的是哪一局，就跟着这一局录
    elif rid != _rec_run_id:
        _rec_run_id = rid
        if _rec_active:
            stop = _finish_autoplay_recording("run_end") or {}
            res = _start_autoplay_recording(_rec_game, _rec_mode) or {}
            if res.get("ok"):
                _log("record_split", run_id=rid, floor=floor,
                     path=str(res.get("path") or ""))
            else:
                _log("record_split_failed", run_id=rid, floor=floor,
                     stop_ok=bool(stop.get("ok")),
                     error=str(res.get("error") or "")[:160])
        else:
            # 上一段本来就没在录（OBS 未就绪等）：直接开新的，不等收尾。
            res = _start_autoplay_recording(_rec_game, _rec_mode) or {}
            _log("record_new_run", ok=bool(res.get("ok")), floor=floor,
                 path=str(res.get("path") or ""), error=str(res.get("error") or "")[:120])
        if not res.get("ok"):
            _rec_pending = time.time() + 6.0


def _record_new_run() -> None:
    """死亡重开新一局：换一个新视频（一局一个视频）。"""
    global _rec_pending
    if _rec_split == "none":
        return
    res = _start_autoplay_recording(_rec_game, _rec_mode) or {}
    _log("record_new_run", ok=bool(res.get("ok")),
         path=str(res.get("path") or ""), error=str(res.get("error") or "")[:120])
    _rec_pending = (0.0 if res.get("ok") or not _recording_retryable(res)
                    else time.time() + 6.0)


def _settings() -> tuple[str, str]:
    return _host.settings()


def _remember_speech(text: str) -> None:
    global _speech_until
    _host.remember_speech(text)
    _speech_until = _host.speech_until


def _say_screen(sts2, text: str, screen: str, state: dict | None = None) -> None:
    """界面决策的解说也要推给 overlay 真说话。

    旧实现只 ``_remember_speech``：计时窗口有了，但没有任何东西推给 overlay，
    于是营火/宝箱/选牌/商店/事件这些屏角色一声不吭（实机反馈）。
    """
    text = " ".join(str(text or "").split())
    if not text:
        return
    # 同一句话 4 分钟内不重复播：B 站把「长时间重复同一内容/挂机」算 B 类
    # 无意义内容，卡屏时同一句台词刷上百遍是很明显的机器特征。
    now = time.time()
    last = _recent_speech.get(text)
    if last is not None and now - last < 240:
        _log("speech_repeat_skipped", screen=screen, text=text[:60])
        return
    # 「卡组还是 C 级，缺防缺 AOE…」这种状态开场白，换了句尾也会连着刷：
    # 同一个开头 2 分钟内只说一次（开口 6 字），后面的屏换个说法（或直说这屏的取舍）。
    opener = text[:6]
    seen = _recent_openers.get(opener)
    if seen is not None and now - seen < 120:
        _log("speech_opener_skipped", screen=screen, text=text[:60])
        return
    _recent_speech[text] = now
    _recent_openers[opener] = now
    if len(_recent_openers) > 200:
        for line, ts in list(_recent_openers.items()):
            if now - ts > 900:
                _recent_openers.pop(line, None)
    if len(_recent_speech) > 200:
        for line, ts in list(_recent_speech.items()):
            if now - ts > 900:
                _recent_speech.pop(line, None)
    _remember_speech(text)
    try:
        tag_for = getattr(sts2, "tag_for", None)
        tag = tag_for(screen) if callable(tag_for) else "思路"
        try:
            sts2.overlay_say(tag, text, "", state)
        except TypeError:
            sts2.overlay_say(tag, text, "")
    except Exception as exc:  # noqa: BLE001
        _log("say_failed", screen=screen, error=f"{type(exc).__name__}: {exc}")


def _sleep(seconds: float) -> None:
    _host.sleep(seconds)


def _pace() -> None:
    _host.wait_voice()


def _call(sts2, fn, *args, **kwargs):
    """跑会打印的 sts2 函数，吞掉 stdout，保留结果。

    CLI 层用 ``sys.exit(2/3)`` 表示「这批动作不能发」（例如牌不可打、目标已死）。
    它抛的是 ``SystemExit``（BaseException），不接住会绕过主循环的异常兜底、
    直接记成 ``ERROR SystemExit: 3`` 然后被判卡住——run-0114 的 BOSS 眩晕就死在这。
    这里统一转成 ``RuntimeError``，让上层按普通错误处理。
    """
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            result = fn(*args, **kwargs)
    except SystemExit as exc:
        _log("cli_exit", code=getattr(exc, "code", 0), fn=getattr(fn, "__name__", "?"))
        raise RuntimeError(f"{getattr(fn, '__name__', 'cli')} exit {getattr(exc, 'code', 0)}") from None
    return result, buf.getvalue()


def _acts(state: dict) -> list[str]:
    out = []
    for a in state.get("available_actions") or []:
        if isinstance(a, str):
            out.append(a)
        elif isinstance(a, dict):
            name = a.get("name") or a.get("action")
            if name:
                out.append(str(name))
    return out


def _screen(state: dict) -> str:
    return str(state.get("screen") or "").upper()


def _run_meta(state: dict) -> tuple[int, int, int]:
    """(楼层, 当前 HP, 最大 HP)。

    战斗中以 ``combat.player`` 为准：mod 的 ``run.current_hp`` 在战斗内偶尔会慢
    一拍（BOSS 眩晕那局 overlay 显示 76、实际已经 64），有偏差时记一条
    ``hp_mismatch`` 方便以后回查。
    """
    run = state.get("run") or {}
    hp = int(run.get("current_hp") or run.get("hp") or 0)
    max_hp = int(run.get("max_hp") or 0)
    if _screen(state) == "COMBAT":
        player = (state.get("combat") or {}).get("player") or {}
        combat_hp = player.get("current_hp")
        if combat_hp is not None and int(combat_hp) != hp:
            _log("hp_mismatch", run=hp, combat=int(combat_hp),
                 run_max=max_hp, combat_max=int(player.get("max_hp") or 0))
            hp = int(combat_hp)
            max_hp = int(player.get("max_hp") or max_hp)
    return (int(run.get("floor") or 0), hp, max_hp)


def _timeline_event(kind: str, label: str = "", **extra) -> None:
    """给录像时间轴写一条事件；没录像时安静跳过。"""
    _host.event(kind, label, **extra)


def _record_screen_transition(state: dict, last: str = "") -> None:
    """只在画面/楼层/局号变化时标章节，不把每一步都刷进时间轴。"""
    screen = _screen(state)
    if not screen:
        return
    floor, hp, max_hp = _run_meta(state)
    run_id = str(state.get("run_id") or (state.get("run") or {}).get("run_id") or "")
    prev = _timeline_prev
    if run_id and run_id != prev.get("run_id"):
        if prev.get("run_id"):
            _timeline_event("new_run", f"新的一局 · {run_id}",
                            run_id=run_id, floor=floor, screen=screen)
        prev["run_id"] = run_id
    if screen != prev.get("screen"):
        if screen == "COMBAT":
            kind, label = "combat_start", f"第 {floor} 层 · 战斗开始"
        elif prev.get("screen") == "COMBAT":
            kind, label = "combat_end", f"第 {floor} 层 · 战斗结束"
        elif screen == "GAME_OVER":
            kind, label = "game_over", "本局结束"
        else:
            kind = {
                "CARD_SELECTION": "card", "REWARD": "reward", "MAP": "map",
                "EVENT": "event", "REST": "rest", "SHOP": "shop",
                "CHEST": "chest", "MAIN_MENU": "menu",
                "CHARACTER_SELECT": "character",
            }.get(screen, "screen")
            names = {
                "card": "选牌", "reward": "奖励", "map": "地图",
                "event": "事件", "rest": "营火", "shop": "商店",
                "chest": "宝箱", "menu": "主菜单", "character": "选角色",
            }
            label = f"第 {floor} 层 · {names.get(kind, screen)}"
            if last and not last.startswith(("MAP ", "WAIT", "LLM")):
                label += f" · {last}"
        _timeline_event(kind, label[:160], screen=screen, floor=floor,
                        hp=hp, max_hp=max_hp, run_id=run_id)
        prev["screen"] = screen
    elif floor != prev.get("floor"):
        _timeline_event("floor", f"进入第 {floor} 层", screen=screen,
                        floor=floor, hp=hp, max_hp=max_hp, run_id=run_id)
    prev["floor"] = floor


def _set_from_state(state: dict, last: str = "") -> None:
    floor, hp, max_hp = _run_meta(state)
    _write_status(screen=_screen(state), floor=floor, hp=hp, max_hp=max_hp,
                  last=last or _screen(state))
    _record_screen_transition(state, last)


# ---------------------------------------------------------------- 开局
# 控制台「开局设置」写 config.json 的 start_options；这里按 mtime 缓存，
# 每局开新局前读一次就够，不跟 3 秒轮询抢文件。
_start_options_cache: dict = {"sig": object(), "values": dict(_START_FALLBACK)}


def _start_options() -> dict:
    """当前生效的角色/进阶（控制台可改）；读失败回退铁甲 + A10。"""
    try:
        stat = paths.CONFIG_FILE.stat()
        sig = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        sig = None
    if sig != _start_options_cache["sig"]:
        _start_options_cache["values"] = dict(_load_start_options())
        _start_options_cache["sig"] = sig
    return dict(_start_options_cache["values"])


def _character_index(cs: dict, character_id: str) -> int | None:
    wanted = str(character_id or "").strip().upper()
    if not wanted:
        return None
    for item in cs.get("characters") or ():
        if not isinstance(item, dict):
            continue
        if str(item.get("character_id") or "").strip().upper() != wanted:
            continue
        try:
            return int(item.get("index"))
        except (TypeError, ValueError):
            continue
    return None


def _set_ascension(sts2, state: dict, cs: dict, target: int) -> tuple[dict, dict]:
    """把选人界面的进阶调到 target；游戏上限更低时停在上限。

    两个方向都走：游戏会记住上次的进阶，所以“想打 A0”必须能降下来。
    """
    asc = int(cs.get("ascension") or 0)
    mx = int(cs.get("max_ascension") or 0)
    target = max(0, int(target or 0))
    if mx > 0:
        target = min(target, mx)
    action = "increase_ascension" if asc < target else "decrease_ascension"
    for _ in range(abs(target - asc) + 1):
        if asc == target or action not in _acts(state):
            break
        sts2.act(action)
        state = sts2.get_state()
        cs = state.get("character_select") or {}
        new_asc = int(cs.get("ascension") or 0)
        if new_asc == asc:      # 按钮点了没反应：别再空转
            break
        asc = new_asc
        action = "increase_ascension" if asc < target else "decrease_ascension"
    if asc != target:
        # 按钮不可用 / 点了没反应：照常开局，但把差距记下来，方便事后排查。
        _log("start_ascension_unmet", want=target, ascension=asc)
    return state, cs


def _ensure_run(sts2, state: dict, options: dict | None = None) -> dict | None:
    screen = _screen(state)
    acts = _acts(state)
    if screen == "GAME_OVER":
        for name in ("return_to_main_menu", "return_to_menu", "proceed",
                     "open_main_menu", "main_menu"):
            if name in acts:
                sts2.act(name)
                return sts2.get_state()
        return None
    if screen == "MAIN_MENU":
        for name in ("continue_run", "open_character_select", "abandon_run"):
            if name in acts:
                sts2.act(name)
                return sts2.get_state()
        return None
    if screen == "CHARACTER_SELECT":
        opts = dict(options) if isinstance(options, dict) else _start_options()
        want_char = str(opts.get("character") or _START_FALLBACK["character"])
        try:
            want_asc = int(opts.get("ascension", _START_FALLBACK["ascension"]))
        except (TypeError, ValueError):
            want_asc = int(_START_FALLBACK["ascension"])
        cs = state.get("character_select") or {}
        selected = str(cs.get("selected_character_id") or "")
        if selected.upper() != want_char.strip().upper():
            idx = _character_index(cs, want_char)
            if idx is None:
                # 角色没解锁 / 这个版本没有：保持当前选择照常开局，别卡死在选人界面。
                _log("start_character_unavailable", want=want_char, selected=selected)
            else:
                sts2.act("select_character", option_index=int(idx))
                state = sts2.get_state()
                cs = state.get("character_select") or {}
        state, cs = _set_ascension(sts2, state, cs, want_asc)
        if "embark" in _acts(state):
            _log("start_run", character=str(cs.get("selected_character_id") or want_char),
                 ascension=int(cs.get("ascension") or 0), want_ascension=want_asc)
            sts2.act("embark")
            return sts2.get_state()
    return None


# ---------------------------------------------------------------- 地图
def _map_nodes(state: dict) -> tuple[list[dict], list[dict]]:
    mp = state.get("map") or {}
    nodes = [n for n in (mp.get("nodes") or []) if isinstance(n, dict)]
    # 模组 choose_map_node 的 option_index 是 available_nodes 的下标，优先用它，
    # 别指望「nodes 过滤 is_available」的顺序和它一致。
    avail = [n for n in (mp.get("available_nodes") or []) if isinstance(n, dict)]
    if not avail:
        avail = [n for n in nodes if n.get("is_available")]
    return nodes, avail


def _route_plan(sts2) -> dict:
    path = Path(str(getattr(sts2, "PLAN_PATH", paths.LOGS / "route_plan.json")))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _route_current(plan: dict, state: dict) -> bool:
    """路线计划是否属于当前 run/act；没有身份的计划一律重算。"""
    ident = plan.get("identity") if isinstance(plan, dict) else None
    if not isinstance(ident, dict) or not ident:
        return False
    run = state.get("run") or {}
    run_id = str(state.get("run_id") or run.get("run_id") or "")
    act_id = str(run.get("act_id") if run.get("act_id") is not None
                 else run.get("act") if run.get("act") is not None else "")
    if ident.get("run_id") and run_id and str(ident["run_id"]) != run_id:
        return False
    if ident.get("act_id") is not None and act_id and str(ident["act_id"]) != act_id:
        return False
    map_sig = str(state.get("map_sig") or (state.get("map") or {}).get("map_sig") or "")
    if ident.get("map_sig") and map_sig and str(ident["map_sig"]) != map_sig:
        return False
    return True


def _route_next_coord(plan: dict, nodes: list[dict]):
    """计划里「下一格」坐标；当前节点不在计划里则取计划起点（用于判断是否偏航）。"""
    path = [p for p in (plan.get("path") or []) if isinstance(p, list) and len(p) >= 2]
    if not path:
        return None
    cur = next((n for n in nodes if n.get("is_current")), None)
    idx = None
    if cur is not None:
        idx = next((i for i, p in enumerate(path)
                    if int(p[0]) == int(cur.get("row", -1))
                    and int(p[1]) == int(cur.get("col", -1))), None)
    if idx is None:
        return (int(path[0][0]), int(path[0][1]))
    if idx + 1 >= len(path):
        return None
    return (int(path[idx + 1][0]), int(path[idx + 1][1]))


def _route_target(plan: dict, nodes: list[dict], avail: list[dict]):
    """取计划里下一格；计划起点是「下一次可选节点」，当前节点在路径里时取下一格。"""
    path = [p for p in (plan.get("path") or []) if isinstance(p, list) and len(p) >= 2]
    if not path:
        return None
    cur = next((n for n in nodes if n.get("is_current")), None)
    idx = None
    if cur is not None:
        idx = next((i for i, p in enumerate(path)
                    if int(p[0]) == int(cur.get("row", -1))
                    and int(p[1]) == int(cur.get("col", -1))), None)
    if idx is not None:
        if idx + 1 >= len(path):
            return None
        target = (int(path[idx + 1][0]), int(path[idx + 1][1]))
    else:
        target = (int(path[0][0]), int(path[0][1]))
    return next((n for n in avail
                 if (int(n.get("row", -1)), int(n.get("col", -1))) == target), None)


def _publish_route(sts2, state: dict, output: str = "", reason: str = "") -> dict:
    plan = _route_plan(sts2)
    seq = str(plan.get("seq") or "")
    option = plan.get("option")
    note = str(plan.get("note") or "")
    start = plan.get("start") or {}
    _write_status(route_seq=seq, route_option=option, route_note=note,
                  route_floor=int((state.get("run") or {}).get("floor") or 0))
    _log("route_plan", seq=seq, option=option, reason=reason,
         start=start, out=str(output or "")[-500:])
    _timeline_event("route", f"路线规划 {seq}".strip(),
                    route=seq, option=option, floor=_run_meta(state)[0])
    return plan


def _route_policy_ok(plan: dict) -> bool:
    """计划的精英政策和当前规则一致吗？旧代码写下的、带精英的 Act1 计划一律重算。"""
    if not isinstance(plan, dict):
        return False
    pol = plan.get("policy") or {}
    if not pol:
        return False
    if pol.get("elite_ok") is not False:
        return True
    path = [p for p in (plan.get("path") or []) if isinstance(p, list) and len(p) >= 3]
    if not any(str(p[2]) == "Elite" for p in path):
        return True
    return pol.get("forced") is True


def _ensure_route_plan(sts2, state: dict) -> dict:
    """MAP 上确保有一份**本局本幕**的路线计划；stale 就重规划并 commit。

    LLM 模式以前直接把 MAP 丢给模型、从不刷新 route_plan，于是计划文件可能
    还是上一局/上一幕的（日志里 ROUTE_PLAN_STALE 一直刷），模型只能走一步
    看一步。现在进 MAP 前先对齐计划，再交给 ctx/菜单当依据。
    """
    plan = _route_plan(sts2)
    if _route_current(plan, state) and plan.get("path") and _route_policy_ok(plan):
        # 偏航/计划下一格已经不在可选点里（上一格被改道、或在事件里跳了行）：
        # 立刻按当前可选点重排，别让模型拿着一份走不通的计划继续猜。
        nodes, avail = _map_nodes(state)
        nxt = _route_next_coord(plan, nodes)
        coords = {(n.get("row"), n.get("col")) for n in avail}
        if not coords or (nxt is not None and nxt in coords):
            return plan
    output = ""
    try:
        _, output = _call(sts2, sts2.cmd_route, commit=0)
    except Exception as exc:  # noqa: BLE001
        _log("route_failed", error=f"{type(exc).__name__}: {exc}")
        return plan if _route_current(plan, state) else {}
    return _publish_route(sts2, state, output, reason="auto")


def _choose_map(sts2, state: dict) -> str:
    nodes, avail = _map_nodes(state)
    if not avail:
        # 战斗/宝箱结算后的地图过场：节点表偶尔晚 1–2 秒才出现。直接报
        # MAP_NO_AVAILABLE 会被卡住守卫当成连续无进展收尾（f13 就停在这里）。
        for _ in range(10):
            time.sleep(0.4)
            try:
                state = sts2.get_state()
            except Exception:
                continue
            if _screen(state) != "MAP":
                break
            nodes, avail = _map_nodes(state)
            if avail:
                _write_status(screen=_screen(state))
                break
        if not avail:
            acts = set(_acts(state))
            for name in ("proceed", "continue", "embark"):
                if name in acts:
                    sts2.act(name)
                    return f"MAP {name}"
            return "WAIT_MAP"

    plan = _route_plan(sts2)
    target = _route_target(plan, nodes, avail) if _route_current(plan, state) else None
    if target is not None and not _state.get("route_seq"):
        # 计划是上一局/启动前生成的，只要身份和下一格仍有效就直接接着走，
        # 同时把路线代号补到控制台，避免“明明有计划但界面空着”。
        _publish_route(sts2, state, reason="reuse")
    if target is None:
        # 开局/换幕/换图/路线断了：只在这里规划一次并 commit，后面按计划走。
        plan = _ensure_route_plan(sts2, state)
        target = _route_target(plan, nodes, avail) if _route_current(plan, state) else None

    chosen = target
    if chosen is None:
        # 规划器失败才退回保守优先级，不让自动玩停在地图。
        _, hp, mhp = _run_meta(state)
        pct = hp / mhp if mhp else 1.0
        priority = {"RestSite": 0 if pct < 0.70 else 4,
                    "Shop": 1, "Monster": 2, "Event": 3, "Unknown": 3,
                    "Treasure": 2, "Elite": 8, "Boss": 9}
        chosen = min(avail, key=lambda n: priority.get(str(n.get("node_type")), 5))
        _log("route_fallback", seq=plan.get("seq"), chosen=chosen.get("node_type"))
    idx = avail.index(chosen)
    act = "choose_map_node"
    if act not in _acts(state):
        return "MAP_ACTION_MISSING"
    sts2.act(act, option_index=idx)
    _log("map", idx=idx, row=chosen.get("row"), col=chosen.get("col"),
         type=chosen.get("node_type"), route_seq=plan.get("seq"))
    return f"MAP {chosen.get('node_type')} ({chosen.get('row')},{chosen.get('col')})"


# ---------------------------------------------------------------- 战斗
def _sync_llm_planner(planner) -> None:
    """右键/控制台改模型后，不必重开整局，下一个 LLM 回合就换。"""
    if planner is None:
        return
    try:
        from dafeiyu.core.config import LiveConfig  # noqa: PLC0415

        cfg = LiveConfig.load()
        want = str(cfg.autoplay_llm_model or cfg.llm_model or "deepseek-chat")
        if getattr(planner, "model", "") != want:
            planner.model = want
            planner.cfg = cfg
            _log("llm_model_switch", model=want)
    except Exception:
        pass


def _combat_llm(tools, planner, state: dict, game: str) -> str:
    """战斗回合交给 LangGraph + LLM；非战斗界面仍用本地快速处理。"""
    from dafeiyu.agent.graph import run_turn  # noqa: PLC0415

    _sync_llm_planner(planner)

    floor, hp, max_hp = _run_meta(state)
    # 战斗中以 combat.player 的 HP 为准（run.current_hp 偶尔慢一拍），保证
    # 提示词/字幕里的血量与画面上的一致。
    goal = combat_goal({"floor": floor, "current_hp": hp, "max_hp": max_hp})
    _write_status(last="LLM 思考…")
    try:
        result = run_turn(
            goal, tools=tools, planner=planner, dry_run=False,
            game=game, max_attempts=2,
            should_stop=_stopping,
        )
    except Exception as exc:  # noqa: BLE001
        _log("llm_failed", error=f"{type(exc).__name__}: {exc}")
        return f"LLM_FAILED {type(exc).__name__}: {exc}"
    floor = int((state.get("run") or {}).get("floor") or 0)
    turn = int(((state.get("combat") or {}).get("turn")) or state.get("turn") or 0)
    _add_llm_usage(result.get("tokens"), str(result.get("run_id") or ""),
                   note=f"战斗 f{floor} t{turn}")
    if _stopping():
        _log("llm_cancelled", run_id=result.get("run_id"))
        return "STOPPED"
    if not result.get("ok"):
        steps = result.get("steps") or []
        nodes = {str(s.get("node") or "") for s in steps if isinstance(s, dict)}
        if "execute" in nodes:
            stage = "EXECUTE"
        elif "validate" in nodes:
            stage = "VALIDATE"
        else:
            stage = "PLAN"
        err = str(result.get("error") or "规划/验算未通过")[:180]
        _log("llm_failed", error=err, run_id=result.get("run_id"), stage=stage)
        return f"LLM_FAILED_{stage} {err}"
    plan = result.get("plan") or {}
    seq = plan.get("seq") if isinstance(plan, dict) else None
    say = plan.get("say") if isinstance(plan, dict) else None
    if isinstance(say, list):
        _remember_speech(" ".join(str(x) for x in say if str(x).strip()))
    elif say:
        _remember_speech(str(say))
    _log("llm_combat", run_id=result.get("run_id"), steps=len(seq or []),
         tokens=result.get("tokens"),
         out=str((result.get("result") or {}).get("output") or "")[:160])
    return f"LLM {len(seq or [])}步"


_BATCH_FAIL_LINES = ("BATCH STOP", "BATCH_FIX", "BATCH PARTIAL", "BATCH_EMPTY",
                     "BATCH_BAD_JSON", "PLAN_OVER_BUDGET")


def _num(value, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _combat_signature(state: dict | None) -> tuple:
    c = (state or {}).get("combat") or {}
    p = c.get("player") or {}
    hand = tuple((str(x.get("card_id") or x.get("id") or ""),
                  _num(x.get("energy_cost", x.get("cost"))))
                 for x in (c.get("hand") or []) if isinstance(x, dict))
    enemies = tuple((str(e.get("enemy_id") or e.get("name") or ""),
                     _num(e.get("current_hp")), _num(e.get("block")))
                    for e in (c.get("enemies") or []) if isinstance(e, dict))
    return ((state or {}).get("screen"), (state or {}).get("turn"),
            _num(p.get("energy")), _num(p.get("current_hp")),
            _num(p.get("block")), hand, enemies)


def _combat_progressed(before: dict, after: dict | None, out: str) -> tuple[bool, str]:
    """一批动作到底有没有推进局面：只看输出会把失败当成功，然后无限重放同一手。"""
    if not isinstance(after, dict) or not after.get("screen"):
        return False, "执行后读不到局面"
    if after.get("screen") != "COMBAT":
        return True, "离开战斗"
    if _combat_signature(before) != _combat_signature(after):
        return True, "局面变化"
    if str(after.get("turn")) != str(before.get("turn")):
        return True, "回合推进"
    for line in _BATCH_FAIL_LINES:
        if line in (out or ""):
            return False, f"回执失败：{line}"
    return False, "局面没有变化"


def _combat_pass_required(state: dict) -> bool:
    """本回合不能出牌（BOSS 的眩晕/跳过回合）：只有 end_turn 是合法动作。"""
    acts = set(_acts(state))
    return "play_card" not in acts and "end_turn" in acts


# ---------------------------------------------------------------- SL（时间回溯）
# save_and_quit → 主菜单 → continue_run 会把整场战斗还原到第 1 回合。以前只有人工
# 在对话里跑 `tools/sts2.py do save_and_quit` 才能用；自动玩的白名单菜单按设计
# 不放 save_and_quit（AGENTS 不变式），所以「肥鱼不会 SL 了」——这里在 runner
# 里做一次受控回档：只有「这回合怎么打都会死、且不是第 1 回合」才用。
_SL_MAX = 6
_sl_used = 0
_sl_done: set[str] = set()
_sl_last = 0.0


def _combat_key(state: dict) -> str:
    run = state.get("run") or {}
    c = state.get("combat") or {}
    ids = ",".join(str(e.get("enemy_id") or e.get("name") or "")
                   for e in c.get("enemies") or [])
    return f"{run.get('run_id') or '?'}|{_num(run.get('floor'), 0)}|{ids}"


def _sl_worth_it(state: dict, plans: list[dict]) -> bool:
    """第 1 回合回档=同一手牌重开，纯浪费；只有必死的中局才值得重打整场。"""
    combat = state.get("combat") or {}
    turn = _num(state.get("turn") or combat.get("turn"), 0)
    if turn <= 1 or not plans:
        return False
    hp = _num((combat.get("player") or {}).get("current_hp"), 0)
    if hp <= 0:
        return False
    for p in plans:
        if _num(p.get("hp"), 0) > 0:
            return False        # 有活下来的线：不 SL
        if _num(p.get("taken"), 0) < hp:
            return False        # 计划没吃到致死伤害，别误判
    return True


def _try_sl(sts2, state: dict) -> bool:
    """必死时回档一次；成功返回 True（主循环会从主菜单 continue_run）。"""
    global _sl_used, _sl_last
    if _sl_used >= _SL_MAX or _stopping():
        return False
    if time.time() - _sl_last < 20:
        return False
    key = _combat_key(state)
    if key in _sl_done:
        return False
    try:
        from ..decision import sim as _sim  # noqa: PLC0415

        base = _sim.Sim.from_state(state)
        if not base.alive() or base.incoming() < base.hp:
            return False        # 先做便宜判断，别每回合都跑 1 秒 DFS
        plans = _sim.search(state, max_cards=8, max_plans=6)
    except Exception as exc:  # noqa: BLE001
        _log("sl_failed", error=f"{type(exc).__name__}: {exc}")
        return False
    if not _sl_worth_it(state, plans):
        return False
    hp = _num((state.get("combat") or {}).get("player", {}).get("current_hp"), 0)
    turn = _num(state.get("turn") or (state.get("combat") or {}).get("turn"), 0)
    floor = _num((state.get("run") or {}).get("floor"), 0)
    try:
        sts2.act("save_and_quit")
    except Exception as exc:  # noqa: BLE001
        _log("sl_failed", error=f"{type(exc).__name__}: {exc}")
        return False
    _sl_used += 1
    _sl_done.add(key)
    _sl_last = time.time()
    _log("sl", floor=floor, turn=turn, hp=hp, used=_sl_used,
         plans=[{"k": p.get("k"), "hp": p.get("hp"), "taken": p.get("taken")}
                for p in plans[:3]])
    _write_status(last="时间回溯…", route_seq="")
    _say_screen(sts2, "时间回溯。这局不算，重打。", "COMBAT", state)
    return True


_COMBAT_BARKS = (
    ("先算账，再摸牌。", "数学爬塔。"),
    ("这波我算过了。", "稳一点稳一点。"),
    ("认真打，A10 不让的。", "别抖。"),
    ("出牌出牌。", "打得好看点。"),
    ("嗨呀，又到我表演了。", "别演砸。"),
)


def _combat_bark(plan: dict, state: dict) -> tuple[str, str]:
    """本地兜底战斗台词：报局面，不报血条。

    旧模板是 ``它 {incoming}，这手打 {dmg}``：每回合把血量和伤害念一遍，
    观众听着像报账（实机反馈「讲话总是播报 hp」）。现在只在真会死/斩杀/残血
    时给一句有情绪的人话，其余交给日常口癖，并且每回合都尽量换一句。
    """
    run = state.get("run") or {}
    combat = state.get("combat") or {}
    player = combat.get("player") or {}
    hp = _num(player.get("current_hp", run.get("current_hp", run.get("hp"))))
    max_hp = _num(player.get("max_hp", run.get("max_hp")), 1) or 1
    incoming = _num(plan.get("incoming"), 0)
    kills = _num(plan.get("kills"), 0)
    low = 0 < hp * 100 <= max_hp * 30
    if kills:
        return "刚好够，收工。", "一分不多。"
    if 0 < hp <= incoming:
        return "唉……这一下没挡住就没了。", "先挡，别慌。"
    if low:
        return "唔……血不多了，稳着来。", "再撑两回合。"
    turn = _num(state.get("turn") or combat.get("turn"), 0)
    return _COMBAT_BARKS[turn % len(_COMBAT_BARKS)]


def _combat_local(sts2, advisor, state: dict) -> str:
    acts = set(_acts(state))
    if _combat_pass_required(state):
        # 眩晕/被跳过：所有手牌都是 playable=false，问模型只会让它排 999 费的牌，
        # 执行层再 exit 3 → 整步报错 → 三次后被判卡住（run-0114）。
        sts2.act("end_turn")
        return "COMBAT pass"
    if not ({"play_card", "end_turn"} & acts):
        return "WAIT_COMBAT"
    plan = None
    try:
        plan = advisor.build_plan(state)
    except Exception as exc:  # noqa: BLE001
        _log("advisor_failed", error=f"{type(exc).__name__}: {exc}")
    if plan and plan.get("steps"):
        thought, meme = _combat_bark(plan, state)
        spec = advisor.spec_from_plan(plan, thought, meme)
        _remember_speech(" ".join(x for x in (thought, meme) if x))
        try:
            _, out = _call(sts2, sts2.cmd_batch, json.dumps(spec, ensure_ascii=False))
        except BaseException as exc:  # noqa: BLE001
            # 以前 SystemExit / RuntimeError 会一路冲到主循环当 fatal 整局停掉。
            _log("combat_exec_failed", kind=plan.get("kind"),
                 error=f"{type(exc).__name__}: {exc}")
            return "COMBAT exec-failed"
        _log("combat", dmg=plan.get("dmg"), taken=plan.get("taken"),
             kills=plan.get("kills"), out=out.strip().splitlines()[-1:] or [""])
        # 回执 + 前后状态都要核对：cmd_batch 返回了输出不等于牌真的打出去了
        # （BATCH STOP / PLAN_OVER_BUDGET / 动画帧都可能什么都没做）。
        try:
            after = sts2.get_state()
        except Exception as exc:  # noqa: BLE001
            _log("combat_state_failed", error=f"{type(exc).__name__}: {exc}")
            after = None
        ok, why = _combat_progressed(state, after, out)
        if not ok:
            _log("combat_no_progress", why=why, kind=plan.get("kind"),
                 steps=len(plan.get("steps") or []),
                 out=(out or "").strip().splitlines()[-3:])
            return "COMBAT no-progress"
        return f"COMBAT {plan.get('dmg', 0)}dmg"
    # 没有可打计划：结束回合（只在合法时）。
    if "end_turn" in acts:
        sts2.act("end_turn")
        return "COMBAT end"
    return "WAIT_COMBAT"


def _combat(sts2, advisor, state: dict, *, mode: str = "local",
            llm_tools=None, llm_planner=None, game: str = "") -> str:
    if _combat_pass_required(state):
        # 眩晕/跳过回合：手牌全是 playable=false，问模型只会得到不可执行的 seq
        # （还烧 token）。直接结束回合。
        sts2.act("end_turn")
        _log("stunned_pass")
        return "COMBAT pass"
    if mode == "llm" and _try_sl(sts2, state):
        return "COMBAT sl"
    if mode == "llm" and llm_tools is not None and llm_planner is not None:
        out = _combat_llm(llm_tools, llm_planner, state, game)
        if out == "STOPPED" or _stopping():
            return "STOPPED"
        if out.startswith("LLM_FAILED"):
            # 规划/验算/执行失败、或者网络抖动直接抛异常，都重新读当前局面用
            # 本地算账兜底。无人值守连播时，一次 Read timeout 不该让整局停掉；
            # 兜底同样不推进的话，主循环的卡住守卫会在 3 次后收尾。
            _log("llm_fallback", error=out)
            try:  # 执行失败可能已经打了一半，必须按最新手牌重算，不能用旧 state
                fresh = sts2.get_state()
            except Exception:
                fresh = state
            local = _combat_local(sts2, advisor, fresh)
            return f"LLM 失败，本地兜底（{local}）"
        return out
    return _combat_local(sts2, advisor, state)


# ---------------------------------------------------------------- 界面决策（LLM 优先）
# 这些屏幕的点击属于「玩家决策」：llm 模式先问模型，模型给不出可执行动作才退回本地启发式。
# 主菜单/角色选择/确认框是过场管道，不算决策，始终走本地。
LLM_SCREENS = ("MAP", "EVENT", "REST", "SHOP", "CHEST", "REWARD",
                "CARD_SELECTION", "BUNDLE_SELECTION")


def _after_deck_pick(sts2) -> None:
    """点完 ``select_deck_card`` 之后，把这场选牌的收尾做完、并等界面真的离开。

    两个实机坑：
    1. mod 的 ``select_deck_card`` 只选不确认，``selection.selected_count`` 还经常
       滞后为 0；模型看到 0/1 会再点同一张，而它是**切换** —— 第二下把选择取消。
    2. 选择/确认/界面切换都是异步的：点完立刻读状态会拿到**旧的** CARD_SELECTION，
       下一回合再点一次。run-0049 f7（营火锻造）、f21（商店删牌）、f1（涅奥删牌）
       都因此被判 stalled，其中商店那次牌其实已经删掉了（used=True）。

    所以这里不依赖调用方传进来的 ``selection`` 字段（它可能已经过期）：
    点完先等选择生效，需要确认就确认，最后最多等 5 秒直到屏幕不再是 CARD_SELECTION。
    """
    time.sleep(0.35)
    try:
        now = sts2.get_state()
    except Exception:  # noqa: BLE001
        return
    if _screen(now) == "CARD_SELECTION":
        acts = set(_acts(now))
        sel = now.get("selection") or {}
        if "confirm_selection" in acts and sts2._int(sel.get("max_select"), 0) <= 1:
            try:
                sts2.act("confirm_selection")
                _log("auto_confirm_selection",
                     kind=str(sel.get("kind") or ""),
                     selected=sts2._int(sel.get("selected_count"), 0))
            except Exception as exc:  # noqa: BLE001
                _log("auto_confirm_failed", error=f"{type(exc).__name__}: {exc}")
    deadline = time.time() + 5.0
    while time.time() < deadline:
        time.sleep(0.2)
        try:
            if _screen(sts2.get_state()) != "CARD_SELECTION":
                return
        except Exception:  # noqa: BLE001
            return
    _log("deck_pick_still_selection")


def _screen_llm(sts2, planner, state: dict, screen: str) -> str:
    """让 LLM 选一个界面动作并执行；返回空串表示「模型没给出可执行动作」。

    两道保险：菜单只来自 ``decision/actions.available``（白名单，禁用项不进菜单），
    模型的动作/下标对不上就带着原因重问一次，仍不行才落回本地兜底。
    """
    try:
        from ..decision import actions  # noqa: PLC0415
        from ..decision import ctx as ctxmod  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        _log("screen_llm_unavailable", error=f"{type(exc).__name__}: {exc}")
        return ""
    if screen == "MAP":
        plan = _ensure_route_plan(sts2, state)
        if plan:
            # 计划是开局整幕算好的：ctx 给出下一格和精英政策，菜单标出计划节点
            state = dict(state, route_plan=plan)
    menu = actions.available(state)
    if screen == "CHEST":
        # 实机反馈「宝箱遗物老是忘了拿」：把 mod 真实给的宝箱字段记下来，
        # 才能分清是没这个动作、还是 has_relic_been_claimed 提前变 true。
        ch = state.get("chest") or {}
        _log("chest_state", screen=screen, acts=sorted(_acts(state)),
             open=ch.get("is_open"), claimed=ch.get("has_relic_been_claimed"),
             relics=[str(r.get("name") or "") for r in (ch.get("relic_options") or [])
                     if isinstance(r, dict)],
             run_relics=len((state.get("run") or {}).get("relics") or []),
             rewards=[{"type": str(r.get("reward_type") or ""),
                       "name": str(r.get("name") or ""),
                       "claimable": r.get("claimable"),
                       "claimed": r.get("claimed")}
                      for r in ((state.get("reward") or {}).get("rewards") or [])
                      if isinstance(r, dict)],
             menu=[{"a": e["a"], "i": e["i"], "n": e.get("n")} for e in menu])
    if screen == "REST":
        # 营火「没有锻造」时要能回查：是游戏没给/禁用了，还是菜单漏了。
        _log("rest_menu", items=[{"a": e["a"], "i": e["i"], "n": e.get("n")}
                                 for e in menu],
             opts=[{"i": i, "n": str(o.get("title") or o.get("name") or ""),
                    "ok": _rest_option_enabled(o), "id": str(o.get("option_id") or "")}
                   for i, o in enumerate((state.get("rest") or {}).get("options") or [])
                   if isinstance(o, dict)])
    if not menu:
        return ""
    try:
        brief = ctxmod.build(state)
    except Exception as exc:  # noqa: BLE001
        _log("screen_llm_brief_failed", error=f"{type(exc).__name__}: {exc}")
        return ""
    brief["acts"] = [{"a": e["a"], "i": e["i"], "n": e.get("n") or "",
                      "fx": e.get("fx") or ""} for e in menu]
    goal = noncombat_goal(screen, state)
    feedback = ""
    for attempt in (1, 2):
        _write_status(last=f"LLM 决策（{screen}）…")
        res = planner.plan(brief, feedback=feedback, goal=goal)
        if not res.get("ok"):
            _log("screen_plan_failed", screen=screen, attempt=attempt,
                 error=str(res.get("error") or "")[:180])
            if attempt == 1 and not _transient_llm_error(res.get("error")):
                # 只有「模型回了但不可执行」才值得再问一次；超时/限流/5xx 在
                # LLMPlanner 里已经重试过 3 次了，再叠一层只会把这一步拖到几分钟。
                time.sleep(1.0)
                feedback = "上一次请求超时或失败，没有收到回复。只输出一个 JSON 对象。"
                continue
            _log("llm_fallback", screen=screen,
                 error=str(res.get("error") or feedback)[:180])
            return ""
        plan = res.get("plan") if isinstance(res.get("plan"), dict) else {}
        entry, err = actions.check(plan, state)
        if entry is None:
            feedback = (f"上一版不能执行：{err}。只从 context.acts 里挑一项并给对 i；"
                        "如果被路线拦住改选了别的格子，重写 say 让台词和动作一致。")
            _log("screen_plan_invalid", screen=screen, attempt=attempt, error=err[:160])
            continue
        say = plan.get("say")
        items = ([str(x) for x in say if str(x).strip()] if isinstance(say, list)
                 else ([str(say)] if say else []))
        text = items[0] if items else ""
        if len(items) > 1:
            _log("say_truncated", screen=screen, dropped=len(items) - 1, say=text[:80])
        # 模型偶尔还是会把 ctx 里的「HP 64/80」抄进 say：播出去前统一摘掉血条。
        tidy = _tidy_say(text)
        if tidy != text:
            _log("say_trimmed", screen=screen, before=text[:80], after=tidy[:80])
            text = tidy
        _add_llm_usage(res.get("usage"), note=f"{screen_label(screen)} {actions.label(entry)}")
        if text:
            # 先说话再点：观众先听到「为什么」，再看到画面变化（和战斗回合一致）。
            _say_screen(sts2, text, screen, state)
        try:
            actions.execute(sts2, entry)
        except Exception as exc:  # noqa: BLE001
            _log("screen_execute_failed", screen=screen, act=actions.label(entry),
                 error=f"{type(exc).__name__}: {exc}")
            return ""
        _log("llm_screen", screen=screen, act=entry["a"], i=entry.get("i"),
             tokens=res.get("usage"), say=text[:120])
        if entry["a"] == "select_deck_card":
            _after_deck_pick(sts2)
        if entry["a"] == "skip_reward_cards":
            _reward_leave(sts2)     # 别让 Card 奖励留在屏上再被 claim 一次
        return f"{screen} {actions.label(entry)}"
    _log("llm_fallback", error=f"{screen} 规划不可用：{feedback}"[:200])
    return ""


# ---------------------------------------------------------------- 非战斗
def _reward(sts2, state: dict) -> str:
    try:
        summary, _ = _call(sts2, sts2.cmd_rewards, pick="auto", quiet=True)
        deck = (summary or {}).get("deck")
        return f"REWARD deck={deck}"
    except BaseException as exc:  # noqa: BLE001
        _log("reward_failed", error=f"{type(exc).__name__}: {exc}")
        if "proceed" in _acts(state):
            sts2.act("proceed")
            return "REWARD proceed"
        return "REWARD unknown"


def _selection_need(sel: dict, n_cards: int = 0) -> int:
    """这一屏要选几张。

    游戏在「选 2 张普通牌」里会把 min/max 都给 0，只能从 prompt 读；
    旧实现固定只点第一张，选完还停在 CARD_SELECTION，一屏能卡 20 步。
    「选择任意张」用 max_select=999999999 当哨兵：必须按实际牌数封顶，
    否则一直点满不了，卡到 stalled（run-0040 f9 就是这么停的）。
    """
    need = 0
    for key in ("max_select", "max", "min_select", "min"):
        try:
            n = int(sel.get(key) or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            need = n
            break
    if not need:
        prompt = re.sub(r"\[[^\]]*\]", "", str(sel.get("prompt") or ""))
        m = re.search(r"选择\s*(\d+)\s*张", prompt)
        need = int(m.group(1)) if m else 1
    if n_cards and need > n_cards:
        need = n_cards
    return max(1, need)


def _blind_selection(sel: dict) -> bool:
    """mod 对「事件选 N 张」只回 pending、selected_count 永远是 0。"""
    try:
        mn = int(sel.get("min_select") or 0)
        mx = int(sel.get("max_select") or 0)
    except (TypeError, ValueError):
        return False
    return mn == 0 and mx == 0 and _selection_need(sel) >= 2


def _deck_multi_select(state: dict) -> bool:
    """这一屏是不是「从牌组里挑 N 张」（移除/升级/变化/附魔）——只有这种才走本地。

    mod 0.8.0 的牌组多选屏只回 pending、``selected_count`` 不可靠，模型点会重复点
    同一张（run-0113 一晚 1332 步 / 530 万 token），所以要本地按顺序点满再确认。

    但**不能只看 ``select_deck_card`` 在不在**：卡牌奖励屏（``choose_reward_card``
    + ``reward.pending_card_choice``）和「从 N 张里选 1 张」的捏奥祝福/事件
    （need=1）也会带 ``select_deck_card``。71f1acb 把它们一起拦进本地后，模型
    再也看不到自己拿了哪张，紧接着的奖励屏 ``card_options`` 又是空的，它就照着
    ctx 的「没有卡牌选项」说「奖励没牌可拿 / 涅奥没给好处」——玩家看到的是
    「明明拿了却说没拿」。单张选牌一次点击就结算，交回模型没有任何风险。
    """
    if "select_deck_card" not in _acts(state):
        return False
    if "choose_reward_card" in _acts(state):
        return False
    if (state.get("reward") or {}).get("pending_card_choice"):
        return False
    sel = state.get("selection") or {}
    cards = [c for c in (sel.get("cards") or []) if isinstance(c, dict)]
    if _blind_selection(sel):
        return True
    return _selection_need(sel, len(cards)) > 1


def _deck_select_key(state: dict, sel: dict, cards: list[dict]) -> str:
    """同一屏牌组多选的身份：换局/换层/换提示/换牌表就重新计数。

    只按 run+提示+牌表做键时，同一局里两次「锻造选 1 张」且牌表没变会撞键：
    f13 点过一张、f16 再开同一屏时会以为已经选满，直接卡在 ``CARD wait 1/1``
    （实机 run-0009 f16，退出前整局被判「删牌界面卡死」）。
    """
    ids = ",".join(str(c.get("card_id") or c.get("name") or "") for c in cards)
    run = state.get("run") or {}
    return "|".join((str(state.get("run_id") or ""), str(run.get("floor") or ""),
                     str(sel.get("prompt") or ""), ids[:200]))


def _deck_order(state: dict, sel: dict, cards: list[dict]) -> list[int]:
    """选牌顺序：删/变化先打击、防御，升级先核心牌。

    复用 ``decision/noncombat.select_eval``（和奖励/商店同一套牌面评估）：
    - 移除/变化：打击 +20、防御 +12、诅咒 +35，核心牌 −45（`_remove_score`）；
    - 升级：打击只给 5 分、防御 12 分，BASH 等核心牌 +36（`_upgrade_score`）。
    评估失败就退回原顺序（0、1、2…），至少不会卡住。
    """
    order = list(range(len(cards)))
    try:
        try:
            from ..decision import noncombat
        except Exception:  # 测试里 runner 可能不是按包导入
            import importlib

            _dec = Path(__file__).resolve().parent.parent / "decision"
            if str(_dec) not in sys.path:
                sys.path.insert(0, str(_dec))
            noncombat = importlib.import_module("noncombat")

        deck = (state.get("run") or {}).get("deck") or []
        prompt = str(sel.get("prompt") or "")
        if not prompt:
            kind = str(sel.get("kind") or "")
            prompt = {"deck_upgrade_select": "选择1张牌来升级",
                      "deck_transform_select": "选择1张牌来变化"}.get(kind, "")
        ev = noncombat.evaluate({"run": {"deck": deck}},
                                {"kind": "select", "prompt": prompt, "cards": cards})
        scores = {int(o["i"]): float(o["sc"]) for o in (ev or {}).get("opts") or []}
        if scores:
            order.sort(key=lambda i: (-scores.get(i, 0.0), i))
    except Exception:
        pass
    return order


def _reward_leave(sts2) -> str:
    """跳过选牌后把奖励屏收掉。

    mod 的 ``skip_reward_cards`` 只关掉选牌（pending=False），Card 奖励还留在
    奖励列表里 claimable；下一步再从奖励屏 claim 同一张就会无限循环
    （run-0005 f1 实测：REWARD claim → CARD skip → REWARD claim…300+ 步）。
    """
    try:
        now = sts2.get_state()
    except Exception as exc:  # noqa: BLE001
        _log("reward_leave_state_failed", error=f"{type(exc).__name__}: {exc}")
        return ""
    if "collect_rewards_and_proceed" in _acts(now):
        try:
            sts2.act("collect_rewards_and_proceed")
            _log("reward_leave")
            return "leave"
        except Exception as exc:  # noqa: BLE001
            _log("reward_leave_failed", error=f"{type(exc).__name__}: {exc}")
    return ""


def _card_selection(sts2, state: dict) -> str:
    acts = _acts(state)
    sel = state.get("selection") or {}
    cards = [c for c in (sel.get("cards") or []) if isinstance(c, dict)]
    if "choose_reward_card" in acts and cards:
        try:
            bi, bs, _ = sts2.pick_best_card(cards)
            if bs >= 1:
                sts2.act("choose_reward_card", option_index=int(bi))
                return f"CARD pick {bi}"
        except Exception:
            pass
        if "skip_reward_cards" in acts:
            sts2.act("skip_reward_cards")
            return "CARD skip+leave" if _reward_leave(sts2) else "CARD skip"
    if "select_deck_card" in acts and cards:
        need = _selection_need(sel, len(cards))
        # mod 0.8.0 起牌组多选屏（「移除/升级 N 张」）支持 confirm_selection：
        # 点一次 = 选一张，重复点同一张会取消。用本地计数保证按 0、1、2… 点不同
        # 的牌，点满 need 张再确认；selected_count 只在它更大时用来同步（旧 mod
        # 永远回 0，run-0113 就是这么卡了 6 小时）。
        key = _deck_select_key(state, sel, cards)
        done = _deck_clicks.get(key, 0)
        try:
            already = int(sel.get("selected_count") or sel.get("selected") or 0)
        except (TypeError, ValueError):
            already = 0
        done = max(done, already)
        if done >= need:
            try:
                now = sts2.get_state()
            except Exception:
                now = state
            if "confirm_selection" in _acts(now):
                sts2.act("confirm_selection")
                return "CARD confirm"
            if _screen(now) != "CARD_SELECTION":
                return f"CARD -> {_screen(now)}"
            now_sel = now.get("selection") or {}
            auto = now_sel.get("requires_confirmation") is False
            if auto or (not _blind_selection(now_sel)
                        and already == 0 and now_sel.get("can_confirm") is not True):
                # 这一屏选完立刻生效（没有确认键），或本地计数是上一屏残留：
                # 清掉重新点，别死等一个永远不会出现的 confirm。
                _log("deck_counter_reset", key=key[:80], done=done, need=need,
                     selected=already, auto=bool(auto))
                _deck_clicks[key] = 0
                done = 0
            else:
                return f"CARD wait {done}/{need}"
        if done >= len(cards):
            return f"CARD wait {done}/{need}"
        if len(_deck_clicks) > 40:
            _deck_clicks.clear()
        _deck_clicks[key] = done + 1
        order = _deck_order(state, sel, cards)
        idx = order[done] if done < len(order) else done
        try:
            sts2.act("select_deck_card", option_index=int(idx))
            return f"CARD select {done + 1}/{need}"
        except Exception as exc:  # noqa: BLE001
            _log("deck_select_failed", i=done, error=f"{type(exc).__name__}: {exc}")
            return "CARD select failed"
    if "confirm_selection" in acts:
        sts2.act("confirm_selection")
        return "CARD confirm"
    if "skip_reward_cards" in acts:
        sts2.act("skip_reward_cards")
        return "CARD skip+leave" if _reward_leave(sts2) else "CARD skip"
    if "proceed" in acts:
        sts2.act("proceed")
        return "CARD proceed"
    return "CARD unknown"


_REST_HEAL_WORDS = ("休息", "睡觉", "回复", "rest", "sleep", "heal")
_REST_SMITH_WORDS = ("锻造", "升级", "smith", "upgrade")
_REST_HEAL_IDS = ("HEAL", "REST")
_REST_SMITH_IDS = ("SMITH",)


def _rest_option_enabled(opt: dict) -> bool:
    """mod 的 REST 选项带 is_enabled（agent 视图叫 enabled，ctx 摘要叫 ok）。"""
    for key in ("is_enabled", "enabled", "ok"):
        if key in opt:
            return bool(opt.get(key))
    return True


def _rest_option_matches(opt: dict, words: tuple[str, ...], ids: tuple[str, ...]) -> bool:
    """先认 option_id（HEAL/SMITH，不随语言变），再退回标题子串。"""
    oid = str(opt.get("option_id") or opt.get("id") or "").strip().upper()
    if oid and oid in ids:
        return True
    name = str(opt.get("title") or opt.get("name") or "")
    low = name.lower()
    return any(w in name or w in low for w in words)


def _rest(sts2, state: dict) -> str:
    """本地兜底的营火选择：只在游戏允许的选项里挑。

    实机 run-0110：事件把最大生命打到 1（满血）且牌全升级，营火里「休息」可用但
    回 0 血、「锻造」被游戏禁用。旧实现只按血量比挑标题、完全不看 ``is_enabled``，
    于是每轮都点那个被禁用的「锻造」，被 409 打回后再重放同一个动作，整局卡死。
    """
    opts = [o for o in ((state.get("rest") or {}).get("options") or []) if isinstance(o, dict)]
    acts = _acts(state)
    if not opts:
        # 有些版本选完休息/锻造后 REST 只剩 proceed，不点会原地循环。
        if "proceed" in acts:
            sts2.act("proceed")
            return "REST proceed"
        return "REST no options"
    _, hp, mhp = _run_meta(state)
    pct = hp / mhp if mhp else 1.0
    words, ids = ((_REST_HEAL_WORDS, _REST_HEAL_IDS) if pct < 0.70
                  else (_REST_SMITH_WORDS, _REST_SMITH_IDS))
    usable = [(i, o) for i, o in enumerate(opts) if _rest_option_enabled(o)]
    if not usable:
        # 全被禁用：能走就走，走不了交给主循环的「原地打转」兜底。
        if "proceed" in acts:
            sts2.act("proceed")
            return "REST proceed"
        return "REST all disabled"
    # 想要的被禁用时退到第一个可用项（例如满血 + 没牌可升级 → 只能点休息）。
    idx = next((i for i, o in usable if _rest_option_matches(o, words, ids)), usable[0][0])
    if "choose_rest_option" in acts:
        sts2.act("choose_rest_option", option_index=int(idx))
        return f"REST choose_rest_option {idx}"
    if "proceed" in acts:
        sts2.act("proceed")
        return f"REST proceed {idx}"
    return "REST no options"


def _event(sts2, state: dict) -> str:
    opts = [o for o in ((state.get("event") or {}).get("options") or []) if isinstance(o, dict)]
    idx = next((i for i, o in enumerate(opts)
                if not (o.get("is_locked") or o.get("locked"))), 0)
    if "choose_event_option" in _acts(state):
        sts2.act("choose_event_option", option_index=idx)
        return f"EVENT {idx}"
    if "proceed" in _acts(state):
        sts2.act("proceed")
        return "EVENT proceed"
    return "EVENT unknown"


def _shop(sts2, state: dict) -> str:
    acts = _acts(state)
    # 货架开着时游戏不提供 leave_shop，只给 close_shop_inventory；先关货架，
    # 下一步才可能离开。旧实现没有这条，LLM 超时后兜底直接 "SHOP unknown" 收尾。
    if "close_shop_inventory" in acts:
        sts2.act("close_shop_inventory")
        return "SHOP close"
    if "leave_shop" in acts:
        sts2.act("leave_shop")
        return "SHOP leave"
    if "proceed" in acts:
        sts2.act("proceed")
        return "SHOP proceed"
    return "SHOP unknown"


def _bundle(sts2, state: dict) -> str:
    """牌包选择：模型失败时兜底挑第一包再确认，别把整局停在第 1 层。"""
    acts = _acts(state)
    bundles = [b for b in (state.get("bundles") or []) if isinstance(b, dict)]
    if "choose_bundle" in acts and bundles:
        idx = int(bundles[0].get("index") or 0)
        sts2.act("choose_bundle", option_index=idx)
        return "BUNDLE pick"
    if "confirm_bundle" in acts:
        sts2.act("confirm_bundle")
        return "BUNDLE confirm"
    return "BUNDLE unknown"


def _chest(sts2, state: dict) -> str:
    acts = _acts(state)
    if "open_chest" in acts:
        sts2.act("open_chest")
        return "CHEST open"
    if "choose_treasure_relic" in acts:
        sts2.act("choose_treasure_relic", option_index=0)
        return "CHEST relic"
    rewards = [r for r in ((state.get("reward") or {}).get("rewards") or [])
               if isinstance(r, dict)]
    if "claim_reward" in acts and rewards:
        sts2.act("claim_reward", option_index=0)
        return "CHEST claim"
    if "claim_all_rewards" in acts:
        sts2.act("claim_all_rewards")
        return "CHEST claim all"
    if "collect_rewards_and_proceed" in acts:
        sts2.act("collect_rewards_and_proceed")
        return "CHEST collect"
    if "close_chest" in acts:
        sts2.act("close_chest")
        return "CHEST close"
    if "proceed" in acts:
        sts2.act("proceed")
        return "CHEST proceed"
    return "CHEST unknown"


def _modal(sts2, state: dict) -> str:
    # 主菜单的 abandon_run 会弹确认框，只认 proceed/dismiss 会停手。
    # confirm 优先：这些框都是我们自己点的动作弹出来的（放弃/存档退出），确认才是本意。
    for name in ("confirm_modal", "dismiss_modal", "proceed", "dismiss",
                 "close_modal", "close"):
        if name in _acts(state):
            sts2.act(name)
            return f"MODAL {name}"
    return "MODAL unknown"


def _one_step(sts2, advisor, state: dict, *, mode: str = "local",
              llm_tools=None, llm_planner=None, screen_planner=None,
              game: str = "") -> str:
    if _stopping():
        return "STOPPED"
    screen = _screen(state)
    acts = _acts(state)
    if "confirm_modal" in acts or "dismiss_modal" in acts:
        # 确认框弹着时底层屏幕的按钮点不动，先把它收掉（否则会一直 UNKNOWN/WAIT）。
        return _modal(sts2, state)
    if screen == "GAME_OVER":
        return "GAME_OVER"
    if screen in ("MAIN_MENU", "CHARACTER_SELECT"):
        new = _ensure_run(sts2, state)
        return "START_RUN" if new else f"WAIT_{screen}"
    if screen == "COMBAT":
        return _combat(sts2, advisor, state, mode=mode, llm_tools=llm_tools,
                       llm_planner=llm_planner, game=game)
    if mode == "llm" and screen_planner is not None and screen in LLM_SCREENS:
        # 只有「从牌组挑 N 张」的多选才走本地（mod 的 selected_count 不可靠，
        # 模型会重复点同一张）；单张选牌和卡牌奖励是玩家决策，必须交给模型。
        if screen == "CARD_SELECTION" and _deck_multi_select(state):
            _log("deck_selection_local", need=_selection_need(state.get("selection") or {}))
        else:
            out = _screen_llm(sts2, screen_planner, state, screen)
            if out:
                return out
    if screen == "REWARD":
        return _reward(sts2, state)
    if screen == "CARD_SELECTION":
        return _card_selection(sts2, state)
    if screen == "MAP":
        return _choose_map(sts2, state)
    if screen == "REST":
        return _rest(sts2, state)
    if screen == "EVENT":
        return _event(sts2, state)
    if screen == "SHOP":
        return _shop(sts2, state)
    if screen == "CHEST":
        return _chest(sts2, state)
    if screen == "BUNDLE_SELECTION":
        return _bundle(sts2, state)
    if screen == "MODAL":
        return _modal(sts2, state)
    return f"UNKNOWN {screen}"


# ---------------------------------------------------------------- 卡住兜底
# 这几个返回值表示「这一步没有推进局面」；主循环据此统计原地打转。
_NO_PROGRESS = ("REST no options", "REST all disabled", "MAP_NO_AVAILABLE",
                "REWARD_FAILED", "CARD select failed")


def _error_code(last: str) -> str:
    """从 ``xxx failed: invalid_target 说明`` 里取错误码，判断是不是同一种失败。"""
    m = re.search(r"failed:\s*(\S+)", last or "")
    return m.group(1) if m else "unknown"


def _transient_llm_error(err) -> bool:
    """LLMPlanner 已经在内部退避重试过了：这些错误再问一遍只会更慢。"""
    text = str(err or "")
    return text.startswith(("HTTP 429", "HTTP 5", "ReadTimeout", "ConnectTimeout",
                            "ConnectionError", "ConnectError", "Timeout",
                            "ConnectionResetError", "RemoteDisconnected"))


def _stall_key(screen: str, last: str) -> tuple[str, str] | None:
    """把「没有推进」的一步归一成可比较的键；正常推进返回 None。"""
    if last.startswith("ERROR"):
        return (screen, "ERROR " + _error_code(last))
    if last in _NO_PROGRESS:
        return (screen, last)
    if last.startswith("CARD "):
        # 选牌/奖励/确认都会回 "CARD ..."：字符串一模一样说明界面没变。
        # 真正有进展时 selected 变化，串也不同（比如 1/2 → 2/2），不会误判。
        return (screen, last)
    if last.startswith(("CARD_SELECTION select_deck_card",
                        "CARD_SELECTION choose_reward_card")):
        # 模型点同一张牌：界面没变（换了牌下标就不同，不会累计）。
        return (screen, last)
    if last in ("COMBAT no-progress", "COMBAT exec-failed"):
        # 这批动作没推进局面（回执失败/状态没变）：同一画面重复出现就是卡住。
        return (screen, last)
    if last.startswith("WAIT_"):
        return (screen, last.split(" ", 1)[0])
    if last in ("SHOP open", "SHOP close",
                "SHOP open_shop_inventory", "SHOP close_shop_inventory"):
        # 商店开/关货架来回切字符串不同（本地兜底和 LLM 标签两种写法），普通去重
        # 抓不到；归一成一个键才判得出来（实机里 90 金买不起祭品就这么来回切）。
        return (screen, "SHOP toggle")
    return None


class _StallGuard:
    """同一画面同一失败连续出现就收尾。

    旧实现只认几个字面量（其中 ``"CHEST"`` 根本不会出现，异常串更不会），
    所以一个被游戏拒绝的动作可以原样重放几百次，直到 5000 步上限。
    """

    def __init__(self, limit: int = 3):
        self.limit = max(1, int(limit))
        self.key: tuple[str, str] | None = None
        self.hits = 0

    def hit(self, key: tuple[str, str]) -> int:
        if key == self.key:
            self.hits += 1
        else:
            self.key, self.hits = key, 1
        return self.hits

    def clear(self) -> None:
        self.key, self.hits = None, 0


# 「从牌组移除 N 张」是 mod 的已知 blocker（docs/TODO_mod_deck_remove.md）：
# 卡住时按文档里的临时绕过自动回滚一次——save_and_quit → 主菜单 → continue_run。
_rollback_tried = False


def _recover_blind_screen(sts2, state: dict, last: str) -> bool:
    """多选卡死时回滚一次；返回 True 表示已经回滚，主循环应重读局面继续。"""
    global _rollback_tried
    if _rollback_tried or not str(last).startswith(("CARD ui wait", "CARD wait")):
        return False
    sel = state.get("selection") or {}
    need = _selection_need(sel, len(sel.get("cards") or []))
    try:
        selected = int(sel.get("selected_count") or sel.get("selected") or 0)
    except (TypeError, ValueError):
        selected = 0
    if not (_blind_selection(sel) or (selected >= need and need > 0)):
        # 普通「锻造/移除选 1 张」屏没有确认键是正常的（选完就生效），
        # 这一步只是在等过场；回滚会把正在打的整局丢掉（run-0009 f16）。
        return False
    _rollback_tried = True
    prompt = str((state.get("selection") or {}).get("prompt") or "")[:60]
    try:
        sts2.act("save_and_quit")
    except Exception as exc:  # noqa: BLE001
        _log("blind_recover_failed", error=f"{type(exc).__name__}: {exc}",
             prompt=prompt)
        return False
    _log("blind_recover", prompt=prompt, last=last)
    _write_status(last="删牌界面卡死，回滚重开…", error="")
    return True


# ---------------------------------------------------------------- 循环
def _relaunch_game(game: str) -> bool:
    """游戏进程没了/API 一直连不上时重启一次；失败只记日志，不抛。"""
    try:
        from dafeiyu.games.launch import launch_game  # noqa: PLC0415

        out = launch_game(game)
        _log("relaunch", ok=bool(out.get("ok")),
             error=str(out.get("error") or "")[:120])
        return bool(out.get("ok"))
    except Exception as exc:  # noqa: BLE001
        _log("relaunch_failed", error=f"{type(exc).__name__}: {exc}")
        return False


def _wait_game(sts2, timeout: float = 120.0) -> dict | None:
    end = time.time() + timeout
    while time.time() < end:
        if _stop.is_set() or STOP_PATH.exists():
            return None
        try:
            state = sts2.get_state()
            if state.get("screen"):
                return state
        except Exception:
            pass
        _write_status(last="等待游戏 API…")
        time.sleep(1.0)
    return None


def _run(game: str, mode: str = "local") -> None:
    global _thread, _speech_until, _rollback_tried, _sl_used, _sl_last
    global _rec_run_id, _rec_game, _rec_mode, _rec_pending, _rec_active, _rec_path
    global _rec_split
    _rollback_tried = False
    _sl_used = 0
    _sl_last = 0.0
    _sl_done.clear()
    _rec_run_id = None
    _rec_game = game
    _rec_mode = mode
    _rec_pending = 0.0
    _rec_active = False
    _rec_path = ""
    try:
        _rec_split = str(recording_policy.policy_for(game).get("split") or "run")
    except Exception:  # noqa: BLE001
        _rec_split = "run"
    _deck_clicks.clear()
    sts2 = advisor = llm_tools = llm_planner = screen_planner = None
    try:
        sts2, advisor = _import_tools()
        if mode == "llm":
            from dafeiyu.agent.planner import LLMPlanner  # noqa: PLC0415
            from ..decision.agent_tools import Sts2Tools  # noqa: PLC0415
            from dafeiyu.core.config import LiveConfig  # noqa: PLC0415

            cfg = LiveConfig.load()
            from dafeiyu.persona.layers import prompt_prefix  # noqa: PLC0415

            persona = prompt_prefix(cfg, game_id="slay-the-spire-2", live=True)
            llm_tools = Sts2Tools(game)
            # 界面决策（地图/事件/营火/商店/宝箱/选牌）是简单选择，关掉思维链：
            # deepseek-flash 开着 thinking 时实测一次 24s、4096 reasoning token、
            # 最终 content 为空；关掉后同一局面 0.8s 就回 JSON。
            think_pref = str(getattr(cfg, "autoplay_llm_thinking", "off") or "off")
            think_pref = think_pref.strip().lower()
            combat_thinking = {"off": False, "on": True}.get(think_pref)  # auto → 模型默认
            screen_thinking = True if think_pref == "on" else False
            llm_planner = LLMPlanner(
                cfg, model=cfg.autoplay_llm_model or cfg.llm_model,
                timeout=25.0, system_prompt=(persona + "\n\n" + SYSTEM_PROMPT).strip(),
                thinking=combat_thinking,
            )
            screen_planner = LLMPlanner(
                cfg, model=cfg.autoplay_llm_model or cfg.llm_model,
                timeout=40.0, system_prompt=(persona + "\n\n" + NONCOMBAT_PROMPT).strip(),
                thinking=screen_thinking,
            )
            ok, why = screen_planner.available()
            if not ok:
                _log("llm_unavailable", error=why)
                _write_status(last=why, error=why)
        _host.speech_until = 0.0
        _speech_until = 0.0
        _write_status(running=True, game=game, error="", step=0, mode=mode,
                      started_at=time.time(), last="启动游戏…")
        if not _stopping():
            res = _start_autoplay_recording(game, mode) or {}
            if not res.get("ok") and _recording_retryable(res):
                _rec_pending = time.time() + 6.0   # OBS 没起来就过几秒再试，别让整局没录像
        if _stopping():
            _write_status(last="已停手")
            return
        state = _wait_game(sts2, timeout=150)
        if state is None and not _stopping():
            # 启动慢、或上一次游戏没退干净导致等不到 API：重启一次再等，
            # 无人值守时不能因为一次启动超时就整晚不播。
            _log("start_timeout", relaunch=True)
            if _relaunch_game(game):
                state = _wait_game(sts2, timeout=180)
        if state is None:
            _write_status(running=False, error="启动超时：游戏 API 未就绪")
            return
        _set_from_state(state, "已连接游戏")
        # 开局不主动播报招呼；第一条台词从实际战斗/事件开始。
        _pace()
        _log("start", game=game, mode=mode)
        step = 0
        guard = _StallGuard()
        unknown_key: tuple[str, str] | None = None
        unknown_hits = 0
        # 游戏进程失联（崩溃/被关掉）时的看门狗：连续 40 次读不到状态
        # （约 60 秒）就重启游戏；重启后能连上就继续，连不上 3 次才收尾。
        dead_reads = 0
        alive_steps = 0
        restarts = 0
        while not _stopping():
            if step >= 5000:
                _write_status(running=False, last="达到步数上限，停手")
                break
            try:
                state = sts2.get_state()
            except Exception as exc:  # noqa: BLE001
                dead_reads += 1
                _write_status(last="读取游戏状态失败",
                              error=f"{type(exc).__name__}: {exc}")
                if dead_reads >= 40:
                    dead_reads = 0   # 重新计数，别每次循环都触发重启
                    _log("game_lost", error=str(exc)[:160], restarts=restarts)
                    restarts += 1
                    if restarts > 3:
                        _write_status(running=False, last="游戏失联，重启无效，停手",
                                      error=f"游戏失联：{type(exc).__name__}")
                        _log("game_lost_giveup", restarts=restarts)
                        return
                    _write_status(last="游戏失联，正在重启…", error=str(exc)[:120])
                    if _relaunch_game(game):
                        state = _wait_game(sts2, timeout=180)
                        dead_reads = 0
                        if state is not None:
                            _set_from_state(state, "游戏已重启")
                            _log("game_relaunched", restarts=restarts)
                            continue
                        _log("game_relaunch_timeout", restarts=restarts)
                    time.sleep(3.0)
                    continue
                time.sleep(1.5)
                continue
            dead_reads = 0
            alive_steps += 1
            if alive_steps >= 600:
                # 稳定跑了十分钟就把重启配额还回去，避免长直播里几次崩溃后
                # 后面再崩就直接停手。
                restarts = 0
                alive_steps = 0
            if not state.get("screen"):
                _write_status(last="等待游戏…")
                time.sleep(1.0)
                continue
            _set_from_state(state)
            if _screen(state) == "GAME_OVER":
                # 新一局重新给 SL 额度（一局最多 6 次）。
                _sl_used = 0
                _sl_last = 0.0
                _sl_done.clear()
                # 每局一个视频：死亡就收尾，等新一局第一屏再开新录像。
                if _rec_active and _rec_split != "none":
                    _rec_run_id = ""
                    stop = _finish_autoplay_recording("run_end") or {}
                    _log("record_split", reason="game_over",
                         stop_ok=bool(stop.get("ok")))
                _say("这局结束了，我回主菜单再来一局。")
                _write_status(last="GAME_OVER，回主菜单", route_seq="",
                              route_option=None, route_note="", route_floor=0)
                _pace()
                # 结算动画/过场里动作可能还没出来：连试三次（每次都重读局面），
                # 无人值守时不能因为一次没点动就整晚停手。
                new_state = None
                for attempt in range(3):
                    try:
                        cur = state if attempt == 0 else sts2.get_state()
                        new_state = _ensure_run(sts2, cur)
                    except Exception as exc:  # noqa: BLE001
                        _log("game_over_retry", attempt=attempt + 1,
                             error=f"{type(exc).__name__}: {exc}")
                        new_state = None
                    if new_state:
                        break
                    time.sleep(1.2)
                if new_state:
                    _set_from_state(new_state, "新一局")
                    _record_new_run()      # 新一局 = 新视频
                    _log("restart_run")
                    continue
                _write_status(running=False, error="GAME_OVER 无法返回主菜单",
                              last="GAME_OVER")
                _log("game_over", restart=False)
                return
            try:
                last = _one_step(sts2, advisor, state, mode=mode,
                                 llm_tools=llm_tools, llm_planner=llm_planner,
                                 screen_planner=screen_planner, game=game)
            except BaseException as exc:  # noqa: BLE001
                last = f"ERROR {type(exc).__name__}: {exc}"
                _log("step_error", error=last)
                _write_status(error=last)
                time.sleep(1.2)
            if last == "STOPPED":
                _write_status(last="已停手")
                _log("stop")
                break
            step += 1
            # 这一步之后的状态刷新也可能失败（游戏正好在切场景/退出）：
            # 用旧 state 继续，别让一次读状态异常把整局打成 fatal。
            try:
                fresh = state if last.startswith("WAIT") else sts2.get_state()
            except Exception as exc:  # noqa: BLE001
                fresh = state
                _log("state_refresh_failed", error=f"{type(exc).__name__}: {exc}")
            _set_from_state(fresh, last)
            _maybe_split_recording(fresh)
            _write_status(step=step, error="" if not last.startswith("ERROR") else _state.get("error", ""))
            _log("step", step=step, last=last)
            if last.startswith("UNKNOWN") or last.endswith("unknown"):
                # 一次「未知」不一定真卡死：动画/过场/兜底缺口都可能只出现一步。
                # 连续 3 次同画面才收尾，避免像 run-0111 那样因为一次缺动作就整局停掉。
                hit = (_screen(state), last)
                if hit == unknown_key:
                    unknown_hits += 1
                else:
                    unknown_key, unknown_hits = hit, 1
                _log("unknown_screen", last=last, hits=unknown_hits,
                     screen=_screen(state))
                if unknown_hits >= 3:
                    _write_status(error=last, last="未知界面，正在收尾…", stopping=True)
                    return
                _write_status(error=last, last="未知界面，再看一眼…")
                _pace()
                time.sleep(0.8)
                continue
            unknown_key, unknown_hits = None, 0
            if last.startswith("LLM_FAILED"):
                _write_status(error=last, last="LLM 执行失败，正在收尾…", stopping=True)
                _log("llm_stop", last=last)
                return
            key = _stall_key(_screen(state), last)
            if key is None:
                guard.clear()
            elif guard.hit(key) >= guard.limit:
                if _recover_blind_screen(sts2, state, last):
                    # 已 save_and_quit，下一轮在主菜单走 continue_run 回滚。
                    guard.clear()
                    _log("blind_recover_wait", last=last)
                    time.sleep(2.0)
                    continue
                _write_status(error=f"界面没有进展：{last}",
                              last="界面卡住，正在收尾…", stopping=True)
                _log("stalled", last=last, screen=_screen(state), hits=guard.hits)
                return
            _pace()
        _write_status(last="正在收尾…", stopping=True)
        _log("stop")
    except BaseException as exc:  # noqa: BLE001
        _write_status(error=f"{type(exc).__name__}: {exc}",
                      last="异常收尾…", stopping=True)
        _log("fatal", error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            _finish_autoplay_recording("autoplay_end")
        except Exception:
            pass
        stopped = _host.stopping()
        with _lock:
            _thread = None
            _write_status(stopping=False, running=False, last="已停手")
        if stopped:
            # 停止文件只用于通知本轮；收尾后清掉，下一次启动不会看到残留。
            _host.clear_stop()


def start_autoplay(game: str | None = None, *, mode: str | None = None,
                   pace: str | None = None) -> dict:
    """启动游戏并开始自动玩；立即返回，实际运行在后台线程。"""
    global _thread
    from dafeiyu.core.config import LiveConfig
    from dafeiyu.games.launch import launch_game

    patch = {}
    if mode is not None:
        mode = str(mode).strip().lower()
        if mode not in ("local", "llm"):
            return {"ok": False, "message": "玩法只能是 local 或 llm"}
        patch["autoplay_mode"] = mode
    if pace is not None:
        pace = str(pace).strip().lower()
        if pace not in ("voice", "normal"):
            return {"ok": False, "message": "节奏只能是 voice 或 normal"}
        patch["autoplay_pace"] = pace
    cfg = LiveConfig.load()
    if patch:
        cfg.save_patch(patch)
        cfg = LiveConfig.load()
    mode = str(mode or cfg.autoplay_mode or "local").strip().lower()
    pace = str(pace or cfg.autoplay_pace or "voice").strip().lower()
    mode = mode if mode in ("local", "llm") else "local"
    pace = pace if pace in ("voice", "normal") else "voice"
    if mode == "llm":
        from dafeiyu.agent.graph import StateGraph  # noqa: PLC0415
        from dafeiyu.agent.planner import LLMPlanner  # noqa: PLC0415

        if StateGraph is None:
            return {"ok": False, "message": "LLM 玩需要安装 langgraph：pip install -e .[agent]",
                    "mode": mode}
        ok, why = LLMPlanner(
            cfg, model=cfg.autoplay_llm_model or cfg.llm_model,
            system_prompt=SYSTEM_PROMPT,
        ).available()
        if not ok:
            return {"ok": False, "message": why, "mode": mode}

    with _lock:
        if _thread is not None and _thread.is_alive():
            return {"ok": True, "already": True, "message": "她已经在玩了",
                    "status": status()}
        current = status()
        local_stale = (current.get("pid") == os.getpid()
                       and not (_thread is not None and _thread.is_alive()))
        if current.get("running") and _pid_alive(current.get("pid")) and not local_stale:
            return {"ok": True, "already": True, "message": "她已经在玩了",
                    "status": current}
        game = str(game or cfg.game or "").strip()
        if game != GAME_ID:
            return {"ok": False, "message": "当前只有杀戮尖塔 2 配了自动玩法"}
        _host.clear_stop()
        launched = launch_game(game)
        if not launched.get("ok"):
            return {"ok": False, "message": launched.get("error") or "游戏启动失败",
                    "launch": launched}
        _host.reset(game=game, mode=mode, pace=pace)
        _timeline_prev.update({"screen": "", "floor": 0, "run_id": ""})
        _write_status(running=True, game=game, error="", last="启动中…",
                      screen="", step=0, mode=mode, pace=pace,
                      started_at=time.time(), pid=os.getpid(),
                      tokens_in=0, tokens_out=0, tokens_total=0, llm_calls=0,
                      tokens_last=0, recording=False, record_path="", record_error="",
                      stopping=False, route_seq="", route_option=None,
                      route_note="", route_floor=0)
        _thread = threading.Thread(target=_run, args=(game, mode),
                                   name="dafeiyu-autoplay", daemon=True)
        _thread.start()
        return {"ok": True, "message": "已开始：先启动游戏，再让她打",
                "mode": mode, "pace": pace, "launch": launched, "status": status()}


def stop_autoplay() -> dict:
    """跨进程停手：写停止文件；本进程的录像立即收尾，不等 LLM 回来。"""
    global _speech_until
    _host.stop_path = STOP_PATH
    _host.request_stop()
    _speech_until = 0.0
    current = status()
    remote = bool(current.get("pid") and int(current.get("pid")) != os.getpid()
                  and current.get("running") and _pid_alive(current.get("pid")))
    if remote:
        # 从 CLI/另一个进程停：只写停止文件，不能把远端进程的状态改成
        # running=false（那会让控制台以为停了，实际还在动）。
        _write_status(last="正在停手…", stopping=True)
        return {"ok": True, "message": "正在停手…", "status": status()}
    if current.get("running") and _pid_alive(current.get("pid")):
        _write_status(last="正在停手…", stopping=True)
    else:
        _write_status(running=False, last="已停手", stopping=False)
    # 先停 OBS，避免用户点了停手录像还在后台继续。CLI 进程通常没有 session，
    # 这里要按 OBS 状态把上一段收尾，否则重启时 StartRecord 会 500、旧视频一直变长。
    try:
        from dafeiyu.recording import auto  # noqa: PLC0415

        auto.force_stop("stopped")
    except Exception:
        pass
    th = _thread
    if th is not None and th.is_alive() and th is not threading.current_thread():
        th.join(1.5)
    alive = bool(th is not None and th.is_alive())
    if not alive:
        _write_status(running=False, last="已停手", stopping=False)
        _host.clear_stop()
    return {"ok": True, "message": "正在停手…" if alive else "已停手",
            "status": status()}


__all__ = ["start_autoplay", "stop_autoplay", "status"]
