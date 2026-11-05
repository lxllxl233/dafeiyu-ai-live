"""Minecraft 生存开荒的观察/验算/动作层（``mc-bridge/v1``）。

所有游戏操作都只走本地桥：``GET /state`` 读世界，``POST /act`` 发一个动作。
Python 端**不找窗口、不切前台、不发键盘**，游戏可以在后台跑。

桥的三种来源（``MINECRAFT_BRIDGE``）：

- ``http``：只认真机桥，连不上就是离线，绝不退化成键盘；
- ``sim``：内置仿真世界（``bridge/sim.py``），离线验收用；
- ``auto``（默认）：先试 HTTP，连不上才用仿真；状态里 ``source`` 会写清来源。

动作协议（``minecraft.action/v1``）::

    {"seq": [
        {"type": "mine", "block": "oak_log", "count": 3},
        {"type": "craft", "item": "oak_planks", "count": 2},
        {"type": "place", "block": "crafting_table"},
        {"type": "craft", "item": "wooden_pickaxe"}
    ]}

验算直接复用 :mod:`rules` 的 ``apply_step``（在一份状态副本上跑），
所以「验算通过」和「真跑能过」是同一套规则，不会出现两套阈值。
"""

from __future__ import annotations

import copy
from typing import Any

from dafeiyu.games.contracts import ActionCode, ActionResult, GameStateEnvelope

from . import rules
from .compat.mc import Bridge, McError, McUnavailable

GAME_ID = rules.GAME_ID
MAX_STEPS = rules.MAX_STEPS
MAX_TOTAL_WAIT = 20.0
SCREEN_MAP = {
    "playing": "PLAYING",
    "paused": "PAUSED",
    "dead": "DEAD",
    "menu": "MENU",
    "": "",
}
PHASE_LABEL = {"day": "白天", "dusk": "黄昏", "night": "夜里"}
MAX_INVENTORY_ROWS = 24
MAX_BLOCK_ROWS = 12
MAX_MOB_ROWS = 8
NEARBY_RADIUS = 24.0


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dist(player: dict, item: dict) -> float:
    dx = _num(item.get("x")) - _num(player.get("x"))
    dz = _num(item.get("z")) - _num(player.get("z"))
    return round((dx * dx + dz * dz) ** 0.5, 1)


def context(raw: dict[str, Any] | None, *, source: str = "offline",
            error: str = "") -> dict[str, Any]:
    """把桥/仿真状态压成给模型和适配器看的一份上下文。"""
    raw = raw if isinstance(raw, dict) else {}
    world = raw.get("world") if isinstance(raw.get("world"), dict) else {}
    player = raw.get("player") if isinstance(raw.get("player"), dict) else {}
    flags = raw.get("flags") if isinstance(raw.get("flags"), dict) else {}
    stats = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
    screen = SCREEN_MAP.get(str(raw.get("screen") or "").lower(),
                            str(raw.get("screen") or "").upper())

    inventory = {
        str(name): _int(number)
        for name, number in (player.get("inventory") or {}).items()
        if _int(number) > 0
    }
    held = str(player.get("held") or "")

    blocks = []
    for block in raw.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        away = _dist(player, block)
        if away > NEARBY_RADIUS:
            continue
        blocks.append({
            "name": str(block.get("name") or ""),
            "kind": str(block.get("kind") or ""),
            "dist": away,
            "x": _int(block.get("x")),
            "y": _int(block.get("y")),
            "z": _int(block.get("z")),
        })
    blocks.sort(key=lambda row: row["dist"])

    mobs = []
    for mob in raw.get("mobs") or []:
        if not isinstance(mob, dict):
            continue
        mobs.append({
            "name": str(mob.get("name") or ""),
            "hostile": bool(mob.get("hostile")),
            "hp": _int(mob.get("hp")),
            "dist": _dist(player, mob),
        })
    mobs.sort(key=lambda row: row["dist"])

    time_s = _num(world.get("time"))
    phase = str(world.get("phase") or rules.phase_of(time_s))
    return {
        "game": GAME_ID,
        "source": source,
        "screen": screen,
        "day": _int(world.get("day"), rules.day_of(time_s)) or 1,
        "phase": phase,
        "phase_label": PHASE_LABEL.get(phase, phase),
        "clock": rules.clock_of(time_s),
        "weather": str(world.get("weather") or "clear"),
        "dimension": str(world.get("dimension") or "overworld"),
        "difficulty": str(world.get("difficulty") or "normal"),
        "world": str(world.get("name") or ""),
        "player_name": str(player.get("name") or "")[:20],
        "hp": _int(player.get("hp")),
        "max_hp": _int(player.get("max_hp"), 20),
        "food": _int(player.get("food")),
        "xp_level": _int(player.get("level")),
        "armor": _int(player.get("armor")),
        "held": held,
        "inventory": dict(sorted(inventory.items(),
                                 key=lambda kv: (-kv[1], kv[0]))[:MAX_INVENTORY_ROWS]),
        "nearby": {
            "blocks": blocks[:MAX_BLOCK_ROWS],
            "mobs": mobs[:MAX_MOB_ROWS],
        },
        "shelter": round(_num(player.get("shelter")), 2),
        "crafting_table": bool(flags.get("crafting_table")),
        "furnace": bool(flags.get("furnace")),
        "stats": {
            "mined": _int(stats.get("mined")),
            "placed": _int(stats.get("placed")),
            "crafted": _int(stats.get("crafted")),
            "kills": _int(stats.get("kills")),
            "deaths": _int(stats.get("deaths")),
            "actions": _int(stats.get("actions")),
        },
        "position": {
            "x": round(_num(player.get("x")), 1),
            "y": round(_num(player.get("y")), 1),
            "z": round(_num(player.get("z")), 1),
        },
        "last": str(raw.get("last") or "")[:160],
        "bridge": {
            "source": source,
            "api": _int(raw.get("api"), rules.BRIDGE_VERSION),
            "url": str(raw.get("url") or ""),
            "error": str(error or raw.get("fallback_reason") or "")[:160],
        },
    }


