# -*- coding: utf-8 -*-
"""单回合战斗模拟器（本地算账，模型只做取舍）。

为什么要有这一层
----------------
v19 的 `advisor._simulate` 只认「伤害/格挡/易伤/虚弱」，于是：
  · 放血/祭品回的能量看不见 → 明明能多打一张，计划却按 3 费封顶；
  · 无情猛攻/踩踏的变费看不见 → 下一张攻击实际 0 费，却被当成 2 费拒掉；
  · 抽牌、消耗、加牌、药水全不在模型里 → 计划只会在「当前手牌」里打转；
  · 击杀连锁（杀一只→另一只吃尸眩晕）只按计划尾部补算，不会反过来影响选牌。

本模块把「一张牌打出去，能量/血/格挡/易伤/抽牌/消耗/变费怎么变」逐步模拟，
供 ctx.py 生成候选计划、act.py 校验模型给的顺序。数值全部来自实时 state，
卡牌文字只用来识别「这张牌会不会改费用/加牌/消耗」，不替代实时数值。

对外接口
--------
    sim = Sim.from_state(state)
    sim.cost_of(card)
    sim.play(card, target_index) -> step dict
    sim.use_potion(slot, target_index) -> step dict
    sim.end_turn() -> 结算 dict
    plans = search(state, max_cards=8, max_plans=5) -> [plan dict, ...]
    ok, err, sim, steps = validate(state, seq)
"""
from __future__ import annotations

import os
import re
import sys
import time

# 允许直接跑本文件：decision 目录提供 sts2 / 其它本地模块。
_HERE = os.path.dirname(os.path.abspath(__file__))
_PACK = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(_PACK))
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sts2  # noqa: E402

TYPES = {"Attack": "A", "Skill": "S", "Power": "P", "Status": "T", "Curse": "C"}
TYPE_CN = {"A": "攻", "S": "技", "P": "能", "T": "状", "C": "诅", "?": "?"}

# 回合末状态牌税：按当前 STS2 卡库拆成「可格挡伤害」与「无视格挡失去生命」。
# 旧表把 SLIMED/WOUND 当成 3 伤，但当前卡库 Slimed 是 1 费抽 1、Wound 无效果。
STATUS_DAMAGE = {
    "BURN": 2, "INFECTION": 3, "DECAY": 2, "TOXIC": 5, "DISINTEGRATION": 6,
}
STATUS_HP_LOSS = {
    "BAD_LUCK": 13, "BECKON": 6, "CALL": 6,  # CALL 是旧 id，保留兼容
}
STATUS_HP_PER_CARD = {"REGRET": 1}
STATUS_TAX = {
    **STATUS_DAMAGE, **STATUS_HP_LOSS,
    "SLIMED": 0, "WOUND": 0, "DAZED": 0, "VOID": 0,
}


def status_tax_parts(hand) -> tuple[int, int]:
    """未打出的状态牌回合末税：``(可被格挡的伤害, 无视格挡的失去生命)``。

    只认当前卡库真实有回合末掉血/失血的牌；REGRET 按手牌张数逐张扣。
    未知状态牌按 0 处理并在文档里标 unknown，不猜伤害。
    """
    dmg = hp = 0
    live = [c for c in (hand or []) if isinstance(c, dict) and not c.get("_played")]
    for c in live:
        cid = str(c.get("id") or c.get("card_id") or "").upper()
        dmg += STATUS_DAMAGE.get(cid, 0)
        hp += STATUS_HP_LOSS.get(cid, 0)
        if cid in STATUS_HP_PER_CARD:
            hp += STATUS_HP_PER_CARD[cid] * len(live)
    return dmg, hp


def _num(name: str, text: str, default=0) -> int:
    """从卡牌文字里取「X点<name>」的 X（只用于文字型效果兜底）。"""
    m = re.search(r"(\d+)\s*点\s*" + re.escape(name), text or "")
    return int(m.group(1)) if m else default


def _clean_card_text(card: dict) -> str:
    return sts2._strip_tags(str(card.get("resolved_rules_text") or card.get("rules_text") or ""))


def _db(card: dict) -> dict:
    return sts2.CARD_DB.get(str(card.get("card_id") or "")) or {}


def _dyn(card: dict, *names: str):
    v = sts2._card_live(card, set(names))
    return int(v) if v is not None else None


def card_type(card: dict) -> str:
    cid = str(card.get("card_id") or "")
    t = str(card.get("card_type") or card.get("type") or _db(card).get("type") or "")
    if t in TYPES:
        return TYPES[t]
    if cid.startswith(("STRIKE", "DEFEND")):
        return "A" if cid.startswith("STRIKE") else "S"
    return "?"


def _cost_mod(text: str, cid: str = "") -> dict:
    """识别改费牌。只影响模拟，不改游戏（文字优先，id 兜底）。"""
    cid = cid.upper()
    if cid == "UNRELENTING":
        return {"na": 1}
    if cid == "STOMP":
        return {"per": 1}
    if cid == "CORRUPTION":
        return {"sk0": 1}
    t = (text or "").replace(" ", "")
    if ("下一张攻击牌" in t or "下一张的攻击牌" in t) and ("耗能变为0" in t or "耗能为0" in t or "耗能降为0" in t):
        return {"na": 1}
    if "下一张技能牌" in t and ("耗能变为0" in t or "耗能为0" in t or "耗能降为0" in t):
        return {"ns": 1}
    if "技能牌消耗变为0" in t or ("技能牌" in t and "耗能变为0" in t and "本场" in t):
        return {"sk0": 1}
    if "每打出过一张攻击牌" in t and "耗能减少" in t:
        return {"per": 1}
    if "这张牌的耗能降为0" in t:
        return {"self0": 1}
    if "耗能加1" in t:
        return {"self+": 1}
    if "耗能减少1" in t and "这张牌" in t:
        return {"self-": 1}
    return {}


