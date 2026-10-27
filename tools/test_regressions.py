# -*- coding: utf-8 -*-
"""历史错误回归库：每个用例都来自账本 `logs/decisions.jsonl` 的真实局面。

和 test_sim.py 的区别：那边的局面是手搓的；这里每一个都标了 run/楼层/回合，
是**真的打错或算错过的局面**，修完后必须仍然算对。跑法：

    python tools/test_regressions.py

口径：只断言「当时可见信息 + 确定性结算」，不涉及抽牌随机与 AI 评价。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("STS2_DECISION_LOG", "0")

import sim  # noqa: E402
import act  # noqa: E402

ok = True


def check(name, got, want=True):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def card(cid, cost=1, dmg=0, blk=0, up=False, rules="", dyn=None, **extra):
    c = {"card_id": cid, "name": cid, "energy_cost": cost, "playable": True,
         "upgraded": up,
         "target_type": "AnyEnemy" if (dmg and not extra.get("aoe")) else "Self"}
    if dmg:
        c["damage"] = dmg
    if blk:
        c["block"] = blk
    if rules:
        c["rules_text"] = rules
    if dyn:
        c["dynamic_values"] = [{"name": k, "current_value": v} for k, v in dyn.items()]
    c.update(extra)
    return c


def enemy(idx=0, eid="E0", hp=20, atk=12, powers=None):
    return {"index": idx, "enemy_id": eid, "name": eid, "current_hp": hp, "hp": hp,
            "max_hp": hp, "is_alive": True, "block": 0, "powers": powers or [],
            "intents": [{"type": "Attack", "damage": atk, "hits": 1,
                         "total_damage": atk}]}


def state(hand, energy=3, hp=50, enemies=None, powers=None, relics=None, potions=None):
    return {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn"],
        "run": {"current_hp": hp, "max_hp": 80, "max_energy": 3,
                "potions": potions or [], "floor": 5, "act": 1, "ascension": 10,
                "relics": [{"relic_id": r} for r in (relics or [])]},
        "combat": {"turn": 3,
                   "player": {"current_hp": hp, "max_hp": 80, "energy": energy,
                              "block": 0, "powers": powers or []},
                   "hand": hand,
                   "enemies": enemies or [enemy()]},
    }


# ---------------------------------------------------------------- R1 状态牌不是攻击牌
# run HECC0H1NUR f30 t2：手牌两张「毒素」（状态牌，1 费，回合末在手扣 5 血）。
# 旧 sim 把引擎给的 Damage=5 当成攻击伤害，两次「打毒素」凭空对敌人输出 10 点，
# 预测敌人 59 血、实际 69 血 —— 斩杀线直接被算高 10 点。
TOXIC_TEXT = "在你的回合结束时，如果这张牌在你的手牌中，你受到5点伤害。"
st = state([card("TOXIC", 1, rules=TOXIC_TEXT), card("TOXIC", 1, rules=TOXIC_TEXT),
            card("BATTLE_TRANCE", 0)], energy=3, enemies=[enemy(0, "MYTE", 69, 10)])
sm = sim.Sim.from_state(st)
t0 = sm.by_index(0)["hp"]
check("R1 毒素回合末税 5/张", sim.status_tax_parts(sm.hand), (10, 0))
sm.play(sm.hand[0], None)
sm.play(sm.hand[1], None)
check("R1 打状态牌不伤敌人", sm.by_index(0)["hp"], t0)
check("R1 状态牌照收费用", sm.energy, 1)
check("R1 打掉两张税清零", sim.status_tax_parts(sm.hand), (0, 0))

# ---------------------------------------------------------------- R2 势不可当要算伤害
# run Z6DHD2PJBQ f14 t2：势不可当+（8 点）+ 耸肩无视，实际敌人掉 8 血，旧 sim 掉 0。
st = state([card("JUGGERNAUT", 2, up=True), card("SHRUG_IT_OFF", 1, blk=8)],
           energy=3, enemies=[enemy(0, "CORPSE_SLUG", 28, 12)],
           powers=[{"power_id": "JUGGERNAUT_POWER", "name": "势不可当", "amount": 4}])
sm = sim.Sim.from_state(st)
e0 = sm.by_index(0)["hp"]
jug = next(c for c in sm.hand if c["id"] == "JUGGERNAUT")
step = sm.play(jug, None)
check("R2 势不可当进场不吃伤害", sm.by_index(0)["hp"], e0)
shr = next(c for c in sm.hand if c["id"] == "SHRUG_IT_OFF")
step = sm.play(shr, None)
check("R2 起甲触发势不可当", sm.by_index(0)["hp"], e0 - 4)
check("R2 步骤里记下势不可当伤害", step.get("jugg"), 4)

# ---------------------------------------------------------------- R3 延迟给费不能当场到账
# run 6E26GF0HPM f9：战鼓（被消耗时获得 2 费）当回合预测 5 费、实际 3 费；
# run Z6DHD2PJBQ f25：薪火之源（回合开始时获得）同理。
for cid, cost, rules, dyn in (
        ("DRUM_OF_BATTLE", 0, "抽2张牌。这张牌被消耗时，获得。", {"Energy": 2}),
        ("PYRE", 2, "在回合开始时，获得。", {"Energy": 2})):
    st = state([card(cid, cost, rules=rules, dyn=dyn)], energy=3)
    sm = sim.Sim.from_state(st)
    c = sm.hand[0]
    check(f"R3 {cid} 不当场回能", c.get("en"), None)
    check(f"R3 {cid} 记为延迟", bool(c.get("en_deferred")), True)
    sm.play(c, None)
    check(f"R3 {cid} 打完能量不变", sm.energy, 3 - cost)

# ---------------------------------------------------------------- R4 滑溜：一次只掉 1
# run RJJBH9NXR8 f12 t1：三只小墨宝各带「滑溜」1 层，旧 sim 一刀算满伤 → 误判能斩。
st = state([card("STRIKE_IRONCLAD", 1, dmg=6), card("STRIKE_IRONCLAD", 1, dmg=6)],
           energy=3, enemies=[enemy(0, "INKLET", 17, 9,
                                    powers=[{"power_id": "SLIPPERY_POWER", "name": "滑溜",
                                             "amount": 1}])])
sm = sim.Sim.from_state(st)
e = sm.by_index(0)
sm.play(sm.hand[0], 0)
check("R4 滑溜第一刀只掉 1", e["hp"], 16)
sm.play(sm.hand[1], 0)
check("R4 滑溜用掉后正常吃伤", e["hp"], 10)

# ---------------------------------------------------------------- R5 状态牌不打会扣自己
# 同 R1 局面：回合末两张毒素在手 → 10 点可格挡伤害，且不算「失去生命」。
st = state([card("STRIKE_IRONCLAD", 1, dmg=6), card("TOXIC", 1), card("TOXIC", 1)],
           energy=3)
sm = sim.Sim.from_state(st)
check("R5 回合末税按张数叠加", sim.status_tax_parts(sm.hand), (10, 0))
sm.play(sm.hand[0], 0)
check("R5 打掉一张税就少 5", sim.status_tax_parts(sm.hand), (10, 0))

# ---------------------------------------------------------------- R6 清场这一手不硬校验血量
# run Z6DHD2PJBQ f5 t3：一刀打死 6 血怪，游戏当场回 6 血（燃烧之血），
# 本地 sim 继续报 48 —— 账本里 15 条 fx.hp 误报全是这个口径差。
st = state([card("STRIKE_IRONCLAD", 1, dmg=6)], energy=3, hp=48,
           enemies=[enemy(0, "SLUDGE_SPINNER", 6, 9)], relics=["BURNING_BLOOD"])
okv, err, sm, steps = sim.validate(st, [{"c": "STRIKE_IRONCLAD", "t": 0}])
check("R6 一刀清场", okv, True)
fc = act.forecast(st, sm, steps, ended=False)
check("R6 清场标记", fc.get("clear"), 1)
check("R6 清场时 fx.hp 不拦", act.fx_error({"hp": 48}, fc), None)
check("R6 清场仍校验能量", bool(act.fx_error({"e": 99}, fc)), True)

# ---------------------------------------------------------------- R7 免费攻击只免一张
# run85 f11 t4 / f17 t3 / f20 t3 / f21 t2：挂「免费攻击」时引擎把**所有**攻击牌都报
# energy_cost=0，计划和 sim 都按「全都免费」算，实际只有一张免费 → 打到第三张断供，
# 整个 batch 中断（61 个 model 批次里 4 次，6.6%）。
FIVE_STRIKES = [card("STRIKE_IRONCLAD", 1, dmg=6) for _ in range(5)]
st = state([dict(c) for c in FIVE_STRIKES], energy=3,
           powers=[{"power_id": "FREE_ATTACK", "name": "免费攻击", "amount": 1}])
sm = sim.Sim.from_state(st)
check("R7 免费攻击记 1 层", sm.free_atk, 1)
check("R7 下一张攻击按 0 费", sm.cost_of(sm.hand[0]), 0)
sm.play(sm.hand[0], 0)
check("R7 免费用掉后剩 3 费", (sm.energy, sm.free_atk), (3, 0))
check("R7 用完第二张恢复原费", sm.cost_of(sm.hand[1]), 1)
n = 0
for c in list(sm.hand):
    if c.get("_played"):
        continue
    if sm.play(c, 0) is None:
        break
    n += 1
check("R7 3 费只够再打 3 张", (n, sm.energy), (3, 0))
check("R7 第 5 张打不出", len([c for c in sm.hand if not c.get("_played")]), 1)

# ---------------------------------------------------------------- R8 引擎报全 0 费要还原原费
# 同一局面：手牌三张攻击的 energy_cost 都是引擎报的 0（buff 期间），ctx 必须还原成原费，
# 否则我看到的是一整手 0 费牌 —— 这正是 run85 那 4 次超支的直接原因。
zero_hand = [card("STRIKE_IRONCLAD", 0, dmg=6) for _ in range(3)]
st = state(zero_hand, energy=3,
           powers=[{"power_id": "FREE_ATTACK", "name": "免费攻击", "amount": 1}])
sm = sim.Sim.from_state(st)
check("R8 手牌还原成原费", [c["c"] for c in sm.hand], [1, 1, 1])
check("R8 标出被减免", [c.get("disc") for c in sm.hand], [1, 1, 1])
sm.play(sm.hand[0], 0)
check("R8 免费那张不扣费", sm.energy, 3)
sm.play(sm.hand[1], 0)
check("R8 第二张按原费扣", sm.energy, 2)

# ---------------------------------------------------------------- R9 永恒 0 费打击不还原
# 营养汤把 4 张打击附成「0 费 + 永恒」：这是真 0 费，别被上面的还原逻辑改成 1 费。
eternal_hand = [card("STRIKE_IRONCLAD", 0, dmg=9, rules="造成9点伤害。 永恒。")]
st = state(eternal_hand, energy=3,
           powers=[{"power_id": "FREE_ATTACK", "name": "免费攻击", "amount": 1}])
sm = sim.Sim.from_state(st)
check("R9 永恒的 0 费保持 0", sm.hand[0]["c"], 0)
check("R9 永恒标记", sm.hand[0].get("eternal"), 1)

# ---------------------------------------------------------------- R10 毒素不进攻击榜
# run85 f22：VALUE_RANK 把「毒素」排成「5伤/1费=5.0」并进了 3 个 plan —— 那是自伤。
import sts2  # noqa: E402

toxic = card("TOXIC", 1, rules=TOXIC_TEXT, dyn={"Damage": 5})
check("R10 毒素不是攻击牌", sts2._attack_profile(toxic), None)
check("R10 毒素不进 EFF 表", sts2._attack_effect_table([toxic], [enemy(0, "MYTE", 69, 10)], {}), [])
rank = sts2._hand_value_rank([toxic, card("STRIKE_IRONCLAD", 1, dyn={"Damage": 6})],
                             [enemy(0, "MYTE", 69, 10)])
check("R10 毒素不进价值榜", "TOXIC" in rank, False)
check("R10 同一手牌里打击照常上榜", "STRIKE_IRONCLAD" in rank, True)

# ---------------------------------------------------------------- R11 永冻冰晶：首张能力 +7 甲
# run85 f12 t1 / f17 t2 / f19 t1：第一张能力牌白拿 7 甲，旧 sim 不算 →
# 6 个回合的入伤被高估，合计 42 点（换血线 advisory 的主要来源）。
st = state([card("INFLAME", 1, rules="获得2点力量。"), card("INFLAME", 1, rules="获得2点力量。")],
           energy=3, relics=["PERMAFROST"])
sm = sim.Sim.from_state(st)
check("R11 遗物认出来", sm.permafrost, 7)
sm.play(sm.hand[0], None)
check("R11 首张能力给 7 甲", sm.block, 7)
sm.play(sm.hand[1], None)
check("R11 第二张能力不再给甲", sm.block, 7)

# ---------------------------------------------------------------- R12 狱火回合初自伤
# run85：take 误差里 12/35 是固定 +1 —— 狱火「回合开始时失去 1 点生命」没算进回合末血量。
st = state([card("DEFEND_IRONCLAD", 1, blk=5)], energy=3, hp=40,
           powers=[{"power_id": "INFERNO_POWER", "name": "狱火", "amount": 1}],
           enemies=[enemy(0, "DUMMY", 30, 0)])  # 敌人不攻击：只剩自伤
sm = sim.Sim.from_state(st)
check("R12 狱火记 1 点", sm.tick, 1)
res = sm.end_turn()
check("R12 回合末血量含自伤", res["hp_after"], 39)
check("R12 掉血含自伤", res["taken"], 1)
check("R12 账本标 tick", res["tick"], 1)

# ---------------------------------------------------------------- R13 尖叫：跨 50% 血线当回合不挨打
# run85 f11 t3 骇鳗 150 血：打到 84（≤75 的自晕线）它这回合就不攻击了，
# 旧 sim 照算 18 点入伤（实际只掉 1 点，还是狱火）。这是「大额高估」的主因之一。
eel = {"index": 0, "enemy_id": "TERROR_EEL", "name": "骇鳗", "current_hp": 84,
       "hp": 84, "max_hp": 150, "is_alive": True, "block": 0,
       "powers": [{"power_id": "SHRIEK_POWER", "name": "尖叫", "amount": 75},
                  {"power_id": "WEAK_POWER", "name": "虚弱", "amount": 2}],
       "intents": [{"type": "Attack", "damage": 18, "hits": 1, "total_damage": 18}]}
st = state([card("STRIKE_IRONCLAD", 1, dyn={"Damage": 14})], energy=3, enemies=[eel])
sm = sim.Sim.from_state(st)
check("R13 认出 75 的自晕线", sm.by_index(0)["stun_at"], 75)
check("R13 没跨线前照算入伤", sm.incoming(), 18)
sm.play(sm.hand[0], 0)
check("R13 跨线即眩晕", sm.by_index(0)["stunned"], True)
check("R13 眩晕后入伤 0", sm.incoming(), 0)

# ---------------------------------------------------------------- R14 振翅：攻击伤害减半
# run85 f19 t3 偷窃草蜢「振翅」：预测 26 实打 13，直接导致假斩杀。
flutter = {"index": 0, "enemy_id": "THIEVING_HOPPER", "name": "偷窃草蜢", "current_hp": 40,
           "hp": 40, "max_hp": 84, "is_alive": True, "block": 0,
           "powers": [{"power_id": "FLUTTER_POWER", "name": "振翅", "amount": 1}],
           "intents": [{"type": "Attack", "damage": 0, "hits": 1, "total_damage": 0}]}
st = state([card("STRIKE_IRONCLAD", 1, dyn={"Damage": 10})], energy=3, enemies=[flutter])
sm = sim.Sim.from_state(st)
check("R14 振翅认出来", sm.by_index(0)["flutter"], 1)
sm.play(sm.hand[0], 0)
check("R14 只吃一半伤害", sm.by_index(0)["hp"], 35)

# ---------------------------------------------------------------- R15 幻象：打死会复活，不是斩杀
# run85 f21：爪牙带「幻象」，连杀 3 次每次都满血复活；模型每次都预测 kill，白扔 3 回合。
illus = {"index": 0, "enemy_id": "PARAFRIGHT", "name": "帕拉弗莱特", "current_hp": 5,
         "hp": 5, "max_hp": 21, "is_alive": True, "block": 0,
         "powers": [{"power_id": "ILLUSION_POWER", "name": "幻象", "amount": 1},
                    {"power_id": "MINION_POWER", "name": "爪牙", "amount": 1}],
         "intents": [{"type": "Attack", "damage": 0, "hits": 1, "total_damage": 0}]}
st = state([card("STRIKE_IRONCLAD", 1, dyn={"Damage": 10})], energy=3, enemies=[illus])
sm = sim.Sim.from_state(st)
check("R15 幻象认出来", sm.by_index(0)["illusion"], 1)
sm.play(sm.hand[0], 0)
check("R15 打死了标复活", sm.enemies[0].get("revive"), 1)
fc = act.forecast(st, sm, [], ended=False)
check("R15 不算斩杀", fc.get("kill"), None)
check("R15 告诉模型会复活", fc.get("revive"), [0])
check("R15 不当成清场", fc.get("clear"), None)

# ---------------------------------------------------------------- R16 领奖不能绕过牌组上限
# run85：collect_rewards_and_proceed 静默把卡牌奖励领走，牌组 16→20，
# 而 DECK_CAP 只在 choose_reward_card 上生效。
deck16 = [{"card_id": "STRIKE_IRONCLAD", "name": "打击"} for _ in range(16)]
rw = {"screen": "REWARD",
      "run": {"act_id": 1, "floor": 10, "deck": deck16},
      "reward": {"rewards": [{"reward_type": "Card", "description": "将一张牌添加到你的牌组。"},
                             {"reward_type": "Gold", "description": "8金币"}]}}
check("R16 认出还有卡牌奖励", sts2.pending_card_reward(rw), True)
check("R16 满编时给 DECK_CAP", "DECK_CAP" in (sts2.deck_cap_guard(rw) or ""), True)
rw2 = {"screen": "REWARD", "run": {"act_id": 1, "floor": 10, "deck": deck16},
       "reward": {"rewards": [{"reward_type": "Gold", "description": "8金币"}]}}
check("R16 没有卡牌奖励就不拦（纯金币/遗物照收）", sts2.pending_card_reward(rw2), False)

# ---------------------------------------------------------------- R17 过渡快照别误报
# run85 f11 t2：手牌打空、能量还剩 1，被判「过渡快照」拒绝 end（账本 2 条 stale_end）。
def _pre_state(energy, hand_n=0):
    return {"screen": "COMBAT", "run": {"max_energy": 3},
            "combat": {"turn": 2, "player": {"energy": energy, "max_energy": 3},
                       "hand": [], "enemies": [enemy()]},
            "agent_view": {"combat": {"draw_cards": [{"card_id": "STRIKE_IRONCLAD"}] * 4}}}


check("R17 还有能量 → 不是过渡快照", act._pre_draw(_pre_state(1)), False)
check("R17 满能量空手 → 仍是过渡快照", act._pre_draw(_pre_state(3)), True)

# ---------------------------------------------------------------- R18 药水位满了要明说，别静默 pending
# run85 f14/f15/f19/f20：四次领药水奖励都回 pending，从未到手（A10 只有 2 格）。
full_pots = [{"index": 0, "potion_id": "FIRE_POTION", "name": "火焰药水"},
             {"index": 1, "potion_id": "BLOCK_POTION", "name": "格挡药水"}]
one_free = [dict(full_pots[0]), {"index": 1, "potion_id": None, "name": None}]
potion_rw = lambda pots: {"screen": "REWARD", "run": {"act_id": 1, "floor": 14, "potions": pots},
                          "reward": {"rewards": [{"reward_type": "Potion",
                                                  "description": "获得一瓶药水。"}]}}
check("R18 认出药水奖励", sts2.pending_potion_rewards(potion_rw(full_pots)), [0])
check("R18 少一瓶时空格够", sts2.free_potion_slots(potion_rw(one_free)), 1)
check("R18 满了就没空格", sts2.free_potion_slots(potion_rw(full_pots)), 0)
check("R18 满格时必须拦（否则静默 pending）",
      sts2.free_potion_slots(potion_rw(full_pots)) < len(sts2.pending_potion_rewards(potion_rw(full_pots))),
      True)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
