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
# run87：`act_id` 是 0 基（1 = 第二幕，上限 18），所以「满编」要用 18 张的牌组来验；
# 旧用例拿 16 张在第二幕当满编，其实是被 deck_cap 的一基/零基混用挡住的（P10c）。
deck16 = [{"card_id": "STRIKE_IRONCLAD", "name": "打击"} for _ in range(16)]
deck18 = [{"card_id": "STRIKE_IRONCLAD", "name": "打击"} for _ in range(18)]
rw = {"screen": "REWARD",
      "run": {"act_id": 1, "floor": 10, "deck": deck16},
      "reward": {"rewards": [{"reward_type": "Card", "description": "将一张牌添加到你的牌组。"},
                             {"reward_type": "Gold", "description": "8金币"}]}}
check("R16 认出还有卡牌奖励", sts2.pending_card_reward(rw), True)
rw18 = {"screen": "REWARD",
        "run": {"act_id": 1, "floor": 10, "deck": deck18},
        "reward": {"rewards": [{"reward_type": "Card", "description": "将一张牌添加到你的牌组。"}]}}
check("R16 满编时给 DECK_CAP", "DECK_CAP" in (sts2.deck_cap_guard(rw18) or ""), True)
check("R16 第二幕 16 张不算满编（上限 18）", sts2.deck_cap_guard(rw), None)
rw2 = {"screen": "REWARD", "run": {"act_id": 1, "floor": 10, "deck": deck16},
       "reward": {"rewards": [{"reward_type": "Gold", "description": "8金币"}]}}
check("R16 没有卡牌奖励就不拦（纯金币/遗物照收）", sts2.pending_card_reward(rw2), False)

# --- R16b run89：没打开的卡牌奖励会让 collect 静默选第一张（pending_card_choice=false）
rw_unopened = {"screen": "REWARD", "run": {"act_id": 0, "floor": 7, "deck": deck16[:-2]},
               "reward": {"pending_card_choice": False,
                          "rewards": [{"index": 0, "reward_type": "Gold", "description": "9金币"},
                                      {"index": 1, "reward_type": "Card",
                                       "description": "将一张牌添加到你的牌组。"}]}}
check("R16b 没打开的卡牌奖励也能认出来", sts2.unclaimed_card_reward(rw_unopened), 1)
check("R16b 但 pending_card_choice 是 false（老护栏会放行）",
      sts2.pending_card_reward(rw_unopened), False)
check("R16b 金币在 0 位、卡牌在 1 位：返回 1 不是 0", sts2.unclaimed_card_reward(rw_unopened) != 0, True)
rw_claimed = {"screen": "REWARD", "reward": {"rewards": [
    {"index": 0, "reward_type": "Card", "is_claimed": True}]}}
check("R16b 已领的卡牌不再算未处理", sts2.unclaimed_card_reward(rw_claimed), None)
rw_gold_only = {"screen": "REWARD", "reward": {"rewards": [
    {"index": 0, "reward_type": "Gold"}]}}
check("R16b 纯金币奖励不触发自动 skip", sts2.unclaimed_card_reward(rw_gold_only), None)