def parse_card(card: dict, idx: int | None = None) -> dict:
    """把手牌压成模拟用的牌模型（字段用短名，ctx 直接复用）。"""
    cid = str(card.get("card_id") or card.get("id") or "?")
    db = _db(card)
    text = _clean_card_text(card)
    out = {
        "i": idx, "id": cid, "n": str(card.get("name") or db.get("name") or cid),
        "c": max(0, sts2._int(card.get("energy_cost", card.get("cost")), sts2._int(db.get("cost"), 0))),
        # 卡库原费：引擎在「免费攻击」生效时会把所有攻击牌报成 0 费，
        # 需要原费才能还原「这一张本来要几费」。
        "c_base": max(0, sts2._int(db.get("cost"), 0)),
        "t": card_type(card),
        "up": 1 if (card.get("upgraded") or card.get("is_upgraded")) else 0,
        "play": card.get("playable", True) is not False,
        "x": bool(card.get("costs_x") or db.get("is_x_cost")),
    }
    if not out["play"]:
        out["why"] = str(card.get("unplayable_reason") or "")[:24]

    # 引擎给的实时牌面（ctx 用它，而不是 DB 里可能过期的中文描述）
    if text:
        out["ltx"] = text
        # 永恒 ETERNAL：不能被移除（营养汤把打击附成 0 费+永恒，商店删不掉）
        if "永恒" in text:
            out["eternal"] = 1

    # 狂怒 RAGE：卡面数字在 DB 里是变量占位符（"获得点格挡"），引擎实测 3 点/攻击。
    if str(cid).upper().startswith("RAGE"):
        out["rage"] = 5 if out["up"] else 3

    prof = sts2._attack_profile(card)
    # 状态/诅咒牌不是攻击牌：引擎也会给它们一个 Damage 字段（毒素 5 点是「回合末自己掉血」），
    # 直接当攻击伤害喂给 sim 会把 5 点算到敌人头上（run HECC0H1NUR f30 实测差 10 点）。
    if out["t"] in ("T", "C"):
        prof = None
    if prof:
        per, fixed, cond = prof
        out["dmg"], out["hits"], out["cond"] = int(per), max(1, fixed), max(0, cond)
    else:
        dmg = _dyn(card, "Damage", "CalculatedDamage")
        if dmg is None:
            dmg = sts2._int(db.get("damage"), 0)
        if dmg and out["t"] in ("T", "C"):
            dmg = 0  # 状态牌的 "Damage" 是回合末自伤，不是攻击
        if dmg:
            hits = max(1, sts2._int(db.get("hit_count"), 1))
            out["dmg"], out["hits"] = int(dmg), hits
            fixed, cond = sts2._hits_from_text(card)
            out["hits"], out["cond"] = max(out["hits"], fixed), cond
    blk = _dyn(card, "Block")
    if blk is None:
        blk = sts2._int(db.get("block"), 0)
    if blk:
        out["blk"] = int(blk)
    draw = _dyn(card, "Cards")
    if draw is None:
        draw = sts2._int(db.get("cards_draw"), 0)
    if draw:
        out["draw"] = int(draw)
    en = _dyn(card, "Energy", "CalculatedEnergy")
    if en is None:
        en = sts2._int(db.get("energy_gain"), 0)
    if en and out["t"] in ("P", "S") and ("回合开始时" in text or "被消耗时" in text):
        # 延迟给费：薪火之源（回合开始时获得）、战鼓（被消耗时获得）。
        # 引擎的 Energy 字段照样返回 2，但本回合拿不到 —— 按即时回能算会多算一整回合的费
        # （run Z6DHD2PJBQ f25 / 6E26GF0HPM f9 实测：预测 5 费，实际 3 费）。
        out["en_deferred"] = int(en)
        en = 0
    if en:
        out["en"] = int(en)
    hp = _dyn(card, "HpLoss")
    if hp is None:
        hp = sts2._int(db.get("hp_loss"), 0)
    if hp:
        out["hp"] = int(hp)
    for key, short in (("VulnerablePower", "vuln"), ("WeakPower", "weak"),
                       ("FrailPower", "frail"), ("StrengthPower", "str"),
                       ("DexterityPower", "dex"), ("PlatingPower", "plating")):
        v = _dyn(card, key)
        if v is None:
            for ap in db.get("powers_applied") or []:
                if isinstance(ap, dict) and str(ap.get("power") or "").lower() == key.replace("Power", "").lower():
                    v = sts2._int(ap.get("amount"), 0)
                    break
        if v:
            out[short] = int(v)

    if "所有敌人" in text or str(card.get("target_type") or db.get("target") or "") in ("AllEnemies",):
        out["aoe"] = True
    cm = _cost_mod(text, cid)
    if cm:
        out["cm"] = cm
    # 消耗牌：打出去就进消耗牌堆。契约终结/邪眼/被遗忘的仪式/午夜都吃「消耗过几张」，
    # 所以这几张的牌序必须排在消耗源之后（实机反馈：老是先打支付牌，白亏）。
    if "消耗" in text and ("消耗。" in text or text.endswith("消耗") or "消耗。" in text):
        out["exh"] = 1
    # 加牌：只关心「加入手牌」的即时张数
    if "加入你的手牌" in text:
        n = _num("张", text, 1)
        if "将" in text:
            m = re.search(r"将(\d+)张", text)
            n = int(m.group(1)) if m else n
        out["add"] = max(1, n)
    # 添柴/Stoke：消耗所有手牌，再按消耗张数随机生成 —— 生成什么是随机的，
    # 本地账本不能拿旧手牌继续排（实机：打完添柴还按原计划出牌，新牌白瞎）。
    if "消耗所有手牌" in text or ("消耗" in text and "随机" in text
                                 and "加入你的手牌" in text):
        out["gen"] = 1
    if out["t"] == "P":
        out["pw"] = cid
    # ---- 跨回合收益（下一轮出牌能不能吃到）：留着不散的才写 ----
    # 敌人身上的易伤/虚弱/脆弱、自己身上的力量/敏捷/覆甲、能力牌、保留、下回合抽牌。
    nxt = {}
    for k in ("vuln", "weak", "frail", "str", "dex", "plating"):
        if out.get(k):
            nxt[k] = out[k]
    if out["t"] == "P":
        nxt["power"] = 1
    if "保留" in text:
        nxt["retain"] = 1
    m = re.search(r"下回合(?:开始时)?抽(?:取)?(\d+)张", text)
    if m:
        nxt["draw"] = int(m.group(1))
    if "覆甲" in text and "plating" not in nxt:
        nxt["plating"] = _num("点", text, 1)
    if "壁垒" in text:
        nxt["block_keep"] = 1
    if nxt:
        out["nxt"] = nxt
    # 少数「按手牌/消耗堆算」的牌：实时数值不够，补语义
    if cid == "BODY_SLAM" and "dmg" not in out:
        out["dmg"], out["hits"] = 0, 1
    if cid == "SECOND_WIND":
        out["exh_hand"] = "nonatk"
    if cid in ("FIEND_FIRE", "STOKE"):
        out["exh_hand"] = "all"
    if cid == "PACT_END":
        out["pact"] = 1
    if cid == "SPITE":
        out["spite"] = 1
    return out


def parse_potion(p: dict) -> dict | None:
    if not isinstance(p, dict) or not p.get("occupied"):
        return None
    text = sts2._strip_tags(str(p.get("description") or ""))
    out = {"p": sts2._int(p.get("index"), 0), "id": str(p.get("potion_id") or ""),
           "n": str(p.get("name") or p.get("potion_id") or ""), "d": text[:80],
           "t": "enemy" if p.get("requires_target") else "self",
           "use": bool(p.get("can_use"))}
    for key, pat in (("dmg", r"造成(\d+)点伤害"), ("blk", r"获得(\d+)点格挡"),
                     ("en", r"获得(\d+)点能量"), ("draw", r"抽(\d+)张"),
                     ("heal", r"回复(\d+)点生命"), ("vuln", r"给予(\d+)层易伤"),
                     ("weak", r"给予(\d+)层虚弱"), ("str", r"获得(\d+)点力量"),
                     ("dex", r"获得(\d+)点敏捷")):
        m = re.search(pat, text)
        if m:
            out[key] = int(m.group(1))
    if "所有敌人" in text:
        out["aoe"] = True
    # 固化药水这类「把格挡变成 N 倍」：卡面没有数字，不认就会在 fx.hp 上算错
    # （run82 打猎人杀手时被这条坑过一次）。
    m = re.search(r"格挡(?:变为|变成|增加至|翻)(?:\D{0,3})([二三两1-3])倍", text)
    if m:
        mul = {"二": 2, "两": 2, "三": 3}.get(m.group(1), 0)
        if not mul and m.group(1).isdigit():
            mul = int(m.group(1))
        if mul:
            out["blk_mul"] = mul
            out["use"] = bool(p.get("can_use"))
    # 药水也标一下「这一瓶能不能喂下一轮」：力量/敏捷/易伤/虚弱都是跨回合的
    nxt = {k: out[k] for k in ("vuln", "weak", "str", "dex") if out.get(k)}
    if nxt:
        out["nxt"] = nxt
    return out


def _power_ids(powers) -> dict:
    """power_id/中文名 → 层数。"""
    out = {}
    for p in powers or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("power_id") or p.get("id") or "").upper()
        if pid:
            out[pid] = sts2._int(p.get("amount"), 1)
    return out


def _free_attack_count(powers) -> int:
    """未消耗的「免费攻击 / FREE_ATTACK」层数：只有下一张攻击牌是 0 费。

    run85 f11/f17/f20/f21 四次 batch 中断的根因：引擎在 buff 生效时把**所有**
    攻击牌的 energy_cost 都报成 0，ctx 照抄 → 计划按「全都免费」算能量，
    实际只有一张免费，打到第三张就断供。这里把 buff 还原成「还剩几张免费」。
    """
    n = 0
    for p in powers or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("power_id") or p.get("id") or "").upper()
        if pid in ("FREE_ATTACK", "FREE_ATTACK_POWER"):
            n += max(1, sts2._int(p.get("amount"), 1))
    return n


def _turn_start_hp_loss(powers) -> int:
    """回合开始时自己掉血的 power（狱火 INFERNO）：进回合末血量预测。

    run85 的 take 误差里 12/35 是固定 +1 —— 都是狱火那 1 点回合初自伤没算。
    """
    total = 0
    for p in powers or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("power_id") or p.get("id") or "").upper()
        blob = f"{p.get('name') or ''}{p.get('description') or ''}"
        if "INFERNO" in pid or "狱火" in blob:
            total += max(1, sts2._int(p.get("amount"), 1))
    return total


# ---------------------------------------------------------------- 建模边界（必须说出来）
# 「没建模」不等于「不会发生」：下面这些会改预测，但 sim 目前不模拟。
# 遇到就在 ctx / forecast 里标出来（`unmodeled` / `approx`），让模型知道这一手是**估的**，
# 而不是拿一个精确到个位的数字去赌斩杀。
UNMODELED_POWERS = {
    "ESCAPE_ARTIST_POWER": "N 回合后离场：还要不要挡会变，本模型只按当前意图算",
    "PLATING_POWER": "敌人回合末给自己覆甲：斩杀线会变，本模型不算它给自己上甲",
}
UNMODELED_RELICS = {
    "GORGET": "战斗开始 +4 覆甲：本模型没算",
    "CANDELABRA": "第 2 回合开始 +2 费：本模型按 3 费算",
    "PAELS_FLESH": "第 3 回合起 +1 费：本模型按 3 费算",
    "MUMMIFIED_HAND": "打能力牌后随机一张手牌本回合免费：本模型不算",
    "ORNAMENTAL_FAN": "同回合第 3 张攻击 +4 甲：本模型没算",
}


