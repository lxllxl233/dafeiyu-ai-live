# -*- coding: utf-8 -*-
"""cmd_batch 回归测试：按 card_id 解析 + 每步等玩家回合 + 增量输出 + 异常即停。

用法：python tools/test_batch.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import sts2  # noqa: E402


def _combat(hand, energy=3, enemy_hp=68, hp=53):
    return {
        "screen": "COMBAT",
        "run_id": "run_test",
        "available_actions": ["play_card", "end_turn"],
        "run": {"current_hp": hp, "max_hp": 84, "act_id": 0, "floor": 1, "deck": []},
        "combat": {
            "turn_number": 1,
            "player": {"energy": energy, "block": 0},
            "hand": hand,
            "enemies": [{"enemy_id": "WRIGGLER", "hp": enemy_hp, "max_hp": enemy_hp,
                         "block": 0, "intent": {"type": "Attack", "damage": 9}}],
        },
    }


def main():
    calls = []
    state = {"hand": [
        {"card_id": "STRIKE_IRONCLAD", "energy_cost": 1, "playable": True, "target_type": "AnyEnemy"},
        {"card_id": "BASH", "energy_cost": 2, "playable": True, "target_type": "AnyEnemy"},
        {"card_id": "DEFEND_IRONCLAD", "energy_cost": 1, "playable": True, "target_type": "Self"},
    ]}

    def fake_state():
        return _combat([dict(c) for c in state["hand"]], energy=3 - len(calls), enemy_hp=68 - 6 * len(calls))

    def fake_act(action, **params):
        calls.append(params)
        # 打出后手牌压缩
        idx = params.get("card_index")
        if idx is not None and 0 <= idx < len(state["hand"]):
            state["hand"].pop(idx)
        return {"status": "completed", "state": fake_state()}

    sts2.get_state = fake_state
    sts2.act = fake_act
    sts2.overlay_say = lambda *a, **k: None
    sts2.log = lambda *a, **k: None

    os.environ["PYTHONIOENCODING"] = "utf-8"
    spec = '{"actions":[{"action":"play_card","card_id":"STRIKE_IRONCLAD","target_index":0},' \
           '{"action":"play_card","card_id":"BASH","target_index":0}]}'
    try:
        sts2.cmd_batch(spec)
    except SystemExit as e:
        print("unexpected exit", e.code)
        return 1

    ok = True
    if len(calls) != 2:
        print(f"FAIL 期望 2 次动作，实际 {len(calls)}")
        ok = False
    if calls and calls[0].get("card_index") != 0:
        print("FAIL 第一次应解析到 index 0")
        ok = False
    if len(calls) > 1 and calls[1].get("card_index") != 0:
        # 打掉 STRIKE 后 BASH 顶到 0
        print(f"FAIL 第二次应按压缩后的手牌解析，实际 {calls[1].get('card_index')}")
        ok = False

    # 缺牌必须停，不能盲打
    state["hand"] = [{"card_id": "DEFEND_IRONCLAD", "energy_cost": 1, "playable": True}]
    calls.clear()
    try:
        sts2.cmd_batch('{"actions":[{"action":"play_card","card_id":"BASH","target_index":0}]}')
        print("FAIL 缺牌时应该退出")
        ok = False
    except SystemExit as e:
        if e.code != 3:
            print(f"FAIL 缺牌退出码应为 3，实际 {e.code}")
            ok = False

    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