# --- R16c run91：结算慢的根因是「5 个独立回合 + 每次护栏拒绝都要重试」。
# `sts2.py rewards` 把整个 REWARD 界面放进一个进程里循环。这里用假引擎锁死两个死循环风险：
#   1) skip_reward_cards 之后 rewards 里的 Card 条目**仍是未领状态**
#      → 只看 is_claimed 的实现会一直 claim→skip→claim（run91 f14 实测刷了 6 轮还没 collect）
#   2) 收干净之后必须真的调 collect，不能停在 REWARD 界面
def _fake_reward_run(mode, states):
    """把 sts2 的 get_state/act 换成脚本化假引擎，跑一次 cmd_rewards，返回调用序列。"""
    calls = []
    idx = {"st": 0}

    def fake_get_state():
        return states[min(idx["st"], len(states) - 1)]

    def fake_act(action, **params):
        calls.append((action, params.get("option_index")))
        idx["st"] += 1
        return {"status": "completed", "message": "fake"}

    saved = (sts2.get_state, sts2.act, sts2.wait_stable, sts2.overlay_say, sts2.compact)
    sts2.get_state, sts2.act = fake_get_state, fake_act
    sts2.wait_stable = lambda *a, **k: states[min(idx["st"], len(states) - 1)]
    sts2.overlay_say = lambda *a, **k: None
    sts2.compact = lambda *a, **k: ""
    try:
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sts2.cmd_rewards(take=mode, potion="skip", thought="", meme="")
        return calls, buf.getvalue()
    finally:
        sts2.get_state, sts2.act, sts2.wait_stable, sts2.overlay_say, sts2.compact = saved


def _rw(rewards, pending=False, deck=15, screen="REWARD"):
    return {"screen": screen, "run": {"act_id": 0, "floor": 14, "deck": [{}] * deck},
            "reward": {"pending_card_choice": pending, "rewards": rewards}}


_gold_card = [{"index": 0, "reward_type": "Gold", "is_claimed": True},
              {"index": 1, "reward_type": "Card"}]
_calls, _out = _fake_reward_run(None, [_rw(_gold_card),
                                       _rw(_gold_card, pending=True, screen="CARD_SELECTION"),
                                       _rw(_gold_card),
                                       _rw(_gold_card, screen="MAP")])
check("R16c 跳过卡牌后仍然会 collect（不会卡在 REWARD）",
      "collect_rewards_and_proceed" in [c[0] for c in _calls], True)
check("R16c 同一张卡牌只 claim 一次（skip 后条目仍未领也不能重刷）",
      [c[0] for c in _calls].count("claim_reward"), 1)
check("R16c 卡牌走 skip 而不是静默 collect", "skip_reward_cards" in [c[0] for c in _calls], True)
check("R16c 动作数收敛（≤4 步收完）", len(_calls) <= 4, True)
_take_calls, _ = _fake_reward_run(0, [_rw([{"index": 0, "reward_type": "Card"}], pending=False),
                                     _rw([{"index": 0, "reward_type": "Card"}], pending=True,
                                         screen="CARD_SELECTION"),
                                     _rw([], pending=False),
                                     _rw([], screen="MAP")])
check("R16c --take 路径会调 choose_reward_card",
      "choose_reward_card" in [c[0] for c in _take_calls], True)
check("R16c --take 路径不会再去 skip 卡牌",
      "skip_reward_cards" in [c[0] for c in _take_calls], False)
check("R16c 药水价值排序：力量药水该留、技能药水可丢",
      (sts2.potion_rank("STRENGTH_POTION"), sts2.potion_rank("SKILL_POTION")), (0, 2))


# --- R16d run92：结算「卡很久」的真根因不是引擎，是**等玩家回合的盲等**。
# 实测 `tools/act.py` 清场那一步 `run_batch` = 92.6 秒，而结算本身只有 1.1 秒。
# 原因：清场后批量里剩下的 `end` 步还会调 `_player_turn_state()`，屏幕已经是
# REWARD/MAP，旧版对着非战斗屏幕空转满 8 秒超时，外层再重试 10 次 → 8s×11 ≈ 90 秒。
def _pts_with(screen, acts, timeout=0.3):
    saved = sts2.get_state
    sts2.get_state = lambda: {"screen": screen, "available_actions": list(acts)}
    try:
        import time as _t
        t0 = _t.time()
        got = sts2._player_turn_state(timeout=timeout)
        return got, _t.time() - t0
    finally:
        sts2.get_state = saved


