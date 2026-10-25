# -*- coding: utf-8 -*-
"""Offline checks for the multi-select UX changes in sts2.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sts2


def _base(screen, selection=None, **extra):
    s = {
        "screen": screen,
        "run": {"floor": 1, "hp": 48, "max_hp": 80, "gold": 99},
        "augment": {},
    }
    if selection is not None:
        s["selection"] = selection
    s.update(extra)
    return s


cards = [
    {"card_id": "STRIKE_IRONCLAD", "energy_cost": 1, "is_selected": False},
    {"card_id": "STRIKE_IRONCLAD", "energy_cost": 1, "is_selected": True},
    {"card_id": "DEFEND_IRONCLAD", "energy_cost": 1, "is_selected": False},
    {"card_id": "BASH", "energy_cost": 2, "is_selected": False},
]

print("=== 1) 多选进行中：已选 1/2 ===")
print(
    sts2.compact(
        _base(
            "CARD_SELECTION",
            {
                "prompt": "选择[blue]2[/blue]张牌来[gold]移除[/gold]。",
                "min_select": 2,
                "max_select": 2,
                "selected_count": 1,
                "cards": cards,
            },
        )
    )
)

print()
print("=== 2) 已满足、需要确认 ===")
print(
    sts2.compact(
        _base(
            "CARD_SELECTION",
            {
                "prompt": "选择 2 张牌移除",
                "min_select": 2,
                "max_select": 3,
                "selected_count": 2,
                "requires_confirmation": True,
                "cards": cards,
            },
        )
    )
)

print()
print("=== 3) 界面没暴露张数（降级提示） ===")
print(
    sts2.compact(
        _base(
            "CARD_SELECTION",
            {
                "prompt": "选择 2 张牌移除",
                "min_select": 2,
                "max_select": 2,
                "cards": [{"card_id": "STRIKE_IRONCLAD", "energy_cost": 1}],
            },
        )
    )
)

print()
print("=== 4) 单张选择不误报 ===")
print(
    sts2.compact(
        _base(
            "CARD_SELECTION",
            {"prompt": "选择 1 张牌升级", "min_select": 1, "max_select": 1, "selected_count": 0, "cards": cards[:1]},
        )
    )
)

print()
print("=== 5) 指纹能感知「选中/取消」 ===")
a = _base("CARD_SELECTION", {"prompt": "p", "selected_count": 0, "cards": [{"card_id": "X", "is_selected": False}]})
b = _base("CARD_SELECTION", {"prompt": "p", "selected_count": 1, "cards": [{"card_id": "X", "is_selected": True}]})
print("fingerprint 不同:", sts2._fingerprint(a) != sts2._fingerprint(b))

print()
print("=== 6) 描述定位在列表变化后仍能找到同一张 ===")
orig = [
    {"card_id": "STRIKE_IRONCLAD", "upgraded": False},
    {"card_id": "STRIKE_IRONCLAD", "upgraded": False},
    {"card_id": "DEFEND_IRONCLAD", "upgraded": False},
]
after_removal = [
    {"card_id": "STRIKE_IRONCLAD", "upgraded": False},
    {"card_id": "DEFEND_IRONCLAD", "upgraded": False},
]
exact_desc = {"card_id": "STRIKE_IRONCLAD", "upgraded": False, "occurrence": 0}
fallback_desc = {"card_id": "STRIKE_IRONCLAD", "upgraded": False, "occurrence": 1}
print("精确命中（第 1 张打击）:", sts2._resolve_by_descriptor(after_removal, exact_desc), "(应为 (0, True))")
print("回退命中（第 2 张打击已被移除）:", sts2._resolve_by_descriptor(after_removal, fallback_desc), "(应为 (0, False))")
missing_desc = {"card_id": "BASH", "upgraded": False, "occurrence": 0}
print("完全不在列表:", sts2._resolve_by_descriptor(after_removal, missing_desc), "(应为 (None, False))")

print()
print("=== 7) 卡牌奖励（min/max=0，走 choose_reward_card） ===")
print(
    sts2.compact(
        _base(
            "CARD_SELECTION",
            {
                "kind": "deck_card_select",
                "prompt": "造成8点伤害。",
                "min_select": 0,
                "max_select": 0,
                "selected_count": 0,
                "requires_confirmation": False,
                "can_confirm": False,
                "cards": cards,
            },
            available_actions=[
                "save_and_quit",
                "resolve_rewards",
                "choose_reward_card",
                "skip_reward_cards",
                "select_deck_card",
            ],
        )
    )
)

sys.exit(0)
