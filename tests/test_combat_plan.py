"""战斗规划/执行统一后的回归：费用、免费攻击、变费、狂怒、清场、执行回执。

旧实现里 advisor.py 自己维护一份静态费用模型，和 sim.validate、执行层各算一套；
这一组测试锁住「本地战斗 = sim 计划 + sim 校验 + 回执校验」这条链。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECISION = ROOT / "games/slay-the-spire-2/decision"
for p in (ROOT / "src", ROOT, DECISION):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import sim  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, DECISION / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def card(cid, cost, dmg=None, text="", ctype="Attack", dyn=None, **kw):
    values = list(dyn or [])
    if dmg is not None:
        values.append({"name": "Damage", "current_value": dmg})
    c = {"card_id": cid, "name": cid, "energy_cost": cost, "card_type": ctype,
         "playable": True, "resolved_rules_text": text, "dynamic_values": values}
    c.update(kw)
    return c


def state(hand, energy=3, hp=70, block=0, powers=None, enemy_hp=80, potions=None):
    return {"screen": "COMBAT",
            "run": {"current_hp": hp, "max_hp": 80, "potions": potions or []},
            "combat": {"player": {"current_hp": hp, "max_hp": 80, "block": block,
                                  "energy": energy, "max_energy": 3,
                                  "powers": powers or []},
                       "hand": hand,
                       "enemies": [{"index": 0, "enemy_id": "JAW_WORM", "name": "大颚虫",
                                    "current_hp": enemy_hp, "max_hp": enemy_hp, "block": 0,
                                    "powers": [],
                                    "intents": [{"intent_type": "Attack",
                                                 "total_damage": 10}]}]}}


STRIKE = card("STRIKE_IRONCLAD", 1, dmg=6, text="造成6点伤害。")
BLOODLETTING = card("BLOODLETTING", 0, text="失去3点生命。获得2点能量。", ctype="Skill",
                    dyn=[{"name": "HpLoss", "current_value": 3},
                         {"name": "Energy", "current_value": 2}])


def test_energy_card_is_planned_first_and_pays_for_attacks():
    """3 能量要打「回能 2 + 两张 1 费攻击」：计划的每一步费用必须是逐步更新的。"""
    st = state([BLOODLETTING, STRIKE, STRIKE], energy=1)
    plans = sim.search(st)
    assert plans, "scenario must have a plan"
    best = plans[0]
    assert best["seq"][0][0] == "BLOODLETTING", best["seq"]
    ids = [x[0] for x in best["seq"]]
    assert ids.count("STRIKE_IRONCLAD") == 2
    ok, err, sm, _steps = sim.validate(st, [{"c": i} for i in ids])
    assert ok, err
    assert sm.energy == 1  # 1 + 2 - 1 - 1

    # 回能牌没打出去就排两张攻击：第一张之后就没费了，逐步校验要拒绝。
    ok2, err2, _sm, _ = sim.validate(st, [{"c": "STRIKE_IRONCLAD"},
                                          {"c": "STRIKE_IRONCLAD"}])
    assert not ok2 and "只剩 0" in err2


def test_only_one_free_attack_layer_is_discounted():
    """引擎把三张攻击都报 0 费时，只有一层免费：第二张开始要付原费。"""
    free = card("STRIKE_IRONCLAD", 0, dmg=6, text="造成6点伤害。")
    st = state([free, free, free], energy=1,
               powers=[{"power_id": "FREE_ATTACK", "name": "免费攻击", "amount": 1}])
    sm = sim.Sim.from_state(st)
    assert sm.free_atk == 1
    assert [sm.cost_of(c) for c in sm.hand] == [0, 0, 0]
    first = sm.clone()
    first.play(first.hand[0], 0)
    assert first.free_atk == 0
    assert [first.cost_of(c) for c in first.hand if not c.get("_played")] == [1, 1]
    # 三张全排：第三张时能量已花光 → validate 必须拒绝
    ok, err, _sm, _ = sim.validate(st, [{"c": "STRIKE_IRONCLAD"}] * 3)
    assert not ok and ("只剩 0" in err or "不能打" in err)


def test_cost_reduction_card_changes_later_cost():
    """先打「下一张攻击牌 0 费」，后面那张攻击的当下费用才是 0。"""
    unr = card("UNRELENTING", 2, dmg=14,
               text="造成14点伤害。你打出的下一张攻击牌耗能变为0。")
    st = state([unr, STRIKE], energy=2)
    sm = sim.Sim.from_state(st)
    assert sm.cost_of(sm.hand[1]) == 1
    played = sm.clone()
    played.play(played.hand[0], 0)
    assert played.next_attack_zero == 1
    assert played.cost_of(played.hand[1]) == 0
    assert played.energy == 0
    ok, err, _sm, _steps = sim.validate(st, [{"c": "UNRELENTING"}, {"c": "STRIKE_IRONCLAD"}])
    assert ok, err


def test_rage_block_only_counts_playable_attacks():
    """剩 1 费、两张 1 费攻击、狂怒 3：只能算一张的格挡（旧实现虚报 6）。"""
    st = state([STRIKE, STRIKE], energy=1)
    sm = sim.Sim.from_state(st)
    sm.rage = 3
    assert sim._rage_block(sm) == 3
    sm.energy = 0
    assert sim._rage_block(sm) == 0
    sm.energy = 2
    assert sim._rage_block(sm) == 6


def test_validate_rejects_tail_after_clear():
    """斩杀后多写的动作要在执行前拒绝，不能打到一半报错。"""
    st = state([STRIKE, STRIKE], energy=3, enemy_hp=5)
    ok, err, _sm, steps = sim.validate(st, [{"c": "STRIKE_IRONCLAD"},
                                            {"c": "STRIKE_IRONCLAD"}])
    assert not ok and "已经清场" in err
    assert len(steps) == 1
