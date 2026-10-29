# -*- coding: utf-8 -*-
"""P2-1 非战斗评估器（奖励 / 商店 / 营火 / 选牌）。

定位
----
这是**建议层**，不是执行层：`ctx.choice.eval` 只给分、缺口和理由，
真正点哪个选项仍由模型按当前局面决定。分数是启发式，不是胜率。

设计原则
--------
- 透明：每个分数都带 `why`，模型能看出加/减分来自哪里。
- 保守：牌组越大门槛越高，宁可不抓，也不把 18 张牌组再塞一张。
- 只读：不写存档、不改 `state`、不调用游戏 API，可离线回归。
- 复用：奖励、商店、营火、选牌共用同一套 `deck_stats` 与 `card_eval`。
"""
from __future__ import annotations

import os
import sys

# 既能被 ctx 当库导入，也能直接跑；decision 目录提供 sim / sts2。
_HERE = os.path.dirname(os.path.abspath(__file__))
_PACK = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(_PACK))
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sim  # noqa: E402

RARITY = {
    "Basic": -10,
    "Common": 0,
    "Uncommon": 4,
    "Rare": 8,
    "Ancient": 10,
}

# 升级优先：只覆盖高频牌，未列出的牌按通用分处理。
UPGRADE_PRIORITY = {
    "BASH": 36,
    "SHRUG_IT_OFF": 30,
    "TRUE_GRIT": 26,
    "POMMEL_STRIKE": 26,
    "BODY_SLAM": 30,
    "UPPERCUT": 20,
    "CLEAVE": 18,
    "OFFERING": 26,
    "BURNING_PACT": 20,
    "DARK_EMBRACE": 24,
    "FEEL_NO_PAIN": 20,
    "CORRUPTION": 26,
    "BLOODLETTING": 20,
    "ARMAMENTS": 14,
}

CORE = {
    "CORRUPTION", "DARK_EMBRACE", "FEEL_NO_PAIN", "OFFERING",
    "BODY_SLAM", "PACT_END", "PACT_S_END", "BURNING_PACT",
    "BLOODLETTING", "UPPERCUT", "SHRUG_IT_OFF",
}


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, int(round(v))))


def _card_model(card, idx=0):
    """尽量保留实牌效果；缺字段时退回 sim 的卡库解析。

    `ctx.choice` 里的牌已经是 `sim.parse_card` 的短名格式（id/t/dmg/blk），
    不能再丢回 `parse_card`，否则 `card_id` 不在、动态值会二次丢失。
    """
    if not isinstance(card, dict):
        return {"i": idx, "id": "?", "n": "?", "c": 0, "t": "?"}
    if card.get("id") and card.get("t"):
        e = dict(card)
        e["i"] = idx
        e["c"] = _int(e.get("c"), 0)
        return e
    try:
        e = sim.parse_card(card, idx)
    except Exception:
        e = {"i": idx, "id": str(card.get("card_id") or card.get("id") or "?"),
             "n": str(card.get("name") or "?"),
             "c": _int(card.get("energy_cost", card.get("cost")), 0),
             "t": str(card.get("card_type") or "?")}
    e["i"] = idx
    return e


