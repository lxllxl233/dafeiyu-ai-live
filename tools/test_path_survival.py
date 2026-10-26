# -*- coding: utf-8 -*-
"""Offline tests for P0-2 BRPS/path survival and plan identity.

No game API, no real route_plan mutation outside a temp directory.
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import path as brps
import sts2


def node(row, col, ntype, children=(), avail=False, current=False):
    return {
        "row": row, "col": col, "node_type": ntype,
        "children": [{"row": r, "col": c} for r, c in children],
        "is_available": avail, "is_current": current,
    }


MAP = {
    "rows": 6,
    "nodes": [
        node(0, 2, "Ancient", [(1, 1), (1, 3)], current=True),
        # A: elite line, then two campfires / one shop
        node(1, 1, "Monster", [(2, 1)], avail=True),
        node(2, 1, "Elite", [(3, 1)]),
        node(3, 1, "RestSite", [(4, 1)]),
        node(4, 1, "Shop", [(5, 1)]),
        node(5, 1, "RestSite", [(6, 1)]),
        node(6, 1, "Boss", []),
        # B: two normal fights, then boss
        node(1, 3, "Monster", [(2, 3)], avail=True),
        node(2, 3, "Monster", [(3, 3)]),
        node(3, 3, "Shop", [(4, 3)]),
        node(4, 3, "Boss", []),
    ],
}

DECK = ([{"card_id": "STRIKE_IRONCLAD"}] * 5
        + [{"card_id": "DEFEND_IRONCLAD"}] * 4
        + [{"card_id": "BASH"}])


def mk_state(hp=64, mhp=80, run_id="run-test", act_id=0, deck=None, map_data=None):
    return {
        "screen": "MAP",
        "run_id": run_id,
        "map": map_data or MAP,
        "run": {
            "current_hp": hp, "max_hp": mhp, "act_id": act_id,
            "floor": 1, "deck": deck or DECK,
            "potions": [], "gold": 99,
        },
    }


PROFILES = {
    "Monster": {"hp": 50, "incoming": 10, "burst": 14, "assumed": False},
    "Elite": {"hp": 90, "incoming": 12, "burst": 18, "assumed": False},
}


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_hp_sensitive_ranking():
    """64 HP can take the elite/rest line; 37 HP must take the safe line."""
    high = mk_state(hp=64)
    low = mk_state(hp=37)
    paths = brps.enumerate_paths(MAP)
    sc_high = brps.score_paths(high, MAP, paths, sts2.CARD_DB, sts2.MONSTER_DB,
                               sts2.ENCOUNTER_DB, 10, profiles=PROFILES)
    sc_low = brps.score_paths(low, MAP, paths, sts2.CARD_DB, sts2.MONSTER_DB,
                              sts2.ENCOUNTER_DB, 10, profiles=PROFILES)
    bh = sc_high[0]
    bl = sc_low[0]
    check(bh["start"]["col"] == 1, f"64 HP should choose elite/rest line, got {bh['seq']}")
    check(bl["start"]["col"] == 3, f"37 HP should choose safe line, got {bl['seq']}")
    check(bh["score"] > bl["score"], "64 HP ranking score should be higher")
    print("PASS hp_sensitive_ranking", bh["seq"], bh["hp_after"], "vs", bl["seq"], bl["hp_after"])


def test_forced_elite():
    paths = brps.enumerate_paths(MAP)
    elite = next(p for p in paths if p[0]["col"] == 1)
    safe = next(p for p in paths if p[0]["col"] == 3)
    a = brps.score_path(mk_state(), MAP, elite, sts2.CARD_DB, sts2.MONSTER_DB,
                        sts2.ENCOUNTER_DB, 10, profiles=PROFILES)
    b = brps.score_path(mk_state(), MAP, safe, sts2.CARD_DB, sts2.MONSTER_DB,
                        sts2.ENCOUNTER_DB, 10, profiles=PROFILES)
    check(a["forced_elite"] is True, "single-child elite must be marked forced")
    check(b["forced_elite"] is False, "safe line must not be marked forced")
    check(a["forced"] and a["forced"][0]["why"] == "single_child_chain", "forced reason missing")
    print("PASS forced_elite", a["forced"], "safe_forced=", b["forced"])


def test_boss_gate():
    # Weak deck: 10 damage per attack, low block -> fail against a 199 HP boss.
    weak_db = {
        "STRIKE_IRONCLAD": {"type": "Attack", "cost": 1, "damage": 6, "target": "AnyEnemy"},
        "DEFEND_IRONCLAD": {"type": "Skill", "cost": 1, "block": 5, "target": "Self"},
    }
    enc = {"B1": {"id": "B1", "name": "Test Boss", "room_type": "Boss", "act": "Act 1 - Overgrowth",
                  "monsters": [{"id": "B"}]}}
    mon = {"B": {"id": "B", "name": "Boss", "min_hp": 199, "damage_values": {"hit": {"normal": 20, "ascension": 20}}}}
    weak = mk_state(hp=64, deck=DECK)
    wg = brps.boss_gate(weak, weak_db, mon, enc, 10)
    check(wg["ok"] is False, "weak deck must fail boss gate")
    # Strong deck: many upgraded 20-damage attacks -> kill before dying.
    strong_db = {
        "STRIKE_IRONCLAD": {"type": "Attack", "cost": 1, "damage": 20, "target": "AnyEnemy"},
        "DEFEND_IRONCLAD": {"type": "Skill", "cost": 1, "block": 30, "target": "Self"},
    }
    strong = mk_state(hp=80, deck=DECK)
    sg = brps.boss_gate(strong, strong_db, mon, enc, 10)
    check(sg["ok"] is True, "strong deck should pass boss gate")
    print("PASS boss_gate weak=", wg["expected"], "strong=", sg["expected"])


def test_plan_identity():
    s1 = mk_state(run_id="r1", act_id=0)
    snap1 = sts2._snapshot(s1)
    plan = {"identity": {"run_id": "r1", "act_id": 0, "map_sig": snap1["map_sig"]},
            "snapshot": snap1}
    check(sts2._plan_status(snap1, plan) == "ok", "same identity must be ok")
    s2 = mk_state(run_id="r2", act_id=0)
    check(sts2._plan_status(sts2._snapshot(s2), plan) == "stale", "new run must be stale")
    s3 = mk_state(run_id="r1", act_id=1)
    check(sts2._plan_status(sts2._snapshot(s3), plan) == "stale", "new act must be stale")
    check(sts2._plan_status(snap1, {"snapshot": snap1}) == "legacy", "old plan must not be ok")
    print("PASS plan_identity")


def test_forced_dominator():
    m = {
        "rows": 4,
        "nodes": [
            node(0, 2, "Ancient", [(1, 2)], current=True),
            node(1, 2, "Monster", [(2, 2), (2, 3)], avail=True),
            node(2, 2, "Elite", [(3, 2)]),
            node(2, 3, "Monster", [(2, 2)]),
            node(3, 2, "Boss", []),
        ],
    }
    paths = brps.enumerate_paths(m)
    out = brps.score_paths(mk_state(map_data=m, deck=DECK), m, paths,
                           sts2.CARD_DB, sts2.MONSTER_DB, sts2.ENCOUNTER_DB,
                           10, profiles=PROFILES)
    check(len(out) >= 2, "dominator fixture should have two paths")
    check(all(r["forced_elite"] for r in out), "common elite must be forced on every path")
    check(any(any(x["why"] == "dominates_all_paths" for x in r["forced"]) for r in out),
          "dominator path must carry dominates_all_paths")
    print("PASS forced_dominator")


def test_hp_gate_line():
    m = {"rows": 1, "nodes": [node(0, 1, "Boss", [], avail=True)]}
    line = sts2._hp_gate_line(mk_state(map_data=m))
    check(line and "BOSS_TTK FAIL" in line, f"boss gate line missing: {line}")
    print("PASS hp_gate_line", line[:80])


def test_route_actual_ledger():
    s = mk_state(hp=64)
    snap = sts2._snapshot(s)
    paths = brps.enumerate_paths(MAP)
    scored = brps.score_paths(s, MAP, paths, sts2.CARD_DB, sts2.MONSTER_DB,
                              sts2.ENCOUNTER_DB, 10, profiles=PROFILES)
    b = scored[0]
    plan = {
        "identity": {"run_id": "run-test", "act_id": 0, "map_sig": snap["map_sig"]},
        "snapshot": snap,
        "option": 0,
        "start": b["start"],
        "seq": b["seq"],
        "predicted": {"steps": b["steps"]},
    }
    fd, plan_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    fd, actual_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    old_plan, old_actual = sts2.PLAN_PATH, sts2.ROUTE_ACTUAL
    try:
        sts2.PLAN_PATH, sts2.ROUTE_ACTUAL = plan_path, actual_path
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)
        before = mk_state(hp=64)
        after = mk_state(hp=64)
        after["run"]["floor"] = 2
        sts2._record_route_step(before=before, params={"option_index": 0}, after=after)
        with open(plan_path, encoding="utf-8") as f:
            got = json.load(f)
        check(got.get("actual") and got["actual"][0]["row"] == 1, "actual path not appended")
        check(got["actual"][0].get("pred_hp") is not None, "predicted HP not joined")
        with open(actual_path, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        check(len(rows) == 1 and rows[0]["type"] == "Monster", "actual JSONL missing")
        check(rows[0].get("pred_hp") == 64, "actual JSONL must carry entry predicted HP")
        check(rows[0].get("pred_hp_out") == 50, "actual JSONL must carry post-node predicted HP")
        print("PASS route_actual_ledger", got["actual"][0])
    finally:
        sts2.PLAN_PATH, sts2.ROUTE_ACTUAL = old_plan, old_actual
        for p in (plan_path, actual_path):
            try:
                os.remove(p)
            except OSError:
                pass


def test_route_plan_stale_output():
    """A new run must never produce PLAN_OK from the old route file."""
    fd, plan_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    old_plan, old_get = sts2.PLAN_PATH, sts2.get_state
    try:
        sts2.PLAN_PATH = plan_path
        sts2.get_state = lambda: mk_state(run_id="run-a", act_id=0)
        with redirect_stdout(io.StringIO()):
            sts2.cmd_route(commit=0, note="stale-test")
        out = io.StringIO()
        sts2.get_state = lambda: mk_state(run_id="run-b", act_id=0)
        with redirect_stdout(out):
            sts2.cmd_plan()
        text = out.getvalue()
        check("PLAN_STALE" in text, "new run must print PLAN_STALE")
        check("PLAN_OK" not in text, "new run must not print PLAN_OK")
        print("PASS route_plan_stale_output")
    finally:
        sts2.PLAN_PATH, sts2.get_state = old_plan, old_get
        try:
            os.remove(plan_path)
        except OSError:
            pass


def main():
    test_hp_sensitive_ranking()
    test_forced_elite()
    test_forced_dominator()
    test_boss_gate()
    test_plan_identity()
    test_hp_gate_line()
    test_route_actual_ledger()
    test_route_plan_stale_output()
    print("ALL OK")


if __name__ == "__main__":
    main()
