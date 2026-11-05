"""Minecraft 生存开荒的规则参考实现（纯函数，不做 I/O）。

这份规则被两处共用，保证「验算」和「真跑」永远是同一套：

- ``bridge/sim.py``：内置仿真世界，没有真机时用它跑通 observe/validate/execute；
- ``engine.py``：验算 LLM / 本地计划时，在一份状态副本上逐步 :func:`apply_step`。

真机桥（Fabric 模组）只要实现同样的 ``mc-bridge/v1`` 动作语义，Python 端不换代码。
这里是「参考实现」，不是游戏本体：不做寻路、不碰渲染、不读写存档。

状态形状（简化）::

    {"screen": "playing|paused|dead|menu",
     "world": {"time": 30.0, "day": 1, "phase": "day|dusk|night",
               "weather": "clear", "difficulty": "normal"},
     "player": {"x": 0.0, "y": 64.0, "z": 0.0, "hp": 20, "max_hp": 20,
                "food": 20.0, "level": 0, "xp": 0, "armor": 0, "held": "",
                "inventory": {"bread": 2}, "shelter": 0.0, "dead": False},
     "blocks": [{"id": "b1", "name": "oak_log", "kind": "tree",
                 "x": 3, "y": 64, "z": 2}],
     "mobs": [{"id": "m1", "name": "cow", "hostile": false, "hp": 10,
               "max_hp": 10, "attack": 0, "x": 5.0, "y": 64.0, "z": -4.0}],
     "flags": {"crafting_table": false, "furnace": false},
     "clock": {"hunger": 0.0, "mobs": 0.0, "starve": 0.0},
     "stats": {"mined": 0, "placed": 0, "crafted": 0, "kills": 0,
               "deaths": 0, "actions": 0},
     "last": "新世界…"}
"""

from __future__ import annotations

import math
from typing import Any

GAME_ID = "minecraft"
BRIDGE_VERSION = 1

# 一局「游戏内时间」：1200 秒 = 一天（20 分钟，和原版一致），
# 白天 600 秒、黄昏 100 秒、夜里 500 秒；1 秒 ≈ 1.2 游戏分钟。
DAY_LENGTH = 1200.0
DAY_END = 600.0
DUSK_END = 700.0
HUNGER_SECONDS = 240.0
STARVE_SECONDS = 20.0
MOB_SECONDS = 9.0
SHELTER_FULL = 0.85
SHELTER_PER_BLOCK = 0.15

MAX_STEPS = 8
MAX_COUNT = 16
MAX_WAIT = 10.0

# 动作名到「中文动作」的映射，只用于校验和报错，不是游戏规则。
ACTIONS = ("goto", "mine", "craft", "smelt", "place", "attack", "eat", "equip",
           "wait")
ACTION_SECONDS = {
    "goto": 6.0,
    "mine": 3.0,
    "craft": 2.0,
    "smelt": 8.0,
    "place": 2.0,
    "attack": 2.0,
    "eat": 1.5,
    "equip": 0.5,
    "wait": 0.0,
}

# 合成表：``in`` 里的数量是「一份产物」的消耗，``table`` 表示必须放下工作台。
RECIPES: dict[str, dict[str, Any]] = {
    "oak_planks": {"out": 4, "in": {"oak_log": 1}, "table": False},
    "stick": {"out": 4, "in": {"oak_planks": 2}, "table": False},
    "crafting_table": {"out": 1, "in": {"oak_planks": 4}, "table": False},
    "chest": {"out": 1, "in": {"oak_planks": 8}, "table": True},
    "wooden_pickaxe": {"out": 1, "in": {"oak_planks": 3, "stick": 2}, "table": True},
    "wooden_axe": {"out": 1, "in": {"oak_planks": 3, "stick": 2}, "table": True},
    "wooden_sword": {"out": 1, "in": {"oak_planks": 2, "stick": 1}, "table": True},
    "stone_pickaxe": {"out": 1, "in": {"cobblestone": 3, "stick": 2}, "table": True},
    "stone_axe": {"out": 1, "in": {"cobblestone": 3, "stick": 2}, "table": True},
    "stone_sword": {"out": 1, "in": {"cobblestone": 2, "stick": 1}, "table": True},
    "furnace": {"out": 1, "in": {"cobblestone": 8}, "table": True},
    "torch": {"out": 4, "in": {"coal": 1, "stick": 1}, "table": False},
    "iron_pickaxe": {"out": 1, "in": {"iron_ingot": 3, "stick": 2}, "table": True},
    "iron_sword": {"out": 1, "in": {"iron_ingot": 2, "stick": 1}, "table": True},
    "iron_axe": {"out": 1, "in": {"iron_ingot": 3, "stick": 2}, "table": True},
}