_got, _dt = _pts_with("REWARD", ["collect_rewards_and_proceed"])
check("R16d 清场后（REWARD）立刻返回 _BATTLE_OVER", _got is sts2._BATTLE_OVER, True)
check("R16d 而且不能空转满超时（<0.5 秒返回）", _dt < 0.5, True)
_got, _dt = _pts_with("MAP", ["choose_map_node"])
check("R16d 地图界面也立刻返回 _BATTLE_OVER", _got is sts2._BATTLE_OVER, True)
_got, _dt = _pts_with("CARD_SELECTION", ["select_deck_card"])
check("R16d 选牌界面也立刻返回 _BATTLE_OVER", _got is sts2._BATTLE_OVER, True)
_got, _ = _pts_with("COMBAT", ["play_card", "end_turn"])
check("R16d 真玩家回合仍然正常返回快照", isinstance(_got, dict) and _got.get("screen"), "COMBAT")
_got, _dt = _pts_with("COMBAT", ["save_and_quit"], timeout=0.2)
check("R16d 战斗中无动作（敌人回合）→ 还是超时返回 None", _got, None)
check("R16d 战斗中等待确实会等到超时（不误判成结束）", _dt >= 0.15, True)


# --- R16e run92：领奖时「自动挑牌」的排序（`card_pick_score`）必须真的把废牌排后面。
# 病根：以前 CARD_SELECTION 直接拿第 0 张，run92 f14 因此吃了 SPOILS_MAP（600 金币的
# 死牌、不能打出）这种纯占位牌。排序坏了就等于把牌组继续喂肥。
_cards = [{"id": "SPOILS_MAP", "name": "藏宝图", "cost": -1, "type": "Skill",
           "raw": {"description": "不能被打出。"}},
          {"id": "STRIKE_IRONCLAD", "name": "打击", "cost": 1, "type": "Attack",
           "raw": {"description": "造成 6 点伤害。"}},
          {"id": "HEMOKINESIS", "name": "御血术", "cost": 1, "type": "Attack",
           "raw": {"description": "失去 2 点生命。造成 15 点伤害。"}},
          {"id": "ASCENDERS_BANE", "name": "进阶之灾", "cost": -1, "type": "Curse",
           "raw": {"description": "不能被打出。"}}]
_i, _s, _n = sts2.pick_best_card(_cards)
check("R16e 御血术（15 伤/1 费）排第一", _n, "御血术")
check("R16e 诅咒/藏宝图这类死牌分数为负", sts2.card_pick_score(_cards[3]) < 0, True)
check("R16e 自动挑牌不会挑到死牌", _n not in ("藏宝图", "进阶之灾"), True)
_i2, _s2, _n2 = sts2.pick_best_card([_cards[0], _cards[3]])
check("R16e 全是废牌时也返回一个合法下标（由阈值决定跳不跳）", _i2 in (0, 1), True)


# --- R16f run92：`overlay_say` 不能阻塞在 TTS 预热上（3–4 秒/句）。
# 实测 av_client 日志：prewarm_end 2600–4640ms，published 只要 3–23ms。
def _say_latency():
    import time as _t
    saved = (sts2._prewarm_tts, sts2.requests.post, sts2._av_log)

    class _R:
        status_code = 200

        def json(self):
            return {"id": 1}

    def _slow_prewarm(text, cid):
        _t.sleep(1.2)

    sts2._prewarm_tts = _slow_prewarm
    sts2.requests.post = lambda *a, **k: _R()
    sts2._av_log = lambda *a, **k: None
    try:
        t0 = _t.time()
        sts2.overlay_say("战斗", "测一句预热不挡发布", "", None, force=True)
        return _t.time() - t0
    finally:
        sts2._prewarm_tts, sts2.requests.post, sts2._av_log = saved


_dt = _say_latency()
check("R16f 预热 1.2 秒时 overlay_say 仍然立刻返回（<0.5 秒）", _dt < 0.5, True)


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