def deck_stats(deck):
    """牌组结构统计；只用牌面，不预测遗物/能力。"""
    cards = [_card_model(c, i) for i, c in enumerate(deck or []) if isinstance(c, dict)]
    st = {
        "size": len(cards), "play": 0, "atk": 0, "skl": 0, "pow": 0,
        "curse": 0, "strike": 0, "defend": 0, "blk": 0, "blk_total": 0,
        "aoe": 0, "draw": 0, "draw_total": 0, "en": 0, "en_total": 0,
        "exh": 0, "debuff": 0, "up": 0, "upg": 0, "cost": 0,
    }
    for c in cards:
        cid = str(c.get("id") or "").upper()
        typ = str(c.get("t") or "?")
        if typ in ("C", "T"):
            st["curse"] += 1
            continue
        st["play"] += 1
        if cid.startswith("STRIKE"):
            st["strike"] += 1
        if cid.startswith("DEFEND"):
            st["defend"] += 1
        if typ == "A":
            st["atk"] += 1
        elif typ == "S":
            st["skl"] += 1
        elif typ == "P":
            st["pow"] += 1
        blk = _int(c.get("blk"), 0)
        if blk:
            st["blk"] += 1
            st["blk_total"] += blk
        if c.get("aoe"):
            st["aoe"] += 1
        if _int(c.get("draw"), 0):
            st["draw"] += 1
            st["draw_total"] += _int(c.get("draw"), 0)
        if _int(c.get("en"), 0):
            st["en"] += 1
            st["en_total"] += _int(c.get("en"), 0)
        if c.get("exh"):
            st["exh"] += 1
        if _int(c.get("vuln"), 0) or _int(c.get("weak"), 0):
            st["debuff"] += 1
        if c.get("up"):
            st["up"] += 1
        elif typ in ("A", "S", "P"):
            st["upg"] += 1
        st["cost"] += max(0, _int(c.get("c"), 0))
    st["avgc"] = round(st["cost"] / st["play"], 2) if st["play"] else 0.0
    return st


def needs(st):
    """按 A10 手册排缺口；越靠前越优先。"""
    out = []
    if st["aoe"] < 2:
        out.append("aoe")
    if st["blk"] < 6:
        out.append("block")
    if st["draw"] < 2:
        out.append("draw")
    if st["en"] < 1:
        out.append("energy")
    if st["atk"] < 5:
        out.append("attack")
    if st["size"] > 16:
        out.append("trim")
    return out


NEED_LABEL = {
    "aoe": "缺AOE", "block": "缺防", "draw": "缺抽牌",
    "energy": "缺能量", "attack": "缺输出", "trim": "牌太厚",
}


def deck_power(deck):
    """牌组强度 0–100（A/B/C/D）：最强 8 张 + 结构缺口 + 升级率。

    只看牌面，不预测遗物/能力；给路线、精英、营火一个可比较的数，
    避免「只看血量」——血够但卡组C 时进精英也是负收益。
    """
    cards = [c for c in (deck or []) if isinstance(c, dict)]
    st = deck_stats(cards)
    vals = sorted((card_eval(c, st, i)["sc"] for i, c in enumerate(cards)), reverse=True)
    if not vals:
        return {"sc": 0, "g": "D", "need": ["无牌"], "why": "空牌组"}
    core = sum(vals[:8]) / len(vals[:8])
    avg = sum(vals) / len(vals)
    bonus = 0.0
    if st["aoe"] >= 2:
        bonus += 5
    if st["blk"] >= 6:
        bonus += 5
    if st["draw"] >= 2:
        bonus += 3
    if st["en"] >= 1:
        bonus += 3
    if st["size"] > 18:
        bonus -= 1.5 * (st["size"] - 18)
    bonus -= 4 * st["curse"]
    bonus += min(8.0, st["up"] * 1.2)
    sc = _clamp(core * 0.58 + avg * 0.22 + bonus + 4)
    g = "A" if sc >= 62 else "B" if sc >= 50 else "C" if sc >= 38 else "D"
    gaps = needs(st)
    need = [NEED_LABEL[k] for k in gaps]
    tail = ("缺口：" + "、".join(need)) if need else "结构完整"
    return {"sc": sc, "g": g, "need": need,
            "why": f"核心8张均{core:.0f}/整组{avg:.0f}，{tail}"}


def _why_parts(parts):
    return "；".join([p for p in parts if p][:4])