PICKAXE_TIER = {"wooden_pickaxe": 1, "stone_pickaxe": 2, "iron_pickaxe": 3}
PICK_REQUIRED = {"cobblestone": 1, "coal_ore": 1, "iron_ore": 2}
BLOCK_DROPS = {
    "oak_log": {"oak_log": 1},
    "cobblestone": {"cobblestone": 1},
    "coal_ore": {"coal": 1},
    "iron_ore": {"raw_iron": 1},
}
BLOCK_KIND = {
    "oak_log": "tree",
    "cobblestone": "stone",
    "coal_ore": "coal",
    "iron_ore": "iron",
}
KIND_BLOCK = {kind: name for name, kind in BLOCK_KIND.items()}

PLACEABLE = {"crafting_table", "furnace", "chest", "torch", "oak_planks",
             "cobblestone", "oak_log"}
SOLID_PLACE = {"crafting_table", "furnace", "chest", "oak_planks",
               "cobblestone", "oak_log"}

WEAPON_DAMAGE = {
    "wooden_sword": 4, "stone_sword": 5, "iron_sword": 6,
    "wooden_axe": 3, "stone_axe": 4, "iron_axe": 5,
    "wooden_pickaxe": 2, "stone_pickaxe": 3, "iron_pickaxe": 3,
}
WEAPONS = tuple(WEAPON_DAMAGE)

FOOD = {"bread": 5.0, "cooked_beef": 8.0, "beef": 2.0}
SMELT = {"raw_iron": ("iron_ingot", 1), "beef": ("cooked_beef", 1)}
SMELT_FUEL = {"coal": 1}

NAMES = {
    "oak_log": "橡木原木", "oak_planks": "橡木木板", "stick": "木棍",
    "cobblestone": "圆石", "coal": "煤炭", "coal_ore": "煤矿石",
    "iron_ore": "铁矿石", "raw_iron": "粗铁", "iron_ingot": "铁锭",
    "crafting_table": "工作台", "furnace": "熔炉", "chest": "箱子",
    "torch": "火把", "wooden_pickaxe": "木镐", "stone_pickaxe": "石镐",
    "iron_pickaxe": "铁镐", "wooden_axe": "木斧", "stone_axe": "石斧",
    "iron_axe": "铁斧", "wooden_sword": "木剑", "stone_sword": "石剑",
    "iron_sword": "铁剑", "bread": "面包", "beef": "生牛肉",
    "cooked_beef": "熟牛肉", "cow": "牛", "zombie": "僵尸",
    "tree": "树", "stone": "石头", "iron": "铁矿", "home": "家",
}

DIFFICULTIES = ("peaceful", "normal", "hard")
DIFFICULTY_DAMAGE = {"peaceful": 0, "normal": 2, "hard": 3}


class RuleError(ValueError):
    """计划不合法；message 直接给模型/日志看，要能指导下一步。"""


def cn(name: str) -> str:
    """物品/生物的中文名；不认识就原样返回。"""
    return NAMES.get(str(name or ""), str(name or ""))


def phase_of(time_s: float) -> str:
    t = float(time_s) % DAY_LENGTH
    if t < DAY_END:
        return "day"
    if t < DUSK_END:
        return "dusk"
    return "night"


