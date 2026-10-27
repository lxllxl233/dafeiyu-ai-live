# -*- coding: utf-8 -*-
"""门槛体检回归：甲牌识别 / 占比 / 抽牌 / 引擎判定。

    python tools/test_gates.py
"""
from __future__ import annotations

import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)

import gates  # noqa: E402

ok = True


def check(name, got, want=True):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def c(cid, name, text="", ctype="Skill", dynamic=None, cost=None):
    out = {"card_id": cid, "name": name, "card_type": ctype,
           "rules_text": text, "resolved_rules_text": text,
           "dynamic_values": dynamic or []}
    if cost is not None:
        out["cost"] = cost
    return out


deck_ok = (
    [c("STRIKE_IRONCLAD", "打击", "造成6点伤害。", "Attack")] * 2
    + [c("DEFEND_IRONCLAD", "防御", "获得5点格挡。")] * 4
    + [c("SHRUG_IT_OFF", "耸肩无视", "获得8点格挡。 抽1张牌。")] * 2
    + [c("BLOOD_WALL", "血墙", "失去2点生命。 获得16点格挡。")]
    + [c("BATTLE_TRANCE", "战斗恍惚", "抽3张牌。")]
    + [c("PERFECTED_STRIKE", "完美打击", "造成16点伤害。", "Attack",
         [{"name": "Damage", "base_value": 16, "current_value": 16}])]
    + [c("UPPERCUT", "上勾拳", "造成13点伤害。", "Attack",
         [{"name": "Damage", "base_value": 13, "current_value": 13}])]
    + [c("BARRICADE", "壁垒", "格挡不再在你的回合开始时消失。", "Power")]
    + [c("BLADE_DANCE", "刀刃之舞", "生成3把小刀。")]
    + [c("BRIGHT_SWORD", "亮剑", "造成5点伤害。 抽1张牌。", "Attack",
         [{"name": "Damage", "base_value": 5, "current_value": 5}], cost=0)]
    + [c("POOR_SLEEP", "睡眠不佳", "", "Curse")]
)
info = gates.analyse(deck_ok)
check("甲牌数（防御x4+耸肩x2+血墙+壁垒文本）", info["block"], 8)
check("抽牌数（耸肩x2+恍惚+亮剑）", info["draw"], 4)
check("引擎识别（壁垒）", info["engines"], ["壁垒"])
check("诅咒不计入可打", info["playable"], len(deck_ok) - 1)
check("占比 8/15 过线", info["block_ratio"] >= 0.45, True)
check("0费输出识别（刀刃之舞+亮剑）", info["free_output"], 2)
check("第二幕门槛全过", all(r[3] for r in gates.gate(2, info)), True)

deck_bad = (
    [c("STRIKE_IRONCLAD", "打击", "造成6点伤害。", "Attack")] * 5
    + [c("DEFEND_IRONCLAD", "防御", "获得5点格挡。")] * 2
    + [c("BASH", "痛击", "造成8点伤害。")] * 2
    + [c("PERFECTED_STRIKE", "完美打击", "造成16点伤害。", "Attack")] * 2
)
info2 = gates.analyse(deck_bad)
check("run73 型牌组甲牌占比不过", [r[3] for r in gates.gate(1, info2)][1], False)
check("第三幕缺引擎要 FAIL", [r[3] for r in gates.gate(3, info2)][-1], False)
check("没有 0 费输出", info2["free_output"], 0)
check("第二幕 0 费输出门槛要 FAIL", [r[3] for r in gates.gate(2, info2)][-1], False)
check("0 费攻击牌也算（亮剑）", gates.is_free_output(
    {"card_id": "BRIGHT_SWORD", "name": "亮剑", "card_type": "Attack", "cost": 0,
     "rules_text": "造成5点伤害。",
     "dynamic_values": [{"name": "Damage", "current_value": 5}]}), True)
check("2 费攻击牌不算", gates.is_free_output(
    {"card_id": "BASH", "name": "痛击", "card_type": "Attack", "cost": 2,
     "rules_text": "造成8点伤害。",
     "dynamic_values": [{"name": "Damage", "current_value": 8}]}), False)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
