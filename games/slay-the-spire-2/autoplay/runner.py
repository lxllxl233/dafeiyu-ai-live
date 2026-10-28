"""让她玩：启动游戏后自动跑一局。

这不是「点一下 Steam 就完事」，而是一个可停止的本地回合循环：
- 战斗：复用 `tools/advisor.py` 的本地算账（伤害/格挡/斩杀），不靠模型猜；
- 奖励：复用 `sts2.py rewards` 的自动挑牌/空收；
- 地图：复用 `sts2.py route` 的路线计划，按下一格坐标选；
- 事件/营火/商店：保守选择，遇到未知界面就停，不乱按。

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

from ..decision.prompts import SYSTEM_PROMPT, combat_goal

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
_ui_picks: set[str] = set()
_timeline_prev: dict = {"screen": "", "floor": 0, "run_id": ""}


def bind_host(host: AutoplayHost) -> AutoplayHost:
    """由核心 facade/插件显式注入 host；替换时同步旧测试用的模块别名。"""
    global _host, _lock, _stop, _state, _speech_until
    global STATUS_PATH, STOP_PATH, LOG_PATH
    _host = host
    _lock = host.lock
    _stop = host.stop_event
    _state = host.state
    _speech_until = host.speech_until
    STATUS_PATH = host.status_path
    STOP_PATH = host.stop_path
    LOG_PATH = host.log_path
    return host


def _write_status(**updates) -> dict:
    return _host.set_status(**updates)


def _log(msg: str, **data) -> None:
    _host.log(msg, **data)


def _stopping() -> bool:
    return _host.stopping() or _stop.is_set() or STOP_PATH.exists()


def _add_llm_usage(usage: dict | None, run_id: str = "") -> dict:
    return _host.add_usage(usage, run_id)


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


def _start_autoplay_recording(game: str, mode: str) -> None:
    _host.start_recording(note=f"自动玩 · {mode}")


def _finish_autoplay_recording(result: str = "") -> dict:
    return _host.stop_recording(result)


def _settings() -> tuple[str, str]:
    return _host.settings()


def _remember_speech(text: str) -> None:
    global _speech_until
    _host.remember_speech(text)
    _speech_until = _host.speech_until


def _sleep(seconds: float) -> None:
    _host.sleep(seconds)


def _pace() -> None:
    _host.wait_voice()


def _call(sts2, fn, *args, **kwargs):
    """跑会打印的 sts2 函数，吞掉 stdout，保留结果。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
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
    run = state.get("run") or {}
    return (int(run.get("floor") or 0), int(run.get("current_hp") or run.get("hp") or 0),
            int(run.get("max_hp") or 0))


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
def _ensure_run(sts2, state: dict) -> dict | None:
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
        cs = state.get("character_select") or {}
        selected = str(cs.get("selected_character_id") or "")
        if selected != "IRONCLAD":
            chars = cs.get("characters") or []
            idx = next((c.get("index") for c in chars
                        if str(c.get("character_id") or "").upper() == "IRONCLAD"), None)
            if idx is not None:
                sts2.act("select_character", option_index=int(idx))
                state = sts2.get_state()
                cs = state.get("character_select") or {}
        asc = int(cs.get("ascension") or 0)
        mx = int(cs.get("max_ascension") or 0)
        for _ in range(max(0, mx - asc)):
            if "increase_ascension" not in _acts(state):
                break
            sts2.act("increase_ascension")
            state = sts2.get_state()
        if "embark" in _acts(state):
            sts2.act("embark")
            return sts2.get_state()
    return None


# ---------------------------------------------------------------- 地图
def _map_nodes(state: dict) -> tuple[list[dict], list[dict]]:
    nodes = [n for n in ((state.get("map") or {}).get("nodes") or [])
             if isinstance(n, dict)]
    return nodes, [n for n in nodes if n.get("is_available")]


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


