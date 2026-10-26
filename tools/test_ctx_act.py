# -*- coding: utf-8 -*-
"""离线自测：ctx v3 局面包 + act v2 校验/执行（不碰真游戏）。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import sts2  # noqa: E402
import ctx  # noqa: E402
import act  # noqa: E402

ok = True


def check(name, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def card(cid, cost, dmg=None, blk=None, vuln=None, draw=None, en=None, hp=None, hits=1,
         text="", target="AnyEnemy", up=False):
    dv = []
    for k, v in (("Damage", dmg), ("Block", blk), ("VulnerablePower", vuln),
                 ("Cards", draw), ("Energy", en), ("HpLoss", hp)):
        if v is not None:
            dv.append({"name": k, "current_value": v})
    if hits > 1 and not text:
        text = f"造成{dmg}点伤害{hits}次。"
    return {"card_id": cid, "name": cid, "energy_cost": cost, "upgraded": up,
            "playable": True, "requires_target": dmg is not None and target != "AllEnemies",
            "target_type": target, "dynamic_values": dv, "resolved_rules_text": text}


def enemy(eid, hp, atk, hits=1, powers=None, idx=0):
    return {"index": idx, "enemy_id": eid, "name": eid, "current_hp": hp, "max_hp": hp,
            "block": 0, "is_alive": True, "powers": powers or [],
            "intents": [{"intent_type": "ATTACK", "damage": atk, "hits": hits,
                         "total_damage": atk * hits}]}


def make_state(turn=2, hp=62, energy=3):
    raven = [{"power_id": "RAVENOUS_POWER", "name": "饥饿", "amount": 1, "is_debuff": False}]
    hand = [card("BASH", 2, dmg=8, vuln=2), card("STRIKE_IRONCLAD", 1, dmg=6),
            card("STRIKE_IRONCLAD", 1, dmg=6), card("DEFEND_IRONCLAD", 1, blk=5),
            card("BLOODLETTING", 0, en=2, hp=3), card("POMMEL_STRIKE", 1, dmg=9, draw=1)]
    enemies = [enemy("JAW_WORM", 18, 11, idx=0),
               enemy("CORPSE_SLUG", 25, 9, hits=2, powers=raven, idx=1)]
    return {
        "screen": "COMBAT", "available_actions": ["play_card", "end_turn"],
        "run": {"current_hp": hp, "max_hp": 80, "max_energy": 3, "floor": 4, "act_id": 0,
                "ascension": 10, "gold": 99,
                "deck": [{"card_id": "STRIKE_IRONCLAD", "name": "打击", "energy_cost": 1}] * 12,
                "relics": [{"relic_id": "BURNING_BLOOD", "name": "燃烧之血",
                            "description": "战斗结束后回复6点生命。"}],
                "potions": [{"index": 0, "potion_id": "FIRE_POTION", "name": "火焰药水",
                             "occupied": True, "description": "对一名敌人造成20点伤害。",
                             "can_use": True, "requires_target": True}]},
        "combat": {"turn": turn,
                   "player": {"current_hp": hp, "max_hp": 80, "energy": energy, "block": 0,
                              "powers": [{"power_id": "STRENGTH_POWER", "name": "力量", "amount": 2}]},
                   "hand": hand, "enemies": enemies},
        "agent_view": {"combat": {
            "draw_cards": [{"card_id": "STRIKE_IRONCLAD", "upgraded": False},
                           {"card_id": "STRIKE_IRONCLAD", "upgraded": False},
                           {"card_id": "DEFEND_IRONCLAD", "upgraded": False},
                           {"card_id": "ANGER", "upgraded": False}],
            "discard_cards": [{"card_id": "BASH", "upgraded": False}],
            "exhaust_cards": [{"card_id": "ANGER", "upgraded": False}]}},
    }


print("=== 1) ctx v3：字段齐、同名牌合并、牌堆、连锁 ===")
st = make_state()
d = ctx._combat(st, {})
s = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
print("  size", len(s), "bytes")
check("有抽牌堆", d["piles"]["draw"]["n"], 4)
check("抽牌堆合并", ["STRIKE_IRONCLAD", 2] in d["piles"]["draw"]["c"], True)
check("手牌同名牌合并 q=2", [x for x in d["hand"] if x["id"] == "STRIKE_IRONCLAD"][0]["q"], 2)
check("can.en 看到放血", d["can"]["en"]["by"][0]["id"], "BLOODLETTING")
check("calc.chain 杀0晕1", d["calc"].get("chain", [{}])[0].get("stun"), 1)
check("enemy 有当前意图", d["enemies"][0]["it"]["tot"], 11)
check("瘦身：<3KB", len(s) < 3000, True)

print()
print("=== 2) ctx 意图记忆：下一次意图 ===")
mem = {}
for turn, (dmg, hits) in enumerate([(11, 1), (9, 2), (11, 1), (9, 2)], 1):
    st = make_state(turn=turn)
    st["combat"]["enemies"] = [enemy("JAW_WORM", 44, dmg, hits=hits, idx=0)]
    d = ctx._combat(st, mem)
    nx = d["enemies"][0].get("nx")
    hist = d["enemies"][0].get("hist")
    print("  turn", turn, "sig", ctx._sig(st["combat"]["enemies"][0]), "nx", nx, "hist", hist)
check("第4回合推下一手 A11", nx, "A11")

print()
print("=== 3) act：合法顺序执行 + fx 账本 ===")
st = make_state()
calls = []
sts2.get_state = lambda: st
sts2.overlay_say = lambda *a, **k: None
sts2.cmd_batch = lambda spec: calls.append(json.loads(spec))
seq = [{"c": "BASH", "t": 0}, {"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}]
out = act._execute(st, seq, ["它 18，先破甲。", "一刀带走。"], {})
print("  ", {k: out.get(k) for k in ("ok", "fx", "retarget", "mid")})
check("执行成功", out["ok"], True)
check("发了一条 batch", len(calls), 1)
check("batch 跳过粗检", calls[0].get("skip_budget"), True)
check("第一刀是 BASH", calls[0]["actions"][0]["card_id"], "BASH")
check("fx 有血量", "hp" in out["fx"], True)
check("台词挂在第一手", calls[0]["actions"][0].get("thought"), "它 18，先破甲。")

print()
print("=== 4) act：费用不够整手拒，一张不打 ===")
calls.clear()
st = make_state(energy=1)
out = act._execute(st, [{"c": "BASH", "t": 0}, {"c": "STRIKE_IRONCLAD", "t": 0}], [], {})
print("  ", out.get("ok"), out.get("err"))
check("拒绝", out["ok"], False)
check("没有发 batch", len(calls), 0)

print()
print("=== 5) act：自报 fx 错了打回 ===")
st = make_state()
out = act._execute(st, [{"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}], [], {"e": 99})
print("  ", out.get("ok"), out.get("err"))
check("fx 不一致拒绝", out["ok"], False)
check("给出实际账本", out["fx"]["e"], 2)

print()
print("=== 6) act：目标死了自动改打活怪 ===")
st = make_state()
out = act._execute(st, [{"c": "BASH", "t": 0}, {"c": "BLOODLETTING"},
                        {"c": "STRIKE_IRONCLAD", "t": 0},
                        {"c": "STRIKE_IRONCLAD", "t": 0},
                        {"c": "POMMEL_STRIKE", "t": 0}], [], {})
print("  ", out.get("ok"), out.get("retarget"))
check("自动换目标", bool(out.get("retarget")), True)

print()
print("=== 7) act：老 plan 格式仍可用 ===")
st = make_state()
out = act._execute(st, [{"a": "play", "id": "STRIKE_IRONCLAD", "t": 0}, {"a": "end"}], [], {})
check("兼容 v19", out["ok"], True)

print()
print("=== 8) act：不写 end 只打半回合 ===")
st = make_state()
out = act._execute(st, [{"c": "STRIKE_IRONCLAD", "t": 0}], [], {})
check("mid 标记", out.get("mid"), 1)
check("没有 end_turn", calls[-1]["actions"][-1].get("action"), "play_card")

print()
print("=== 9) 变费：无情猛攻后大牌 0 费，act 放行 ===")
st = make_state()
st["combat"]["hand"] = [card("UNRELENTING", 2, dmg=14), card("BASH", 2, dmg=8),
                       card("DEFEND_IRONCLAD", 1, blk=5)]
st["combat"]["player"]["energy"] = 3
d = ctx._combat(st, {})
check("ctx 标出 can.cost", d.get("can", {}).get("cost", [{}])[0].get("id"), "UNRELENTING")
calls.clear()
out = act._execute(st, [{"c": "UNRELENTING", "t": 0}, {"c": "BASH", "t": 0},
                        {"c": "DEFEND_IRONCLAD"}, {"end": 1}], [], {})
print("  ", out.get("ok"), out.get("err"))
check("2+0+1 费可执行", out["ok"], True)
check("发了 3 张牌 + end", len(calls[0]["actions"]), 4)

print()
print("=== 10) 药水插进牌序 ===")
st = make_state()
st["combat"]["hand"] = [card("STRIKE_IRONCLAD", 1, dmg=6)]
st["combat"]["player"]["energy"] = 3
calls.clear()
out = act._execute(st, [{"c": "STRIKE_IRONCLAD", "t": 1}, {"p": 0, "t": 1}, {"end": 1}], [], {})
print("  ", out.get("ok"), out.get("fx"))
check("药水执行", out["ok"], True)
check("第二步是药水", calls[0]["actions"][1]["action"], "use_potion")
check("药水目标 1", calls[0]["actions"][1].get("target_index"), 1)

print()
print("=== 11) fx 全对时放行 ===")
st = make_state()
out = act._execute(st, [{"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}], [], {})
fx = out["fx"]
out2 = act._execute(st, [{"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}], [],
                    {"e": fx["e"], "hp": fx["hp"], "take": fx["take"]})
print("  ", out2.get("ok"), out2.get("err"))
check("自报账正确", out2["ok"], True)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