def unmodeled_parts(enemies, powers, relic_ids) -> list[dict]:
    """收集「这一局里存在、但 sim 没建模」的机制，供 ctx/forecast 如实标注。"""
    out: list[dict] = []
    seen: set[str] = set()

    def _add(pid: str, why: str):
        if pid and pid not in seen:
            seen.add(pid)
            out.append({"id": pid, "why": why})

    for e in enemies or []:
        if not isinstance(e, dict):
            continue
        for p in e.get("powers") or []:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("power_id") or p.get("id") or "").upper()
            if pid in UNMODELED_POWERS:
                _add(pid, UNMODELED_POWERS[pid])
    for p in powers or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("power_id") or p.get("id") or "").upper()
        if pid in UNMODELED_POWERS:
            _add(pid, UNMODELED_POWERS[pid])
    for rid in sorted({str(x).upper() for x in (relic_ids or ())}):
        if rid in UNMODELED_RELICS:
            _add(rid, UNMODELED_RELICS[rid])
    return out


def _stun_threshold(e: dict) -> int:
    """「横冲直撞 N」/「尖叫」：血量第一次跌破阈值时这回合的攻击直接取消。

    仪式兽实测阈值是 buff 里写的 160（机制文案写 150 是错的），所以从
    powers 的「横冲直撞NNN」里取数字，取不到就当没有这个机制。
    尖叫 SHRIEK_POWER（run85 f11 骇鳗）：生命值第一次降到 50% 或以下就自晕，
    power 的 amount 就是 50% 血线（150 血的骇鳗 amount=75）。
    """
    maxhp = sts2._int(e.get("max_hp"), 0)
    for p in e.get("powers") or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("power_id") or p.get("id") or "").upper()
        blob = f"{p.get('name') or ''}{p.get('power_id') or ''}{p.get('description') or ''}"
        if "SHRIEK_POWER" not in pid and "尖叫" not in blob:
            if "横冲直撞" not in blob:
                continue
        n = sts2._int(p.get("amount"), 0)
        if "SHRIEK_POWER" in pid or "尖叫" in blob:
            if 0 < n < maxhp:
                return n
            return max(1, maxhp // 2)
        m = re.search(r"横冲直撞\s*(\d+)", blob) or re.search(r"(\d+)", blob)
        if m:
            n = sts2._int(m.group(1), 0)
            if 0 < n < 10000:
                return n
    return 0


def enemy_state(e: dict, idx: int | None = None) -> dict:
    pid = _power_ids(e.get("powers"))
    pm = sts2._power_map(e.get("powers"))
    final_atk, raw_atk = sts2._intent_damage_parts(e)
    types = [str(x.get("intent_type") or x.get("type") or "?")
             for x in (e.get("intents") or []) if isinstance(x, dict)]
    ravenous = "RAVENOUS_POWER" in pid or any("当有敌人死亡" in str(x.get("description") or "") for x in (e.get("powers") or []) if isinstance(x, dict))
    return {
        "i": sts2._int(e.get("index", idx if idx is not None else 0), 0),
        "id": str(e.get("enemy_id") or "?"), "n": str(e.get("name") or "?"),
        "hp": sts2._int(e.get("current_hp", e.get("hp")), 0),
        "max": sts2._int(e.get("max_hp"), 0),
        "blk": sts2._int(e.get("block"), 0),
        "atk": final_atk + sum(per * hits for per, hits in raw_atk),
        "atk_final": final_atk, "atk_raw": raw_atk,
        "types": types,
        "vuln": sts2._int(pm.get("vuln"), 0), "weak": sts2._int(pm.get("weak"), 0),
        # 虚弱/意图在回合开始时已锁定：本回合新挂的虚弱不改这一回合的攻击伤害（实测）
        "weak0": sts2._int(pm.get("weak"), 0),
        "timid": sts2._int(pm.get("timid"), 0), "thorns": sts2._int(pm.get("thorns"), 0),
        "slippery": sts2._int(pid.get("SLIPPERY_POWER"), 0),
        "artifact": sts2._int(pm.get("artifact"), 0),
        "intangible": sts2._int(pm.get("intangible"), 0),
        "ravenous": ravenous,
        "minion": "MINION_POWER" in pid,
        # 振翅：攻击牌伤害 -50%；幻象：死了下回合满血复活（都不该被当成普通目标）
        "flutter": 1 if "FLUTTER_POWER" in pid else 0,
        "illusion": 1 if "ILLUSION_POWER" in pid else 0,
        "escape": sts2._int(pid.get("ESCAPE_ARTIST_POWER"), 0),
        "dead": not (e.get("is_alive", True) and sts2._int(e.get("current_hp", e.get("hp")), 1) > 0),
        "stunned": False, "str": sts2._int(pm.get("str"), 0),
        "stun_at": _stun_threshold(e) or sts2._int(e.get("stun_at"), 0),
        "_stun_done": False,
        "hit_cap": _hit_cap(e), "dig": _has_power(e, ("钻洞", "埋地", "DIG")),
        # 硬化外壳：本回合最多失去 N 点生命（累计），用 _lost_turn 记账
        "hp_loss_cap": _hp_loss_cap(e), "_lost_turn": 0,
        "_dig_done": False,
    }


def _has_power(e: dict, keys: tuple) -> int:
    for p in e.get("powers") or []:
        if not isinstance(p, dict):
            continue
        blob = f"{p.get('name') or ''}{p.get('power_id') or ''}{p.get('description') or ''}".upper()
        if any(str(k).upper() in blob for k in keys):
            return 1
    return 0


def _hit_cap(e: dict) -> int:
    """「难以杀灭 N」：单次伤害封顶 N（破击 30 也只算 9）。"""
    for p in e.get("powers") or []:
        if not isinstance(p, dict):
            continue
        blob = f"{p.get('name') or ''}{p.get('description') or ''}"
        if "难以杀灭" not in blob and "HARD_TO_KILL" not in blob.upper():
            continue
        n = sts2._int(p.get("amount"), 0)
        if not n:
            m = re.search(r"(\d+)", blob)
            n = sts2._int(m.group(1), 0) if m else 0
        if 0 < n < 1000:
            return n
    return 0


def _hp_loss_cap(e: dict) -> int:
    """「硬化外壳 N」：这只怪**每回合**最多失去 N 点生命（run86 f15 鬼祟珊瑚群 45→25）。

    和「难以杀灭 N」（单次封顶）不是一回事：这是**整回合累计**的上限，所以
    45 血的怪看着能一刀 57 斩杀，实际这一回合最多掉 20 → 不存在斩杀线。
    """
    for p in e.get("powers") or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("power_id") or p.get("id") or "").upper()
        blob = f"{p.get('name') or ''}{p.get('description') or ''}"
        if "HARDENED_SHELL" not in pid and "硬化外壳" not in blob:
            continue
        n = sts2._int(p.get("amount"), 0)
        if not n:
            m = re.search(r"(\d+)", blob)
            n = sts2._int(m.group(1), 0) if m else 0
        if 0 < n < 1000:
            return n
    return 0


