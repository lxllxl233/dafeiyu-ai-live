# -*- coding: utf-8 -*-
"""对比「旧协议（每刀一条命令）」与「v18 协议（一回合一条 batch）」的回显体积。

不碰真游戏：用假 state 跑 sts2 的命令函数，只统计 stdout 字符数。
用法：python tools/compare_turn_protocol.py
"""
import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import sts2  # noqa: E402

HAND = [
    {"card_id": "BASH", "energy_cost": 2, "playable": True, "target_type": "AnyEnemy", "name": "痛击",
     "description": "造成8点伤害。给予2层易伤。"},
    {"card_id": "STRIKE_IRONCLAD", "energy_cost": 1, "playable": True, "target_type": "AnyEnemy", "name": "打击"},
    {"card_id": "STRIKE_IRONCLAD", "energy_cost": 1, "playable": True, "target_type": "AnyEnemy", "name": "打击"},
    {"card_id": "DEFEND_IRONCLAD", "energy_cost": 1, "playable": True, "target_type": "Self", "name": "防御"},
    {"card_id": "DEFEND_IRONCLAD", "energy_cost": 1, "playable": True, "target_type": "Self", "name": "防御"},
]
ENEMY = {"enemy_id": "JAW_WORM", "name": "Jaw Worm", "hp": 44, "max_hp": 44, "block": 0,
         "intent": {"type": "Attack", "damage": 11}}


def combat(hand=None, energy=3, turn=1, enemy_hp=44):
    return {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn"],
        "run": {"current_hp": 62, "max_hp": 84, "act_id": 0, "floor": 3, "deck": [{}] * 12,
                "max_energy": 3, "potions": [], "relics": []},
        "combat": {"turn_number": turn, "player": {"energy": energy, "block": 0},
                   "hand": [dict(c) for c in (hand or HAND)],
                   "enemies": [dict(ENEMY, hp=enemy_hp)]},
    }


def fake_run(fn, *a, **k):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*a, **k)
    return buf.getvalue()


def main():
    sts2.overlay_say = lambda *a, **k: None
    sts2.log = lambda *a, **k: None
    sts2._meme_hint = lambda *a, **k: None

    # ---------- 旧协议：state → play×4 → end_turn ----------
    old_state = {"hand": [dict(c) for c in HAND]}
    calls = []

    def old_get_state():
        return combat(hand=old_state["hand"], energy=max(0, 3 - len(calls)),
                      enemy_hp=44 - 6 * len(calls))

    def old_act(action, **params):
        calls.append(params)
        if action == "end_turn":
            old_state["hand"] = [dict(c) for c in HAND]
            return {"status": "completed", "state": combat(hand=HAND, turn=2)}
        idx = params.get("card_index")
        if idx is not None:
            old_state["hand"].pop(idx)
        return {"status": "completed", "state": old_get_state()}

    sts2.get_state = old_get_state
    sts2.act = old_act

    out_old = fake_run(sts2.cmd_state)
    for cid in ("STRIKE_IRONCLAD", "STRIKE_IRONCLAD", "DEFEND_IRONCLAD"):
        out_old += fake_run(sts2.cmd_play, cid, 0)
    out_old += fake_run(sts2.cmd_do, "end_turn")

    # ---------- v18 协议：一条 batch（含 end_turn） ----------
    new_state = {"hand": [dict(c) for c in HAND]}
    calls2 = []

    def new_get_state():
        return combat(hand=new_state["hand"], energy=max(0, 3 - len(calls2) * 1),
                      enemy_hp=44 - 6 * len(calls2), turn=1)

    def new_act(action, **params):
        calls2.append(params)
        if action == "end_turn":
            new_state["hand"] = [dict(c) for c in HAND]
            return {"status": "completed", "state": combat(hand=HAND, turn=2)}
        idx = params.get("card_index")
        if idx is not None and 0 <= idx < len(new_state["hand"]):
            new_state["hand"].pop(idx)
        return {"status": "completed", "state": new_get_state()}

    sts2.get_state = new_get_state
    sts2.act = new_act
    spec = ('{"actions":[{"action":"play_card","card_id":"STRIKE_IRONCLAD","target_index":0},'
            '{"action":"play_card","card_id":"STRIKE_IRONCLAD","target_index":0},'
            '{"action":"play_card","card_id":"DEFEND_IRONCLAD","target_index":0},'
            '{"action":"end_turn"}]}')
    out_new = fake_run(sts2.cmd_batch, spec)

    old_req = 1 + 3 + 1   # state + 3 张牌 + end_turn
    new_req = 1           # 一条 batch 打完整个回合
    print(f"旧协议 请求 {old_req} 次  回显 {len(out_old):5,} 字符")
    print(f"v18    请求 {new_req} 次  回显 {len(out_new):5,} 字符")
    print(f"回显 {len(out_old) / max(1, len(out_new)):.1f}× 省，请求 {old_req / new_req:.0f}× 省")
    return 0


if __name__ == "__main__":
    sys.exit(main())
