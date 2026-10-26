# -*- coding: utf-8 -*-
"""离线审计：把历史决策按现在的护栏重放，看有多少手会被拦下。

只读 logs/decisions.jsonl（记录里有当时的完整 state 和 seq），不连游戏。

    python tools/audit_guards.py --last 200
    python tools/audit_guards.py --run-id J6RVMF9LSM
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("STS2_DECISION_LOG", "0")

import act  # noqa: E402
import sim  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(ROOT, "logs", "decisions.jsonl"))
    ap.add_argument("--last", type=int, default=200)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--show", type=int, default=6, help="打印多少条被拦的明细")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print("no ledger:", args.file)
        return
    recs = []
    with io.open(args.file, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                recs.append(json.loads(ln))
            except ValueError:
                continue
    if args.run_id:
        recs = [r for r in recs if str(r.get("run_id")) == args.run_id]
    recs = recs[-args.last:]

    total = turns = hit = 0
    kinds: dict[str, int] = {}
    samples = []
    for r in recs:
        st = r.get("state")
        inp = r.get("input") or {}
        seq = inp.get("seq") if isinstance(inp, dict) else None
        if not isinstance(st, dict) or st.get("screen") != "COMBAT" or not seq:
            continue
        total += 1
        ok, _err, sm, steps = sim.validate(st, seq)
        if not ok or not steps or steps[-1].get("k") != "end":
            continue
        turns += 1
        err, _meta = act._guards(st, sm, True, {})
        if not err:
            continue
        hit += 1
        kind = err.split("：")[0].replace("护栏·", "")
        kinds[kind] = kinds.get(kind, 0) + 1
        if len(samples) < args.show:
            samples.append((r.get("floor"), r.get("turn"), kind, err.split("。")[0][:150]))

    print(f"扫描 {total} 条带状态的决策，其中 {turns} 手是「结束回合」")
    print(f"按新护栏会被拦下：{hit} 手")
    for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print()
    for floor, turn, kind, msg in samples:
        print(f"- f{floor} T{turn} [{kind}] {msg}")


if __name__ == "__main__":
    main()
