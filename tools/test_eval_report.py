# -*- coding: utf-8 -*-
"""P3-1 评估报告测试：全部用临时日志，不碰实机。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import contextlib
import io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import eval_report  # noqa: E402
import test_all  # noqa: E402

OK = 0


def check(name, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"OK   {name}")
    else:
        print(f"FAIL {name} {detail}")
        raise SystemExit(1)


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def fixture(tmp):
    summaries = [
        {"run_id": "r1", "ts": "2026-01-01T00:00:01", "result": "victory",
         "ascension": 10, "floor": 50, "hp": 20, "max_hp": 80,
         "deck_size": 28, "gold": 30, "rejected": 1, "hp_lost": 10,
         "potions_used": 2},
        {"run_id": "r2", "ts": "2026-01-01T00:00:02", "result": "defeat",
         "ascension": 10, "floor": 16, "hp": 0, "max_hp": 80,
         "deck_size": 24, "gold": 5, "rejected": 2, "hp_lost": 80,
         "potions_used": 1},
        {"run_id": "r3", "ts": "2026-01-01T00:00:03", "result": "victory",
         "ascension": 0, "floor": 20, "hp": 30, "max_hp": 80,
         "deck_size": 22, "gold": 0, "rejected": 0, "hp_lost": 5,
         "potions_used": 0},
        {"run_id": "r1", "ts": "2026-01-01T00:00:04", "result": "victory",
         "ascension": 10, "floor": 51, "hp": 25, "max_hp": 80,
         "deck_size": 29, "gold": 40, "rejected": 1, "hp_lost": 9,
         "potions_used": 2},
    ]
    write_jsonl(os.path.join(tmp, "run_summaries.jsonl"), summaries)
    decisions = [
        {"run_id": "r1", "outcome": "executed", "mid": False,
         "input": {"seq": [{"c": 1}, {"p": "fire"}]},
         "retargets": [{"from": 0, "to": 1}]},
        {"run_id": "r1", "outcome": "rejected_validate", "mid": False,
         "input": {"seq": []}, "err": "抽牌边界：抽到未知牌", "retargets": []},
        {"run_id": "r2", "outcome": "batch_error", "mid": True,
         "input": {"seq": [{"c": 1}]}, "retargets": []},
        {"run_id": "r2", "outcome": "executed", "mid": True,
         "input": {"seq": [{"c": 2}]}, "retargets": []},
    ]
    write_jsonl(os.path.join(tmp, "decisions.jsonl"), decisions)


def test_full():
    with tempfile.TemporaryDirectory() as tmp:
        fixture(tmp)
        r = eval_report.build_report(tmp)
        a = r["all"]
        check("run rows", a["runs"] == 3, str(a))
        check("dedupe latest r1", next(x for x in r["runs"] if x["run_id"] == "r1")["floor"] == 51,
              str(r["runs"]))
        check("known wins/losses", (a["known"], a["wins"], a["losses"]) == (3, 2, 1), str(a))
        check("win rate", a["win_rate"] == 0.6667, str(a["win_rate"]))
        check("a10 samples", r["a10"]["runs"] == 2, str(r["a10"]))
        check("a10 win rate", r["a10"]["win_rate"] == 0.5, str(r["a10"]["win_rate"]))
        check("avg floor", a["avg_floor"] == 29.0, str(a["avg_floor"]))
        check("median floor", a["median_floor"] == 20, str(a["median_floor"]))
        check("best floor", a["best_floor"] == 51, str(a["best_floor"]))
        check("summary hp lost", a["hp_lost"] == 94, str(a["hp_lost"]))
        check("summary rejected", a["rejected"] == 3, str(a["rejected"]))
        check("summary potions", a["potions_used"] == 3, str(a["potions_used"]))
        c = r["combat"]
        check("decision executed", c["executed"] == 2, str(c))
        check("decision rejected", c["rejected"] == 2, str(c))
        check("reject rate", c["reject_rate"] == 0.5, str(c["reject_rate"]))
        check("draw boundary", c["draw_boundary_rejects"] == 1, str(c))
        check("retargets", c["retargets"] == 1, str(c["retargets"]))
        check("potion uses", c["potion_uses"] == 1, str(c["potion_uses"]))
        check("mid decisions", c["mid_decisions"] == 2, str(c["mid_decisions"]))
        md = eval_report.render_markdown(r)
        check("md has win rate", "66.7%" in md, md[:300])
        check("md has a10", "A10 胜率" in md, md[:300])
        check("md no warning", "## 警告" not in md, md[:300])


def test_last_and_empty():
    with tempfile.TemporaryDirectory() as tmp:
        fixture(tmp)
        r = eval_report.build_report(tmp, last=1)
        check("last limits runs", r["all"]["runs"] == 1, str(r["all"]))
        check("last uses latest", r["runs"][0]["run_id"] == "r1", str(r["runs"]))
        check("last filters decisions", r["combat"]["decisions"] == 2, str(r["combat"]))
    with tempfile.TemporaryDirectory() as tmp:
        r = eval_report.build_report(tmp)
        check("empty runs", r["all"]["runs"] == 0, str(r["all"]))
        check("empty win rate None", r["all"]["win_rate"] is None, str(r["all"]))
        check("empty a10 None", r["a10"]["win_rate"] is None, str(r["a10"]))
        check("empty warning", any("NO_FINAL_SUMMARIES" in w for w in r["warnings"]),
              str(r["warnings"]))
        md = eval_report.render_markdown(r)
        check("empty md warning", "NO_FINAL_SUMMARIES" in md, md[:300])


def test_malformed_and_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "run_summaries.jsonl"), "w", encoding="utf-8") as f:
            f.write("not json\n")
            f.write(json.dumps({"run_id": "x", "result": "defeat", "ascension": 10,
                                "floor": 10}) + "\n")
        r = eval_report.build_report(tmp)
        check("malformed ignored", r["all"]["runs"] == 1, str(r["all"]))
        check("malformed loss", r["all"]["losses"] == 1, str(r["all"]))
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "run_summary_r9.json"), "w", encoding="utf-8") as f:
            json.dump({"run_id": "r9", "result": "victory", "ascension": 10,
                       "floor": 50}, f)
        r = eval_report.build_report(tmp)
        check("fallback single file", r["all"]["wins"] == 1, str(r["all"]))
        check("fallback a10", r["a10"]["wins"] == 1, str(r["a10"]))


def test_main_require_samples():
    with tempfile.TemporaryDirectory() as tmp:
        fixture(tmp)
        old = sys.argv
        try:
            sys.argv = ["eval_report.py", "--dir", tmp, "--json",
                        "--require-samples", "4"]
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = eval_report.main()
        finally:
            sys.argv = old
        check("require samples exit 2", code == 2, str(code))


def test_harness_discovery():
    names = [os.path.basename(p) for p in test_all.discover()]
    check("harness excludes self", "test_all.py" not in names, str(names))
    check("harness sees sim test", "test_sim.py" in names, str(names))
    check("harness no duplicate", len(names) == len(set(names)), str(names))


def main():
    test_full()
    test_last_and_empty()
    test_malformed_and_fallback()
    test_main_require_samples()
    test_harness_discovery()
    print(f"\nALL OK ({OK} checks)")


if __name__ == "__main__":
    main()
