# -*- coding: utf-8 -*-
"""Compact ctx view to save tokens.

Usage:
  python tools/mini.py            # run ctx.py then print compact summary
  python tools/mini.py --stdin    # read act.py JSON from stdin
"""
import json
import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    if "--stdin" in sys.argv:
        raw = sys.stdin.read()
        try:
            obj = json.loads(raw)
        except Exception:
            print("BAD_JSON", raw[:200])
            sys.exit(1)
        if not obj.get("ok", True) and "ctx" not in obj:
            print("ERR", json.dumps(obj, ensure_ascii=False)[:300])
            sys.exit(0)
        return obj, obj.get("ctx") or {}
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "ctx.py"), "--out", os.path.join(ROOT, "tmp_ctx.json")],
                   capture_output=True, cwd=ROOT)
    with open(os.path.join(ROOT, "tmp_ctx.json"), encoding="utf-8") as f:
        return {}, json.load(f)


def main():
    top, c = load()
    if not c:
        print(json.dumps(top, ensure_ascii=False)[:400])
        return
    out = {"ok": top.get("ok"), "err": top.get("err"), "mid": top.get("mid"),
           "log": top.get("log"), "fx": top.get("fx"), "screen": c.get("screen")}
    run = c.get("run") or {}
    out["run"] = {"f": run.get("f"), "hp": run.get("hp"), "max": run.get("max"), "gold": run.get("gold"), "deck": run.get("deck")}
    me = c.get("me") or {}
    out["me"] = {"hp": me.get("hp"), "blk": me.get("blk"), "e": me.get("e"), "buffs": me.get("buffs")}
    if c.get("hand"):
        out["hand"] = [{"id": h.get("id"), "c": h.get("c"), "dmg": h.get("dmg"), "blk": h.get("blk"),
                        "hits": h.get("hits"), "q": h.get("q"), "en": h.get("en"), "dmg2": h.get("dmg2")}
                       for h in c["hand"]]
    if c.get("enemies"):
        out["enemies"] = [{"i": e.get("i"), "id": e.get("id"), "hp": e.get("hp"), "b": e.get("b"),
                           "it": e.get("it"), "nx": e.get("nx"), "b2": e.get("b2")} for e in c["enemies"]]
    if c.get("calc"):
        out["calc"] = c["calc"]
    if c.get("plans"):
        out["plans"] = [{"k": p.get("k"), "s": p.get("s"), "d": p.get("d"), "take": p.get("take"), "hp": p.get("hp"), "kill": p.get("kill")} for p in c["plans"][:2]]
    if c.get("warn"):
        out["warn"] = c["warn"]
    if c.get("potions"):
        out["potions"] = [{"i": p.get("i"), "id": p.get("id"), "use": p.get("use")} for p in c["potions"]]
    if c.get("choice"):
        ch = c["choice"]
        out["choice"] = {"kind": ch.get("kind"), "items": ch.get("items") or ch.get("opts") or ch.get("cards") or ch.get("relics")}
    print(json.dumps(out, ensure_ascii=False))


main()