class Sim:
    """一份可复制的回合内状态。"""

    def __init__(self):
        self.hp = 0
        self.maxhp = 0
        self.block = 0
        self.energy = 0
        self.e_max = 0
        self.hand: list[dict] = []
        self.enemies: list[dict] = []
        self.potions: list[dict] = []
        self.attacks = 0
        self.skills = 0
        self.played = 0
        self.exhausts = 0
        self.draws = 0
        self.gen_unknown = 0
        self.selfhp = 0
        self.lost_hp_turn = 0
        self.maxhp_gain = 0
        self.str_bonus = 0
        self.dex_bonus = 0
        self.next_attack_zero = False
        self.next_skill_zero = False
        self.skills_zero = False
        # 「免费攻击」还有几张没花（来自 FREE_ATTACK buff）。和 next_attack_zero 分开：
        # 前者是进战斗/换回合时就挂着的，后者是本回合打了无情猛攻才给的。
        self.free_atk = 0
        self.free_discount = 0
        # 永冻冰晶：本场第一张能力牌给 7 甲（run85 有 6 个回合的入伤高估来自它）
        self.permafrost = 0
        self._pf_used = False
        # 狱火：回合开始时失去 N 点生命（算进回合末血量）
        self.tick = 0
        # 建模边界：这一局存在但 sim 没建模的机制（见 UNMODELED_*）
        self.unmodeled: list[dict] = []
        self.next_attack_twice = False
        self.barricade = False
        self.metallicize = 0
        self.plating = 0
        self.rage = 0
        self.fnp = 0
        self.dark_embrace = 0
        self.rupture = 0
        self.player_vuln = 0
        self.player_frail = 0
        self.player_weak = 0
        # run82/83 的四个机制：柔嫩（每打一张牌 -1 力 -1 敏）、缩小（攻伤×0.7）、
        # 缠结（攻击牌 +1 费）、失衡（攻击被完全格挡则眩晕）。
        self.tender = 0
        self.shrink = 0
        self.tangled = 0
        self.imbalanced = 0
        # 势不可当：每获得一次格挡就对随机敌人打 N 点。随机 → 模拟按「当前目标/第一只活怪」结算，
        # 多怪场里误差 ≤N，单怪场完全准确（run Z6DHD2PJBQ 实测漏算 8 点/次）。
        self.jugg = 0
        self.status_tax = 0
        self.thorns_taken = 0
        self.steps: list[dict] = []
        self.exhaust_pile = 0
        # 这一手里「第几步打完就把敌人全清了」（0 基步号）；None=没清场。
        # 清场之后战斗立刻结束，引擎不再接受出牌 —— 调用方据此拒绝「清场后补牌」（P14）。
        self.cleared_at: int | None = None

    def _dex_block(self) -> int:
        """批内新加的敏捷（快照里的敏捷已经在 card["blk"] 里了）。脆弱对这部分同样生效。"""
        if not self.dex_bonus:
            return 0
        if self.player_frail:
            return int(self.dex_bonus * 0.75)
        return self.dex_bonus

    def _mark_cleared(self, n: int) -> None:
        """第 n 步（0 基）打完场上没怪了 → 记下（幻象 reviving 的怪不算清场）。"""
        if self.cleared_at is not None:
            return
        if self.alive():
            return
        if any(e.get("revive") for e in self.enemies):
            return
        self.cleared_at = n

    @classmethod
    def from_state(cls, state: dict) -> "Sim":
        s = cls()
        c = state.get("combat") or {}
        p = c.get("player") or {}
        run = state.get("run") or {}
        s.hp = sts2._int(p.get("current_hp", run.get("current_hp")), 0)
        s.maxhp = sts2._int(p.get("max_hp", run.get("max_hp")), 1)
        s.block = sts2._int(p.get("block"), 0)
        s.energy = sts2._int(p.get("energy"), 0)
        s.e_max = sts2._int(p.get("max_energy", run.get("max_energy")), s.energy)
        s.hand = [parse_card(x, i) for i, x in enumerate(c.get("hand") or []) if isinstance(x, dict)]
        s.enemies = [enemy_state(x, i) for i, x in enumerate(c.get("enemies") or []) if isinstance(x, dict)]
        # 允许上游/测试直接注射机制字段（enemy_state 只从 powers 里解析）
        for e, src in zip(s.enemies, [x for x in (c.get("enemies") or []) if isinstance(x, dict)]):
            for k in ("hit_cap", "hp_loss_cap", "dig", "stun_at"):
                v = sts2._int(src.get(k), 0)
                if v and not e.get(k):
                    e[k] = v
        s.potions = [p for p in (parse_potion(x) for x in (run.get("potions") or [])) if p]
        pid = _power_ids(p.get("powers"))
        pm = sts2._power_map(p.get("powers"))
        s.player_vuln = sts2._int(pm.get("vuln"), 0)
        s.player_frail = sts2._int(pm.get("frail"), 0)
        s.player_weak = sts2._int(pm.get("weak"), 0)
        s.tender = sts2._int(pm.get("tender"), 0)
        s.shrink = sts2._int(pm.get("shrink"), 0)
        s.tangled = sts2._int(pm.get("tangled"), 0)
        s.imbalanced = sts2._int(pm.get("imbalanced"), 0)
        s.jugg = sts2._int(pid.get("JUGGERNAUT_POWER"), 0)
        s.metallicize = sts2._int(pm.get("metallicize"), 0)
        s.barricade = bool(pm.get("barricade"))
        s.plating = sts2._int(pm.get("plating"), 0)
        s.fnp = sts2._int(pid.get("FEEL_NO_PAIN_POWER"), 0)
        s.dark_embrace = sts2._int(pid.get("DARK_EMBRACE_POWER"), 0)
        s.rupture = sts2._int(pid.get("RUPTURE_POWER"), 0)
        s.skills_zero = "CORRUPTION_POWER" in pid
        s.free_atk = _free_attack_count(p.get("powers"))
        s.tick = _turn_start_hp_loss(p.get("powers"))
        rids = {str(r.get("relic_id") or r.get("id") or "").upper()
                for r in (run.get("relics") or []) if isinstance(r, dict)}
        if "PERMAFROST" in rids:
            s.permafrost = 7
        s.unmodeled = unmodeled_parts((state.get("combat") or {}).get("enemies"),
                                      p.get("powers"), rids)
        if s.free_atk:
            # buff 生效时引擎把所有攻击牌的 energy_cost 都报 0：还原成原费，
            # 只留 free_atk 这一个「还能免几张」的口径，避免按「全免费」算能量。
            for card in s.hand:
                if card.get("t") == "A" and not card.get("c") and card.get("c_base") \
                        and not card.get("eternal"):
                    card["c"] = card["c_base"]
                    card["disc"] = 1
        av = (state.get("agent_view") or {}).get("combat") or {}
        s.exhaust_pile = len([x for x in (av.get("exhaust_cards") or []) if isinstance(x, dict)])
        return s

    def clone(self) -> "Sim":
        n = Sim.__new__(Sim)
        n.__dict__ = {k: (list(v) if isinstance(v, list) else v) for k, v in self.__dict__.items()}
        n.enemies = [dict(e) for e in self.enemies]
        n.potions = [dict(p) for p in self.potions]
        n.hand = [dict(c) for c in self.hand]
        return n

    # ---------------------------------------------------------------- 查询
    def alive(self) -> list[dict]:
        return [e for e in self.enemies if not e["dead"] and e["hp"] > 0]

    def by_index(self, i: int) -> dict | None:
        for e in self.enemies:
            if e["i"] == i and not e["dead"] and e["hp"] > 0:
                return e
        return None

    def threat(self, e: dict) -> int:
        return sts2._int(e.get("atk"), 0)

    def cost_of(self, card: dict) -> int:
        if not card.get("play", True):
            return 999
        if card.get("x"):
            return self.energy
        c = max(0, sts2._int(card.get("c"), 0))
        if card.get("t") == "A" and (self.next_attack_zero or self.free_atk > 0):
            c = 0
        if card.get("t") == "S" and (self.next_skill_zero or self.skills_zero):
            c = 0
        cm = card.get("cm") or {}
        if cm.get("per"):
            c = max(0, c - self.attacks * cm["per"])
        if cm.get("self+"):
            c = max(0, c + cm["self+"])
        if cm.get("self-"):
            c = max(0, c - cm["self-"])
        # 午夜：本场战斗每消耗过一张牌，费用 -1（消耗牌堆 + 本回合已消耗）。
        if card.get("id") == "MIDNIGHT":
            c = max(0, c - (self.exhaust_pile + self.exhausts))
        return c

    def incoming(self) -> int:
        total = 0
        for e in self.alive():
            if e["stunned"]:
                continue
            # total_damage 已是最终值（含敌力量/虚弱/玩家易伤）；只有回落
            # 到 damage×hits 的那部分才补修正，避免玩家易伤被乘两次。
            total += sts2._int(e.get("atk_final"), 0)
            for per, hits in e.get("atk_raw") or []:
                d = sts2._int(per, 0)
                # 本回合新挂的虚弱不改这回合已锁定的意图伤害（run82 实测）
                if e.get("weak0", e["weak"]):
                    d = int(d * 0.75)
                if self.player_vuln:
                    d = int(d * 1.5)
                total += d * max(1, sts2._int(hits, 1))
        return total

    # ---------------------------------------------------------------- 结算
    def lose_hp(self, n: int) -> int:
        """失去生命（无视格挡）：放血/祭品/血墙/状态牌。"""
        n = max(0, int(n))
        self.hp -= n
        self.lost_hp_turn += n
        return n

    def hurt_player(self, n: int) -> int:
        n = max(0, int(n))
        if not n:
            return 0
        absorbed = min(self.block, n)
        self.block -= absorbed
        d = n - absorbed
        self.hp -= d
        self.lost_hp_turn += d
        return d

    def hurt_enemy(self, e: dict, per: int, hits: int = 1,
                   strength: bool = True, vuln: bool = True, attack: bool = True) -> int:
        if e["dead"] or e["hp"] <= 0:
            return 0
        per = max(0, int(per))
        if strength:
            per += self.str_bonus
        if vuln and e["vuln"]:
            per = int(per * 1.5)  # 力量先加，易伤后乘；顺序不能反
        # 振翅 FLUTTER：从攻击牌受到的伤害 -50%（能力牌/药水/势不可当不算攻击牌）
        if attack and e.get("flutter"):
            per = max(1, int(per * 0.5)) if per else 0
        if e["timid"]:
            e["blk"] += e["timid"]
            e["timid"] = 0
        had_blk = e["blk"] > 0
        dealt = 0
        for _ in range(max(1, hits)):
            if e["blk"] > 0:
                absorb = min(e["blk"], per)
                e["blk"] -= absorb
                d = per - absorb
            else:
                d = per
            if d <= 0:
                continue
            if e["intangible"]:
                d = 1
            elif e["slippery"] > 0:
                d = 1
                e["slippery"] -= 1
            cap = e.get("hit_cap") or 0
            if cap and d > cap:
                d = cap          # 「难以杀灭 N」：单次伤害封顶 N
            # 「硬化外壳 N」：整回合最多失去 N 点生命。超出预算的伤害不再扣血，
            # 所以 45 血 + 每回合上限 20 的怪，一刀 57 也死不了（run86 f15 t4 实测 45→25）。
            budget = e.get("hp_loss_cap") or 0
            if budget:
                left = budget - sts2._int(e.get("_lost_turn"), 0)
                if left <= 0:
                    break
                if d > left:
                    d = left
                e["_lost_turn"] = sts2._int(e.get("_lost_turn"), 0) + d
            e["hp"] -= d
            dealt += d
            if e["thorns"]:
                d = self.hurt_player(e["thorns"])
                self.thorns_taken += d
            if e["hp"] <= 0:
                e["hp"] = 0
                e["dead"] = True
                # 幻象 ILLUSION：死了下回合满血复活 → 这不是斩杀，别把它算进 kill
                if e.get("illusion") and not e.get("_revived_done"):
                    e["revive"] = 1
                    e["_revived_done"] = True
                self._on_death(e)
                break
        # 横冲直撞：第一次跌破阈值 → 这只怪本回合的攻击取消（不算「击晕」状态，只跳这一下）
        at = e.get("stun_at") or 0
        if at and not e.get("_stun_done") and e["hp"] <= at and e["hp"] > 0:
            e["stunned"] = True
            e["_stun_done"] = True
        # 地道虫「钻洞」：把它堆的格挡一次性剥光 → 当回合眩晕（实测，老版本漏算）
        if had_blk and e["blk"] <= 0 and e.get("dig") and not e.get("_dig_done") and e["hp"] > 0:
            e["stunned"] = True
            e["_dig_done"] = True
        return dealt

    def _on_death(self, dead: dict):
        # 吃尸眩晕：一只死，带饥饿的其它怪本回合被击晕并 +1 力量
        for o in self.enemies:
            if o is dead or o["dead"] or o["hp"] <= 0:
                continue
            if o["ravenous"]:
                o["stunned"] = True
                o["str"] += 1
        # 召唤者死，爪牙撤
        if any(not o["dead"] and not o["minion"] for o in self.enemies):
            return
        for o in self.enemies:
            if o["minion"]:
                o["dead"] = True
                o["hp"] = 0

    def apply_debuff(self, e: dict, key: str, n: int):
        if n <= 0 or e is None or e["dead"]:
            return
        if e["artifact"] > 0:
            e["artifact"] -= 1
            return
        e[key] += n

    def end_turn(self) -> dict:
        """只结算、不改状态（搜索会在同一节点反复调用）。"""
        inc = self.incoming()
        block = self.block + self.metallicize + self.plating
        # 可格挡的状态伤害与攻击共享格挡池；失去生命（呼唤/悔恨）无视格挡。
        tax_dmg, tax_hp = status_tax_parts(self.hand)
        end_taken = max(0, inc + tax_dmg - block)
        # 狱火：回合开始时自伤，发生在「敌方结算之后、下一次 ctx 快照之前」，
        # 所以它算进回合末血量（run85 的 +1 误差全都是这一项）。
        tick = max(0, sts2._int(self.tick, 0))
        taken = end_taken + tax_hp + self.thorns_taken + tick
        hp_after = self.hp - end_taken - tax_hp - tick
        return {"incoming": inc, "taken": taken, "hp_after": hp_after,
                "block": block, "tax": tax_dmg + tax_hp,
                "tax_dmg": tax_dmg, "tax_hp": tax_hp,
                "thorns": self.thorns_taken, "tick": tick}

    # ---------------------------------------------------------------- 出牌
    def play(self, card: dict, target: int | None = None) -> dict | None:
        if not card.get("play", True) or card.get("_played"):
            return None
        # 契约终结：消耗牌堆不足 3 张时根本不可打（不能当成 0 费空过）。
        if card.get("id") == "PACTS_END" and self.exhaust_pile + self.exhausts < 3:
            return None
        cost = self.cost_of(card)
        if cost > self.energy:
            return None
        cm = card.get("cm") or {}
        used_na = self.next_attack_zero and card.get("t") == "A" and not cm.get("na")
        used_ns = self.next_skill_zero and card.get("t") == "S" and not cm.get("ns")
        # 免费攻击只会花掉一层：打掉一张攻击牌就少一张免费（不是整手都免）
        if card.get("t") == "A" and not cm.get("na"):
            base_c = max(0, sts2._int(card.get("c"), 0))
            if base_c > cost:
                self.free_discount += base_c - cost
            if self.free_atk > 0 and not used_na:
                self.free_atk -= 1
        self.energy -= cost
        self.played += 1
        card["_played"] = True
        # 柔嫩 TENDER：本回合你每打出一张牌就失去 1 点力量和 1 点敏捷。
        # 牌面 Damage 已经含力量，所以等价于「手上剩下的牌各 -1 伤 / -1 甲」。
        # 实测：先打大锤再打小刀不吃亏；反过来就掉数值 —— 排牌序必须先贵后贱。
        if self.tender:
            for _x in self.hand:
                if _x is card or _x.get("_played"):
                    continue
                if _x.get("dmg"):
                    _x["dmg"] = max(0, int(_x["dmg"]) - self.tender)
                if _x.get("blk"):
                    _x["blk"] = max(0, int(_x["blk"]) - self.tender)
        if card.get("t") == "A":
            self.attacks += 1
        if card.get("t") == "S":
            self.skills += 1
        step = {"k": "c", "id": card["id"], "t": target, "e": cost, "d": 0, "blk": 0}
        blk0 = self.block

        # 自伤 / 回能 / 抽牌 / 加牌
        if card.get("hp"):
            d = self.lose_hp(card["hp"])
            self.selfhp += d
            step["hp"] = -d
            if self.rupture and d:
                self.str_bonus += self.rupture
        if card.get("en"):
            amount = card["en"]
            # 被遗忘的仪式：本回合没消耗过牌 → 不给能量（牌面 Energy 是条件值 3）。
            if card["id"] == "FORGOTTEN_RITUAL" and self.exhausts <= 0:
                amount = 0
            if amount:
                self.energy = min(self.e_max + 10, self.energy + amount)
                step["en"] = amount
        if card["id"] == "EXPECT_A_FIGHT":
            # 跃跃欲试：手里每张攻击牌回 1 费（引擎 CalculatedEnergy 缺失时本地数）。
            gain = card.get("en")
            if not gain:
                gain = len([x for x in self.hand if x is not card
                            and not x.get("_played") and x.get("t") == "A"])
            if gain:
                self.energy = min(self.e_max + 10, self.energy + gain)
                step["en"] = step.get("en", 0) + gain
        if card.get("draw"):
            self.draws += card["draw"]
            step["draw"] = card["draw"]
        if card.get("str"):
            self.str_bonus += card["str"]
        if card.get("dex"):
            self.dex_bonus += card["dex"]
        if card.get("blk"):
            if card.get("exh_hand") == "nonatk":
                n = len([x for x in self.hand if x is not card and not x.get("_played")
                         and x.get("t") != "A"])
                gain = max(0, card["blk"] * n)
            else:
                # card["blk"] 是引擎 dynamic_values 的当前值，**已经含快照时点的脆弱/力量**：
                # 全账本按 (牌, 升没升, 有没有脆弱) 分组，防御 5→3、耸肩 8→6、血墙 16→12、
                # 嘲讽 7→5、坚毅 7→5 —— 一律 0.75 向下取整，说明引擎侧已经砍过了（P15）。
                # 所以这里只补**批内新加**的敏捷，不能再乘一次 0.75（run86 f12t2 就是这么把
                # 4 点入伤预测成 6 点的）。
                gain = max(0, card["blk"] + self._dex_block())
            # 邪眼：本回合消耗过牌 → 再给一份（引擎 Block 只报基础值）。
            if card.get("id") == "EVIL_EYE" and self.exhausts > 0:
                gain += max(0, card.get("blk", 0))
            self.block += gain
            step["blk"] = gain
        # 狂怒：本回合每打一张攻击牌 +N 格挡（引擎实测生效，老版本漏算）
        if card.get("t") == "P" and self.permafrost and not self._pf_used:
            # 永冻冰晶：本场第一张能力牌给 7 甲（和狂怒/势不可当同属「额外获得格挡」）
            self._pf_used = True
            self.block += self.permafrost
            step["blk"] = step.get("blk", 0) + self.permafrost
        if card.get("rage"):
            self.rage += card["rage"]
        if self.rage and card.get("t") == "A":
            self.block += self.rage
            step["blk"] = step.get("blk", 0) + self.rage
        # 势不可当：本张牌每获得一次格挡，就对敌人补 N 点（随机目标 → 取当前目标/第一只活怪）
        if self.jugg and self.block > blk0:
            tg = self.by_index(target) if target is not None else None
            if tg is None:
                alive = self.alive()
                tg = alive[0] if alive else None
            if tg is not None:
                jd = self.hurt_enemy(tg, self.jugg, 1)
                step["d"] = step.get("d", 0) + jd
                step["jugg"] = jd
        if card.get("add"):
            step["add"] = card["add"]
        if card.get("gen"):
            # 添柴：手牌整把消耗并随机补牌 —— 生成结果本地不可知，hand 清空，
            # 后续只能等下一次 ctx 再排（validate 会拦住后面还预排的牌）。
            self.hand = []
            self.gen_unknown = 1
            step["gen"] = 1
        if card.get("pact") and self.exhaust_pile + self.exhausts >= 3 and not card.get("dmg"):
            card["dmg"] = 18

        # 伤害
        e = self.by_index(target) if target is not None else None
        if card.get("dmg") is not None:
            per = int(card["dmg"])
            if card["id"] == "BODY_SLAM":
                per = self.block
            hits = int(card.get("hits", 1))
            if card.get("spite") and self.lost_hp_turn:
                hits = max(hits, 2)
            if card.get("x"):
                hits = max(1, cost)
            if card["id"] == "FIEND_FIRE":
                hits = max(1, len([x for x in self.hand if x is not card and not x.get("_played")]))
            if card["id"] == "BULLY" and e:
                per += 2 * e["vuln"]
            if self.next_attack_twice and card.get("t") == "A":
                hits = max(1, hits) * 2
                self.next_attack_twice = False
            targets = self.alive() if card.get("aoe") else ([e] if e else [])
            dealt = 0
            for t in targets:
                if not t or t["dead"]:
                    continue
                h = hits
                if t["vuln"] and card.get("cond"):
                    h = max(h, int(card["cond"]))
                dealt += self.hurt_enemy(t, per, h)
            step["d"] = dealt
            if card["id"] == "REAPER":
                self.hp = min(self.maxhp, self.hp + dealt)
            if card["id"] == "FEED" and any(x["dead"] for x in targets):
                self.maxhp += 3
                self.maxhp_gain += 3

        # debuff
        if card.get("vuln"):
            if card.get("aoe"):
                for t in self.alive():
                    self.apply_debuff(t, "vuln", card["vuln"])
            elif e:
                self.apply_debuff(e, "vuln", card["vuln"])
                if card["id"] == "MOLTEN_FIST" and e:
                    e["vuln"] *= 2
                if card["id"] == "DOMINATE" and e:
                    self.str_bonus += e["vuln"]
        if card.get("weak"):
            if card.get("aoe"):
                for t in self.alive():
                    self.apply_debuff(t, "weak", card["weak"])
            elif e:
                self.apply_debuff(e, "weak", card["weak"])
        if card.get("frail") and e:
            self.apply_debuff(e, "frail", card["frail"])

        # 变费 / 能力
        if cm.get("na"):
            self.next_attack_zero = True
        if cm.get("ns"):
            self.next_skill_zero = True
        if cm.get("sk0"):
            self.skills_zero = True
        if used_na:
            self.next_attack_zero = False
        if used_ns:
            self.next_skill_zero = False
        if card["id"] == "ONE_TWO_PUNCH":
            self.next_attack_twice = True
        if card.get("pw"):
            self._power(card)
        if card.get("exh") or cm.get("self0") or (card.get("t") == "S" and self.skills_zero
                                                  and card["id"] != "CORRUPTION"):
            self.exhaust(card)
        if card.get("exh_hand"):
            for x in self.hand:
                if x is card or x.get("_played"):
                    continue
                if card["exh_hand"] == "nonatk" and x.get("t") == "A":
                    continue
                x["_played"] = True
                self.exhaust(x)
        self.steps.append(step)
        return step

    def exhaust(self, card: dict | None = None):
        self.exhausts += 1
        if self.fnp:
            self.block += self.fnp
        if self.dark_embrace:
            self.draws += 1

    def _power(self, card: dict):
        cid = card["id"]
        if cid == "FEEL_NO_PAIN":
            self.fnp += max(1, card.get("blk", 3) or 3)
        elif cid == "DARK_EMBRACE":
            pass
        elif cid == "CORRUPTION":
            self.skills_zero = True
        elif cid == "BARRICADE":
            self.barricade = True
        elif cid == "METALLICIZE":
            self.metallicize += card.get("blk", 3) or 3
        elif cid == "STONE_ARMOR":
            self.plating += card.get("plating", 4) or 4
        elif cid == "RUPTURE":
            self.rupture += max(1, card.get("str", 1) or 1)

    # ---------------------------------------------------------------- 药水
    def potion(self, p: dict, target: int | None = None) -> dict | None:
        if not p or not p.get("use") or p.get("_used"):
            return None
        p["_used"] = True
        step = {"k": "p", "p": p["p"], "id": p["id"], "t": target, "d": 0}
        if p.get("heal"):
            self.hp = min(self.maxhp, self.hp + p["heal"])
            step["heal"] = p["heal"]
        if p.get("en"):
            self.energy += p["en"]
        if p.get("draw"):
            self.draws += p["draw"]
        if p.get("str"):
            self.str_bonus += p["str"]
        if p.get("dex"):
            self.dex_bonus += p["dex"]
        if p.get("blk_mul"):
            # 固化药水：把「当前」格挡变 N 倍；之后再起的甲不再翻倍。
            before = self.block
            self.block = int(self.block * p["blk_mul"])
            step["blk"] = self.block - before
            step["blk_mul"] = p["blk_mul"]
        if p.get("blk"):
            self.block += p["blk"] + self.dex_bonus
            step["blk"] = p.get("blk")
        if p.get("dmg"):
            targets = self.alive() if p.get("aoe") else ([self.by_index(target)] if target is not None else [])
            dealt = 0
            for t in targets:
                if t:
                    dealt += self.hurt_enemy(t, p["dmg"], 1, strength=False, vuln=False)
            step["d"] = dealt
        e = self.by_index(target) if target is not None else None
        if p.get("vuln"):
            if p.get("aoe"):
                for t in self.alive():
                    self.apply_debuff(t, "vuln", p["vuln"])
            elif e:
                self.apply_debuff(e, "vuln", p["vuln"])
        if p.get("weak"):
            if p.get("aoe"):
                for t in self.alive():
                    self.apply_debuff(t, "weak", p["weak"])
            elif e:
                self.apply_debuff(e, "weak", p["weak"])
        self.steps.append(step)
        return step