# ---------------------------------------------------------------- R19 红面具的虚弱已在引擎意图里
# run85 报告把 42 点入伤高估摊给「永冻冰晶 + 红面具」两个遗物，核对后只有永冻冰晶是缺口：
# RED_MASK 给的 1 层虚弱已经算进引擎的 total_damage，sim 再乘一次 0.75 会变成「低估」。
# 这条用例钉死：total_damage 存在时按它算，只有回落到 damage×hits 时才补虚弱。
def _weak_enemy(with_total: bool):
    e = {"index": 0, "enemy_id": "DUMMY", "name": "木桩", "current_hp": 60, "hp": 60,
         "max_hp": 60, "is_alive": True, "block": 0,
         "powers": [{"power_id": "WEAK_POWER", "name": "虚弱", "amount": 1}],
         "intents": [{"type": "Attack", "damage": 12, "hits": 1}]}
    if with_total:
        e["intents"][0]["total_damage"] = 9  # 引擎已把 12 打成 9
    return e


st = state([card("DEFEND_IRONCLAD", 1, blk=5)], energy=3, enemies=[_weak_enemy(True)],
           relics=["RED_MASK"])
sm = sim.Sim.from_state(st)
check("R19 total_damage 已是最终值，不再乘虚弱", sm.incoming(), 9)
st = state([card("DEFEND_IRONCLAD", 1, blk=5)], energy=3, enemies=[_weak_enemy(False)],
           relics=["RED_MASK"])
check("R19 回落路径才补一次虚弱", sim.Sim.from_state(st).incoming(), 9)

# ---------------------------------------------------------------- R20 没建模的机制要明说
# 斩杀/入伤不能假装精确：ESCAPE_ARTIST / PLATING / 没建模的遗物必须出现在 ctx 和 forecast 里。
plat = {"index": 0, "enemy_id": "THIEF", "name": "窃贼", "current_hp": 30, "hp": 30,
        "max_hp": 30, "is_alive": True, "block": 0,
        "powers": [{"power_id": "PLATING_POWER", "name": "覆甲", "amount": 4}],
        "intents": [{"type": "Attack", "damage": 6, "hits": 1, "total_damage": 6}]}
st = state([card("STRIKE_IRONCLAD", 1, dyn={"Damage": 6})], energy=3, enemies=[plat],
           relics=["GORGET"])
sm = sim.Sim.from_state(st)
ids = [x["id"] for x in sm.unmodeled]
check("R20 标出敌人覆甲", "PLATING_POWER" in ids, True)
check("R20 标出没建模的遗物", "GORGET" in ids, True)
fc = act.forecast(st, sm, [], ended=False)
check("R20 forecast 带 approx", fc.get("approx"), ids)
st_plain = state([card("STRIKE_IRONCLAD", 1, dyn={"Damage": 6})], energy=3)
sm_plain = sim.Sim.from_state(st_plain)
check("R20 全部建模时不乱标", sm_plain.unmodeled, [])
check("R20 干净局面没有 approx", act.forecast(st_plain, sm_plain, [], ended=False).get("approx"), None)

cst = {"screen": "COMBAT", "available_actions": ["play_card", "end_turn"],
       "run": {"current_hp": 60, "max_hp": 80, "max_energy": 3, "floor": 9, "act_id": 1,
               "ascension": 10, "gold": 100, "potions": [], "deck": [],
               "relics": [{"relic_id": "GORGET", "name": "护喉甲"}]},
       "combat": {"turn": 3,
                  "player": {"current_hp": 60, "max_hp": 80, "energy": 3, "block": 0,
                             "powers": []},
                  "hand": [card("STRIKE_IRONCLAD", 1, dyn={"Damage": 6})],
                  "enemies": [plat]},
       "agent_view": {"combat": {"draw_cards": [{"card_id": "STRIKE_IRONCLAD"}],
                                 "discard_cards": []}}}
import ctx  # noqa: E402

