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


def c(cid, name, text="", ctype="Skill", dynamic=None):
    return {"card_id": cid, "name": name, "card_type": ctype,
            "rules_text": text, "resolved_rules_text": text,
            "dynamic_values": dynamic or []}


deck_ok = (
    [c("STRIKE_IRONCLAD", "打击", "造成6点伤害。", "Attack")] * 3
    + [c("DEFEND_IRONCLAD", "防御", "获得5点格挡。")] * 4
    + [c("SHRUG_IT_OFF", "耸肩无视", "获得8点格挡。 抽1张牌。")] * 2
    + [c("BLOOD_WALL", "血墙", "失去2点生命。 获得16点格挡。")]
    + [c("BATTLE_TRANCE", "战斗恍惚", "抽3张牌。")]
    + [c("BARRICADE", "壁垒", "格挡不再在你的回合开始时消失。", "Power")]
    + [c("POOR_SLEEP", "睡眠不佳", "", "Curse")]
)
info = gates.analyse(deck_ok)
check("甲牌数（防御x4+耸肩x2+血墙+壁垒文本）", info["block"], 8)
check("抽牌数（耸肩x2+恍惚）", info["draw"], 3)
check("引擎识别（壁垒）", info["engines"], ["壁垒"])
check("诅咒不计入可打", info["playable"], len(deck_ok) - 1)
check("占比 8/12 ≈ 0.667", info["block_ratio"] >= 0.45, True)
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

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
