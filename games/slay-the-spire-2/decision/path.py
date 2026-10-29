# -*- coding: utf-8 -*-
"""BRPS: Boss-Ready Path Survival.

Pure, offline route model.  It does not call the game and does not import
``sts2``; callers pass the state and the small DB dicts.  This keeps route
ranking testable and prevents a route bug from becoming a game-action bug.

The model is deliberately conservative:

* a path is projected node by node with current deck DPS/block;
* a combat node costs ``max(0, incoming - block) * turns``;
* campfires heal 30% max HP (only when below max);
* a boss gate compares projected kill turns with projected survivable turns;
* forced elite nodes (single-child chain) are marked, not silently ignored.

It is an estimate, not a combat simulator.  Unknown room/enemy data is marked
``assumed`` so a later ledger can judge the prediction instead of hiding it.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter

ROOM_DANGER = {"elite", "boss", "ancient"}
ROOM_COMBAT = {"monster", "elite", "boss", "ancient"}

GENERIC = {
    "Monster": {"hp": 48, "incoming": 10, "burst": 14},
    "Elite": {"hp": 120, "incoming": 17, "burst": 26},
    "Boss": {"hp": 199, "incoming": 20, "burst": 34},
}

# Game progression order.  The encounter DB labels the first three acts;
# Underdocks is the remaining act.  Unknown ids fall back to Act 1.
ACT_LABELS = ["Act 1 - Overgrowth", "Act 2 - Hive", "Act 3 - Glory", "Underdocks"]


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _key(n):
    return (_int(n.get("row"), -1), _int(n.get("col"), -1))


def _ntype(n):
    return str(n.get("node_type") or "").strip().lower()


def _children(m, n):
    nodes = (m or {}).get("nodes") or []
    by_pos = {_key(x): x for x in nodes if x.get("row") is not None}
    by_id = {x.get("id"): x for x in nodes if x.get("id")}
    out = []
    for ch in n.get("children") or []:
        if isinstance(ch, dict):
            node = by_pos.get((_int(ch.get("row"), -1), _int(ch.get("col"), -1)))
            if node is None:
                node = {
                    "row": ch.get("row"),
                    "col": ch.get("col"),
                    "node_type": ch.get("node_type") or ch.get("type"),
                }
        else:
            node = by_id.get(ch)
        if node is not None:
            out.append(node)
    return out


def map_signature(m: dict) -> str:
    """Stable map identity; used to invalidate a plan on reroll/new act."""
    nodes = (m or {}).get("nodes") or []
    raw = []
    for n in nodes:
        raw.append((
            _int(n.get("row"), -1),
            _int(n.get("col"), -1),
            str(n.get("node_type") or ""),
            tuple(sorted(_key(ch) for ch in _children(m, n))),
        ))
    raw.sort()
    h = hashlib.sha1(repr(raw).encode("utf-8")).hexdigest()
    return h[:16]


def act_label(act_id=None) -> str:
    """Map 0-based act_id (or a name) to an encounter DB act label."""
    if isinstance(act_id, str):
        s = act_id.strip()
        if s in ACT_LABELS:
            return s
        if s.lower().startswith("act "):
            for label in ACT_LABELS:
                if label.lower() == s.lower():
                    return label
        if s.isdigit():
            act_id = int(s)
        else:
            return ACT_LABELS[0]
    if isinstance(act_id, (int, float)):
        idx = int(act_id)
        if 0 <= idx < len(ACT_LABELS):
            return ACT_LABELS[idx]
    return ACT_LABELS[0]


def _act_index(state: dict):
    run = state.get("run") or {}
    if "act_id" in run and run.get("act_id") is not None:
        return run.get("act_id")
    raw = run.get("act")
    # Legacy/offline fixtures use 1-based ``act``; live state exposes
    # 0-based ``act_id``.  Treat a bare numeric act as 1-based.
    if isinstance(raw, (int, float)):
        return max(0, int(raw) - 1)
    return raw


def enumerate_paths(m: dict, start=None) -> list[list[dict]]:
    """All start->Boss/Ancient (or last-row) paths, deduped, capped."""
    nodes = (m or {}).get("nodes") or []
    rows = _int((m or {}).get("rows"), 16)
    if start is not None:
        starts = [start]
    else:
        starts = [n for n in nodes if n.get("is_available")]
        if not starts:
            cur = next((n for n in nodes if n.get("is_current")), None)
            starts = _children(m, cur) if cur else []
    paths = []
    seen = set()

    def dfs(n, path):
        if len(path) > 40 or len(paths) > 2000:
            return
        path = path + [n]
        typ = _ntype(n)
        if typ in ("boss", "ancient") or _int(n.get("row"), -1) >= rows:
            sig = tuple(_key(x) for x in path)
            if sig not in seen:
                seen.add(sig)
                paths.append(path)
            return
        for ch in _children(m, n):
            if ch in path:
                continue
            dfs(ch, path)

    for start_node in starts:
        dfs(start_node, [])
    return paths


def _card_meta(card, card_db):
    cid = str(card.get("card_id") or card.get("id") or "")
    return card_db.get(cid) or {}, cid


def deck_profile(deck, card_db=None, *, max_energy=3, potions=0, ascension=10) -> dict:
    """Estimate per-turn damage/block from the real deck, before combat buffs."""
    card_db = card_db or {}
    deck = [c for c in (deck or []) if isinstance(c, dict)]
    total_damage = total_block = total_cost = 0.0
    attacks = blocks = aoe = upgraded = dead = 0
    for c in deck:
        meta, cid = _card_meta(c, card_db)
        typ = str(c.get("card_type") or c.get("type") or meta.get("type") or "").lower()
        target = str(c.get("target") or meta.get("target") or "").lower()
        cost = c.get("energy_cost")
        if cost is None:
            cost = c.get("cost", meta.get("cost", 0))
        cost = _float(cost, 0.0)
        if str(c.get("type") or "").lower() in ("curse", "status") or "curse" in typ or "status" in typ:
            dead += 1
            continue
        if c.get("upgraded") or c.get("is_upgraded"):
            upgraded += 1
        if "attack" in typ:
            attacks += 1
            dmg = c.get("damage")
            if dmg is None:
                dmg = meta.get("damage")
            hits = _int(c.get("hit_count") or meta.get("hit_count"), 1)
            total_damage += max(0.0, _float(dmg, 0.0)) * max(1, hits)
        elif "skill" in typ:
            if "block" in typ or _float(meta.get("block"), 0) > 0:
                blocks += 1
            blk = c.get("block")
            if blk is None:
                blk = meta.get("block")
            total_block += max(0.0, _float(blk, 0.0))
        if "all" in target:
            aoe += 1
        if "power" in typ:
            # Powers are not direct damage/block; they are counted separately
            # so a power-heavy deck is not accidentally treated as a block deck.
            pass
        if _float(cost, 0) > 0:
            total_cost += _float(cost, 0)
    playable = max(1, len(deck) - dead)
    avg_cost = total_cost / playable if playable else 1.0
    cards_per_turn = min(5.0, max(1.0, max_energy / max(0.6, avg_cost)))
    attack_share = attacks / playable if playable else 0.0
    block_share = blocks / playable if playable else 0.0
    avg_attack = total_damage / attacks if attacks else 0.0
    avg_block = total_block / blocks if blocks else 0.0
    # A small uplift for upgrades/energy is intentionally capped: the route
    # model must not promise a boss kill that the current deck cannot deliver.
    quality = 1.0 + min(0.20, 0.02 * upgraded)
    dps = max(3.0, avg_attack * cards_per_turn * attack_share * quality)
    block_per_turn = max(0.0, avg_block * cards_per_turn * block_share * quality)
    return {
        "size": len(deck),
        "playable": playable,
        "dead": dead,
        "attacks": attacks,
        "blocks": blocks,
        "aoe": aoe,
        "upgraded": upgraded,
        "avg_cost": round(avg_cost, 2),
        "dps": round(dps, 1),
        "block": round(block_per_turn, 1),
        "potions": _int(potions, 0),
        "ascension": _int(ascension, 10),
    }


def _damage_values(monster: dict, ascension: int) -> list[float]:
    vals = []
    for v in (monster.get("damage_values") or {}).values():
        if not isinstance(v, dict):
            continue
        raw = v.get("ascension") if _int(ascension, 10) >= 10 else v.get("normal")
        if raw is None:
            raw = v.get("normal") or v.get("ascension")
        if raw is not None:
            vals.append(max(0.0, _float(raw, 0.0)))
    return vals


def monster_profile(monster: dict, ascension=10) -> dict:
    """Single-monster HP and damage band from the local DB."""
    if not monster:
        return {"hp": 48, "incoming": 10, "burst": 14, "assumed": True}
    hp = monster.get("min_hp_ascension") if _int(ascension, 10) >= 10 else monster.get("min_hp")
    if hp is None:
        hp = monster.get("min_hp") or monster.get("max_hp") or 48
    vals = _damage_values(monster, ascension)
    incoming = sum(vals) / len(vals) if vals else 10.0
    burst = max(vals) if vals else max(14.0, incoming)
    return {
        "id": monster.get("id"),
        "name": monster.get("name"),
        "hp": _int(hp, 48),
        "incoming": round(incoming, 1),
        "burst": round(burst, 1),
        "assumed": not bool(vals),
    }


def encounter_profile(enc: dict, monster_db: dict, ascension=10) -> dict:
    monsters = []
    for m in (enc or {}).get("monsters") or []:
        if isinstance(m, dict):
            mid = m.get("id")
        else:
            mid = str(m)
        monsters.append(monster_profile((monster_db or {}).get(mid) or {"id": mid}, ascension))
    if not monsters:
        return dict(GENERIC["Monster"], assumed=True)
    return {
        "id": enc.get("id"),
        "name": enc.get("name"),
        "hp": sum(m["hp"] for m in monsters),
        "incoming": round(sum(m["incoming"] for m in monsters), 1),
        "burst": round(sum(m["burst"] for m in monsters), 1),
        "monsters": monsters,
        "assumed": any(m.get("assumed") for m in monsters),
    }


def _act_encounters(encounters_db, act_id=None, room_type=None) -> list:
    label = act_label(act_id)
    rows = [e for e in (encounters_db or {}).values() if isinstance(e, dict)]
    label_rows = [e for e in rows if str(e.get("act") or "") == label]
    rows = label_rows or rows
    wanted = {"monster", "elite", "boss"} if room_type is None else {str(room_type).lower()}
    return [e for e in rows if str(e.get("room_type") or "").lower() in wanted]


def _room_profile(encounters_db, monster_db, act_id, room_type, ascension=10) -> dict:
    encs = _act_encounters(encounters_db, act_id, room_type)
    profiles = [encounter_profile(e, monster_db, ascension) for e in encs]
    profiles = [p for p in profiles if p.get("hp")]
    if not profiles:
        return dict(GENERIC.get(str(room_type).title(), GENERIC["Monster"]), assumed=True)
    # Average is the expected encounter.  The worst case is kept separately so
    # a warning can be emitted without making the whole route look doomed.
    avg = {
        "id": "avg:" + str(room_type),
        "name": f"{room_type} avg",
        "hp": round(sum(p["hp"] for p in profiles) / len(profiles)),
        "incoming": round(sum(p["incoming"] for p in profiles) / len(profiles), 1),
        "burst": round(max(p["burst"] for p in profiles), 1),
        "assumed": any(p.get("assumed") for p in profiles),
        "options": len(profiles),
    }
    return avg


def boss_options(encounters_db, monster_db, act_id=None, ascension=10) -> list:
    encs = _act_encounters(encounters_db, act_id, "boss")
    out = [encounter_profile(e, monster_db, ascension) for e in encs]
    out = [p for p in out if p.get("hp")]
    if not out:
        out = [dict(GENERIC["Boss"], id="generic-boss", name="generic boss", assumed=True)]
    return out


def _combat_loss(profile: dict, deck: dict, *, potion_block=0.0,
                 turn_cap=None) -> dict:
    hp = max(1, _int(profile.get("hp"), 1))
    dps = max(1.0, _float(deck.get("dps"), 1.0))
    ttk = max(1, int(math.ceil(hp / dps)))
    turns = min(ttk, max(1, _int(turn_cap, ttk))) if turn_cap else ttk
    block = _float(deck.get("block"), 0.0) + max(0.0, potion_block)
    net = max(0.0, _float(profile.get("incoming"), 0.0) - block)
    loss = int(round(net * turns))
    return {
        "ttk": ttk,
        "turns": turns,
        "loss": loss,
        "incoming": profile.get("incoming"),
        "block": round(block, 1),
        "net": round(net, 1),
        "assumed": bool(profile.get("assumed")),
        "capped": turns < ttk,
    }


def _children_count(m, n):
    return len(_children(m, n))


def _forced_nodes(m, path, options_count=1) -> list[dict]:
    """Mark dangerous nodes the chosen path cannot route around."""
    forced = []
    for i, n in enumerate(path):
        typ = _ntype(n)
        if typ not in ROOM_DANGER:
            continue
        prev = path[i - 1] if i else None
        # The first node is forced when it is the only available option.
        if prev is None:
            forced_by_choice = options_count <= 1
        else:
            forced_by_choice = _children_count(m, prev) <= 1
        # A boss is the act gate by definition; only elite gets a route warning.
        if typ == "elite" and forced_by_choice:
            forced.append({"row": _int(n.get("row"), -1), "col": _int(n.get("col"), -1),
                           "type": "Elite", "why": "single_child_chain"})
    return forced


def boss_gate(state: dict, card_db=None, monster_db=None, encounters_db=None,
              ascension=10, hp=None) -> dict:
    """Compare current deck kill turns against projected survivable turns."""
    run = state.get("run") or {}
    hp = _int(hp if hp is not None else run.get("current_hp", run.get("hp")), 0)
    deck = deck_profile(run.get("deck") or [], card_db, max_energy=_int(run.get("max_energy"), 3),
                        potions=len([p for p in (run.get("potions") or [])
                                     if isinstance(p, dict) and (p.get("occupied") or p.get("potion_id"))]),
                        ascension=ascension)
    opts = boss_options(encounters_db, monster_db, _act_index(state), ascension)
    potion_block = deck["potions"] * 8.0
    rows = []
    for p in opts:
        ttk = max(1, int(math.ceil(max(1, p["hp"]) / max(1.0, deck["dps"]))))
        net = max(0.0, _float(p.get("incoming"), 0.0) - deck["block"])
        survive = 999 if net <= 0 else int(math.floor((hp + potion_block) / net))
        rows.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "hp": p.get("hp"),
            "incoming": p.get("incoming"),
            "burst": p.get("burst"),
            "ttk": ttk,
            "survive": survive,
            "window": survive - ttk,
            "ok": survive >= ttk,
            "assumed": bool(p.get("assumed")),
        })
    worst = min(rows, key=lambda r: r["window"]) if rows else None
    expected = {
        "hp": round(sum(r["hp"] for r in rows) / len(rows)) if rows else None,
        "ttk": round(sum(r["ttk"] for r in rows) / len(rows)) if rows else None,
        "survive": min((r["survive"] for r in rows), default=None),
        "window": worst["window"] if worst else None,
    }
    return {
        "ok": all(r["ok"] for r in rows) if rows else False,
        "ok_count": sum(1 for r in rows if r["ok"]),
        "total": len(rows),
        "deck": deck,
        "hp": hp,
        "potion_block": potion_block,
        "options": rows,
        "worst": worst,
        "expected": expected,
        "act": act_label(_act_index(state)),
        "assumed": any(r.get("assumed") for r in rows),
    }


def elite_policy(act_id, deck: dict, potions: int, hp_ratio: float) -> tuple[bool, str]:
    """A10 路线里的精英闸：Act1 一律不碰；Act2+ 要卡组成型才允许「考虑」。

    A10 的精英（尤其 Act1）会直接打掉半条命和全部药水，开局路线必须绕开。
    卡组闸沿用战斗侧的精英四检：防御≥6 / AOE≥1 / 药水≥1；再叠血线 60%。
    返回 (allowed, why)，why 会写进路线计划和 ctx，模型/观众都能看到原因。
    """
    act = _int(act_id, 0)
    if act < 1:
        return False, "Act1 不碰精英（A10 开局硬规则）"
    d = deck or {}
    defense = _int(d.get("defense"), 0)
    aoe = _int(d.get("aoe"), 0)
    pots = _int(potions, 0)
    if defense < 6 or aoe < 1 or pots < 1:
        return False, f"卡组闸未过：防御{defense}/AOE{aoe}/药水{pots}"
    if hp_ratio < 0.60:
        return False, f"血 {hp_ratio:.0%} < 60%"
    return True, "Act2+ 卡组闸通过；精英只作备选，先看精英后到 Boss 的补给"


def _resource_score(path: list[dict]) -> float:
    c = Counter(_ntype(n) for n in path)
    sc = 4.0 * c["restsite"] + 2.0 * c["shop"] + 2.0 * c["treasure"]
    sc += 1.0 * c["event"] + 0.6 * c["unknown"]
    if c["restsite"] >= 3:
        sc += 1.5
    if any(_ntype(n) == "restsite" for n in path[-3:]):
        sc += 2.0
    if any(_ntype(n) == "shop" for n in path[-5:]):
        sc += 2.5
    if c["treasure"] >= 2:
        sc += 1.0
    return sc


def score_path(state: dict, m: dict, path: list[dict], card_db=None, monster_db=None,
               encounters_db=None, ascension=10, profiles=None) -> dict:
    """Project one path and return a transparent BRPS record."""
    run = state.get("run") or {}
    hp = _int(run.get("current_hp", run.get("hp")), 0)
    mhp = max(1, _int(run.get("max_hp"), hp or 1))
    deck = deck_profile(run.get("deck") or [], card_db,
                        max_energy=_int(run.get("max_energy"), 3),
                        potions=len([p for p in (run.get("potions") or [])
                                     if isinstance(p, dict) and (p.get("occupied") or p.get("potion_id"))]),
                        ascension=ascension)
    act_id = _act_index(state)
    profiles = profiles or {}
    normal = profiles.get("Monster") or _room_profile(encounters_db, monster_db, act_id, "Monster", ascension)
    elite = profiles.get("Elite") or _room_profile(encounters_db, monster_db, act_id, "Elite", ascension)
    options_count = len([n for n in (m.get("nodes") or []) if n.get("is_available")]) or 1
    forced = _forced_nodes(m, path, options_count)
    steps = []
    cur_hp = hp
    dead = False
    for n in path:
        typ = _ntype(n)
        row = _int(n.get("row"), -1)
        step = {"row": row, "col": _int(n.get("col"), -1), "type": n.get("node_type"),
                "hp_in": cur_hp, "loss": 0, "heal": 0, "ttk": None}
        if typ in ("boss", "ancient"):
            step["hp_out"] = cur_hp
            step["boss"] = True
            steps.append(step)
            break
        if typ == "monster":
            # Normal fights are short; without a cap, a high-HP multi-enemy
            # encounter would erase every path at 80 HP and make rank useless.
            info = _combat_loss(normal, deck, turn_cap=3)
            cur_hp -= info["loss"]
            step.update({"loss": info["loss"], "ttk": info["ttk"], "turns": info["turns"],
                         "profile": "Monster", "assumed": info["assumed"]})
        elif typ == "elite":
            info = _combat_loss(elite, deck, turn_cap=6)
            cur_hp -= info["loss"]
            step.update({"loss": info["loss"], "ttk": info["ttk"], "turns": info["turns"],
                         "profile": "Elite", "assumed": info["assumed"]})
        elif typ == "restsite":
            heal = min(mhp - cur_hp, int(round(0.30 * mhp))) if cur_hp < mhp else 0
            cur_hp += max(0, heal)
            step["heal"] = max(0, heal)
        elif typ == "unknown":
            # Unknown is often an event, but not always.  Charge half a normal
            # fight so a path of question marks is not treated as free healing.
            loss = int(round(_combat_loss(normal, deck, turn_cap=2)["loss"] * 0.5))
            cur_hp -= loss
            step["loss"] = loss
        step["hp_out"] = max(0, cur_hp)
        steps.append(step)
        if cur_hp <= 0:
            dead = True
            break
    hp_before_boss = max(0, cur_hp)
    gate = boss_gate(state, card_db, monster_db, encounters_db, ascension, hp=hp_before_boss)
    forced_elite = any(x["type"] == "Elite" for x in forced)
    hp_ratio = hp_before_boss / mhp if mhp else 0.0
    resource = _resource_score(path)
    window = gate.get("worst", {}).get("window") if gate.get("worst") else None
    window_score = 0.0 if window is None else max(-10.0, min(15.0, float(window) * 2.0))
    score = 55.0 * hp_ratio + resource + (15.0 if gate.get("ok") else 0.0) + window_score
    if dead:
        score -= 100.0
    if forced_elite:
        score -= 6.0
    return {
        "path": path,
        "seq": " ".join(_node_sym(n) for n in path),
        "steps": steps,
        "start": {"row": path[0].get("row"), "col": path[0].get("col")} if path else None,
        "hp_start": hp,
        "hp_after": hp_before_boss,
        "hp_pct": round(hp_ratio, 3),
        "dead": dead,
        "death_row": next((s["row"] for s in steps if s.get("hp_out") == 0), None) if dead else None,
        "forced": forced,
        "forced_elite": forced_elite,
        "resource": round(resource, 1),
        "boss_gate": gate,
        "score": round(score, 2),
        "deck": deck,
        "profile_source": {
            "monster": "avg" if normal.get("options") else ("generic" if normal.get("assumed") else "act"),
            "elite": "avg" if elite.get("options") else ("generic" if elite.get("assumed") else "act"),
        },    }


def _node_sym(n):
    sym = {"Monster": "M", "Elite": "E", "RestSite": "R", "Shop": "$",
           "Event": "?", "Treasure": "T", "Boss": "B", "Ancient": "A", "Unknown": "?"}
    return sym.get(str(n.get("node_type")), "?")


def score_paths(state: dict, m: dict, paths, card_db=None, monster_db=None,
                encounters_db=None, ascension=10, profiles=None) -> list[dict]:
    out = [score_path(state, m, p, card_db, monster_db, encounters_db,
                      ascension=ascension, profiles=profiles)
           for p in (paths or [])]
    # Dominators: an elite that every candidate path must cross is forced even
    # when the previous node has two children that converge on it.
    if len(out) > 1:
        common = set.intersection(*[
            {_key(n) for n in r["path"] if _ntype(n) == "elite"} for r in out
        ]) if out else set()
        for r in out:
            for n in r["path"]:
                if _ntype(n) != "elite" or _key(n) not in common:
                    continue
                already = any(x.get("row") == _int(n.get("row"), -1)
                              and x.get("col") == _int(n.get("col"), -1)
                              for x in r["forced"])
                if already:
                    continue
                r["forced"].append({"row": _int(n.get("row"), -1),
                                    "col": _int(n.get("col"), -1),
                                    "type": "Elite", "why": "dominates_all_paths"})
                r["forced_elite"] = True
                r["score"] = round(r["score"] - 6.0, 2)
    out.sort(key=lambda r: (not r["dead"], r["score"], r["hp_after"]), reverse=True)
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def gate_line(gate: dict) -> str:
    """One human-readable line for route/plan output."""
    if not gate:
        return ""
    w = gate.get("worst") or {}
    exp = gate.get("expected") or {}
    if gate.get("ok"):
        return (f"BOSS_TTK OK {gate.get('act')} 最差 {w.get('name')} "
                f"{w.get('ttk')} 回合 / 可活 {w.get('survive')} 回合"
                f"（窗口 +{w.get('window')}）")
    return (f"BOSS_TTK FAIL {gate.get('act')} 最差 {w.get('name')} "
            f"{w.get('ttk')} 回合 / 可活 {w.get('survive')} 回合"
            f"（窗口 {w.get('window')}，期望 TTK {exp.get('ttk')}）："
            f"当前牌组/血量还接不住 Boss")


def elite_gate(state: dict, card_db=None, monster_db=None, encounters_db=None,
               ascension=10) -> dict:
    run = state.get("run") or {}
    deck = deck_profile(run.get("deck") or [], card_db,
                        max_energy=_int(run.get("max_energy"), 3), ascension=ascension)
    p = _room_profile(encounters_db, monster_db, _act_index(state), "Elite", ascension)
    return _combat_loss(p, deck)