cout = ctx.build(cst)
check("R20 ctx 也标 unmodeled", [x["id"] for x in cout.get("unmodeled") or []],
      ["PLATING_POWER", "GORGET"])
check("R20 ctx 说明为什么不精确", bool(cout.get("unmodeled_why")), True)

# ---------------------------------------------------------------- R21 脆弱不重复打折（P15）
# 引擎 dynamic_values 里的 Block **已经**按脆弱砍过：全账本按 (牌, 升没升, 脆弱) 分组，
# 防御 5→3、耸肩 8→6、血墙 16→12、嘲讽 7→5、坚毅 7→5（一律 ×0.75 向下取整）。
# 旧 sim 又乘一次 0.75 → 入伤多算（run86 f12t2 预测 6、实际 4）。
FRAIL = [{"name": "Frail", "amount": 2}]
st_f = state([card("DEFEND_IRONCLAD", 1, blk=3, dyn={"Block": 3})], energy=3, hp=40,
             powers=FRAIL, enemies=[enemy(atk=9)])
ok_f, err_f, sm_f, steps_f = sim.validate(st_f, [{"c": "DEFEND_IRONCLAD"}, {"end": 1}])
check("R21 脆弱回合：引擎给 3 就起 3 甲", steps_f[0].get("blk"), 3)
check("R21 脆弱回合：入伤 9-3=6（不再 9-2=7）", sm_f.end_turn()["taken"], 6)
st_nf = state([card("DEFEND_IRONCLAD", 1, blk=5, dyn={"Block": 5})], energy=3, hp=40,
              enemies=[enemy(atk=9)])
ok_nf, err_nf, sm_nf, steps_nf = sim.validate(st_nf, [{"c": "DEFEND_IRONCLAD"}, {"end": 1}])
check("R21 无脆弱：引擎给 5 就起 5 甲", steps_nf[0].get("blk"), 5)
check("R21 无脆弱：入伤 9-5=4", sm_nf.end_turn()["taken"], 4)
# 批内新加的敏捷要补（快照值不含它），脆弱对这部分仍然生效
sm_dex = sim.Sim.from_state(st_f)
sm_dex.dex_bonus = 4
check("R21 批内敏捷照加（脆弱砍这部分：+3）", sm_dex._dex_block(), 3)
check("R21 没批内敏捷时不乱加", sim.Sim.from_state(st_nf)._dex_block(), 0)

# ---------------------------------------------------------------- R22 牌组上限 / 待选牌（P10）
import sts2  # noqa: E402

check("R22 act 是 1 基", sts2.run_act({"act": "1", "act_id": "0"}), 1)
check("R22 act_id 是 0 基 → 第二幕", sts2.run_act({"act": "2", "act_id": "1"}), 2)
check("R22 只有 act_id 也能对（run86 就缺 act）", sts2.run_act({"act_id": "1"}), 2)
check("R22 没有幕信息退回第一幕", sts2.run_act({}), 1)
check("R22 第二幕上限 18（旧代码按 16 算）", sts2.deck_cap(sts2.run_act({"act_id": "1"})), 18)
check("R22 第三幕上限 20（旧代码按 18 算）", sts2.deck_cap(sts2.run_act({"act_id": "2"})), 20)
check("R22 第一幕上限 16", sts2.deck_cap(sts2.run_act({"act_id": "0"})), 16)
# run86 f21/f22 的原始形状：skip 之后 rewards 里那张 Card 依然 claimable
SKIPPED = {"screen": "REWARD", "reward": {
    "pending_card_choice": False, "can_reward": True,
    "rewards": [{"reward_type": "Card", "claimable": True, "is_claimed": False}]}}
check("R22 skip 之后不再算「在等选牌」（旧判据死锁的根因）",
      sts2.pending_card_reward(SKIPPED), False)
check("R22 真在选牌时仍要拦",
      sts2.pending_card_reward({"screen": "REWARD",
                                "reward": {"pending_card_choice": True}}), True)