def day_of(time_s: float) -> int:
    return 1 + int(max(0.0, float(time_s)) // DAY_LENGTH)


def clock_of(time_s: float) -> str:
    """把游戏内时间写成 ``HH:MM``；0 秒 = 早上 06:00，一天 1200 秒。"""
    minutes = int(6 * 60 + (float(time_s) % DAY_LENGTH) * 1.2) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# ---------------------------------------------------------------- 背包/状态
def inventory(state: dict) -> dict[str, int]:
    inv = state.setdefault("player", {}).setdefault("inventory", {})
    return {str(k): int(v) for k, v in inv.items() if int(v) > 0}


def count(state: dict, item: str) -> int:
    return int(inventory(state).get(item, 0))


def take(state: dict, item: str, number: int = 1) -> bool:
    number = int(number)
    if count(state, item) < number:
        return False
    inv = state["player"]["inventory"]
    left = int(inv.get(item, 0)) - number
    if left > 0:
        inv[item] = left
    else:
        inv.pop(item, None)
    return True


def give(state: dict, item: str, number: int = 1) -> None:
    inv = state["player"].setdefault("inventory", {})
    inv[item] = int(inv.get(item, 0)) + int(number)


def pickaxe_tier(state: dict) -> int:
    return int(PICKAXE_TIER.get(str(state["player"].get("held") or ""), 0))


def weapon_damage(state: dict) -> int:
    return int(WEAPON_DAMAGE.get(str(state["player"].get("held") or ""), 1))


def distance(state: dict, x: float, z: float) -> float:
    player = state.get("player") or {}
    return round(math.hypot(float(x) - float(player.get("x") or 0),
                            float(z) - float(player.get("z") or 0)), 1)


def nearest_block(state: dict, name: str) -> dict | None:
    blocks = [b for b in state.get("blocks") or [] if b.get("name") == name]
    return min(blocks, key=lambda b: distance(state, b["x"], b["z"])) if blocks else None


def nearest_mob(state: dict, name: str = "", *, hostile_only: bool = False) -> dict | None:
    mobs = list(state.get("mobs") or [])
    if name:
        mobs = [m for m in mobs if str(m.get("name") or "") == name]
    if hostile_only:
        mobs = [m for m in mobs if m.get("hostile")]
    return min(mobs, key=lambda m: distance(state, m["x"], m["z"])) if mobs else None


# ---------------------------------------------------------------- 生成世界
def _block(index: int, name: str, x: int, y: int, z: int) -> dict:
    return {"id": f"b{index}", "name": name, "kind": BLOCK_KIND[name],
            "x": x, "y": y, "z": z}


def _spawn_blocks() -> list[dict]:
    """固定出生点：三棵橡树、一片石头、一点煤和铁，够走完第一天。"""
    marks = [
        ("oak_log", 3, 64, 2), ("oak_log", 3, 65, 2), ("oak_log", 3, 66, 2),
        ("oak_log", -5, 64, -2), ("oak_log", -5, 65, -2),
        ("oak_log", 4, 64, -6),
        ("cobblestone", 8, 63, 4), ("cobblestone", 9, 63, 4),
        ("cobblestone", 8, 63, 5), ("cobblestone", 9, 63, 5),
        ("cobblestone", 8, 62, 4), ("cobblestone", 9, 62, 4),
        ("cobblestone", 10, 63, 4), ("cobblestone", 8, 63, 6),
        ("cobblestone", 10, 62, 4), ("cobblestone", 10, 62, 5),
        ("cobblestone", 11, 63, 5), ("cobblestone", 10, 63, 6),
        ("cobblestone", 9, 62, 5), ("cobblestone", 11, 62, 4),
        ("cobblestone", 9, 61, 4), ("cobblestone", 10, 61, 5),
        ("coal_ore", 11, 62, 6), ("coal_ore", 12, 62, 6),
        ("coal_ore", 11, 61, 6), ("coal_ore", 12, 61, 6),
        ("iron_ore", 13, 61, 7), ("iron_ore", 14, 61, 7),
    ]
    return [_block(i + 1, name, x, y, z) for i, (name, x, y, z) in enumerate(marks)]


def _cow(index: int = 1, x: float = 5.0, z: float = -4.0) -> dict:
    return {"id": f"m{index}", "name": "cow", "hostile": False,
            "hp": 10, "max_hp": 10, "attack": 0,
            "x": float(x), "y": 64.0, "z": float(z)}


def _ensure_food(state: dict) -> list[str]:
    """每天补一点牛，保证长局（7 天）不会因为食物耗尽必死。"""
    cows = [mob for mob in state.get("mobs") or [] if mob.get("name") == "cow"]
    if len(cows) >= 2:
        return []
    player = state.setdefault("player", {})
    notes: list[str] = []
    for index in range(2 - len(cows)):
        dx = 6.0 if index == 0 else -6.0
        dz = 3.0 if index == 0 else -3.0
        state.setdefault("mobs", []).append(_cow(
            100 + index, float(player.get("x") or 0.0) + dx,
            float(player.get("z") or 0.0) + dz))
        notes.append("附近又来了牛")
    return notes


def new_world(difficulty: str = "normal", *, time_s: float = 30.0) -> dict:
    """开一个新世界；``difficulty`` 只影响夜里怪物的伤害。"""
    difficulty = str(difficulty or "normal").strip().lower()
    if difficulty not in DIFFICULTIES:
        difficulty = "normal"
    state: dict[str, Any] = {
        "screen": "playing",
        "world": {
            "name": "生存·第 1 天",
            "time": float(time_s),
            "day": day_of(time_s),
            "phase": phase_of(time_s),
            "weather": "clear",
            "dimension": "overworld",
            "difficulty": difficulty,
        },
        "player": {
            "name": "阿肥",
            "x": 0.0, "y": 64.0, "z": 0.0,
            "hp": 20, "max_hp": 20,
            "food": 20.0, "level": 0, "xp": 0, "armor": 0,
            "on_ground": True, "held": "",
            "inventory": {"bread": 2},
            "shelter": 0.0,
            "dead": False,
        },
        "blocks": _spawn_blocks(),
        "mobs": [_cow()],
        "flags": {"crafting_table": False, "furnace": False},
        "clock": {"hunger": 0.0, "mobs": 0.0, "starve": 0.0},
        "stats": {"mined": 0, "placed": 0, "crafted": 0, "kills": 0,
                  "deaths": 0, "actions": 0},
        "last": "新世界：先砍树做工作台，天黑前搭个能躲的地方。",
    }
    return state


def _spawn_night(state: dict) -> list[str]:
    if state["world"].get("difficulty") == "peaceful":
        return []
    player = state["player"]
    spots = [(float(player["x"]) + 6.0, float(player["z"])),
             (float(player["x"]) - 5.0, float(player["z"]) + 5.0)]
    for index, (x, z) in enumerate(spots, 1):
        if any(m.get("name") == "zombie" for m in state.get("mobs") or []):
            continue
        state.setdefault("mobs", []).append({
            "id": f"z{state['world'].get('day', 1)}-{index}",
            "name": "zombie", "hostile": True, "hp": 20, "max_hp": 20,
            "attack": 2, "x": x, "y": 64.0, "z": z,
        })
    return ["天黑了，附近有僵尸"]


def tick(state: dict, seconds: float) -> list[str]:
    """推进游戏时间：昼夜、饥饿、夜里挨打、死亡判定；返回要播报的短句。"""
    seconds = max(0.0, float(seconds))
    notes: list[str] = []
    world = state.setdefault("world", {})
    player = state.setdefault("player", {})
    clock = state.setdefault("clock", {"hunger": 0.0, "mobs": 0.0, "starve": 0.0})

    old_time = float(world.get("time") or 0.0)
    old_phase = phase_of(old_time)
    old_day = day_of(old_time)
    world["time"] = old_time + seconds
    world["day"] = day_of(world["time"])
    phase = phase_of(world["time"])
    world["phase"] = phase
    if int(world["day"]) != old_day:
        notes.extend(_ensure_food(state))
    if phase == "night":
        # 一天的夜里只刷一次；从存档/中途接回来也能补上。
        if int(clock.get("spawn_day") or 0) != int(world["day"]):
            clock["spawn_day"] = int(world["day"])
            notes.extend(_spawn_night(state))
    elif phase == "day" and old_phase == "night":
        state["mobs"] = [m for m in state.get("mobs") or []
                         if not m.get("hostile")]
        notes.append("天亮了")

    if seconds <= 0:
        return notes

    # 饥饿：每 70 秒掉 1 点；饿到 0 后每 15 秒掉 1 血。
    clock["hunger"] = float(clock.get("hunger") or 0.0) + seconds
    while clock["hunger"] >= HUNGER_SECONDS:
        clock["hunger"] -= HUNGER_SECONDS
        player["food"] = max(0.0, float(player.get("food") or 0.0) - 1.0)
    if float(player.get("food") or 0.0) <= 0.0:
        clock["starve"] = float(clock.get("starve") or 0.0) + seconds
        while clock["starve"] >= STARVE_SECONDS:
            clock["starve"] -= STARVE_SECONDS
            player["hp"] = int(player.get("hp") or 0) - 1
            notes.append("饿得掉血")

    # 夜里没遮挡就会挨打；搭到 0.85 就算有个能躲的地方。
    if phase == "night" and float(player.get("shelter") or 0.0) < SHELTER_FULL:
        clock["mobs"] = float(clock.get("mobs") or 0.0) + seconds
        while clock["mobs"] >= MOB_SECONDS:
            clock["mobs"] -= MOB_SECONDS
            zombie = nearest_mob(state, "zombie")
            if zombie is None:
                continue
            damage = DIFFICULTY_DAMAGE.get(
                str(world.get("difficulty") or "normal"), 2)
            if damage <= 0:
                continue
            player["hp"] = int(player.get("hp") or 0) - damage
            notes.append(f"{cn('zombie')}偷袭，-{damage} 血")

    if int(player.get("hp") or 0) <= 0 and state.get("screen") != "dead":
        player["hp"] = 0
        player["dead"] = True
        state["screen"] = "dead"
        stats = state.setdefault("stats", {})
        stats["deaths"] = int(stats.get("deaths") or 0) + 1
        notes.append("角色倒下了")
    return notes


# ---------------------------------------------------------------- 动作
def normalize_step(step: Any) -> dict:
    """把模型/本地策略写的动作变成规范 dict；不合法就抛 :class:`RuleError`。"""
    if not isinstance(step, dict):
        raise RuleError("动作必须是对象")
    raw_kind = str(step.get("type") or step.get("action") or "").strip().lower()
    kind = {
        "walk": "goto", "move": "goto", "go": "goto", "travel": "goto",
        "dig": "mine", "break": "mine", "gather": "mine",
        "make": "craft", "build": "place", "put": "place", "place_block": "place",
        "cook": "smelt", "hit": "attack", "kill": "attack", "fight": "attack",
        "sleep": "sleep",
    }.get(raw_kind, raw_kind)
    if kind == "sleep":
        raise RuleError("这张地图没有床，夜里靠搭方块躲，别用 sleep")
    if kind not in ACTIONS:
        raise RuleError(f"不认识的动作：{raw_kind or '空'}")
    if kind == "goto":
        target = str(step.get("target") or step.get("to") or "").strip().lower()
        if target:
            return {"type": "goto", "target": target}
        for key in ("x", "y", "z"):
            if step.get(key) is not None:
                return {"type": "goto", "x": float(step["x"]), "y": float(step.get("y", 64)),
                        "z": float(step["z"])}
        raise RuleError("goto 需要 target（tree/stone/coal/iron/home）或 x/y/z")
    if kind == "mine":
        block = str(step.get("block") or step.get("target") or "").strip().lower()
        if not block:
            raise RuleError("mine 需要 block（oak_log/cobblestone/coal_ore/iron_ore）")
        return {"type": "mine", "block": block, "count": _count(step)}
    if kind == "craft":
        item = str(step.get("item") or step.get("craft") or "").strip().lower()
        if not item:
            raise RuleError("craft 需要 item")
        return {"type": "craft", "item": item, "count": _count(step)}
    if kind == "smelt":
        item = str(step.get("item") or step.get("smelt") or "").strip().lower()
        if not item:
            raise RuleError("smelt 需要 item（raw_iron/beef）")
        return {"type": "smelt", "item": item, "count": _count(step)}
    if kind == "place":
        block = str(step.get("block") or step.get("item") or "").strip().lower()
        if not block:
            raise RuleError("place 需要 block")
        return {"type": "place", "block": block}
    if kind == "attack":
        target = str(step.get("target") or step.get("mob") or "nearest").strip().lower()
        return {"type": "attack", "target": target}
    if kind == "eat":
        item = str(step.get("item") or "").strip().lower()
        return {"type": "eat", "item": item}
    if kind == "equip":
        item = str(step.get("item") or "").strip().lower()
        if not item:
            raise RuleError("equip 需要 item")
        return {"type": "equip", "item": item}
    seconds = _float(step.get("seconds"), 1.0)
    if not (0.2 <= seconds <= MAX_WAIT):
        raise RuleError(f"wait 的 seconds 要在 0.2–{MAX_WAIT:.0f} 之间")
    return {"type": "wait", "seconds": round(seconds, 1)}


def _count(step: dict) -> int:
    number = int(_float(step.get("count"), 1))
    if not (1 <= number <= MAX_COUNT):
        raise RuleError(f"count 要在 1–{MAX_COUNT} 之间")
    return number


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_block_name(target: str) -> str:
    if target in KIND_BLOCK:
        return KIND_BLOCK[target]
    if target in BLOCK_DROPS:
        return target
    raise RuleError(f"不认识的目标：{target}（用 tree/stone/coal/iron 或方块名）")


def _handle_goto(state: dict, step: dict) -> str:
    player = state["player"]
    if "x" in step:
        player.update(x=round(step["x"], 1), y=round(step["y"], 1),
                      z=round(step["z"], 1))
        return f"走到 ({player['x']:.0f}, {player['y']:.0f}, {player['z']:.0f})"
    target = str(step["target"])
    if target in ("home", "spawn", "base"):
        player.update(x=0.0, y=64.0, z=0.0)
        return "回到家门口"
    if target in KIND_BLOCK or target in BLOCK_DROPS:
        name = _resolve_block_name(target)
        block = nearest_block(state, name)
        if block is None:
            raise RuleError(f"附近没有{cn(name)}")
        player.update(x=float(block["x"]), y=float(block["y"]),
                      z=float(block["z"]))
        return f"走到{cn(name)}旁边（{block['x']}, {block['y']}, {block['z']}）"
    mob = nearest_mob(state, target)
    if mob is None:
        raise RuleError(f"附近没有{cn(target)}")
    player.update(x=round(float(mob["x"]) + 1.0, 1), y=float(mob["y"]),
                  z=round(float(mob["z"]), 1))
    return f"靠近{cn(target)}"


def _handle_mine(state: dict, step: dict) -> str:
    name = _resolve_block_name(str(step["block"]))
    required = int(PICK_REQUIRED.get(name, 0))
    tier = pickaxe_tier(state)
    if required and tier < required:
        need = {1: "木镐", 2: "石镐", 3: "铁镐"}[required]
        raise RuleError(f"挖{cn(name)}需要{need}；先 craft wooden_pickaxe 之类")
    blocks = [b for b in state.get("blocks") or [] if b.get("name") == name]
    if not blocks:
        raise RuleError(f"附近没有{cn(name)}了")
    blocks.sort(key=lambda b: distance(state, b["x"], b["z"]))
    count = min(int(step["count"]), len(blocks))
    for block in blocks[:count]:
        state["blocks"].remove(block)
        for item, number in BLOCK_DROPS[name].items():
            give(state, item, number)
    state["stats"]["mined"] = int(state["stats"].get("mined") or 0) + count
    return f"挖到 {count}×{cn(name)}"


def _handle_craft(state: dict, step: dict) -> str:
    item = str(step["item"])
    recipe = RECIPES.get(item)
    if recipe is None:
        raise RuleError(f"不会合成 {item}；可用：{', '.join(sorted(RECIPES))}")
    count = int(step["count"])
    if recipe.get("table") and not state["flags"].get("crafting_table"):
        raise RuleError(f"合成{cn(item)}需要工作台；先 craft crafting_table 再 place")
    for material, need in recipe["in"].items():
        want = int(need) * count
        have = count_of(state, material)
        if have < want:
            raise RuleError(
                f"{cn(material)}不够：{cn(item)}×{count} 需要 {want}，只有 {have}")
    for material, need in recipe["in"].items():
        take(state, material, int(need) * count)
    out = int(recipe["out"]) * count
    give(state, item, out)
    state["stats"]["crafted"] = int(state["stats"].get("crafted") or 0) + count
    return f"合成 {out}×{cn(item)}"


def count_of(state: dict, item: str) -> int:
    return count(state, item)


def _handle_smelt(state: dict, step: dict) -> str:
    item = str(step["item"])
    if not state["flags"].get("furnace"):
        raise RuleError("还没有熔炉；先 craft furnace 再 place")
    target = SMELT.get(item)
    if target is None:
        raise RuleError(f"熔炉烧不了 {cn(item)}；用 raw_iron 或 beef")
    count = int(step["count"])
    if count_of(state, item) < count:
        raise RuleError(f"{cn(item)}不够：要 {count}，只有 {count_of(state, item)}")
    if count_of(state, "coal") < count:
        raise RuleError(f"煤炭不够：烧 {count} 个要 {count} 块煤")
    for _ in range(count):
        take(state, item, 1)
        take(state, "coal", 1)
        give(state, target[0], int(target[1]))
    return f"烧好 {count}×{cn(target[0])}"


def _handle_place(state: dict, step: dict) -> str:
    block = str(step["block"])
    if block not in PLACEABLE:
        raise RuleError(f"不能放置 {block}；可放：{', '.join(sorted(PLACEABLE))}")
    if count_of(state, block) < 1:
        raise RuleError(f"背包里没有{cn(block)}")
    take(state, block, 1)
    if block == "crafting_table":
        state["flags"]["crafting_table"] = True
    if block == "furnace":
        state["flags"]["furnace"] = True
    if block in SOLID_PLACE:
        player = state["player"]
        player["shelter"] = round(min(1.0, float(player.get("shelter") or 0.0)
                                      + SHELTER_PER_BLOCK), 2)
    state["stats"]["placed"] = int(state["stats"].get("placed") or 0) + 1
    return f"放下{cn(block)}"


def _handle_attack(state: dict, step: dict) -> str:
    target = str(step["target"])
    hostile = target in ("nearest", "zombie", "zombies", "hostile", "mob")
    if hostile:
        mob = nearest_mob(state, hostile_only=True)
        if mob is None:
            mob = nearest_mob(state, "zombie")
    else:
        mob = nearest_mob(state, target)
    if mob is None:
        raise RuleError(f"附近没有{cn(target) if target != 'nearest' else '怪'}")
    if distance(state, mob["x"], mob["z"]) > 6.0:
        raise RuleError(f"{cn(mob['name'])}太远了，先 goto 靠近")
    damage = weapon_damage(state)
    mob["hp"] = int(mob.get("hp") or 0) - damage
    if int(mob["hp"]) <= 0:
        state["mobs"].remove(mob)
        state["stats"]["kills"] = int(state["stats"].get("kills") or 0) + 1
        if mob["name"] == "cow":
            give(state, "beef", 1)
            return f"打倒{cn('cow')}，拿到生牛肉"
        return f"打倒{cn(mob['name'])}"
    player = state["player"]
    hit = int(mob.get("attack") or 0)
    if hit:
        player["hp"] = int(player.get("hp") or 0) - hit
    return f"打{cn(mob['name'])} {damage} 点，它反击 {hit} 点"


def _handle_eat(state: dict, step: dict) -> str:
    player = state["player"]
    if float(player.get("food") or 0.0) >= 19.5:
        raise RuleError("现在不饿，先把食物留着")
    item = str(step.get("item") or "")
    if not item:
        owned = [name for name in FOOD if count_of(state, name) > 0]
        if not owned:
            raise RuleError("背包里没有吃的")
        item = max(owned, key=lambda name: FOOD[name])
    if item not in FOOD:
        raise RuleError(f"{cn(item)}不能吃；能吃：{', '.join(FOOD)}")
    if count_of(state, item) < 1:
        raise RuleError(f"背包里没有{cn(item)}")
    take(state, item, 1)
    player["food"] = round(min(20.0, float(player.get("food") or 0.0)
                               + FOOD[item]), 1)
    return f"吃了{cn(item)}，饱食度回到 {player['food']:.0f}"


def _handle_equip(state: dict, step: dict) -> str:
    item = str(step["item"])
    if item not in RECIPES and item not in ("bread", "cooked_beef"):
        raise RuleError(f"不知道 {item} 是什么")
    if count_of(state, item) < 1:
        raise RuleError(f"背包里没有{cn(item)}")
    state["player"]["held"] = item
    return f"手持{cn(item)}"


_HANDLERS = {
    "goto": _handle_goto,
    "mine": _handle_mine,
    "craft": _handle_craft,
    "smelt": _handle_smelt,
    "place": _handle_place,
    "attack": _handle_attack,
    "eat": _handle_eat,
    "equip": _handle_equip,
    "wait": lambda state, step: "原地等了一会儿",
}


def apply_step(state: dict, step: Any) -> dict:
    """执行一个动作并推进时间；不合法抛 :class:`RuleError`。

    调用方负责持久化；这个函数只改传入的 dict，所以验算可以直接在副本上跑。
    """
    if state.get("screen") == "dead":
        raise RuleError("角色已经倒下，先重生再行动")
    if state.get("screen") == "paused":
        raise RuleError("游戏已暂停")
    action = normalize_step(step)
    message = _HANDLERS[action["type"]](state, action)
    seconds = (float(action["seconds"]) if action["type"] == "wait"
               else ACTION_SECONDS[action["type"]])
    notes = tick(state, seconds)
    if notes:
        message = "；".join([message] + notes)
    state["stats"]["actions"] = int(state["stats"].get("actions") or 0) + 1
    state["last"] = message
    return {"ok": True, "type": action["type"], "message": message,
            "state": state}


__all__ = [
    "ACTIONS", "ACTION_SECONDS", "BLOCK_DROPS", "BLOCK_KIND", "BRIDGE_VERSION",
    "DAY_LENGTH", "DIFFICULTIES", "FOOD", "GAME_ID", "KIND_BLOCK", "MAX_COUNT",
    "MAX_STEPS", "MAX_WAIT", "NAMES", "PICKAXE_TIER", "PICK_REQUIRED",
    "PLACEABLE", "RECIPES", "RuleError", "SHELTER_FULL", "SMELT", "WEAPON_DAMAGE",
    "apply_step", "clock_of", "cn", "count", "day_of", "distance", "give",
    "inventory", "nearest_block", "nearest_mob", "new_world", "normalize_step",
    "phase_of", "pickaxe_tier", "tick", "weapon_damage",
]