def card_eval(card, st, idx=0):
    """一张牌的 0–100 分；不做胜率承诺。"""
    c = _card_model(card, idx)
    typ = str(c.get("t") or "?")
    cid = str(c.get("id") or "").upper()
    name = str(c.get("n") or cid)
    if typ in ("C", "T"):
        return {"i": idx, "sc": 0, "tag": "dead", "why": "诅咒/状态牌：不抓"}

    base = {"A": 38, "S": 36, "P": 52}.get(typ, 30)
    parts = []
    tag = []

    cost = max(1, _int(c.get("c"), 0))
    dmg = max(0, _int(c.get("dmg"), 0)) * max(1, _int(c.get("hits"), 1))
    blk = max(0, _int(c.get("blk"), 0))
    if dmg:
        add = min(26, int(round(dmg / cost * 1.7)))
        base += add
        parts.append(f"{dmg}伤/{cost}费+{add}")
    if blk:
        add = min(22, int(round(blk / cost * 2.0)))
        base += add
        parts.append(f"{blk}甲/{cost}费+{add}")
        if st["blk"] < 6:
            base += 6
            tag.append("block")
    draw = _int(c.get("draw"), 0)
    if draw:
        add = min(12, draw * 5)
        base += add
        parts.append(f"抽{draw}+{add}")
        if st["draw"] < 2:
            base += 4
            tag.append("draw")
    en = _int(c.get("en"), 0)
    if en:
        add = min(12, en * 6)
        base += add
        parts.append(f"回{en}能+{add}")
        if st["en"] < 1:
            base += 4
            tag.append("energy")
    if c.get("aoe"):
        add = 18 if st["aoe"] < 2 else 8
        base += add
        tag.append("aoe")
        parts.append(f"AOE缺口+{add}" if st["aoe"] < 2 else f"AOE+{add}")
    deb = _int(c.get("vuln"), 0) + _int(c.get("weak"), 0)
    if deb:
        add = min(10, deb * 5)
        base += add
        if st["debuff"] < 2:
            base += 3
            tag.append("debuff")
        parts.append(f"debuff+{add}")
    if typ == "P":
        tag.append("power")
        parts.append("能力牌")
    if c.get("hp"):
        base -= _int(c.get("hp"), 0) * 2
        parts.append(f"自伤{_int(c.get('hp'), 0)}")
    if c.get("x"):
        parts.append("X费")

    rarity = str(c.get("rarity") or "")
    if not rarity:
        rarity = str((sim._db({"card_id": cid}) or {}).get("rarity") or "")
    base += RARITY.get(rarity, 0)
    if cid.startswith(("STRIKE", "DEFEND")):
        base -= 18
        parts.append("基础牌-18")
    if c.get("up"):
        parts.append("已升级")
    size_pen = min(15.0, max(0, st["size"] - 14) * 2.2)
    if size_pen:
        base -= size_pen
        parts.append(f"牌组{st['size']}张-{size_pen:.0f}")
    if _int(c.get("c"), 0) >= 3 and dmg < 15 and blk < 15 and typ != "P":
        base -= 5
        parts.append("高费低效-5")

    return {"i": idx, "sc": _clamp(base), "tag": "+".join(tag) or "generic",
            "why": _why_parts(parts) or f"{name}：通用牌"}


def reward_eval(choice, st):
    opts = []
    for j, card in enumerate(choice.get("cards") or []):
        idx = _int(card.get("i"), j)
        e = card_eval(card, st, idx)
        opts.append(e)
    if not opts:
        # 奖励屏上的 Card 条目还没 claim：这时 card_options 必然是空的，不能说
        # 「没有卡牌选项」——模型会照着这句说「奖励没牌可拿」，可实际上有牌，
        # 而且直接 collect 会被 mod 静默塞第一张进牌组（实机反馈「拿了也说没拿」）。
        has_card_item = any("card" in str(x.get("t") or "").lower()
                            for x in (choice.get("items") or []) if isinstance(x, dict))
        if has_card_item:
            return {"mode": "reward", "opts": [], "best": None, "skip": False,
                    "bar": 0, "why": "卡牌奖励还没打开：先 claim_reward 再看牌/跳过"}
        return {"mode": "reward", "opts": [], "best": None, "skip": True,
                "bar": 0, "why": "没有卡牌选项"}
    best = max(opts, key=lambda x: (x["sc"], -x["i"]))
    if st["size"] >= 18:
        bar = 62
    elif st["size"] >= 16:
        bar = 54
    else:
        bar = 42
    skip = best["sc"] < bar
    if skip:
        why = f"最高{best['sc']}分 < 门槛{bar}：跳过奖励"
    else:
        why = f"{best['tag']}（{best['sc']}分）≥{bar}：可拿"
    return {"mode": "reward", "opts": opts, "best": best["i"], "skip": skip,
            "bar": bar, "why": why}


