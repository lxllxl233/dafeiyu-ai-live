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
_state: dict = {
    "running": False, "screen": "", "floor": 0, "hp": 0, "max_hp": 0,
    "step": 0, "last": "", "error": "", "started_at": 0, "updated_at": 0,
    "game": "", "pid": 0,
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
    try:
        sts2, _ = _import_tools()
        sts2.overlay_say("思路", text, "")
    except Exception:
        pass


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
        for name in ("continue_run", "open_character_select"):
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
def _combat(sts2, advisor, state: dict) -> str:
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
        _, out = _call(sts2, sts2.cmd_batch, json.dumps(spec, ensure_ascii=False))
        _log("combat", dmg=plan.get("dmg"), taken=plan.get("taken"),
             kills=plan.get("kills"), out=out.strip().splitlines()[-1:] or [""])
        return f"COMBAT {plan.get('dmg', 0)}dmg"
    # 没有可打计划：结束回合（只在合法时）。
    if "end_turn" in acts:
        sts2.act("end_turn")
        return "COMBAT end"
    return "WAIT_COMBAT"


# ---------------------------------------------------------------- 非战斗
def _reward(sts2, state: dict) -> str:
    try:
        summary, _ = _call(sts2, sts2.cmd_rewards, pick="auto", quiet=True)
        deck = (summary or {}).get("deck")
        return f"REWARD deck={deck}"
    except Exception as exc:  # noqa: BLE001
        _log("reward_failed", error=f"{type(exc).__name__}: {exc}")
        return "REWARD_FAILED"


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
        mn = int(sel.get("min_select") or 0)
        for i in range(max(1, min(mn, len(cards)))):
            try:
                sts2.act("select_deck_card", option_index=i)
            except Exception:
                break
        if "confirm_selection" in _acts(sts2.get_state()):
            sts2.act("confirm_selection")
        return "CARD select"
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
    if "claim_reward" in _acts(state):
        try:
            sts2.act("claim_reward", option_index=0)
        except Exception:
            pass
    if "collect_rewards_and_proceed" in _acts(sts2.get_state()):
        sts2.act("collect_rewards_and_proceed")
    return "CHEST"


def _modal(sts2, state: dict) -> str:
    for name in ("proceed", "dismiss", "close"):
        if name in _acts(state):
            sts2.act(name)
            return f"MODAL {name}"
    return "MODAL unknown"


def _one_step(sts2, advisor, state: dict) -> str:
    screen = _screen(state)
    if screen == "GAME_OVER":
        return "GAME_OVER"
    if screen in ("MAIN_MENU", "CHARACTER_SELECT"):
        new = _ensure_run(sts2, state)
        return "START_RUN" if new else f"WAIT_{screen}"
    if screen == "COMBAT":
        return _combat(sts2, advisor, state)
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


def _run(game: str) -> None:
    global _thread
    sts2 = advisor = None
    try:
        sts2, advisor = _import_tools()
        _write_status(running=True, game=game, error="", step=0,
                      started_at=time.time(), last="启动游戏…")
        state = _wait_game(sts2, timeout=150)
        if state is None:
            _write_status(running=False, error="启动超时：游戏 API 未就绪")
            return
        _set_from_state(state, "已连接游戏")
        _say("我去打一局，先看开局。")
        _log("start", game=game)
        step = 0
        while not _stop.is_set() and not STOP_PATH.exists():
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
                _say("这局结束了，先停手。")
                _write_status(running=False, last="GAME_OVER")
                _log("game_over")
                return
            try:
                last = _one_step(sts2, advisor, state)
            except BaseException as exc:  # noqa: BLE001
                last = f"ERROR {type(exc).__name__}: {exc}"
                _log("step_error", error=last)
                _write_status(error=last)
                time.sleep(1.2)
            step += 1
            _set_from_state(sts2.get_state() if not last.startswith("WAIT") else state, last)
            _write_status(step=step)
            _log("step", step=step, last=last)
            if last.startswith("UNKNOWN") or last.endswith("unknown"):
                _write_status(running=False, error=last, last="未知界面，停手")
                _log("unknown_screen", last=last)
                return
            time.sleep(0.7)
        _write_status(running=False, last="已停手")
        _log("stop")
    except BaseException as exc:  # noqa: BLE001
        _write_status(running=False, error=f"{type(exc).__name__}: {exc}", last="异常停手")
        _log("fatal", error=f"{type(exc).__name__}: {exc}")
    finally:
        with _lock:
            _thread = None


def start_autoplay(game: str | None = None) -> dict:
    """启动游戏并开始自动玩；立即返回，实际运行在后台线程。"""
    global _thread
    from ..core.config import LiveConfig
    from ..games.launch import launch_game

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
        game = str(game or LiveConfig.load().game or "").strip()
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
                      screen="", step=0, started_at=time.time(), pid=os.getpid())
        _thread = threading.Thread(target=_run, args=(game,),
                                   name="dafeiyu-autoplay", daemon=True)
        _thread.start()
        return {"ok": True, "message": "已开始：先启动游戏，再让她打",
                "launch": launched, "status": status()}


def stop_autoplay() -> dict:
    """跨进程停手：写停止文件，桌面端/管理端都能调用。"""
    try:
        paths.LOGS.mkdir(parents=True, exist_ok=True)
        STOP_PATH.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass
    _stop.set()
    current = status()
    if current.get("running") and _pid_alive(current.get("pid")):
        _write_status(last="正在停手…")
    else:
        _write_status(running=False, last="已停手")
    return {"ok": True, "message": "已请求停手", "status": status()}


__all__ = ["start_autoplay", "stop_autoplay", "status"]
