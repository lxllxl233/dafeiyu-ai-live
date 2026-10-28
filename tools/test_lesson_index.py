# -*- coding: utf-8 -*-
"""经验卡检索自测：指定敌人 / 血量 / 节点能不能命中**正确**的经验，无关回合能不能闭嘴。

守的事：
  1. 卡片自检：字段齐、`t`≤60 字、case 卡必须有 `verified:true` + 存在的账本文件坐标；
  2. 敌人命中：骇鳗 30% → 骇鳗卡；异蛙寄生虫 44% → 寄生虫卡；瀑布巨兽 → 爆炸卡；
  3. 血量命中：精英 ≤45% / Boss <70% / 入伤≥15 且 <45% 各命中对应卡；
  4. 血量边界：45% 命中、46% 不命中（阈值不是摆设）；
  5. 节点命中：MAP 精英节点 30% 命中、只有杂怪节点不命中；MAP 营火 55% 命中、80% 不命中；
     商店 30% 命中、REWARD 命中、SHOP 80% 不命中；
  6. 无关回合：90% 血打普通怪 = 0 条，且 ctx 里**没有** `lessons` 字段（不花字节）；
  7. 预算：命中再多也只给 3 条，JSON ≤420 字节；
  8. 未核实卡片（verified:false）**不注入**；未知条件键不崩也不乱命中；
  9. 注入到 ctx：战斗/非战斗都会带，且规则 5 条仍在、不互相挤掉。

用法：python tools/test_lesson_index.py
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

import ctx  # noqa: E402
import hard_rules  # noqa: E402
import lesson_index as li  # noqa: E402

ctx.MEM_PATH = os.path.join(tempfile.gettempdir(), "sts2_test_lesson_mem.json")

ok = True


def check(name, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def card(cid, cost, dmg=None, blk=None):
    dv = []
    for k, v in (("Damage", dmg), ("Block", blk)):
        if v is not None:
            dv.append({"name": k, "current_value": v})
    return {"card_id": cid, "name": cid, "energy_cost": cost, "upgraded": False,
            "playable": True, "requires_target": dmg is not None, "target_type": "AnyEnemy",
            "dynamic_values": dv, "resolved_rules_text": ""}


def enemy(eid, hp, max_hp=None, dmg=11, hits=1):
    return {"index": 0, "enemy_id": eid, "name": eid, "current_hp": hp,
            "max_hp": max_hp or hp, "block": 0, "is_alive": True, "powers": [],
            "intents": [{"intent_type": "ATTACK", "damage": dmg, "hits": hits,
                         "total_damage": dmg * hits}]}


def make_combat(hp=62, enemies=None, hand=None, potions=None):
    hand = hand if hand is not None else [card("BASH", 2, dmg=8), card("STRIKE_IRONCLAD", 1, dmg=6),
                                          card("DEFEND_IRONCLAD", 1, blk=5)]
    return {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn"],
        "run": {"current_hp": hp, "max_hp": 80, "max_energy": 3, "floor": 7, "act_id": 0,
                "ascension": 10, "gold": 99,
                "deck": [{"card_id": "STRIKE_IRONCLAD", "name": "打击", "energy_cost": 1}] * 12,
                "relics": [], "potions": potions or []},
        "combat": {"turn": 2,
                   "player": {"current_hp": hp, "max_hp": 80, "energy": 3, "block": 0,
                              "powers": []},
                   "hand": hand,
                   "enemies": enemies or [enemy("JAW_WORM", 44, dmg=11)]},
        "agent_view": {"combat": {"draw_cards": [], "discard_cards": [], "exhaust_cards": []}},
    }


def make_map(hp, nodes):
    return {
        "screen": "MAP",
        "run": {"current_hp": hp, "max_hp": 80, "max_energy": 3, "floor": 6, "act_id": 0,
                "ascension": 10, "gold": 120, "deck": [], "relics": [], "potions": []},
        "map": {"nodes": [{"node_type": t, "is_available": True, "row": 6, "col": i,
                           "children": []} for i, t in enumerate(nodes)]},
    }


def ids(matches):
    return [c["id"] for c in matches]


def facts(enemies=(), hp=62, screen="COMBAT", hand=(), node=(), incoming=None, potion=0,
          max_hp=80):
    f = {
        "screen": screen, "hp": hp, "max_hp": max_hp, "hp_pct": 100.0 * hp / max_hp,
        "act": 1, "floor": 7,
        "enemies": list(enemies),
        "enemy_types": sorted({t for t in (li.enemy_type(e) for e in enemies) if t}),
        "enemy_n": len(enemies), "hand": list(hand), "potions": potion, "node": list(node),
    }
    if incoming is not None:
        f["incoming"] = incoming
    return f


print("=== 1) 卡片自检：证据必须落地 ===")
cards = li.load()
check("卡片数 ≥ 15", len(cards) >= 15, True)
check("validate() 无错", li.validate(cards), [])
check("全部 verified（没核实的别进注入层）",
      [c["id"] for c in cards if not c.get("verified")], [])
check("t 都 ≤60 字", [c["id"] for c in cards if len(c["t"]) > 60], [])
check("case 卡都有账本坐标",
      [c["id"] for c in cards if c.get("kind") == "case"
       and not os.path.exists(os.path.join(ROOT, c["src"]["file"]))], [])

print()
print("=== 2) 敌人命中：指定敌人只出它自己的经验 ===")
m = li.match(facts(["TERROR_EEL"], hp=24))
check("骇鳗 30% 命中骇鳗卡", "L105_eel" in ids(m), True)
check("骇鳗 30% 也命中精英低血卡", "L100_elite_lowhp" in ids(m), True)
check("骇鳗卡排第一（越具体越前）", ids(m)[0], "L105_eel")
check("骇鳗 90% 不再命中骇鳗卡", "L105_eel" in ids(li.match(facts(["TERROR_EEL"], hp=72))), False)

m = li.match(facts(["PHROG_PARASITE"], hp=35))
check("异蛙寄生虫 44% 命中", "L100_phrog" in ids(m), True)
m = li.match(facts(["WATERFALL_GIANT"], hp=90))
check("瀑布巨兽命中爆炸卡", ids(m), ["L96_waterfall"])
m = li.match(facts(["LAGAVULIN_MATRIARCH"], hp=73))
check("乐加维林命中 DPS 卡", "L104_lagavulin" in ids(m), True)
check("乐加维林 91% 不命中 Boss 低血卡", "L98_beast" in ids(m), False)
m = li.match(facts(["CEREMONIAL_BEAST"], hp=39))
check("祭典野兽 49% 命中", "L98_beast" in ids(m), True)
check("祭典野兽 90% 不命中", "L98_beast" in ids(li.match(facts(["CEREMONIAL_BEAST"], hp=72))), False)
m = li.match(facts(["SKULKING_COLONY"], hp=25))
check("珊瑚群命中硬化外壳卡", "L101_colony" in ids(m), True)
m = li.match(facts(["NIBBIT", "NIBBIT"], hp=12))
check("小啃兽 15% 命中", "L97_nibbit" in ids(m), True)
m = li.match(facts(["INKLET", "INKLET", "INKLET"], hp=7))
check("墨宝 3 只 9% 命中残血多怪卡", "L102_inklet" in ids(m), True)

print()
print("=== 3) 血量命中：档位与边界 ===")
m = li.match(facts(["PHROG_PARASITE"], hp=36))   # 45%
check("精英 45% 命中低血精英卡", "L100_elite_lowhp" in ids(m), True)
m = li.match(facts(["PHROG_PARASITE"], hp=37))   # 46.25%
check("精英 46% 不命中低血精英卡", "L100_elite_lowhp" in ids(m), False)
m = li.match(facts(["JAW_WORM"], hp=24, incoming=18))
check("入伤 18 + 30% 命中不挡卡", "L105_unblocked" in ids(m), True)
m = li.match(facts(["JAW_WORM"], hp=24, incoming=14))
check("入伤 14 不命中不挡卡", "L105_unblocked" in ids(m), False)
m = li.match(facts(["JAW_WORM"], hp=60, incoming=20))
check("入伤 20 但 75% 血不命中（血线不符）", "L105_unblocked" in ids(m), False)

print()
print("=== 4) 手牌命中：血墙 / SPITE ===")
m = li.match(facts(["TERROR_EEL"], hp=6, hand=["BLOOD_WALL"], incoming=10))
check("6 血握血墙命中自伤闸卡", "L105_bloodwall" in ids(m), True)
m = li.match(facts(["TERROR_EEL"], hp=60, hand=["BLOOD_WALL"], incoming=10))
check("75% 血握血墙不命中", "L105_bloodwall" in ids(m), False)
m = li.match(facts(["TERROR_EEL"], hp=60, hand=["SPITE"]))
check("握 SPITE 命中模拟器虚高卡", "L105_spite" in ids(m), True)

print()
print("=== 5) 节点命中：MAP 精英 / 营火，商店，奖励 ===")
m = li.match(facts([], hp=24, screen="MAP", node=["Elite", "Monster"]))
check("MAP 30% 有精英节点命中", "L105_map_elite" in ids(m), True)
m = li.match(facts([], hp=24, screen="MAP", node=["Monster"]))
check("MAP 30% 只有杂怪不命中", ids(m), [])
m = li.match(facts([], hp=44, screen="MAP", node=["Rest", "Monster"]))
check("MAP 55% 有营火命中休息卡", "L99_rest" in ids(m), True)
m = li.match(facts([], hp=64, screen="MAP", node=["Rest"]))
check("MAP 80% 有营火不命中", ids(m), [])
m = li.match(facts([], hp=24, screen="SHOP"))
check("商店 30% 命中", "L105_shop" in ids(m), True)
m = li.match(facts([], hp=64, screen="SHOP"))
check("商店 80% 不命中", ids(m), [])
m = li.match(facts([], hp=36, screen="REWARD"))
check("低血奖励界面命中 skip 卡", ids(m), ["L103_reward"])
m = li.match(facts([], hp=72, screen="REWARD"))
check("满血奖励界面不念 skip（少刷屏）", ids(m), [])

print()
print("=== 6) 无关回合：一条都不给 ===")
m = li.match(facts(["JAW_WORM"], hp=72))
check("90% 血普通怪 0 条", ids(m), [])
d = ctx.build(make_combat(hp=72, enemies=[enemy("JAW_WORM", 44)]))
check("ctx 里没有 lessons 字段", "lessons" in d, False)
check("规则照旧 5 条", len(d.get("rules") or []), 5)

print()
print("=== 7) 预算：最多 3 条、≤420 字节 ===")
f = facts(["TERROR_EEL"], hp=6, hand=["BLOOD_WALL", "SPITE"], incoming=18)
m = li.match(f)
check("命中条数被截到 3", len(m), 3)
blob = json.dumps(li.render(m), ensure_ascii=False, separators=(",", ":"))
print("  lessons 块", len(blob.encode("utf-8")), "字节")
check("≤420 字节", len(blob.encode("utf-8")) <= li.MAX_BYTES, True)
check("render 结果 ≤3", len(li.render(m)), 3)

print()
print("=== 8) 未核实卡片与未知条件不外泄 ===")
bad_card = {"id": "L_test_unverified", "kind": "case", "when": {"enemy_any": ["TERROR_EEL"]},
            "t": "这条没核实", "verified": False,
            "src": {"run": "run105", "file": "logs/lessons.jsonl", "ts": "x"}}
check("verified:false 不注入", li.match(facts(["TERROR_EEL"], hp=24), [bad_card]), [])
weird = {"id": "L_test_weird", "kind": "case", "when": {"bogus_key": 1},
         "t": "未知条件", "verified": True,
         "src": {"run": "run105", "file": "logs/lessons.jsonl", "ts": "x"}}
check("未知条件键不命中", li.match(facts(["TERROR_EEL"], hp=24), [weird]), [])
check("validate 能抓到 verified:false 的 case 卡", li.validate([bad_card]) != [], True)
check("validate 能抓到未知条件键", li.validate([weird]) != [], True)

print()
print("=== 9) 注入到 ctx：战斗 / 非战斗都带，且不挤掉规则 ===")
d = ctx.build_with_mem(make_combat(hp=24, enemies=[enemy("TERROR_EEL", 150, 150)]), {})
mine = [x.get("id") for x in (d.get("lessons") or [])]
check("战斗 lessons 非空", len(mine) > 0, True)
check("骇鳗在战斗 lessons 里", "L105_eel" in mine, True)
check("战斗规则仍在", len(d.get("rules") or []), 5)
check("lessons_v", d.get("lesson_v"), li.VERSION)

dm = ctx.build(make_map(hp=24, nodes=["Elite", "Monster"]))
check("MAP 带 lessons", "L105_map_elite" in [x.get("id") for x in dm.get("lessons") or []], True)
check("MAP 规则仍在", len(dm.get("rules") or []), 5)

# 真实入口 act 返回的 ctx 也要带（模型每回合读的是它，不是直接调 ctx.build）
import act  # noqa: E402
import sts2  # noqa: E402

sts2.get_state = lambda: make_combat(hp=24, enemies=[enemy("TERROR_EEL", 150, 150)])
c = act._next_ctx()
check("act 返回体也带 lessons", "L105_eel" in [x.get("id") for x in c.get("lessons") or []], True)
check("act 返回体 rules 仍是 5 条", len(c.get("rules") or []), 5)

# 字节账：命中时多花多少（一回合的预算意识）
plain = json.dumps(ctx.build(make_combat(hp=72)), ensure_ascii=False, separators=(",", ":"))
hit = json.dumps(ctx.build(make_combat(hp=24, enemies=[enemy("TERROR_EEL", 150, 150)])),
                 ensure_ascii=False, separators=(",", ":"))
blk_plain = json.dumps({"lessons": []}, ensure_ascii=False)
print(f"  ctx 字节：无经验 {len(plain.encode('utf-8'))} / 有经验 {len(hit.encode('utf-8'))}")
check("经验块本身 ≤420 字节",
      len(json.dumps({"lessons": d.get("lessons")}, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")) <= li.MAX_BYTES + 20, True)

print()
print("=== 10) 关掉开关就不注入（回溯用）===")
os.environ["STS2_LESSONS"] = "0"
try:
    check("STS2_LESSONS=0 → 空", li.for_ctx({"screen": "COMBAT", "me": {"hp": 24, "max": 80},
                                             "enemies": [{"id": "TERROR_EEL"}]}), {})
finally:
    os.environ.pop("STS2_LESSONS", None)

print()
print("=== 11) 重复调用结果稳定（缓存/排序不能抖）===")
a = ids(li.match(facts(["TERROR_EEL"], hp=24, hand=["BLOOD_WALL"], incoming=18)))
b = ids(li.match(facts(["TERROR_EEL"], hp=24, hand=["BLOOD_WALL"], incoming=18)))
check("两次一致", a, b)
check("具体卡在前", a[0], "L105_eel")

print()
print("=== 12) 账本回放：真实 before 快照能不能命中（不是合成数据）===")
LEDGER = os.path.join(ROOT, "logs", "decisions.jsonl")
REPLAY = [
    ("run105", "5UMKQR2A1K", "2026-10-27T23:49:10", "L105_eel"),      # 24/80 进骇鳗
    ("run96", "BSZ3HC4QR4", "2026-10-27T21:28:11", "L96_waterfall"),  # 45/80 进 Boss
    ("run97", "V020HZGZTM", "2026-10-27T21:44:23", "L97_nibbit"),     # 12/80 进双啃兽
    ("run98", "NL379UQDMC", "2026-10-27T22:09:07", "L98_beast"),      # 39/80 进 Boss
    ("run100", "5M1JDNLKXN", "2026-10-27T22:36:32", "L100_phrog"),    # 35/80 进精英
    ("run101", "5V1DCM61ZS", "2026-10-27T22:52:27", "L101_colony"),   # 25/80 进精英
    ("run102", "108VPCY1CE", "2026-10-27T23:11:40", "L102_inklet"),   # 7/80 进双怪
    ("run103", "EXH7P5V56J", "2026-10-27T23:27:30", "L103_gardener"), # 24/80 进 4 怪
    ("run104", "M8D7WN3957", "2026-10-27T23:40:42", "L104_lagavulin"),  # 73/80 进 Boss
]
if os.path.exists(LEDGER):
    rows = [json.loads(l) for l in open(LEDGER, encoding="utf-8") if l.strip()]
    idx = {(str(r.get("run_id")), r.get("ts")): r for r in rows}
    for name, rid, ts, want in REPLAY:
        r = idx.get((rid, ts))
        if not r:
            check(f"{name} {ts} 在账本里", False, True)
            continue
        b = r.get("before") or {}
        me = b.get("me") or {}
        f = facts([e.get("id") for e in (b.get("enemies") or []) if isinstance(e, dict)],
                  hp=me.get("hp"), max_hp=me.get("max"),
                  hand=[c.get("id") for c in (b.get("hand") or []) if isinstance(c, dict)])
        check(f"{name} 回放命中 {want}", want in ids(li.match(f)), True)

    # 反向：健康回合不许命中（run105 f2 64/80 打杂怪）
    r = idx.get(("5UMKQR2A1K", "2026-10-27T23:43:57"))
    if r:
        b = r.get("before") or {}
        me = b.get("me") or {}
        f = facts([e.get("id") for e in (b.get("enemies") or []) if isinstance(e, dict)],
                  hp=me.get("hp"), max_hp=me.get("max"))
        check("run105 f2 健康回合 0 条", ids(li.match(f)), [])
else:
    print("  （没有 logs/decisions.jsonl，跳过回放）")

print()
print("ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
