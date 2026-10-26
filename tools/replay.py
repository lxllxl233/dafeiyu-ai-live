# -*- coding: utf-8 -*-
"""离线重放决策账本：只读 decisions.jsonl，不连游戏、不改状态。

用法：
  python tools/replay.py                    # 最近 10 条有 state 的决策
  python tools/replay.py --last 20 --run-id <id>
  python tools/replay.py --file some_record.json

对每条记录：
  * 用记录里的 state + input.seq 重新跑 sim.validate；
  * 对照 recorded steps / forecast；
  * 若记录里带 chosen_plan，检查它是否就是账本里的候选之一。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import decision_log  # noqa: E402
import sim  # noqa: E402

OK_OUTCOMES = {"executed", "batch_error"}
FAIL_OUTCOMES = {"rejected_validate", "rejected_fx", "no_actions", "stale_end",
                 "missing_seq", "not_combat"}


def _forecast(sm, steps: list, ended: bool) -> dict:
    if ended:
        res = sm.end_turn()
        hp, take = res["hp_after"], res["taken"]
    else:
        hp, take = sm.hp, None
    fx = {"e": sm.energy, "hp": hp, "draw": sm.draws, "exh": sm.exhausts}
    if take is not None:
        fx["take"] = take
    if sm.selfhp:
        fx["self"] = sm.selfhp
    kills = [e["i"] for e in sm.enemies if e["dead"]]
    if kills:
        fx["kill"] = kills
    return fx


def _steps_view(steps) -> list:
    out = []
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        if s.get("k") == "end":
            out.append({"k": "end"})
        elif s.get("k") == "p":
            out.append({"k": "p", "p": s.get("p"), "t": s.get("t")})
        else:
            out.append({"k": "c", "id": s.get("id"), "t": s.get("t")})
    return out


def verify_record(r: dict, search: bool = False) -> dict:
    """返回 {ok, reason, ...}；不抛异常。"""
    try:
        st = r.get("state")
        seq = (r.get("input") or {}).get("seq")
        outcome = r.get("outcome")
        if not isinstance(st, dict) or st.get("screen") != "COMBAT":
            return {"ok": False, "reason": "没有可重放的战斗 state"}
        if not isinstance(seq, list):
            return {"ok": False, "reason": "input.seq 不是列表"}
        expected_ok = outcome in OK_OUTCOMES
        got_ok, err, sm, steps = sim.validate(st, seq)
        if got_ok != expected_ok:
            return {"ok": False, "reason": f"validate={got_ok}，账本 outcome={outcome}",
                    "err": err}
        if not got_ok:
            return {"ok": True, "reason": "按账本被拒", "validate": False}

        ended = bool(steps and steps[-1].get("k") == "end")
        want = _forecast(sm, steps, ended)
        got_fx = r.get("forecast") or {}
        diffs = {k: (got_fx.get(k), want.get(k)) for k in ("e", "hp", "take")
                 if k in want and got_fx.get(k) != want.get(k)}
        if diffs:
            return {"ok": False, "reason": f"forecast 不一致 {diffs}",
                    "forecast": want}
        recorded_steps = _steps_view(r.get("steps"))
        if recorded_steps and recorded_steps != _steps_view(steps):
            return {"ok": False, "reason": "steps 不一致",
                    "recorded": recorded_steps, "replayed": _steps_view(steps)}

        out = {"ok": True, "reason": "ok", "validate": True,
               "chosen_plan": bool(r.get("chosen_plan")), "forecast": want}
        if search and expected_ok:
            plans = sim.search(st, max_cards=8, max_plans=5, time_limit=0.8,
                               max_nodes=120_000)
            matched = decision_log._chosen_plan(steps, plans)
            rec_plan = r.get("chosen_plan")
            if rec_plan and not matched:
                return {"ok": False, "reason": "重算候选里找不到账本所选线",
                        "plans": [p.get("k") for p in plans]}
            if rec_plan and matched and rec_plan.get("score") != matched.get("score"):
                return {"ok": False, "reason": f"score 不一致 {rec_plan.get('score')} != {matched.get('score')}"}
            out["replan"] = matched
        return out
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def main():
    ap = argparse.ArgumentParser(description="离线重放决策账本")
    ap.add_argument("--file", help="单条记录的 JSON 文件")
    ap.add_argument("--run-id", help="只重放该 run")
    ap.add_argument("--last", type=int, default=10, help="最近 N 条（默认 10）")
    ap.add_argument("--search", action="store_true", help="重算候选并核对 chosen_plan/score")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是逐行文本")
    args = ap.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8-sig") as f:
            recs = [json.load(f)]
    else:
        recs = decision_log.read_decisions()
        if args.run_id:
            recs = [r for r in recs if str(r.get("run_id")) == str(args.run_id)]
        recs = [r for r in recs if isinstance(r.get("state"), dict)]
        if args.last and args.last > 0:
            recs = recs[-args.last:]

    results = []
    for r in recs:
        res = verify_record(r, search=args.search)
        res["floor"] = r.get("floor")
        res["turn"] = r.get("turn")
        res["outcome"] = r.get("outcome")
        results.append(res)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = "PASS" if r.get("ok") else "FAIL"
            print(f"{mark} f{r.get('floor')} t{r.get('turn')} {r.get('outcome')} "
                  f"-> {r.get('reason')}")
        bad = sum(1 for r in results if not r.get("ok"))
        print(f"REPLAY {len(results) - bad}/{len(results)} PASS")
    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
