# -*- coding: utf-8 -*-
"""P1-1 离线战斗数学回归：入伤口径 / Frail / 力量易伤顺序 / 荆棘 / 状态税。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import sts2  # noqa: E402
import sim  # noqa: E402
from tools import ctx  # noqa: E402

os.environ.setdefault("STS2_DECISION_LOG", "0")

ok = True


def check(name, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def card(cid, cost, dmg=None, blk=None, draw=None, playable=True, text="", target="AnyEnemy"):
    dv = []
    for k, v in (("Damage", dmg), ("Block", blk), ("Cards", draw)):
        if v is not None:
            dv.append({"name": k, "current_value": v})
    return {
        "card_id": cid, "name": cid, "energy_cost": cost, "playable": playable,
        "requires_target": dmg is not None and target != "AllEnemies",
        "target_type": target, "dynamic_values": dv,
        "resolved_rules_text": text,
    }


def enemy(hp=50, atk=0, hits=1, total=None, powers=None, idx=0):
    intent = {"intent_type": "ATTACK", "damage": atk, "hits": hits}
    if total is not None:
        intent["total_damage"] = total
    elif atk:
        intent["total_damage"] = atk * hits
    return {
        "index": idx, "enemy_id": "A", "name": "A", "current_hp": hp, "max_hp": hp,
        "block": 0, "is_alive": True, "powers": powers or [],
        "intents": [intent] if atk else [],
    }


def state(hand, enemies, hp=50, energy=3, block=0, player_powers=None):
    return {
        "screen": "COMBAT", "available_actions": ["play_card", "end_turn"],
        "run": {"current_hp": hp, "max_hp": 80, "max_energy": 3, "potions": []},
        "combat": {
            "turn": 1,
            "player": {"current_hp": hp, "max_hp": 80, "energy": energy,
                       "block": block, "powers": player_powers or []},
            "hand": hand, "enemies": enemies,
        },
    }


VULN1 = [{"name": "Vulnerable", "amount": 1}]
FRAIL1 = [{"name": "Frail", "amount": 1}]

print("=== 1) total_damage 是最终值，玩家易伤不能再乘一次 ===")
e = enemy(atk=5, hits=4, total=20)
st = state([], [e], player_powers=VULN1)
check("Sim 入伤 20", sim.Sim.from_state(st).incoming(), 20)
check("sts2.combat_incoming 20", sts2.combat_incoming(st), 20)
check("ctx it.tot 不再乘 1.5", ctx._combat(st, {})["enemies"][0]["it"].get("tot"), 20)

print()
print("=== 2) 缺 total_damage 才补敌力量/虚弱/玩家易伤 ===")
# (5 基础 + 2 力量)=7；虚弱 5；易伤 7；4 段 = 28
e = enemy(atk=5, hits=4, total=None,
          powers=[{"name": "Strength", "amount": 2}, {"name": "Weak", "amount": 1}])
# 去掉 helper 自动补的 total_damage，模拟旧接口/缺字段
e["intents"][0].pop("total_damage", None)
st = state([], [e], player_powers=VULN1)
check("Sim 回落 28", sim.Sim.from_state(st).incoming(), 28)
check("sts2 回落 28", sts2.combat_incoming(st), 28)
d = ctx._combat(st, {})
check("ctx it.tot 28", d["enemies"][0]["it"].get("tot"), 28)
check("ctx it.d 7", d["enemies"][0]["it"].get("d"), 7)

print()
print("=== 3) 力量先于易伤：每段 (6+2)*1.5=12，两段 24 ===")
sm = sim.Sim()
sm.str_bonus = 2
e = sim.enemy_state(enemy(hp=100), 0)
e["vuln"] = 1
check("两段力量+易伤", sm.hurt_enemy(e, 6, 2), 24)

# 走真实出牌路径，确认不是只有 helper 对
st = state([card("STRIKE_IRONCLAD", 1, dmg=6)], [enemy(hp=100)])
sm = sim.Sim.from_state(st)
sm.str_bonus = 2
sm.enemies[0]["vuln"] = 1
step = sm.play(sm.hand[0], 0)
check("出牌路径 (6+2)*1.5=12", step.get("d"), 12)

print()
print("=== 4) Frail 只砍卡牌格挡，max_block 也同步 ===")
st = state([card("DEFEND_IRONCLAD", 1, blk=5)], [enemy(atk=10)], player_powers=FRAIL1)
sm = sim.Sim.from_state(st)
check("max_block 5→3", sim.max_block(st), 3)
sm.play(sm.hand[0], None)
res = sm.end_turn()
check("Frail 后净掉 7", res["taken"], 7)
check("Frail 后血 43", res["hp_after"], 43)

print()
print("=== 5) 荆棘反伤进 taken，且不重复扣血 ===")
st = state([card("STRIKE_IRONCLAD", 1, dmg=6)],
           [enemy(hp=30, powers=[{"name": "Thorns", "amount": 2}])])
sm = sim.Sim.from_state(st)
sm.play(sm.hand[0], 0)
res = sm.end_turn()
check("荆棘后血 48", sm.hp, 48)
check("taken 含荆棘 2", res["taken"], 2)
check("hp_after 不二次扣", res["hp_after"], 48)
check("thorns 字段 2", res["thorns"], 2)

print()
print("=== 6) 状态税：按当前卡库，可格挡/无视格挡分开 ===")
check("Slimed 无税", sim.STATUS_TAX.get("SLIMED"), 0)
check("Wound 无税", sim.STATUS_TAX.get("WOUND"), 0)


def tax_of(cid, block=0, extra=None, playable=False):
    hand = [card(cid, -1, playable=playable)] + (extra or [])
    return sim.Sim.from_state(state(hand, [enemy()], block=block)).end_turn()


res = tax_of("INFECTION", block=5)
check("感染 3 可被 5 甲吃掉", (res["taken"], res["tax_dmg"]), (0, 3))
res = tax_of("BURN")
check("燃烧 2", (res["taken"], res["tax_dmg"]), (2, 2))
res = tax_of("BECKON", block=10)
check("呼唤 6 无视格挡", (res["taken"], res["tax_hp"]), (6, 6))
res = tax_of("BAD_LUCK")
check("厄运 13 无视格挡", (res["taken"], res["tax_hp"]), (13, 13))
res = tax_of("DECAY")
check("衰败 2 可格挡", (res["taken"], res["tax_dmg"]), (2, 2))
res = tax_of("DISINTEGRATION")
check("崩解 6 可格挡", (res["taken"], res["tax_dmg"]), (6, 6))
res = tax_of("REGRET", extra=[card("STRIKE_IRONCLAD", 1, dmg=6), card("DEFEND_IRONCLAD", 1, blk=5)])
check("悔恨按 3 张手牌扣 3", (res["taken"], res["tax_hp"]), (3, 3))
res = tax_of("TOXIC")
check("剧毒 5 可格挡", (res["taken"], res["tax_dmg"]), (5, 5))
res = tax_of("SLIMED", playable=True)
check("黏液出不出都不掉血", res["taken"], 0)

print()
print("=== 7) ctx.calc：税进 net/full，Slimed 不再凭空加税 ===")
st = state([card("DEFEND_IRONCLAD", 1, blk=5), card("INFECTION", -1)],
           [enemy(atk=5, hits=2, total=10)])
d = ctx._combat(st, {})
calc = d["calc"]
check("calc.inc 10", calc.get("inc"), 10)
check("calc.blk 5", calc.get("blk"), 5)
check("calc.tax 3", calc.get("tax"), 3)
check("calc.net 8", calc.get("net"), 8)
check("calc.full false", calc.get("full"), False)
check("calc.tax_dmg 3", calc.get("tax_dmg"), 3)

st = state([card("DEFEND_IRONCLAD", 1, blk=5), card("INFECTION", -1)], [enemy()])
calc = ctx._combat(st, {})["calc"]
check("5 甲挡 3 税 → full", (calc.get("net"), calc.get("full")), (0, True))

st = state([card("DEFEND_IRONCLAD", 1, blk=5)], [enemy(atk=5, hits=2, total=10)],
           player_powers=FRAIL1)
calc = ctx._combat(st, {})["calc"]
check("ctx 也吃 Frail：blk3/net7", (calc.get("blk"), calc.get("net")), (3, 7))

st = state([card("SLIMED", 1, draw=1, playable=True, text="抽1张牌。消耗。")],
           [enemy(atk=5, hits=2, total=10)])
calc = ctx._combat(st, {})["calc"]
check("Slimed 不进 tax", calc.get("tax"), None)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
