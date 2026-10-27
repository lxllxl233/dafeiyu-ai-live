# -*- coding: utf-8 -*-
"""Verify BLOCK_POOL / LEFT_BLOCK_ON_TABLE helpers (run33 feedback)."""

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


def blk(cid, val):
    return {"card_id": cid, "dynamic_values": [{"name": "Block", "current_value": val}]}


two_shrugs = [blk("SHRUG_IT_OFF", 8), blk("SHRUG_IT_OFF", 8)]

total, parts = sts2.hand_block_pool(two_shrugs)
check("两张耸肩合计", total, 16)
check("两张耸肩明细", parts, ["耸肩无视8", "耸肩无视8"])

note = sts2.block_pool_note(two_shrugs, 20)
check("不够挡满时提示净掉", "净掉 4" in note, True)
check("够挡满时提示", "够挡满" in sts2.block_pool_note(two_shrugs, 16), True)
# run87：引擎的 Block 动态值已经是砍过脆弱的（耸肩 8→6），所以脆弱回合引擎给的是 6；
# 这里拿「引擎会给的值」验合计，同时确认再传 frail=1 也不会二次打折（旧实现会算成 9）。
two_frail = [blk("SHRUG_IT_OFF", 6), blk("SHRUG_IT_OFF", 6)]
check("脆弱回合按引擎值合计 12", sts2.hand_block_pool(two_frail, 1)[0], 12)
check("脆弱后够挡提示按 12 算", "净掉 4" in sts2.block_pool_note(two_frail, 16, 1), True)
check("脆弱不二次打折", sts2.hand_block_pool(two_shrugs, 1)[0], 16)
check("无格挡牌", "无格挡牌" in sts2.block_pool_note([{"card_id": "STRIKE_IRONCLAD"}], 9), True)

# 攻击牌上的 Block 字段不该被当成格挡牌（坚毅/铁斩波才该算）
mixed = [blk("IRON_WAVE", 5), {"card_id": "STRIKE_IRONCLAD", "dynamic_values": [{"name": "Damage", "current_value": 6}]}]
check("混排只算格挡牌", sts2.hand_block_pool(mixed)[0], 5)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
