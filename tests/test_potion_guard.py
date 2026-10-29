"""药水闸：Boss / 重大损失 / 致死不交药水，执行层要拦下来。

背景（run 75S0RYFKEH f17）：整场 Boss 只带两瓶药水，直到 18 血才交迅捷、
5 血才交赌徒，已经救不回来。提示词写了「Boss 不省药」，但模型会以
「这瓶是抽牌/换手，不直接挡」为由跳过 —— 这里锁住执行层的硬闸。
"""
from __future__ import annotations

from dafeiyu.games.registry import GameRegistry

sim = GameRegistry().load_game_module("slay-the-spire-2", "decision.sim")
act = GameRegistry().load_game_module("slay-the-spire-2", "decision.act")
assert sim is not None and act is not None


def _card(cid, cost, dmg=None, blk=None):
    dv = []
    if dmg is not None:
        dv.append({"name": "Damage", "current_value": dmg})
    if blk is not None:
        dv.append({"name": "Block", "current_value": blk})
    return {"card_id": cid, "name": cid, "energy_cost": cost, "upgraded": False,
            "playable": True, "requires_target": dmg is not None,
            "target_type": "AnyEnemy", "dynamic_values": dv,
            "resolved_rules_text": ""}


def _potion(slot=0, name="格挡药水", desc="获得12点格挡。"):
    return {"index": slot, "occupied": True, "potion_id": "BLOCK_POTION",
            "name": name, "description": desc, "can_use": True,
            "requires_target": False}


def _state(hp=70, inc=14, potions=None, boss=False, hand=None):
    run = {"current_hp": hp, "max_hp": 80, "max_energy": 3, "floor": 17,
           "act_id": 0, "ascension": 10, "gold": 99,
           "deck": [{"card_id": "STRIKE_IRONCLAD", "name": "打击",
                     "energy_cost": 1}] * 12,
           "relics": [], "potions": potions or []}
    if boss:
        run["boss_id"] = "JAW_WORM_BOSS"
    return {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn", "use_potion"],
        "run": run,
        "combat": {"turn": 3,
                   "player": {"current_hp": hp, "max_hp": 80, "energy": 3,
                              "block": 0, "powers": []},
                   "hand": hand or [_card("STRIKE_IRONCLAD", 1, dmg=6)],
                   "enemies": [{"index": 0, "enemy_id": "JAW_WORM",
                                "name": "JAW_WORM", "current_hp": 200, "max_hp": 200,
                                "block": 0, "is_alive": True, "powers": [],
                                "intents": [{"intent_type": "ATTACK", "damage": inc,
                                             "hits": 1, "total_damage": inc}]}]},
        "agent_view": {"combat": {"draw_cards": [], "discard_cards": [],
                                  "exhaust_cards": []}},
    }


def _sm(st, seq=None):
    ok, err, sm, _steps = sim.validate(st, seq or [{"end": 1}])
    assert ok, err
    return sm


def test_lethal_turn_without_potion_is_blocked():
    st = _state(hp=30, inc=30, potions=[_potion()])
    err = act._guard_potion(st, _sm(st), {})
    assert err and "护栏·药水" in err and "致死" in err
    assert "槽0" in err and '"p": 槽位' in err


def test_lethal_turn_without_any_potion_is_fine():
    st = _state(hp=30, inc=30, potions=[])
    assert act._guard_potion(st, _sm(st), {}) is None


def test_big_hit_on_boss_forces_potion_but_normal_fight_does_not():
    st_boss = _state(hp=70, inc=14, boss=True, potions=[_potion()])
    err = act._guard_potion(st_boss, _sm(st_boss), {})
    assert err and "Boss 战" in err
    st_norm = _state(hp=70, inc=14, potions=[_potion()])
    assert act._guard_potion(st_norm, _sm(st_norm), {}) is None


def test_small_hit_does_not_drink():
    st = _state(hp=70, inc=5, potions=[_potion()])
    assert act._guard_potion(st, _sm(st), {}) is None


def test_plan_that_already_uses_a_potion_passes():
    st = _state(hp=30, inc=30, potions=[_potion()])
    sm = _sm(st, [{"p": 0}, {"end": 1}])
    assert act._guard_potion(st, sm, {}) is None


def test_allow_take_keeps_the_escape_hatch():
    st = _state(hp=30, inc=30, potions=[_potion()])
    assert act._guard_potion(st, _sm(st), {"allow_take": 1}) is None


def test_non_mitigating_potions_do_not_force_a_drink():
    """缚魂药水（实测无效）和灰水（消耗手牌）减不了这一手的伤害，
    逼模型交只会换来「没用，不浪费」+ 4 次重试（run SLUA8TYL73 f17 t2）。"""
    binding = _potion(name="缚魂药水", desc="给予所有敌人1层虚弱和1层易伤。")
    binding["potion_id"] = "POTION_OF_BINDING"
    ash = _potion(slot=1, name="灰水", desc="消耗你手牌中的任意张牌。")
    ash["potion_id"] = "ASHWATER"
    st = _state(hp=30, inc=30, potions=[binding, ash])
    assert act._guard_potion(st, _sm(st), {}) is None


def test_is_boss_fight_matches_enemy_prefix():
    assert sim.is_boss_fight(_state(boss=True)) is True
    assert sim.is_boss_fight(_state(boss=False)) is False
    # boss_id 全幕都在：靠「Boss 层」或活怪 id 词根认，不能只看字段有没有值
    plain = _state()
    plain["run"].update({"boss_id": "VANTOM_BOSS", "floor": 8})
    assert sim.is_boss_fight(plain) is False
    plain["combat"]["enemies"][0]["enemy_id"] = "VANTOM"
    assert sim.is_boss_fight(plain) is True
    # THE_KIN_BOSS 的怪叫 KIN_PRIEST / KIN_FOLLOWER，没有共同前缀也要认出来
    kin = _state()
    kin["run"].update({"boss_id": "THE_KIN_BOSS", "floor": 8})
    kin["combat"]["enemies"][0]["enemy_id"] = "KIN_PRIEST"
    assert sim.is_boss_fight(kin) is True