check("R22 字段缺失 + 有 card_options → 拦",
      sts2.pending_card_reward({"reward": {"card_options": [{"id": 1}]}}), True)
check("R22 字段缺失时退回旧判据（还有没领的 Card → 保守拦）",
      sts2.pending_card_reward({"reward": {"rewards": [{"reward_type": "Card",
                                                        "claimable": True}]}}), True)
check("R22 字符串 false 也当假",
      sts2.pending_card_reward({"reward": {"pending_card_choice": "false"}}), False)
check("R22 deck_size 优先数牌组列表",
      sts2.deck_size({"run": {"deck": [1, 2, 3], "deck_size": 9}}), 3)
check("R22 deck_size 没有列表时退回 deck_size",
      sts2.deck_size({"run": {"deck_size": 9}}), 9)
check("R22 字段缺失时报出真实字段名（run87 首个奖励界面要验）",
      "REWARD_KEYS" in sts2.reward_field_hint({"reward": {"rewards": [], "can_reward": True}}), True)
check("R22 字段在时不吵",
      sts2.reward_field_hint({"reward": {"pending_card_choice": False}}), "")

# ---------------------------------------------------------------- R23 清场后不许再排牌（P14）
# run86 f21t4：嘲讽→雷击→打击斩杀→焚身，第 4 步时引擎已在奖励界面，batch 只能 3/5 退出。
st_cl = state([card("STRIKE_IRONCLAD", 1, dmg=6, dyn={"Damage": 6}),
               card("DEFEND_IRONCLAD", 1, blk=5, dyn={"Block": 5})],
              energy=3, hp=40, enemies=[enemy(hp=5, atk=9)])
ok_c, err_c, sm_c, steps_c = sim.validate(st_cl, [{"c": "STRIKE_IRONCLAD"},
                                                  {"c": "DEFEND_IRONCLAD"}])
check("R23 记下「第 1 步清场」", sm_c.cleared_at, 0)
check("R23 清场后补牌 → 报 CLEAR_OVERFLOW",
      (act.clear_overflow_error(steps_c, sm_c.cleared_at) or "").startswith("CLEAR_OVERFLOW"), True)
ok_c2, err_c2, sm_c2, steps_c2 = sim.validate(st_cl, [{"c": "STRIKE_IRONCLAD"}, {"end": 1}])
check("R23 清场 + 尾部 end 不算越界", act.clear_overflow_error(steps_c2, sm_c2.cleared_at), None)
ok_c3, err_c3, sm_c3, steps_c3 = sim.validate(st_cl, [{"c": "DEFEND_IRONCLAD"}, {"end": 1}])
check("R23 没清场时不插手", act.clear_overflow_error(steps_c3, sm_c3.cleared_at), None)
check("R23 没清场时 cleared_at 为空", sm_c3.cleared_at, None)
dry = act._dry(st_cl, [{"c": "STRIKE_IRONCLAD"}, {"c": "DEFEND_IRONCLAD"}], {})
check("R23 --dry 也能提前看到越界", bool(dry.get("clear_overflow")), True)

# ---------------------------------------------------------------- R24 斩杀按 enemy_id 计数配对（P12）
import accuracy  # noqa: E402


def en(i, eid, hp=10, **kw):
    d = {"i": i, "id": eid, "n": eid, "hp": hp, "max": hp, "blk": 0}
    d.update(kw)
    return d


def krec(kill, before, after, clear=None, screen="COMBAT"):
    fc = {"kill": list(kill)}
    if clear:
        fc["clear"] = clear
    return {"floor": 9, "turn": 1, "outcome": "executed", "ended": 1,
            "forecast": fc,
            "before": {"enemies": before, "me": {"hp": 50}},
            "after": {"enemies": after, "me": {"hp": 40}, "screen": screen}}


