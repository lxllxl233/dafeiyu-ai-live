# -*- coding: utf-8 -*-
"""回放审计自测：判定函数必须能被手写样例证否，否则报告里的数字没意义。

守的事：
  1. 事实提取：从原始 state 抽 hp/入伤/手牌/节点，不依赖 mod；
  2. 规则判定：dmg/self/hp_entry/boss/potion 各给「触发/不触发 + 违反/不违反」样例；
  3. 违规率聚合：applies/violated 计数与明细行数一致；
  4. 模拟一致性：一致 / 保守 / 危险 / 预测死但没活下来 / 回血跳过 五种分类；
  5. 今天重放：历史「执行」的牌序不能被误拒；历史「被护栏拒」的牌序今天仍被拒；
  6. allow 入账：带 allow 的行不能再算「护栏漏」。

用法：python tools/test_replay_audit.py
"""
from __future__ import annotations

import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("STS2_DECISION_LOG", "0")
os.environ.setdefault("STS2_DECISION_STATE", "0")

import replay_audit as ra  # noqa: E402

ok = True


def check(name, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def state(hp=50, max_hp=80, enemies=None, hand=None, potions=0, nodes=None, turn=1,
          block=0, energy=3, it_dmg=9, it_hits=1, screen="COMBAT"):
    enemies = enemies if enemies is not None else [("JAW_WORM", 44, 44)]
    hand = hand if hand is not None else ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"]
    return {
        "screen": screen,
        "turn": turn,
        "run": {"current_hp": hp, "max_hp": max_hp, "act": 1, "floor": 7,
                "deck": [], "relics": [], "gold": 10,
                "potions": [{"index": i, "potion_id": "FIRE_POTION" if i < potions else None,
                             "occupied": i < potions} for i in range(2)]},
        "combat": {
            "turn": turn,
            "player": {"current_hp": hp, "max_hp": max_hp, "energy": energy, "block": block,
                       "powers": []},
            "hand": [{"card_id": c, "energy_cost": 1, "playable": True} for c in hand],
            "enemies": [{"enemy_id": eid, "name": eid, "current_hp": ehp, "max_hp": emax,
                         "is_alive": ehp > 0, "powers": [],
                         "intents": [{"intent_type": "Attack", "damage": it_dmg,
                                      "hits": it_hits, "total_damage": it_dmg * it_hits}]}
                        for eid, ehp, emax in enemies],
        },
        "map": {"nodes": [{"node_type": t, "is_available": True} for t in (nodes or [])]},
    }


def row(st, seq=None, fx=None, steps=None, forecast=None, outcome="executed",
        before_hp=None, after_hp=None, run_id="TESTRUN01"):
    """造一条账本行：before/after 只放审计用到的字段。"""
    b = {"me": {"hp": before_hp if before_hp is not None else st["combat"]["player"]["current_hp"],
                "max": 80}}
    a = {"me": {"hp": after_hp if after_hp is not None else b["me"]["hp"]}} if after_hp is not None else None
    r = {"ts": "2026-10-27T00:00:00", "run_id": run_id, "floor": st["run"]["floor"],
         "turn": st["turn"], "outcome": outcome, "state": st,
         "input": {"seq": seq if seq is not None else [{"end": 1}], "say": [], "fx": fx or {}},
         "steps": steps, "forecast": forecast, "before": b, "after": a}
    return r


print("=== 1) 事实提取 ===")
f = ra.facts_from_state(state(hp=24, enemies=[("TERROR_EEL", 150, 150)], hand=["INFLAME"],
                              it_dmg=6, it_hits=3, nodes=["Elite", "Monster"], turn=3))
check("hp", f["me"]["hp"], 24)
check("hp_pct", round(f["hp_pct"]), 30)
check("入伤=总伤害", f["incoming"], 18)
check("敌人 id", f["enemy_ids"], ["TERROR_EEL"])
check("敌人类别 elite", ra.enemy_rank(f["enemy_ids"]), "elite")
check("手牌", f["hand_ids"], ["INFLAME"])
check("节点", [o["t"] for o in f["choice"]["opts"]], ["Elite", "Monster"])
check("turn", f["combat"]["turn"], 3)
check("dead 敌人不计", ra.facts_from_state(state(enemies=[("A", 0, 10)]))["enemy_ids"], [])

print()
print("=== 2) 规则判定 ===")
st = state(hp=24, enemies=[("JAW_WORM", 44, 44)], it_dmg=16)
f = ra.facts_from_state(st)
r = row(st, forecast={"take": 16, "kill": [], "hp": 8})
check("dmg：入伤16 净掉16 无斩杀 → 违反", ra.rule_dmg(f, r, {"take": 16, "kill": []})[:2],
      (True, True))
check("dmg：净掉16 但有斩杀 → 不违反",
      ra.rule_dmg(f, r, {"take": 16, "kill": [0]})[:2], (True, False))
check("dmg：入伤10 不触发", ra.rule_dmg(ra.facts_from_state(state(it_dmg=10)), r,
                                       {"take": 10, "kill": []})[:2], (False, False))
check("dmg：清场不判",
      ra.rule_dmg(f, r, {"take": 16, "kill": [], "clear": 1})[:2], (True, False))

f_self = ra.facts_from_state(state(hp=24, hand=["BLOOD_WALL", "STRIKE_IRONCLAD"]))
r_self = row(state(hp=24), steps=[{"k": "c", "id": "BLOOD_WALL"}])
check("self：30% 血打血墙 → 违反", ra.rule_self(f_self, r_self)[:2], (True, True))
r_noself = row(state(hp=24), steps=[{"k": "c", "id": "STRIKE_IRONCLAD"}])
check("self：握着没打 → 不违反", ra.rule_self(f_self, r_noself)[:2], (True, False))
check("self：50% 血不触发",
      ra.rule_self(ra.facts_from_state(state(hp=40, hand=["BLOOD_WALL"])), r_self)[:2],
      (False, False))

st_e = state(hp=36, enemies=[("TERROR_EEL", 150, 150)])   # 45%
f_e = ra.facts_from_state(st_e)
r_e = row(st_e)
r_e["_fight_start"] = True
check("hp_entry：45% 进精英 → 反例", ra.rule_hp_entry(f_e, r_e)[:2], (True, True))
st_f = state(hp=72, enemies=[("TERROR_EEL", 150, 150)])   # 90%
r_f = row(st_f)
r_f["_fight_start"] = True
check("hp_entry：90% 进精英 → 不触发", ra.rule_hp_entry(ra.facts_from_state(st_f), r_f)[:2],
      (False, False))
st_b = state(hp=48, enemies=[("CEREMONIAL_BEAST", 262, 262)])  # 60%
r_b = row(st_b)
r_b["_fight_start"] = True
check("hp_entry：60% 进 Boss → 反例", ra.rule_hp_entry(ra.facts_from_state(st_b), r_b)[:2],
      (True, True))
r_b2 = row(st_b)
r_b2["_fight_start"] = False
check("hp_entry：不是首回合不判", ra.rule_hp_entry(ra.facts_from_state(st_b), r_b2)[:2],
      (False, False))

st_bs = state(hp=72, enemies=[("CEREMONIAL_BEAST", 262, 262)])  # 90%
r_bs = row(st_bs)
r_bs["_fight_start"] = True
check("boss：90% 进 Boss → 不违反", ra.rule_boss_hp(ra.facts_from_state(st_bs), r_bs)[:2],
      (True, False))
st_pot = state(hp=72, enemies=[("CEREMONIAL_BEAST", 262, 262)], potions=2)
r_pot = row(st_pot, steps=[{"k": "c", "id": "STRIKE_IRONCLAD"}])
r_pot["_fight_start"] = True
r_pot["_fight_potions"] = []
check("potion：带2瓶没喝 → 违反", ra.rule_potion(ra.facts_from_state(st_pot), r_pot)[:2],
      (True, True))
r_pot2 = copy.deepcopy(r_pot)
r_pot2["_fight_potions"] = ["FIRE_POTION"]
check("potion：喝过 → 不违反", ra.rule_potion(ra.facts_from_state(st_pot), r_pot2)[:2],
      (True, False))

print()
print("=== 3) 聚合：计数与明细一致 ===")
rows = [row(state(hp=24, it_dmg=16, enemies=[("JAW_WORM", 44, 44)]),
            forecast={"take": 16, "kill": []}, outcome="executed", before_hp=24, after_hp=8),
        row(state(hp=24, it_dmg=16, enemies=[("JAW_WORM", 44, 44)]),
            forecast={"take": 16, "kill": [0]}, outcome="executed", before_hp=24, after_hp=8)]
ra.annotate(rows)
res = ra.audit(rows, check_today=False)
check("行数", res["total"]["行数"], 2)
check("dmg 触发 2", res["total"].get("规则触发:dmg"), 2)
check("dmg 违反 1", res["total"].get("规则违反:dmg"), 1)
check("明细 1 条", len([v for v in res["violations"] if v["rule"] == "dmg"]), 1)
check("无效动作 0", sum(v for k, v in res["total"].items()
                        if k.startswith("历史:") and k != "历史:executed"), 0)

print()
print("=== 4) 模拟一致性分类 ===")
def gap(pred, got_before, got_after, outcome="executed", flags=None):
    st = state(hp=got_before)
    r = row(st, forecast=dict({"hp": pred, "take": got_before - pred}, **(flags or {})),
            outcome=outcome, before_hp=got_before, after_hp=got_after)
    return ra.sim_gap(r)

check("一致", gap(30, 40, 30)["cat"], "ok")
check("预测更惨 = 保守", gap(20, 40, 30)["cat"], "conservative")
check("预测更好 = 危险", gap(35, 40, 30)["cat"], "optimistic")
check("预测死且真死 = death", gap(-5, 10, 0)["cat"], "death")
check("预测死但没死 = death_missed", gap(-5, 10, 1)["cat"], "death_missed")
check("回血跳过", gap(30, 40, 55).get("skip"), "heal")
check("非 executed 不进对账", gap(30, 40, 30, outcome="rejected_fx").get("cmp", False), False)
check("clear 标记跳过", gap(30, 40, 30, flags={"clear": 1}).get("skip"), "clear")

print()
print("=== 5) 今天重放：不误拒历史执行、不放行历史护栏 ===")
st_ok = state(hp=40, enemies=[("JAW_WORM", 44, 44)], it_dmg=9,
              hand=["STRIKE_IRONCLAD", "STRIKE_IRONCLAD", "DEFEND_IRONCLAD"])
r_ok = row(st_ok, seq=[{"c": "STRIKE_IRONCLAD", "t": 0}, {"c": "STRIKE_IRONCLAD", "t": 0},
                       {"c": "DEFEND_IRONCLAD"}, {"end": 1}], outcome="executed")
v = ra.today_verdict(r_ok)
check("正常牌序今天放行", v["ok"], True)
check("正常牌序无护栏", v["guard"], None)

r_bad_seq = row(st_ok, seq=[{"c": "ATTACK_NONEXISTENT"}, {"end": 1}], outcome="rejected_validate")
v2 = ra.today_verdict(r_bad_seq)
check("不存在的牌今天仍拒", v2["ok"], False)

r_left = row(state(hp=40, hand=["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"], it_dmg=5),
             seq=[{"end": 1}], outcome="rejected_guard")
v3 = ra.today_verdict(r_left)
check("end 留费今天仍拦", bool(v3.get("guard")), True)
check("拦的原因是漏费", "漏费" in (v3.get("guard") or ""), True)

r_allow = copy.deepcopy(r_left)
r_allow["outcome"] = "executed"
r_allow["input"]["allow"] = {"allow_leftover": 1}
v4 = ra.today_verdict(r_allow)
check("写了 allow_leftover 就不拦", v4.get("guard"), None)
check("allow 已入账", v4.get("allow_known"), True)

print()
print("=== 6) allow 未入账的行单独计数（不冤枉护栏）===")
r_noallow = row(state(hp=40, hand=["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"], it_dmg=5),
                seq=[{"end": 1}], outcome="executed")
check("这行没有 allow 记录", "allow" in r_noallow["input"], False)
ra.annotate([r_noallow])
res = ra.audit([r_noallow], check_today=True)
check("计入疑漏", res["total"].get("护栏疑漏:allow 未入账"), 1)
check("不算今天新拦", res["total"].get("今天新拦:guard", 0), 0)

print()
print("=== 7) 真实账本冒烟（有就跑，没有跳过）===")
led = os.path.join(ROOT, "logs", "decisions.jsonl")
if os.path.exists(led):
    rr = ra.load_rows()
    rr = [x for x in rr if str(x.get("run_id")) == "5UMKQR2A1K"][:6]
    ra.annotate(rr)
    res = ra.audit(rr, check_today=True)
    check("真实行能跑完", res["total"]["行数"] > 0, True)
    check("规则每行 5 条", res["total"]["规则注入条数"] / max(1, res["total"]["行数"]), 5.0)
else:
    print("  （没有账本，跳过）")

print()
print("ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
