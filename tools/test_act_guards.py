# -*- coding: utf-8 -*-
"""护栏回归：漏费 / 漏甲 / 漏斩 / 过渡快照 ctx。

run69 的病：结束回合时还有费、还能起甲却裸吃伤害；能一回合斩却不斩，
多挨一刀；end_turn 后立刻读状态，拿到的是「新回合还没抽牌」的快照。
这三条现在由 act.py 强制执行，不靠模型自觉。全部离线，不碰游戏 API。

    python tools/test_act_guards.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("STS2_DECISION_LOG", "0")

import act  # noqa: E402
import ctx as ctxmod  # noqa: E402
import sts2  # noqa: E402

ok = True


def check(name, got, want=True):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def card(cid, cost=1, dmg=0, blk=0):
    c = {"card_id": cid, "name": cid, "energy_cost": cost, "playable": True,
         "target_type": "AnyEnemy" if dmg else "Self"}
    if dmg:
        c["damage"] = dmg
    if blk:
        c["block"] = blk
    return c


def enemy(idx=0, hp=20, atk=12):
    return {"index": idx, "enemy_id": "E0", "name": "E0", "current_hp": hp,
            "hp": hp, "max_hp": hp, "is_alive": True, "block": 0, "powers": [],
            "intents": [{"type": "Attack", "damage": atk, "hits": 1,
                         "total_damage": atk}]}


def combat(hand, energy=3, hp=50, incoming=12, ehp=20):
    return {
        "screen": "COMBAT",
        "available_actions": ["play_card", "end_turn"],
        "run": {"current_hp": hp, "max_hp": 80, "max_energy": 3, "potions": [],
                "floor": 5, "act": 1, "ascension": 10},
        "combat": {"turn": 3,
                   "player": {"current_hp": hp, "max_hp": 80, "energy": energy,
                              "block": 0, "powers": []},
                   "hand": hand,
                   "enemies": [enemy(0, ehp, incoming)]},
    }


# 批处理与状态读全部打桩：只验护栏本身
ran = {"n": 0}
act._run_batch = lambda acts: (ran.__setitem__("n", ran["n"] + 1) or ([], None))
sts2.get_state = lambda: combat([card("STRIKE_IRONCLAD", 1, dmg=6)])
_settled = {"called": 0}


def fake_settle(prev_hand_len, timeout=4.0):
    _settled["called"] += 1
    return combat([card("STRIKE_IRONCLAD", 1, dmg=6)])


sts2.wait_new_turn_hand = fake_settle


def guard_err(st, seq):
    out = act._execute(st, seq, [], {}, {})
    return out, (out.get("err") or "")


# 1) 漏费：还剩 3 费就 end，手上还有打击和防御
st = combat([card("STRIKE_IRONCLAD", 1, dmg=6), card("DEFEND_IRONCLAD", 1, blk=5)])
out, err = guard_err(st, [{"end": 1}])
check("漏费的 end 必须拒绝", out.get("ok"), False)
check("漏费错误说明可读", "漏费" in err, True)
check("漏费提到能叠甲的牌", "DEFEND_IRONCLAD" in err, True)
check("被拒时不执行批处理", ran["n"], 0)

# 2) 显式 allow_leftover 才放行
out, err = (lambda r: (r, r.get("err") or ""))(
    act._execute(st, [{"end": 1}], [], {}, {"allow_leftover": True}))
check("allow_leftover 放行", out.get("ok"), True)
check("放行后确实执行了", ran["n"], 1)

# 3) 换血线：1 费打完一刀、0 费吃 12 点，手上有防御 —— 给提示、不拦
st = combat([card("STRIKE_IRONCLAD", 1, dmg=6), card("DEFEND_IRONCLAD", 1, blk=5)],
            energy=1, incoming=12, ehp=40)
out, err = guard_err(st, [{"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}])
check("费花光的换血不硬拦", out.get("ok"), True)
check("换血线给提示", any("换血线" in w for w in (out.get("warn") or [])), True)
check("换血线报出能顶多少", any("能顶" in w for w in (out.get("warn") or [])), True)

# 3b) 留着 1 费不花 = 漏费（这条必须拦，别把护栏全拆了）
st = combat([card("STRIKE_IRONCLAD", 1, dmg=6), card("DEFEND_IRONCLAD", 1, blk=5)],
            energy=2, incoming=12, ehp=40)
out, err = guard_err(st, [{"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}])
check("有费不花仍要拦", out.get("ok"), False)
check("拦的是漏费", "漏费" in err, True)

out, err = (lambda r: (r, r.get("err") or ""))(
    act._execute(combat([card("STRIKE_IRONCLAD", 1, dmg=6), card("DEFEND_IRONCLAD", 1, blk=5)],
                        energy=1, incoming=12, ehp=40),
                 [{"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}], [], {},
                 {"allow_take": True}))
check("allow_take 放行换血", out.get("ok"), True)

# 4) 漏斩：2 血敌人一刀就死，却把费全拿去起甲
st = combat([card("STRIKE_IRONCLAD", 1, dmg=6),
             card("DEFEND_IRONCLAD", 1, blk=5),
             card("DEFEND_IRONCLAD", 1, blk=5)], energy=2, incoming=12, ehp=2)
out, err = guard_err(st, [{"c": "DEFEND_IRONCLAD"}, {"c": "DEFEND_IRONCLAD"}, {"end": 1}])
check("能斩不斩要拒绝", out.get("ok"), False)
check("漏斩错误说明可读", "漏斩" in err, True)
check("漏斩给出斩杀线", "STRIKE_IRONCLAD" in err, True)

# 5) 该打的仗照打：满能量全打光、零入伤，不该被拦
st = combat([card("DEFEND_IRONCLAD", 1, blk=5), card("DEFEND_IRONCLAD", 1, blk=5),
             card("DEFEND_IRONCLAD", 1, blk=5)], energy=3, incoming=12, ehp=40)
out, err = guard_err(st, [{"c": "DEFEND_IRONCLAD"}, {"c": "DEFEND_IRONCLAD"},
                          {"c": "DEFEND_IRONCLAD"}, {"end": 1}])
check("正常满防回合不被拦", out.get("ok"), True)

# 6) 清场收尾：最后一刀打死，剩费都不算漏
st = combat([card("STRIKE_IRONCLAD", 1, dmg=6), card("DEFEND_IRONCLAD", 1, blk=5)],
            energy=3, incoming=12, ehp=5)
out, err = guard_err(st, [{"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}])
check("斩杀收尾不拦剩余能量", out.get("ok"), True)

# 7) end 之后必须等新回合抽完牌再交 ctx
before = _settled["called"]
st = combat([card("DEFEND_IRONCLAD", 1, blk=5)], energy=1, incoming=5, ehp=40)
act._execute(st, [{"c": "DEFEND_IRONCLAD"}, {"end": 1}], [], {}, {})
check("end 后调用了 wait_new_turn_hand", _settled["called"], before + 1)

# 8) ctx 给出真实手牌张数（同名牌合并后也数得清）
d = ctxmod.build_with_mem(combat([card("DEFEND_IRONCLAD", 1, blk=5)] * 3), {})
check("ctx 有 hand_n", d.get("hand_n"), 3)
check("合并后条目只有 1 条", len(d.get("hand") or []), 1)

# 9) 死亡遗言：能斩但斩杀线 0 甲，而敌人身上有「蒸汽喷发」——给告警而不是拦
st = combat([card("STRIKE_IRONCLAD", 1, dmg=6)], energy=1, incoming=0, ehp=2)
st["combat"]["enemies"][0]["powers"] = [{"name": "蒸汽喷发20", "amount": 20}]
out, err = guard_err(st, [{"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}])
check("遗言 + 0 甲斩杀放行", out.get("ok"), True)
check("遗言告警出现", any("遗言" in w for w in (out.get("warn") or [])), True)

# 10) 同一条线换成「带甲的斩杀」就放行
st = combat([card("STRIKE_IRONCLAD", 1, dmg=6),
             card("DEFEND_IRONCLAD", 1, blk=5), card("DEFEND_IRONCLAD", 1, blk=5)],
            energy=3, incoming=0, ehp=2)
st["combat"]["enemies"][0]["powers"] = [{"name": "蒸汽喷发20", "amount": 20}]
out, err = guard_err(st, [{"c": "DEFEND_IRONCLAD"}, {"c": "DEFEND_IRONCLAD"},
                          {"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}])
check("带甲斩杀放行", out.get("ok"), True)

# 11) 漏甲不该罚「已经打过防御」的手：run73 实测的误判
st = combat([card("DEFEND_IRONCLAD", 1, blk=5), card("STRIKE_IRONCLAD", 1, dmg=6),
             card("STRIKE_IRONCLAD", 1, dmg=6)], energy=3, incoming=10, ehp=60)
out, err = guard_err(st, [{"c": "DEFEND_IRONCLAD"}, {"c": "STRIKE_IRONCLAD", "t": 0},
                          {"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}])
check("打过的防御不再算漏甲", "漏甲" in err, False)

# 12) 同名多张：只打过 1 张、还剩 1 张没打 —— 同样只提示换血，不硬拦
st = combat([card("DEFEND_IRONCLAD", 1, blk=5), card("DEFEND_IRONCLAD", 1, blk=5)],
            energy=1, incoming=12, ehp=60)
out, err = guard_err(st, [{"c": "DEFEND_IRONCLAD"}, {"end": 1}])
check("还剩同名甲只提示", any("换血线" in w for w in (out.get("warn") or [])), True)
check("提示里点名那张甲", any("DEFEND_IRONCLAD" in w for w in (out.get("warn") or [])), True)

# 13) --dry：只验算不执行（run82 三次「第四张牌没费了」都是心算错的）
st = combat([card("STRIKE_IRONCLAD", 1, dmg=6), card("DEFEND_IRONCLAD", 1, blk=5)],
            energy=1, incoming=12, ehp=60)
before = ran["n"]
d = act._dry(st, [{"c": "STRIKE_IRONCLAD", "t": 0}], {}, {})
check("dry 成功", d.get("ok"), True)
check("dry 不执行批处理", ran["n"], before)
check("dry 带每步账", len(d.get("steps") or []), 1)
check("dry 给最佳线", bool(d.get("best")), True)
d2 = act._dry(st, [{"c": "STRIKE_IRONCLAD", "t": 0}, {"c": "DEFEND_IRONCLAD"}], {}, {})
check("dry 也会拦没费的牌", d2.get("ok"), False)

# 14) ctx 要解释「柔嫩/缩小/缠结」怎么算（run82 就是不知道柔嫩才把牌序打反）
st = combat([card("STRIKE_IRONCLAD", 1, dmg=6)])
st["combat"]["player"]["powers"] = [{"power_id": "TENDER_POWER", "name": "柔嫩", "amount": 1}]
d = ctxmod.build_with_mem(st, {})
check("ctx 给出柔嫩口径", any("柔嫩" in m for m in (d.get("mods") or [])), True)
check("柔嫩提示告诉先打贵的", any("先打贵的" in m for m in (d.get("mods") or [])), True)
st["combat"]["player"]["powers"] = [{"power_id": "SHRINK_POWER", "name": "缩小", "amount": 1}]
check("缩小也说清楚", any("-30%" in m for m in
                          (ctxmod.build_with_mem(st, {}).get("mods") or [])), True)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)