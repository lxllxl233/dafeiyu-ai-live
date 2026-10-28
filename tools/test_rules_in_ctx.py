# -*- coding: utf-8 -*-
"""硬闸注入自测：5 条规则必须随每份局面包进入模型输入。

背景（2026-10-28 审计 run96–run106）：规则只写在决策手册里，靠一次性
加载；会话被压缩 20 次后规则掉出上下文，run104 T2（21 点不挡）、run105 f7
（30% 血进精英）都发生在规则不在输入里的回合。

本测试守三件事：
  1. ctx 战斗 / 非战斗局面包都带 `rules`，内容与 tools/hard_rules.py 逐字一致；
  2. 构建过程**不读任何知识库文件**——手册不重载、被压缩都不影响注入；
  3. 决策手册（decision/knowledge/ironclad/OS.md）与 hard_rules.py 没漂移。

用法：python tools/test_rules_in_ctx.py
"""
from __future__ import annotations

import builtins
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

os.environ.setdefault("STS2_DECISION_LOG", "0")

import ctx  # noqa: E402
import act  # noqa: E402
import sts2  # noqa: E402
import hard_rules  # noqa: E402

# 别把测试写进真实的 intent_memory.json
ctx.MEM_PATH = os.path.join(tempfile.gettempdir(), "sts2_test_rules_mem.json")

SKILL = os.path.join(ROOT, "games", "slay-the-spire-2", "decision", "knowledge",
                     "ironclad", "OS.md")

ok = True


