# -*- coding: utf-8 -*-
"""离线自测：造几个假战斗，验证 advisor 的算术与台词。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import advisor  # noqa: E402


def card(cid, cost, dmg=None, hits=1, block=None, vuln=None, selfdmg=None, text="", target=""):
    dv = []
    if dmg is not None:
        dv.append({"name": "Damage", "current_value": dmg})
    if block is not None:
        dv.append({"name": "Block", "current_value": block})
    if vuln is not None:
        dv.append({"name": "VulnerablePower", "current_value": vuln})
    if selfdmg is not None:
        dv.append({"name": "HpLoss", "current_value": selfdmg})
    c = {"card_id": cid, "name": cid, "energy_cost": cost, "upgraded": False,
         "dynamic_values": dv, "resolved_rules_text": text, "target": target}
    if hits > 1:
        c["resolved_rules_text"] = (text or f"造成{dmg}点伤害{2 if hits == 2 else 3}次。")
    return c


def enemy(name, hp, atk, hits=1):
    return {"enemy_id": name, "name": name, "current_hp": hp, "hp": hp, "block": 0,
            "powers": [], "intents": [{"intent_type": "ATTACK", "damage": atk,
                                       "hits": hits, "total_damage": atk * hits}]}


def run(name, hand, enemies, hp=46, energy=3, block=0):
    state = {"screen": "COMBAT", "available_actions": ["play_card", "end_turn"],
             "run": {"current_hp": hp, "max_hp": 77},
             "combat": {"player": {"current_hp": hp, "energy": energy, "block": block, "powers": []},
                        "hand": hand, "enemies": enemies}}
    plan = advisor.build_plan(state)
    print(f"== {name}")
    print(advisor.render(plan))
    for ln in advisor.candidates(plan):
        print("  LINE", ln)


run("单怪高攻，该斩杀",
    [card("BASH", 2, dmg=10, vuln=3), card("HEMOKINESIS", 1, dmg=20, selfdmg=2),
     card("STRIKE_IRONCLAD", 1, dmg=6), card("DEFEND_IRONCLAD", 1, block=5)],
    [enemy("BEAST", 30, 12)])

run("三只小怪，该 AOE",
    [card("STOMP", 3, dmg=12, text="对所有敌人造成12点伤害。", target="AllEnemies"),
     card("STRIKE_IRONCLAD", 1, dmg=6), card("DEFEND_IRONCLAD", 1, block=5)],
    [enemy("A", 14, 6), enemy("B", 14, 6), enemy("C", 14, 6)])

run("挡不住，只能挡一半",
    [card("DEFEND_IRONCLAD", 1, block=5), card("DEFEND_IRONCLAD", 1, block=5),
     card("STRIKE_IRONCLAD", 1, dmg=6)],
    [enemy("BIG", 60, 26)], hp=30)

run("空场倒计时，全输出",
    [card("HEMOKINESIS", 1, dmg=20, selfdmg=2), card("STRIKE_IRONCLAD", 1, dmg=6)],
    [enemy("IDLE", 50, 0)])
