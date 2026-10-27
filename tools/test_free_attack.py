# -*- coding: utf-8 -*-
"""免费攻击（FREE_ATTACK / 无情猛攻）的确定性模拟测试。

为什么单独一个文件：run85 里这个 bug 造成了 4 次 batch 中断（61 个 model 批次里
6.6%），是全轮对「出手质量」伤害最大的缺陷。它的形态只有三种：

1. 引擎在 buff 生效时把**所有**攻击牌的 energy_cost 报成 0（手牌看起来整手免费）；
2. 「免费」只该作用在**下一张**攻击牌上，打过一张就用掉；
3. 因此**出牌顺序**会改变「这一回合到底能打几张」，而旧模型对此完全不敏感。

这里不碰网络、不碰游戏，全部用手搓局面 + 本地 sim 结算：

    python tools/test_free_attack.py
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


def card(cid, cost, dmg, rules=""):
    c = {"card_id": cid, "name": cid, "energy_cost": cost, "playable": True,
         "upgraded": False, "target_type": "AnyEnemy",
         "dynamic_values": [{"name": "Damage", "current_value": dmg}]}
    if rules:
        c["rules_text"] = rules
    return c


def enemy(hp=200):
    return {"index": 0, "enemy_id": "DUMMY", "name": "木桩", "current_hp": hp, "hp": hp,
            "max_hp": hp, "is_alive": True, "block": 0, "powers": [],
            "intents": [{"type": "Attack", "damage": 0, "hits": 1, "total_damage": 0}]}


def state(hand, energy, free=0):
    powers = [{"power_id": "FREE_ATTACK", "name": "免费攻击", "amount": free}] if free else []
    return {"screen": "COMBAT", "available_actions": ["play_card", "end_turn"],
            "run": {"current_hp": 70, "max_hp": 80, "max_energy": 3, "floor": 5,
                    "act": 1, "ascension": 10, "relics": [], "potions": []},
            "combat": {"turn": 2,
                       "player": {"current_hp": 70, "max_hp": 80, "energy": energy,
                                  "block": 0, "powers": powers},
                       "hand": hand, "enemies": [enemy()]}}


def run_plan(hand, energy, free, ids):
    """按 ids 顺序出牌，返回 (打出的张数, 剩余能量, 是否打出过超额)。"""
    st = state([dict(c) for c in hand], energy, free)
    sm = sim.Sim.from_state(st)
    played = 0
    for cid in ids:
        c = next((x for x in sm.hand if x["id"] == cid and not x.get("_played")), None)
        if c is None:
            break
        if sm.play(c, 0) is None:
            break
        played += 1
        if sm.energy < 0:
            return played, sm.energy, True
    return played, sm.energy, sm.energy < 0


BASH = card("BASH", 2, 8)
HIT = card("STRIKE_IRONCLAD", 1, 6)

# ---------------------------------------------------------------- 1) 一张免费 = 只免一张
print("=== 1) 1 张免费 + 3 张攻击（2/1/1 费），能量 2 ===")
sm = sim.Sim.from_state(state([dict(BASH), dict(HIT), dict(HIT)], 2, 1))
check("免费层数", sm.free_atk, 1)
check("下一张攻击 0 费", sm.cost_of(sm.hand[0]), 0)
n, e, over = run_plan([BASH, HIT, HIT], 2, 1, ["BASH", "STRIKE_IRONCLAD", "STRIKE_IRONCLAD"])
check("先打贵的：3 张全打出", (n, e, over), (3, 0, False))
n2, e2, over2 = run_plan([BASH, HIT, HIT], 2, 1, ["STRIKE_IRONCLAD", "BASH", "STRIKE_IRONCLAD"])
check("先打便宜的：只打出 2 张（免费被小刀吃掉）", (n2, e2, over2), (2, 0, False))
check("顺序不同结果不同（这就是旧模型缺的敏感度）", n != n2, True)

# ---------------------------------------------------------------- 2) 耗尽：3 费 + 1 免 → 4 张
print("\n=== 2) 1 张免费 + 5 张 1 费攻击，能量 3 ===")
five = [card("STRIKE_IRONCLAD", 1, 6) for _ in range(5)]
n, e, over = run_plan(five, 3, 1, ["STRIKE_IRONCLAD"] * 5)
check("最多打出 4 张（1 免 + 3 费）", (n, e), (4, 0))
check("全程能量不为负", over, False)

# ---------------------------------------------------------------- 3) 没有免费 → 只有 3 张
print("\n=== 3) 没有免费，5 张 1 费攻击，能量 3 ===")
n, e, over = run_plan(five, 3, 0, ["STRIKE_IRONCLAD"] * 5)
check("只能打出 3 张", (n, e), (3, 0))
check("没有超额", over, False)

# ---------------------------------------------------------------- 4) 免费只有一层，用完就恢复
print("\n=== 4) 免费层数只被消耗一次 ===")
sm = sim.Sim.from_state(state([dict(HIT), dict(HIT)], 3, 1))
sm.play(sm.hand[0], 0)
check("用掉后层数归零", sm.free_atk, 0)
check("第二张按原费", sm.cost_of(sm.hand[1]), 1)
sm.play(sm.hand[1], 0)
check("总共只花了 1 费", sm.energy, 2)

# ---------------------------------------------------------------- 5) 2 层免费 ≠ 整手免费
print("\n=== 5) 2 层免费 + 4 张 1 费攻击，能量 2 ===")
n, e, over = run_plan(five[:4], 2, 2, ["STRIKE_IRONCLAD"] * 4)
check("2 免 + 2 费 = 4 张", (n, e), (4, 0))
sm2 = sim.Sim.from_state(state([dict(x) for x in five[:4]], 2, 2))
check("层数读数", sm2.free_atk, 2)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
