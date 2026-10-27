# -*- coding: utf-8 -*-
"""离线自测：sim 的实费/回能/变费/抽牌/消耗/药水/击杀连锁。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import sim  # noqa: E402

ok = True


def check(name, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def card(cid, cost, dmg=None, blk=None, vuln=None, draw=None, en=None, hp=None, hits=1,
         text="", target="AnyEnemy", up=False, x=False, playable=True):
    dv = []
    if dmg is not None:
        dv.append({"name": "Damage", "current_value": dmg})
    if blk is not None:
        dv.append({"name": "Block", "current_value": blk})
    if vuln is not None:
        dv.append({"name": "VulnerablePower", "current_value": vuln})
    if draw is not None:
        dv.append({"name": "Cards", "current_value": draw})
    if en is not None:
        dv.append({"name": "Energy", "current_value": en})
    if hp is not None:
        dv.append({"name": "HpLoss", "current_value": hp})
    if hits > 1 and not text:
        text = f"造成{dmg}点伤害{hits}次。"
    return {"card_id": cid, "name": cid, "energy_cost": cost, "upgraded": up,
            "playable": playable, "costs_x": x,
            "requires_target": dmg is not None and target != "AllEnemies",
            "target_type": target, "dynamic_values": dv, "resolved_rules_text": text}


def enemy(eid, hp, atk, hits=1, powers=None, idx=0):
    e = {"index": idx, "enemy_id": eid, "name": eid, "current_hp": hp, "max_hp": hp, "block": 0,
         "is_alive": True, "powers": powers or [],
         "intents": [{"intent_type": "ATTACK", "damage": atk, "hits": hits,
                      "total_damage": atk * hits}]}
    return e


def state(hand, enemies, hp=60, energy=3, block=0, potions=None, powers=None):
    return {"screen": "COMBAT", "available_actions": ["play_card", "end_turn"],
            "run": {"current_hp": hp, "max_hp": 80, "max_energy": 3,
                    "potions": potions or []},
            "combat": {"turn": 1, "player": {"current_hp": hp, "max_hp": 80,
                                             "energy": energy, "block": block,
                                             "powers": powers or []},
                       "hand": hand, "enemies": enemies}}


print("=== 1) 实费 / 伤害 / 格挡 ===")
s = state([card("STRIKE_IRONCLAD", 1, dmg=6)], [enemy("A", 30, 10)])
plan = sim.search(s)[0]
check("打一刀", plan["seq"], [["STRIKE_IRONCLAD", 0]])
check("伤害 6", plan["dmg"], 6)
check("净掉 10", plan["taken"], 10)

print()
print("=== 2) 放血 +2 能量，能多打一张 ===")
s = state([card("BLOODLETTING", 0, hp=3, en=2), card("STRIKE_IRONCLAD", 1, dmg=6),
           card("STRIKE_IRONCLAD", 1, dmg=6), card("STRIKE_IRONCLAD", 1, dmg=6),
           card("STRIKE_IRONCLAD", 1, dmg=6)],
          [enemy("A", 40, 10)])
plan = sim.search(s)[0]
print("  ", plan)
check("5 张都打（放血 + 4 刀）", len([x for x in plan["seq"] if not str(x[0]).startswith("@")]), 5)
check("能量剩 1（3+2-4）", plan["e"], 1)
check("自伤 3 进入血量", plan["hp"], 60 - 3 - 10)

print()
print("=== 3) 无情猛攻：下一张攻击 0 费 ===")
s = state([card("UNRELENTING", 2, dmg=14), card("STRIKE_IRONCLAD", 1, dmg=6),
           card("STRIKE_IRONCLAD", 1, dmg=6)], [enemy("A", 40, 10)])
plan = sim.search(s)[0]
print("  ", plan)
check("先打无情猛攻", plan["seq"][0][0], "UNRELENTING")
check("后两张攻击都能打（2 费 + 0 + 1）", len(plan["seq"]), 3)

print()
print("=== 4) 踩踏：每打一张攻击 -1 费 ===")
s = state([card("STRIKE_IRONCLAD", 1, dmg=6), card("STRIKE_IRONCLAD", 1, dmg=6),
           card("STOMP", 3, dmg=12, target="AllEnemies", text="对所有敌人造成12点伤害。 你在本回合中每打出过一张攻击牌，其耗能减少1。")],
          [enemy("A", 30, 5, idx=0), enemy("B", 30, 5, idx=1)])
plan = sim.search(s)[0]
print("  ", plan)
check("踩踏排最后", plan["seq"][-1][0], "STOMP")
check("三张都打", len(plan["seq"]), 3)

print()
print("=== 5) 杀一只→另一只吃尸眩晕 ===")
raven = [{"power_id": "RAVENOUS_POWER", "name": "饥饿", "amount": 1, "is_debuff": False}]
s = state([card("BASH", 2, dmg=10), card("STRIKE_IRONCLAD", 1, dmg=6)],
          [enemy("CORPSE_SLUG", 10, 9, hits=2, idx=0),
           enemy("CORPSE_SLUG", 25, 9, hits=2, powers=raven, idx=1)])
plan = sim.search(s)[0]
print("  ", plan)
check("斩杀 0 号", 0 in plan["kill"], True)
check("1 号被晕", 1 in plan["stun"], True)
check("本回合只吃 0（晕的那只不打）", plan["taken"], 0)

print()
print("=== 6) 药水可以插进牌序 ===")
potions = [{"index": 0, "potion_id": "FIRE_POTION", "name": "火焰药水", "occupied": True,
            "description": "对一名敌人造成20点伤害。", "can_use": True, "requires_target": True}]
s = state([card("STRIKE_IRONCLAD", 1, dmg=6)], [enemy("A", 22, 10)], potions=potions)
seq = [{"c": "STRIKE_IRONCLAD", "t": 0}, {"p": 0, "t": 0}, {"end": 1}]
good, err, sm, steps = sim.validate(s, seq)
check("药水顺序合法", good, True)
check("药水后怪死", [e["dead"] for e in sm.enemies], [True])

print()
print("=== 7) 校验：费用不够要在那一步拒绝 ===")
s = state([card("BASH", 2, dmg=10), card("STRIKE_IRONCLAD", 1, dmg=6)], [enemy("A", 30, 10)], energy=2)
good, err, sm, steps = sim.validate(s, [{"c": "BASH", "t": 0}, {"c": "STRIKE_IRONCLAD", "t": 0}])
print("  ", good, err)
check("第二张被拒", good, False)
check("错误定位到第 2 步", "第2步" in err, True)

print()
print("=== 8) 状态牌回合末掉血 ===")
s = state([card("INFECTION", -1, playable=False), card("STRIKE_IRONCLAD", 1, dmg=6)],
          [enemy("A", 30, 10)])
plan = sim.search(s)[0]
check("感染 3 伤计入", plan["taken"], 10 + 3)

# 当前卡库 Slimed 是 1 费抽 1，不再有回合末 3 伤；Wound 也无效果。
s = state([card("SLIMED", 1, draw=1, text="抽1张牌。消耗。"),
           card("STRIKE_IRONCLAD", 1, dmg=6)], [enemy("A", 30, 10)])
plan = sim.search(s)[0]
check("黏液不再计 3 伤", plan["taken"], 10)

print()
print("=== 9) 狂怒：每打一张攻击牌 +3 格挡（实测） ===")
s = state([card("RAGE", 0), card("STRIKE_IRONCLAD", 1, dmg=6), card("STRIKE_IRONCLAD", 1, dmg=6)],
          [enemy("A", 60, 9)])
plan = sim.search(s)[0]
check("两张攻击 = 6 层甲（9 伤只吃 3）", plan["taken"], 3)

print("=== 10) 横冲直撞：跌破阈值那一下攻击取消 ===")
e = enemy("BEAST", 160, 26)
e["stun_at"] = 160
s = state([card("STRIKE_IRONCLAD", 1, dmg=6)], [e])
plan = sim.search(s)[0]
check("跨过阈值不吃伤害", plan["taken"], 0)

print()
print("=== 11) 狂怒 + 横冲直撞 组合 ===")
e = enemy("BEAST", 160, 26)
e["stun_at"] = 160
s = state([card("RAGE", 0), card("BASH", 2, dmg=10)], [e])
plan = sim.search(s)[0]
check("痛击跌破阈值：零伤", plan["taken"], 0)

print()
print("=== 12) 难以杀灭 9：单次伤害封顶 ===")
e = enemy("EXOSKELETON", 40, 0)
e["hit_cap"] = 9
s = state([card("BLUDGEON", 3, dmg=42)], [e], energy=3)
plan = sim.search(s)[0]
check("42 只算 9", plan["dmg"], 9)

print()
print("=== 13) 钻洞：剥光格挡就晕 ===")
e = enemy("TUNNELER", 92, 21)
e["block"] = 6
e["dig"] = 1
s = state([card("STRIKE_IRONCLAD", 1, dmg=9)], [e])
plan = sim.search(s)[0]
check("剥光格挡零伤", plan["taken"], 0)

print()
print("=== 14) 本回合新挂的虚弱不改这回合的意图伤害 ===")
e = enemy("FOE", 80, 12)
s = state([card("UPPERCUT", 2, dmg=13)], [e])
plan = sim.search(s)[0]
check("挂虚弱后仍吃 12", plan["taken"], 12)

print()
print("=== 15) 人工制品按施加次数吃，不按层数 ===")
e = enemy("ARTI", 80, 12, powers=[{"power_id": "ARTIFACT_POWER", "name": "AF", "amount": 1}])
s = state([card("BASH", 2, dmg=8, vuln=2)], [e])
plan = sim.search(s)[0]
check("2 层易伤被 1 层人工制品全挡", plan["dmg"], 8)

print()
print("=== 16) 柔嫩：每打一张牌，剩下的牌 -1 伤 / -1 甲 ===")
pw = [{"power_id": "TENDER_POWER", "name": "柔嫩", "amount": 1}]
s = state([card("BLUDGEON", 3, dmg=42), card("STRIKE_IRONCLAD", 1, dmg=9),
           card("DEFEND_IRONCLAD", 1, blk=5)],
          [enemy("FOE", 200, 8)], energy=9, powers=pw)
st = sim.Sim.from_state(s)
check("柔嫩已解析", st.tender, 1)
check("先打大锤前的打击", st.hand[1]["dmg"], 9)
st.play(st.hand[0], 0)
check("打完大锤后打击 -1", st.hand[1]["dmg"], 8)
st.play(st.hand[1], 0)
check("打掉两张后格挡 5→3", st.hand[2]["blk"], 3)

print()
print("=== 17) 固化药水：现有格挡 ×3 ===")
pot = {"index": 0, "potion_id": "FIXED_POTION", "name": "固化药水", "occupied": True,
       "can_use": True, "requires_target": False,
       "description": "把你的格挡变为三倍。"}
s = state([card("STRIKE_IRONCLAD", 1, dmg=9)], [enemy("FOE", 60, 12)],
          block=10, potions=[pot])
st = sim.Sim.from_state(s)
check("药水解析出 blk_mul", st.potions[0].get("blk_mul"), 3)
st.potion(st.potions[0])
check("10 甲 → 30 甲", st.block, 30)

print()
print("=== 18) 缩小 / 缠结 / 失衡 也认得出 ===")
pw = [{"power_id": "SHRINK_POWER", "name": "缩小", "amount": 1},
      {"power_id": "TANGLED_POWER", "name": "缠结", "amount": 1},
      {"power_id": "IMBALANCED_POWER", "name": "失衡", "amount": 1}]
st = sim.Sim.from_state(state([card("STRIKE_IRONCLAD", 1, dmg=9)], [enemy("FOE", 60, 12)],
                              powers=pw))
check("缩小", st.shrink, 1)
check("缠结", st.tangled, 1)
check("失衡", st.imbalanced, 1)

print()
print("=== 19) 硬化外壳 20：整回合最多掉 20，且不给假斩杀（run86 f15 t4 真实局面）===")
e = enemy("SKULKING_COLONY", 45, 24)
e["hp_loss_cap"] = 20
s = state([card("UNRELENTING", 2, dmg=19), card("STRIKE_IRONCLAD", 1, dmg=11),
           card("THUNDERCLAP", 1, dmg=9, target="AllEnemies")], [e], energy=3)
st = sim.Sim.from_state(s)
for c in list(st.hand):
    st.play(c, 0)
check("三张 39 伤只掉 20（45→25）", st.by_index(0)["hp"], 25)
check("没有斩杀线", [x["i"] for x in sim.lethal_targets(s)], [])

print()
print("=== 20) 硬化外壳：power 解析 + 不误伤真实斩杀 ===")
pw = [{"power_id": "HARDENED_SHELL_POWER", "name": "硬化外壳", "amount": 20}]
e = enemy("SKULKING_COLONY", 15, 24, powers=pw)
check("power 解析出上限 20", sim.Sim.from_state(state([], [e])).enemies[0]["hp_loss_cap"], 20)
s = state([card("UNRELENTING", 2, dmg=19)], [e], energy=3)
check("15 血在上限之内，仍可斩杀", [x["i"] for x in sim.lethal_targets(s)], [0])

print()
print("=== 21) 硬化外壳：预算是整回合累计，不是每张牌重置 ===")
e = enemy("SHELL", 60, 10)
e["hp_loss_cap"] = 20
st = sim.Sim.from_state(state([card("STRIKE_IRONCLAD", 1, dmg=9)] * 3, [e], energy=3))
dealt = 0
for c in list(st.hand):
    dealt += st.hurt_enemy(st.by_index(0), c["dmg"], 1, strength=False, vuln=False)
check("9+9+9 只累计 20", dealt, 20)
check("血量 60→40", st.by_index(0)["hp"], 40)
check("格子里的上限字段保留", st.by_index(0)["hp_loss_cap"], 20)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