def _choose_map(sts2, state: dict) -> str:
    nodes, avail = _map_nodes(state)
    if not avail:
        return "MAP_NO_AVAILABLE"

    plan = _route_plan(sts2)
    target = _route_target(plan, nodes, avail) if _route_current(plan, state) else None
    if target is not None and not _state.get("route_seq"):
        # 计划是上一局/启动前生成的，只要身份和下一格仍有效就直接接着走，
        # 同时把路线代号补到控制台，避免“明明有计划但界面空着”。
        _publish_route(sts2, state, reason="reuse")
    if target is None:
        # 开局/换幕/换图/路线断了：只在这里规划一次并 commit，后面按计划走。
        output = ""
        try:
            _, output = _call(sts2, sts2.cmd_route, commit=0)
        except Exception as exc:  # noqa: BLE001
            _log("route_failed", error=f"{type(exc).__name__}: {exc}")
        plan = _publish_route(sts2, state, output, reason="auto")
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

    run = state.get("run") or {}
    goal = combat_goal(run)
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
    _add_llm_usage(result.get("tokens"), str(result.get("run_id") or ""))
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


def _combat_local(sts2, advisor, state: dict) -> str:
    acts = set(_acts(state))
    if not ({"play_card", "end_turn"} & acts):
        return "WAIT_COMBAT"
    plan = None
    try:
        plan = advisor.build_plan(state)
    except Exception as exc:  # noqa: BLE001
        _log("advisor_failed", error=f"{type(exc).__name__}: {exc}")
    if plan and plan.get("steps"):
        thought = ""
        meme = ""
        if int(state.get("turn") or 0) % 3 == 1:
            thought = (f"它 {plan.get('incoming', 0)}，这手打 {plan.get('dmg', 0)}"
                       if plan.get("incoming") else f"这手打 {plan.get('dmg', 0)} 点")
            cands = advisor.candidates(plan)
            meme = cands[0] if cands else ""
        spec = advisor.spec_from_plan(plan, thought, meme)
        _remember_speech(" ".join(x for x in (thought, meme) if x))
        _, out = _call(sts2, sts2.cmd_batch, json.dumps(spec, ensure_ascii=False))
        _log("combat", dmg=plan.get("dmg"), taken=plan.get("taken"),
             kills=plan.get("kills"), out=out.strip().splitlines()[-1:] or [""])
        return f"COMBAT {plan.get('dmg', 0)}dmg"
    # 没有可打计划：结束回合（只在合法时）。
    if "end_turn" in acts:
        sts2.act("end_turn")
        return "COMBAT end"
    return "WAIT_COMBAT"


def _combat(sts2, advisor, state: dict, *, mode: str = "local",
            llm_tools=None, llm_planner=None, game: str = "") -> str:
    if mode == "llm" and llm_tools is not None and llm_planner is not None:
        out = _combat_llm(llm_tools, llm_planner, state, game)
        if out == "STOPPED" or _stopping():
            return "STOPPED"
        if out.startswith(("LLM_FAILED_PLAN", "LLM_FAILED_VALIDATE", "LLM_FAILED_EXECUTE")):
            # 规划/验算/执行失败时重新读当前局面，用本地算账兜底；执行失败
            # 也可能是 LLM 只打了一半，此时更不能把整局停在这里。
            _log("llm_fallback", error=out)
            try:  # 执行失败可能已经打了一半，必须按最新手牌重算，不能用旧 state
                fresh = sts2.get_state()
            except Exception:
                fresh = state
            local = _combat_local(sts2, advisor, fresh)
            return f"LLM 失败，本地兜底（{local}）"
        return out
    return _combat_local(sts2, advisor, state)


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