# -------------------------------------------------------------------- 选线
def _policies(sim: Sim) -> list[tuple[str, int | None]]:
    out: list[tuple[str, int | None]] = [("threat", None), ("finish", None)]
    for e in sim.alive():
        out.append((f"focus:{e['i']}", e["i"]))
    return out


def _pick_target(sim: Sim, policy: str, focus: int | None, card: dict) -> int | None:
    alive = sim.alive()
    if not alive:
        return None
    if policy.startswith("focus") and focus is not None and sim.by_index(focus):
        return focus
    if policy == "finish":
        return min(alive, key=lambda e: (e["hp"], -e["atk"]))["i"]
    return max(alive, key=lambda e: (e["atk"], -e["hp"]))["i"]


def _score(sim: Sim, res: dict, energy0: int, hp0: int, incoming0: int, played: list[dict]) -> float:
    if res["hp_after"] <= 0:
        return -1_000_000 + sum(s.get("d", 0) for s in sim.steps)
    score = 0.0
    score += 3.0 * sum(s.get("d", 0) for s in sim.steps)
    score -= 7.0 * res["taken"]
    score -= 3.0 * sim.energy
    score -= 2.5 * sim.selfhp
    score += 2.0 * sim.draws
    score += 4.0 * sim.exhausts
    score += 6.0 * sum(1 for c in sim.hand if c.get("_played") and c.get("pw"))
    score -= 120.0 * sum(1 for p in sim.potions if p.get("_used"))
    for e in sim.enemies:
        if e["dead"]:
            score += 150 + 9 * e["atk"]
        elif e["stunned"]:
            score += 6 * e["atk"]
    if res["taken"] == 0 and incoming0 > 0:
        score += 45
    if res["hp_after"] == hp0 and res["taken"] == 0:
        score += 15
    # 终局多杀/清场
    if not sim.alive():
        score += 300
    return score