def _upgrade_score(card, st, idx=0):
    c = _card_model(card, idx)
    if c.get("up"):
        return {"i": idx, "sc": 0, "why": "已升级"}
    cid = str(c.get("id") or "").upper()
    if cid.startswith("STRIKE"):
        return {"i": idx, "sc": 5, "why": "打击不优先升级"}
    if cid.startswith("DEFEND"):
        return {"i": idx, "sc": 12, "why": "防御不优先升级"}
    sc = card_eval(card, st, idx)["sc"] + UPGRADE_PRIORITY.get(cid, 0)
    return {"i": idx, "sc": _clamp(sc),
            "why": f"升级收益+{UPGRADE_PRIORITY.get(cid, 0)}" if cid in UPGRADE_PRIORITY
            else "按通用牌面"}


def upgrade_target(deck, st):
    opts = [_upgrade_score(c, st, i) for i, c in enumerate(deck or [])
            if isinstance(c, dict) and not c.get("upgraded")]
    opts = [o for o in opts if o["sc"] > 0]
    if not opts:
        return None
    b = max(opts, key=lambda x: x["sc"])
    c = _card_model(deck[b["i"]], b["i"])
    return {"id": c.get("id"), "n": c.get("n"), "sc": b["sc"], "why": b["why"]}


def _mode_from_prompt(prompt):
    p = str(prompt or "").lower()
    if "升级" in p or "upgrade" in p:
        return "upgrade"
    if "移除" in p or "删除" in p or "remove" in p:
        return "remove"
    if "变化" in p or "transform" in p:
        return "transform"
    if "复制" in p or "duplicate" in p:
        return "copy"
    return "choose"


def _remove_score(card, st, idx=0):
    c = _card_model(card, idx)
    cid = str(c.get("id") or "").upper()
    base = 100 - card_eval(card, st, idx)["sc"]
    why = []
    if cid.startswith("STRIKE"):
        base += 20
        why.append("优先删打击")
    elif cid.startswith("DEFEND"):
        base += 12
        why.append("可删防御")
    if cid in CORE:
        base -= 45
        why.append("核心不删")
    if c.get("t") in ("C", "T"):
        base += 35
        why.append("诅咒/状态优先删")
    if st["size"] > 16:
        base += 8
        why.append("牌组偏厚")
    return {"i": idx, "sc": _clamp(base), "tag": "remove",
            "why": _why_parts(why) or "按牌面弱项"}


def select_eval(choice, st):
    mode = _mode_from_prompt(choice.get("prompt") or "")
    opts = []
    for j, card in enumerate(choice.get("cards") or []):
        idx = _int(card.get("i"), j)
        if mode == "upgrade":
            e = _upgrade_score(card, st, idx)
            e["tag"] = "upgrade"
        elif mode in ("remove", "transform"):
            e = _remove_score(card, st, idx)
        else:
            e = card_eval(card, st, idx)
            if mode == "copy":
                e["sc"] = _clamp(e["sc"] + 10)
                e["why"] = "复制高质量牌"
        opts.append(e)
    if not opts:
        return {"mode": mode, "opts": [], "best": None, "skip": True, "why": "没有可选牌"}
    best = max(opts, key=lambda x: (x["sc"], -x["i"]))
    skip = bool(choice.get("min") == 0 and best["sc"] < 30)
    return {"mode": mode, "opts": opts, "best": best["i"], "skip": skip,
            "why": "可跳过（最低分选项仍很差）" if skip else f"最高{best['sc']}分"}