# run86 f22t2 的形状：两只同 id 的蛋，杀一只 → 幸存者重新编号成 0
c1 = accuracy.kill_cases(krec([0], [en(0, "BOWLBUG_EGG"), en(1, "BOWLBUG_EGG")],
                              [en(0, "BOWLBUG_EGG")]))
check("R24 同种怪杀一只 → 计数确认", [x["verdict"] for x in c1], ["confirmed"])
check("R24 旧口径按下标会误报假斩杀", [x["old"] for x in c1], ["false"])
c2 = accuracy.kill_cases(krec([0, 1], [en(0, "RAT"), en(1, "RAT")], [en(0, "RAT")]))
check("R24 两只里只死一只：第二条是真假斩杀", [x["verdict"] for x in c2], ["confirmed", "false"])
c3 = accuracy.kill_cases(krec([0], [en(0, "A"), en(1, "B")], [en(0, "A")]))
check("R24 打错了 id → 假斩杀", [x["verdict"] for x in c3], ["false"])
c4 = accuracy.kill_cases(krec([0], [en(0, "A")], [], clear=1, screen="REWARD"))
check("R24 清场 + 战斗结束 → 确认", [x["verdict"] for x in c4], ["confirmed"])
c5 = accuracy.kill_cases({"floor": 9, "turn": 5, "outcome": "executed", "ended": 1,
                          "forecast": {"kill": [0]},
                          "before": {"enemies": [en(0, "THIEVING_HOPPER")], "me": {"hp": 50}},
                          "after": {"screen": "REWARD", "me": {"hp": 61}}})
check("R24 逃跑（无清场证据）→ 无法判定，不硬凑", [x["verdict"] for x in c5], ["undecidable"])
c6 = accuracy.missed_cases(krec([], [en(0, "MINION_SLIME", b=[{"id": "MINION_POWER"}]),
                                    en(1, "BOSS")], [en(0, "BOSS")]))
check("R24 爪牙随主人撤场不算漏杀", c6["minion"], [("MINION_SLIME", 1)])
c7 = accuracy.missed_cases(krec([], [en(0, "A")], [en(0, "B")]))
check("R24 少了一只却没预测 → 未预测死亡", c7["miss"], [("A", 1)])
c8 = accuracy.missed_cases(krec([], [en(0, "A")], [], screen="REWARD"))
check("R24 战斗结束造成的消失不算漏杀", c8["ended"], True)

# ---------------------------------------------------------------- R25 入伤口径边界（P13）
def trec(pred_take, b_hp, a_hp, screen="COMBAT", self_hp=None, clear=None, pred_hp=None):
    fc = {"take": pred_take, "hp": a_hp if pred_hp is None else pred_hp}
    if self_hp is not None:
        fc["self"] = self_hp
    if clear:
        fc["clear"] = 1
    return {"floor": 1, "turn": 1, "outcome": "executed", "ended": 1,
            "forecast": fc,
            "before": {"me": {"hp": b_hp}},
            "after": {"me": {"hp": a_hp}, "screen": screen}}


MIX = [trec(4, 64, 60),                       # 战斗内正常对账
       trec(0, 43, 49, screen="REWARD"),      # 清场后燃烧之血 +6
       trec(0, 55, 61, screen="REWARD"),      # 逃跑 + 战后回血（run86 f19t5）
       trec(29, 7, 0, screen="GAME_OVER"),    # 死亡夹到 0（run86 f23t5）
       trec(0, 72, 70, self_hp=2)]            # 血墙自扣 2（run86 f14t4）
inc, excl = accuracy.incoming_rows(MIX)
check("R25 只统计战斗内记录", len(inc), 2)
check("R25 战斗结束的 3 条进排除桶、不丢", sum(excl.values()), 3)
check("R25 自扣血减掉后入伤为 0", [x[2] for x in inc if x[3] == 2], [0])
hps, hexcl = accuracy.hp_rows(MIX)
check("R25 血量用同一批记录", len(hps), 2)
check("R25 血墙自扣血已经在 forecast.hp 里（不再二次补偿）",
      [x[1] - x[2] for x in hps if x[0]["forecast"].get("self") == 2], [0])