def _seq_json(sim: Sim) -> list:
    out = []
    for s in sim.steps:
        if s["k"] == "p":
            out.append([f"@{s['p']}", s["t"]] if s.get("t") is not None else [f"@{s['p']}"])
        else:
            out.append([s["id"], s["t"]] if s.get("t") is not None else [s["id"]])
    return out


def _plan(sim: Sim, res: dict, score: float, kind: str) -> dict:
    kills = [e["i"] for e in sim.enemies if e["dead"]]
    stun = [e["i"] for e in sim.enemies if e["stunned"] and not e["dead"]]
    return {"k": kind, "seq": _seq_json(sim), "dmg": sum(s.get("d", 0) for s in sim.steps),
            "kill": kills, "stun": stun, "taken": res["taken"],
            "hp": res["hp_after"], "e": sim.energy, "score": round(score, 1),
            "risk": bool(res["hp_after"] <= 0)}


def search(state: dict, max_cards: int = 6, max_plans: int = 5,
           time_limit: float = 1.0, max_nodes: int = 200_000) -> list[dict]:
    """穷举「费用内所有牌序 × 目标策略」，返回 best/maxdmg/kill/guard/potion。

    P1-2：手牌最多看 10 张、深度默认 6（ctx 传 8）；DFS 有墙钟和节点上限，
    超时只停止继续展开，已经找到的候选仍然返回。`maxdmg` 是纯伤害上限，
    可能是 hp<=0 的自杀线，带 `risk=True`，调用方必须单独提示。
    """
    base = Sim.from_state(state)
    if not base.alive():
        return []
    energy0, hp0, incoming0 = base.energy, base.hp, base.incoming()
    playable = [c for c in base.hand if c.get("play", True)]
    if len(playable) > 10:
        playable = playable[:10]
    depth = max(0, min(int(max_cards), 8))
    seen_keys: list[tuple] = []
    for c in playable:
        key = (c["id"], c.get("up", 0), c.get("c", 0))
        if key not in seen_keys:
            seen_keys.append(key)

    best: dict[str, tuple[float, Sim, dict]] = {}
    started = time.monotonic()
    deadline = started + time_limit if time_limit and time_limit > 0 else None
    nodes = 0
    timed_out = False

    def record(sim: Sim, res: dict, score: float, kind: str):
        cur = best.get(kind)
        if cur is None or score > cur[0]:
            best[kind] = (score, sim.clone(), res)

    def record_maxdmg(sim: Sim, res: dict, dmg: float):
        """伤害并列时优先活下来/少打牌，避免 maxdmg 把自杀线当默认上限。"""
        cur = best.get("maxdmg")
        if cur is None or dmg > cur[0] or (
                dmg == cur[0] and (res["hp_after"], -len(sim.steps)) >
                (cur[2]["hp_after"], -len(cur[1].steps))):
            best["maxdmg"] = (dmg, sim.clone(), res)

    def dfs(sim: Sim, used: set[int], depth: int, policy: str, focus: int | None,
            potion_line: bool = False):
        nonlocal nodes, timed_out
        if timed_out:
            return
        nodes += 1
        if nodes > max_nodes or (deadline is not None and time.monotonic() > deadline):
            timed_out = True
            return
        res = sim.end_turn()
        score = _score(sim, res, energy0, hp0, incoming0, [])
        dmg_total = sum(s.get("d", 0) for s in sim.steps)
        if potion_line:
            record(sim, res, score, "potion")
        else:
            record(sim, res, score, "best")
            # 纯伤害上限：不管防御，只看这回合最多能打多少（ctx.calc.dmg / ttk 用）
            record_maxdmg(sim, res, dmg_total)
            if res["taken"] == 0:
                record(sim, res, score, "guard")
            if any(e["dead"] for e in sim.enemies):
                record(sim, res, score, "kill")
        if depth <= 0:
            return
        for c in playable:
            if c["i"] in used:
                continue
            # 同 id 同费只按第一个下标展开，砍掉等价排列
            twin = next((x for x in playable if x is not c and x["i"] not in used
                         and (x["id"], x.get("up", 0), x.get("c", 0)) == (c["id"], c.get("up", 0), c.get("c", 0))), None)
            if twin is not None and twin["i"] < c["i"]:
                continue
            cost = sim.cost_of(c)
            if cost > sim.energy:
                continue
            nxt = sim.clone()
            target = _pick_target(nxt, policy, focus, c)
            if c.get("dmg") is not None and target is None and not c.get("aoe"):
                continue
            card = next((x for x in nxt.hand if x["i"] == c["i"]), None)
            if not card:
                continue
            nxt.play(card, target if target is not None else (nxt.alive()[0]["i"] if nxt.alive() else None))
            if nxt.played == sim.played:
                continue
            dfs(nxt, used | {c["i"]}, depth - 1, policy, focus, potion_line)

    for policy, focus in _policies(base):
        dfs(base.clone(), set(), depth, policy, focus)

    # 药水线：只试「药水先手」，让回能/斩杀药水能进入候选（用一次扣分，不轻易交）
    useful = [p for p in base.potions if p.get("use") and any(
        p.get(k) for k in ("dmg", "blk", "blk_mul", "en", "heal", "vuln", "weak",
                           "str", "dex"))]
    for p in useful[:2]:
        for policy, focus in _policies(base):
            if timed_out:
                break
            nxt = base.clone()
            pp = next((x for x in nxt.potions if x["p"] == p["p"]), None)
            if not pp:
                continue
            tgt = _pick_target(nxt, policy, focus, {}) if pp["t"] == "enemy" else None
            nxt.potion(pp, tgt)
            dfs(nxt, set(), depth, policy, focus, potion_line=True)

    out = []
    for kind in ("best", "maxdmg", "kill", "guard", "potion"):
        if kind in best:
            score, sim, res = best[kind]
            out.append(_plan(sim, res, score, kind))
    return out[:max_plans]