def rest_eval(choice, state, st):
    run = state.get("run") or {}
    hp = _int(run.get("current_hp", run.get("hp")), 0)
    mhp = max(1, _int(run.get("max_hp"), hp or 1))
    pct = hp / mhp if mhp else 0.0
    heal = min(mhp - hp, int(round(0.30 * mhp)))
    up = upgrade_target(run.get("deck") or [], st)
    pw = deck_power(run.get("deck") or [])
    opts = []
    for j, o in enumerate(choice.get("opts") or []):
        idx = _int(o.get("i"), j)
        name = str(o.get("n") or o.get("title") or "").lower()
        if not o.get("ok", True):
            opts.append({"i": idx, "sc": 0, "why": "选项不可用"})
            continue
        if "休息" in name or "sleep" in name or "rest" in name or "回复" in name:
            sc = 40 + 60 * (1 - pct) + (15 if pct < 0.50 else 0)
            sc += 8 if pw["sc"] < 48 else 0      # 卡组弱，多睡一会也合理
            why = f"回复约{heal}血（{pct:.0%}，卡组{pw['g']}）"
        elif "锻造" in name or "smith" in name or "升级" in name or "upgrade" in name:
            # 用户口径：血 50% 以上就可以按形势升级强力牌/关键牌，不是一律睡。
            sc = 52 + (pw["sc"] - 45) * 0.7
            sc += 9 if pw["g"] in ("A", "B") else 0
            if up:
                sc += (up["sc"] - 45) * 0.6      # 升级目标越强越值得
            else:
                sc = 0
                why = "没有可升级牌"
            if pct < 0.50:
                sc -= 30
            elif pct < 0.60:
                sc -= 6
            why = why if not up else f"升级{up['n']}（{up['sc']}分）；血{pct:.0%}、卡组{pw['g']}"
        else:
            sc = 40
            why = "未知选项"
        opts.append({"i": idx, "sc": _clamp(sc), "why": why})
    if not opts:
        return {"mode": "rest", "opts": [], "best": None, "heal": heal,
                "up": up, "pw": pw, "why": "没有选项"}
    best = max(opts, key=lambda x: (x["sc"], -x["i"]))
    return {"mode": "rest", "opts": opts, "best": best["i"], "heal": heal,
            "up": up, "pw": pw, "why": f"{best['why']}；{best['sc']}分"}


def _price_eff(price):
    return max(0.0, min(1.0, 1.0 - max(0, price - 50) / 150.0))


def _item_base(item):
    sec = str(item.get("sec") or "")
    name = str(item.get("n") or "")
    fx = str(item.get("fx") or "")
    text = name + fx
    if sec == "potions":
        base = 46
    elif sec == "relics":
        base = 54
    else:
        base = 50
    if any(k in text for k in ("能量", "抽", "格挡", "回复", "力量", "敏捷")):
        base += 8
    if any(k in text for k in ("诅咒", "失去", "伤害")):
        base -= 6
    return base


def shop_eval(choice, st):
    gold = _int(choice.get("gold"), 0)
    opts = []
    for j, it in enumerate(choice.get("items") or []):
        sec = str(it.get("sec") or "")
        idx = _int(it.get("i"), j)
        price = _int(it.get("price"), 0)
        ok = bool(it.get("ok", True)) and price <= gold
        if not ok:
            opts.append({"sec": sec, "i": idx, "sc": 0, "why": "买不起/已售"})
            continue
        if sec == "cards":
            card = sim.parse_card({"card_id": it.get("id"), "energy_cost": it.get("c")}, idx)
            base = card_eval(card, st, idx)["sc"]
            why = "补缺口" if base >= 55 else "牌面一般"
        elif sec == "removal":
            base = 70 if st["size"] >= 16 else (52 if st["size"] >= 14 else 38)
            why = "删基础牌" if st["size"] >= 16 else "牌组不厚"
        else:
            base = _item_base(it)
            why = "资源"
        sc = base * 0.8 + 20 * _price_eff(price)
        opts.append({"sec": sec, "i": idx, "sc": _clamp(sc),
                     "why": f"{why}；{price}金"})
    rem = choice.get("removal") or {}
    if rem:
        price = _int(rem.get("price"), 0)
        ok = bool(rem.get("ok", True)) and price <= gold
        base = 70 if st["size"] >= 16 else (52 if st["size"] >= 14 else 38)
        opts.append({"sec": "removal", "i": 0, "sc": _clamp(base * 0.8 + 20 * _price_eff(price))
                     if ok else 0, "why": f"删牌；{price}金" if ok else "买不起"})
    buy = sorted([o for o in opts if o["sc"] >= 60], key=lambda x: x["sc"], reverse=True)
    return {"mode": "shop", "opts": opts, "buy": buy[:3],
            "best": buy[0] if buy else None,
            "why": f"预算{gold}金；按缺口/价格排序"}


