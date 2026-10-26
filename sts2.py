#!/usr/bin/env python3
"""StS2 bridge client: compact state, actions, commentary, logging.

The heavy lifting is in the game mod's local HTTP API (STS2-Agent v0.8.0).
This client is deliberately thin: it formats state for a human/agent, posts
one action at a time, keeps a JSONL transcript, and pushes quips to the
transparent OBS overlay.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
API = os.environ.get("STS2_API", "http://127.0.0.1:8080")
OVERLAY = os.environ.get("STS2_OVERLAY", "http://127.0.0.1:8730")
LOGS = os.path.join(ROOT, "logs")
SESSION_PTR = os.path.join(LOGS, "current_session.json")
PLAN_PATH = os.path.join(LOGS, "route_plan.json")
DATA_DIR = os.path.join(ROOT, "tools", "data", "eng")
os.makedirs(LOGS, exist_ok=True)


def _load_db(name: str) -> dict:
    p = os.path.join(DATA_DIR, name)
    try:
        with open(p, encoding="utf-8") as f:
            items = json.load(f)
        return {str(x.get("id")): x for x in items if isinstance(x, dict) and x.get("id")}
    except Exception:
        return {}


SQLITE_DB = os.path.join(ROOT, "db", "sts2.db")


def _load_sqlite(table: str, name_col: str = "name_zh", desc_col: str = "desc_zh", extra=()) -> dict:
    """db/sts2.db 是从站点重抓的中文数据，通常比 tools/data/eng/*.json 新。

    返回 {id: {"name": 中文名, "description": 中文效果, ...extra 列}}。
    """
    if not os.path.exists(SQLITE_DB):
        return {}
    try:
        con = sqlite3.connect(SQLITE_DB)
        cols = [name_col, desc_col] + list(extra)
        cur = con.execute(f"SELECT id, {', '.join(cols)} FROM {table}")
        out = {}
        for row in cur.fetchall():
            rid = str(row[0])
            item = {"name": row[1] or "", "description": row[2] or ""}
            for i, col in enumerate(extra, start=3):
                item[col] = row[i]
            out[rid] = item
        con.close()
        return out
    except Exception:
        return {}


def _merge_db(base: dict, newer: dict) -> dict:
    """中文库覆盖同名字段（name/description 等），其余字段保留英文库的（block/damage/hit_count 等）。"""
    out = dict(base)
    for rid, item in (newer or {}).items():
        merged = dict(out.get(rid) or {})
        for k, v in item.items():
            if v not in (None, "", []):
                merged[k] = v
        out[rid] = merged
    return out


CARD_DB = _merge_db(
    _load_db("cards.json"),
    _load_sqlite("cards", extra=("cost", "target", "type", "rarity")),
)
RELIC_DB = _merge_db(_load_db("relics.json"), _load_sqlite("relics"))
POTION_DB = _merge_db(_load_db("potions.json"), _load_sqlite("potions"))
MONSTER_DB = _load_db("monsters.json")


# ---------------------------------------------------------------- plumbing
def get_state() -> dict:
    r = requests.get(API + "/state", timeout=15)
    r.raise_for_status()
    d = r.json()
    return d.get("data", d)


def get_actions() -> dict:
    r = requests.get(API + "/actions/available", timeout=15)
    r.raise_for_status()
    return r.json().get("data", {})


def _record_route_step():
    """Append the node we just entered to logs/route_plan.json (actual path)."""
    try:
        st = get_state()
    except Exception:
        return
    run = st.get("run") or {}
    floor = run.get("floor")
    nodes = (st.get("map") or {}).get("nodes") or []
    cur = next((n for n in nodes if n.get("is_current")), None)
    if cur is None or floor is None:
        return
    try:
        with open(PLAN_PATH, encoding="utf-8") as f:
            plan = json.load(f)
    except (OSError, ValueError):
        plan = {}
    actual = plan.get("actual") or []
    step = {"floor": floor, "row": cur.get("row"), "col": cur.get("col"), "type": cur.get("node_type")}
    if actual and actual[-1].get("floor") == floor:
        actual[-1] = step
    else:
        actual.append(step)
    plan["actual"] = actual
    seq = (plan.get("seq") or "").split()
    idx = len(actual) - 1
    sym = _route_sym().get(str(step["type"]), "?")
    if 0 <= idx < len(seq) and seq[idx] != sym:
        print(f"ROUTE_DEVIATED 第{idx + 1}步实际={sym}，原计划={seq[idx]}：建议 route → route --commit 重记")
    try:
        with open(PLAN_PATH, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _alive_enemies(st: dict) -> list:
    return [x for x in ((st.get("combat") or {}).get("enemies") or []) if _int(x.get("hp"), 0) > 0]


def _auto_target_index(st: dict):
    """Default target: lowest-HP alive enemy (fallback: first)."""
    es = _alive_enemies(st)
    if not es:
        return None
    best = min(range(len(es)), key=lambda i: _int(es[i].get("hp"), 9999))
    return best


def act(action: str, **params) -> dict:
    body = {"action": action}
    for k, v in params.items():
        if v is not None:
            body[k] = v
    r = requests.post(API + "/action", json=body, timeout=40)
    try:
        d = r.json()
    except Exception:
        d = {"ok": False, "error": {"message": r.text[:500]}}
    if not d.get("ok"):
        err = d.get("error", {})
        raise RuntimeError(f"{action} failed: {err.get('code')} {err.get('message')}")
    if action == "choose_map_node":
        _record_route_step()
    return d.get("data", {})


def overlay_say(tag: str, thought: str, meme: str, state: dict | None = None):
    thought = _clean_speech(thought)
    meme = _clean_speech(meme)
    if not (thought or meme):
        return
    payload = {"tag": tag, "thought": thought, "meme": meme}
    if state:
        run = state.get("run") or {}
        payload.update(
            {
                "character": "铁甲战士",
                "ascension": run.get("ascension", 10),
                "floor": run.get("floor", 0),
                "hp": run.get("hp", run.get("current_hp")),
                "max_hp": run.get("max_hp"),
                "gold": run.get("gold"),
                "screen": state.get("screen", ""),
            }
        )
    try:
        requests.post(OVERLAY + "/comment", json=payload, timeout=1.5)
    except Exception:
        pass


def tag_for(screen: str) -> str:
    return {
        "COMBAT": "战斗",
        "MAP": "路线",
        "REWARD": "选牌",
        "SHOP": "商店",
        "EVENT": "事件",
        "REST": "营火",
        "CHEST": "宝箱",
        "CARD_SELECTION": "选牌",
        "MODAL": "弹窗",
        "GAME_OVER": "结算",
        "CHARACTER_SELECT": "开局",
    }.get(screen, "思路")


# ---------------------------------------------------------------- formatting
def _first(d: dict, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def _int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def _short(v, n=60):
    s = str(v) if v is not None else ""
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _powers(powers) -> str:
    if not powers:
        return ""
    out = []
    for p in powers:
        if isinstance(p, dict):
            name = p.get("name") or p.get("power_id") or p.get("id") or "?"
            amt = p.get("amount", p.get("stacks", p.get("value")))
            out.append(f"{name}:{amt}" if amt is not None else str(name))
    return ", ".join(out)


def _strip_tags(s: str) -> str:
    s = re.sub(r"\[[^\]]*\]", "", s or "")
    return s.replace("\n", " ").strip()


_SPEAKER_RE = re.compile(r"(?:大肥鱼|DeepSeek|AI)\s*[:：]\s*", re.I)


def _clean_speech(s: str) -> str:
    """The banner shows only the line itself; never a speaker prefix/attribution."""
    return _SPEAKER_RE.sub("", str(s or "")).strip()


def _card_live(c, key_names):
    """First matching dynamic_values entry (current_value) among key_names."""
    for v in c.get("dynamic_values") or []:
        if not isinstance(v, dict):
            continue
        if str(v.get("name") or "") in key_names:
            cv = v.get("current_value", v.get("value"))
            if isinstance(cv, (int, float)):
                return float(cv)
    return None


def _hand_value_rank(hand, enemies) -> str:
    """Per-energy value ranking so cheap low-damage cards don't get defaulted to."""
    if not hand:
        return ""
    n_enemy = max(1, len([e for e in (enemies or [])
                          if isinstance(e, dict) and _int(e.get("hp", e.get("current_hp")), 1) > 0]))
    atk, blk = [], []
    for c in hand:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("card_id") or c.get("id") or "?")
        up = "+" if (c.get("upgraded") or c.get("is_upgraded")) else ""
        cost = _int(c.get("energy_cost", c.get("cost")), 0)
        if cost is None or cost < 0:
            continue
        dmg = _card_live(c, {"Damage", "CalculatedDamage"})
        block = _card_live(c, {"Block"})
        repeat = _card_live(c, {"Repeat"})
        if dmg and repeat and repeat > 1:
            # 多段卡（飞剑 3伤×4段）：动态值里的 Damage 是单段，必须乘段数
            dmg = dmg * repeat
        label = f"{cid}{up}"
        if dmg:
            per = dmg / max(1, cost)
            tag = ""
            tgt = str(c.get("target_type") or "")
            if "All" in tgt and n_enemy > 1:
                tag = f"[{n_enemy}敌合计]"
            elif "Random" in str(tgt) and n_enemy > 1:
                tag = "[随机分摊]"
            seg = f"×{repeat:.0f}段" if (repeat and repeat > 1) else ""
            atk.append((per, f"{label} {dmg:.0f}伤{seg}/{cost or 0}费={per:.1f}{tag}"))
        if block:
            per = block / max(1, cost)
            blk.append((per, f"{label} {block:.0f}甲/{cost or 0}费={per:.1f}"))
    parts = []
    if atk:
        atk.sort(key=lambda x: -x[0])
        parts.append("ATTACK " + " > ".join(s for _, s in atk[:4]))
    if blk:
        blk.sort(key=lambda x: -x[0])
        parts.append("BLOCK " + " > ".join(s for _, s in blk[:3]))
    if not parts:
        return ""
    return ("VALUE_RANK " + " | ".join(parts)
            + "  ← 同费先打高伤/高甲牌；打谁/打多少以 EFF 为准（含易伤/格挡/胆小/多段）")


def _bad_card_kind(c: dict) -> str:
    """'curse' / 'status' / '' for deck-hygiene checks (unremovable ones excluded)."""
    cid = str(c.get("card_id") or c.get("id") or "").upper()
    ctype = str(c.get("card_type") or c.get("type") or "").lower()
    if "ETERNAL" in cid or "ASCENDERS_BANE" in cid:
        return ""  # 永恒：删不掉，别浪费时间
    if "curse" in ctype or cid in {
        "DOUBT", "GUILT", "REGRET", "SHAME", "PAIN", "PARASITE", "CLUMSY", "DECAY",
        "DOUBT_CURSE", "INJURY", "NORMALITY", "WRITHE",
    }:
        return "curse"
    if "status" in ctype or cid in {"SLIMED", "WOUND", "BURN", "DAZED", "VOID"}:
        return "status"
    return ""


def _deck_bad_line(run: dict) -> str:
    deck = run.get("deck") or []
    curses, status = [], []
    for c in deck:
        if not isinstance(c, dict):
            continue
        kind = _bad_card_kind(c)
        cid = str(c.get("card_id") or c.get("id") or "?")
        if kind == "curse":
            curses.append(cid)
        elif kind == "status":
            status.append(cid)
    if not curses and not status:
        return ""
    parts = []
    if curses:
        parts.append("诅咒 " + ", ".join(curses))
    if status:
        parts.append("状态 " + ", ".join(status))
    return "DECK_BAD " + " | ".join(parts) + "  ← 下一个「移除」机会优先删这些（诅咒 > 打击）"


def _fmt_card_desc(desc: str, upgraded: bool) -> str:
    """展开中文库里的 {IfUpgraded:show:X|Y} 之类的模板标记。"""
    if not desc:
        return ""

    def _pick(m):
        return m.group(1) if upgraded else m.group(2)

    desc = re.sub(r"\{IfUpgraded:show:([^|]*)\|([^}]*)\}", _pick, desc)
    desc = re.sub(r"\{[^}]*\}", "", desc)
    return _strip_tags(desc)


def _card_line(i: int, c: dict) -> str:
    cid = c.get("card_id") or c.get("id") or "?"
    meta = CARD_DB.get(str(cid), {})
    name = meta.get("name") or c.get("name") or ""
    cost = c.get("energy_cost", c.get("cost", meta.get("cost", "?")))
    if isinstance(cost, float) and cost.is_integer():
        cost = int(cost)
    playable = c.get("playable", c.get("can_play", c.get("is_playable")))
    target = c.get("target_type") or meta.get("target") or ""
    upgraded_now = bool(c.get("upgraded") or c.get("is_upgraded"))
    # 实时文本优先：本地库可能落后一个版本；都没有时用中文库并展开模板。
    live_desc = _strip_tags(c.get("resolved_rules_text") or c.get("rules_text") or "")
    desc = live_desc or _fmt_card_desc(meta.get("description") or "", upgraded_now)
    if not desc:
        vals = c.get("dynamic_values") or []
        desc = " ".join(
            f"{v.get('name')}={v.get('current_value', v.get('value'))}"
            for v in vals
            if isinstance(v, dict)
        )
    flags = []
    if playable is False:
        flags.append("UNPLAYABLE")
    upgraded = bool(c.get("upgraded") or c.get("is_upgraded"))
    if upgraded:
        flags.append("+")
    # 卡面文字不会随升级变化（"Deal 6 damage"），真实数值在 dynamic_values.current_value。
    live = []
    for v in c.get("dynamic_values") or []:
        if not isinstance(v, dict):
            continue
        cv = v.get("current_value", v.get("value"))
        bv = v.get("base_value")
        if cv is None:
            continue
        nm = v.get("name") or "?"
        live.append(f"{nm}={cv}" if bv in (None, cv) else f"{nm}={cv}(base {bv})")
    tgts = c.get("valid_target_indices")
    ttxt = f" targets={tgts}" if isinstance(tgts, list) and len(tgts) > 1 else ""
    livetxt = f" | {', '.join(live)}" if live else ""
    star = c.get("star_cost") or 0
    starx = c.get("star_costs_x")
    startxt = f" star={'X' if starx else star}" if (star or starx) else ""
    reason = c.get("unplayable_reason")
    unplay = (
        f" UNPLAYABLE({reason})"
        if (playable is False and reason)
        else (" UNPLAYABLE" if playable is False else "")
    )
    return (
        f"[{i}] {cid} ({name}{'+' if upgraded else ''}) "
        f"cost={cost}{startxt} target={target}{ttxt} :: {desc[:110]}{livetxt}{unplay}"
    )


_MECH_CACHE: dict = {}

# Powers whose text is generic status boilerplate and only adds noise to the readout.
_GENERIC_POWERS = {
    "STRENGTH_POWER",
    "DEXTERITY_POWER",
    "VULNERABLE_POWER",
    "WEAK_POWER",
    "FRAIL_POWER",
    "POISON_POWER",
    "THORNS_POWER",
    "METALLICIZE_POWER",
    "PLATED_ARMOR_POWER",
    "INTANGIBLE_POWER",
}


def _enemy_mechanics_map() -> dict:
    """enemy_id -> list of '标题: 说明' strings for its passive powers (from db/sts2.db)."""
    if _MECH_CACHE:
        return _MECH_CACHE
    path = os.path.join(ROOT, "db", "sts2.db")
    if not os.path.exists(path):
        return _MECH_CACHE
    try:
        con = sqlite3.connect(path)
        for eid, pj in con.execute("select id, powers_json from enemies"):
            try:
                powers = json.loads(pj or "[]")
            except Exception:
                powers = []
            items = []
            for p in powers if isinstance(powers, list) else []:
                if not isinstance(p, dict):
                    continue
                if str(p.get("key") or "") in _GENERIC_POWERS:
                    continue
                tz = p.get("titleZh") or p.get("titleEn") or ""
                dz = p.get("descriptionZh") or p.get("descriptionEn") or ""
                if tz and dz:
                    items.append(f"{tz}: {dz}")
            if items:
                _MECH_CACHE[str(eid)] = items
        con.close()
    except Exception:
        pass
    return _MECH_CACHE


def enemy_mechanics(enemies) -> list:
    """Mechanic lines for the enemies currently in combat (deduped, truncated)."""
    m = _enemy_mechanics_map()
    out, seen = [], set()
    for e in enemies or []:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("enemy_id") or e.get("monster_id") or e.get("id") or "")
        if not eid or eid in seen:
            continue
        seen.add(eid)
        for line in m.get(eid, []):
            if len(line) > 150:
                line = line[:147] + "..."
            out.append(f"{eid} | {line}")
    return out


def _enemy_line(i: int, e: dict) -> str:
    eid = e.get("enemy_id") or e.get("monster_id") or e.get("id") or e.get("name") or "?"
    meta = MONSTER_DB.get(str(eid), {})
    name = meta.get("name") or e.get("name") or ""
    hp = e.get("current_hp", e.get("hp", "?"))
    mhp = e.get("max_hp", "?")
    block = e.get("block", 0)
    intents = e.get("intents") or e.get("intent") or []
    itxt = []
    if isinstance(intents, list):
        for it in intents:
            if isinstance(it, dict):
                t = it.get("intent_type") or it.get("type") or "?"
                # 多段意图必须用「每段伤害 × 段数 = 总伤」显示，
                # 否则 total_damage=10 + hits=2 会被读成 "10x2"（实际是 5x2）。
                dmg = it.get("damage", it.get("value"))
                hits = _int(it.get("hits", 1), 1)
                td = it.get("total_damage")
                s = f"{t}"
                if dmg is not None:
                    s += f" {dmg}"
                    if hits > 1:
                        s += f"x{hits}={_int(dmg) * hits}"
                        if td is not None and _int(td) != _int(dmg) * hits:
                            s += f"(td={td})"
                elif td is not None:
                    s += f" {td}"
                itxt.append(s)
            else:
                itxt.append(str(it))
    elif intents:
        itxt.append(str(intents))
    powers = _powers(e.get("powers"))
    return (
        f"[{i}] {eid} ({name}) hp={hp}/{mhp} block={block}"
        + (f" intent={';'.join(itxt)}" if itxt else "")
        + (f" powers=({powers})" if powers else "")
    )


_FX_CACHE: dict = {}


def _db_fx(table: str, item_id: str) -> str:
    """'中文名: 中文效果' for a relic/potion id from db/sts2.db (cached)."""
    key = (table, item_id)
    if key in _FX_CACHE:
        return _FX_CACHE[key]
    out = ""
    path = os.path.join(ROOT, "db", "sts2.db")
    if os.path.exists(path):
        try:
            con = sqlite3.connect(path)
            row = con.execute(
                f"select name_zh, desc_zh, desc_en from {table} where id = ?", (item_id,)
            ).fetchone()
            con.close()
            if row:
                name = row[0] or ""
                desc = row[1] or row[2] or ""
                desc = desc.replace(
                    "res://images/packed/sprite_fonts/ironclad_energy_icon.png", "1能量"
                )
                desc = re.sub(r"\[[^\]]*\]", "", desc).replace("\n", " ").strip()
                desc = re.sub(r"\s+", " ", desc)
                # source data drops the energy icon; restore the known meaning
                desc = desc.replace("并获得 。", "并获得 1 点能量。")
                desc = desc.replace("获得 。", "获得 1 点能量。")
                out = f"{name}: {desc}"
        except Exception:
            out = ""
    _FX_CACHE[key] = out
    return out


def relic_fx(relic_ids) -> list:
    out = []
    for rid in relic_ids or []:
        fx = _db_fx("relics", str(rid))
        if fx:
            out.append(fx if len(fx) <= 90 else fx[:87] + "...")
    return out


def potion_fx(potion_ids) -> list:
    out = []
    for pid in potion_ids or []:
        fx = _db_fx("potions", str(pid))
        if fx:
            out.append(fx if len(fx) <= 90 else fx[:87] + "...")
    return out


def potion_slots(state: dict) -> list:
    """run.potions 里的槽位（0/1），空槽也会返回，potion_id 为 None 表示空。"""
    run = state.get("run") or {}
    return [p for p in (run.get("potions") or []) if isinstance(p, dict)]


def potion_at(state: dict, index: int):
    for p in potion_slots(state):
        if _int(p.get("index"), -1) == index:
            return p
    return None


def map_ascii(m: dict) -> str:
    sym = {
        "Monster": "M",
        "Elite": "E",
        "RestSite": "R",
        "Shop": "$",
        "Event": "?",
        "Treasure": "T",
        "Boss": "B",
        "Ancient": "A",
        "Unknown": "?",
    }
    if not m:
        return "(no map)"
    grid = {}
    nodes = m.get("nodes") or []
    for n in nodes:
        ch = sym.get(n.get("node_type"), "?")
        if n.get("is_current"):
            ch = "@"
        elif n.get("is_available"):
            ch = "*"
        elif n.get("visited"):
            ch = "."
        grid[(n.get("row"), n.get("col"))] = ch
    rows = m.get("rows", 16)
    cols = m.get("cols", 7)
    out = []
    for r in range(rows + 1):
        out.append(f"{r:2d} " + "".join(grid.get((r, c), " ") for c in range(cols)))
    out.append("   " + "".join(str(c % 10) for c in range(cols)))
    avail = [n for n in nodes if n.get("is_available")]
    out.append("AVAILABLE:")
    for i, n in enumerate(avail):
        ch = [(x.get("row"), x.get("col")) for x in (n.get("children") or [])]
        out.append(
            f"  opt{i}: {n.get('node_type')} row={n.get('row')} col={n.get('col')} -> {ch}"
        )
    return "\n".join(out)


STATE_CACHE = os.path.join(LOGS, ".state_cache.json")


def _cache_load() -> dict:
    try:
        with open(STATE_CACHE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _cache_save(d: dict) -> None:
    try:
        tmp = STATE_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE_CACHE)
    except Exception:
        pass


_TGT_SHORT = {"AnyEnemy": "敌", "AllEnemies": "全体", "RandomEnemy": "随机",
              "Self": "己", "None": "-", "All": "全体"}


def _short_card_line(i: int, c: dict) -> str:
    """Second+ sighting of a card: keep only what a decision needs (省上下文)."""
    cid = c.get("card_id") or c.get("id") or "?"
    meta = CARD_DB.get(str(cid), {})
    cost = c.get("energy_cost", c.get("cost", meta.get("cost", "?")))
    if isinstance(cost, float) and cost.is_integer():
        cost = int(cost)
    playable = c.get("playable", c.get("can_play", c.get("is_playable")))
    tgt = str(c.get("target_type") or meta.get("target") or "")
    for k, v in _TGT_SHORT.items():
        tgt = tgt.replace(k, v)
    vals = []
    for v in c.get("dynamic_values") or []:
        if isinstance(v, dict) and v.get("current_value") is not None:
            vals.append(f"{v.get('name')}={v.get('current_value')}")
    reason = c.get("unplayable_reason")
    if playable is False:
        unplay = f" UNPLAYABLE({reason})" if reason else " UNPLAYABLE"
    else:
        unplay = ""
    vt = (" | " + ",".join(vals)) if vals else ""
    up = "+" if (c.get("upgraded") or c.get("is_upgraded")) else ""
    return f"[{i}] {cid}{up} c{cost} {tgt}{vt}{unplay}"


def compact(state: dict, full: bool = False, brief: bool = False) -> str:
    screen = state.get("screen", "?")
    run = state.get("run") or {}
    combat = state.get("combat") or {}
    lines = []
    char = run.get("character_id") or run.get("character") or "IRONCLAD"
    asc = run.get("ascension", "?")
    act = run.get("act_id", run.get("act", "?"))
    try:
        act = int(act) + 1
    except Exception:
        pass
    floor = run.get("floor", run.get("total_floor", "?"))
    hp = run.get("current_hp", run.get("hp", "?"))
    mhp = run.get("max_hp", "?")
    gold = run.get("gold", "?")
    deck = run.get("deck") or []
    lines.append(
        f"SCREEN {screen} | {char} A{asc} | act {act} floor {floor} | HP {hp}/{mhp} gold {gold} deck {len(deck)}"
    )
    if brief and screen == "COMBAT":
        # 出牌后的极简回显：只留「还能打什么、打谁、差多少」。
        c = combat
        player = c.get("player") or {}
        energy = player.get("energy", "?")
        block = player.get("block", 0)
        incoming = combat_incoming(state)
        hand = c.get("hand") or []
        ids = []
        for card in hand:
            if not isinstance(card, dict):
                continue
            cid = str(card.get("card_id") or card.get("id") or "?").replace("_IRONCLAD", "")
            cost = card.get("energy_cost", card.get("cost", "?"))
            if isinstance(cost, float) and cost.is_integer():
                cost = int(cost)
            dead = "x" if (card.get("playable") is False or card.get("can_play") is False) else ""
            ids.append(f"{cid}{dead}({cost})")
        lines.append(f"TURN {c.get('turn_number', '?')} | E{energy} blk{block} 入伤{incoming}")
        lines.append(f"HAND {len(hand)}: " + " ".join(ids))
        for i, e in enumerate(c.get("enemies") or []):
            if isinstance(e, dict):
                lines.append(" " + _enemy_line(i, e))
        if incoming > 0:
            pool, parts = hand_block_pool(hand)
            if pool:
                lines.append(f"BLOCK_POOL {pool}（{' + '.join(parts)}）")
        acts = state.get("available_actions") or []
        lines.append("LEGAL " + ",".join(str(x) for x in acts))
        return "\n".join(lines)

    cache = _cache_load()
    dirty = False

    # ---- 药水 / 遗物 / 诅咒：常驻只留一行 HUD，完整说明只在变化时打 ----
    potions = run.get("potions") or []
    occupied = []
    potion_ids = []
    for i, p in enumerate(potions):
        if isinstance(p, dict) and (p.get("occupied") or p.get("potion_id")):
            pid = str(p.get("potion_id") or p.get("name"))
            potion_ids.append(pid)
            pm = POTION_DB.get(pid, {})
            occupied.append(f"{i}:{pid}" + (f"({pm.get('name')})" if pm.get("name") else ""))

    relics = run.get("relics") or []
    rnames = []
    relic_ids = []
    for r in relics[:12]:
        if isinstance(r, dict):
            rid = str(r.get("relic_id") or r.get("name") or "?")
            relic_ids.append(rid)
            rm = RELIC_DB.get(rid, {})
            rnames.append(rid + (f"({rm.get('name')})" if rm.get("name") else ""))
        else:
            rnames.append(str(r))

    curses = []
    for c in deck:
        if isinstance(c, dict) and _bad_card_kind(c) == "curse":
            curses.append(str(c.get("card_id") or c.get("id") or "?"))

    hud = []
    if occupied:
        hud.append("potions " + " ".join(occupied))
    if rnames:
        hud.append(f"relics {len(rnames)}")
    if curses:
        hud.append("curses " + ",".join(curses))
    if hud:
        lines.append("HUD " + " | ".join(hud))

    if occupied and (full or cache.get("potions") != potion_ids):
        lines.append("POTIONS " + " ".join(occupied))
        for fx in potion_fx(potion_ids):
            lines.append("  POTION FX " + fx)
        cache["potions"] = potion_ids
        dirty = True
    if rnames and (full or cache.get("relics") != relic_ids):
        lines.append("RELICS " + ",".join(rnames))
        for fx in relic_fx(relic_ids):
            lines.append("  RELIC FX " + fx)
        cache["relics"] = relic_ids
        dirty = True
    if curses and (full or cache.get("curses") != curses):
        lines.append("DECK_BAD 诅咒 " + ",".join(curses) + " ← 商店优先删")
        cache["curses"] = curses
        dirty = True

    if screen == "COMBAT":
        turn = combat.get("turn_number", combat.get("round", combat.get("turn", "?")))
        player = combat.get("player") or {}
        energy = player.get("energy", "?")
        maxe = run.get("max_energy", "?")
        stars = player.get("stars", 0)
        block = player.get("block", 0)
        lines.append(f"TURN {turn} | energy {energy}/{maxe} stars {stars} block {block}")
        hand = combat.get("hand") or []
        lines.append(f"HAND {len(hand)}")
        seen = cache.get("cards") or {}
        for i, c in enumerate(hand):
            if not isinstance(c, dict):
                continue
            cid = str(c.get("card_id") or c.get("id") or "")
            if not full and seen.get(cid, 0) >= 1:
                lines.append(" " + _short_card_line(i, c))
            else:
                lines.append(" " + _card_line(i, c))
                if cid:
                    seen[cid] = seen.get(cid, 0) + 1
                    cache["cards"] = seen
                    dirty = True
        rank = _hand_value_rank(hand, combat.get("enemies") or [])
        if rank:
            lines.append(rank)
        enemies = combat.get("enemies") or []
        lines.append(f"ENEMIES {len(enemies)}")
        for i, e in enumerate(enemies):
            if isinstance(e, dict):
                lines.append(" " + _enemy_line(i, e))
        for row in _intent_cycle_lines(enemies, combat.get("available_actions") or state.get("available_actions") or []):
            lines.append(row)
        _incoming = combat_incoming(state)
        if _incoming > 0:
            lines.append(block_pool_note(hand, _incoming))
        held = [p for p in potion_slots(state) if p.get("potion_id")]
        if held and _incoming > 0:
            max_hp = _int(run.get("max_hp"), 0) or 80
            hp_now = _int(run.get("current_hp", run.get("hp")), max_hp)
            if _incoming >= max_hp * 0.25 or hp_now - _incoming <= max_hp * 0.35:
                names = ", ".join((p.get("name") or p.get("potion_id") or "?") for p in held)
                lines.append(
                    f"POTION_NUDGE 入伤 {_incoming} vs HP {hp_now}：槽里有 {len(held)} 瓶（{names}）"
                    "——先过 §4 四问（挡不住/斩杀差/会死就交），别让它吃灰"
                )
        for row in _attack_effect_table(hand, enemies, player):
            lines.append(row)
        copies = _copy_check(hand)
        if copies:
            lines.append(copies)
        pp = _powers(player.get("powers"))
        if pp:
            lines.append("PLAYER POWERS " + pp)
        mech = enemy_mechanics(enemies)
        en_ids = ",".join(sorted(
            str(e.get("enemy_id") or e.get("id") or "")
            for e in enemies if isinstance(e, dict)
        ))
        mech_key = f"{state.get('run_id')}|{act}|{floor}|{en_ids}"
        if mech and (full or cache.get("mech_key") != mech_key):
            lines.append("MECHANICS " + " ／ ".join(mech))
            if enemies:
                lines.append("PREFIGHT 先写一句「机制+意图循环」再出牌（Boss/精英查 references/enemies.md）")
            cache["mech_key"] = mech_key
            dirty = True
        if combat.get("end_turn_will_kill_player"):
            lines.append("WARNING end_turn_will_kill_player")
    elif screen == "MAP":
        lines.append(map_ascii(state.get("map") or {}))
    elif screen == "REWARD":
        rew = state.get("reward") or {}
        for i, r in enumerate(rew.get("rewards") or []):
            if isinstance(r, dict):
                lines.append(
                    f" reward[{i}] {r.get('reward_type', r.get('type', '?'))} "
                    f"{_short(r.get('description') or r.get('name') or '', 70)}"
                )
        for i, c in enumerate(rew.get("card_options") or []):
            if isinstance(c, dict):
                lines.append(" card_opt[" + str(i) + "] " + _card_line(i, c))
    elif screen == "SHOP":
        shop = state.get("shop") or {}
        lines.append(f"SHOP open={shop.get('is_open')} gold={gold}")
        for sec in ("cards", "relics", "potions"):
            for i, it in enumerate(shop.get(sec) or []):
                if isinstance(it, dict):
                    lines.append(
                        f" {sec}[{i}] {it.get('name') or it.get('card_id') or it.get('relic_id') or it.get('potion_id')}"
                        f" price={it.get('price')} available={it.get('available')}"
                    )
        rem = shop.get("card_removal") or {}
        if rem:
            lines.append(f" removal available={rem.get('available')} price={rem.get('price')}")
    elif screen == "EVENT":
        ev = state.get("event") or {}
        lines.append("EVENT " + str(ev.get("event_id") or ev.get("name") or ""))
        for i, o in enumerate(ev.get("options") or []):
            if isinstance(o, dict):
                lines.append(
                    f" opt[{i}] {_short(o.get('title') or o.get('text') or o.get('name') or '', 70)}"
                    f" locked={o.get('is_locked', o.get('locked'))} proceed={o.get('is_proceed')}"
                )
    elif screen == "REST":
        for i, o in enumerate((state.get("rest") or {}).get("options") or []):
            if isinstance(o, dict):
                lines.append(
                    f" rest[{i}] {_short(o.get('title') or o.get('name') or o.get('option_id') or '', 60)}"
                    f" enabled={o.get('is_enabled', o.get('enabled'))}"
                )
    elif screen == "CARD_SELECTION":
        sel = state.get("selection") or {}
        prompt = str(sel.get("prompt") or sel.get("title") or "")
        lines.append("SELECTION " + prompt)
        mn = _int(sel.get("min_select", sel.get("minSelect")), 1)
        mx = _int(sel.get("max_select", sel.get("maxSelect")), 1)
        cnt = sel.get("selected_count", sel.get("selectedCount"))
        cnt = _int(cnt, -1) if cnt is not None else -1
        cards_sel = sel.get("cards") or []
        picked = sum(1 for c in cards_sel if isinstance(c, dict) and (c.get("is_selected") or c.get("selected")))
        known = cnt >= 0 or any(isinstance(c, dict) and ("is_selected" in c or "selected" in c) for c in cards_sel)
        if cnt < 0:
            cnt = picked
        still_need = max(0, mn - cnt)
        legal_now = [str(x) for x in (state.get("available_actions") or [])]
        if mx <= 0 and "choose_reward_card" in legal_now:
            # 卡牌奖励这类「三选一」不是 select_deck_card 多选：min/max 都是 0。
            lines.append(
                " SELECTION_KIND 卡牌奖励（不是多选）："
                "choose_reward_card --option <i> 选一张，skip_reward_cards 跳过"
            )
        elif known:
            state_txt = f"SELECTION_STATE 已选 {cnt}/{mx}（最少 {mn}）"
        else:
            state_txt = f"SELECTION_STATE 需要 {mn}–{mx} 张（界面没暴露已选张数，按提示选满即可）"
        if mx > 0:
            if still_need:
                state_txt += f" ← 还差 {still_need} 张，继续点不同下标（同一张再点会取消）"
            else:
                state_txt += " ← 已满足最少张数"
                if sel.get("requires_confirmation") or sel.get("requiresConfirmation"):
                    state_txt += "，需要 confirm_selection 提交"
                else:
                    state_txt += "，点击后通常会自动提交"
            lines.append(" " + state_txt)
        is_removal = any(k in prompt for k in ("移除", "Remov"))
        for i, c in enumerate(cards_sel):
            if isinstance(c, dict):
                line = _card_line(i, c)
                if c.get("is_selected") or c.get("selected"):
                    line += "  <<已选中>>"
                bad = _bad_card_kind(c)
                if bad:
                    line += ("  <<CURSE·优先删>>" if is_removal else f"  <<{bad.upper()}>>")
                lines.append(" " + line)
    elif screen == "CHEST":
        ch = state.get("chest") or {}
        lines.append(f"CHEST opened={ch.get('is_open')} relics={len(ch.get('relic_options') or [])}")
    elif screen == "MODAL":
        m = state.get("modal") or {}
        lines.append(
            f"MODAL {m.get('type_name')} confirm={m.get('can_confirm')} dismiss={m.get('can_dismiss')}"
        )
    elif screen == "GAME_OVER":
        go = state.get("game_over") or {}
        lines.append(f"GAME_OVER victory={go.get('is_victory')} floor={go.get('floor')}")

    legal = state.get("available_actions") or []
    lines.append("LEGAL " + ",".join(str(x) for x in legal))
    if dirty:
        _cache_save(cache)
    return "\n".join(lines)


# ---------------------------------------------------------------- logging
def _new_session_path() -> str:
    p = os.path.join(LOGS, "run_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".jsonl")
    with open(SESSION_PTR, "w", encoding="utf-8") as f:
        json.dump({"path": p, "started": datetime.now().isoformat()}, f, ensure_ascii=False)
    return p


def _session_path(action: str | None = None) -> str:
    # Opening character select always starts a fresh run log; otherwise the
    # new run's first actions would be appended to the previous run.
    if action == "open_character_select":
        return _new_session_path()
    if os.path.exists(SESSION_PTR):
        try:
            with open(SESSION_PTR, encoding="utf-8") as f:
                p = json.load(f).get("path")
            if p and os.path.exists(p):
                return p
        except Exception:
            pass
    return _new_session_path()


def log(action: str, params: dict, before: dict, after: dict, resp: dict, thought: str, meme: str):
    run = after.get("run") or {}
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "params": params,
        "status": resp.get("status"),
        "message": resp.get("message"),
        "screen_before": before.get("screen"),
        "screen_after": after.get("screen"),
        "floor": run.get("floor"),
        "hp": run.get("hp", run.get("current_hp")),
        "max_hp": run.get("max_hp"),
        "gold": run.get("gold"),
        "thought": _clean_speech(thought),
        "meme": _clean_speech(meme),    }
    with open(_session_path(action), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- commands
def _recent_meme_gap(limit: int = 4) -> int:
    """How many of the last `limit` logged actions had no meme (0 = recent meme exists)."""
    gap, _, _ = _meme_stats(limit)
    return gap


# 不设硬上限：梗按“有没有节目效果”决定，只保留密度提示。
# 密度目标 25%（每 4 个动作至少 1 条梗）；低于目标就提示，连续 2 个动作没梗就催。
MEME_PACE_AFTER = 12
MEME_TARGET_RATIO = 0.25


def _meme_stats(limit: int = 4):
    """Return (gap_since_last_meme, total_memes_this_session, actions_this_session)."""
    # Read the current session pointer without creating a new log (pure read).
    if not os.path.exists(SESSION_PTR):
        return 0, 0
    try:
        with open(SESSION_PTR, encoding="utf-8") as f:
            path = json.load(f).get("path")
    except Exception:
        return 0, 0
    if not path or not os.path.exists(path):
        return 0, 0
    tail = []
    total = 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                tail.append(line)
                if '"meme": "' in line and not line.rstrip().endswith('"meme": ""}'):
                    total += 1
    except OSError:
        return 0, 0
    if not tail:
        return 0, 0
    gap = 0
    for line in reversed(tail[-limit:]):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if str(e.get("meme") or "").strip():
            break
        gap += 1
    return gap, total, len(tail)


def _meme_hint():
    gap, total, actions = _meme_stats()
    if actions <= 0:
        return
    pct = 100.0 * total / actions
    print(f"MEME_BAR {total}/{actions} {pct:.0f}% (≥{MEME_TARGET_RATIO:.0%})")
    if gap >= 4:
        print(
            f"MUST_MEME 连着 {gap} 个动作没梗了（本局 {total} 条 / {pct:.0f}%）。"
            f"下一条动作必须配梗：python tools/memedb.py suggest --situation quips"
        )
        return
    if gap >= 2:
        print(
            f"SUGGEST_MEME 最近 {gap} 个动作没有 meme（本局 {total} 条 / {pct:.0f}%）；"
            f"挑一条贴上（memedb.py suggest --situation quips），没有合适的就跳过。"
        )
        return
    if actions >= MEME_PACE_AFTER and total < actions * MEME_TARGET_RATIO:
        print(
            f"MEME_PACE 密度偏低（{pct:.0f}% < 目标 {MEME_TARGET_RATIO:.0%}）："
            f"这条动作用一条轻梗把节奏拉回来（python tools/memedb.py suggest --situation quips）"
        )


def cmd_state(raw=False, full=False):
    s = get_state()
    if raw:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        print(compact(s, full=full))


def cmd_acts():
    print(json.dumps(get_actions(), ensure_ascii=False, indent=1))


def cmd_map():
    print(map_ascii(get_state().get("map") or {}))


def _route_sym() -> dict:
    return {
        "Monster": "M", "Elite": "E", "RestSite": "R", "Shop": "$",
        "Event": "?", "Treasure": "T", "Boss": "B", "Ancient": "A", "Unknown": "?",
    }


_DEFENSE_IDS = {
    "DEFEND_IRONCLAD", "TRUE_GRIT", "BLOOD_WALL", "SHRUG_IT_OFF", "TAUNT",
    "IMPERVIOUS", "FLAME_BARRIER", "EVIL_EYE", "METALLICIZE", "ARMAMENTS",
}


def _deck_stats(state: dict) -> dict:
    """牌组健康度：张数 / 防御 / AOE / 升级数 / 诅咒。用于判断「这路线还走得动吗」。"""
    run = state.get("run") or {}
    deck = run.get("deck") or []
    atk = skl = pwr = up = defense = aoe = curses = 0
    for c in deck:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("card_id") or c.get("id") or "")
        meta = CARD_DB.get(cid, {})
        ctype = str(c.get("card_type") or meta.get("type") or "").lower()
        if c.get("upgraded") or c.get("is_upgraded"):
            up += 1
        if "attack" in ctype:
            atk += 1
        elif "skill" in ctype:
            skl += 1
        elif "power" in ctype:
            pwr += 1
        if cid in _DEFENSE_IDS or (meta.get("block") or 0) > 0:
            defense += 1
        if "all" in str(meta.get("target") or "").lower():
            aoe += 1
        kind = _bad_card_kind(c)
        if kind == "curse":
            curses += 1
    return {
        "size": len(deck),
        "attack": atk,
        "skill": skl,
        "power": pwr,
        "defense": defense,
        "aoe": aoe,
        "upgraded": up,
        "curses": curses,
    }


def _snapshot(state: dict) -> dict:
    run = state.get("run") or {}
    hp = _int(run.get("current_hp", run.get("hp")), 0)
    mhp = _int(run.get("max_hp"), 0)
    potions = run.get("potions") or []
    held = [p for p in potions if isinstance(p, dict) and (p.get("occupied") or p.get("potion_id"))]
    return {
        "floor": run.get("floor"),
        "act": run.get("act"),
        "hp": hp,
        "max_hp": mhp,
        "gold": _int(run.get("gold"), 0),
        "potions": len(held),
        "deck": _deck_stats(state),
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


def _fmt_snap(s: dict) -> str:
    d = s.get("deck") or {}
    pct = (s["hp"] / s["max_hp"]) if s.get("max_hp") else 0
    return (
        f"第{s.get('floor')}层 HP {s.get('hp')}/{s.get('max_hp')}（{pct:.0%}）"
        f" 金币{s.get('gold')} 药水{s.get('potions')}"
        f" 牌组{d.get('size')}（攻{d.get('attack')}/防{d.get('defense')}/AOE{d.get('aoe')}/升级{d.get('upgraded')}）"
    )


def _load_plan():
    try:
        with open(PLAN_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_plan(data):
    with open(PLAN_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _replan_reasons(cur: dict, plan: dict):
    """当前状态 vs 上次规划：返回需要重规划的理由（空 = 按原计划继续）。"""
    reasons = []
    if not plan or not plan.get("snapshot"):
        return reasons
    base = plan["snapshot"]
    cur_pct = (cur["hp"] / cur["max_hp"]) if cur.get("max_hp") else 0
    base_pct = (base["hp"] / base["max_hp"]) if base.get("max_hp") else 0
    drop = base["hp"] - cur["hp"]
    if base_pct >= 0.70 and cur_pct < 0.70:
        reasons.append(f"血线跌破 70%（{base_pct:.0%}→{cur_pct:.0%}）：原计划的精英/普通怪序列要降级")
    if drop >= max(10, int(0.20 * max(1, cur.get("max_hp", 1)))):
        reasons.append(f"这一段掉了 {drop} 血：后面的营火要从『锻造』改成『睡觉』")
    if cur_pct < 0.50:
        reasons.append(f"HP {cur_pct:.0%} <50%：下一格必须是营火，绕开任何精英/双怪")
    dd, bd = cur.get("deck") or {}, base.get("deck") or {}
    if dd.get("size", 0) - bd.get("size", 0) >= 3:
        reasons.append(f"牌组 {bd.get('size')}→{dd.get('size')}：抽牌稀释，防御牌占比要看一眼")
    if bd.get("defense", 0) < 6 <= dd.get("defense", 0):
        reasons.append(f"防御牌 {bd.get('defense')}→{dd.get('defense')}：达到 6 张，精英门槛可以放宽")
    if dd.get("aoe", 0) > bd.get("aoe", 0):
        reasons.append(f"拿到 AOE（{bd.get('aoe')}→{dd.get('aoe')}）：多怪精英可以考虑打了")
    if cur.get("potions") == 0 and base.get("potions", 0) > 0:
        reasons.append("药水用光：精英四检里的『救命药』失效，别硬闯")
    if cur.get("gold", 0) >= 100 and base.get("gold", 0) < 100:
        reasons.append(f"金币到 {cur.get('gold')}：开始规划商店（删牌/买防御）")
    if dd.get("curses", 0) > bd.get("curses", 0):
        reasons.append("牌组多了诅咒：下一个移除机会优先删它")
    return reasons


def cmd_route(commit=None, note=""):
    """Plan Act 1 before moving: enumerate paths to the boss and score them."""
    s = get_state()
    m = s.get("map") or {}
    run = s.get("run") or {}
    if not m:
        print("NO MAP (not on map screen)")
        return
    nodes = m.get("nodes") or []
    rows = int(m.get("rows") or 16)
    hp = _int(run.get("current_hp", run.get("hp")), 0)
    mhp = _int(run.get("max_hp"), 0)
    gold = _int(run.get("gold"), 0)
    by_pos = {(int(n.get("row")), int(n.get("col"))): n for n in nodes if n.get("row") is not None}
    by_id = {n.get("id"): n for n in nodes if n.get("id")}

    def children(n):
        out = []
        for ch in n.get("children") or []:
            node = None
            if isinstance(ch, dict):
                node = by_pos.get((int(ch.get("row", -1)), int(ch.get("col", -1))))
            elif isinstance(ch, str):
                node = by_id.get(ch)
            if node is not None:
                out.append(node)
        return out

    def walk(start):
        paths = []

        def dfs(n, path):
            if len(path) > 40 or len(paths) > 2000:
                return
            path = path + [n]
            if n.get("node_type") in ("Boss", "Ancient") or int(n.get("row", -1)) >= rows:
                paths.append(path)
                return
            for ch in children(n):
                if ch in path:
                    continue
                dfs(ch, path)

        dfs(start, [])
        return paths

    def score(path):
        c = Counter(str(n.get("node_type")) for n in path)
        pct = (hp / mhp) if (hp and mhp) else 1.0
        sc = 4.0 * c["RestSite"] + 2.0 * c["Shop"] + 2.0 * c["Treasure"] + 1.0 * c["Event"] + 0.6 * c["Unknown"]
        if c["RestSite"] >= 3:
            sc += 1.5  # 多营火：多敲牌，用户偏好
        elite_pen = 5.0 if pct < 0.55 else 3.5 if pct < 0.70 else 2.0 if pct < 0.85 else 1.0
        # 卡组强度 / 药水会改变「能不能打精英」：防御够、有 AOE、带药水才敢碰
        if deck_stats["defense"] >= 6:
            elite_pen *= 0.78
        if deck_stats["aoe"] >= 1:
            elite_pen *= 0.85
        if potion_count >= 1:
            elite_pen *= 0.85
        if deck_stats["defense"] < 5:
            elite_pen *= 1.25
        sc -= (elite_pen + 2.0) * c["Elite"]  # 非必要不精英
        sc -= (1.0 if pct < 0.45 else 0.3) * c["Monster"]
        if deck_stats["size"] > 16:
            sc += 1.0 * c["Shop"]  # 牌组大了，商店的删牌价值更高
        tail = path[-3:]
        if any(n.get("node_type") == "RestSite" for n in tail):
            sc += 2.0
        pre_boss_shop = any(n.get("node_type") == "Shop" for n in path[-5:])
        if pre_boss_shop:
            sc += 2.5
        if c["Treasure"] >= 2:
            sc += 1.0
        return sc, c, pct, pre_boss_shop

    cur = next((n for n in nodes if n.get("is_current")), None)
    avail = [n for n in nodes if n.get("is_available")]
    if not avail and cur is not None:
        avail = children(cur)
    if not avail:
        print("NO AVAILABLE NODES")
        return

    sym = _route_sym()
    pct = (hp / mhp) if (hp and mhp) else 1.0
    print(f"ROUTE PLAN | HP {hp}/{mhp} ({pct:.0%}) gold {gold} | floor {run.get('floor', '?')}")
    snap = _snapshot(s)
    deck_stats = snap.get("deck") or {}
    potion_count = _int(snap.get("potions"), 0)
    print("SELF " + _fmt_snap(snap))
    gate = []
    gate.append("防御≥6? " + ("是" if deck_stats.get("defense", 0) >= 6 else f"否（{deck_stats.get('defense', 0)}）"))
    gate.append("AOE≥1? " + ("是" if deck_stats.get("aoe", 0) >= 1 else "否"))
    gate.append("药水≥1? " + ("是" if potion_count >= 1 else "否"))
    print("ELITE_GATE " + " / ".join(gate) + " ← 三项全「是」也只当备选：非必要不精英")
    ranked = []
    for i, start in enumerate(avail):
        paths = walk(start)
        if not paths:
            continue
        best = max(paths, key=lambda p: score(p)[0])
        sc, c, p, pre_shop = score(best)
        ranked.append((sc, i, start, best, c, pre_shop))
    ranked.sort(key=lambda x: x[0], reverse=True)
    for sc, i, start, path, c, pre_shop in ranked[:3]:
        seq = " ".join(sym.get(str(n.get("node_type")), "?") for n in path)
        notes = []
        if c["Elite"]:
            notes.append(f"精英{c['Elite']}")
        if c["RestSite"]:
            notes.append(f"营火{c['RestSite']}")
        if c["Shop"]:
            notes.append(f"商店{c['Shop']}")
        if c["Treasure"]:
            notes.append(f"宝箱{c['Treasure']}")
        if any(n.get("node_type") == "RestSite" for n in path[-3:]):
            notes.append("Boss前有营火")
        if pre_shop:
            notes.append("Boss前有商店")
        if pct < 0.70 and c["Elite"]:
            notes.append("HP<70%：精英高风险")
        print(f"opt{i} {start.get('node_type')} ({start.get('row')},{start.get('col')}) score={sc:.1f} | {' / '.join(notes) or '常规'}")
        print(f"  {seq}")
    if pct < 0.70:
        print("WARNING HP<70%：火堆权重提高，优先睡觉；非必要不打精英。")
    if pct < 0.50:
        print("WARNING HP<50%：下一格必须营火；没有营火就改道/用药，不接双怪或精英。")
    if gold >= 100 and not any(r[5] for r in ranked[:1]):
        print("WARNING 金币≥100 且首选路线 Boss 前无商店：考虑改道/预留删牌。")

    # 上次规划 vs 现在：状态变了就重规划，别按三层前的计划硬走。
    plan = _load_plan()
    if plan:
        base = plan.get("snapshot") or {}
        print(
            f"PLAN_BASE 上次规划：第{base.get('floor')}层 opt{plan.get('option')} "
            f"{plan.get('seq', '')} | {_fmt_snap(base) if base else '?'}"
        )
        reasons = _replan_reasons(snap, plan)
        if reasons:
            print("REPLAN 建议重规划：")
            for r in reasons:
                print("  - " + r)
        else:
            print("PLAN_OK 状态与上次规划一致，按原计划走（每次仍要看 AVAILABLE）")
    if commit is not None:
        pick = next((r for r in ranked if r[1] == commit), None)
        if pick is None:
            print(f"COMMIT_FAIL opt{commit} 不在候选里（候选：{[r[1] for r in ranked]}）")
        else:
            sc, i, start, path, c, pre_shop = pick
            seq = " ".join(sym.get(str(n.get("node_type")), "?") for n in path)
            _save_plan(
                {
                    "snapshot": snap,
                    "option": i,
                    "start": {"row": start.get("row"), "col": start.get("col")},
                    "seq": seq,
                    "note": note,
                }
            )
            print(f"PLAN_COMMIT 已记录 opt{i} ({start.get('row')},{start.get('col')}) {seq}")


def cmd_plan(clear=False, note=""):
    """看当前路线计划是否还成立（状态一抖就要重规划）。"""
    s = get_state()
    run = s.get("run") or {}
    if clear:
        try:
            os.remove(PLAN_PATH)
        except OSError:
            pass
        print("PLAN_CLEARED 计划已清空（换幕/换路线/弃局后重记）")
        return
    if not run.get("max_hp"):
        print("NO RUN 不在局内（主菜单/结算界面），没有可对账的计划")
        return
    snap = _snapshot(s)
    print("SELF " + _fmt_snap(snap))
    if clear:
        try:
            os.remove(PLAN_PATH)
        except OSError:
            pass
        print("PLAN_CLEARED 计划已清空（换幕/换路线后重记）")
        return
    plan = _load_plan()
    if not plan:
        print("PLAN_NONE 还没有记录计划：`python sts2.py route --commit <opt>` 先记一条")
        return
    base = plan.get("snapshot") or {}
    print(
        f"PLAN_BASE 第{base.get('floor')}层 opt{plan.get('option')} {plan.get('seq', '')}"
        + (f" | {_fmt_snap(base)}" if base else "")
        + (f" | 备注：{plan['note']}" if plan.get("note") else "")
    )
    reasons = _replan_reasons(snap, plan)
    if reasons:
        print("REPLAN 建议重规划：")
        for r in reasons:
            print("  - " + r)
        print("  → 先 `python sts2.py route` 看新排序，再 `route --commit <opt>` 记下")
    else:
        print("PLAN_OK 仍按原计划：每次选节点前照旧重读 AVAILABLE")


# ---------------------------------------------------------------- combat math
_POWER_ALIASES = {
    "str": ("力量", "Strength", "STRENGTH"),
    "dex": ("敏捷", "Dexterity", "DEXTERITY"),
    "vuln": ("易伤", "Vulnerable", "VULNERABLE"),
    "weak": ("虚弱", "Weak", "WEAK"),
    "frail": ("脆弱", "Frail", "FRAIL"),
    "thorns": ("荆棘", "Thorns", "THORNS"),
    "timid": ("胆小", "Timid", "TIMID"),
    "plating": ("覆甲", "Plating", "PLATING"),
    "metallicize": ("金属化", "Metallicize"),
    "artifact": ("人工制品", "Artifact"),
    "intangible": ("无实体", "Intangible"),
    "barricade": ("壁垒", "Barricade"),
}


def _power_map(powers) -> dict:
    """{'vuln': 2, 'thorns': 3, ...}；认不出的能力按原名保留。"""
    out = {}
    for p in powers or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or p.get("power_id") or p.get("id") or "")
        amt = p.get("amount", p.get("stacks", p.get("value")))
        if not name:
            continue
        for key, aliases in _POWER_ALIASES.items():
            if any(a in name for a in aliases):
                out[key] = _int(amt, 1) if amt is not None else 1
                break
        else:
            out[name] = amt
    return out


def _sim_hits(per: int, hits: int, block: int, timid: int) -> int:
    """模拟 N 段命中：格挡池 + 胆小首击加盾；返回真实进血量。"""
    pool = max(0, block)
    total = 0
    for i in range(max(1, hits)):
        if i == 0:
            pool += max(0, timid)
        d = per
        if pool > 0:
            absorbed = min(pool, d)
            pool -= absorbed
            d -= absorbed
        total += d
    return total


_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _num_from(tok: str) -> int:
    tok = (tok or "").strip()
    if tok.isdigit():
        return int(tok)
    return _CN_NUM.get(tok, 0)


def _hits_from_text(c: dict):
    """从牌面文字补段数：(固定段数, 仅对易伤目标额外的段数)。

    Repeat 动态值只覆盖固定多段（飞剑 3×3）；像「拆卸：若目标易伤则攻击两次」
    这类条件多段只能从文字里读，否则会把 16 伤算成 8。
    """
    txt = _strip_tags(c.get("resolved_rules_text") or c.get("rules_text") or "").replace(" ", "")
    fixed, cond = 1, 0
    m = re.search(r"造成(\d+)点伤害(\d+)次", txt)
    if m:
        fixed = max(fixed, _num_from(m.group(2)))
    hits_tokens = re.findall(r"(?:攻击|打击|命中)([一两二三四五六七八九十\d]+)次", txt)
    if hits_tokens:
        n = max(_num_from(t) for t in hits_tokens)
        if "易伤" in txt:
            cond = max(cond, n)
        else:
            fixed = max(fixed, n)
    return zero_if_bad(fixed, 1), cond


def zero_if_bad(v: int, default: int) -> int:
    return v if v and v > 0 else default


def _attack_profile(c: dict):
    """(每段伤害, 固定段数, 易伤额外段数)；Damage 已含自身力量/虚弱。"""
    per = _card_live(c, {"Damage", "CalculatedDamage"})
    if not per:
        return None
    repeat = _int(_card_live(c, {"Repeat", "Hits"}), 1) or 1
    fixed, cond = _hits_from_text(c)
    return int(per), max(1, repeat, fixed), cond


def _attack_effect_table(hand, enemies, player) -> list:
    """每张攻击牌对每个敌人的真实伤害（易伤 / 格挡 / 胆小 / 段数 / 荆棘反伤）。"""
    live = [
        (i, e)
        for i, e in enumerate(enemies or [])
        if isinstance(e, dict) and _int(e.get("current_hp", e.get("hp")), 1) > 0
    ]
    if not hand or not live:
        return []
    rows = []
    for idx, c in enumerate(hand):
        if not isinstance(c, dict):
            continue
        prof = _attack_profile(c)
        if not prof:
            continue
        per, fixed_hits, cond_hits = prof
        cid = str(c.get("card_id") or "?")
        up = "+" if (c.get("upgraded") or c.get("is_upgraded")) else ""
        parts = []
        lethal = []
        chips = []
        max_hits = fixed_hits
        for ei, e in live:
            pm = _power_map(e.get("powers"))
            mult = 1.5 if pm.get("vuln") else 1.0
            # cond_hits 是「条件满足时的总段数」，不是额外段数
            hits = max(fixed_hits, cond_hits) if pm.get("vuln") else fixed_hits
            max_hits = max(max_hits, hits)
            eff = int(per * mult)
            dmg = _sim_hits(eff, hits, _int(e.get("block"), 0), _int(pm.get("timid"), 0))
            tag = f"e{ei}:{dmg}"
            if pm.get("vuln"):
                tag += "(易伤)"
            if _int(pm.get("timid"), 0):
                tag += f"(胆小{_int(pm.get('timid'), 0)})"
            hp = _int(e.get("current_hp", e.get("hp")), 0)
            if hp and dmg >= hp:
                tag += "★斩"
                lethal.append(f"e{ei}")
            parts.append(tag)
        hit_txt = f"{per}x{max_hits}" if max_hits > 1 else str(per)
        if cond_hits:
            chips.append(f"易伤时{cond_hits}段")
        thorns = max((_int(_power_map(e.get("powers")).get("thorns"), 0) for _i, e in live), default=0)
        if thorns:
            chips.append(f"反伤-{thorns * max_hits}")
        rows.append(
            f"EFF [{idx}] {cid}{up} {hit_txt}伤 → " + " ".join(parts[:4])
            + (f" | ★可斩杀 {'/'.join(lethal)}" if lethal else "")
            + ((" | " + " ".join(chips)) if chips else "")
        )
        if len(rows) >= 6:
            break
    order = _order_hint(hand, live)
    if order:
        rows.append(order)
    return rows


def _order_hint(hand, live) -> str:
    """手里有易伤牌时，提示「先挂易伤」能把其它攻击抬多少。"""
    appliers = []
    for i, c in enumerate(hand or []):
        if not isinstance(c, dict):
            continue
        if _int(_card_live(c, {"VulnerablePower"}), 0) > 0:
            appliers.append((i, c))
    if not appliers:
        return ""
    i0, c0 = appliers[0]
    gains = []
    for i, c in enumerate(hand or []):
        if not isinstance(c, dict) or i == i0:
            continue
        prof = _attack_profile(c)
        if not prof:
            continue
        per, fixed_hits, cond_hits = prof
        before = per * fixed_hits
        after_hits = max(fixed_hits, cond_hits) if cond_hits else fixed_hits
        after = int(per * 1.5) * after_hits
        if after > before:
            gains.append(f"{c.get('card_id')}{'+' if (c.get('upgraded') or c.get('is_upgraded')) else ''} {before}→{after}")
    if not gains:
        return ""
    return (
        f"ORDER 先打 [{i0}] {c0.get('card_id')} 挂易伤 → "
        + "，".join(gains[:4])
        + "  ← 第一回合先挂易伤，别先打普通刀"
    )


def _copy_check(hand) -> str:
    """手里同名但升级状态不同的牌：提醒别打出未升级那张。"""
    groups = {}
    for i, c in enumerate(hand or []):
        if not isinstance(c, dict):
            continue
        cid = str(c.get("card_id") or c.get("id") or "?")
        groups.setdefault(cid, []).append((i, bool(c.get("upgraded") or c.get("is_upgraded"))))
    parts = []
    for cid, items in groups.items():
        if len(items) < 2:
            continue
        ups = [f"[{i}]" for i, u in items if u]
        downs = [f"[{i}]" for i, u in items if not u]
        if ups and downs:
            parts.append(f"{cid}: 升级版 {'/'.join(ups)} vs 未升级 {'/'.join(downs)} → 打升级版")
    if not parts:
        return ""
    return "COPIES " + "；".join(parts[:3]) + "  ← 同名不同级，别把未升级那张打出去"


# ---------------------------------------------------------------- intent memory
INTENT_MEM_PATH = os.path.join(LOGS, "intent_memory.json")


def _intent_sig(enemy: dict) -> str:
    """把当前意图压成一个稳定字符串，用于跨回合比对。"""
    sigs = []
    for it in enemy.get("intents") or []:
        if not isinstance(it, dict):
            continue
        typ = str(it.get("intent_type") or it.get("type") or "?")
        dmg = it.get("damage", it.get("value"))
        hits = _int(it.get("hits", 1), 1)
        if dmg is not None:
            sigs.append(f"{typ} {dmg}" + (f"x{hits}" if hits > 1 else ""))
        else:
            sigs.append(typ)
    return ";".join(sigs) or "?"


def _load_intent_mem() -> dict:
    try:
        with open(INTENT_MEM_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_intent_mem(mem: dict):
    try:
        with open(INTENT_MEM_PATH, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False)
    except OSError:
        pass


def _intent_cycle_lines(enemies, actions) -> list:
    """记录每种敌人见过的意图序列，并推测下一次意图（敌人招式基本是固定循环）。

    run31 复盘：如果能提前知道「下一手是大攻击」，这回合就会多留格挡。
    """
    if not enemies:
        return []
    if not ("play_card" in (actions or []) or "end_turn" in (actions or [])):
        return []  # 只稳定玩家回合才记录，避免把过渡态写进历史
    mem = _load_intent_mem()
    lines = []
    for i, e in enumerate(enemies):
        if not isinstance(e, dict):
            continue
        eid = str(e.get("enemy_id") or e.get("name") or "?")
        cur = _intent_sig(e)
        hist = mem.get(eid) or []
        if not hist or hist[-1] != cur:
            hist = (hist + [cur])[-8:]
            mem[eid] = hist
        if len(hist) < 2:
            continue
        guess = ""
        n = len(hist)
        for j in range(n - 2, -1, -1):
            if hist[j] == cur and j + 1 < n:
                guess = hist[j + 1]
                break
        seq = " → ".join(hist[-4:])
        lines.append(
            f"INTENT_MEM e{i} {eid}: {seq}"
            + (f" | 上次出现后接的是：{guess}" if guess else " | 还没见过重复，先按招式表预判")
        )
    _save_intent_mem(mem)
    return lines


def _intent_damage(enemy: dict) -> int:
    total = 0
    for it in enemy.get("intents") or []:
        if not isinstance(it, dict):
            continue
        typ = str(it.get("intent_type") or it.get("type") or "").upper()
        if "ATTACK" not in typ and "DEATH" not in typ:
            continue
        # total_damage (when present) is already the full attack value for the
        # turn (e.g. label "5x4" -> total_damage 20). Only multiply damage by
        # hits when the mod left total_damage empty, otherwise incoming is
        # over-counted by the hit count.
        td = it.get("total_damage")
        if td is not None:
            total += _int(td)
            continue
        dmg = _int(it.get("damage", it.get("value")), 0)
        hits = _int(it.get("hits", 1), 1)
        total += dmg * max(1, hits)
    return total


def combat_incoming(state: dict) -> int:
    if state.get("screen") != "COMBAT":
        return 0
    enemies = ((state.get("combat") or {}).get("enemies") or [])
    return sum(_intent_damage(e) for e in enemies if isinstance(e, dict))


def combat_anchor(state: dict) -> str:
    if state.get("screen") != "COMBAT":
        return ""
    c = state.get("combat") or {}
    enemies = c.get("enemies") or []
    incoming = combat_incoming(state)
    player = c.get("player") or {}
    energy = player.get("energy", "?")
    hand = c.get("hand") or []
    line = f"ANCHOR enemies={len(enemies)} incoming={incoming} energy={energy} hand={len(hand)}"
    pm_self = _power_map(player.get("powers"))
    self_bits = [f"hp={player.get('current_hp', player.get('hp', '?'))}"]
    if player.get("block"):
        self_bits.append(f"blk={player.get('block')}")
    for key, label in (("str", "力"), ("dex", "敏"), ("weak", "虚弱"), ("vuln", "易伤"), ("frail", "脆弱")):
        if pm_self.get(key):
            self_bits.append(f"{label}{pm_self[key]}")
    line += " | self: " + " ".join(self_bits)
    # 多段意图逐条展开：伤害×段数 与 total_damage 都对出来，防止把 8x4 误读成 8。
    detail = []
    for e in enemies:
        if not isinstance(e, dict):
            continue
        name = str(e.get("enemy_id") or e.get("name") or "?")
        for it in e.get("intents") or []:
            if not isinstance(it, dict):
                continue
            typ = str(it.get("intent_type") or it.get("type") or "")
            if "ATTACK" not in typ.upper() and "DEATH" not in typ.upper():
                continue
            dmg = it.get("damage", it.get("value"))
            hits = it.get("hits", 1)
            td = it.get("total_damage")
            calc = ""
            if isinstance(dmg, (int, float)):
                calc = f"dmg={dmg}x{hits}"
                if isinstance(hits, (int, float)) and hits > 1:
                    calc += f"={float(dmg)*hits:.0f} 多段{hits}次"
            if td is not None:
                calc += f" td={td}"
            detail.append(f"{name}[{typ} {calc}]")
    if detail:
        line += " | " + " ".join(detail[:6])
    if incoming == 0:
        line += " | NO_INCOMING: attack/engine first; pure defense only for setup/draw/retain"
    else:
        line += " | " + block_pool_note(hand, incoming)
    return line


def hand_block_pool(hand) -> tuple:
    """手牌里可直接提供的格挡合计（含覆甲/坚毅这类基础格挡牌）。"""
    total = 0
    parts = []
    for c in hand:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("card_id") or "")
        block = 0
        for v in c.get("dynamic_values") or []:
            if not isinstance(v, dict):
                continue
            if str(v.get("name", "")).strip().lower() == "block":
                block = _int(v.get("current_value", v.get("value")), 0)
        if block <= 0:
            continue
        total += block
        nm = (CARD_DB.get(cid) or {}).get("name") or cid
        parts.append(f"{nm}{block}")
    return total, parts


def block_pool_note(hand, incoming: int) -> str:
    """BLOCK_POOL：手牌够不够挡满？不够的话净掉多少？

    run33 复盘（用户指出）：潜行群落那一回合手里两张耸肩、两费富余，
    却没叠防御硬吃伤害。把「能挡多少 / 挡完净掉多少」直接打在屏上。
    """
    total, parts = hand_block_pool(hand)
    if not parts:
        return f"BLOCK_POOL 手牌无格挡牌｜入伤 {incoming} → 净掉 {incoming}"
    txt = f"BLOCK_POOL 手牌可挡 {total}（{' + '.join(parts)}）｜入伤 {incoming} → "
    if total >= incoming:
        return txt + "够挡满（净掉 0）"
    return txt + f"不够，净掉 {incoming - total}"


def _card_strength(c: dict):
    """Rank duplicate copies: upgraded first, then higher live values."""
    up = 1 if (c.get("upgraded") or c.get("is_upgraded")) else 0
    tot = 0.0
    for v in c.get("dynamic_values") or []:
        if not isinstance(v, dict):
            continue
        cv = v.get("current_value", v.get("value"))
        if isinstance(cv, (int, float)):
            tot += float(cv)
    return (up, tot)


def cmd_play(card_id: str, target: int | None = None, thought: str = "", meme: str = "",
             card: int | None = None, full: bool = False):
    """Play a card by card_id. Same-name copies (Strike vs Strike+) resolve to the strongest."""
    s = get_state()
    hand = ((s.get("combat") or {}).get("hand") or [])
    q = str(card_id or "").upper()
    matches = []
    for i, c in enumerate(hand):
        cid = str(c.get("card_id") or c.get("id") or "")
        name = str(c.get("name") or "")
        if (cid and (cid.upper() == q or q in cid.upper())) or (name and q in name.upper()):
            matches.append((i, c))
    if not matches:
        print(f"CARD_NOT_IN_HAND {card_id}")
        print(compact(s))
        sys.exit(2)
    if card is not None:
        idx = card
        # 显式指定下标时，若同名牌还有更强的版本，提醒一下再打（不然 6 伤 vs 9 伤又打错）
        best = max(matches, key=lambda m: _card_strength(m[1]))
        if best[0] != idx:
            best_c = best[1]
            print(
                "WRONG_COPY 指定的 [%d] 不是最强同名副本"
                "（[%d]%s%s 更强：%s）；确认要打这张就继续，否则用 play %s"
                % (
                    idx,
                    best[0],
                    best_c.get("card_id"),
                    "+" if (best_c.get("upgraded") or best_c.get("is_upgraded")) else "",
                    ", ".join(
                        f"{v.get('name')}={v.get('current_value')}"
                        for v in (best_c.get("dynamic_values") or []) if isinstance(v, dict)
                    ),
                    card_id,
                )
            )
    elif len(matches) > 1:
        desc = []
        for i, c in matches:
            vals = ", ".join(
                f"{v.get('name')}={v.get('current_value')}"
                for v in (c.get("dynamic_values") or []) if isinstance(v, dict)
            )
            desc.append(f"[{i}]{c.get('card_id')}{'+' if (c.get('upgraded') or c.get('is_upgraded')) else ''}({vals})")
        best = max(matches, key=lambda m: _card_strength(m[1]))
        idx = best[0]
        print(f"MULTI_COPY {len(matches)} candidates: " + " | ".join(desc) + f" -> chose [{idx}] (upgraded/highest); use --card <index> to override")
    else:
        idx, _ = matches[0]
    pure_def = {
        "DEFEND_IRONCLAD", "BLOOD_WALL", "FLAME_BARRIER", "TRUE_GRIT",
        "SHRUG_IT_OFF", "TAUNT", "IMPERVIOUS",
    }
    if q in pure_def and combat_incoming(s) == 0:
        print("WARNING no incoming attack: pure defense may be wasted; attack/engine first unless setting up Body Slam/Barricade/retain.")
    cmd_do("play_card", card=idx, target=target, thought=thought, meme=meme, full=full)


def _fingerprint(s: dict):
    c = s.get("combat") or {}
    hand = c.get("hand") or []
    ids = tuple((x.get("card_id") or x.get("id"), x.get("energy_cost", x.get("cost"))) for x in hand if isinstance(x, dict))
    p = c.get("player") or {}
    enemies = tuple((e.get("enemy_id") or e.get("name"), e.get("current_hp")) for e in (c.get("enemies") or []) if isinstance(e, dict))
    # 多选界面：进度变化必须进指纹，否则 wait_stable 会把「选中/取消」误判成已稳定。
    sel = s.get("selection") or {}
    sel_cards = sel.get("cards") or []
    sel_fp = (
        tuple((x.get("card_id") or x.get("id"), bool(x.get("is_selected") or x.get("selected"))) for x in sel_cards if isinstance(x, dict)),
        _int(sel.get("selected_count", sel.get("selectedCount")), -1),
        _int(sel.get("max_select", sel.get("maxSelect")), -1),
    )
    return (
        s.get("screen"),
        s.get("turn"),
        p.get("energy"),
        ids,
        enemies,
        len(s.get("available_actions") or []),
        sel_fp,
    )


def wait_legal(action: str, timeout: float = 5.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        s = get_state()
        if action in (s.get("available_actions") or []):
            return s
        if s.get("screen") != "COMBAT":
            return s
        last = s
        time.sleep(0.15)
    return last


def wait_new_turn_hand(prev_hand_len: int, timeout: float = 2.5) -> dict:
    """end_turn 之后手牌可能是「还没抽牌」的过渡快照：等手牌数稳定再交给调用方决策。

    run30 的坑：结束回合后立刻用返回快照做决策，看到的手牌是空的（或只有 1 张），
    于是漏掉了当回合本来能打的斩杀牌。
    """
    end = time.time() + timeout
    last = get_state()
    stable = 0
    while time.time() < end:
        s = get_state()
        hand = ((s.get("combat") or {}).get("hand") or [])
        n = len(hand)
        if s.get("screen") != "COMBAT":
            return s
        if n == len(((last.get("combat") or {}).get("hand") or [])) and n > 0:
            stable += 1
        else:
            stable = 0
        last = s
        if stable >= 2 and n >= 1:
            return s
        time.sleep(0.12)
    return last


def wait_stable_player_state(timeout: float = 6.0):
    end = time.time() + timeout
    last_fp = None
    last_state = None
    stable = 0
    while time.time() < end:
        s = get_state()
        fp = _fingerprint(s)
        if fp == last_fp:
            stable += 1
        else:
            stable = 0
            last_fp = fp
        last_state = s
        acts = s.get("available_actions") or []
        if s.get("screen") != "COMBAT":
            if stable >= 1:
                return s
        elif ("play_card" in acts or "end_turn" in acts) and stable >= 1:
            return s
        time.sleep(0.15)
    print("WARNING state did not stabilize into a player turn; not trusting this snapshot.")
    return last_state or get_state()


def _sel_progress(s: dict):
    """(已选, 最少, 最多, 是否停留选牌界面)"""
    if s.get("screen") != "CARD_SELECTION":
        return (None, None, None, False)
    sel = s.get("selection") or {}
    cnt = sel.get("selected_count", sel.get("selectedCount"))
    picked = sum(1 for c in (sel.get("cards") or []) if isinstance(c, dict) and (c.get("is_selected") or c.get("selected")))
    cnt = _int(cnt, -1) if cnt is not None else -1
    if cnt < 0:
        cnt = picked
    return (
        cnt,
        _int(sel.get("min_select", sel.get("minSelect")), 1),
        _int(sel.get("max_select", sel.get("maxSelect")), 1),
        True,
    )


def wait_selection_settle(before: dict, timeout: float = 3.0) -> dict:
    """点完一张牌后等界面稳定：屏幕离开选牌界面，或已选张数/卡面发生变化。"""
    base_fp = _fingerprint(before)
    end = time.time() + timeout
    last = before
    stable = 0
    while time.time() < end:
        s = get_state()
        fp = _fingerprint(s)
        changed = fp != base_fp
        if s.get("screen") != "CARD_SELECTION":
            if stable >= 1:
                return s
        elif changed and fp == _fingerprint(last):
            if stable >= 1:
                return s
        stable = stable + 1 if fp == _fingerprint(last) else 0
        last = s
        time.sleep(0.12)
    return last


def _resolve_by_descriptor(cards: list, desc: dict):
    """按下标快照里的卡牌描述，在新快照里找回同一张牌。

    返回 (下标, 是否精确命中)。列表若缩短（有些界面点一张就立刻移除），
    精确的第 n 张可能已经不存在，此时回退到同名牌的第一张，避免整步跳过。
    """
    target_id = desc.get("card_id")
    target_up = desc.get("upgraded")
    matches = []
    for i, c in enumerate(cards):
        if not isinstance(c, dict):
            continue
        if (c.get("card_id") or c.get("id")) != target_id:
            continue
        if bool(c.get("upgraded") or c.get("is_upgraded")) != bool(target_up):
            continue
        matches.append(i)
    nth = desc.get("occurrence", 0)
    if nth < len(matches):
        return matches[nth], True
    if matches:
        return matches[0], False
    return None, False


def _pick_cards_once(picks, thought="", meme=""):
    """多选界面：按给定下标依次选牌，自动处理确认，返回最终状态。"""
    s0 = get_state()
    cards0 = ((s0.get("selection") or {}).get("cards") or [])
    if not cards0:
        print("ERROR picks: 当前不在多选界面（selection.cards 为空）")
        sys.exit(2)
    total0 = _sel_progress(s0)
    print(
        f"PICKS 计划选 {len(picks)} 张：{picks}｜界面要求 {total0[1]}–{total0[2]} 张，当前已选 {total0[0]}"
    )
    # 先把下标解析成「卡牌描述」，之后每步都按描述重新定位，避免列表变化导致错位。
    planned = []
    for idx in picks:
        if idx < 0 or idx >= len(cards0):
            print(f"ERROR picks: 下标 {idx} 越界（界面共 {len(cards0)} 张）")
            sys.exit(2)
        c = cards0[idx]
        cid = c.get("card_id") or c.get("id")
        up = bool(c.get("upgraded") or c.get("is_upgraded"))
        nth = sum(
            1
            for j in range(idx)
            if isinstance(cards0[j], dict)
            and (cards0[j].get("card_id") or cards0[j].get("id")) == cid
            and bool(cards0[j].get("upgraded") or cards0[j].get("is_upgraded")) == up
        )
        planned.append({"card_id": cid, "upgraded": up, "occurrence": nth, "label": f"[{idx}] {cid}{'+' if up else ''}"})

    cur = s0
    for k, desc in enumerate(planned, 1):
        if cur.get("screen") != "CARD_SELECTION":
            print(f"PICKS 第 {k} 张跳过：界面已离开选牌（前 {k - 1} 张已生效）")
            return cur
        cards = ((cur.get("selection") or {}).get("cards") or [])
        idx, exact = _resolve_by_descriptor(cards, desc)
        if idx is None:
            print(f"PICKS 第 {k} 张跳过：{desc['label']} 已不在界面里（可能已被上一张移除）")
            continue
        if not exact:
            print(f"PICKS 第 {k} 张按同名牌回退到下标 {idx}（列表已变化）")
        before = cur
        resp = act("select_deck_card", option_index=idx)
        cur = wait_selection_settle(before)
        cnt, mn, mx, in_sel = _sel_progress(cur)
        status = resp.get("status") if isinstance(resp, dict) else "?"
        if in_sel:
            print(
                f"PICKS {k}/{len(planned)} 已点 {desc['label']}（实际下标 {idx}，{status}）→ 已选 {cnt}/{mx}"
                + (f"，还差 {max(0, mn - cnt)} 张" if cnt < mn else "，已满足")
            )
        else:
            print(f"PICKS {k}/{len(planned)} 已点 {desc['label']}（实际下标 {idx}，{status}）→ 界面已离开选牌")

    # 需要手动确认的界面，自动提交。
    if cur.get("screen") == "CARD_SELECTION":
        acts = cur.get("available_actions") or []
        cnt, mn, mx, _ = _sel_progress(cur)
        if cnt >= mn and ("confirm_selection" in acts or (cur.get("selection") or {}).get("requires_confirmation")):
            try:
                act("confirm_selection")
                cur = wait_selection_settle(cur)
                print(f"PICKS 已选 {cnt}/{mx}，自动 confirm_selection 提交")
            except Exception as e:
                print(f"PICKS confirm_selection 失败：{e}（请手动确认）")
        else:
            print(f"PICKS WARNING 仍在选牌界面（已选 {cnt}/{mx}，最少 {mn}）：还需要再选 {max(0, mn - cnt)} 张")
    return cur


def cmd_do(action, card=None, target=None, option=None, thought="", meme="", options=None, full=False):
    if options:
        if action != "select_deck_card":
            print(f"WARN --options 只用于 select_deck_card（收到 {action}），已忽略")
        else:
            after = _pick_cards_once(options, thought, meme)
            tag = tag_for(after.get("screen", ""))
            overlay_say(tag, thought, meme, after)
            print(compact(after))
            _meme_hint()
            return
    before = get_state()
    if action in ("play_card", "end_turn") and before.get("screen") == "COMBAT":
        acts = before.get("available_actions") or []
        if acts and action not in acts:
            print(f"WAIT {action} not legal yet; waiting for player turn...")
            s = wait_legal(action, timeout=5.0)
            if not s or action not in (s.get("available_actions") or []):
                print(f"ACTION_SKIPPED {action}: no legal player-turn snapshot; not sending during transition.")
                return
            before = s
    if action == "end_turn" and before.get("screen") == "COMBAT":
        hand = ((before.get("combat") or {}).get("hand") or [])
        zeros = []
        for c in hand:
            if not isinstance(c, dict):
                continue
            cost = c.get("energy_cost", c.get("cost"))
            if _int(cost, 99) != 0:
                continue
            if c.get("playable") is False or c.get("can_play") is False:
                continue
            zeros.append(str(c.get("card_id") or c.get("id") or c.get("name") or "?"))
        if zeros:
            print("WARNING ending with 0-cost playable cards: " + ", ".join(zeros) + " — consider free damage/draw/block first.")
        energy = _int(((before.get("combat") or {}).get("player") or {}).get("energy"), 0)
        affordable = []
        for c in hand:
            if not isinstance(c, dict):
                continue
            if c.get("playable") is False or c.get("can_play") is False:
                continue
            cost = _int(c.get("energy_cost", c.get("cost")), 99)
            if cost is None or cost < 0 or cost > energy:
                continue
            cid = str(c.get("card_id") or c.get("id") or "?")
            affordable.append(f"{cid}({cost}费)")
        if energy and energy > 0 and affordable:
            print(
                f"WASTE_ENERGY 还剩 {energy} 点能量没花，手上有 {len(affordable)} 张能打的牌："
                + ", ".join(affordable[:6])
                + " —— 空过回合 = 白挨打；确实不能打（会送 Buff/吃荆棘/留牌给下回合）就在 thought 里写明原因。"
            )
        # 挡牌留在手上就结束回合 → 白吃伤害（run33 潜行群落那回就是两张耸肩没打）
        incoming = combat_incoming(before)
        player_blk = _int(((before.get("combat") or {}).get("player") or {}).get("block"), 0)
        if incoming > player_blk and energy > 0:
            pool, parts = hand_block_pool(hand)
            if parts and pool > 0:
                print(
                    f"LEFT_BLOCK_ON_TABLE 入伤 {incoming}、当前格挡 {player_blk}，手牌还有格挡 "
                    f"{pool}（{' + '.join(parts)}）能量 {energy} —— 够挡就先把血挡住再谈输出。"
                )
    if action == "use_potion":
        slot = _int(option, 0)
        p = potion_at(before, slot)
        if p is None or not p.get("potion_id"):
            print(f"POTION_EMPTY 槽位 {slot} 是空的：先用 state 看 POTIONS 行再决定（别对着空槽点）")
            for q in potion_slots(before):
                print(f"  slot[{q.get('index')}] {q.get('name') or '-'} occupied={bool(q.get('potion_id'))}")
            sys.exit(2)
        print(
            f"POTION_USE 槽位 {slot} · {p.get('name') or p.get('potion_id')}："
            f"{_short(p.get('description') or '', 70)}"
        )
        if p.get("requires_target") and target is None:
            ti = _auto_target_index(before)
            if ti is not None:
                target = ti
                print(f"POTION_AUTO_TARGET 需要目标 → target_index={ti}（最低血敌人）")
    params = {}
    if card is not None:
        params["card_index"] = card
    if target is not None:
        params["target_index"] = target
    if option is not None:
        params["option_index"] = option
    resp = None
    last_err = None
    for attempt in range(2):
        try:
            resp = act(action, **params)
            last_err = None
            break
        except Exception as e:
            last_err = str(e)
            healed = False
            if action in ("play_card", "use_potion") and "invalid_target" in last_err and target is None:
                st = get_state()
                ti = _auto_target_index(st)
                if ti is not None:
                    print(f"AUTO_TARGET 需要目标 → 自动 target_index={ti}（最低血敌人）重试")
                    params["target_index"] = ti
                    target = ti
                    healed = True
            elif action == "play_card" and "invalid_action" in last_err and attempt == 0:
                time.sleep(0.6)
                st = get_state()
                if "play_card" in (st.get("available_actions") or []):
                    print("AUTO_RETRY play_card 失败 → 重读状态后重试一次")
                    healed = True
            if not healed or attempt == 1:
                break
    if resp is None:
        print(f"ERROR {last_err}")
        if action == "play_card":
            print("HINT 0 费牌没打出去=白丢伤害/格挡；检查 --target / 费用 / 目标是否合法，换一张再试。")
        sys.exit(2)
    if action == "use_potion":
        time.sleep(0.4)
        st1 = get_state()
        p1 = potion_at(st1, _int(option, 0))
        still = bool(p1 and p1.get("potion_id"))
        queued = bool(p1 and p1.get("is_queued"))
        if still and not queued:
            print("POTION_PENDING 药水还在槽里且没排队：重读后再试 1 次（需要目标就自动选最低血）")
            try:
                params2 = dict(params)
                if p1.get("requires_target") and "target_index" not in params2:
                    ti = _auto_target_index(st1)
                    if ti is not None:
                        params2["target_index"] = ti
                resp = act("use_potion", **params2)
                print(f"POTION_RETRY -> {resp.get('status')} {resp.get('message')}")
            except Exception as e:
                print(f"POTION_RETRY_FAILED {e}（按没生效重算，别把药水算进计划）")
    after = wait_stable_player_state()
    # 结束回合后的快照可能还没抽牌；等一下再抓，并把「手牌为空但还能出牌」的过渡态标出来。
    if action == "end_turn" and after.get("screen") == "COMBAT":
        prev_len = len(((before.get("combat") or {}).get("hand") or []))
        ready = wait_new_turn_hand(prev_len)
        if ready is not None:
            after = ready
        hand_now = ((after.get("combat") or {}).get("hand") or [])
        acts_now = after.get("available_actions") or []
        if not hand_now and "play_card" in acts_now:
            print(
                "WARNING HAND_STALE 手牌是空的但 play_card 合法：这是抽牌动画中的过渡快照，"
                "先 `python sts2.py state` 重读再决策（别拿这张快照当回合开始）。"
            )
    if action == "play_card" and before.get("screen") == "COMBAT" and after.get("screen") == "COMBAT":
        bh = len(((before.get("combat") or {}).get("hand") or []))
        ah = len(((after.get("combat") or {}).get("hand") or []))
        be = _int(((before.get("combat") or {}).get("player") or {}).get("energy"), 0)
        ae = _int(((after.get("combat") or {}).get("player") or {}).get("energy"), 0)
        if ah == 0 and bh > 1 and ae >= be:
            print("WARNING suspicious post-card state (hand emptied but energy did not drop); re-read before ending turn.")
    tag = tag_for(after.get("screen", ""))
    overlay_say(tag, thought, meme, after)
    log(action, params, before, after, resp or {}, thought, meme)
    if action == "select_deck_card" and after.get("screen") == "CARD_SELECTION":
        cnt, mn, mx, _in = _sel_progress(after)
        if mx > 0 and cnt < mn:
            print(
                f"MULTI_SELECT 已选 {cnt}/{mn}：还要再点 {mn - cnt} 张（同一张再点会取消）。"
                f"下次可一次点完：do select_deck_card --options 0,2"
            )
    in_combat_play = (
        not full
        and action in ("play_card", "use_potion")
        and before.get("screen") == "COMBAT"
        and after.get("screen") == "COMBAT"
    )
    if in_combat_play:
        print(_delta_line(before, after))
        print(compact(after, brief=True))
    else:
        anchor = combat_anchor(after)
        if anchor:
            print(anchor)
        print(compact(after))
    print(f"ACTION {action} {params} -> {resp.get('status')} {resp.get('message')}")
    _meme_hint()


def _player_turn_state(timeout=8.0):
    """等到「玩家回合」快照再返回；超时返回 None（绝不盲发动作）。"""
    end = time.time() + timeout
    while True:
        s = get_state()
        acts = s.get("available_actions") or []
        if s.get("screen") == "COMBAT" and ("play_card" in acts or "end_turn" in acts):
            return s
        if time.time() >= end:
            return None
        time.sleep(0.15)


def _delta_line(before: dict, after: dict) -> str:
    """一行战况增量，替代整份 state（batch 中间步只打这个）。"""
    def _c(s):
        return s.get("combat") or {}
    b, a = _c(before), _c(after)
    bp, ap = b.get("player") or {}, a.get("player") or {}
    br, ar = before.get("run") or {}, after.get("run") or {}
    bits = [
        f"E{bp.get('energy', '?')}→{ap.get('energy', '?')}",
        f"blk{ap.get('block', 0)}",
        f"hp{ar.get('current_hp', '?')}",
    ]
    ben = {str(e.get("enemy_id")) + str(i): e for i, e in enumerate(b.get("enemies") or [])}
    aen = {str(e.get("enemy_id")) + str(i): e for i, e in enumerate(a.get("enemies") or [])}
    dead = [k for k in ben if k not in aen]
    if dead:
        bits.append("dead:" + ",".join(str(v.get("enemy_id")) for v in (ben[k] for k in dead)))
    hps = " ".join(
        f"e{i}:{e.get('hp')}" for i, e in enumerate(a.get("enemies") or [])
    )
    if hps:
        bits.append(hps)
    bits.append(f"hand{len(a.get('hand') or [])}")
    return " · ".join(bits)


def cmd_batch(spec: str):
    items = json.loads(spec)
    if isinstance(items, dict):
        items = items.get("actions", [])
    for i, it in enumerate(items):
        a = it.get("action")
        if not a:
            print(f"batch item {i} missing action")
            sys.exit(2)
        if i > 0:
            st = _player_turn_state()
            if st is None:
                print(f"BATCH STOP at {i} {a}: 没有玩家回合快照（战斗结束/进了选牌/敌人回合）")
                print(compact(get_state()))
                sys.exit(3)
        before = get_state()
        params = {k: it[k] for k in ("card_index", "target_index", "option_index") if k in it}
        # 允许按 card_id 指定，客户端每步按当前手牌重新解析下标（手牌会压缩）。
        cid = it.get("card_id")
        if cid and "card_index" not in params:
            hand = (before.get("combat") or {}).get("hand") or []
            idx = next(
                (k for k, c in enumerate(hand)
                 if isinstance(c, dict) and str(c.get("card_id")) == str(cid)
                 and c.get("playable", True) is not False),
                None,
            )
            if idx is None:
                print(f"BATCH STOP at {i}: 手里没有可打的 {cid}（牌序已变）")
                print(compact(before))
                sys.exit(3)
            params["card_index"] = idx
        thought = it.get("thought", "")
        meme = it.get("meme", "")
        try:
            resp = act(a, **params)
        except Exception as e:
            print(f"BATCH STOP at {i} {a}: {e}")
            sys.exit(2)
        after = resp.get("state") or get_state()
        overlay_say(tag_for(after.get("screen", "")), thought, meme, after)
        log(a, params, before, after, resp or {}, thought, meme)
        print(f"[{i}] {a} {params.get('card_index', params)} -> {resp.get('status')} | {_delta_line(before, after)}")
    print(compact(get_state(), brief=True))


def cmd_wait(screen=None, player_turn=False, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        s = get_state()
        if screen and s.get("screen") != screen:
            time.sleep(0.25)
            continue
        if player_turn:
            acts = s.get("available_actions") or []
            if "play_card" not in acts and "end_turn" not in acts:
                # 选牌/事件/商店不是「战斗回合」，等下去只会白等 30 秒。
                if s.get("screen") != "COMBAT":
                    print(
                        f"WAIT ABORT --player-turn 不适用：当前 screen={s.get('screen')}"
                        f"（不是战斗回合；多选界面看 SELECTION_STATE，或直接 do <action>）"
                    )
                    print(compact(s))
                    return
                time.sleep(0.25)
                continue
        print(compact(s))
        return
    print("WAIT TIMEOUT")
    print(compact(get_state()))
    sys.exit(3)


def cmd_say(thought, meme):
    s = get_state()
    overlay_say(tag_for(s.get("screen", "")), thought, meme, s)
    print("ok")
    _meme_hint()


def cmd_report():
    path = _session_path()
    out = os.path.splitext(path)[0] + ".md"
    entries = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    lines = ["# 杀戮尖塔 2 · AI 实况战报", ""]
    lines.append(f"- 录像会话：`{path}`")
    lines.append(f"- 动作数：{len(entries)}")
    lines.append("")
    for e in entries:
        lines.append(
            f"## {e.get('ts')} · 第{e.get('floor') if e.get('floor') is not None else '?'}层 · {e.get('screen_before')} → {e.get('screen_after')}"
        )
        lines.append(f"- 动作：`{e.get('action')}` {e.get('params')} → {e.get('status')}")
        if e.get("thought"):
            lines.append(f"- 思路：{_clean_speech(e.get('thought'))}")
        if e.get("meme"):
            lines.append(f"- 吐槽：{_clean_speech(e.get('meme'))}")
        lines.append("")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(out)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("state")
    p.add_argument("--raw", action="store_true")
    p.add_argument("--full", action="store_true", help="忽略差异缓存，打印完整药水/遗物/卡牌说明")
    sub.add_parser("acts")
    sub.add_parser("map")
    p = sub.add_parser("route")
    p.add_argument("--commit", type=int, help="把 opt<i> 记为当前路线计划（之后 plan 会拿它和状态对比）")
    p.add_argument("--note", default="", help="计划备注，例如「走商店线，Boss 前留营火」")
    p = sub.add_parser("plan")
    p.add_argument("--clear", action="store_true", help="清空已记录的计划（换幕/改线时用）")
    p.add_argument("--note", default="")
    p = sub.add_parser("play")
    p.add_argument("card_id")
    p.add_argument("--target", type=int)
    p.add_argument("--card", type=int, help="force hand index when several copies exist")
    p.add_argument("--thought", default="")
    p.add_argument("--meme", default="")
    p.add_argument("--full", action="store_true", help="出牌后仍打完整 state（默认只打一行增量 + 手牌/敌人）")
    p = sub.add_parser("do")
    p.add_argument("action")
    p.add_argument("--card", type=int)
    p.add_argument("--target", type=int)
    p.add_argument("--option", type=int)
    p.add_argument("--options", help="多选界面一次点完：逗号分隔下标，如 --options 0,3,5（自动确认）")
    p.add_argument("--thought", default="")
    p.add_argument("--meme", default="")
    p.add_argument("--full", action="store_true", help="动作后打完整 state")
    p = sub.add_parser("batch")
    p.add_argument("spec")
    p = sub.add_parser("wait")
    p.add_argument("--screen")
    p.add_argument("--player-turn", action="store_true")
    p.add_argument("--timeout", type=int, default=30)
    p = sub.add_parser("say")
    p.add_argument("--thought", default="")
    p.add_argument("--meme", default="")
    sub.add_parser("report")
    args = ap.parse_args()
    if args.cmd == "state":
        cmd_state(args.raw, args.full)
    elif args.cmd == "acts":
        cmd_acts()
    elif args.cmd == "map":
        cmd_map()
    elif args.cmd == "route":
        cmd_route(args.commit, args.note)
    elif args.cmd == "plan":
        cmd_plan(args.clear, args.note)
    elif args.cmd == "play":
        cmd_play(args.card_id, args.target, args.thought, args.meme, args.card, args.full)
    elif args.cmd == "do":
        options = None
        if getattr(args, "options", None):
            options = [int(x) for x in str(args.options).replace(" ", "").split(",") if x != ""]
        cmd_do(args.action, args.card, args.target, args.option, args.thought, args.meme, options,
               getattr(args, "full", False))
    elif args.cmd == "batch":
        cmd_batch(args.spec)
    elif args.cmd == "wait":
        cmd_wait(args.screen, args.player_turn, args.timeout)
    elif args.cmd == "say":
        cmd_say(args.thought, args.meme)
    elif args.cmd == "report":
        cmd_report()


if __name__ == "__main__":
    main()
