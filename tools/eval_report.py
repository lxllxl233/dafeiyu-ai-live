# -*- coding: utf-8 -*-
"""P3-1 可复现评估：从终局 summary / 决策账本算 A10 指标。

只读 `logs/`，不连游戏、不猜胜率：

- `run_summaries.jsonl`（或 `run_summary_*.json`）：每局胜负、楼层、牌组、掉血；
- `decisions.jsonl`：执行结果、被拒、重定向、药水、抽牌边界拦截。

没有 finalized summary 时明确输出 `NO_FINAL_SUMMARIES`，不把“没有样本”写成 0% 胜率。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
from collections import Counter
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REJECT_OUTCOMES = {
    "rejected_validate", "rejected_fx", "batch_error", "stale_end",
    "missing_seq", "not_combat", "no_actions",
}
WIN_RESULTS = {"victory", "win"}
LOSS_RESULTS = {"defeat", "loss"}


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _read_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    x = json.loads(line)
                except Exception:
                    continue
                if isinstance(x, dict):
                    rows.append(x)
    except OSError:
        pass
    return rows


def _read_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            x = json.load(f)
        return x if isinstance(x, dict) else None
    except (OSError, ValueError):
        return None


def load_summaries(log_dir):
    """优先 JSONL；没有就扫单局文件。按 run_id 去重，后写覆盖先写。"""
    rows = _read_jsonl(os.path.join(log_dir, "run_summaries.jsonl"))
    if not rows:
        for p in sorted(glob.glob(os.path.join(log_dir, "run_summary_*.json"))):
            x = _read_json(p)
            if x:
                rows.append(x)
    if not rows:
        x = _read_json(os.path.join(log_dir, "run_summary.json"))
        if x:
            rows.append(x)
    by_id = {}
    order = []
    for i, r in enumerate(rows):
        rid = str(r.get("run_id") or r.get("id") or f"_row{i}")
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = r
    out = [by_id[r] for r in order]
    out.sort(key=lambda r: (str(r.get("ts") or ""), str(r.get("run_id") or "")))
    return out


def _result(r):
    x = str(r.get("result") or "").lower()
    if x in WIN_RESULTS:
        return "win"
    if x in LOSS_RESULTS:
        return "loss"
    v = r.get("is_victory")
    if v is True:
        return "win"
    if v is False:
        return "loss"
    return "unknown"


def _is_a10(r):
    return _int(r.get("ascension"), -1) == 10


def _median(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 2) if vals else None


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def aggregate_runs(rows):
    wins = sum(1 for r in rows if _result(r) == "win")
    losses = sum(1 for r in rows if _result(r) == "loss")
    unknown = len(rows) - wins - losses
    known = wins + losses
    floors = [_int(r.get("floor")) for r in rows]
    decks = [_int(r.get("deck_size")) for r in rows]
    golds = [_int(r.get("gold")) for r in rows]
    hps = [_int(r.get("hp")) for r in rows]
    hp_pcts = []
    for r in rows:
        hp, mhp = _int(r.get("hp")), _int(r.get("max_hp"))
        if hp is not None and mhp and mhp > 0:
            hp_pcts.append(round(100.0 * hp / mhp, 1))
    return {
        "runs": len(rows),
        "known": known,
        "wins": wins,
        "losses": losses,
        "unknown": unknown,
        "win_rate": round(wins / known, 4) if known else None,
        "avg_floor": _mean(floors),
        "median_floor": _median(floors),
        "best_floor": max([x for x in floors if x is not None], default=None),
        "avg_deck": _mean(decks),
        "avg_gold": _mean(golds),
        "avg_hp": _mean(hps),
        "avg_hp_pct": _mean(hp_pcts),
        "hp_lost": sum(_int(r.get("hp_lost"), 0) for r in rows),
        "rejected": sum(_int(r.get("rejected"), 0) for r in rows),
        "potions_used": sum(_int(r.get("potions_used"), 0) for r in rows),
        "sl": sum(_int(r.get("sl"), 0) for r in rows),
    }


def _seq_potions(rec):
    n = 0
    for s in ((rec.get("input") or {}).get("seq") or []):
        if isinstance(s, dict) and "p" in s:
            n += 1
    return n


def aggregate_decisions(rows):
    by_outcome = Counter(str(r.get("outcome") or "?") for r in rows)
    executed = by_outcome.get("executed", 0)
    rejected = sum(by_outcome.get(k, 0) for k in REJECT_OUTCOMES)
    draw_rejects = sum(1 for r in rows
                       if str(r.get("outcome")) in REJECT_OUTCOMES
                       and "抽牌边界" in str(r.get("err") or ""))
    retargets = sum(len(r.get("retargets") or []) for r in rows)
    potions = sum(_seq_potions(r) for r in rows)
    mid = sum(1 for r in rows if r.get("mid"))
    return {
        "decisions": len(rows),
        "executed": executed,
        "rejected": rejected,
        "reject_rate": round(rejected / len(rows), 4) if rows else None,
        "draw_boundary_rejects": draw_rejects,
        "retargets": retargets,
        "potion_uses": potions,
        "mid_decisions": mid,
        "by_outcome": dict(sorted(by_outcome.items())),
    }


def build_report(log_dir=None, last=0):
    log_dir = os.path.abspath(log_dir or os.path.join(ROOT, "logs"))
    summaries = load_summaries(log_dir)
    if last and last > 0:
        summaries = summaries[-last:]
    decisions = _read_jsonl(os.path.join(log_dir, "decisions.jsonl"))
    if last and last > 0 and summaries:
        ids = {str(r.get("run_id") or r.get("id")) for r in summaries}
        decisions = [r for r in decisions if str(r.get("run_id")) in ids]
    a10 = [r for r in summaries if _is_a10(r)]
    report = {
        "v": 1,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "dir": log_dir,
            "summaries": "run_summaries.jsonl/run_summary_*.json",
            "decisions": "decisions.jsonl",
            "summary_rows": len(summaries),
            "decision_rows": len(decisions),
        },
        "all": aggregate_runs(summaries),
        "a10": aggregate_runs(a10),
        "combat": aggregate_decisions(decisions),
        "warnings": [],
        "runs": [{
            "run_id": r.get("run_id"),
            "result": _result(r),
            "ascension": _int(r.get("ascension")),
            "floor": _int(r.get("floor")),
            "hp": _int(r.get("hp")),
            "max_hp": _int(r.get("max_hp")),
            "deck_size": _int(r.get("deck_size")),
            "gold": _int(r.get("gold")),
            "rejected": _int(r.get("rejected"), 0),
            "hp_lost": _int(r.get("hp_lost"), 0),
            "potions_used": _int(r.get("potions_used"), 0),
        } for r in summaries],
    }
    if not summaries:
        report["warnings"].append("NO_FINAL_SUMMARIES：没有 finalized 单局 summary，不能计算胜率")
    elif report["all"]["known"] == 0:
        report["warnings"].append("NO_KNOWN_RESULTS：有 summary 但没有 victory/defeat")
    if summaries and not a10:
        report["warnings"].append("NO_A10_SAMPLES：没有 ascension=10 的终局样本")
    return report


def _pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def _val(v):
    return "—" if v is None else str(v)


def render_markdown(r):
    a = r["all"]
    t = r["a10"]
    c = r["combat"]
    lines = [
        "# A10 决策评估报告",
        "",
        f"生成：`{r['generated']}`　样本目录：`{r['source']['dir']}`",
        "",
        "## 总体",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| 终局局数 | {a['runs']} |",
        f"| 已知胜负 | {a['known']}（胜 {a['wins']} / 负 {a['losses']} / 未知 {a['unknown']}） |",
        f"| 胜率 | **{_pct(a['win_rate'])}** |",
        f"| 平均/中位楼层 | {_val(a['avg_floor'])} / {_val(a['median_floor'])} |",
        f"| 最远楼层 | {_val(a['best_floor'])} |",
        f"| 平均牌组/金币 | {_val(a['avg_deck'])} / {_val(a['avg_gold'])} |",
        f"| 总掉血 / 药水 | {a['hp_lost']} / {a['potions_used']} |",
        f"| 账本被拒 / SL | {a['rejected']} / {a['sl']} |",
        "",
        "## A10",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| 终局局数 | {t['runs']} |",
        f"| 已知胜负 | {t['known']}（胜 {t['wins']} / 负 {t['losses']}） |",
        f"| A10 胜率 | **{_pct(t['win_rate'])}** |",
        f"| 平均/中位楼层 | {_val(t['avg_floor'])} / {_val(t['median_floor'])} |",
        f"| 最远楼层 | {_val(t['best_floor'])} |",
        f"| 总掉血 / 药水 | {t['hp_lost']} / {t['potions_used']} |",
        "",
        "## 战斗执行",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| 决策条数 | {c['decisions']} |",
        f"| 执行/被拒 | {c['executed']} / {c['rejected']}（{_pct(c['reject_rate'])}） |",
        f"| 抽牌边界拦截 | {c['draw_boundary_rejects']} |",
        f"| 目标重定向 | {c['retargets']} |",
        f"| 药水使用 | {c['potion_uses']} |",
        f"| `mid` 分两步 | {c['mid_decisions']} |",
        "",
    ]
    if r["warnings"]:
        lines += ["## 警告", ""] + [f"- {w}" for w in r["warnings"]] + [""]
    lines += [
        "## 口径",
        "",
        "- 胜率只除已知胜负；`unknown` 不塞进分母。",
        "- 没有 finalized summary 时输出 `NO_FINAL_SUMMARIES`，不写 0%。",
        "- 战斗执行来自 `decisions.jsonl`；掉血/药水/牌组以 `run_summary` 为准。",
        "- 这是离线账本，不是实机承诺；样本不足时只看趋势，不宣称提升。",
    ]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="从决策账本生成可复现评估")
    ap.add_argument("--dir", default=os.path.join(ROOT, "logs"), help="日志目录")
    ap.add_argument("--last", type=int, default=0, help="只统计最近 N 局")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--md-out", help="同时写 Markdown 到文件")
    ap.add_argument("--require-samples", type=int, default=0,
                    help="已知胜负少于 N 时退出 2（CI 用）")
    args = ap.parse_args()

    r = build_report(args.dir, last=args.last)
    md = render_markdown(r)
    if args.md_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.md_out)), exist_ok=True)
        with open(args.md_out, "w", encoding="utf-8") as f:
            f.write(md)
    print(json.dumps(r, ensure_ascii=False, indent=2) if args.json else md, end="")
    if args.require_samples and r["all"]["known"] < args.require_samples:
        print(f"\n样本不足：已知胜负 {r['all']['known']} < {args.require_samples}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
