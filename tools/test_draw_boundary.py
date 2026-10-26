# -*- coding: utf-8 -*-
"""P1-3 离线回归：抽牌边界、mid 协议、nxdraw 不确定性。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

os.environ["STS2_DECISION_LOG"] = "0"

import sts2  # noqa: E402
import ctx  # noqa: E402
import act  # noqa: E402
import sim  # noqa: E402

ok = True


def check(name, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def card(cid, cost, dmg=None, blk=None, vuln=None, draw=None, text=""):
    dv = []
    for k, v in (("Damage", dmg), ("Block", blk), ("VulnerablePower", vuln), ("Cards", draw)):
        if v is not None:
            dv.append({"name": k, "current_value": v})
    return {"card_id": cid, "name": cid, "energy_cost": cost, "upgraded": False,
            "playable": True, "requires_target": dmg is not None,
            "target_type": "AnyEnemy" if dmg is not None else "Self",
            "dynamic_values": dv, "resolved_rules_text": text}


def make_state(hand=None):
    hand = hand or [
        card("POMMEL_STRIKE", 1, dmg=9, draw=1),
        card("BASH", 2, dmg=8, vuln=2),
        card("DEFEND_IRONCLAD", 1, blk=5),
    ]
    return {
        "screen": "COMBAT", "available_actions": ["play_card", "end_turn"],
        "run": {"current_hp": 50, "max_hp": 80, "max_energy": 3, "floor": 4, "act_id": 0,
                "ascension": 10, "gold": 0, "deck": [], "relics": [], "potions": []},
        "combat": {"turn": 3,
                   "player": {"current_hp": 50, "max_hp": 80, "energy": 3, "block": 0,
                              "powers": []},
                   "hand": hand,
                   "enemies": [{"index": 0, "enemy_id": "JAW_WORM", "name": "JAW_WORM",
                                "current_hp": 40, "max_hp": 40, "block": 0, "is_alive": True,
                                "powers": [],
                                "intents": [{"intent_type": "ATTACK", "damage": 8, "hits": 1,
                                             "total_damage": 8}]}]},
        "agent_view": {"combat": {
            "draw_cards": [{"card_id": "ANGER", "upgraded": False}],
            "discard_cards": [{"card_id": "DEFEND_IRONCLAD", "upgraded": False}],
            "exhaust_cards": []}},
    }


def call_execute(st, seq):
    calls = []
    sts2.get_state = lambda: st
    sts2.overlay_say = lambda *a, **k: None
    sts2.cmd_batch = lambda spec: calls.append(json.loads(spec))
    out = act._execute(st, seq, [], {})
    return out, calls


print("=== 1) 抽牌后写未知牌：validate 明确拒绝 ===")
st = make_state()
valid, err, sm, steps = sim.validate(
    st, [{"c": "POMMEL_STRIKE", "t": 0}, {"c": "ANGER"}, {"end": 1}])
print("  ", valid, err)
check("validate 拒绝", valid, False)
check("错误标抽牌边界", err.startswith("抽牌边界"), True)
check("错误说明抽牌前手牌", "抽牌前手牌" in err, True)
check("只保留抽牌这一步账", [s.get("k") for s in steps], ["c"])
check("抽牌计数为 1", sm.draws, 1)

print()
print("=== 2) act：抽牌边界整手拒绝，一张不打 ===")
out, calls = call_execute(st, [{"c": "POMMEL_STRIKE", "t": 0},
                               {"c": "ANGER"}, {"end": 1}])
print("  ", {k: out.get(k) for k in ("ok", "next", "hint")})
check("执行失败", out["ok"], False)
check("提示 split_at_draw", out.get("next"), "split_at_draw")
check("提示先读 ctx", "先读 ctx" in (out.get("hint") or ""), True)
check("没有发 batch", calls, [])

print()
print("=== 3) 抽牌后打抽牌前已有的牌：允许 ===")
out, calls = call_execute(st, [{"c": "POMMEL_STRIKE", "t": 0},
                               {"c": "BASH", "t": 0}, {"end": 1}])
print("  ", out.get("ok"), out.get("err"))
check("已知牌仍可顺序打", out["ok"], True)
check("发了 3 个动作", len(calls[0]["actions"]), 3)

print()
print("=== 4) 只打抽牌牌不写 end：返回 mid + 重读提示 ===")
out, calls = call_execute(st, [{"c": "POMMEL_STRIKE", "t": 0}])
print("  ", {k: out.get(k) for k in ("ok", "mid", "next", "mid_reason")})
check("抽牌动作执行", out["ok"], True)
check("mid 标记", out.get("mid"), 1)
check("next 是 read_ctx", out.get("next"), "read_ctx")
check("提示已抽牌", "已抽 1 张" in (out.get("mid_reason") or ""), True)
check("没有 end_turn", calls[0]["actions"][-1].get("action"), "play_card")

print()
print("=== 5) 抽牌后直接 end：合法，但账本标抽牌未知 ===")
out, calls = call_execute(st, [{"c": "POMMEL_STRIKE", "t": 0}, {"end": 1}])
check("抽牌后结束合法", out["ok"], True)
check("最后是 end_turn", calls[0]["actions"][-1].get("action"), "end_turn")
check("fx 标 draw_unknown", out["fx"].get("draw_unknown"), 1)
check("抽牌回合不强验 take",
      act.fx_error({"take": 999}, {"take": 0, "draw_unknown": 1}), None)
check("抽牌回合仍验能量",
      bool(act.fx_error({"e": 99}, {"e": 0, "draw_unknown": 1})), True)

print()
print("=== 6) nxdraw 明确标不确定 ===")
st2 = {"agent_view": {"combat": {
    "draw_cards": [{"card_id": "STRIKE_IRONCLAD"}] * 2,
    "discard_cards": [{"card_id": "DEFEND_IRONCLAD"}] * 5}}}
nd = ctx._nx_draw(st2)
print("  ", nd)
check("抽牌堆不足仍预测 5 张", nd.get("n"), 5)
check("标 mixed", nd.get("mix"), True)
check("标 uncertain", nd.get("uncertain"), 1)
check("why 说明洗牌未知", "洗牌顺序未知" in (nd.get("why") or ""), True)
check("两打击 + 三防御期望", (nd.get("dmg"), nd.get("blk")), (12, 15))

st3 = {"agent_view": {"combat": {
    "draw_cards": [{"card_id": "STRIKE_IRONCLAD"}] * 5,
    "discard_cards": []}}}
nd = ctx._nx_draw(st3)
print("  ", nd)
check("抽牌堆够 5 张", nd.get("n"), 5)
check("不标 mixed", nd.get("mix"), False)
check("仍标 uncertain", nd.get("uncertain"), 1)
check("why 说明顺序未知", "顺序未知" in (nd.get("why") or ""), True)
check("5 打击期望", nd.get("dmg"), 30)

st4 = {"agent_view": {"combat": {"draw_cards": [], "discard_cards": []}}}
check("两堆都空返回空", ctx._nx_draw(st4), {})

print()
print("=== 7) 抽牌堆不足且弃牌堆为空：标 short ===")
st5 = {"agent_view": {"combat": {
    "draw_cards": [{"card_id": "STRIKE_IRONCLAD"}] * 2,
    "discard_cards": []}}}
nd = ctx._nx_draw(st5)
print("  ", nd)
check("只能抽 2 张", nd.get("n"), 2)
check("标 short", nd.get("short"), 1)
check("标 uncertain", nd.get("uncertain"), 1)
check("没弃牌不标 mix", nd.get("mix"), False)
check("why 说弃牌堆为空", "弃牌堆为空" in (nd.get("why") or ""), True)

print()
print("=== 8) ctx.calc.nxdraw 透传 uncertain ===")
d = ctx._combat(make_state(), {})
print("  ", d["calc"].get("nxdraw"))
check("calc 有 nxdraw", bool(d["calc"].get("nxdraw")), True)
check("calc.nxdraw.uncertain", d["calc"]["nxdraw"].get("uncertain"), 1)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
