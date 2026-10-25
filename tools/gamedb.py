#!/usr/bin/env python3
"""Query the local StS2 reference DB."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = str(Path(__file__).resolve().parent.parent / "db" / "sts2.db")


def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def rows(sql, args=()):
    with con() as c:
        return [dict(r) for r in c.execute(sql, args)]


def show_card(r):
    print(f"{r['id']} | {r['name_en']} / {r['name_zh']} | {r['cost']}费 {r['type']} {r['rarity']}")
    if r.get("desc_en"):
        print("EN:", r["desc_en"])
    if r.get("desc_zh"):
        print("ZH:", r["desc_zh"])
    if r.get("desc_up_zh"):
        print("ZH+:", r["desc_up_zh"])


def show_enemy(r):
    print(f"{r['id']} | {r['name_en']} / {r['name_zh']} | {r['type']} | HP {r['hp_min']}-{r['hp_max']}")
    powers = json.loads(r.get("powers_json") or "[]")
    for p in powers:
        print(f"  POWER {p.get('key')}: {p.get('titleEn')} / {p.get('titleZh')} - {p.get('descriptionEn')} / {p.get('descriptionZh')}")
    moves = json.loads(r.get("moves_json") or "[]")
    for m in moves:
        intents = ",".join(i.get("key", "") for i in (m.get("intents") or []))
        print(f"  MOVE {m.get('nameEn')} / {m.get('nameZh')} [{intents}]")
    if r.get("notes"):
        print("NOTE:", r["notes"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["card", "enemy", "event", "relic", "potion", "summoners", "bosses", "search", "mechanics", "strategy", "stats"])
    ap.add_argument("query", nargs="?")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    q = args.query or ""

    if args.cmd == "card":
        rs = rows("SELECT * FROM cards WHERE id LIKE ? OR name_en LIKE ? OR name_zh LIKE ? ORDER BY id LIMIT 20",
                  (f"%{q}%", f"%{q}%", f"%{q}%"))
        if args.json:
            print(json.dumps(rs, ensure_ascii=False, indent=1))
        else:
            for r in rs:
                show_card(r)
                print()
    elif args.cmd == "enemy":
        rs = rows("SELECT * FROM enemies WHERE id LIKE ? OR name_en LIKE ? OR name_zh LIKE ? ORDER BY id LIMIT 20",
                  (f"%{q}%", f"%{q}%", f"%{q}%"))
        if args.json:
            print(json.dumps(rs, ensure_ascii=False, indent=1))
        else:
            for r in rs:
                show_enemy(r)
                print()
    elif args.cmd == "event":
        rs = rows("SELECT * FROM events WHERE id LIKE ? OR name_en LIKE ? OR name_zh LIKE ? ORDER BY id LIMIT 20",
                  (f"%{q}%", f"%{q}%", f"%{q}%"))
        if args.json:
            print(json.dumps(rs, ensure_ascii=False, indent=1))
        else:
            for r in rs:
                print(f"{r['id']} | {r['name_en']} / {r['name_zh']} | act {r['act']}")
                print("EN:", (r.get("desc_en") or "")[:400].replace("\n", " "))
                print("ZH:", (r.get("desc_zh") or "")[:400].replace("\n", " "))
                print("OPTIONS:", (r.get("options_json") or "")[:800])
                print()
    elif args.cmd in ("relic", "potion"):
        table = "relics" if args.cmd == "relic" else "potions"
        rs = rows(f"SELECT * FROM {table} WHERE id LIKE ? OR name_en LIKE ? OR name_zh LIKE ? ORDER BY id LIMIT 30",
                  (f"%{q}%", f"%{q}%", f"%{q}%"))
        if args.json:
            print(json.dumps(rs, ensure_ascii=False, indent=1))
        else:
            for r in rs:
                print(f"{r['id']} | {r['name_en']} / {r['name_zh']} | {r['rarity']}")
                print("EN:", r.get("desc_en"))
                print("ZH:", r.get("desc_zh"))
                print()
    elif args.cmd == "summoners":
        rs = rows("SELECT * FROM enemies WHERE moves_json LIKE '%SUMMON%' OR powers_json LIKE '%MINION_POWER%' ORDER BY id")
        for r in rs:
            show_enemy(r)
            print()
    elif args.cmd == "bosses":
        rs = rows("SELECT * FROM enemies WHERE type IN ('Boss','Elite') ORDER BY type, id")
        for r in rs:
            print(f"{r['type']:5} {r['id']:24} {r['name_en']:28} {r['name_zh']:12} HP {r['hp_min']}-{r['hp_max']}")
    elif args.cmd == "mechanics":
        for r in rows("SELECT * FROM mechanics"):
            print(f"{r['key']} | {r['title']}\n  {r['note']}\n")
    elif args.cmd == "strategy":
        for r in rows("SELECT * FROM strategy_rules ORDER BY phase, priority, id"):
            print(f"[{r['phase']}] {r['title']}\n  {r['rule']}\n  WHY: {r['why']}\n  CARDS: {r['cards']}\n")
    elif args.cmd == "stats":
        for table in ("cards", "enemies", "relics", "potions", "events", "powers", "keywords", "mechanics", "memes", "strategy_rules"):
            print(table, rows(f"SELECT COUNT(*) c FROM {table}")[0]["c"])
    elif args.cmd == "search":
        for table, cols in [
            ("cards", "name_en,name_zh,desc_en,desc_zh"),
            ("enemies", "name_en,name_zh,notes"),
            ("relics", "name_en,name_zh,desc_en,desc_zh"),
            ("events", "name_en,name_zh,desc_en,desc_zh"),
        ]:
            where = " OR ".join(f"{c} LIKE ?" for c in cols.split(","))
            rs = rows(f"SELECT * FROM {table} WHERE {where} LIMIT 10", tuple(f"%{q}%" for _ in cols.split(",")))
            for r in rs:
                print(table, "|", r.get("id"), "|", r.get("name_en"), "/", r.get("name_zh"))


if __name__ == "__main__":
    main()