# ---------------------------------------------------------------- R26 过渡快照 → settled 读
# 账本 logs/decisions.jsonl（run 78DZXYQ5PL f1，mode=auto）：end 之后的过渡快照里
# hand=[] / energy=0，sim 只能算出「无牌可打」，于是把新回合的 3 费 5 张牌整个空过，
# 白吃 16 点（78→62），整局血线从这里开始崩。这条坑历史上修过三次
# （run30 空手 / run68 f17 Boss 空过 / run85 f11 误判），每次都是某个调用方绕过守卫。
def _combat_frame(hand_n, energy, mx=3):
    return {"screen": "COMBAT", "run": {"current_hp": 78, "max_hp": 94},
            "available_actions": ["play_card", "end_turn"],
            "combat": {"turn": 1,
                       "player": {"energy": energy, "max_energy": mx,
                                  "current_hp": 78, "block": 0},
                       "hand": [{"id": "STRIKE_IRONCLAD", "cost": 1} for _ in range(hand_n)],
                       "enemies": [{"id": "E0", "current_hp": 20, "intent": "ATTACK"}]}}


_orig_gs, _orig_wait = sts2.get_state, sts2.wait_new_turn_hand
_waits = {"n": 0}


def _fake_wait(prev_hand_len, timeout=4.0):
    _waits["n"] += 1
    return _combat_frame(5, 3)   # 等到的就是「抽完牌的玩家回合」


sts2.wait_new_turn_hand = _fake_wait
_ready = _combat_frame(5, 3)

# 1) hand=0 的过渡帧 → 必须等到真手牌
_waits["n"] = 0
_seen = {"i": 0}
sts2.get_state = lambda *a, **k: (_combat_frame(0, 0) if _seen["i"] == 0 else _ready)
st = sts2.get_state_settled()
check("R26 过渡快照(hand=0) 必须等到真手牌", len(st["combat"]["hand"]), 5)
check("R26 过渡快照确实走了等待分支", _waits["n"] >= 1, True)

# 2) 只读到 1 张也算过渡（run95 的另一半：5 张手牌被读成 1 张）
_waits["n"] = 0
_seen["i"] = 0
sts2.get_state = lambda *a, **k: (_combat_frame(1, 0) if _seen["i"] == 0 else _ready)
st = sts2.get_state_settled()
check("R26 只读到 1 张也等", len(st["combat"]["hand"]), 5)

# 3) 正常手牌（≥2 张）不许等，别给每回合加延迟
_waits["n"] = 0
sts2.get_state = lambda *a, **k: _ready
st = sts2.get_state_settled()
check("R26 手牌正常时不等待", _waits["n"], 0)
check("R26 正常帧原样返回", len(st["combat"]["hand"]), 5)

# 4) 非战斗界面不碰
sts2.get_state = lambda *a, **k: {"screen": "MAP"}
check("R26 非战斗直接返回", sts2.get_state_settled().get("screen"), "MAP")
sts2.get_state, sts2.wait_new_turn_hand = _orig_gs, _orig_wait

# 接线检查：守卫只留一处，别再让某个调用方绕过去
_act_src = open(os.path.join(ROOT, "tools", "act.py"), encoding="utf-8").read()
_ctx_src = open(os.path.join(ROOT, "tools", "ctx.py"), encoding="utf-8").read()
check("R26 act.py 的 auto 走 settled 读", "get_state_settled()" in _act_src, True)
check("R26 ctx.py 的 CLI 走 settled 读", "get_state_settled()" in _ctx_src, True)
check("R26 ctx.py 不再自己手写等待", "wait_new_turn_hand(" in _ctx_src, False)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")