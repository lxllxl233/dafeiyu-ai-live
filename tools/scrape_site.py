#!/usr/bin/env python3
"""Scrape the public slaythespire2.gg wiki list pages into local JSON.

Respects robots.txt: only public pages, reference use, no /api/, low rate.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
PAGES = os.path.join(ROOT, "pages")
RAW = os.path.join(os.path.dirname(ROOT), "db", "raw")
os.makedirs(PAGES, exist_ok=True)
os.makedirs(RAW, exist_ok=True)

BASE = "https://slaythespire2.gg/zh/"
TARGETS = {
    "cards": "cards",
    "relics": "relics",
    "potions": "potions",
    "events": "events",
    "characters": "characters",
    "enchantments": "enchantments",
    "enemies": "enemies",
}

RSC_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)')


def fetch(slug: str) -> str:
    url = BASE + slug
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (local game reference)"}, timeout=60)
    r.raise_for_status()
    return r.text


def rsc_payload(html: str) -> str:
    chunks = []
    for m in RSC_RE.finditer(html):
        try:
            chunks.append(json.loads(m.group(1)))
        except Exception:
            continue
    return "".join(chunks)


def extract_array(payload: str, key: str):
    needle = f'"{key}":['
    start = payload.find(needle)
    while start != -1:
        start = payload.find("[", start)
        depth = 0
        instr = False
        esc = False
        end = None
        for i in range(start, len(payload)):
            ch = payload[i]
            if instr:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    instr = False
            else:
                if ch == '"':
                    instr = True
                elif ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
        if end:
            try:
                data = json.loads(payload[start:end])
            except Exception:
                data = None
            if isinstance(data, list) and data and isinstance(data[0], dict) and "id" in data[0]:
                return data, start, end
        start = payload.find(needle, start + 1)
    return None, -1, -1


def main():
    summary = {}
    for slug, key in TARGETS.items():
        print("fetch", slug, flush=True)
        try:
            html = fetch(slug)
            with open(os.path.join(PAGES, f"{slug}_zh.html"), "w", encoding="utf-8") as f:
                f.write(html)
            payload = rsc_payload(html)
            data, s, e = extract_array(payload, key)
            if not data:
                print("  no array", key, "payload", len(payload), flush=True)
                continue
            out = os.path.join(RAW, f"{key}.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            keys = sorted(data[0].keys())
            summary[key] = {"count": len(data), "bytes": os.path.getsize(out), "fields": keys}
            print(f"  {len(data)} items -> {out} fields={keys[:12]}", flush=True)
        except Exception as exc:
            print("  ERROR", exc, flush=True)
        time.sleep(1.2)

    with open(os.path.join(os.path.dirname(ROOT), "db", "raw", "_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