def envelope(ctx: dict[str, Any]) -> GameStateEnvelope:
    return GameStateEnvelope(
        game_id=GAME_ID,
        revision=f"{ctx.get('day') or 0}-{ctx.get('clock') or ''}-"
                 f"{ctx.get('stats', {}).get('actions') or 0}",
        summary={
            "screen": ctx.get("screen") or "",
            "floor": _int(ctx.get("day")),
            "hp": _int(ctx.get("hp")),
            "max_hp": _int(ctx.get("max_hp"), 20),
            "character": str(ctx.get("world") or ""),
            "in_combat": any(mob.get("hostile")
                             for mob in (ctx.get("nearby") or {}).get("mobs") or []),
            "turn": _int(ctx.get("day")),
        },
        data=ctx,
    )


def resolve_plan(plan: Any) -> tuple[list[dict], str]:
    """只做结构校验，返回 ``(规范步骤, 错误)``；不读游戏、不发动作。"""
    if not isinstance(plan, dict):
        return [], "plan 必须是对象"
    raw_seq = plan.get("seq")
    if raw_seq is None:
        if any(key in plan for key in ("type", "action", "block", "item", "target")):
            raw_seq = [plan]
        else:
            return [], "plan 缺少 seq"
    if not isinstance(raw_seq, list) or not raw_seq:
        return [], "seq 必须是非空数组"
    if len(raw_seq) > MAX_STEPS:
        return [], f"seq 最多 {MAX_STEPS} 步"

    steps: list[dict] = []
    total_wait = 0.0
    for index, item in enumerate(raw_seq, 1):
        try:
            step = rules.normalize_step(item)
        except rules.RuleError as exc:
            return [], f"seq[{index}] {exc}"
        if step["type"] == "wait":
            total_wait += float(step["seconds"])
        steps.append(step)
    if total_wait > MAX_TOTAL_WAIT:
        return [], f"等待时间超过 {MAX_TOTAL_WAIT:.0f} 秒"
    return steps, ""


def check_steps(raw: dict[str, Any], steps: list[dict]) -> str:
    """在状态副本上逐步应用；返回第一条错误，全部合法返回空串。"""
    work = copy.deepcopy(raw)
    for index, step in enumerate(steps, 1):
        try:
            rules.apply_step(work, step)
        except rules.RuleError as exc:
            return f"seq[{index}] {exc}"
    return ""


def _step_label(step: dict) -> str:
    value = ""
    for key in ("block", "item", "target"):
        if step.get(key):
            value = str(step[key])
            break
    return f"{step.get('type')}:{value}" if value else str(step.get("type"))