def check(name, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def card(cid, cost, dmg=None, blk=None, hp=None):
    dv = []
    for k, v in (("Damage", dmg), ("Block", blk), ("HpLoss", hp)):
        if v is not None:
            dv.append({"name": k, "current_value": v})
    return {"card_id": cid, "name": cid, "energy_cost": cost, "upgraded": False,
            "playable": True, "requires_target": dmg is not None, "target_type": "AnyEnemy",
            "dynamic_values": dv, "resolved_rules_text": ""}


def make_combat(hp=62, energy=3):
    st = {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn"],
        "run": {"current_hp": hp, "max_hp": 80, "max_energy": 3, "floor": 4, "act_id": 0,
                "ascension": 10, "gold": 99,
                "deck": [{"card_id": "STRIKE_IRONCLAD", "name": "打击", "energy_cost": 1}] * 12,
                "relics": [{"relic_id": "BURNING_BLOOD", "name": "燃烧之血",
                            "description": "战斗结束后回复6点生命。"}],
                "potions": [{"index": 0, "potion_id": "FIRE_POTION", "name": "火焰药水",
                             "occupied": True, "description": "对一名敌人造成20点伤害。",
                             "can_use": True, "requires_target": True}]},
        "combat": {"turn": 2,
                   "player": {"current_hp": hp, "max_hp": 80, "energy": energy, "block": 0,
                              "powers": []},
                   "hand": [card("BASH", 2, dmg=8), card("STRIKE_IRONCLAD", 1, dmg=6),
                            card("DEFEND_IRONCLAD", 1, blk=5)],
                   "enemies": [{"index": 0, "enemy_id": "JAW_WORM", "name": "JAW_WORM",
                                "current_hp": 44, "max_hp": 44, "block": 0, "is_alive": True,
                                "powers": [],
                                "intents": [{"intent_type": "ATTACK", "damage": 11, "hits": 1,
                                             "total_damage": 11}]}]},
        "agent_view": {"combat": {"draw_cards": [], "discard_cards": [], "exhaust_cards": []}},
    }
    return st


def make_map(hp=24):
    return {
        "screen": "MAP",
        "run": {"current_hp": hp, "max_hp": 80, "max_energy": 3, "floor": 7, "act_id": 0,
                "ascension": 10, "gold": 120, "deck": [], "relics": [], "potions": []},
        "map": {"nodes": [
            {"node_type": "Elite", "is_available": True, "row": 7, "col": 1, "children": []},
            {"node_type": "Rest", "is_available": True, "row": 7, "col": 3, "children": []},
        ]},
    }


print("=== 1) 战斗局面包必须带 5 条硬闸 ===")
st = make_combat()
d = ctx.build_with_mem(st, {})
check("有 rules 字段", "rules" in d, True)
check("rules_v", d.get("rules_v"), hard_rules.VERSION)
check("条数=5", len(d.get("rules") or []), 5)
check("id 顺序", [r["id"] for r in d["rules"]], list(hard_rules.ids()))
check("文本与唯一来源逐字一致",
      [r["t"] for r in d["rules"]], [r["t"] for r in hard_rules.RULES])
check("未把 src/anchor 带进上下文", any(k in d["rules"][0] for k in ("src", "anchor")), False)
blk = json.dumps({"rules_v": d["rules_v"], "rules": d["rules"]}, ensure_ascii=False,
                 separators=(",", ":"))
print("  rules 块", len(blk), "字节")
check("注入预算 <500 字节", len(blk) < 500, True)

print()
print("=== 2) 非战斗局面包同源（血闸在选路时也要在）===")
dm = ctx.build(make_map(hp=24))
check("MAP 也有 rules", "rules" in dm, True)
check("与战斗同源", dm.get("rules"), d.get("rules"))
check("MAP 血量确实低（run105 场景）", (dm.get("run") or {}).get("hp"), 24)

print()
print("=== 3) 不依赖手册重载：构建时读知识库文件就失败 ===")
_real_open = builtins.open


def _guard(file, *a, **k):
    p = str(file).replace("\\", "/")
    if "/knowledge/" in p or "OS.md" in p:
        raise AssertionError(f"构建局面包时读了知识库文件：{p}")
    return _real_open(file, *a, **k)


builtins.open = _guard
try:
    d2 = ctx.build_with_mem(make_combat(), {})
    ok3 = True
    err3 = ""
except AssertionError as e:  # pragma: no cover
    ok3, err3, d2 = False, str(e), {}
finally:
    builtins.open = _real_open
check("构建期间零手册读取", ok3, True)
if not ok3:
    print("  ", err3)
check("仍带 5 条", len((d2 or {}).get("rules") or []), 5)

src = open(hard_rules.__file__, encoding="utf-8").read()
check("hard_rules 无文件 IO（不 open/不读 md）",
      ("open(" in src or "read_text" in src or "Path(" in src), False)

print()
print("=== 4) 真实决策入口 act 的返回体也带 ===")
sts2.get_state = lambda: make_combat()
c = act._next_ctx()
check("act._next_ctx 带 rules", len(c.get("rules") or []), 5)
check("act 与 ctx 同源", c.get("rules"), d.get("rules"))

print()
print("=== 5) 决策手册与硬闸没漂移 ===")
text = open(SKILL, encoding="utf-8").read()
bad = hard_rules.verify_skill(text)
check("锚点全部还在手册里", bad, [])
check("手册标注了唯一来源", "games/slay-the-spire-2/decision/hard_rules.py" in text, True)

print()
print("=== 6) 唯一来源自检 ===")
check("validate() 无错", hard_rules.validate(), [])
check("每条 ≤60 字", [len(r["t"]) > 60 for r in hard_rules.RULES], [False] * 5)

print()
print("=== 7) 自伤闸落到计划层（plans[].self / warn SELF_HP）===")


def low_hp_with(cid, hp=6):
    st = make_combat(hp=hp)
    st["combat"]["hand"] = [card(cid, 1, dmg=12, blk=5), card("DEFEND_IRONCLAD", 1, blk=5),
                            card("STRIKE_IRONCLAD", 1, dmg=6)]
    st["run"]["current_hp"] = hp
    return st


dl = ctx.build_with_mem(low_hp_with("BLOOD_WALL"), {})
flagged = [p for p in dl.get("plans") or [] if p.get("self")]
check("8% 血手握血墙 → 有计划被标 self", bool(flagged), True)
check("标的理由写出血量", all("血" in (p.get("self_why") or "") for p in flagged), True)
check("warn 里有 SELF_HP", any("SELF_HP" in w for w in (dl.get("warn") or [])), True)
dh = ctx.build_with_mem(low_hp_with("BLOOD_WALL", hp=60), {})
check("75% 血不打自伤标记", [p for p in dh.get("plans") or [] if p.get("self")], [])
check("75% 血没有 SELF_HP warn",
      [w for w in (dh.get("warn") or []) if "SELF_HP" in w], [])

print()
print("ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
