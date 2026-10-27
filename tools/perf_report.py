# -*- coding: utf-8 -*-
"""把 `logs/perf.jsonl` 聚成一张慢点表：按场景看 中位数/p90、等待占比、轮询次数。

用法：
    python tools/perf_report.py                 # 最近 200 条
    python tools/perf_report.py --n 60
    python tools/perf_report.py --trace act     # 只看一次 act 调用的阶段分解
    python tools/perf_report.py --tail 5        # 打印最近 5 条原始 trace 的阶段表
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "perf.jsonl")


def load(n: int = 200) -> list[dict]:
    if not os.path.exists(LOG):
        return []
    rows = []
    with open(LOG, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows[-n:]


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((len(xs) - 1) * q))))
    return xs[k]


def summarize(rows: list[dict]) -> None:
    print(f"perf.jsonl 共 {len(rows)} 条 trace\n")
    print(f"{'场景':<22}{'n':>4}{'中位ms':>9}{'p90ms':>9}{'最长ms':>9}"
          f"{'sleep占比':>11}{'状态读':>7}{'动作':>6}{'轮询':>6}")
    print("-" * 92)
    by = defaultdict(list)
    for r in rows:
        by[r.get("trace", "?")].append(r)
    for name, rs in sorted(by.items(), key=lambda kv: -_pct([r["ms"] for r in kv[1]], 0.5)):
        ms = [r["ms"] for r in rs]
        sleep_ms = sum(s.get("sleep_ms", 0) for r in rs for s in r.get("stages") or [])
        polls = sum(s.get("polls", 0) for r in rs for s in r.get("stages") or [])
        reads = sum((r.get("counters") or {}).get("state_read", 0) for r in rs)
        acts = sum((r.get("counters") or {}).get("action_post", 0) for r in rs)
        ratio = (sleep_ms / sum(ms) * 100) if sum(ms) else 0
        print(f"{name:<22}{len(rs):>4}{_pct(ms, 0.5):>9.0f}{_pct(ms, 0.9):>9.0f}"
              f"{max(ms):>9.0f}{ratio:>10.0f}%{reads:>7}{acts:>6}{polls:>6}")

    # 阶段级：谁最贵
    st = defaultdict(lambda: [0.0, 0])
    for r in rows:
        for s in r.get("stages") or []:
            st[s["name"]][0] += s["ms"]
            st[s["name"]][1] += 1
    print("\n阶段总耗时（跨全部 trace）")
    print("-" * 60)
    for name, (tot, cnt) in sorted(st.items(), key=lambda kv: -kv[1][0])[:16]:
        print(f"{name:<28}{tot/1000:>9.2f}s{cnt:>6} 次  平均 {tot/cnt:>7.1f}ms")


def show_trace(rows: list[dict], tail: int) -> None:
    for r in rows[-tail:]:
        print(f"\n=== {r.get('trace')}  {r['ms']}ms  exit={r.get('exit')}  meta={r.get('meta')}")
        for s in r.get("stages") or []:
            print(f"   {s['name']:<26}{s['ms']:>9.1f}ms  sleep={s.get('sleep_ms', 0):>7.1f}"
                  f"  polls={s.get('polls', 0)}")
        if r.get("counters"):
            print(f"   counters={r['counters']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--tail", type=int, default=0)
    ap.add_argument("--trace", default=None)
    args = ap.parse_args()
    rows = load(args.n)
    if not rows:
        print("还没有 perf 数据（跑一次 tools/act.py 就会生成）")
        return
    summarize(rows)
    if args.tail:
        sel = [r for r in rows if not args.trace or r.get("trace") == args.trace]
        show_trace(sel, args.tail)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