class MinecraftTools:
    """实现 ``GameTools``；观察和动作都只走本地桥。"""

    def __init__(self, game_id: str = GAME_ID, *, bridge: Bridge | None = None,
                 mode: str | None = None, sim_path: Any = None,
                 difficulty: str = "normal", stop_check=None) -> None:
        self.game_id = game_id
        self._bridge = bridge or Bridge(mode=mode, sim_path=sim_path,
                                        difficulty=difficulty)
        self.stop_check = stop_check

    # ---------------------------------------------------------------- 观察
    def _read(self) -> tuple[dict[str, Any], str, str]:
        try:
            raw = self._bridge.state()
        except (McUnavailable, McError) as exc:
            return {}, "offline", f"{type(exc).__name__}: {exc}"[:200]
        except Exception as exc:  # noqa: BLE001
            return {}, "offline", f"{type(exc).__name__}: {exc}"[:200]
        if not isinstance(raw, dict):
            return {}, "offline", "桥返回的不是对象"
        source = str(raw.get("source") or self._bridge.source or "http")
        return raw, source, str(raw.get("fallback_reason") or "")

    def snapshot(self) -> dict[str, Any]:
        raw, source, error = self._read()
        return context(raw, source=source, error=error)

    @property
    def source(self) -> str:
        return self._bridge.source

    def available(self) -> bool:
        try:
            return bool(self._bridge.available())
        except Exception:
            return False

    def observe(self) -> ActionResult:
        raw, source, error = self._read()
        ctx = context(raw, source=source, error=error)
        if source == "offline":
            return ActionResult.failure(
                "observe", code=ActionCode.UNAVAILABLE,
                message="MC 桥未连接（装游戏侧桥，或把 MINECRAFT_BRIDGE 设成 sim）",
                data={"context": ctx})
        return ActionResult.success(
            "observe",
            message=(f"第 {ctx['day']} 天 {ctx['clock']} · "
                     f"{ctx['phase_label']} · HP {ctx['hp']}/{ctx['max_hp']} · "
                     f"饱食 {ctx['food']}"),
            state=envelope(ctx),
            data={"context": ctx},
        )

    # ---------------------------------------------------------------- 验算
    def validate(self, plan: dict) -> ActionResult:
        steps, error = resolve_plan(plan)
        raw, source, bridge_error = self._read()
        ctx = context(raw, source=source, error=bridge_error)
        if error:
            return ActionResult.failure(
                "validate", code=ActionCode.INVALID_PLAN, message=error,
                data={"context": ctx})
        if source == "offline":
            return ActionResult.failure(
                "validate", code=ActionCode.UNAVAILABLE,
                message="MC 桥未连接，无法按背包/附近方块验算",
                data={"context": ctx})
        semantic = check_steps(raw, steps)
        if semantic:
            return ActionResult.failure(
                "validate", code=ActionCode.INVALID_PLAN, message=semantic,
                data={"context": ctx})
        return ActionResult.success(
            "validate", message="计划合法",
            data={"seq": steps, "context": ctx})

    # ---------------------------------------------------------------- 执行
    def execute(self, plan: dict, *, dry_run: bool = True) -> ActionResult:
        steps, error = resolve_plan(plan)
        raw, source, bridge_error = self._read()
        ctx = context(raw, source=source, error=bridge_error)
        if error:
            return ActionResult.failure(
                "execute", code=ActionCode.INVALID_PLAN, message=error,
                data={"dry_run": bool(dry_run), "context": ctx})
        if source == "offline":
            return ActionResult.failure(
                "execute", code=ActionCode.UNAVAILABLE,
                message="MC 桥未连接（先装桥，或把 MINECRAFT_BRIDGE 设成 sim）",
                data={"dry_run": bool(dry_run), "context": ctx})
        semantic = check_steps(raw, steps)
        if semantic:
            return ActionResult.failure(
                "execute", code=ActionCode.INVALID_PLAN, message=semantic,
                data={"dry_run": bool(dry_run), "context": ctx})
        if dry_run:
            return ActionResult.success(
                "execute", message="预演通过（未发送动作）",
                data={"dry_run": True, "seq": steps, "sent": [],
                      "context": ctx})

        sent: list[str] = []
        try:
            for step in steps:
                if self.stop_check is not None and self.stop_check():
                    return ActionResult.failure(
                        "execute", code=ActionCode.UNAVAILABLE, message="已停手",
                        data={"dry_run": False, "seq": steps, "sent": sent,
                              "context": self.snapshot()})
                self._bridge.act(step)
                sent.append(_step_label(step))
        except McUnavailable as exc:
            return ActionResult.failure(
                "execute", code=ActionCode.UNAVAILABLE,
                message=f"MC 桥断开：{exc}"[:200],
                data={"dry_run": False, "seq": steps, "sent": sent,
                      "context": self.snapshot()})
        except McError as exc:
            return ActionResult.failure(
                "execute", code=ActionCode.ILLEGAL_ACTION,
                message=f"世界拒绝了动作：{exc}"[:200],
                data={"dry_run": False, "seq": steps, "sent": sent,
                      "context": self.snapshot()})

        after = self.snapshot()
        return ActionResult.success(
            "execute",
            message=f"执行 {len(sent)} 步：{after.get('last') or '完成'}",
            state=envelope(after),
            data={"dry_run": False, "seq": steps, "sent": sent,
                  "receipt": f"mc-{after.get('day') or 0}-"
                             f"{after.get('stats', {}).get('actions') or 0}",
                  "context": after},
        )

    # ---------------------------------------------------------------- 控制
    def start_run(self, difficulty: str | None = None) -> dict[str, Any]:
        try:
            return self._bridge.start(str(difficulty or "normal"))
        except (McUnavailable, McError) as exc:
            return {"ok": False, "error": str(exc)[:200]}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}

    def stop_control(self) -> dict[str, Any]:
        try:
            return self._bridge.stop()
        except Exception:  # noqa: BLE001
            return {"ok": False}

    def reset_world(self, difficulty: str | None = None) -> dict[str, Any]:
        try:
            return self._bridge.reset(str(difficulty or "normal"))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)[:200]}

    def close(self) -> None:
        self._bridge.close()


__all__ = [
    "GAME_ID", "MAX_STEPS", "MinecraftTools", "SCREEN_MAP", "check_steps",
    "context", "envelope", "resolve_plan",
]
