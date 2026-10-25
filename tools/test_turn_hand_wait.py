# -*- coding: utf-8 -*-
"""验证 end_turn 后的过渡快照不会骗人：wait_new_turn_hand 会等到手牌抽完。"""

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


# 模拟：第 1、2 次读到「还没抽牌」（手牌 1 张），第 3 次起才是完整手牌（3 张）
seq = [
    {"screen": "COMBAT", "available_actions": ["play_card"], "combat": {"hand": [{"card_id": "BASH"}]}},
    {"screen": "COMBAT", "available_actions": ["play_card"], "combat": {"hand": [{"card_id": "BASH"}]}},
    {"screen": "COMBAT", "available_actions": ["play_card"], "combat": {"hand": [{"card_id": "BASH"}, {"card_id": "STRIKE_IRONCLAD"}, {"card_id": "DEFEND_IRONCLAD"}]}},
    {"screen": "COMBAT", "available_actions": ["play_card"], "combat": {"hand": [{"card_id": "BASH"}, {"card_id": "STRIKE_IRONCLAD"}, {"card_id": "DEFEND_IRONCLAD"}]}},
    {"screen": "COMBAT", "available_actions": ["play_card"], "combat": {"hand": [{"card_id": "BASH"}, {"card_id": "STRIKE_IRONCLAD"}, {"card_id": "DEFEND_IRONCLAD"}]}},
]
idx = {"i": 0}


def fake_state():
    i = min(idx["i"], len(seq) - 1)
    idx["i"] += 1
    return seq[i]


sts2.get_state = fake_state
sts2.time.sleep = lambda *_a, **_k: None
out = sts2.wait_new_turn_hand(prev_hand_len=1, timeout=2.5)
check("等到抽完牌（3 张）", len(out["combat"]["hand"]), 3)

# 战斗结束时直接返回，不卡等待
idx["i"] = 0
sts2.get_state = lambda: {"screen": "REWARD", "combat": {}, "available_actions": ["claim_reward"]}
out = sts2.wait_new_turn_hand(prev_hand_len=2)
check("离开战斗直接返回", out["screen"], "REWARD")

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
