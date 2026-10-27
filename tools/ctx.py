# -*- coding: utf-8 -*-
"""局面包 v3：一回合一份 JSON，模型只读这一份做决策。

设计目标（v20）
---------------
1. **该有的都有**：手牌实费与全部效果、抽/弃/消耗堆、遗物、药水、能量、
   双方 buff、每只怪的当前意图 + 下一次意图 + 机制、可上的 debuff、可变的费、
   可抽的牌、斩杀线、死亡连锁（杀一只→谁晕）。
2. **能算的全算完**：伤害/格挡/入伤/斩杀/连锁/两回合账都在本地；
   模型不心算，只做「打谁、先打哪张、要不要交药」的取舍。
3. **省 token**：短键、同名牌合并、死怪不写、空字段不写、牌组只给数量；
   典型战斗 1.2–2.2KB，比自然语言快照小一个量级。
4. **非战斗给建议**：奖励/商店/营火/选牌在 `choice.eval` 里给缺口、分数和门槛；
   只做排序提示，不替代路线/事件判断，也不执行动作。

用法
----
    python tools/ctx.py                 # 当前局面
    python tools/ctx.py --pretty        # 缩进，调试用
    python tools/ctx.py --out x.json    # 落盘
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import sts2  # noqa: E402
import sim  # noqa: E402
import mech as mechlib  # noqa: E402
import noncombat  # noqa: E402

DB = os.path.join(ROOT, "db", "sts2.db")
MEM_PATH = os.path.join(ROOT, "logs", "intent_memory.json")

_POWER = {}
_RELIC_NAME = {}
_RELIC_DESC = {}
_ENEMY_NOTE = {}
_LOADED = False

_INTENT_KIND = (("ATTACK", "A"), ("DEATH", "A"), ("DEFEND", "D"), ("BLOCK", "D"),
                ("BUFF", "B"), ("POWER", "B"), ("DEBUFF", "W"), ("STATUS", "S"))


def _load_db():
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        import sqlite3
        con = sqlite3.connect(DB)
        for pid, nz, dz in con.execute("select id, name_zh, desc_zh from powers"):
            _POWER[str(pid)] = (nz or pid, dz or "")
        for eid, notes in con.execute("select id, notes from enemies"):
            _ENEMY_NOTE[str(eid)] = notes or ""
        try:
            for rid, nz, dz in con.execute("select id, name_zh, desc_zh from relics"):
                _RELIC_NAME[str(rid)] = nz or ""
                _RELIC_DESC[str(rid)] = _clean(dz or "")
        except Exception:
            pass
        con.close()
    except Exception:
        pass


def _int(v, d=0):
    return sts2._int(v, d)


def _clean(s) -> str:
    s = sts2._strip_tags(str(s or ""))
    s = re.sub(r"res://\S+", "", s)
    return " ".join(s.split())


def _short(s, n=40) -> str:
    s = _clean(s)
    return s[:n]


# ---------------------------------------------------------------- 意图记忆
def _sig(e: dict) -> str:
    """意图压成短签名：A11 / A9x2 / D / B / W / S1。"""
    bits = []
    for it in e.get("intents") or []:
        if not isinstance(it, dict):
            continue
        typ = str(it.get("intent_type") or it.get("type") or "").upper()
        kind = next((k for key, k in _INTENT_KIND if key in typ), "?")
        if kind == "A":
            td = it.get("total_damage")
            d = _int(it.get("damage", it.get("value")), 0)
            h = _int(it.get("hits"), 1) or 1
            if td is not None:
                tot = _int(td, 0)
                bits.append(f"A{tot}" if h <= 1 or tot == d else f"A{d}x{h}")
            else:
                bits.append(f"A{d}x{h}" if h > 1 else f"A{d}")
        elif kind == "S":
            n = _int(it.get("status_card_count"), 0)
            bits.append(f"S{n}" if n else "S")
        else:
            bits.append(kind)
    return "+".join(bits) or "?"


def _record_intent(state: dict, i: int, e: dict, mem: dict) -> dict:
    """按「层 + 敌人 id + 下标」记历史，返回 {h: [...], nx: 'A11'}。"""
    c = state.get("combat") or {}
    run = state.get("run") or {}
    eid = str(e.get("enemy_id") or e.get("name") or "?")
    key = f"{run.get('run_id') or state.get('run_id') or '?'}|{_int(run.get('floor'), 0)}|{eid}|{i}"
    turn = _int(c.get("turn", state.get("turn", (state.get("agent_view") or {}).get("turn"))), 0)
    acts = set(state.get("available_actions") or [])
    rec = mem.get(key) or {"h": [], "t": -1}
    if turn == 1 and rec.get("t") != 1:
        rec = {"h": [], "t": -1}
    sig = _sig(e)
    if ("play_card" in acts or "end_turn" in acts) and rec.get("t") != turn:
        rec["h"] = (rec.get("h") or [])[-5:] + [sig]
        rec["t"] = turn
    mem[key] = rec
    h = rec.get("h") or []
    nx = ""
    # 1) 当前意图之前出现过 → 接当时的下一个
    for j in range(len(h) - 2, -1, -1):
        if h[j] == sig and j + 1 < len(h):
            nx = h[j + 1]
            break
    # 2) 周期检测：h[-p:] == h[-2p:] → 下一手就是 h[-p]
    if not nx:
        for p in (1, 2, 3):
            if len(h) >= 2 * p and h[-p:] == h[-2 * p:-p]:
                nx = h[-p]
                break
    out = {"h": h[-4:]} if len(h) > 1 else {}
    if nx:
        out["nx"] = nx
    return out


def _load_mem() -> dict:
    try:
        with open(MEM_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_mem(mem: dict):
    try:
        os.makedirs(os.path.dirname(MEM_PATH), exist_ok=True)
        with open(MEM_PATH, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, separators=(",", ":"))
    except OSError:
        pass


# ---------------------------------------------------------------- 战斗
def _intent(e: dict, str_amt: int = 0, weak: int = 0, player_vuln: int = 0) -> dict:
    its = [x for x in (e.get("intents") or []) if isinstance(x, dict)]
    if not its:
        return {"t": "?"}
    kinds, tot, d, h, sc = [], 0, 0, 0, 0
    for it in its:
        typ = str(it.get("intent_type") or it.get("type") or "").upper()
        kind = next((k for key, k in _INTENT_KIND if key in typ), "?")
        kinds.append(kind)
        td = it.get("total_damage")
        if td is not None:
            tot += _int(td, 0)
            hits = _int(it.get("hits"), 1) or 1
            per = _int(it.get("damage", it.get("value")), 0)
            if not per and hits:
                per = _int(td, 0) // hits
            d = max(d, per)
            h = max(h, hits)
        elif kind == "A":
            dd = _int(it.get("damage", it.get("value")), 0) + str_amt
            if weak:
                dd = int(dd * 0.75)
            if player_vuln:
                dd = int(dd * 1.5)
            hh = _int(it.get("hits"), 1) or 1
            d, h = max(d, dd), max(h, hh)
            tot += dd * hh
        sc += _int(it.get("status_card_count"), 0)
    out = {"t": "+".join(dict.fromkeys(kinds)) or "?"}
    if tot:
        out["d"], out["tot"] = d, tot
        if h > 1:
            out["h"] = h
    if sc:
        out["sc"] = sc
    return out


def _buffs(powers) -> list[str]:
    out = []
    for p in powers or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("power_id") or "?")
        name, _desc = _POWER.get(pid, (p.get("name") or pid, ""))
        amt = _int(p.get("amount"), 1)
        out.append(f"{name}{amt}" if amt not in (0, 1) else str(name))
    return out


def _str_amt(e: dict) -> int:
    return _int(sts2._power_map(e.get("powers")).get("str"), 0)


_BASE_POWER = {"STRENGTH_POWER", "DEXTERITY_POWER", "WEAK_POWER",
               "VULNERABLE_POWER", "FRAIL_POWER", "POISON_POWER"}


def _buff_info(powers) -> list[str]:
    """非基础 buff 的一句话机制：『荆棘3=多段反伤』。"""
    out = []
    for p in powers or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("power_id") or "").upper()
        if pid in _BASE_POWER:
            continue
        name, desc = _POWER.get(pid, (p.get("name") or pid, ""))
        amt = _int(p.get("amount"), 1)
        tag = mechlib.POWER_TAG.get(pid) or _short(desc, 22)
        if not tag:
            continue
        out.append(f"{name}{amt if amt not in (0, 1) else ''}={tag}")
    return out


def _mech(e: dict, eid: str, asc: int = 10) -> str:
    """机制：db 攻略/enemies.json 招式伤害 + 特殊 power 标签，≤130 字。"""
    base = mechlib.text(eid, asc, 70)
    mv = mechlib.moves_text(eid, asc, 54)
    parts = [x for x in (base, ("招：" + mv) if mv else "") if x]
    if not parts:
        pid = {str(p.get("power_id") or "").upper(): _int(p.get("amount"), 1)
               for p in (e.get("powers") or []) if isinstance(p, dict)}
        parts = [mechlib.POWER_TAG[k] for k in pid if k in mechlib.POWER_TAG]
    return "；".join(parts)[:130]



def _piles_cards(state: dict) -> tuple[list, list, list]:
    av = state.get("agent_view") or {}
    c = av.get("combat") or {}
    return ([x for x in c.get("draw_cards") or [] if isinstance(x, dict)],
            [x for x in c.get("discard_cards") or [] if isinstance(x, dict)],
            [x for x in c.get("exhaust_cards") or [] if isinstance(x, dict)])


def _card_stats(cid: str) -> tuple[float, int, int]:
    """(费用, 格挡, 伤害) —— 从 db 中文描述里抠，估下回合手牌用。"""
    info = mechlib.card_info(cid)
    tx = info.get("tx") or ""
    cost = info.get("c")
    cost = 1.0 if cost is None or cost < 0 else float(cost)
    b = re.search(r"获得(\d+)点格挡", tx)
    d = re.search(r"造成(\d+)点伤害", tx)
    hits = re.search(r"(\d+)次", tx)
    blk = int(b.group(1)) if b else 0
    dmg = int(d.group(1)) if d else 0
    if dmg and hits:
        dmg *= int(hits.group(1))
    return cost, blk, dmg


def _nx_draw(state: dict, need: int = 5, energy: int = 3) -> dict:
    """下回合手牌预测：只给期望值，不假装知道抽牌顺序。

    P1-3：抽牌堆 >=5 时按整堆组成估 5 张；<5 时先拿光抽牌堆，再从弃牌堆
    按平均质量补足。两种都标 `uncertain:1`，模型不能把它当成确定手牌。
    """
    draw, dis, _exh = _piles_cards(state)
    draw_ids = [str(x.get("card_id") or "?") for x in draw]
    dis_ids = [str(x.get("card_id") or "?") for x in dis]
    if not draw_ids and not dis_ids:
        return {}
    draw_stats = [_card_stats(cid) for cid in draw_ids]
    dis_stats = [_card_stats(cid) for cid in dis_ids]
    mixed = len(draw_stats) < need
    if not mixed:
        # 抽牌堆够 5：下一手是从整堆里抽 5 张，顺序未知，按组成算期望。
        total = need
        extra = 0
        mean_cost = sum(s[0] for s in draw_stats) / len(draw_stats)
        raw_blk = sum(s[1] for s in draw_stats) * need / len(draw_stats)
        raw_dmg = sum(s[2] for s in draw_stats) * need / len(draw_stats)
        why = "按抽牌堆组成估期望；抽牌顺序未知"
        short = 0
    else:
        # 抽牌堆不足 5：先全部抽光，再从弃牌堆随机补 extra 张。
        extra = min(need - len(draw_stats), len(dis_stats))
        total = len(draw_stats) + extra
        known_blk = sum(s[1] for s in draw_stats)
        known_dmg = sum(s[2] for s in draw_stats)
        if extra and dis_stats:
            dis_mean_blk = sum(s[1] for s in dis_stats) / len(dis_stats)
            dis_mean_dmg = sum(s[2] for s in dis_stats) / len(dis_stats)
            dis_mean_cost = sum(s[0] for s in dis_stats) / len(dis_stats)
        else:
            dis_mean_blk = dis_mean_dmg = dis_mean_cost = 0.0
        raw_blk = known_blk + extra * dis_mean_blk
        raw_dmg = known_dmg + extra * dis_mean_dmg
        cost_sum = sum(s[0] for s in draw_stats) + extra * dis_mean_cost
        mean_cost = cost_sum / total if total else 1.0
        if extra:
            why = "抽牌堆不足5，弃牌堆洗牌顺序未知；按比例估"
        else:
            why = "抽牌堆不足5且弃牌堆为空；只能抽到现有张数"
        short = 1 if total < need else 0
    if total <= 0:
        return {}
    mean_cost = mean_cost or 1.0
    # 3 费能打出的比例（平均每费产出）
    scale = min(1.0, energy / (mean_cost * 3)) if mean_cost else 1.0
    scale = max(0.45, scale)
    out = {"n": total, "blk": int(raw_blk * scale), "dmg": int(raw_dmg * scale),
           "pool": total, "mix": bool(extra), "uncertain": 1, "why": why}
    if short:
        out["short"] = 1
    return out


def _nx_tot(sig: str) -> int:
    """从意图签名（A16 / A9x2 / A10+D）抠出伤害总数。"""
    tot = 0
    for part in str(sig or "").split("+"):
        m = re.match(r"A(\d+)(?:x(\d+))?$", part.strip())
        if m:
            tot += int(m.group(1)) * int(m.group(2) or 1)
    return tot


def _piles(state: dict) -> dict:
    av = state.get("agent_view") or {}
    c = av.get("combat") or {}
    run = av.get("run") or {}
    piles = c if c else (run.get("piles") or {})
    if not piles:
        return {}
    out = {}
    for key, name in (("draw_cards", "draw"), ("discard_cards", "dis"), ("exhaust_cards", "exh")):
        cards = [x for x in (piles.get(key) or []) if isinstance(x, dict)]
        if not cards:
            if key == "exhaust_cards":
                out["exh"] = {"n": 0}
            continue
        agg: dict[str, int] = {}
        for x in cards:
            cid = str(x.get("card_id") or "?")
            if x.get("upgraded"):
                cid += "+"
            agg[cid] = agg.get(cid, 0) + 1
        e = {"n": len(cards), "c": [[k, v] for k, v in sorted(agg.items(), key=lambda kv: (-kv[1], kv[0]))]}
        out[name] = e
    if "draw" not in out:
        out["draw"] = {"n": 0}
    if "dis" not in out:
        out["dis"] = {"n": 0}
    out.setdefault("exh", {"n": 0})
    return out


def _hand_sig(c: dict) -> tuple:
    return (c["id"], c.get("up", 0), c.get("c"), c.get("t"), c.get("dmg"), c.get("hits"),
            c.get("blk"), c.get("draw"), c.get("en"), c.get("hp"), c.get("vuln"),
            c.get("weak"), c.get("aoe", False), c.get("x", False), c.get("exh", 0),
            c.get("add", 0), json.dumps(c.get("cm") or {}, sort_keys=True),
            json.dumps(c.get("nxt") or {}, sort_keys=True))


_TEXT_HINT = ("每", "随机", "消耗", "固有", "虚无", "保留", "无法", "抽", "失去",
              "所有敌人", "层", "下回合", "额外", "选择", "丢弃", "洗", "复制")


def _needs_text(desc: str) -> bool:
    return any(k in desc for k in _TEXT_HINT)


def _hand_out(hand: list[dict]) -> list[dict]:
    """同 id 同费同数值的牌合并成一条（带 q 张数），省 token 又不丢信息。"""
    groups: dict[tuple, dict] = {}
    order = []
    for c in hand:
        key = _hand_sig(c)
        if key in groups:
            groups[key]["q"] += 1
            continue
        e = {"id": c["id"], "n": c.get("n") or c["id"], "c": c.get("c", 0), "t": c.get("t", "?")}
        for k_src, k_dst in (("up", "up"), ("dmg", "dmg"), ("hits", "hits"), ("cond", "cond"),
                             ("blk", "blk"), ("draw", "draw"), ("en", "en"), ("hp", "hp"),
                             ("vuln", "vuln"), ("weak", "weak"), ("frail", "frail"),
                             ("str", "str"), ("dex", "dex"), ("aoe", "aoe"), ("x", "x"),
                             ("exh", "exh"), ("cm", "cm"), ("add", "add"), ("pw", "pw"),
                             ("nxt", "nxt")):
            if c.get(k_src):
                e[k_dst] = c[k_src]
        if not c.get("play", True):
            e["play"] = False
            if c.get("why"):
                e["why"] = c["why"]
        info = mechlib.card_info(c["id"])
        if info.get("kw"):
            e["kw"] = info["kw"]
        # 牌面文字优先用引擎的实时文本：DB 里的中文描述可能落后于版本
        # （run69：跃跃欲试 DB 写「获得15点格挡」，引擎实际是回能牌 → 白挨 16 点）。
        live = (c.get("ltx") or "").strip()
        dbx = (info.get("tx") or "").strip()
        if live and dbx and live != dbx:
            e["txdiff"] = 1
        tx = live or dbx
        if tx and _needs_text(tx) and not c.get("up"):
            e["tx"] = tx[:60]
        e["q"] = 1
        groups[key] = e
        order.append(key)
    out = []
    for k in order:
        e = groups[k]
        if e.get("q") == 1:
            e.pop("q", None)
        out.append(e)
    return out


def _calc(state: dict, st: sim.Sim, hand: list[dict], potions: list[dict]) -> tuple[dict, list]:
    inc = st.incoming()
    blk = sim.max_block(state)
    tax_dmg, tax_hp = sim.status_tax_parts(st.hand)
    tax = tax_dmg + tax_hp
    # 可格挡状态伤害和攻击共用格挡池；无视格挡的失血直接进净掉血。
    net = max(0, inc + tax_dmg - blk) + tax_hp
    full = net == 0
    lethal = sim.lethal_targets(state)
    out = {"n": len(st.alive()), "inc": inc, "blk": blk, "net": net, "full": full}
    if tax:
        out["tax"] = tax
        out["tax_dmg"] = tax_dmg
        out["tax_hp"] = tax_hp
    if lethal:
        out["lethal"] = [x["i"] for x in lethal]
        chain = []
        for l in lethal:
            for e in st.alive():
                if e["ravenous"] and e["i"] != l["i"]:
                    save = int(e["atk"] * 0.75) if e["weak"] else e["atk"]
                    chain.append({"kill": l["i"], "stun": e["i"], "save": save})
        if chain:
            out["chain"] = chain
    aoe = [c for c in st.hand if c.get("play", True) and c.get("aoe") and c.get("dmg")]
    if aoe:
        per = max(int(c["dmg"] * (c.get("hits") or 1)) for c in aoe)
        out["aoe"] = {"d": per, "sum": sum(e["hp"] + e["blk"] for e in st.alive())}
    return out, lethal


def _can(st: sim.Sim, hand: list[dict], potions: list[dict]) -> dict:
    out: dict = {}
    vuln = [c["id"] for c in st.hand if c.get("play", True) and c.get("vuln")]
    weak = [c["id"] for c in st.hand if c.get("play", True) and c.get("weak")]
    for p in potions:
        if p.get("vuln"):
            vuln.append(f"@{p['p']}")
        if p.get("weak"):
            weak.append(f"@{p['p']}")
    if vuln:
        out["vuln"] = vuln
    if weak:
        out["weak"] = weak
    draw = [{"id": c["id"], "n": c.get("draw", 0)} for c in st.hand if c.get("play", True) and c.get("draw")]
    en = [{"id": c["id"], "n": c.get("en", 0)} for c in st.hand if c.get("play", True) and c.get("en")]
    tf = []
    for p in potions:
        if p.get("draw"):
            draw.append({"id": f"@{p['p']}", "n": p["draw"]})
        if p.get("en"):
            en.append({"id": f"@{p['p']}", "n": p["en"]})
        if any(k in (p.get("d") or "") for k in ("变化", "升级", "复制", "加入你的手牌", "转化为")):
            tf.append(f"@{p['p']}")
    if tf:
        out["tf"] = tf
    if draw:
        out["draw"] = {"n": sum(x["n"] for x in draw), "by": draw}
    if en:
        out["en"] = {"n": sum(x["n"] for x in en), "by": en}
    cost = [{"id": c["id"], "cm": c["cm"]} for c in st.hand if c.get("play", True) and c.get("cm")]
    if cost:
        out["cost"] = cost
    exh = [c["id"] for c in st.hand if c.get("play", True) and c.get("exh")]
    if exh:
        out["exh"] = exh
    add = [{"id": c["id"], "n": c.get("add", 0)} for c in st.hand if c.get("play", True) and c.get("add")]
    if add:
        out["add"] = add
    aoe = [c["id"] for c in st.hand if c.get("play", True) and c.get("aoe")]
    if aoe:
        out["aoe"] = aoe
    # 这一回合能给下一轮上 buff 的牌/药水（力量、敏捷、易伤、虚弱、覆甲、保留…）
    buff = [{"id": c["id"], "nxt": c["nxt"]} for c in st.hand
            if c.get("play", True) and c.get("nxt")]
    for p in potions:
        if p.get("nxt"):
            buff.append({"id": f"@{p['p']}", "nxt": p["nxt"]})
    if buff:
        out["buff"] = buff
    return out


def _combat(state: dict, mem: dict) -> dict:
    c = state.get("combat") or {}
    p = c.get("player") or {}
    run = state.get("run") or {}
    hp = _int(p.get("current_hp", run.get("current_hp")), 0)
    maxhp = _int(p.get("max_hp", run.get("max_hp")), 1)
    st = sim.Sim.from_state(state)
    potions = [sim.parse_potion(x) for x in (run.get("potions") or [])]
    potions = [x for x in potions if x]
    out = {
        "v": 3,
        "turn": _int(c.get("turn", state.get("turn", (state.get("agent_view") or {}).get("turn"))), 0),
        "screen": "COMBAT",
        "run": {"f": _int(run.get("floor"), 0), "act": _int(run.get("act_id"), 0) + 1,
                "asc": _int(run.get("ascension"), 0), "gold": _int(run.get("gold"), 0),
                "deck": len(run.get("deck") or [])},
        "me": {"hp": hp, "max": maxhp, "blk": _int(p.get("block"), 0),
               "e": _int(p.get("energy"), 0), "em": _int(p.get("max_energy", run.get("max_energy")), 0),
               "stars": _int(p.get("stars"), 0)},
    }
    buffs = _buffs(p.get("powers"))
    if buffs:
        out["me"]["buffs"] = buffs

    relics = _relics_out(run)
    if relics:
        out["relics"] = relics

    if potions:
        out["potions"] = _potions_out(run)

    out["hand"] = _hand_out(st.hand)
    # 同名牌会合并成一条（q 张），只看条数会以为手牌很少：给一个真实张数。
    out["hand_n"] = len(st.hand)
    piles = _piles(state)
    if piles:
        out["piles"] = piles

    # 敌人
    asc = _int(run.get("ascension"), 0)
    pm_self = sts2._power_map(p.get("powers"))
    en_out = []
    for i, e in enumerate([x for x in (c.get("enemies") or []) if isinstance(x, dict)]):
        if not (e.get("is_alive", True) and _int(e.get("current_hp", e.get("hp")), 1) > 0):
            continue
        eid = str(e.get("enemy_id") or "?")
        x = {"i": _int(e.get("index"), i), "id": eid, "n": _clean(e.get("name") or eid),
             "hp": _int(e.get("current_hp"), 0), "max": _int(e.get("max_hp"), 0)}
        if _int(e.get("block"), 0):
            x["blk"] = _int(e.get("block"), 0)
        b = _buffs(e.get("powers"))
        if b:
            x["b"] = b
        bd = _buff_info(e.get("powers"))
        if bd:
            x["bd"] = bd
        x["it"] = _intent(e, _str_amt(e),
                          _int(sts2._power_map(e.get("powers")).get("weak"), 0),
                          _int(pm_self.get("vuln"), 0))
        rec = _record_intent(state, i, e, mem)
        if rec.get("h"):
            x["hist"] = rec["h"]
        if rec.get("nx"):
            x["nx"] = rec["nx"]
            nt = _nx_tot(rec["nx"])
            if nt:
                x["nx_tot"] = nt
        mech = _mech(e, eid, asc)
        if mech:
            x["mech"] = mech
        en_out.append(x)
    out["enemies"] = en_out

    calc, lethal = _calc(state, st, out["hand"], potions)
    out["calc"] = calc
    can = _can(st, out["hand"], potions)
    if can:
        out["can"] = can

    plans = sim.search(state, max_cards=8, max_plans=5, time_limit=0.8,
                       max_nodes=120_000)
    plan_out = []
    for pl in plans:
        p_out = {"k": pl["k"], "s": pl["seq"], "d": pl["dmg"], "take": pl["taken"],
                 "hp": pl["hp"], "e": pl["e"]}
        if pl.get("kill"):
            p_out["kill"] = pl["kill"]
        if pl.get("stun"):
            p_out["stun"] = pl["stun"]
        if pl.get("risk") or pl.get("hp", 1) <= 0:
            p_out["risk"] = 1
        plan_out.append(p_out)
    out["plans"] = plan_out

    # 单体局面：直接给出「这张牌打出去实际掉多少血」（已扣敌方格挡、含易伤），
    # 免得把 hand[].dmg（卡面）或 plans[].d（整套合计）当成单卡伤害。
    if len(en_out) == 1:
        e0 = en_out[0]
        eblk = e0.get("blk", 0) or 0
        vuln0 = any("易伤" in str(b) for b in (e0.get("b") or []))
        for c in out["hand"]:
            if c.get("t") != "A" or not c.get("dmg"):
                continue
            per = int(c["dmg"] * (1.5 if vuln0 else 1.0))
            hits = max(1, _int(c.get("hits"), 1))
            c["eff"] = max(0, per - eblk) * hits

    # 本回合最大伤害 / 还需几回合 / 下回合入伤 / 下回合手牌
    dmax = max([p["dmg"] for p in plans] + [0])
    calc["dmg"] = dmax
    alive = [(x["hp"] + x.get("blk", 0)) for x in en_out]
    if dmax > 0 and alive:
        calc["ttk"] = {str(x["i"]): -(-(x["hp"] + x.get("blk", 0)) // dmax)
                       for x in en_out}
        calc["ttk_all"] = -(-sum(alive) // dmax)
    nx = [x.get("nx_tot") for x in en_out]
    if any(v is not None for v in nx):
        known = sum(v or 0 for v in nx)
        calc["nx_inc"] = known
        if any(x.get("nx") is None for x in en_out):
            calc["nx_part"] = True
    nd = _nx_draw(state)
    if nd:
        calc["nxdraw"] = nd

    warns = []
    if hp and hp <= maxhp * 0.5:
        warns.append(f"hp<50%({hp}/{maxhp})")
    if not potions:
        warns.append("no_potion")
    if calc["net"] and not calc["full"]:
        warns.append(f"cannot_full_block(-{calc['net']})")
    if calc["net"] >= hp:
        warns.append("incoming>=hp：必须全挡/斩杀/喝药")
        warns.append("SL_INSURANCE：本回合可能致死——先 `python sts2.py do save_and_quit` 再 `continue_run` 回档到战斗开始，再打这一手（死透了就没 SL 可用）")
    if en_out and not calc["inc"] and not calc.get("tax"):
        warns.append("本回合无入伤：可少起防，全力输出")
    if calc.get("nx_inc") and calc["nx_inc"] >= hp:
        warns.append(f"下回合预计吃{calc['nx_inc']}≥血量：这回合必须解决或留药")
    risk = [p for p in plans if p.get("risk") or p.get("hp", 1) <= 0]
    if risk:
        kinds = ",".join(p["k"] for p in risk)
        warns.append(f"RISK {kinds} hp<=0：只作伤害上限，不要照打")
    if calc.get("tax"):
        warns.append(f"status_tax({calc['tax']})")
    if piles and piles.get("draw", {}).get("n", 99) <= 2:
        warns.append("draw_low")
    hand_n = len((state.get("combat") or {}).get("hand") or [])
    if hand_n >= 10:
        warns.append("HAND_FULL 手牌 10/10：抽牌/加牌/生成的牌会静默消失（run80 血债），先把牌打掉再抽")
    elif hand_n == 9:
        warns.append("hand_near_full(9/10)：再抽就溢出")
    if any("BINDING" in str(x.get("id") or "").upper() for x in potions):
        warns.append("缚魂药水实测无效：喝了敌方既没虚弱也没易伤（run81 两次验证），别把它算进伤害")
    mods = _mods(state, en_out, calc)
    if mods:
        out["mods"] = mods
    if any("失衡" in str(b) for e in en_out for b in (e.get("b") or [])) and calc.get("full"):
        warns.append("IMBA_STUN 场上有人带「失衡」：完全格挡它的攻击会给它上眩晕，"
                     "下回合白赚一轮——值得为全挡多花一张牌")
    if warns:
        out["warn"] = warns
    return out


_MODS = {
    "tender": "柔嫩：本回合你每打出1张牌就 -1 力量 -1 敏捷 → 先打贵的（大锤/高费），"
              "越往后剩下的牌伤害和格挡逐张 -1",
    "shrink": "缩小：你的攻击伤害 -30%（hand[].dmg 牌面已扣过，别再手动乘）",
    "tangled": "缠结：本回合攻击牌 +1 费（hand[].c 已经是实价，别再自己加）",
}


def _mods(state: dict, enemies: list, calc: dict) -> list[str]:
    """回合内的减伤/加费类 debuff 数值口径：run82 就是因为不知道柔嫩怎么算才踩坑。"""
    pm = sts2._power_map(((state.get("combat") or {}).get("player") or {}).get("powers"))
    out = []
    for key, txt in _MODS.items():
        if _int(pm.get(key), 0):
            out.append(txt)
    return out


# ---------------------------------------------------------------- 非战斗
def _choice(state: dict) -> dict:
    sc = state.get("screen")
    if sc == "MAP":
        m = state.get("map") or {}
        opts = []
        for i, n in enumerate([x for x in (m.get("nodes") or [])
                               if isinstance(x, dict) and x.get("is_available")]):
            opts.append({"i": i, "t": str(n.get("node_type") or "?"),
                         "r": _int(n.get("row")), "c": _int(n.get("col")),
                         "to": [str((x or {}).get("node_type") or "?")
                                for x in (n.get("children") or []) if isinstance(x, dict)]})
        return {"kind": "map", "opts": opts}
    if sc == "EVENT":
        ev = state.get("event") or {}
        opts = []
        for i, o in enumerate([x for x in (ev.get("options") or []) if isinstance(x, dict)]):
            opts.append({"i": i, "n": _short(o.get("title") or o.get("text") or "", 40),
                         "d": _short(o.get("description") or "", 120),
                         "locked": bool(o.get("is_locked")), "go": bool(o.get("is_proceed"))})
        return {"kind": "event", "id": str(ev.get("event_id") or ev.get("id") or ""),
                "title": _short(ev.get("title") or "", 40),
                "desc": _short(ev.get("description") or "", 180), "opts": opts}
    if sc == "REWARD":
        rw = state.get("reward") or {}
        items = []
        for i, r in enumerate([x for x in (rw.get("rewards") or []) if isinstance(x, dict)]):
            items.append({"i": i, "t": str(r.get("reward_type") or r.get("type") or "?"),
                          "n": _short(r.get("name") or "", 30),
                          "d": _short(r.get("description") or "", 80)})
        cards = []
        for i, card in enumerate([x for x in (rw.get("card_options") or []) if isinstance(x, dict)]):
            e = sim.parse_card(card, i)
            e["i"] = i
            cards.append({k: e[k] for k in ("i", "id", "n", "c", "t", "up", "dmg", "hits", "blk",
                                            "draw", "en", "vuln", "weak", "aoe", "x", "exh")
                          if k in e})
        return {"kind": "reward", "items": items, "cards": cards}
    if sc == "SHOP":
        sh = state.get("shop") or {}
        out = {"kind": "shop", "gold": _int((state.get("run") or {}).get("gold")), "items": []}
        for sec in ("cards", "relics", "potions"):
            for i, it in enumerate([x for x in (sh.get(sec) or []) if isinstance(x, dict)]):
                item = {"sec": sec, "i": i, "n": _short(it.get("name") or "", 30),
                        "price": _int(it.get("price")),
                        "ok": bool(it.get("available", True))}
                cid = str(it.get("card_id") or it.get("item_id") or it.get("relic_id")
                          or it.get("potion_id") or it.get("id") or "")
                if cid:
                    item["id"] = cid
                fx = _short(it.get("description") or it.get("resolved_rules_text") or "", 48)
                if fx:
                    item["fx"] = fx
                out["items"].append(item)
        rem = sh.get("card_removal") or {}
        if rem:
            out["removal"] = {"ok": bool(rem.get("available")), "price": _int(rem.get("price"))}
        return out
    if sc == "REST":
        opts = []
        for i, o in enumerate([x for x in ((state.get("rest") or {}).get("options") or [])
                               if isinstance(x, dict)]):
            opts.append({"i": i, "n": _short(o.get("title") or o.get("name") or "", 24),
                         "ok": bool(o.get("is_enabled", o.get("enabled", True)))})
        return {"kind": "rest", "opts": opts}
    if sc == "CARD_SELECTION":
        sel = state.get("selection") or {}
        cards = []
        for i, card in enumerate([x for x in (sel.get("cards") or []) if isinstance(x, dict)]):
            e = sim.parse_card(card, i)
            e["i"] = i
            if card.get("is_selected") or card.get("selected"):
                e["sel"] = True
            cards.append({k: e[k] for k in ("i", "id", "n", "c", "t", "up", "dmg", "hits", "blk",
                                            "draw", "en", "vuln", "weak", "aoe", "x", "exh", "sel")
                          if k in e})
        prompt = _short(sel.get("prompt") or sel.get("title") or "", 80)
        mn, mx = _int(sel.get("min_select"), 0), _int(sel.get("max_select"), 0)
        if not mn and not mx:
            # 牌组选牌界面 mod 有时不给 min/max：从「选择 1 张」兜底
            m = re.search(r"选择\s*(\d+)\s*张", prompt)
            if m:
                mn = mx = int(m.group(1))
        return {"kind": "select", "prompt": prompt,
                "min": mn, "max": mx,
                "cards": cards}
    if sc == "CHEST":
        ch = state.get("chest") or {}
        return {"kind": "chest", "open": bool(ch.get("is_open")),
                "relics": [_short(r.get("name") or "", 30) for r in (ch.get("relic_options") or [])
                           if isinstance(r, dict)]}
    if sc == "MODAL":
        m = state.get("modal") or {}
        return {"kind": "modal", "type": str(m.get("type_name") or ""),
                "confirm": bool(m.get("can_confirm")), "dismiss": bool(m.get("can_dismiss"))}
    if sc == "GAME_OVER":
        go = state.get("game_over") or {}
        return {"kind": "over", "win": bool(go.get("is_victory")), "floor": _int(go.get("floor"))}
    return {"kind": str(sc or "?").lower()}


def _deck(state: dict) -> list[dict]:
    deck = [x for x in ((state.get("run") or {}).get("deck") or []) if isinstance(x, dict)]
    agg: dict[tuple, dict] = {}
    order = []
    for card in deck:
        cid = str(card.get("card_id") or "?")
        up = 1 if card.get("upgraded") else 0
        key = (cid, up)
        if key in agg:
            agg[key]["q"] += 1
            continue
        e = {"id": cid, "n": _clean(card.get("name") or ""),
             "c": _int(card.get("energy_cost"), 0),
             "t": sim.card_type(card), "q": 1}
        if up:
            e["up"] = 1
        agg[key] = e
        order.append(key)
    return [agg[k] for k in order]


def _relics_out(run: dict) -> list[dict]:
    """遗物结构化：id/名字/效果/状态（stack、melted 等），模型不用再解析字符串。"""
    out = []
    for r in run.get("relics") or []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("relic_id") or r.get("id") or "?").strip()
        name = _clean(r.get("name") or _RELIC_NAME.get(rid) or rid)
        fx = _short(r.get("description") or _RELIC_DESC.get(rid) or "", 56)
        st = {}
        if r.get("is_melted"):
            st["melted"] = True
        stack = r.get("stack")
        if isinstance(stack, (int, float)) and stack:
            st["stack"] = int(stack)
        for k in ("charges", "counter", "value", "is_active", "is_disabled"):
            if r.get(k) is not None:
                st[k] = r[k]
        item = {"i": _int(r.get("index"), len(out)), "id": rid, "n": name}
        if fx:
            item["fx"] = fx
        item["st"] = st if st else "on"
        out.append(item)
    return out


def _potions_out(run: dict) -> list[dict]:
    """药水结构化：槽位/id/名字/效果/目标/可用/状态。"""
    raw = {}
    for i, p in enumerate(run.get("potions") or []):
        if isinstance(p, dict):
            raw[_int(p.get("index"), i)] = p
    out = []
    for x in (sim.parse_potion(p) for p in (run.get("potions") or [])):
        if not x:
            continue
        r = raw.get(x["p"], {})
        if r.get("is_queued"):
            st = "queued"
        elif x["use"]:
            st = "usable"
        else:
            st = "blocked"
        out.append({"i": x["p"], "id": x["id"], "n": x["n"], "d": _clean(x.get("d", ""))[:70],
                    "t": x["t"], "use": bool(x["use"]), "st": st})
        if not x["use"]:
            out[-1]["why"] = "战斗外可用" if st == "blocked" else "暂不可用"
        if x.get("nxt"):
            out[-1]["nxt"] = x["nxt"]
    return out


def build(state: dict) -> dict:
    _load_db()
    if state.get("screen") == "COMBAT":
        return _combat(state, _load_mem())
    run = state.get("run") or {}
    out = {
        "v": 3,
        "screen": state.get("screen"),
        "run": {"f": _int(run.get("floor"), 0), "act": _int(run.get("act_id"), 0) + 1,
                "asc": _int(run.get("ascension"), 0), "gold": _int(run.get("gold"), 0),
                "hp": _int(run.get("current_hp"), 0), "max": _int(run.get("max_hp"), 0),
                "deck": len(run.get("deck") or [])},
    }
    relics = _relics_out(run)
    if relics:
        out["relics"] = relics
    potions = _potions_out(run)
    if potions:
        out["potions"] = potions
    deck = _deck(state)
    if deck:
        out["deck"] = deck
    choice = _choice(state)
    try:
        ev = noncombat.evaluate(state, choice)
    except Exception:
        # 评估器是附加建议层；坏牌面/未知 shop 字段不能阻断原始 ctx。
        ev = None
    if ev:
        choice["eval"] = ev
    out["choice"] = choice
    return out


def main():
    ap = argparse.ArgumentParser(description="局面包 v3（JSON）")
    ap.add_argument("--out")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    st = sts2.get_state()
    # end_turn 之后的过渡快照（新回合还没抽牌）不能当手牌用：等抽完再读。
    if st.get("screen") == "COMBAT":
        _c = st.get("combat") or {}
        _hand = [x for x in (_c.get("hand") or []) if isinstance(x, dict)]
        try:
            _draw, _dis, _exh = _piles_cards(st)
        except Exception:  # noqa: BLE001
            _draw = _dis = 0
        if len(_hand) <= 1 and (_draw or _dis):
            try:
                st = sts2.wait_new_turn_hand(len(_hand), timeout=6.0)
            except Exception:  # noqa: BLE001
                pass
    mem = _load_mem()
    d = build_with_mem(st, mem)
    s = json.dumps(d, ensure_ascii=False, indent=1 if args.pretty else None,
                   separators=None if args.pretty else (",", ":"))
    if args.out:
        with io.open(args.out, "w", encoding="utf-8") as f:
            f.write(s)
        print(f"WROTE {args.out} {len(s)} bytes")
    else:
        print(s)


def build_with_mem(state: dict, mem: dict) -> dict:
    """传 mem 进来，避免每回合重复读文件（act 内部用）。"""
    _load_db()
    if state.get("screen") == "COMBAT":
        out = _combat(state, mem)
        _save_mem(mem)
        return out
    return build(state)


if __name__ == "__main__":
    main()
