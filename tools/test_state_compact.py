# -*- coding: utf-8 -*-
"""context 压缩回归测试：同一战斗重复 state 只打增量，省 token。

用法：python tools/test_state_compact.py
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import sts2  # noqa: E402

CARD = {
    "card_id": "DISMANTLE",
    "upgraded": False,
    "energy_cost": 1,
    "target_type": "AnyEnemy",
    "resolved_rules_text": "造成8点伤害。 如果该敌人有易伤状态，则攻击两次。",
    "dynamic_values": [{"name": "Damage", "base_value": 8, "current_value": 8}],
    "playable": True,
    "valid_target_indices": [0, 1],
}


def build_state(potions=None, floor=12):
    return {
        "screen": "COMBAT",
        "run_id": "run_test",
        "available_actions": ["play_card", "end_turn"],
        "run": {
            "character_id": "IRONCLAD",
            "ascension": 10,
            "act_id": 0,
            "floor": floor,
            "current_hp": 53,
            "max_hp": 84,
            "gold": 58,
            "deck": [
                {"card_id": "STRIKE_IRONCLAD", "card_type": "Attack"},
                {"card_id": "DEFEND_IRONCLAD", "card_type": "Skill"},
                {"card_id": "DEBT", "card_type": "Curse"},
            ],
            "potions": potions or [{"potion_id": "BLOOD_POTION", "occupied": True}],
            "relics": [
                {"relic_id": "BURNING_BLOOD"},
                {"relic_id": "GREMLIN_HORN"},
            ],
        },
        "combat": {
            "turn_number": 1,
            "player": {"energy": 3, "block": 0},
            "hand": [dict(CARD) for _ in range(5)],
            "enemies": [
                {"enemy_id": "PHROG_PARASITE", "name": "Phrog Parasite", "hp": 68, "max_hp": 68,
                 "block": 0, "intent": {"type": "StatusCard"}},
            ],
        },
    }


def main():
    cache = sts2.STATE_CACHE
    if os.path.exists(cache):
        os.remove(cache)

    first = sts2.compact(build_state())
    second = sts2.compact(build_state())
    third = sts2.compact(build_state(potions=[{"potion_id": "FLEX_POTION", "occupied": True}]))

    checks = [
        ("首读含完整卡面", "造成8点伤害" in first),
        ("首读含药水说明", "POTION FX" in first),
        ("首读含遗物说明", "RELIC FX" in first),
        ("首读含机制", "MECHANICS" in first),
        ("二读省掉药水说明", "POTION FX" not in second),
        ("二读省掉遗物说明", "RELIC FX" not in second),
        ("二读省掉机制", "MECHANICS" not in second),
        ("二读卡片走短格式", "DISMANTLE c1" in second),
        ("换药水后重新提示", "POTION FX" in third),
        ("HUD 常驻槽位", "HUD potions 0:BLOOD_POTION" in first),
    ]
    ok = True
    for name, passed in checks:
        print(("PASS " if passed else "FAIL ") + name)
        ok = ok and passed

    print(f"bytes: first={len(first.encode('utf-8'))} second={len(second.encode('utf-8'))} "
          f"save={(1 - len(second) / max(1, len(first))) * 100:.0f}%")
    if len(second) >= len(first) * 0.75:
        print("FAIL 二读没有明显变短")
        ok = False
    if os.path.exists(cache):
        os.remove(cache)
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
