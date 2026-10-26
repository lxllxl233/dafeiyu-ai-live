# -*- coding: utf-8 -*-
"""P0-3 执行安全回归：end 严格收尾、防双 end、敌人 current_hp / 原始下标。

全部离线，不调用游戏 API。用法：
    python tools/test_execution_safety.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("STS2_DECISION_LOG", "0")

import act  # noqa: E402
import sim  # noqa: E402
import sts2  # noqa: E402

ok = True


def check(name, got, want=True):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def card(cid="BASH", cost=1, playable=True):
    return {"card_id": cid, "name": cid, "energy_cost": cost,
            "playable": playable, "target_type": "AnyEnemy"}


def enemy(idx, hp, max_hp=30, alive=True):
    return {"index": idx, "enemy_id": f"E{idx}", "name": f"E{idx}",
            "current_hp": hp, "hp": max_hp, "max_hp": max_hp,
            "is_alive": alive, "block": 0, "powers": [], "intents": []}


def combat(turn=3, hand=None, enemies=None, energy=3):
    return {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn"],
        "run": {"current_hp": 50, "max_hp": 80, "max_energy": 3, "potions": []},
        "combat": {"turn": turn,
                   "player": {"current_hp": 50, "max_hp": 80, "energy": energy,
                              "block": 0, "powers": []},
                   "hand": hand if hand is not None else [card()],
                   "enemies": enemies if enemies is not None else [enemy(1, 20)]},
    }


def test_validate_end_last():
    st = combat()
    good, err, _sm, _steps = sim.validate(st, [{"end": 1}, {"c": "BASH"}])
    check("end 后还有动作必须拒绝", good, False)
    check("end 后动作错误可读", "end" in err and "最后" in err)
    good, err, _sm, _steps = sim.validate(st, [{"end": 1}, {"end": 1}])
    check("重复 end 必须拒绝", good, False)
    good, _err, sm, steps = sim.validate(st, [{"c": "BASH", "t": 1}, {"end": 1}])
    check("正常 end 收尾通过", good, True)
    check("end 是最后一步", bool(steps and steps[-1].get("k") == "end"), True)
    check("正常出牌后目标受伤", sm.by_index(1) is not None and sm.enemies[0]["hp"] < 20, True)


def test_alive_original_index():
    st = combat(enemies=[
        enemy(0, 0, 30, False),
        enemy(1, 12, 30, True),
        enemy(2, 4, 30, True),
    ])
    alive = sts2._alive_enemies(st)
    check("尸体不计入活怪", len(alive), 2)
    check("最低血用 current_hp 且返回原始 index", sts2._auto_target_index(st), 2)

    # 没有显式 index 时，返回原 enemies 列表下标，而不是 alive 列表里的位置。
    st2 = combat(enemies=[
        {"enemy_id": "DEAD", "current_hp": 0, "hp": 30, "is_alive": False},
        {"enemy_id": "A", "current_hp": 9, "hp": 30, "is_alive": True},
        {"enemy_id": "B", "current_hp": 5, "hp": 30, "is_alive": True},
    ])
    check("无 index 时返回原列表下标", sts2._auto_target_index(st2), 2)


def test_stale_end():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    old = act.TURN_MEMO
    try:
        act.TURN_MEMO = path
        act._write_memo(2, False, 1)
        st = combat(turn=3, hand=[card()])
        err = act._stale_end(st, [{"end": 1}])
        check("刚 end 过的新回合有牌，纯 end 被拒", bool(err) and "上一手已经 end_turn" in err)

        act._write_memo(3, False, 3)
        err = act._stale_end(combat(turn=3, hand=[card()]), [{"end": 1}])
        check("同一 turn 重复 end 被拒", bool(err) and "已经 end 过" in err)

        act._write_memo(1, False, 0)
        err = act._stale_end(combat(turn=2, hand=[]), [{"end": 1}])
        check("新回合没有可打牌时允许 end", err, None)

        act._write_memo(5, True)
        err = act._stale_end(combat(turn=6, hand=[card()]), [{"end": 1}])
        check("mid 后换回合，纯 end 被拒", bool(err) and "回合已切换" in err)

        act._write_memo(2, False)
        err = act._stale_end(combat(turn=2), [{"end": 1}, {"end": 1}])
        check("seq 双 end 被拒", bool(err) and "2 次 end" in err)
    finally:
        act.TURN_MEMO = old
        try:
            os.remove(path)
        except OSError:
            pass


def test_cmd_batch():
    st = combat(hand=[card()], enemies=[
        enemy(0, 0, 30, False),
        enemy(1, 12, 30, True),
        enemy(2, 4, 30, True),
    ])
    calls = []

    def fake_act(action, **params):
        calls.append({"action": action, **params})
        return {"status": "completed", "state": st}

    old = (sts2.get_state, sts2.act, sts2.overlay_say, sts2.log, sts2._meme_hint, sts2._fingerprint)
    try:
        sts2.get_state = lambda: st
        sts2.act = fake_act
        sts2.overlay_say = lambda *a, **k: None
        sts2.log = lambda *a, **k: None
        sts2._meme_hint = lambda *a, **k: None
        sts2._fingerprint = lambda s: "same"
        sts2.cmd_batch(json.dumps({
            "actions": [{"action": "play_card", "card_id": "BASH", "target_index": 0}],
            "skip_budget": True,
        }))
        check("目标 0 已死 → 改打 original index 2",
              calls and calls[0].get("target_index"), 2)

        calls.clear()
        try:
            sts2.cmd_batch(json.dumps({"actions": [
                {"action": "end_turn"}, {"action": "end_turn"}]}))
            check("重复 end 应退出", False)
        except SystemExit as e:
            check("重复 end 退出码 2", e.code, 2)
            check("重复 end 不发动作", calls, [])

        calls.clear()
        try:
            sts2.cmd_batch(json.dumps({"actions": [
                {"action": "end_turn"},
                {"action": "play_card", "card_id": "BASH"}]}))
            check("end 后接动作应退出", False)
        except SystemExit as e:
            check("end 后接动作退出码 2", e.code, 2)
            check("end 后接动作不发动作", calls, [])
    finally:
        (sts2.get_state, sts2.act, sts2.overlay_say, sts2.log,
         sts2._meme_hint, sts2._fingerprint) = old


def test_execute_double_end():
    called = []
    old = sts2.cmd_batch
    try:
        sts2.cmd_batch = lambda spec: called.append(spec)
        out = act._execute(combat(), [{"end": 1}, {"end": 1}], [], {})
        check("act 双 end 整手拒绝", out.get("ok"), False)
        check("act 双 end 一张不发", called, [])
        check("act 双 end 错误可读", "end" in (out.get("err") or ""))
    finally:
        sts2.cmd_batch = old


def main():
    test_validate_end_last()
    test_alive_original_index()
    test_stale_end()
    test_execute_double_end()
    test_cmd_batch()
    print("ALL OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
