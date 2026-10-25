# -*- coding: utf-8 -*-
"""离线验证 route/plan：大路线规划 + 每次状态变化后的重规划判定。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sts2


def node(row, col, ntype, children=(), avail=False, current=False):
    return {
        "row": row,
        "col": col,
        "node_type": ntype,
        "children": [{"row": r, "col": c} for r, c in children],
        "is_available": avail,
        "is_current": current,
    }


# 两条分支：(1,1)->(2,1) Elite 线 / (1,3)->(2,3) 商店线，都汇到营火再 Boss
MAP = {
    "rows": 4,
    "nodes": [
        node(0, 2, "Ancient", [(1, 1), (1, 3)], current=True),
        node(1, 1, "Monster", [(2, 1)], avail=True),
        node(1, 3, "Monster", [(2, 3)], avail=True),
        node(2, 1, "Elite", [(3, 2)]),
        node(2, 3, "Shop", [(3, 2)]),
        node(3, 2, "RestSite", [(4, 2)]),
        node(4, 2, "Boss", []),
    ],
}


def mk_state(hp=80, mhp=80, gold=99, potions=0, deck=None, floor=1):
    deck = deck if deck is not None else (
        [{"card_id": "STRIKE_IRONCLAD", "upgraded": False}] * 5
        + [{"card_id": "DEFEND_IRONCLAD", "upgraded": False}] * 4
        + [{"card_id": "BASH", "upgraded": False}]
    )
    return {
        "screen": "MAP",
        "map": MAP,
        "run": {
            "current_hp": hp,
            "max_hp": mhp,
            "gold": gold,
            "floor": floor,
            "act": 1,
            "deck": deck,
            "potions": [{"occupied": True, "potion_id": "SWIFT_POTION"} for _ in range(potions)],
            "relics": ["BURNING_BLOOD"],
        },
    }


plan_path = sts2.PLAN_PATH
if os.path.exists(plan_path):
    os.replace(plan_path, plan_path + ".bak")

try:
    print("=== 1) 开局规划（80 血，防御 4 张） ===")
    sts2.get_state = lambda: mk_state()
    sts2.cmd_route()

    print()
    print("=== 2) 记录计划 opt0 ===")
    sts2.cmd_route(commit=0, note="测试计划")

    print()
    print("=== 3) 状态没变：应报 PLAN_OK ===")
    sts2.cmd_plan()

    print()
    print("=== 4) 掉血到 23/80：应报 REPLAN（血线跌破 70%） ===")
    sts2.get_state = lambda: mk_state(hp=23, floor=6)
    sts2.cmd_plan()

    print()
    print("=== 5) 防御牌补到 7 张 + 拿到 AOE：应提示精英门槛放宽 ===")
    deck2 = (
        [{"card_id": "STRIKE_IRONCLAD", "upgraded": False}] * 3
        + [{"card_id": "DEFEND_IRONCLAD", "upgraded": False}] * 4
        + [{"card_id": "BLOOD_WALL"}, {"card_id": "TRUE_GRIT"}, {"card_id": "TAUNT"}]
        + [{"card_id": "HOWL_FROM_BEYOND"}, {"card_id": "BASH"}]
    )
    sts2.get_state = lambda: mk_state(hp=60, floor=8, deck=deck2)
    sts2.cmd_plan()

    print()
    print("=== 6) 清空计划 ===")
    sts2.cmd_plan(clear=True)
    sts2.cmd_plan()
finally:
    if os.path.exists(plan_path):
        os.remove(plan_path)
    if os.path.exists(plan_path + ".bak"):
        os.replace(plan_path + ".bak", plan_path)

print()
print("DONE")
