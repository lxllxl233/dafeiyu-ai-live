"""《我的世界》生存开荒的自动玩循环。

和 toy-duel / VS / STS2 共用 ``AutoplayHost``：状态、停止、日志、Token 都由核心
提供，本包只决定「下一步做什么」。

- 所有观察/动作只走本地桥（``mc-bridge/v1``），不找窗口、不切前台、不发键盘；
- ``local``：按开荒顺序的本地策略（砍树→工作台→木镐→圆石→熔炉→火把→搭棚）；
- ``llm``：每 ``LLM_INTERVAL_S`` 秒问一次模型要 1–5 步，模型失败/不可执行时
  记 ``llm_fallback`` 并降级到本地策略；
- 这是实时游戏，循环不调用 ``host.wait_voice()``，否则夜里站着会被僵尸打死。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from dafeiyu.agent.host import AutoplayHost
from dafeiyu.core.config import LiveConfig

from . import rules
from .engine import GAME_ID, MinecraftTools

DISPLAY_NAME = "我的世界"
LLM_INTERVAL_S = 25.0
LAUNCH_WAIT_S = 90.0
MAX_FAILURES = 6
PICKAXES = ("wooden_pickaxe", "stone_pickaxe", "iron_pickaxe")
WEAPONS = ("wooden_sword", "stone_sword", "iron_sword",
           "wooden_axe", "stone_axe", "iron_axe")

_host = AutoplayHost(GAME_ID)
_lock = _host.lock
_thread: threading.Thread | None = None
_planner = None


def bind_host(host: AutoplayHost) -> AutoplayHost:
    global _host, _lock
    if _thread is not None and _thread.is_alive() and host is not _host:
        _host.log("bind_host_ignored", reason="minecraft thread alive")
        return _host
    _host = host
    _lock = host.lock
    return host


def _active() -> bool:
    return bool(_thread is not None and _thread.is_alive())


def _status() -> dict:
    return _host.status()


def _log(msg: str, **data) -> None:
    try:
        _host.log(msg, **data)
    except Exception:
        pass


def _add_usage(usage: dict | None, note: str, step: int) -> None:
    try:
        _host.add_usage(usage, note=note, step=step)
    except Exception:
        pass


def _start_values() -> dict:
    """读 ``game.json`` 的 start.options 和 config.json 里保存的值。"""
    try:
        from dafeiyu.games.start_options import load_values

        raw = json.loads((Path(__file__).with_name("game.json"))
                         .read_text(encoding="utf-8"))
        spec = raw.get("start") if isinstance(raw.get("start"), dict) else {}
        return load_values(GAME_ID, spec)
    except Exception:  # noqa: BLE001
        return {}


def _difficulty() -> str:
    value = str(_start_values().get("difficulty") or "normal").strip().lower()
    return value if value in rules.DIFFICULTIES else "normal"


def _near(ctx: dict, name: str) -> int:
    return sum(1 for block in (ctx.get("nearby") or {}).get("blocks") or []
               if block.get("name") == name)


def _owned(ctx: dict, name: str) -> int:
    return int((ctx.get("inventory") or {}).get(name) or 0)


def _has(ctx: dict, *names: str) -> bool:
    return any(_owned(ctx, name) > 0 for name in names)


def _local_plan(ctx: dict, step: int) -> dict:
    """开荒顺序：先能活过夜，再追求石头/铁。"""
    screen = str(ctx.get("screen") or "")
    if screen in ("DEAD", "MENU", ""):
        return {"seq": [{"type": "wait", "seconds": 1.0}]}

    inventory = ctx.get("inventory") or {}
    hp = int(ctx.get("hp") or 0)
    food = int(ctx.get("food") or 0)
    shelter = float(ctx.get("shelter") or 0.0)
    phase = str(ctx.get("phase") or "day")
    pickaxe = next((name for name in PICKAXES if inventory.get(name)), "")
    mobs = (ctx.get("nearby") or {}).get("mobs") or []
    zombies = [mob for mob in mobs if mob.get("name") == "zombie"]

    # 1) 先吃饱；有生肉就烤，没煤/没炉子直接吃生肉也行（别等到饿死）。
    if food <= 6:
        if (inventory.get("beef") and ctx.get("furnace")
                and inventory.get("coal", 0) >= 1
                and str(ctx.get("source")) != "http"):
            return {"seq": [{"type": "smelt", "item": "beef", "count": 1}]}
        for item in ("cooked_beef", "bread", "beef"):
            if inventory.get(item):
                return {"seq": [{"type": "eat", "item": item}]}
        if any(mob.get("name") == "cow" for mob in mobs):
            return {"seq": [{"type": "goto", "target": "cow"},
                            {"type": "attack", "target": "cow"}]}

    # 2) 夜里躲/打：有棚最好，没有就先放方块，实在没有就挖材料。
    if phase == "night" and shelter < rules.SHELTER_FULL:
        if zombies and hp > 8 and _has(ctx, *WEAPONS):
            return {"seq": [{"type": "equip", "item": next(
                name for name in WEAPONS if inventory.get(name))},
                {"type": "goto", "target": "zombie"},
                {"type": "attack", "target": "nearest"}]}
        for block in ("cobblestone", "oak_planks", "oak_log"):
            if inventory.get(block):
                return {"seq": [{"type": "place", "block": block}]}
        if pickaxe and _near(ctx, "cobblestone") > 0:
            return {"seq": [{"type": "goto", "target": "stone"},
                            {"type": "mine", "block": "cobblestone", "count": 4}]}
        if inventory.get("oak_log"):
            return {"seq": [{"type": "craft", "item": "oak_planks", "count": 1}]}
        if _near(ctx, "oak_log") > 0:
            return {"seq": [{"type": "mine", "block": "oak_log", "count": 2}]}
        return {"seq": [{"type": "wait", "seconds": 1.0}]}

    # 3) 工作台是后面所有工具的入口。
    if not ctx.get("crafting_table"):
        if inventory.get("oak_planks", 0) >= 4:
            return {"seq": [{"type": "craft", "item": "crafting_table", "count": 1},
                            {"type": "place", "block": "crafting_table"}]}
        if inventory.get("oak_log"):
            return {"seq": [{"type": "craft", "item": "oak_planks", "count": 1}]}
        if _near(ctx, "oak_log") > 0:
            return {"seq": [{"type": "mine", "block": "oak_log", "count": 3}]}
        return {"seq": [{"type": "wait", "seconds": 1.0}]}

    # 4) 木镐：3 木板 + 2 木棍，合成后要拿在手上。
    if not pickaxe:
        planks = inventory.get("oak_planks", 0)
        sticks = inventory.get("stick", 0)
        if planks >= 3 and sticks >= 2:
            return {"seq": [{"type": "craft", "item": "wooden_pickaxe", "count": 1},
                            {"type": "equip", "item": "wooden_pickaxe"}]}
        if sticks < 2 and planks >= 2:
            return {"seq": [{"type": "craft", "item": "stick", "count": 1}]}
        if planks < 3 and inventory.get("oak_log"):
            return {"seq": [{"type": "craft", "item": "oak_planks", "count": 1}]}
        if _near(ctx, "oak_log") > 0:
            return {"seq": [{"type": "mine", "block": "oak_log", "count": 3}]}
        return {"seq": [{"type": "wait", "seconds": 1.0}]}

    # 5) 石头 → 熔炉；顺手留一点火把。
    if not ctx.get("furnace"):
        if inventory.get("cobblestone", 0) >= 8:
            return {"seq": [{"type": "craft", "item": "furnace", "count": 1},
                            {"type": "place", "block": "furnace"}]}
        if _near(ctx, "cobblestone") > 0:
            return {"seq": [{"type": "goto", "target": "stone"},
                            {"type": "mine", "block": "cobblestone", "count": 8}]}
    if inventory.get("coal", 0) < 2 and _near(ctx, "coal_ore") > 0:
        return {"seq": [{"type": "goto", "target": "coal"},
                        {"type": "mine", "block": "coal_ore", "count": 2}]}
    if (inventory.get("coal", 0) > 0 and inventory.get("stick", 0) > 0
            and inventory.get("torch", 0) < 4):
        return {"seq": [{"type": "craft", "item": "torch", "count": 1}]}

    # 6) 搭棚到 0.85；工作台/熔炉本身也算一格遮挡。
    if shelter < rules.SHELTER_FULL:
        for block in ("cobblestone", "oak_planks", "oak_log", "chest"):
            if inventory.get(block):
                return {"seq": [{"type": "place", "block": block}]}
        if inventory.get("oak_log"):
            return {"seq": [{"type": "craft", "item": "oak_planks", "count": 1}]}
        if _near(ctx, "cobblestone") > 0:
            return {"seq": [{"type": "goto", "target": "stone"},
                            {"type": "mine", "block": "cobblestone", "count": 4}]}

    # 7) 有余力再升石头/铁。
    if inventory.get("stick", 0) < 2 and inventory.get("oak_planks", 0) >= 2:
        return {"seq": [{"type": "craft", "item": "stick", "count": 1}]}
    if inventory.get("oak_planks", 0) < 2 and inventory.get("oak_log"):
        return {"seq": [{"type": "craft", "item": "oak_planks", "count": 1}]}
    if (pickaxe and inventory.get("cobblestone", 0) < 3
            and _near(ctx, "cobblestone") > 0):
        return {"seq": [{"type": "goto", "target": "stone"},
                        {"type": "mine", "block": "cobblestone", "count": 3}]}
    if not _has(ctx, "stone_pickaxe") and inventory.get("cobblestone", 0) >= 3 \
            and inventory.get("stick", 0) >= 2:
        return {"seq": [{"type": "craft", "item": "stone_pickaxe", "count": 1},
                        {"type": "equip", "item": "stone_pickaxe"}]}
    if (inventory.get("raw_iron", 0) < 3 and _has(ctx, "stone_pickaxe")
            and _near(ctx, "iron_ore") > 0):
        return {"seq": [{"type": "goto", "target": "iron"},
                        {"type": "mine", "block": "iron_ore", "count": 3}]}
    if (inventory.get("raw_iron", 0) and inventory.get("coal", 0) >= 2
            and str(ctx.get("source")) != "http"):
        return {"seq": [{"type": "smelt", "item": "raw_iron", "count": 2}]}
    if (inventory.get("iron_ingot", 0) >= 3 and inventory.get("stick", 0) >= 2
            and ctx.get("crafting_table")):
        return {"seq": [{"type": "craft", "item": "iron_pickaxe", "count": 1},
                        {"type": "equip", "item": "iron_pickaxe"}]}
    if (inventory.get("iron_ingot", 0) >= 2 and inventory.get("stick", 0) >= 1
            and ctx.get("crafting_table")):
        return {"seq": [{"type": "craft", "item": "iron_sword", "count": 1},
                        {"type": "equip", "item": "iron_sword"}]}

    if _near(ctx, "oak_log") > 0:
        return {"seq": [{"type": "mine", "block": "oak_log", "count": 2}]}
    return {"seq": [{"type": "wait", "seconds": 5.0}]}


def _planner_for(cfg: LiveConfig):
    """LLM 模式用；每次启动按当前配置新建，避免拿到旧 Key。"""
    from dafeiyu.agent.planner import LLMPlanner
    from dafeiyu.persona.layers import prompt_prefix

    from .prompts import DECISION_PROMPT

    prefix = prompt_prefix(cfg, game_id=GAME_ID, live=True)
    return LLMPlanner(
        cfg, timeout=45.0, model=str(cfg.autoplay_llm_model or cfg.llm_model),
        system_prompt=(prefix + "\n\n" + DECISION_PROMPT).strip(), thinking=False,
    )


def _ask_llm(tools: MinecraftTools, planner, ctx: dict, step: int) -> dict | None:
    """问模型要 1–5 步；不可执行时返回 None，由调用方降级。"""
    if planner is None:
        return None
    goal = ("我的世界生存：按当前背包和附近资源给 1–5 步开荒动作；"
            "天黑前把 shelter 搭到 0.85，材料不够就先挖。只输出约定 JSON。")
    try:
        result = planner.plan(ctx, goal=goal)
    except Exception as exc:  # noqa: BLE001
        _log("llm_fallback", step=step, reason=f"{type(exc).__name__}: {exc}"[:160])
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        _log("llm_fallback", step=step,
             reason=str((result or {}).get("error") or "模型没有返回计划")[:160])
        return None
    _add_usage(result.get("usage"), f"MC 生存 {step}", step)
    checked = tools.validate(result.get("plan") or {})
    if not checked.ok:
        _log("llm_fallback", step=step, reason=checked.message[:160])
        return None
    seq = list(checked.data.get("seq") or [])
    return {"seq": seq} if seq else None


def _wait_for_bridge(tools: MinecraftTools, seconds: float) -> bool:
    deadline = time.time() + max(0.0, float(seconds))
    while time.time() < deadline:
        if _host.stopping():
            return False
        if tools.available():
            return True
        _host.sleep(0.5)
    return tools.available()


def _maybe_launch_real(host: AutoplayHost, tools: MinecraftTools,
                       difficulty: str, mode: str) -> MinecraftTools | None:
    """``auto`` 掉进仿真时先试着拉起真机；成功返回 http 工具，失败返回 None。

    以前这里直接拿仿真开跑，桌宠显示「在玩」，但 Minecraft 根本没启动。
    只有真机桥没连上、且用户没有显式设置 ``MINECRAFT_BRIDGE=sim`` 时才尝试；
    拉起后等 ``LAUNCH_WAIT_S``，用户需要在启动器里选 Fabric 配置并进世界。
    """
    if type(tools) is not MinecraftTools or not hasattr(tools, "source"):
        return tools  # 测试里的 FakeTools
    from .compat.mc import bridge_mode

    if bridge_mode() != "auto":
        return tools
    try:
        source = str(tools.snapshot().get("source") or "")
    except Exception:  # noqa: BLE001
        source = ""
    if source != "sim":
        return tools

    host.set_status(screen="STARTING", last="没连上游戏，正在启动 Minecraft…",
                    error="")
    _log("minecraft_launch", game=GAME_ID, reason="auto_sim_fallback")
    launched: dict = {}
    try:
        from dafeiyu.games.launch import launch_game

        launched = launch_game(GAME_ID)
        _log("minecraft_launch_result", ok=bool(launched.get("ok")),
             message=str(launched.get("message") or launched.get("error") or "")[:160])
    except Exception as exc:  # noqa: BLE001
        _log("minecraft_launch_failed", error=f"{type(exc).__name__}: {exc}"[:160])

    real = MinecraftTools(mode="http", stop_check=host.stopping,
                          difficulty=difficulty)
    # 游戏可能已经在加载世界，先给 5 秒；没有再拉起启动器。
    ready = _wait_for_bridge(real, min(5.0, LAUNCH_WAIT_S))
    if not ready and launched.get("ok"):
        ready = _wait_for_bridge(real, LAUNCH_WAIT_S)
    if ready:
        host.set_status(error="", last="已连上游戏")
        _log("minecraft_bridge_ready", source="http")
        return real

    _log("minecraft_no_bridge", mode=mode, launched=bool(launched.get("ok")))
    if mode == "llm":
        host.set_status(
            running=False, stopping=False, screen="",
            error=("没连上真机：请在启动器选 fabric-loader-26.2 并进世界；"
                   "要跑离线仿真请设 MINECRAFT_BRIDGE=sim"),
            last="没连上真机")
    else:
        host.set_status(
            error="没连上真机，先用仿真世界（要玩本机请在启动器里进世界）",
            last="没连上真机，先用仿真")
    return None


def _loop(host: AutoplayHost, mode: str, planner, pace: str,
          difficulty: str) -> None:
    global _thread
    tools = MinecraftTools(stop_check=host.stopping, difficulty=difficulty)
    if mode == "llm" and planner is None:
        planner = _planner_for(LiveConfig.load())
        ok, why = planner.available()
        if not ok:
            host.set_status(running=False, stopping=False, error=why,
                            last="LLM 不可用")
            with _lock:
                _thread = None
            return
    # 只有 LLM 模式要求真游戏；local 仍保留「没桥就用仿真」的离线验收路径。
    real = _maybe_launch_real(host, tools, difficulty, mode) if mode == "llm" else tools
    if real is None and mode == "llm":
        return
    if real is not None:
        tools = real
    delay = 0.08 if pace == "normal" else 0.3
    step = 0
    failures = 0
    last_llm_at = 0.0
    recording = False
    rec_attempted = False
    try:
        from dafeiyu.recording import policy

        rec_split = str(policy.policy_for(GAME_ID).get("split") or "run")
    except Exception:  # noqa: BLE001
        rec_split = "run"

    host.set_status(screen="STARTING", last="连接 MC 桥…")
    if not tools.available():
        _log("minecraft_launch", game=GAME_ID)
        try:
            from dafeiyu.games.launch import launch_game

            launched = launch_game(GAME_ID)
            _log("minecraft_launch_result", ok=bool(launched.get("ok")),
                 message=str(launched.get("message") or
                             launched.get("error") or "")[:160])
        except Exception as exc:  # noqa: BLE001
            _log("minecraft_launch_failed", error=f"{type(exc).__name__}: {exc}"[:160])
        if not _wait_for_bridge(tools, LAUNCH_WAIT_S):
            host.set_status(
                running=False, stopping=False, screen="",
                error="等不到 MC 桥（装游戏侧桥，或把 MINECRAFT_BRIDGE 设成 sim）",
                last="等不到 MC 桥")
            _log("minecraft_no_bridge")
            return

    try:
        tools.start_run(difficulty)
    except Exception as exc:  # noqa: BLE001
        _log("minecraft_start_run_failed", error=f"{type(exc).__name__}: {exc}"[:160])

    try:
        while not host.stopping():
            step += 1
            ctx = tools.snapshot()
            source = str(ctx.get("source") or "offline")
            if source == "offline":
                failures += 1
                host.set_status(last="MC 桥断开", error="MC 桥断开")
                if failures >= MAX_FAILURES:
                    host.set_status(
                        running=False, stopping=False, screen="",
                        error="MC 桥断开（游戏退出或桥插件停了）", last="桥断开")
                    _log("minecraft_bridge_lost", step=step)
                    return
                host.sleep(1.0)
                continue

            screen = str(ctx.get("screen") or "")
            if screen == "DEAD":
                if recording and rec_split == "run":
                    host.stop_recording("run_end")
                    recording = False
                    rec_attempted = False
                host.set_status(
                    running=True, game=GAME_ID, mode=mode, pace=pace,
                    screen=screen, step=step, last="倒下了，准备重生", error="",
                    floor=int(ctx.get("day") or 0),
                    hp=0, food=int(ctx.get("food") or 0),
                    source=source,
                )
                host.sleep(1.2)
                tools.start_run(difficulty)
                continue
            if screen != "PLAYING":
                host.set_status(
                    running=True, game=GAME_ID, mode=mode, pace=pace,
                    screen=screen, step=step, last="等待游戏进入世界…",
                    error="", source=source,
                )
                host.sleep(0.5)
                continue

            if not recording and not rec_attempted:
                rec_attempted = True
                res = host.start_recording(note="自动玩 · 我的世界") or {}
                recording = bool(res.get("ok") and res.get("recording", True))

            plan = None
            now = time.time()
            if mode == "llm" and now - last_llm_at >= LLM_INTERVAL_S:
                last_llm_at = now
                plan = _ask_llm(tools, planner, ctx, step)
                if plan is None and failures:
                    host.set_status(last="模型没给可执行计划，先本地顶一下")
            if plan is None:
                plan = _local_plan(ctx, step)

            result = tools.execute(plan, dry_run=False)
            if not result.ok:
                failures += 1
                host.set_status(last=str(result.message)[:120],
                                error=str(result.message)[:200])
                _log("minecraft_action_error", step=step, code=result.code,
                     error=str(result.message)[:160])
                if failures >= MAX_FAILURES:
                    host.set_status(running=False, stopping=False,
                                    error=str(result.message)[:200],
                                    last="连续失败，停手")
                    return
                host.sleep(0.6)
                continue

            failures = 0
            after = dict(result.data.get("context") or ctx)
            host.set_status(
                running=True, game=GAME_ID, mode=mode, pace=pace,
                screen=after.get("screen") or "",
                floor=int(after.get("day") or 0),
                hp=int(after.get("hp") or 0),
                max_hp=int(after.get("max_hp") or 0),
                food=int(after.get("food") or 0),
                shelter=float(after.get("shelter") or 0.0),
                phase=str(after.get("phase") or ""),
                clock=str(after.get("clock") or ""),
                source=str(after.get("source") or source),
                step=step,
                last=str(result.message)[:160],
                error="",
            )
            _log("minecraft_step", step=step, mode=mode,
                 day=after.get("day"), phase=after.get("phase"),
                 hp=after.get("hp"), food=after.get("food"),
                 shelter=after.get("shelter"), source=after.get("source"),
                 sent=result.data.get("sent"))
            host.sleep(delay)
    except Exception as exc:  # noqa: BLE001
        host.set_status(error=f"{type(exc).__name__}: {exc}", last="异常停止")
        _log("minecraft_fatal", error=f"{type(exc).__name__}: {exc}"[:240])
    finally:
        if recording:
            try:
                host.stop_recording("autoplay_end")
            except Exception:  # noqa: BLE001
                pass
        try:
            host.set_status(
                running=False, stopping=False,
                last="已停手" if host.stopping() else "这一段结束",
            )
        finally:
            with _lock:
                _thread = None


def start(game: str = "", *, mode: str | None = None,
          pace: str | None = None) -> dict:
    global _thread, _planner
    mode = str(mode or "local").strip().lower()
    if mode not in ("local", "llm"):
        return {"ok": False, "game": GAME_ID,
                "message": "我的世界只支持 local / llm 模式"}
    pace = str(pace or _host.pace or "voice").strip().lower()
    if pace not in ("voice", "normal"):
        return {"ok": False, "game": GAME_ID, "message": "节奏只能是 voice 或 normal"}

    planner = None
    if mode == "llm":
        try:
            planner = _planner_for(LiveConfig.load())
            ok, why = planner.available()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "game": GAME_ID,
                    "message": f"LLM 初始化失败：{type(exc).__name__}: {exc}"}
        if not ok:
            return {"ok": False, "game": GAME_ID, "message": why}

    difficulty = _difficulty()
    with _lock:
        if _active():
            return {"ok": True, "already": True, "game": GAME_ID,
                    "message": "她已经在玩了", "status": _status()}
        current = _host.status()
        if (current.get("running")
                and int(current.get("pid") or 0) not in (0, os.getpid())
                and _host.pid_alive(current.get("pid"))):
            return {"ok": True, "already": True, "game": GAME_ID,
                    "message": "她已经在玩了", "status": current}
        _host.clear_stop()
        _host.reset(GAME_ID, mode=mode, pace=pace)
        _host.set_status(
            running=True, game=GAME_ID, mode=mode, pace=pace,
            screen="STARTING", step=0, last="启动中…", error="",
            started_at=time.time(), pid=os.getpid(), stopping=False,
        )
        _log("minecraft_start", mode=mode, pace=pace, difficulty=difficulty)
        _thread = threading.Thread(
            target=_loop, args=(_host, mode, planner, pace, difficulty),
            name="dafeiyu-minecraft", daemon=True)
        _thread.start()
        return {"ok": True, "game": GAME_ID, "mode": mode, "pace": pace,
                "difficulty": difficulty,
                "message": "我的世界已开始", "status": _status()}


def stop() -> dict:
    global _thread
    _host.request_stop()
    try:
        MinecraftTools().stop_control()
    except Exception:  # noqa: BLE001
        pass
    thread = _thread
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(3.0)

    current = _host.status()
    remote = bool(
        current.get("running")
        and int(current.get("pid") or 0) not in (0, os.getpid())
        and _host.pid_alive(current.get("pid"))
    )
    if remote or (thread is not None and thread.is_alive()):
        _host.set_status(stopping=True, last="正在停手…")
        return {"ok": True, "message": "正在停手…", "status": current}

    _thread = None
    _host.clear_stop()
    _host.set_status(running=False, stopping=False, last="已停手")
    return {"ok": True, "message": "已停手", "status": _host.status()}


def status() -> dict:
    return _host.status()


__all__ = ["GAME_ID", "bind_host", "start", "status", "stop"]
