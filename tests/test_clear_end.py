"""清场后写 end 是合法的：batch 会跳过它，别整手拒绝。

实机 f11 t2：模型排「易伤 + 猛攻 + 御血 + end」，第三张清场，整手被
``sim.validate`` 拒成「第 3 步已经清场，后面还有 1 步」，重试两次后落本地兜底。
"""
from __future__ import annotations

from dafeiyu.games.registry import GameRegistry

sim = GameRegistry().load_game_module("slay-the-spire-2", "decision.sim")
assert sim is not None


def _card(cid, dmg):
    return {"card_id": cid, "name": cid, "energy_cost": 1, "upgraded": False,
            "playable": True, "requires_target": True, "target_type": "AnyEnemy",
            "dynamic_values": [{"name": "Damage", "current_value": dmg}],
            "resolved_rules_text": ""}


def _state(hand, enemy_hp=6):
    return {"screen": "COMBAT",
            "run": {"current_hp": 70, "max_hp": 80, "max_energy": 3,
                    "potions": [], "deck": []},
            "combat": {"player": {"current_hp": 70, "max_hp": 80, "energy": 3,
                                  "block": 0, "powers": []},
                       "hand": hand,
                       "enemies": [{"index": 0, "enemy_id": "JAW_WORM",
                                    "name": "JAW_WORM", "current_hp": enemy_hp,
                                    "max_hp": enemy_hp, "block": 0, "powers": [],
                                    "intents": [{"intent_type": "Attack",
                                                 "total_damage": 10}]}]}}


def test_kill_plus_trailing_end_is_allowed():
    st = _state([_card("STRIKE_IRONCLAD", 6)])
    ok, err, sm, steps = sim.validate(st, [{"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}])
    assert ok, err
    assert steps[-1]["k"] == "end"
    assert not sm.alive()


def test_kill_then_another_card_is_still_rejected():
    st = _state([_card("STRIKE_IRONCLAD", 6), _card("STRIKE_IRONCLAD", 6)])
    ok, err, _sm, _steps = sim.validate(
        st, [{"c": "STRIKE_IRONCLAD", "t": 0}, {"c": "STRIKE_IRONCLAD", "t": 0}])
    assert not ok and "已经清场" in err


def test_kill_without_end_still_valid():
    st = _state([_card("STRIKE_IRONCLAD", 6)])
    ok, err, sm, _steps = sim.validate(st, [{"c": "STRIKE_IRONCLAD", "t": 0}])
    assert ok, err
    assert not sm.alive()
