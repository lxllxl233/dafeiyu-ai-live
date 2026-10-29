"""路线/精英/营火要看卡组强度，而不是只看血量。"""
from __future__ import annotations

from dafeiyu.games.registry import GameRegistry

ctx = GameRegistry().load_game_module("slay-the-spire-2", "decision.ctx")
nc = GameRegistry().load_game_module("slay-the-spire-2", "decision.noncombat")
prompts = GameRegistry().load_game_module("slay-the-spire-2", "decision.prompts")


def _card(cid, up=False):
    return {"card_id": cid, "name": cid, "energy_cost": 1, "upgraded": up}


WEAK = ([_card("STRIKE_IRONCLAD")] * 5 + [_card("DEFEND_IRONCLAD")] * 4
        + [_card("BASH")])
STRONG = ([_card("WHIRLWIND", True), _card("WHIRLWIND")]
          + [_card("SHRUG_IT_OFF", True)] * 4
          + [_card("POMMEL_STRIKE", True), _card("UPPERCUT", True),
             _card("BODY_SLAM", True), _card("BASH", True),
             _card("FEEL_NO_PAIN", True), _card("DARK_EMBRACE"),
             _card("ARMAMENTS", True), _card("TRUE_GRIT", True)])


def _rest_state(hp, deck):
    return {"screen": "REST",
            "run": {"current_hp": hp, "max_hp": 80, "deck": deck, "floor": 8},
            "rest": {"options": [{"title": "休息", "is_enabled": True},
                                  {"title": "锻造", "is_enabled": True}]}}


def _map_state(hp, deck):
    return {"screen": "MAP",
            "run": {"current_hp": hp, "max_hp": 80, "deck": deck, "gold": 90,
                    "floor": 6},
            "map": {"nodes": [
                {"row": 1, "col": 0, "node_type": "Elite", "is_available": True},
                {"row": 1, "col": 1, "node_type": "Monster", "is_available": True},
                {"row": 1, "col": 2, "node_type": "RestSite", "is_available": True},
            ]}}


def _rest_eval(hp, deck):
    return ctx.build(_rest_state(hp, deck))["choice"]["eval"]


def _map_eval(hp, deck):
    return ctx.build(_map_state(hp, deck))["choice"]["eval"]


def test_deck_power_rates_the_starter_deck_weak():
    pw = nc.deck_power(WEAK)
    assert pw["sc"] < 40 and pw["g"] in ("C", "D")
    assert "缺AOE" in pw["need"]
    assert isinstance(pw["why"], str) and pw["why"]


def test_deck_power_rates_a_built_deck_higher():
    weak, strong = nc.deck_power(WEAK), nc.deck_power(STRONG)
    assert strong["sc"] >= 55 and strong["g"] in ("A", "B")
    assert strong["sc"] > weak["sc"] + 20


def test_rest_prefers_smith_above_half_hp_with_a_key_card():
    # 55% 血、卡组 A：有关键牌没升 → 锻造（不是一律睡到 70%）。
    ev = _rest_eval(44, STRONG)
    assert ev["best"] == 1
    assert "锻造" not in ev["why"]  # why 是「升级XXX（N分）…」
    assert "升级" in ev["why"]


def test_rest_sleeps_below_half_hp():
    ev = _rest_eval(36, STRONG)
    assert ev["best"] == 0
    assert ev["opts"][0]["sc"] > ev["opts"][1]["sc"]


def test_map_elite_score_follows_deck_strength():
    strong = _map_eval(48, STRONG)
    weak = _map_eval(48, WEAK)
    se = next(o["sc"] for o in strong["opts"] if o["t"] == "elite")
    we = next(o["sc"] for o in weak["opts"] if o["t"] == "elite")
    assert se > we + 20
    # 血 60% + 卡组 A：精英可以比营火更值得；卡组 D 时营火明显更优。
    rest = next(o["sc"] for o in weak["opts"] if o["t"] == "restsite")
    assert we < rest
    assert se > next(o["sc"] for o in strong["opts"] if o["t"] == "restsite")


def test_map_elite_score_drops_at_low_hp():
    high = _map_eval(48, STRONG)
    low = _map_eval(36, STRONG)
    eh = next(o["sc"] for o in high["opts"] if o["t"] == "elite")
    el = next(o["sc"] for o in low["opts"] if o["t"] == "elite")
    assert eh > el + 20


def test_ctx_noncombat_exposes_deck_power():
    brief = ctx.build(_map_state(48, STRONG))
    pw = brief.get("deck_pw")
    assert isinstance(pw, dict)
    assert isinstance(pw.get("sc"), int) and 0 <= pw["sc"] <= 100
    assert pw.get("g") in ("A", "B", "C", "D")
    assert isinstance(pw.get("need"), list)


def test_prompts_route_and_campfire_use_deck_power():
    text = prompts.NONCOMBAT_PROMPT
    assert "deck_pw" in text
    assert "卡组" in text and "≥50%" in text
    # 旧的「HP<70% 睡」一刀切已经不在
    assert "HP<70% 睡" not in text