def map_eval(choice, state, st, pw):
    """选路提示：血量 × 卡组强度 × 节点类型。

    不是替模型做决定，只把「血够但卡组弱」这种负收益选项压下去；
    精英分随卡组强度走，营火/商店在低血或弱卡组时加权。
    """
    run = state.get("run") or {}
    hp = _int(run.get("current_hp", run.get("hp")), 0)
    mhp = max(1, _int(run.get("max_hp"), hp or 1))
    pct = hp / mhp if mhp else 0.0
    gold = _int(run.get("gold"), 0)
    sc_pw = _int(pw.get("sc"), 50)
    opts = []
    for j, o in enumerate(choice.get("opts") or []):
        t = str(o.get("t") or "").lower()
        idx = _int(o.get("i"), j)
        if t == "elite":
            sc = 44 + (sc_pw - 50) * 0.8
            if pct < 0.55:
                sc -= 32
            elif pct < 0.60:
                sc -= 16
            sc += 7 if pw.get("g") in ("A", "B") else 0
            why = f"精英：血{pct:.0%}、卡组{pw.get('g')}（{sc_pw}）"
        elif t in ("restsite", "rest"):
            sc = 46 + (14 if pct < 0.65 else 0) + (10 if sc_pw < 50 else 0)
            why = f"营火：血{pct:.0%}，可回血/升级"
        elif t == "shop":
            sc = 42 + (10 if gold >= 120 else 0) + (8 if sc_pw < 50 else 0)
            why = f"商店：{gold}金、卡组{pw.get('g')}"
        elif t == "treasure":
            sc = 50
            why = "宝箱：遗物"
        elif t in ("event", "unknown"):
            sc = 48 + (6 if pct >= 0.60 else 0)
            why = "事件：可能补牌/回血"
        elif t == "monster":
            sc = 45 - (12 if pct < 0.45 else 0) + max(0, sc_pw - 50) * 0.2
            why = f"小怪：血{pct:.0%}"
        elif t in ("boss", "ancient"):
            sc, why = 90, "Boss/出口"
        else:
            sc, why = 44, f"{o.get('t')}"
        opts.append({"i": idx, "t": t, "sc": _clamp(sc), "why": why})
    if not opts:
        return {"mode": "map", "opts": [], "best": None, "why": "没有可选节点"}
    best = max(opts, key=lambda x: (x["sc"], -x["i"]))
    return {"mode": "map", "opts": opts, "best": best["i"], "pw": pw,
            "why": f"推荐 {best['why']}"}


def evaluate(state, choice):
    kind = str(choice.get("kind") or "")
    if kind not in ("reward", "shop", "rest", "select", "map"):
        return None
    st = deck_stats((state.get("run") or {}).get("deck") or [])
    pw = deck_power((state.get("run") or {}).get("deck") or [])
    out = {"deck": st, "need": needs(st), "pw": pw}
    if kind == "map":
        out.update(map_eval(choice, state, st, pw))
    elif kind == "reward":
        out.update(reward_eval(choice, st))
    elif kind == "shop":
        out.update(shop_eval(choice, st))
    elif kind == "rest":
        out.update(rest_eval(choice, state, st))
    else:
        out.update(select_eval(choice, st))
    return out
