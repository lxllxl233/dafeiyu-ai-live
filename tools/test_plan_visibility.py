# -*- coding: utf-8 -*-
"""P1-2 离线回归：计划可见性、8 张手牌、AOE/药水斩杀、maxdmg 风险线。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ctx  # noqa: E402
import sim  # noqa: E402

os.environ.setdefault("STS2_DECISION_LOG", "0")

ok = True


def check(name, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def card(cid, cost, dmg=None, blk=None, text="", target="AnyEnemy"):
    dv = []
    if dmg is not None:
        dv.append({"name": "Damage", "current_value": dmg})
    if blk is not None:
        dv.append({"name": "Block", "current_value": blk})
    return {"card_id": cid, "name": cid, "energy_cost": cost, "upgraded": False,
            "playable": True, "requires_target": dmg is not None and target != "AllEnemies",
            "target_type": target, "dynamic_values": dv, "resolved_rules_text": text}


def enemy(eid, hp, atk, idx=0):
    return {"index": idx, "enemy_id": eid, "name": eid, "current_hp": hp, "max_hp": hp,
            "block": 0, "is_alive": True, "powers": [],
            "intents": [{"intent_type": "ATTACK", "damage": atk, "hits": 1,
                         "total_damage": atk}]}


def state(hand, enemies, hp=60, energy=3, potions=None):
    return {
        "screen": "COMBAT", "available_actions": ["play_card", "end_turn"],
        "run": {"current_hp": hp, "max_hp": 80, "max_energy": 3, "floor": 1,
                "act_id": 0, "ascension": 10, "gold": 0, "deck": [], "relics": [],
                "potions": potions or []},
        "combat": {"turn": 1,
                   "player": {"current_hp": hp, "max_hp": 80, "energy": energy,
                              "block": 0, "powers": []},
                   "hand": hand, "enemies": enemies},
    }


FIRE = {"index": 0, "potion_id": "FIRE_POTION", "name": "火焰药水", "occupied": True,
        "description": "对一名敌人造成20点伤害。", "can_use": True, "requires_target": True}


print("=== 1) 五类计划都输出，药水单列 ===")
st = state([card("DEFEND_IRONCLAD", 1, blk=10), card("STRIKE_IRONCLAD", 1, dmg=20)],
           [enemy("A", 18, 10)], potions=[FIRE])
d = ctx._combat(st, {})
kinds = [p["k"] for p in d["plans"]]
print("  plans", [(p["k"], p["d"], p.get("risk")) for p in d["plans"]])
check("五类齐全", set(kinds), {"best", "maxdmg", "kill", "guard", "potion"})
check("药水线可见", "potion" in kinds, True)
check("calc.dmg 取全部计划最大值", d["calc"]["dmg"], 20)

print()
print("=== 2) 第 8 张手牌进入伤害上限 ===")
hand = [card("STRIKE_IRONCLAD", 0, dmg=2) for _ in range(7)]
hand.append(card("STRIKE_IRONCLAD", 0, dmg=20))
d = ctx._combat(state(hand, [enemy("A", 100, 1)], energy=3), {})
print("  calc.dmg", d["calc"]["dmg"], "plans", [(p["k"], p["d"]) for p in d["plans"]])
check("8 张都能算进 maxdmg", d["calc"]["dmg"], 34)

print()
print("=== 3) AOE 斩杀纳入每个目标 ===")
aoe = card("WHIRLWIND", 1, dmg=12, target="AllEnemies", text="对所有敌人造成12点伤害。")
st = state([aoe], [enemy("A", 10, 10, idx=0), enemy("B", 10, 10, idx=1)])
lethal = {x["i"] for x in sim.lethal_targets(st)}
print("  lethal", lethal)
check("AOE 可杀 0/1", lethal, {0, 1})

aoe_potion = {"index": 0, "potion_id": "EXPLOSIVE_POTION", "name": "爆炸药水",
              "occupied": True, "description": "对所有敌人造成12点伤害。",
              "can_use": True, "requires_target": False}
st = state([], [enemy("A", 10, 10, idx=0), enemy("B", 10, 10, idx=1)],
           potions=[aoe_potion])
lethal = {x["i"] for x in sim.lethal_targets(st)}
print("  AoE potion lethal", lethal)
check("AOE 药水可杀 0/1", lethal, {0, 1})

print()
print("=== 4) 伤害药水可补刀 ===")
st = state([card("STRIKE_IRONCLAD", 1, dmg=6)], [enemy("A", 15, 10)], potions=[FIRE])
lethal = {x["i"] for x in sim.lethal_targets(st)}
print("  lethal", lethal)
check("药水补刀", lethal, {0})

print()
print("=== 5) maxdmg 自杀线标 RISK，不冒充安全 best ===")
st = state([card("DEFEND_IRONCLAD", 1, blk=5), card("STRIKE_IRONCLAD", 1, dmg=10)],
           [enemy("A", 100, 4)], hp=3, energy=1)
d = ctx._combat(st, {})
maxdmg = [p for p in d["plans"] if p["k"] == "maxdmg"]
best = [p for p in d["plans"] if p["k"] == "best"]
print("  plans", [(p["k"], p["hp"], p.get("risk")) for p in d["plans"]])
print("  warn", d.get("warn"))
check("maxdmg 存在", len(maxdmg), 1)
check("maxdmg 标 risk", maxdmg[0].get("risk"), 1)
check("best 不是自杀线", bool(best and not best[0].get("risk")), True)
check("有专门 RISK 警告", any("RISK" in w for w in d.get("warn", [])), True)

print()
print("=== 6) 搜索有时间上限，不无限展开 ===")
hand = [card(f"CARD{i}", 0, dmg=i + 1) for i in range(10)]
st = state(hand, [enemy("A", 100, 1)], energy=3)
t0 = time.monotonic()
plans = sim.search(st, max_cards=8, max_plans=5, time_limit=0.05)
elapsed = time.monotonic() - t0
print("  elapsed", round(elapsed, 3), "plans", len(plans))
check("时间上限生效", elapsed < 1.0, True)
check("仍有初始候选", len(plans) >= 1, True)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
