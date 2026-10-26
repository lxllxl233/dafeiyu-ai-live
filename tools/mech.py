# -*- coding: utf-8 -*-
"""怪物 / 卡牌机制档案（工具层，ctx.py 只读它的结果）。

设计：
1. **一次性建库**：从 db/sts2.db（中文名、招式、powers、notes）+ tools/data/eng/monsters.json
   （招式伤害，含 ascension 值）合并，落盘 db/mech.json，之后只读缓存。
2. **按需注入**：ctx.py 只取「当前场上那几只怪」的档案，不整表塞进上下文。
3. **机制优先**：notes（手写中文攻略）> powers 关键词 > 招式意图，拼成一行 ≤90 字。

用法：
    python tools/mech.py --rebuild          # 重建缓存
    python tools/mech.py --id NIBBIT        # 查一只怪
    python tools/mech.py --field            # 查当前战斗里的怪（读 /state）
    python tools/mech.py --card STOMP       # 查一张牌的机制/词条
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "sts2.db")
MON = os.path.join(ROOT, "tools", "data", "eng", "monsters.json")
CACHE = os.path.join(ROOT, "db", "mech.json")

# 意图 key → 短码
INTENT_CODE = {
    "ATTACK": "A", "DEFEND": "D", "BLOCK": "D", "BUFF": "B", "POWER": "B",
    "DEBUFF": "W", "STATUS": "S", "CARD_DEBUFF": "W", "SUMMON": "召",
    "SPLIT": "裂", "DEATH_BLOW": "爆", "ESCAPE": "逃", "HEAL": "疗",
    "SLEEP": "睡", "STUN": "晕", "UNKNOWN": "?",
}

# 初始 power → 机制标签
POWER_TAG = {
    "RAVENOUS_POWER": "吃同伴",
    "MINION_POWER": "召唤物（主人死则撤）",
    "RITUAL_POWER": "仪式：每回合涨力",
    "ARTIFACT_POWER": "人工制品：先破免伤",
    "THORNS_POWER": "荆棘：多段反伤",
    "SLIPPERY_POWER": "滑溜：前几刀只掉1",
    "INTANGIBLE_POWER": "无实体：伤害压到1",
    "PLATING_POWER": "覆甲：回合末加甲",
    "STOCK_POWER": "死亡召唤",
    "INFESTED_POWER": "死亡召唤",
    "STEAM_ERUPTION_POWER": "死后下回合爆炸",
    "ANGER_POWER": "越打越怒（涨力）",
    "CURIOSITY_POWER": "好奇：你出牌它涨力",
    "UNRAVELING_POWER": "解离：每回合掉甲",
    "SHIFTING_POWER": "变形：每回合换意图",
    "REGEN_POWER": "每回合回血",
    "METALLICIZE_POWER": "开局带甲",
    "BARRICADE_POWER": "甲不清空",
    "SIDE_DECK_POWER": "备用牌堆",
    "FOG_POWER": "迷雾：塞牌",
}

_KW = {
    "消耗": "EXH", "虚无": "ETH", "固有": "INN", "保留": "RET",
    "无法打出": "UNP", "永恒": "ETR", "敏捷": "SLY",
}

_cache: dict | None = None


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", str(s or "").lower())


def _conn():
    return sqlite3.connect(DB)


def build() -> dict:
    """从 db + monsters.json 生成机制缓存。"""
    con = _conn()
    mon = {}
    try:
        with open(MON, encoding="utf-8") as f:
            for m in json.load(f):
                mon[str(m.get("id")).upper()] = m
    except OSError:
        pass

    powers = {}
    for pid, nz, dz in con.execute("select id, name_zh, desc_zh from powers"):
        powers[str(pid).upper()] = (nz or pid, (dz or "").strip())

    card_kw = {}
    for cid, desc, raw, cost in con.execute("select id, desc_zh, raw, cost from cards"):
        kws = []
        for zh, code in _KW.items():
            if zh in (desc or ""):
                kws.append(code)
        try:
            rk = (json.loads(raw or "{}") or {}).get("keywords") or []
            for k in rk:
                code = {"Exhaust": "EXH", "Ethereal": "ETH", "Innate": "INN",
                        "Retain": "RET", "Unplayable": "UNP", "Eternal": "ETR",
                        "Sly": "SLY"}.get(str(k))
                if code and code not in kws:
                    kws.append(code)
        except Exception:
            pass
        card_kw[cid] = {"kw": kws, "tx": (desc or "").strip(),
                        "c": None if cost is None else float(cost)}

    out = {"enemies": {}, "cards": card_kw}
    for row in con.execute("select id,name_zh,type,hp_min,hp_max,powers_json,moves_json,notes from enemies"):
        eid = str(row[0])
        moves = []
        try:
            moves = json.loads(row[6] or "[]")
        except Exception:
            moves = []
        pk = []
        try:
            pk = [str(p.get("key") or "").upper() for p in json.loads(row[5] or "[]")]
        except Exception:
            pk = []
        m = mon.get(eid.upper()) or {}
        dmg = {}
        for k, v in (m.get("damage_values") or {}).items():
            if isinstance(v, dict):
                dmg[_norm(k)] = [v.get("normal"), v.get("ascension")]
            else:
                dmg[_norm(k)] = [v, v]
        mv, used = [], set()
        for x in moves:
            keys = [str(i.get("key") or "").upper() for i in (x.get("intents") or [])]
            code = "+".join(dict.fromkeys(INTENT_CODE.get(k, "?") for k in keys)) or "?"
            hit = None
            for cand in (x.get("nameEn"), x.get("id"), x.get("nameZh")):
                k = _norm(cand or "")
                if k and k in dmg:
                    hit = dmg[k]
                    used.add(k)
                    break
            item = {"n": x.get("nameZh") or x.get("nameEn") or "?", "t": code}
            if hit:
                item["d"] = hit
            mv.append(item)
        rest = [v for k, v in dmg.items() if k not in used]
        tags = []
        for k in pk:
            if k in POWER_TAG:
                tags.append(POWER_TAG[k])
        if any(x["t"] == "召" for x in mv):
            tags.insert(0, "召唤")
        note = (row[7] or "").strip()
        out["enemies"][eid] = {
            "n": row[1] or eid, "ty": row[2] or "", "hp": [row[3], row[4]],
            "mv": mv, "rest": rest, "tags": list(dict.fromkeys(tags)), "note": note,
            "pw": [(p, powers.get(p, ("", ""))[0]) for p in pk],
        }
    return out


def cache(rebuild: bool = False) -> dict:
    global _cache
    if _cache is not None and not rebuild:
        return _cache
    data = None
    if not rebuild:
        try:
            with open(CACHE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
    if not data:
        data = build()
        try:
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            with open(CACHE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        except OSError:
            pass
    _cache = data
    return data


def _val(pair, asc: int):
    """damage_values 的 [normal, ascension] → 当前进阶下的值。"""
    if not pair:
        return None
    n, a = pair[0], pair[1]
    if asc >= 2 and a is not None:
        return a
    return n if n is not None else a


def moves_text(eid: str, asc: int = 10, limit: int = 70) -> str:
    """招式一览：『顶撞13A+D｜哈气B』。"""
    d = cache()["enemies"].get(str(eid).upper())
    if not d:
        return ""
    bits = []
    for m in d.get("mv") or []:
        v = _val(m.get("d"), asc)
        bits.append(f"{m['n']}{v if v is not None else ''}{m['t']}")
    if d.get("rest"):
        vals = sorted({_val(x, asc) for x in d["rest"] if _val(x, asc) is not None})
        if vals:
            bits.append("未标注招式伤害 " + "/".join(str(v) for v in vals))
    s = "｜".join(bits)
    return s[:limit]


def text(eid: str, asc: int = 10, limit: int = 90) -> str:
    """机制摘要：notes 优先 + 标签 + 无机制时的招式伤害范围。"""
    d = cache()["enemies"].get(str(eid).upper())
    if not d:
        return ""
    parts = []
    note = (d.get("note") or "").strip()
    if note:
        parts.append(note)
    tags = [t for t in (d.get("tags") or []) if t and t not in note]
    if tags:
        parts.append("、".join(tags))
    s = "；".join(parts)
    if not s:
        # 没手写攻略的怪：用招式表当机制（名字+伤害+意图），比只给数字有用
        bits = []
        for m in d.get("mv") or []:
            v = _val(m.get("d"), asc)
            bits.append(f"{m['n']}{v if v is not None else ''}{m['t']}")
        rest = [_val(x, asc) for x in d.get("rest") or []]
        rest = [x for x in rest if x is not None]
        if rest:
            bits.append("/".join(str(x) for x in sorted(set(rest))) + "(未标注)")
        s = "｜".join(bits)
    return s[:limit]


def card_info(cid: str) -> dict:
    """{kw:[...], tx:'描述'}。"""
    c = cache()["cards"].get(str(cid))
    if not c:
        return {"kw": [], "tx": ""}
    return c


def field_ids(state: dict) -> list[str]:
    out = []
    for e in ((state.get("combat") or {}).get("enemies") or []):
        if isinstance(e, dict) and e.get("is_alive", True):
            out.append(str(e.get("enemy_id") or "?"))
    return out


def main():
    ap = sys.argv[1:]
    if "--rebuild" in ap:
        c = cache(rebuild=True)
        print(f"rebuilt enemies={len(c['enemies'])} cards={len(c['cards'])}")
        return
    if "--id" in ap:
        eid = ap[ap.index("--id") + 1]
        d = cache()["enemies"].get(eid.upper())
        print(json.dumps(d, ensure_ascii=False, indent=1))
        print("TEXT:", text(eid))
        print("MOVES:", moves_text(eid))
        return
    if "--card" in ap:
        cid = ap[ap.index("--card") + 1]
        print(json.dumps(card_info(cid), ensure_ascii=False, indent=1))
        return
    if "--field" in ap:
        sys.path.insert(0, ROOT)
        import sts2

        st = sts2.get_state()
        for eid in field_ids(st):
            print(eid, "|", text(eid), "|", moves_text(eid))
        return
    print(__doc__)


if __name__ == "__main__":
    main()
