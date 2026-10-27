# -*- coding: utf-8 -*-
"""历史错误回归库：每个用例都来自账本 `logs/decisions.jsonl` 的真实局面。

和 test_sim.py 的区别：那边的局面是手搓的；这里每一个都标了 run/楼层/回合，
是**真的打错或算错过的局面**，修完后必须仍然算对。跑法：

    python tools/test_regressions.py

口径：只断言「当时可见信息 + 确定性结算」，不涉及抽牌随机与 AI 评价。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("STS2_DECISION_LOG", "0")

import sim  # noqa: E402

ok = True


def check(name, got, want=True):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def card(cid, cost=1, dmg=0, blk=0, up=False, rules="", dyn=None, **extra):
    c = {"card_id": cid, "name": cid, "energy_cost": cost, "playable": True,
         "upgraded": up,
         "target_type": "AnyEnemy" if (dmg and not extra.get("aoe")) else "Self"}
    if dmg:
        c["damage"] = dmg
    if blk:
        c["block"] = blk
    if rules:
        c["rules_text"] = rules
    if dyn:
        c["dynamic_values"] = [{"name": k, "current_value": v} for k, v in dyn.items()]
    c.update(extra)
    return c


def enemy(idx=0, eid="E0", hp=20, atk=12, powers=None):
    return {"index": idx, "enemy_id": eid, "name": eid, "current_hp": hp, "hp": hp,
            "max_hp": hp, "is_alive": True, "block": 0, "powers": powers or [],
            "intents": [{"type": "Attack", "damage": atk, "hits": 1,
                         "total_damage": atk}]}


def state(hand, energy=3, hp=50, enemies=None, powers=None, relics=None, potions=None):
    return {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn"],
        "run": {"current_hp": hp, "max_hp": 80, "max_energy": 3,
                "potions": potions or [], "floor": 5, "act": 1, "ascension": 10,
                "relics": [{"relic_id": r} for r in (relics or [])]},
        "combat": {"turn": 3,
                   "player": {"current_hp": hp, "max_hp": 80, "energy": energy,
                              "block": 0, "powers": powers or []},
                   "hand": hand,
                   "enemies": enemies or [enemy()]},
    }


# ---------------------------------------------------------------- R1 状态牌不是攻击牌
# run HECC0H1NUR f30 t2：手牌两张「毒素」（状态牌，1 费，回合末在手扣 5 血）。
# 旧 sim 把引擎给的 Damage=5 当成攻击伤害，两次「打毒素」凭空对敌人输出 10 点，
# 预测敌人 59 血、实际 69 血 —— 斩杀线直接被算高 10 点。
TOXIC_TEXT = "在你的回合结束时，如果这张牌在你的手牌中，你受到5点伤害。"
st = state([card("TOXIC", 1, rules=TOXIC_TEXT), card("TOXIC", 1, rules=TOXIC_TEXT),
            card("BATTLE_TRANCE", 0)], energy=3, enemies=[enemy(0, "MYTE", 69, 10)])
sm = sim.Sim.from_state(st)
t0 = sm.by_index(0)["hp"]
check("R1 毒素回合末税 5/张", sim.status_tax_parts(sm.hand), (10, 0))
sm.play(sm.hand[0], None)
sm.play(sm.hand[1], None)
check("R1 打状态牌不伤敌人", sm.by_index(0)["hp"], t0)
check("R1 状态牌照收费用", sm.energy, 1)
check("R1 打掉两张税清零", sim.status_tax_parts(sm.hand), (0, 0))

# ---------------------------------------------------------------- R2 势不可当要算伤害
# run Z6DHD2PJBQ f14 t2：势不可当+（8 点）+ 耸肩无视，实际敌人掉 8 血，旧 sim 掉 0。
st = state([card("JUGGERNAUT", 2, up=True), card("SHRUG_IT_OFF", 1, blk=8)],
           energy=3, enemies=[enemy(0, "CORPSE_SLUG", 28, 12)],
           powers=[{"power_id": "JUGGERNAUT_POWER", "name": "势不可当", "amount": 4}])
sm = sim.Sim.from_state(st)
e0 = sm.by_index(0)["hp"]
jug = next(c for c in sm.hand if c["id"] == "JUGGERNAUT")
step = sm.play(jug, None)
check("R2 势不可当进场不吃伤害", sm.by_index(0)["hp"], e0)
shr = next(c for c in sm.hand if c["id"] == "SHRUG_IT_OFF")
step = sm.play(shr, None)
check("R2 起甲触发势不可当", sm.by_index(0)["hp"], e0 - 4)
check("R2 步骤里记下势不可当伤害", step.get("jugg"), 4)

# ---------------------------------------------------------------- R3 延迟给费不能当场到账
# run 6E26GF0HPM f9：战鼓（被消耗时获得 2 费）当回合预测 5 费、实际 3 费；
# run Z6DHD2PJBQ f25：薪火之源（回合开始时获得）同理。
for cid, cost, rules, dyn in (
        ("DRUM_OF_BATTLE", 0, "抽2张牌。这张牌被消耗时，获得。", {"Energy": 2}),
        ("PYRE", 2, "在回合开始时，获得。", {"Energy": 2})):
    st = state([card(cid, cost, rules=rules, dyn=dyn)], energy=3)
    sm = sim.Sim.from_state(st)
    c = sm.hand[0]
    check(f"R3 {cid} 不当场回能", c.get("en"), None)
    check(f"R3 {cid} 记为延迟", bool(c.get("en_deferred")), True)
    sm.play(c, None)
    check(f"R3 {cid} 打完能量不变", sm.energy, 3 - cost)

# ---------------------------------------------------------------- R4 滑溜：一次只掉 1
# run RJJBH9NXR8 f12 t1：三只小墨宝各带「滑溜」1 层，旧 sim 一刀算满伤 → 误判能斩。
st = state([card("STRIKE_IRONCLAD", 1, dmg=6), card("STRIKE_IRONCLAD", 1, dmg=6)],
           energy=3, enemies=[enemy(0, "INKLET", 17, 9,
                                    powers=[{"power_id": "SLIPPERY_POWER", "name": "滑溜",
                                             "amount": 1}])])
sm = sim.Sim.from_state(st)
e = sm.by_index(0)
sm.play(sm.hand[0], 0)
check("R4 滑溜第一刀只掉 1", e["hp"], 16)
sm.play(sm.hand[1], 0)
check("R4 滑溜用掉后正常吃伤", e["hp"], 10)

# ---------------------------------------------------------------- R5 状态牌不打会扣自己
# 同 R1 局面：回合末两张毒素在手 → 10 点可格挡伤害，且不算「失去生命」。
st = state([card("STRIKE_IRONCLAD", 1, dmg=6), card("TOXIC", 1), card("TOXIC", 1)],
           energy=3)
sm = sim.Sim.from_state(st)
check("R5 回合末税按张数叠加", sim.status_tax_parts(sm.hand), (10, 0))
sm.play(sm.hand[0], 0)
check("R5 打掉一张税就少 5", sim.status_tax_parts(sm.hand), (10, 0))

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
