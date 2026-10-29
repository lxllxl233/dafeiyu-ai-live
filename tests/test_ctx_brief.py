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


def test_combat_brief_has_one_line_account(monkeypatch):
    # 下轮意图来自 logs/intent_memory.json 的历史；测试不能依赖真实对局留下的记忆
    # （key 里的 run_id 是 "?" 时条目会随测试运行累积，断言就会时好时坏）。
    mem = {"?|4|JAW_WORM|0": {"h": ["A11", "A11"], "t": 2}}
    monkeypatch.setattr(ctx, "_load_mem", lambda: {k: dict(v) for k, v in mem.items()})
    monkeypatch.setattr(ctx, "_save_mem", lambda m: None)
    brief = ctx.build(_combat())
    line = (brief.get("calc") or {}).get("line")
    assert isinstance(line, str) and line, "calc.line 缺失"
    assert "血60/80" in line
    assert "本回合最多挡5" in line
    assert "挨11" in line and "净6" in line
    assert "下轮挨" in line
    assert len(brief.get("rules") or []) == 5


def test_boss_major_hit_warns_to_drink_potion():
    """Boss 战净掉 ≥12% 最大生命且手里有药水：ctx 要直接点名槽位。
    执行层 act._guard_potion 会拦「空手过」，观察侧先给一次纠偏机会。"""
    state = _combat(hp=70)
    state["run"]["boss_id"] = "JAW_WORM_BOSS"
    state["run"]["potions"] = [{"index": 0, "occupied": True,
                                "potion_id": "BLOCK_POTION", "name": "格挡药水",
                                "description": "获得12点格挡。", "can_use": True,
                                "requires_target": False}]
    state["combat"]["enemies"][0]["intents"][0].update({"damage": 20,
                                                       "total_damage": 20})
    warns = ctx.build(state).get("warn") or []
    assert any("POTION" in w and "槽0" in w for w in warns), warns


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


def test_finished_event_brief_says_reward_already_granted():
    """涅奥这类事件结算完只剩「继续」；ctx 要写明奖励已进 relics/deck，
    否则模型会照着空选项说「涅奥没给好处/没拿」（实机反馈）。"""
    state = {
        "screen": "EVENT", "run_id": "r-neow",
        "run": {"floor": 1, "act_id": "0", "current_hp": 64, "max_hp": 80, "gold": 99,
                "deck": [{"card_id": "STRIKE_IRONCLAD", "name": "打击",
                          "energy_cost": 1}],
                "relics": [{"relic_id": "HEFTY_TABLET", "name": "沉重石板"}],
                "potions": []},
        "available_actions": ["choose_event_option"],
        "event": {"event_id": "NEOW", "title": "涅奥", "is_finished": True,
                  "options": [{"title": "Proceed", "is_proceed": True}]},
    }
    choice = ctx.build(state)["choice"]
    assert choice["done"] is True
    assert "relics" in choice["note"]


def test_reward_brief_does_not_claim_there_is_no_card():
    """奖励屏只有一个「Card」条目时 card_options 必然是空的；ctx 不能因此说
    「没有卡牌选项」——模型会照着说「奖励没牌可拿/没拿」（实机反馈）。"""
    state = {
        "screen": "REWARD", "run_id": "r-reward",
        "run": {"floor": 15, "act_id": "0", "current_hp": 40, "max_hp": 80, "gold": 120,
                "deck": [{"card_id": "STRIKE_IRONCLAD", "name": "打击",
                          "energy_cost": 1}] * 17, "relics": [], "potions": []},
        "available_actions": ["claim_reward", "collect_rewards_and_proceed"],
        "reward": {"pending_card_choice": False, "card_options": [],
                   "rewards": [{"index": 0, "reward_type": "Gold",
                                "description": "7金币", "claimable": True},
                               {"index": 1, "reward_type": "Card",
                                "description": "将一张牌添加到你的牌组。",
                                "claimable": True}]},
    }
    brief = ctx.build(state)
    choice = brief["choice"]
    card_item = [x for x in choice["items"] if x["t"] == "Card"][0]
    assert card_item.get("claim_first") is True
    ev = choice["eval"]
    assert ev["why"] != "没有卡牌选项"
    assert "claim_reward" in ev["why"]
    assert ev["skip"] is False
