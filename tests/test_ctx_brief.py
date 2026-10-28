"""局面包自检：战斗包带一行账（calc.line），非战斗包带 choice 菜单。"""
from __future__ import annotations

from dafeiyu.games.registry import GameRegistry

ctx = GameRegistry().load_game_module("slay-the-spire-2", "decision.ctx")
assert ctx is not None


def _card(cid, cost, dmg=None, blk=None):
    dv = []
    if dmg is not None:
        dv.append({"name": "Damage", "current_value": dmg})
    if blk is not None:
        dv.append({"name": "Block", "current_value": blk})
    return {"card_id": cid, "name": cid, "energy_cost": cost, "upgraded": False,
            "playable": True, "requires_target": dmg is not None,
            "target_type": "AnyEnemy", "dynamic_values": dv, "resolved_rules_text": ""}


def _combat(hp=60, energy=3):
    return {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn"],
        "run": {"current_hp": hp, "max_hp": 80, "max_energy": 3, "floor": 4,
                "act_id": 0, "ascension": 10, "gold": 99,
                "deck": [{"card_id": "STRIKE_IRONCLAD", "name": "打击",
                          "energy_cost": 1}] * 12,
                "relics": [{"relic_id": "BURNING_BLOOD", "name": "燃烧之血",
                            "description": "战斗结束后回复6点生命。"}],
                "potions": []},
        "combat": {"turn": 2,
                   "player": {"current_hp": hp, "max_hp": 80, "energy": energy,
                              "block": 0, "powers": []},
                   "hand": [_card("BASH", 2, dmg=8), _card("STRIKE_IRONCLAD", 1, dmg=6),
                            _card("DEFEND_IRONCLAD", 1, blk=5)],
                   "enemies": [{"index": 0, "enemy_id": "JAW_WORM", "name": "JAW_WORM",
                                "current_hp": 44, "max_hp": 44, "block": 0,
                                "is_alive": True, "powers": [],
                                "intents": [{"intent_type": "ATTACK", "damage": 11,
                                             "hits": 1, "total_damage": 11}]}]},
        "agent_view": {"combat": {"draw_cards": [], "discard_cards": [],
                                  "exhaust_cards": []}},
    }


def test_combat_brief_has_one_line_account():
    brief = ctx.build(_combat())
    line = (brief.get("calc") or {}).get("line")
    assert isinstance(line, str) and line, "calc.line 缺失"
    assert "血60/80" in line
    assert "本回合最多挡5" in line
    assert "挨11" in line and "净6" in line
    assert "下轮挨" in line
    assert len(brief.get("rules") or []) == 5


def test_combat_brief_marks_lethal_line():
    state = _combat(hp=30)
    state["combat"]["enemies"][0]["current_hp"] = 12
    state["combat"]["enemies"][0]["max_hp"] = 12
    brief = ctx.build(state)
    assert (brief.get("calc") or {}).get("lethal") == [0]
    assert "杀线0" in (brief.get("calc") or {}).get("line", "")


def test_noncombat_brief_has_menu_and_no_calc():
    state = {
        "screen": "MAP", "run_id": "r1",
        "run": {"floor": 6, "act_id": "0", "current_hp": 57, "max_hp": 80, "gold": 131,
                "deck": [{"card_id": "STRIKE_IRONCLAD", "name": "打击",
                          "energy_cost": 1}], "relics": [], "potions": []},
        "available_actions": ["save_and_quit", "choose_map_node"],
        "map": {"nodes": [{"row": 0, "col": 0, "node_type": "Monster",
                           "is_available": True, "children": []}]},
    }
    brief = ctx.build(state)
    assert brief.get("screen") == "MAP"
    assert brief.get("choice", {}).get("opts")
    assert "calc" not in brief
    assert len(brief.get("rules") or []) == 5