# -------------------------------------------------------------------- 速算
def _rage_block(s: "Sim") -> int:
    """狂怒还能补多少甲：只算「剩下能量真的打得出去」的攻击牌。

    旧实现把所有没打出的攻击牌都当成已经打出去，2 张 1 费攻击只剩 1 费时会
    虚报 +rage 的格挡，进而低估入伤（ctx.calc.blk / net 跟着错）。
    """
    if not s.rage:
        return 0
    budget = s.energy
    attacks = 0
    for c in sorted((x for x in s.hand
                     if x.get("t") == "A" and x.get("play", True) and not x.get("_played")),
                    key=lambda x: s.cost_of(x)):
        cost = s.cost_of(c)
        if cost > budget:
            continue
        budget -= cost
        attacks += 1
    return s.rage * attacks


def max_block(state: dict) -> int:
    """当前手牌在费用内最多能叠多少甲（含覆甲/金属化），用于 full_block 判断。"""
    s = Sim.from_state(state)
    cards = [c for c in s.hand if c.get("play", True) and (c.get("blk") or c.get("en"))]
    # 回能/0 费先打，再按每费格挡排序
    cards.sort(key=lambda c: (0 if c.get("en") else 1, -(c.get("blk", 0) / max(1, s.cost_of(c)))))
    for c in cards:
        if s.cost_of(c) <= s.energy:
            s.play(c, None)
    return s.block + s.metallicize + s.plating + _rage_block(s)


