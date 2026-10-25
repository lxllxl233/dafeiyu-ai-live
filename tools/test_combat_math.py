# -*- coding: utf-8 -*-
"""离线验证战斗数值：状态识别、多段模拟、条件多段、EFF 表、同名牌提醒。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sts2

ok = True


def check(name, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


print("=== 1) 能力名识别（中/英） ===")
pm = sts2._power_map(
    [
        {"name": "易伤", "amount": 2},
        {"name": "Thorns", "amount": 3},
        {"name": "胆小", "amount": 7},
        {"name": "力量", "amount": 3},
        {"name": "Weak", "amount": 1},
        {"name": "奇怪的Buff", "amount": 5},
    ]
)
check("vuln", pm.get("vuln"), 2)
check("thorns", pm.get("thorns"), 3)
check("timid", pm.get("timid"), 7)
check("str", pm.get("str"), 3)
check("weak", pm.get("weak"), 1)
check("未知能力保留原名", pm.get("奇怪的Buff"), 5)

print()
print("=== 2) 多段命中模拟（格挡池 + 胆小首击加盾） ===")
check("9 单段 vs 0 格挡", sts2._sim_hits(9, 1, 0, 0), 9)
check("9 单段 vs 10 格挡", sts2._sim_hits(9, 1, 10, 0), 0)
check("9x2 段 vs 10 格挡（第一段打空，第二段进 8）", sts2._sim_hits(9, 2, 10, 0), 8)
check("9 单段 vs 胆小 7", sts2._sim_hits(9, 1, 0, 7), 2)
check("9x2 段 vs 胆小 7（只有第一段被吃）", sts2._sim_hits(9, 2, 0, 7), 11)
check("3x3 段 vs 胆小 7（7 点格挡池吃完还剩 2 点进血）", sts2._sim_hits(3, 3, 0, 7), 2)

print()
print("=== 3) 牌面文字里的多段/条件多段 ===")
dismantle = {"card_id": "DISMANTLE", "resolved_rules_text": "造成8点伤害。 如果该敌人有易伤状态，则攻击两次。"}
check("拆卸 (固定段, 易伤段)", sts2._hits_from_text(dismantle), (1, 2))
boomerang = {"card_id": "SWORD_BOOMERANG", "resolved_rules_text": "随机对敌人造成3点伤害3次。"}
check("飞剑回旋镖", sts2._hits_from_text(boomerang), (3, 0))
plain = {"card_id": "STRIKE_IRONCLAD", "resolved_rules_text": "造成6点伤害。"}
check("打击", sts2._hits_from_text(plain), (1, 0))

print()
print("=== 4) EFF 表（易伤/格挡/胆小/荆棘） ===")
hand = [
    {"card_id": "STRIKE_IRONCLAD", "energy_cost": 1, "dynamic_values": [{"name": "Damage", "current_value": 6}]},
    {"card_id": "UPPERCUT", "energy_cost": 2, "dynamic_values": [{"name": "Damage", "current_value": 13}, {"name": "VulnerablePower", "current_value": 1}]},
    {"card_id": "DISMANTLE", "energy_cost": 1, "resolved_rules_text": "造成8点伤害。 如果该敌人有易伤状态，则攻击两次。", "dynamic_values": [{"name": "Damage", "current_value": 8}]},
]
enemies = [
    {"enemy_id": "A", "current_hp": 30, "block": 10, "powers": [{"name": "荆棘", "amount": 2}]},
    {"enemy_id": "B", "current_hp": 20, "block": 0, "powers": [{"name": "易伤", "amount": 2}]},
    {"enemy_id": "C", "current_hp": 20, "block": 0, "powers": [{"name": "易伤", "amount": 2}, {"name": "胆小", "amount": 7}]},
]
rows = sts2._attack_effect_table(hand, enemies, {"powers": []})
for r in rows:
    print("   ", r)
joined = "\n".join(rows)
check("打击对 B 易伤 9", "e1:9(易伤)" in joined, True)
check("拆卸对 B 12x2=24", "e1:24(易伤)" in joined, True)
check("打击对 A 被 10 格挡吃掉", "e0:0" in joined, True)
check("打击对 C 被胆小 7 吃掉后剩 2", "e2:2" in joined, True)
check("荆棘反伤提示", "反伤-2" in joined, True)
check("先挂易伤的引导", any(r.startswith("ORDER") for r in rows), True)

print()
print("=== 5) 同名不同级提醒 ===")
hand2 = [
    {"card_id": "STRIKE_IRONCLAD", "upgraded": False},
    {"card_id": "STRIKE_IRONCLAD", "upgraded": True},
    {"card_id": "BASH", "upgraded": False},
]
line = sts2._copy_check(hand2)
print("   ", line)
check("提示打升级版", "打升级版" in line, True)

print()
print("=== 6) --card 指到弱副本时告警 ===")
fake_state = {
    "screen": "COMBAT",
    "combat": {
        "hand": [
            {"card_id": "STRIKE_IRONCLAD", "upgraded": False, "energy_cost": 1,
             "dynamic_values": [{"name": "Damage", "current_value": 6}]},
            {"card_id": "STRIKE_IRONCLAD", "upgraded": True, "energy_cost": 1,
             "dynamic_values": [{"name": "Damage", "current_value": 9}]},
        ],
        "enemies": [{"enemy_id": "X", "current_hp": 30, "intents": []}],
        "player": {"energy": 3},
    },
    "available_actions": ["play_card", "end_turn"],
}
sts2.get_state = lambda: fake_state
calls = []
sts2.cmd_do = lambda *a, **k: calls.append((a, k))
sts2.cmd_play("STRIKE_IRONCLAD", target=0, card=0)
print("     play_card 调用:", calls)
check("打到的是下标 0", calls and calls[0][1].get("card") == 0, True)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
