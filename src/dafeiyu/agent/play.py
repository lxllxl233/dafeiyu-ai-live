"""让她玩：启动游戏后自动跑一局。

这不是「点一下 Steam 就完事」，而是一个可停止的本地回合循环：
- 战斗：复用 `tools/advisor.py` 的本地算账（伤害/格挡/斩杀），不靠模型猜；
- 奖励：复用 `sts2.py rewards` 的自动挑牌/空收；
- 地图：复用 `sts2.py route` 的路线计划，按下一格坐标选；
- 事件/营火/商店：保守选择，遇到未知界面就停，不乱按。

所有进展写 `logs/autoplay.json`，桌面右键和管理端都能看；停手通过
`logs/autoplay.stop` 跨进程通知，不依赖同一个 Python 进程。
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

from .. import paths

STATUS_PATH = paths.LOGS / "autoplay.json"
STOP_PATH = paths.LOGS / "autoplay.stop"
LOG_PATH = paths.LOGS / "autoplay.jsonl"

_lock = threading.RLock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_speech_until = 0.0
_ui_picks: set[str] = set()
_state: dict = {
    "running": False, "screen": "", "floor": 0, "hp": 0, "max_hp": 0,
    "step": 0, "last": "", "error": "", "started_at": 0, "updated_at": 0,
    "game": "", "pid": 0, "mode": "local", "pace": "voice",
    # LLM 实际 token（来自 API 的 usage，不是按字数估算）
    "tokens_in": 0, "tokens_out": 0, "tokens_total": 0, "llm_calls": 0,
    "tokens_last": 0,
    # 录像/停止状态
    "recording": False, "record_path": "", "record_error": "",
    "stopping": False,
}


def _write_status(**updates) -> dict:
    with _lock:
        _state.update(updates)
        _state["updated_at"] = time.time()
        try:
            paths.LOGS.mkdir(parents=True, exist_ok=True)
            STATUS_PATH.write_text(json.dumps(_state, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        except OSError:
            pass
        return dict(_state)


def _log(msg: str, **data) -> None:
    row = {"ts": time.time(), "msg": msg, **data}
    try:
        paths.LOGS.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _stopping() -> bool:
    return _stop.is_set() or STOP_PATH.exists()


def _add_llm_usage(usage: dict | None, run_id: str = "") -> dict:
    """累计 API 返回的 usage；失败重试的 token 也算，观众看到的是真账单。"""
    usage = usage if isinstance(usage, dict) else {}
    try:
        tin = int(usage.get("input") or 0)
        tout = int(usage.get("output") or 0)
        total = int(usage.get("total") or (tin + tout))
    except (TypeError, ValueError):
        tin = tout = total = 0
    if total <= 0 and tin <= 0 and tout <= 0:
        return {"input": 0, "output": 0, "total": 0}
    with _lock:
        _state["tokens_in"] = int(_state.get("tokens_in") or 0) + tin
        _state["tokens_out"] = int(_state.get("tokens_out") or 0) + tout
        _state["tokens_total"] = int(_state.get("tokens_total") or 0) + total
        _state["llm_calls"] = int(_state.get("llm_calls") or 0) + 1
        _state["tokens_last"] = total
        _state["updated_at"] = time.time()
        try:
            STATUS_PATH.write_text(json.dumps(_state, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        except OSError:
            pass
    _log("llm_usage", run_id=run_id, input=tin, output=tout, total=total,
         cumulative=int(_state.get("tokens_total") or 0))
    return {"input": tin, "output": tout, "total": total}


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                                ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _import_tools():
    """延迟导入：桌宠启动时不加载游戏兼容层。"""
    tools = str(paths.TOOLS)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import advisor  # noqa: PLC0415
    import sts2  # noqa: PLC0415
    return sts2, advisor


def status() -> dict:
    """优先读文件，管理端和桌宠进程看到同一份。"""
    try:
        raw = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            with _lock:
                _state.update({k: raw.get(k, v) for k, v in _state.items()})
            return {**_state, "stop_requested": STOP_PATH.exists()}
    except (OSError, ValueError):
        pass
    with _lock:
        return {**_state, "stop_requested": STOP_PATH.exists()}


def _say(text: str) -> None:
    _remember_speech(text)
    try:
        sts2, _ = _import_tools()
        sts2.overlay_say("思路", text, "")
    except Exception:
        pass


def _start_autoplay_recording(game: str, mode: str) -> None:
    """自动玩默认录像；OBS 不在就尝试拉起，失败只记状态，不挡玩法。"""
    try:
        from ..recording import auto  # noqa: PLC0415

        res = auto.start_session(game, note=f"自动玩 · {mode}")
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    _write_status(
        recording=bool(res.get("ok")),
        record_path=str(res.get("path") or ""),
        record_error="" if res.get("ok") else str(res.get("error") or "录像启动失败"),
    )
    _log("record_start" if res.get("ok") else "record_failed",
         path=res.get("path"), error=res.get("error"))


def _finish_autoplay_recording(result: str = "") -> dict:
    try:
        from ..recording import auto  # noqa: PLC0415

        res = auto.stop_session(result=result)
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    _write_status(recording=False,
                  record_path=str(res.get("path") or _state.get("record_path") or ""),
                  record_error="" if res.get("ok") else str(res.get("error") or ""))
    _log("record_stop" if res.get("ok") else "record_stop_failed",
         path=res.get("path"), error=res.get("error"))
    return res


def _settings() -> tuple[str, str]:
    """读本机配置；菜单/控制台刚改过也能在下一步生效。"""
    try:
        from ..core.config import LiveConfig

        cfg = LiveConfig.load()
        mode = str(cfg.autoplay_mode or "local").strip().lower()
        pace = str(cfg.autoplay_pace or "voice").strip().lower()
        return (mode if mode in ("local", "llm") else "local",
                pace if pace in ("voice", "normal") else "voice")
    except Exception:
        return "local", "voice"


def _remember_speech(text: str) -> None:
    """按字数估一个「这句话大概播完」的时间，给语音模式留出气口。"""
    global _speech_until
    clean = " ".join(str(text or "").split())
    if not clean:
        return
    # 中文 TTS 约 4-5 字/秒，再加一点合成/出场余量；不要无限等。
    seconds = min(10.0, max(1.6, len(clean) * 0.22 + 1.0))
    _speech_until = max(_speech_until, time.time() + seconds)


def _sleep(seconds: float) -> None:
    end = time.time() + max(0.0, float(seconds))
    while time.time() < end:
        if _stop.is_set() or STOP_PATH.exists():
            return
        time.sleep(min(0.2, max(0.02, end - time.time())))


def _pace() -> None:
    """normal = 旧版快节奏；voice = 等上一句估计播完，至少留 2 秒。"""
    if _stopping():
        return
    _, pace = _settings()
    if pace == "normal":
        _sleep(0.7)
        return
    _sleep(max(2.0, _speech_until - time.time()))


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


def _set_from_state(state: dict, last: str = "") -> None:
    floor, hp, max_hp = _run_meta(state)
    _write_status(screen=_screen(state), floor=floor, hp=hp, max_hp=max_hp,
                  last=last or _screen(state))


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


def _choose_map(sts2, state: dict) -> str:
    nodes, avail = _map_nodes(state)
    if not avail:
        return "MAP_NO_AVAILABLE"
    # 首次进图 / 计划过期：让兼容层选一条 brps 最高的路线并落盘。
    try:
        _, _ = _call(sts2, sts2.cmd_route, commit=0)
    except Exception as exc:  # noqa: BLE001
        _log("route_failed", error=f"{type(exc).__name__}: {exc}")
    plan = _route_plan(sts2)
    path = [p for p in (plan.get("path") or []) if isinstance(p, list) and len(p) >= 2]
    target = None
    if path:
        cur = next((n for n in nodes if n.get("is_current")), None)
        if cur:
            idx = next((i for i, p in enumerate(path)
                        if int(p[0]) == int(cur.get("row", -1))
                        and int(p[1]) == int(cur.get("col", -1))), None)
            if idx is not None and idx + 1 < len(path):
                target = (int(path[idx + 1][0]), int(path[idx + 1][1]))
        if target is None:
            target = (int(path[0][0]), int(path[0][1]))
    chosen = None
    if target:
        chosen = next((n for n in avail
                       if (int(n.get("row", -1)), int(n.get("col", -1))) == target), None)
    if chosen is None:
        # 保守优先级：血少先营火，钱多先商店，普通怪优先，精英最后。
        _, hp, mhp = _run_meta(state)
        pct = hp / mhp if mhp else 1.0
        priority = {"RestSite": 0 if pct < 0.70 else 4,
                     "Shop": 1, "Monster": 2, "Event": 3, "Unknown": 3,
                     "Treasure": 2, "Elite": 8, "Boss": 9}
        chosen = min(avail, key=lambda n: priority.get(str(n.get("node_type")), 5))
    idx = avail.index(chosen)
    act = "choose_map_node"
    if act not in _acts(state):
        return "MAP_ACTION_MISSING"
    sts2.act(act, option_index=idx)
    _log("map", idx=idx, row=chosen.get("row"), col=chosen.get("col"),
         type=chosen.get("node_type"))
    return f"MAP {chosen.get('node_type')} ({chosen.get('row')},{chosen.get('col')})"


# ---------------------------------------------------------------- 战斗
def _combat_llm(tools, planner, state: dict, game: str) -> str:
    """战斗回合交给 LangGraph + LLM；非战斗界面仍用本地快速处理。"""
    from .graph import run_turn  # noqa: PLC0415

    run = state.get("run") or {}
    goal = (f"第 {run.get('floor', '?')} 层，HP {run.get('current_hp', '?')}/"
            f"{run.get('max_hp', '?')}。打完整回合：先看局面，能斩杀就斩杀，"
            "不能斩杀先挡满，最后 end。")
    _write_status(last="LLM 思考…")
    try:
        result = run_turn(
            goal, tools=tools, planner=planner, dry_run=False,
            game=game or "slay-the-spire-2", max_attempts=2,
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
            from .planner import LLMPlanner  # noqa: PLC0415
            from .tools import Sts2Tools  # noqa: PLC0415
            from ..core.config import LiveConfig  # noqa: PLC0415

            cfg = LiveConfig.load()
            llm_tools = Sts2Tools(game)
            llm_planner = LLMPlanner(cfg, model=cfg.autoplay_llm_model or cfg.llm_model,
                                      timeout=25.0)
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
        _say("我去打一局，先看开局。")
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
                _write_status(last="GAME_OVER，回主菜单")
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
                _write_status(running=False, error=last, last="未知界面，停手")
                _log("unknown_screen", last=last)
                return
            if last.startswith("LLM_FAILED"):
                _write_status(running=False, error=last, last="LLM 执行失败，停手")
                _log("llm_stop", last=last)
                return
            if last in ("CHEST", "REST no options", "MAP_NO_AVAILABLE", "REWARD_FAILED"):
                stalled += 1
                if stalled >= 4:
                    _write_status(running=False, error=f"界面没有进展：{last}",
                                  last="界面卡住，停手")
                    _log("stalled", last=last)
                    return
            else:
                stalled = 0
            _pace()
        _write_status(running=False, last="已停手")
        _log("stop")
    except BaseException as exc:  # noqa: BLE001
        _write_status(running=False, error=f"{type(exc).__name__}: {exc}", last="异常停手")
        _log("fatal", error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            _finish_autoplay_recording("autoplay_end")
        except Exception:
            pass
        with _lock:
            _thread = None
            _state["stopping"] = False
            _state["running"] = False
            _write_status(stopping=False, running=False)


def start_autoplay(game: str | None = None, *, mode: str | None = None,
                   pace: str | None = None) -> dict:
    """启动游戏并开始自动玩；立即返回，实际运行在后台线程。"""
    global _thread
    from ..core.config import LiveConfig
    from ..games.launch import launch_game

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
        from .graph import StateGraph  # noqa: PLC0415
        from .planner import LLMPlanner  # noqa: PLC0415

        if StateGraph is None:
            return {"ok": False, "message": "LLM 玩需要安装 langgraph：pip install -e .[agent]",
                    "mode": mode}
        ok, why = LLMPlanner(cfg, model=cfg.autoplay_llm_model or cfg.llm_model).available()
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
        if game != "slay-the-spire-2":
            return {"ok": False, "message": "当前只有杀戮尖塔 2 配了自动玩法"}
        try:
            STOP_PATH.unlink()
        except OSError:
            pass
        launched = launch_game(game)
        if not launched.get("ok"):
            return {"ok": False, "message": launched.get("error") or "游戏启动失败",
                    "launch": launched}
        _stop.clear()
        _write_status(running=True, game=game, error="", last="启动中…",
                      screen="", step=0, mode=mode, pace=pace,
                      started_at=time.time(), pid=os.getpid(),
                      tokens_in=0, tokens_out=0, tokens_total=0, llm_calls=0,
                      tokens_last=0, recording=False, record_path="", record_error="",
                      stopping=False)
        _thread = threading.Thread(target=_run, args=(game, mode),
                                   name="dafeiyu-autoplay", daemon=True)
        _thread.start()
        return {"ok": True, "message": "已开始：先启动游戏，再让她打",
                "mode": mode, "pace": pace, "launch": launched, "status": status()}


def stop_autoplay() -> dict:
    """跨进程停手：写停止文件；本进程的录像立即收尾，不等 LLM 回来。"""
    global _speech_until
    try:
        paths.LOGS.mkdir(parents=True, exist_ok=True)
        STOP_PATH.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass
    _stop.set()
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
    return {"ok": True, "message": "正在停手…" if alive else "已停手",
            "status": status()}


__all__ = ["start_autoplay", "stop_autoplay", "status"]
