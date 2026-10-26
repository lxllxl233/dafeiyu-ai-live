# -*- coding: utf-8 -*-
"""cmd_batch 回归测试：按 card_id 解析 + 每步等玩家回合 + 增量输出 + 异常即停。

覆盖（每一条都对应一次「模型少发一次请求」的路径）：
  1. 两张牌按 card_id 逐步重解析（手牌压缩不打错）
  2. 缺牌 → 退出码 3，不盲打
  3. end_turn 收尾 → 等新回合手牌，直接打完整快照（省掉下回合的 state）
  4. end_turn 不在最后 → 退出码 2
  5. 费用超预算 → 动手前退出码 2（不打半截回合）
  6. 目标已死 → 自动改打活敌人
  7. 最后一刀打完战斗 → end_turn 自动跳过
  8. 出牌后撞过渡快照（状态没变）→ 重读后再走下一步

用法：python tools/test_batch.py
"""
import io
import json
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
        calls.append({"action": action, **params})
        if action == "end_turn":
            state["hand"] = [
                {"card_id": "STRIKE_IRONCLAD", "energy_cost": 1, "playable": True},
                {"card_id": "STRIKE_IRONCLAD", "energy_cost": 1, "playable": True},
                {"card_id": "DEFEND_IRONCLAD", "energy_cost": 1, "playable": True},
                {"card_id": "DEFEND_IRONCLAD", "energy_cost": 1, "playable": True},
                {"card_id": "BASH", "energy_cost": 2, "playable": True},
            ]
            return {"status": "completed", "state": fake_state()}
        idx = params.get("card_index")
        if idx is not None and 0 <= idx < len(state["hand"]):
            state["hand"].pop(idx)
        return {"status": "completed", "state": fake_state()}

    sts2.get_state = fake_state
    sts2.act = fake_act
    sts2.overlay_say = lambda *a, **k: None
    sts2.log = lambda *a, **k: None
    sts2._meme_hint = lambda *a, **k: None
    os.environ["PYTHONIOENCODING"] = "utf-8"

    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            print(f"FAIL {msg}")
            ok = False

    # 1) 两张牌按 card_id 重解析
    spec = '{"actions":[{"action":"play_card","card_id":"STRIKE_IRONCLAD","target_index":0},' \
           '{"action":"play_card","card_id":"BASH","target_index":0}]}'
    sts2.cmd_batch(spec)
    check(len(calls) == 2, f"期望 2 次动作，实际 {len(calls)}")
    check(calls and calls[0].get("card_index") == 0, "第一次应解析到 index 0")
    check(len(calls) > 1 and calls[1].get("card_index") == 0, "第二次应按压缩后的手牌解析到 0")

    # 3) end_turn 收尾 → 新回合快照
    calls.clear()
    state["hand"] = [
        {"card_id": "STRIKE_IRONCLAD", "energy_cost": 1, "playable": True},
        {"card_id": "DEFEND_IRONCLAD", "energy_cost": 1, "playable": True},
    ]
    sts2.cmd_batch('{"actions":[{"action":"play_card","card_id":"STRIKE_IRONCLAD","target_index":0},'
                   '{"action":"end_turn"}]}')
    check([c["action"] for c in calls] == ["play_card", "end_turn"], f"end_turn 未执行：{calls}")
    check(len(state["hand"]) == 5, "end_turn 后应已拿到新回合 5 张手牌")

    # 4) end_turn 不在最后 → 直接拒绝
    calls.clear()
    try:
        sts2.cmd_batch('{"actions":[{"action":"end_turn"},{"action":"play_card","card_id":"BASH"}]}')
        check(False, "end_turn 不在最后时应该退出")
    except SystemExit as e:
        check(e.code == 2, f"退出码应为 2，实际 {e.code}")
        check(not calls, "被拒绝时不该发出任何动作")

    # 5) 费用超预算 → 动手前退出
    calls.clear()
    state["hand"] = [
        {"card_id": "BASH", "energy_cost": 2, "playable": True},
        {"card_id": "BASH", "energy_cost": 2, "playable": True},
    ]
    try:
        sts2.cmd_batch('{"actions":[{"action":"play_card","card_id":"BASH"},'
                       '{"action":"play_card","card_id":"BASH"}]}')
        check(False, "超预算时应该退出")
    except SystemExit as e:
        check(e.code == 2, f"超预算退出码应为 2，实际 {e.code}")
        check(not calls, "超预算时不该动手")

    # 2) 缺牌必须停，不能盲打
    state["hand"] = [{"card_id": "DEFEND_IRONCLAD", "energy_cost": 1, "playable": True}]
    calls.clear()
    try:
        sts2.cmd_batch('{"actions":[{"action":"play_card","card_id":"BASH","target_index":0}]}')
        check(False, "缺牌时应该退出")
    except SystemExit as e:
        check(e.code == 3, f"缺牌退出码应为 3，实际 {e.code}")

    # 6) 目标下标失效 → TARGET_FIX 到活敌人
    state["hand"] = [{"card_id": "BASH", "energy_cost": 2, "playable": True}]
    calls.clear()
    sts2.cmd_batch('{"actions":[{"action":"play_card","card_id":"BASH","target_index":9}]}')
    check(calls and calls[0].get("target_index") == 0, f"应改打 0 号敌人，实际 {calls}")

    # 7) 最后一刀把战斗打完 → end_turn 自动跳过，不白发 invalid_action
    calls.clear()
    reward = {"screen": "REWARD", "available_actions": ["choose_reward_card"],
              "run": {"current_hp": 53, "max_hp": 84, "act_id": 0, "floor": 2, "deck": []},
              "reward": {"rewards": [], "card_options": []}}

    def fake_act_kill(action, **params):
        calls.append({"action": action, **params})
        return {"status": "completed", "state": reward}

    sts2.act = fake_act_kill
    sts2.get_state = lambda: (_combat([{"card_id": "BASH", "energy_cost": 2, "playable": True}])
                              if not calls else reward)
    sts2.cmd_batch('{"actions":[{"action":"play_card","card_id":"BASH","target_index":0},'
                   '{"action":"end_turn"}]}')
    check([c["action"] for c in calls] == ["play_card"], f"战斗结束后不该再发 end_turn：{calls}")

    # 8) 过渡快照：play 返回的状态和动作前一模一样 → 重读，别拿旧手牌继续
    reads = {"n": 0}
    cur = {"hand": [{"card_id": "STRIKE_IRONCLAD", "energy_cost": 1, "playable": True},
                    {"card_id": "STRIKE_IRONCLAD", "energy_cost": 1, "playable": True}], "step": 0}

    def gs8():
        reads["n"] += 1
        return _combat([dict(c) for c in cur["hand"]], energy=3)

    def act8(action, **params):
        calls.append({"action": action, **params})
        if action == "play_card":
            cur["hand"].pop(params.get("card_index", 0))
            cur["step"] += 1
            if cur["step"] == 1:  # 第一刀故意回过渡快照（旧手牌）
                return {"status": "completed", "state": _combat(
                    [{"card_id": "STRIKE_IRONCLAD", "energy_cost": 1, "playable": True},
                     {"card_id": "STRIKE_IRONCLAD", "energy_cost": 1, "playable": True}], energy=3)}
        return {"status": "completed", "state": gs8()}

    sts2.get_state = gs8
    sts2.act = act8
    calls.clear()
    reads["n"] = 0
    sts2.cmd_batch('{"actions":[{"action":"play_card","card_id":"STRIKE_IRONCLAD","target_index":0},'
                   '{"action":"play_card","card_id":"STRIKE_IRONCLAD","target_index":0}]}')
    check(len(calls) == 2, f"过渡快照后应继续打完第二刀，实际 {calls}")
    check(reads["n"] >= 4, f"过渡快照应触发重读（get_state 次数 {reads['n']}）")

    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