def _selection_need(sel: dict) -> int:
    """这一屏要选几张。

    游戏在「选 2 张普通牌」里会把 min/max 都给 0，只能从 prompt 读；
    旧实现固定只点第一张，选完还停在 CARD_SELECTION，一屏能卡 20 步。
    """
    for key in ("max_select", "max", "min_select", "min"):
        try:
            n = int(sel.get(key) or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            return n
    prompt = re.sub(r"\[[^\]]*\]", "", str(sel.get("prompt") or ""))
    m = re.search(r"选择\s*(\d+)\s*张", prompt)
    return int(m.group(1)) if m else 1


def _blind_selection(sel: dict) -> bool:
    """mod 对「事件选 N 张」只回 pending、selected_count 永远是 0。"""
    try:
        mn = int(sel.get("min_select") or 0)
        mx = int(sel.get("max_select") or 0)
    except (TypeError, ValueError):
        return False
    return mn == 0 and mx == 0 and _selection_need(sel) >= 2


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
            return "CARD skip"
    if "select_deck_card" in acts and cards:
        need = _selection_need(sel)
        try:
            already = int(sel.get("selected_count") or sel.get("selected") or 0)
        except (TypeError, ValueError):
            already = 0
        if _blind_selection(sel) and hasattr(sts2, "ui_pick_cards"):
            key = f"{state.get('run_id','')}|{sel.get('prompt','')}|" \
                  f"{','.join(str(c.get('card_id') or c.get('name') or '') for c in cards)}"
            if key not in _ui_picks:
                if len(_ui_picks) > 40:
                    _ui_picks.clear()
                _ui_picks.add(key)
                try:
                    clicked = bool(sts2.ui_pick_cards(need))
                except Exception:
                    clicked = False
                if clicked:
                    time.sleep(0.8)
                    now = sts2.get_state()
                    if "confirm_selection" in _acts(now):
                        sts2.act("confirm_selection")
                        now = sts2.get_state()
                    if _screen(now) != "CARD_SELECTION":
                        return f"CARD ui {need} -> {_screen(now)}"
                    _log("ui_pick_still_selection", need=need)
                    return f"CARD ui wait {need}"
        if already < need:
            # 一次只点一张，下一轮重新读 selected_count；一屏要选 2 张时
            # 不能重复点 index 0（旧实现就是这样原地打转）。
            idx = min(already, len(cards) - 1)
            try:
                sts2.act("select_deck_card", option_index=int(idx))
                return f"CARD select {already + 1}/{need}"
            except Exception:
                return "CARD select failed"
        now = sts2.get_state()
        if "confirm_selection" in _acts(now):
            sts2.act("confirm_selection")
            return "CARD confirm"
        if _screen(now) != "CARD_SELECTION":
            return f"CARD -> {_screen(now)}"
        return f"CARD wait {already}/{need}"
    if "confirm_selection" in acts:
        sts2.act("confirm_selection")
        return "CARD confirm"
    if "skip_reward_cards" in acts:
        sts2.act("skip_reward_cards")
        return "CARD skip"
    if "proceed" in acts:
        sts2.act("proceed")
        return "CARD proceed"
    return "CARD unknown"


def _rest(sts2, state: dict) -> str:
    opts = [o for o in ((state.get("rest") or {}).get("options") or []) if isinstance(o, dict)]
    if not opts:
        # 有些版本选完休息/锻造后 REST 只剩 proceed，不点会原地循环。
        if "proceed" in _acts(state):
            sts2.act("proceed")
            return "REST proceed"
        return "REST no options"
    _, hp, mhp = _run_meta(state)
    pct = hp / mhp if mhp else 1.0
    want = ("休息", "睡觉", "回复", "heal") if pct < 0.70 else ("锻造", "升级", "smith", "upgrade")
    idx = next((i for i, o in enumerate(opts)
                if any(w in str(o.get("title") or o.get("name") or "") for w in want)), 0)
    action = "choose_rest_option" if "choose_rest_option" in _acts(state) else "proceed"
    sts2.act(action, option_index=idx) if action == "choose_rest_option" else sts2.act(action)
    return f"REST {action} {idx}"


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
    if "leave_shop" in _acts(state):
        sts2.act("leave_shop")
        return "SHOP leave"
    if "proceed" in _acts(state):
        sts2.act("proceed")
        return "SHOP proceed"
    return "SHOP unknown"


def _chest(sts2, state: dict) -> str:
    acts = _acts(state)
    if "open_chest" in acts:
        sts2.act("open_chest")
        return "CHEST open"
    if "choose_treasure_relic" in acts:
        sts2.act("choose_treasure_relic", option_index=0)
        return "CHEST relic"
    if "claim_reward" in acts:
        sts2.act("claim_reward", option_index=0)
        return "CHEST claim"
    if "collect_rewards_and_proceed" in acts:
        sts2.act("collect_rewards_and_proceed")
        return "CHEST collect"
    if "proceed" in acts:
        sts2.act("proceed")
        return "CHEST proceed"
    return "CHEST unknown"


def _modal(sts2, state: dict) -> str:
    # 主菜单的 abandon_run 会弹确认框，只认 proceed/dismiss 会停手。
    for name in ("proceed", "confirm_modal", "dismiss", "close"):
        if name in _acts(state):
            sts2.act(name)
            return f"MODAL {name}"
    return "MODAL unknown"


def _one_step(sts2, advisor, state: dict, *, mode: str = "local",
              llm_tools=None, llm_planner=None, game: str = "") -> str:
    if _stopping():
        return "STOPPED"
    screen = _screen(state)
    if screen == "GAME_OVER":
        return "GAME_OVER"
    if screen in ("MAIN_MENU", "CHARACTER_SELECT"):
        new = _ensure_run(sts2, state)
        return "START_RUN" if new else f"WAIT_{screen}"
    if screen == "COMBAT":
        return _combat(sts2, advisor, state, mode=mode, llm_tools=llm_tools,
                       llm_planner=llm_planner, game=game)
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
    if screen == "MODAL":
        return _modal(sts2, state)
    return f"UNKNOWN {screen}"


# ---------------------------------------------------------------- 循环
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
    global _thread, _speech_until
    sts2 = advisor = llm_tools = llm_planner = None
    try:
        sts2, advisor = _import_tools()
        if mode == "llm":
            from dafeiyu.agent.planner import LLMPlanner  # noqa: PLC0415
            from ..decision.agent_tools import Sts2Tools  # noqa: PLC0415
            from dafeiyu.core.config import LiveConfig  # noqa: PLC0415

            cfg = LiveConfig.load()
            llm_tools = Sts2Tools(game)
            llm_planner = LLMPlanner(
                cfg, model=cfg.autoplay_llm_model or cfg.llm_model,
                timeout=25.0, system_prompt=SYSTEM_PROMPT,
            )
        _host.speech_until = 0.0
        _speech_until = 0.0
        _write_status(running=True, game=game, error="", step=0, mode=mode,
                      started_at=time.time(), last="启动游戏…")
        if not _stopping():
            _start_autoplay_recording(game, mode)
        if _stopping():
            _write_status(last="已停手")
            return
        state = _wait_game(sts2, timeout=150)
        if state is None:
            _write_status(running=False, error="启动超时：游戏 API 未就绪")
            return
        _set_from_state(state, "已连接游戏")
        # 开局不主动播报招呼；第一条台词从实际战斗/事件开始。
        _pace()
        _log("start", game=game, mode=mode)
        step = 0
        stalled = 0
        while not _stopping():
            if step >= 5000:
                _write_status(running=False, last="达到步数上限，停手")
                break
            try:
                state = sts2.get_state()
            except Exception as exc:  # noqa: BLE001
                _write_status(last="读取游戏状态失败", error=f"{type(exc).__name__}: {exc}")
                time.sleep(1.5)
                continue
            if not state.get("screen"):
                _write_status(last="等待游戏…")
                time.sleep(1.0)
                continue
            _set_from_state(state)
            if _screen(state) == "GAME_OVER":
                _say("这局结束了，我回主菜单再来一局。")
                _write_status(last="GAME_OVER，回主菜单", route_seq="",
                              route_option=None, route_note="", route_floor=0)
                _pace()
                new_state = _ensure_run(sts2, state)
                if new_state:
                    _set_from_state(new_state, "新一局")
                    _log("restart_run")
                    continue
                _write_status(running=False, error="GAME_OVER 无法返回主菜单",
                              last="GAME_OVER")
                _log("game_over", restart=False)
                return
            try:
                last = _one_step(sts2, advisor, state, mode=mode,
                                 llm_tools=llm_tools, llm_planner=llm_planner, game=game)
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
            _set_from_state(sts2.get_state() if not last.startswith("WAIT") else state, last)
            _write_status(step=step, error="" if not last.startswith("ERROR") else _state.get("error", ""))
            _log("step", step=step, last=last)
            if last.startswith("UNKNOWN") or last.endswith("unknown"):
                _write_status(error=last, last="未知界面，正在收尾…", stopping=True)
                _log("unknown_screen", last=last)
                return
            if last.startswith("LLM_FAILED"):
                _write_status(error=last, last="LLM 执行失败，正在收尾…", stopping=True)
                _log("llm_stop", last=last)
                return
            if last in ("CHEST", "REST no options", "MAP_NO_AVAILABLE", "REWARD_FAILED"):
                stalled += 1
                if stalled >= 4:
                    _write_status(error=f"界面没有进展：{last}",
                                  last="界面卡住，正在收尾…", stopping=True)
                    _log("stalled", last=last)
                    return
            else:
                stalled = 0
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
    # 先停 OBS，避免用户点了停手录像还在后台继续。
    try:
        _finish_autoplay_recording("stopped")
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