def lethal_targets(state: dict) -> list[dict]:
    """每个敌人：把易伤/力量/AOE/药水都算进去，够斩杀就列出来。

    P1-2 起不再只算单体攻击：AOE 牌与伤害药水会命中所有活怪，药水的
    力量/易伤/回能也会在伤害前使用。这里用少量确定性顺序做贪心核对
    （vuln 先手 / 纯伤害 / AOE 先手），不展开完整 DFS；返回的 `d`
    是从该怪「当前 HP+格挡」实际扣掉的值。
    """
    base = Sim.from_state(state)
    out = []
    time_limit = 0.35
    deadline = time.monotonic() + time_limit

    def _effective(e: dict) -> int:
        return 0 if e["dead"] or e["hp"] <= 0 else e["hp"] + e["blk"]

    def _target_damage(s: Sim, c: dict, e: dict) -> int:
        if c.get("id") == "BODY_SLAM":
            per = s.block
        else:
            per = sts2._int(c.get("dmg"), 0)
        if per <= 0:
            return 0
        per += s.str_bonus
        if e and e["vuln"]:
            per = int(per * 1.5)
        return per * max(1, sts2._int(c.get("hits"), 1))

    def _use_buffs(s: Sim, target: int, use_vuln: bool):
        """先喝力量/回能/易伤药，再打力量/回能/易伤牌（药水不占费用）。"""
        for p in s.potions:
            if p.get("_used") or not p.get("use"):
                continue
            useful = p.get("en") or p.get("str") or (use_vuln and p.get("vuln"))
            if useful:
                s.potion(p, target)
        cards = [c for c in s.hand if c.get("play", True) and not c.get("_played")
                 and (c.get("en") or c.get("str") or (use_vuln and c.get("vuln")))]
        cards.sort(key=lambda c: (s.cost_of(c), 0 if c.get("en") else 1, -_target_damage(s, c, s.by_index(target))))
        for c in cards:
            if s.cost_of(c) > s.energy:
                continue
            s.play(c, target)

    def _use_damage(s: Sim, target: int, aoe_first: bool):
        """药水伤害先结（不吃力量/易伤），再按对目标伤害/费用打牌。"""
        for p in s.potions:
            if p.get("_used") or not p.get("use") or not p.get("dmg"):
                continue
            s.potion(p, target)
            if s.by_index(target) is None:
                return
        cards = [c for c in s.hand if c.get("play", True) and not c.get("_played")
                 and (c.get("dmg") is not None or c.get("id") == "BODY_SLAM")]
        cards.sort(key=lambda c: (
            0 if (aoe_first and c.get("aoe")) else 1,
            -_target_damage(s, c, s.by_index(target)) / max(1, s.cost_of(c)),
            s.cost_of(c),
        ))
        for c in cards:
            if s.cost_of(c) > s.energy:
                continue
            s.play(c, target)
            if s.by_index(target) is None:
                return

    for target in base.alive():
        if time.monotonic() > deadline:
            break
        before = _effective(target)
        if before <= 0:
            continue
        best = 0
        killed = False
        # 三种确定性顺序：易伤先手、纯伤害、AOE 先手；任一能杀就算能杀。
        for mode in ("vuln", "direct", "aoe"):
            s = base.clone()
            e = s.by_index(target["i"])
            if not e:
                continue
            _use_buffs(s, target["i"], use_vuln=(mode != "direct"))
            _use_damage(s, target["i"], aoe_first=(mode == "aoe"))
            e2 = s.by_index(target["i"])
            after = _effective(e2) if e2 else 0
            dealt = max(0, before - after)
            best = max(best, dealt)
            if e2 is None or after <= 0:
                killed = True
                break
        if killed:
            out.append({"i": target["i"], "d": max(best, before), "hp": target["hp"]})
    return out


# -------------------------------------------------------------------- 校验
def resolve_card(sim: Sim, cid: str, used: set[int]) -> dict | None:
    key = str(cid or "").strip().upper()
    want_up = key.endswith("+")
    if want_up:
        key = key[:-1]
    pool = [c for c in sim.hand if c["i"] not in used
            and (not want_up or c.get("up"))]
    exact = [c for c in pool if c["id"].upper() == key]
    if not exact:
        pref = [c for c in pool if c["id"].upper().startswith(key)]
        if len({c["id"] for c in pref}) == 1:
            exact = pref
    if not exact:
        return None
    # 同名多张：先挑当下最便宜，再挑升级/伤害高的（变费后别打错那张）
    exact.sort(key=lambda c: (sim.cost_of(c), -(c.get("up") or 0), -(c.get("dmg") or 0)))
    return exact[0]


def validate(state: dict, seq: list) -> tuple[bool, str, Sim, list]:
    """逐步校验模型给的顺序：牌/药水是否存在、每一步当下费用够不够。

    end 只能出现一次且必须是最后一步；end 后还有动作（包括双 end）整手拒绝，
    不截断前半手假装成功。目标死了自动改打威胁最大的活怪（和游戏里的 batch
    一致），记在 sim.retargets。返回 (ok, err, sim, steps)。ok=False 时调用方
    一张都不许打。

    P1-3 抽牌边界：`draw` 只会增加 `sim.draws`，不会把未知牌放进 `sim.hand`。
    如果抽牌后又写一张不在抽牌前手里的牌，返回 `抽牌边界：...` 错误；调用方
    必须停在这张抽牌牌（不写 end），读新 ctx 再决定。
    """
    sim = Sim.from_state(state)
    sim.retargets = []
    used: set[int] = set()
    steps = []
    seq = list(seq or [])
    for n, raw in enumerate(seq):
        if isinstance(raw, str):
            raw = {"end": 1} if raw.lower() == "end" else {"c": raw}
        if not isinstance(raw, dict):
            return False, f"第{n + 1}步不是对象", sim, steps
        if raw.get("end") or raw.get("a") == "end":
            # end_turn 之后再写的动作会落到新回合，牌序/能量都已经变了。
            # 严格拒绝，而不是截断前半手后假装成功。
            if n != len(seq) - 1:
                return (False, f"第{n + 1}步 end 必须是最后一步（后面还有 "
                              f"{len(seq) - n - 1} 步）；拒绝执行整手", sim, steps)
            steps.append({"k": "end"})
            return True, "", sim, steps
        if "p" in raw or raw.get("a") == "potion" or "slot" in raw:
            slot = sts2._int(raw.get("p", raw.get("slot")), 0)
            p = next((x for x in (parse_potion(y) for y in (state.get("run") or {}).get("potions") or [])
                      if x and x["p"] == slot), None)
            if not p:
                return False, f"第{n + 1}步：药水槽 {slot} 是空的", sim, steps
            if not p["use"]:
                return False, f"第{n + 1}步：{p['n']} 现在不能用（{p['d']}）", sim, steps
            tgt = raw.get("t", raw.get("target"))
            tgt = sts2._int(tgt, -1) if tgt is not None else None
            if p["t"] == "enemy" and tgt is None:
                alive = sim.alive()
                if not alive:
                    return False, f"第{n + 1}步：{p['n']} 需要目标但没有活怪", sim, steps
                tgt = max(alive, key=lambda e: e["atk"])["i"]
            if tgt is not None and p["t"] == "enemy" and not sim.by_index(tgt):
                alive = sim.alive()
                if not alive:
                    return False, f"第{n + 1}步：目标 {tgt} 不是活怪", sim, steps
                tgt = max(alive, key=lambda e: e["atk"])["i"]
                sim.retargets.append({"at": n + 1, "to": tgt})
            p2 = sim.potion(p, tgt)
            if not p2:
                return False, f"第{n + 1}步：{p['n']} 用不了", sim, steps
            steps.append(p2)
            sim._mark_cleared(n)
            continue
        cid = raw.get("c", raw.get("id", raw.get("card_id")))
        card = resolve_card(sim, str(cid or ""), used)
        if not card:
            if sim.draws:
                return (False, (f"抽牌边界：第{n + 1}步 {cid} 不在抽牌前手牌里；"
                                f"前面已抽 {sim.draws} 张，抽到什么由游戏决定，不能预排。"
                                "先把抽牌牌单独打出去（不要写 end），读新 ctx 再出下一手"),
                        sim, steps)
            return False, f"第{n + 1}步：手里没有可打的 {cid}", sim, steps
        cost = sim.cost_of(card)
        if cost >= 999 or not card.get("play", True):
            # 999 是「本回合不可打」的哨兵（BOSS 眩晕时整手牌都不可打），
            # 旧文案说「要 999 费，只剩 2」会让模型以为补能量就能打。
            reason = str(card.get("why") or "眩晕/本回合不可用")[:20]
            return False, (f"第{n + 1}步：{card['id']} 本回合不能打（{reason}）；"
                           "整手都不能打就只写 end"), sim, steps
        if cost > sim.energy:
            return False, (f"第{n + 1}步：{card['id']} 现在要 {cost} 费，只剩 {sim.energy}"
                           "（回能/变费牌要先打）"), sim, steps
        tgt = raw.get("t", raw.get("target"))
        tgt = sts2._int(tgt, -1) if tgt is not None else None
        alive = sim.alive()
        if tgt is not None and not sim.by_index(tgt):
            if not alive:
                return False, f"第{n + 1}步：目标 {tgt} 不是活怪", sim, steps
            tgt = max(alive, key=lambda e: e["atk"])["i"]
            sim.retargets.append({"at": n + 1, "to": tgt})
        if card.get("dmg") is not None and not card.get("aoe") and tgt is None:
            if not alive:
                return False, f"第{n + 1}步：{card['id']} 需要目标但没有活怪", sim, steps
            tgt = max(alive, key=lambda e: e["atk"])["i"]
        step = sim.play(card, tgt)
        if not step:
            return False, f"第{n + 1}步：{card['id']} 打不出去", sim, steps
        used.add(card["i"])
        steps.append(step)
        sim._mark_cleared(n)
        if card.get("gen") and n != len(seq) - 1:
            # 生成边界：打完添柴手牌已经换成随机的，后面预排的牌不一定还在手里。
            return (False, (f"生成边界：第{n + 1}步 {card['id']} 会消耗所有手牌并随机生成新牌，"
                            "后面预排的牌不一定还在。只打这张（不要写 end），"
                            "读新 ctx 看完新手牌再出下一手"), sim, steps)
        if not sim.alive() and n != len(seq) - 1:
            # 清场后多写的动作会落到结算/新回合，前置拒绝比打到一半报错干净。
            return (False, (f"第{n + 1}步已经清场，后面还有 {len(seq) - n - 1} 步；"
                            "清场就停在清场那张牌"), sim, steps)
    return True, "", sim, steps
