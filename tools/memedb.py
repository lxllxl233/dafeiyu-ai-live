#!/usr/bin/env python3
"""Query the local StS2 meme DB and suggest a situational quip."""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = str(Path(__file__).resolve().parent.parent / "db" / "sts2.db")

SITUATIONS = {
    "combat": ["combat", "ironclad", "card", "enemy", "self_deprecating"],
    "boss": ["boss", "elite", "a10", "hype", "damage"],
    "elite": ["elite", "a10", "hype", "combat"],
    "death": ["death", "restart", "self_deprecating"],
    "low_hp": ["low_hp", "death", "block"],
    "bad_draw": ["bad_draw", "rng", "self_deprecating"],
    "good_draw": ["good_draw", "rng", "hype"],
    "reward": ["card", "relic", "hype", "draft"],
    "shop": ["relic", "gold", "meta"],
    "map": ["elite", "meta", "death"],
    "event": ["meta", "funny", "rng"],
    "victory": ["victory", "hype"],
    "fish": ["fish", "meta"],
    # 社区口头禅：按 category 取，不按 tag
    "quips": [],
}


def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def rows(sql, args=()):
    with con() as c:
        return [dict(r) for r in c.execute(sql, args)]


def show(r):
    print(f"[{r['intensity']}] {r['id']} | {r['category']} | {r['text']}")
    print(f"  例：{r['example']}")
    print(f"  触发：{r['trigger']}  (冷却 {r['cooldown']} 分钟)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["stats", "list", "random", "suggest", "search", "show"])
    ap.add_argument("query", nargs="?")
    ap.add_argument("--category")
    ap.add_argument("--tag")
    ap.add_argument("--situation", choices=sorted(SITUATIONS))
    ap.add_argument("--max-intensity", type=int, default=2)
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--avoid", default="")
    ap.add_argument("--include-cross-gen", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "stats":
        print("memes", rows("SELECT COUNT(*) c FROM memes")[0]["c"])
        for r in rows("SELECT category, COUNT(*) c FROM memes GROUP BY category ORDER BY c DESC"):
            print(f"  {r['category']}: {r['c']}")
        for r in rows("SELECT intensity, COUNT(*) c FROM memes GROUP BY intensity ORDER BY intensity"):
            print(f"  intensity {r['intensity']}: {r['c']}")
        return

    if args.cmd == "list":
        sql = "SELECT * FROM memes WHERE 1=1"
        params = []
        if args.category:
            sql += " AND category LIKE ?"
            params.append(f"%{args.category}%")
        if args.tag:
            sql += " AND tags LIKE ?"
            params.append(f"%{args.tag}%")
        sql += " AND intensity <= ? ORDER BY category, intensity, id"
        params.append(args.max_intensity)
        rs = rows(sql, tuple(params))
        if args.json:
            print(json.dumps(rs, ensure_ascii=False, indent=1))
        else:
            for r in rs:
                show(r)
        return

    if args.cmd == "show":
        rs = rows("SELECT * FROM memes WHERE id=? OR text LIKE ?", (args.query, f"%{args.query}%"))
        for r in rs:
            show(r)
        return

    if args.cmd == "search":
        rs = rows("SELECT * FROM memes WHERE text LIKE ? OR example LIKE ? OR trigger LIKE ? OR tags LIKE ?",
                  (f"%{args.query}%",) * 4)
        for r in rs[:30]:
            show(r)
        return

    # random / suggest
    avoid = {x.strip() for x in args.avoid.split(",") if x.strip()}
    sql = "SELECT * FROM memes WHERE intensity <= ?"
    params = [args.max_intensity]
    if not args.include_cross_gen:
        sql += " AND game != 'sts1'"
    if args.tag:
        sql += " AND tags LIKE ?"
        params.append(f"%{args.tag}%")
    if args.situation:
        if args.situation == "quips":
            sql += " AND category = ?"
            params.append("社区口头禅")
        else:
            tags = SITUATIONS[args.situation]
            sql += " AND (" + " OR ".join("tags LIKE ?" for _ in tags) + ")"
            params.extend(f"%{t}%" for t in tags)
    rs = rows(sql, tuple(params))
    rs = [r for r in rs if r["id"] not in avoid]
    if not rs:
        print("NO_MEME_FIT")
        return
    random.shuffle(rs)
    rs = rs[: args.limit]
    if args.json:
        print(json.dumps(rs, ensure_ascii=False, indent=1))
    else:
        for r in rs:
            show(r)
            print()


if __name__ == "__main__":
    main()
