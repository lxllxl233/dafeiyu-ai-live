# -*- coding: utf-8 -*-
"""P2-1 离线回归：非战斗评估器（奖励 / 商店 / 营火 / 选牌）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ctx  # noqa: E402
import noncombat  # noqa: E402

os.environ.setdefault("STS2_DECISION_LOG", "0")

ok = True


def check(name, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def card(cid, cost, dmg=None, blk=None, text="", target="AnyEnemy", up=False):
    dv = []
    if dmg is not None:
        dv.append({"name": "Damage", "current_value": dmg})
    if blk is not None:
        dv.append({"name": "Block", "current_value": blk})
    c = {"card_id": cid, "name": cid, "energy_cost": cost, "upgraded": up,
         "playable": True, "requires_target": dmg is not None and target != "AllEnemies",
         "target_type": target, "dynamic_values": dv, "resolved_rules_text": text}
    return c


def deck(n_atk=4, n_def=4):
    d = [card("STRIKE_IRONCLAD", 1, dmg=6) for _ in range(n_atk)]
    d += [card("DEFEND_IRONCLAD", 1, blk=5) for _ in range(n_def)]
    return d


def run(deck_cards, hp=60, mhp=80, gold=100, floor=5):
    return {"screen": "MAP", "run": {"current_hp": hp, "max_hp": mhp, "gold": gold,
                                     "floor": floor, "act_id": 0, "ascension": 10,
                                     "deck": deck_cards, "relics": [], "potions": []}}


print("=== 1) 牌组缺口 ===")
d = deck(4, 5) + [card("SHRUG_IT_OFF", 1, blk=8, text="获得8点格挡。抽1张牌。"),
                  card("CLEAVE", 1, dmg=8, target="AllEnemies", text="对所有敌人造成8点伤害。")]
st = noncombat.deck_stats(d)
check("张数", st["size"], 11)
check("AOE 计数", st["aoe"], 1)
check("格挡牌数", st["blk"], 6)
check("抽牌牌数", st["draw"], 1)
check("缺口顺序", noncombat.needs(st)[:3], ["aoe", "draw", "energy"])

print("=== 2) 奖励：AOE 优先 ===")
choice = {"kind": "reward", "cards": [
    {**card("STRIKE_IRONCLAD", 1, dmg=6), "i": 0},
    {**card("CLEAVE", 1, dmg=8, target="AllEnemies", text="对所有敌人造成8点伤害。"), "i": 1},
]}
ev = noncombat.evaluate(run(deck(4, 4)), choice)
print("  ", ev["opts"], ev["best"], ev["skip"])
check("AOE 最优", ev["best"], 1)
check("AOE 不被跳过", ev["skip"], False)
check("AOE 标签", "aoe" in ev["opts"][1]["tag"], True)

print("=== 3) 奖励门槛：大牌组只拿质变 ===")
big = deck(9, 9)
choice = {"kind": "reward", "cards": [
    {**card("STRIKE_IRONCLAD", 1, dmg=6), "i": 0},
    {**card("DEFEND_IRONCLAD", 1, blk=5), "i": 1},
]}
ev = noncombat.evaluate(run(big), choice)
print("  ", ev["opts"], ev["best"], ev["skip"], ev["bar"])
check("18 张时跳过弱牌", ev["skip"], True)
check("门槛 62", ev["bar"], 62)

print("=== 4) 营火：低血睡、满血锻 ===")
choice = {"kind": "rest", "opts": [{"i": 0, "n": "休息", "ok": True},
                                   {"i": 1, "n": "锻造", "ok": True}]}
low = noncombat.evaluate(run(deck(4, 4) + [card("BASH", 2, dmg=8, text="造成8点伤害。")], hp=40), choice)
high = noncombat.evaluate(run(deck(4, 4) + [card("BASH", 2, dmg=8, text="造成8点伤害。")], hp=80), choice)
print("  low", low["opts"], "best", low["best"])
print("  high", high["opts"], "best", high["best"])
check("低血睡", low["best"], 0)
check("满血锻", high["best"], 1)
check("回复量", low["heal"], 24)

print("=== 5) 选牌：升级 / 删除 ===")
choice = {"kind": "select", "prompt": "选择一张牌升级", "min": 1, "max": 1, "cards": [
    {**card("STRIKE_IRONCLAD", 1, dmg=6), "i": 0},
    {**card("BASH", 2, dmg=8, text="造成8点伤害。"), "i": 1},
]}
ev = noncombat.evaluate(run(deck(4, 4)), choice)
print("  upgrade", ev["opts"], ev["best"])
check("升级痛击", ev["best"], 1)
check("升级模式", ev["mode"], "upgrade")

choice = {"kind": "select", "prompt": "选择一张牌移除", "min": 1, "max": 1, "cards": [
    {**card("STRIKE_IRONCLAD", 1, dmg=6), "i": 0},
    {**card("SHRUG_IT_OFF", 1, blk=8, text="获得8点格挡。抽1张牌。"), "i": 1},
]}
ev = noncombat.evaluate(run(deck(4, 4)), choice)
print("  remove", ev["opts"], ev["best"])
check("优先删打击", ev["best"], 0)
check("删除模式", ev["mode"], "remove")

print("=== 6) 商店：价格与删牌 ===")
st = run(deck(8, 8), gold=100)
choice = {"kind": "shop", "gold": 100, "items": [
    {"sec": "cards", "i": 0, "id": "SHRUG_IT_OFF", "n": "耸肩无视", "price": 50, "ok": True},
    {"sec": "relics", "i": 0, "n": "测试遗物", "price": 120, "ok": True},
], "removal": {"ok": True, "price": 75}}
ev = noncombat.evaluate(st, choice)
print("  ", ev["opts"], "buy", ev["buy"])
check("有可买项", len(ev["buy"]) >= 1, True)
check("买不起的遗物 0 分", [o["sc"] for o in ev["opts"] if o["sec"] == "relics"][0], 0)
check("删牌在候选中", any(o["sec"] == "removal" for o in ev["opts"]), True)

print("=== 7) ctx 集成：奖励界面自动带 eval ===")
st = {
    "screen": "REWARD",
    "run": {"current_hp": 60, "max_hp": 80, "gold": 50, "floor": 5,
            "act_id": 0, "ascension": 10, "deck": deck(4, 4), "relics": [], "potions": []},
    "reward": {"rewards": [], "card_options": [
        card("CLEAVE", 1, dmg=8, target="AllEnemies", text="对所有敌人造成8点伤害。"),
        card("STRIKE_IRONCLAD", 1, dmg=6),
    ]},
}
out = ctx.build(st)
check("choice 有 eval", "eval" in out["choice"], True)
check("eval 模式", out["choice"]["eval"]["mode"], "reward")
check("ctx 原字段还在", "cards" in out["choice"], True)
print("  eval", out["choice"]["eval"])

print("=== 8) ctx 集成：营火 / 选牌 / 商店 ===")
st = {
    "screen": "REST",
    "run": {"current_hp": 40, "max_hp": 80, "gold": 50, "floor": 6,
            "act_id": 0, "ascension": 10,
            "deck": deck(4, 4) + [card("BASH", 2, dmg=8, text="造成8点伤害。")],
            "relics": [], "potions": []},
    "rest": {"options": [{"title": "休息", "is_enabled": True},
                         {"title": "锻造", "is_enabled": True}]},
}
out = ctx.build(st)
check("营火 eval", out["choice"]["eval"]["mode"], "rest")
check("营火 best 睡", out["choice"]["eval"]["best"], 0)

st = {
    "screen": "CARD_SELECTION",
    "run": {"current_hp": 60, "max_hp": 80, "gold": 50, "floor": 6,
            "act_id": 0, "ascension": 10, "deck": deck(4, 4), "relics": [], "potions": []},
    "selection": {"prompt": "选择一张牌升级", "min_select": 1, "max_select": 1,
                  "cards": [card("STRIKE_IRONCLAD", 1, dmg=6),
                            card("BASH", 2, dmg=8, text="造成8点伤害。")]},
}
out = ctx.build(st)
check("选牌 eval", out["choice"]["eval"]["mode"], "upgrade")
check("选牌 best 痛击", out["choice"]["eval"]["best"], 1)

st = {
    "screen": "SHOP",
    "run": {"current_hp": 60, "max_hp": 80, "gold": 100, "floor": 7,
            "act_id": 0, "ascension": 10, "deck": deck(8, 8), "relics": [], "potions": []},
    "shop": {"cards": [{"card_id": "SHRUG_IT_OFF", "name": "耸肩无视",
                        "price": 50, "available": True}],
             "relics": [], "potions": [],
             "card_removal": {"available": True, "price": 75}},
}
out = ctx.build(st)
check("商店 eval", out["choice"]["eval"]["mode"], "shop")
check("商店保留 id", out["choice"]["items"][0].get("id"), "SHRUG_IT_OFF")
check("商店有删牌项", any(o["sec"] == "removal" for o in out["choice"]["eval"]["opts"]), True)

print("\n" + ("ALL OK" if ok else "FAILED"))
sys.exit(0 if ok else 1)
