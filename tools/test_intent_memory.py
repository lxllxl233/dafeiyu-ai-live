# -*- coding: utf-8 -*-
"""离线验证意图记忆：同一敌人的意图循环能被记录并推测出下一次。"""

import json
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


def enemy(sig_type, dmg=None, hits=1):
    it = {"intent_type": sig_type, "hits": hits}
    if dmg is not None:
        it["damage"] = dmg
    return {"enemy_id": "TOADPOLE", "current_hp": 20, "intents": [it]}


mem_path = sts2.INTENT_MEM_PATH
if os.path.exists(mem_path):
    os.replace(mem_path, mem_path + ".bak")
try:
    # 第一观察：只有一条记录，不给推测
    line1 = sts2._intent_cycle_lines([enemy("Attack", 8)], ["play_card"])
    check("首次观察只有记录", len(line1), 0)

    # 第二次：Buff
    line2 = sts2._intent_cycle_lines([enemy("Buff")], ["play_card"])
    check("第二次有历史", "INTENT_MEM" in line2[0], True)

    # 第三次回到 Attack 8 → 应该推测「上次 Attack 8 后面接的是 Buff」
    line3 = sts2._intent_cycle_lines([enemy("Attack", 8)], ["play_card"])
    check("能推测下一次", "上次出现后接的是：Buff" in line3[0], True)

    # 过渡态不记录
    before = json.load(open(mem_path, encoding="utf-8"))
    sts2._intent_cycle_lines([enemy("Debuff")], [])
    after = json.load(open(mem_path, encoding="utf-8"))
    check("过渡态不写入历史", before == after, True)

    # 多段意图的签名
    check("多段签名", sts2._intent_sig(enemy("Attack", 4, 3)), "Attack 4x3")
finally:
    if os.path.exists(mem_path):
        os.remove(mem_path)
    if os.path.exists(mem_path + ".bak"):
        os.replace(mem_path + ".bak", mem_path)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
