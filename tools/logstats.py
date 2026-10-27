#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""决策日志分析：把 `logs/decisions.jsonl` 变成可统计、可复盘的证据。

用法：
  python tools/logstats.py summary                 # 结局/拒绝分布 + 每局明细
  python tools/logstats.py errors [--top 30]       # 归一化错误排行
  python tools/logstats.py cases --kind guard      # 逐条看案例（带当时局面）
  python tools/logstats.py dump --idx 812          # 打印第 N 条原始记录

设计：只读日志，不改任何东西。所有结论都能用 `cases`/`dump` 回到原始记录核对。
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "decisions.jsonl")

# 错误归类：把「漏甲 / 漏费 / 漏斩」和各类 fx 校验失败单独记账
KIND = [
    ("miss_block", re.compile(r"漏甲")),
    ("miss_energy", re.compile(r"漏费")),
    ("miss_lethal", re.compile(r"漏斩")),
    ("fx_hp", re.compile(r"fx\.hp")),
    ("fx_e", re.compile(r"fx\.e")),
    ("fx_take", re.compile(r"fx\.take")),
    ("fx_draw", re.compile(r"fx\.draw")),
    ("energy_cost", re.compile(r"现在要 \d+ 费")),
    ("no_card", re.compile(r"手里没有可打的")),
    ("draw_boundary", re.compile(r"抽牌边界")),
    ("bad_target", re.compile(r"目标 \d+ 不是活怪")),
    ("batch", re.compile(r"batch exit")),
    ("stale", re.compile(r"过渡快照")),
]


def kind_of(rec: dict) -> str:
    err = str(rec.get("err") or "")
    if rec.get("outcome") == "executed":
        return "executed"
    for name, pat in KIND:
        if pat.search(err):
            return name
    return rec.get("outcome") or "?"


def load(path: str = LOG) -> list[dict]:
    out = []
    if not os.path.exists(path):
        return out
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    return out


def _player(rec: dict) -> dict:
    return ((rec.get("before") or {}).get("combat") or {}).get("player") or {}


def _hand(rec: dict) -> list[dict]:
    return [x for x in (((rec.get("before") or {}).get("combat") or {}).get("hand") or [])
            if isinstance(x, dict)]


def cmd_summary(recs: list[dict]) -> int:
    print(f"记录 {len(recs)} 条")
    outs = collections.Counter(r.get("outcome") for r in recs)
    print("\n== 结局分布 ==")
    for k, v in outs.most_common():
        print(f"  {k:<18}{v:>5}  {v/len(recs)*100:5.1f}%")
    print("\n== 错误归类（非 executed） ==")
    kinds = collections.Counter(kind_of(r) for r in recs if r.get("outcome") != "executed")
    tot = sum(kinds.values()) or 1
    for k, v in kinds.most_common():
        print(f"  {k:<18}{v:>5}  {v/tot*100:5.1f}%")
    print("\n== 每局 ==")
    per = collections.defaultdict(collections.Counter)
    for r in recs:
        per[str(r.get("run_id"))][kind_of(r)] += 1
    print(f"  {'run':<12}{'decisions':>10}{'executed':>9}{'rejected':>9}  主要问题")
    for run, c in sorted(per.items(), key=lambda kv: -sum(kv[1].values())):
        bad = {k: v for k, v in c.items() if k != "executed"}
        top = "、".join(f"{k}×{v}" for k, v in sorted(bad.items(), key=lambda kv: -kv[1])[:3])
        print(f"  {run:<12}{sum(c.values()):>10}{c['executed']:>9}{sum(bad.values()):>9}  {top}")
    return 0


def cmd_errors(recs: list[dict], top: int) -> int:
    norm = collections.Counter()
    for r in recs:
        if r.get("outcome") == "executed":
            continue
        err = re.sub(r"\d+", "N", str(r.get("err") or ""))[:110]
        norm[(kind_of(r), err)] += 1
    print(f"{'次数':>5}  {'类别':<14} 错误")
    for (k, e), n in norm.most_common(top):
        print(f"{n:>5}  {k:<14} {e}")
    return 0


def _brief(rec: dict) -> str:
    p = _player(rec)
    hand = _hand(rec)
    en = p.get("energy")
    blk = p.get("block")
    hn = "、".join(f"{h.get('name')}({h.get('energy_cost')})" for h in hand[:8])
    return (f"run {rec.get('run_id')} f{rec.get('floor')} t{rec.get('turn')} "
            f"hp={p.get('current_hp')} 甲={blk} 能量={en} 手牌[{hn}]")


def cmd_cases(recs: list[dict], kind: str, limit: int) -> int:
    rows = [r for r in recs if kind_of(r) == kind]
    print(f"{kind} 共 {len(rows)} 条，显示前 {min(limit, len(rows))} 条\n")
    for i, r in enumerate(rows[:limit]):
        print(f"[{i}] {_brief(r)}")
        print(f"    计划 {json.dumps(r.get('input'), ensure_ascii=False)[:220]}")
        print(f"    错误 {str(r.get('err') or '')[:220]}")
        fc = r.get("forecast") or {}
        if fc:
            print(f"    实际 {json.dumps(fc, ensure_ascii=False)[:200]}")
        print()
    return 0


def cmd_verify(recs: list[dict], limit: int, field: str) -> int:
    """拿「回合中（mid）不结束回合」的真实记录，检验 sim 预测 vs 游戏实际。

    这类记录没有抽牌随机性，预测应当**逐字段吻合**；不吻合就是 sim 漏了机制。
    只用决策当时可见的信息：before 快照 + 实际 after 快照，不掺任何事后信息。
    """
    rows = [r for r in recs if r.get("outcome") == "executed" and r.get("mid")
            and r.get("before") and r.get("after")]
    bad = []
    for r in rows:
        b, a = r["before"], r["after"]
        bm = b.get("me") or {}
        am = a.get("me") or {}
        fc = r.get("forecast") or {}
        diff = []
        if "e" in fc and fc["e"] is not None and am.get("e") is not None and fc["e"] != am.get("e"):
            diff.append(f"能量: 预测 {fc['e']} 实际 {am.get('e')}")
        if "hp" in fc and fc["hp"] is not None and am.get("hp") is not None and fc["hp"] != am.get("hp"):
            diff.append(f"HP: 预测 {fc['hp']} 实际 {am.get('hp')}")
        killed_fc = len(fc.get("kill") or [])
        dead_bf = {e["i"] for e in (b.get("enemies") or []) if (e.get("hp") or 0) <= 0}
        dead_af = {e["i"] for e in (a.get("enemies") or []) if (e.get("hp") or 0) <= 0}
        if dead_af - dead_bf != set() and len(dead_af - dead_bf) != killed_fc:
            diff.append(f"击杀: 预测 {killed_fc} 实际 {len(dead_af - dead_bf)}")
        if diff:
            bad.append((r, diff, b, a, bm, am))
    n = len(rows)
    ok = n - len(bad)
    print(f"回合中快照 {n} 条：预测全对 {ok} 条（{ok/max(n,1)*100:.1f}%），不符 {len(bad)} 条")
    by_field = collections.Counter()
    sigs = collections.Counter()
    for r, diff, b, a, bm, am in bad:
        for d in diff:
            by_field[d.split(":")[0]] += 1
        if field == "all" or by_field:
            sigs[" + ".join(sorted({d.split(":")[0] for d in diff}))] += 1
    print("  按字段:", dict(by_field))
    print("  组合:", dict(sigs.most_common(6)))
    if limit:
        print(f"\n前 {min(limit, len(bad))} 条不符：")
        for r, diff, b, a, bm, am in bad[:limit]:
            print(f"  run {b['run']['run_id']} f{b['run']['floor']} t{b.get('turn')} "
                  f"牌组 {b['run'].get('deck_size')} | {'；'.join(diff)}")
            print(f"    手牌 {[h['id'] for h in (b.get('hand') or [])]} 能量 {bm.get('e')} → 实 {am.get('e')}")
            print(f"    序列 {json.dumps(r.get('input', {}).get('seq') if isinstance(r.get('input'), dict) else r.get('input'), ensure_ascii=False)[:200]}")
            print(f"    药水 {b['run'].get('potions')} 遗物 {b['run'].get('relics')}")
            print(f"    我buff {bm.get('b')} 敌 {[(e['id'], e.get('hp'), e.get('b')) for e in (b.get('enemies') or [])]}")
    return 0


def cmd_replay(recs: list[dict], limit: int) -> int:
    """用**真实局面**重放真实牌序，检查 sim 的结算是否跟游戏一致。

    数据来源：账本里 `state`（compact 全量局面）+ `input.seq` + `after`（游戏真实结果）。
    只取「回合中」记录：这些没有回合末抽牌随机性，结算应当完全确定。
    这里检验的是 sim 本身（不是当时写的 forecast），所以能挖出「模型算错」之外的
    「模拟器算错」——两者都会让决策跑偏。
    """
    try:
        import sim  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print(f"import sim 失败：{exc}")
        return 1
    rows = [r for r in recs if r.get("outcome") == "executed" and r.get("mid")
            and r.get("state") and r.get("after")]
    bad = []
    unvalid = []
    used = 0
    for r in rows:
        seq = ((r.get("input") or {}).get("seq")) or []
        if not seq or any(isinstance(x, dict) and "p" in x for x in seq):
            continue  # 药水效果不在 sim 的 validate 范围里，先跳过
        used += 1
        try:
            ok, err, sm, _steps = sim.validate(r["state"], seq)
        except Exception as exc:  # noqa: BLE001
            unvalid.append((r, f"异常 {type(exc).__name__}: {exc}"))
            continue
        if not ok:
            unvalid.append((r, str(err)))
            continue
        a = r["after"]
        am = a.get("me") or {}
        if not (a.get("enemies") or []):
            continue  # 战斗刚结束：燃烧之血等战后回血会污染对比，不算 sim 的账
        diff = []
        if am.get("e") is not None and sm.energy != am["e"]:
            diff.append(f"能量 {sm.energy}≠{am['e']}")
        if am.get("hp") is not None and sm.hp != am["hp"]:
            diff.append(f"血量 {sm.hp}≠{am['hp']}")
        # 手牌：抽牌是随机的，只检查「打出去的牌确实离手、没打的牌还在」
        played = collections.Counter()
        for x in seq:
            if isinstance(x, dict) and x.get("c"):
                played[str(x["c"]).rstrip("+")] += 1
        for h in (a.get("hand") or []):
            h["id"]  # noqa: B018  (保持结构，顺便防止空 dict)
        if any(n > 0 for n in played.values()):
            ahand = collections.Counter(str(h.get("id")) for h in (a.get("hand") or []))
            shand = collections.Counter(str(c.get("id")) for c in sm.hand if not c.get("_played"))
            ghost = [k for k in played if ahand.get(k, 0) > 0 and shand.get(k, 0) > 0
                     and ahand[k] >= played[k] and shand[k] >= played[k]]
            if ghost:
                diff.append(f"打出的牌还在手上 {sorted(ghost)}")
        aehp = {str(e.get("id")) + str(e.get("i")): e.get("hp") for e in (a.get("enemies") or [])}
        sehp = {str(e.get("id")) + str(e.get("i")): e.get("hp") for e in sm.enemies}
        for k in sehp:
            if k in aehp and aehp[k] is not None and sehp[k] != aehp[k]:
                diff.append(f"敌血 {sehp[k]}≠{aehp[k]}")
                break
        if diff:
            bad.append((r, diff))
    print(f"可重放 {used} 条：完全吻合 {used-len(bad)-len(unvalid)} 条，数值不符 {len(bad)} 条，"
          f"重放失败 {len(unvalid)} 条")
    if unvalid:
        print("\n== 重放失败（游戏接受、sim 却算不出来） ==")
        for r, err in unvalid[:limit]:
            b = r["state"]
            print(f"  f{b['run'].get('floor')} t{b.get('turn')} {r['run_id']} | {err[:120]}")
            print(f"    seq {json.dumps(((r.get('input') or {}).get('seq')), ensure_ascii=False)[:180]}")
            print(f"    手牌 {[(h.get('id'), h.get('energy_cost', h.get('cost'))) for h in (b.get('combat') or {}).get('hand') or []][:8]}")
    if bad:
        print("\n== 数值不符 ==")
        sig = collections.Counter()
        for r, diff in bad:
            sig[tuple(d.split(" ")[0] for d in diff)] += 1
        for k, v in sig.most_common(8):
            print(f"  {'+'.join(k)} ×{v}")
        for r, diff in bad[:limit]:
            b = r["state"]
            print(f"  f{b['run'].get('floor')} t{b.get('turn')} {r['run_id']} | {'；'.join(diff)}")
            print(f"    seq {json.dumps(((r.get('input') or {}).get('seq')), ensure_ascii=False)[:180]}")
            print(f"    手牌 {[(h.get('id'), h.get('energy_cost', h.get('cost'))) for h in (b.get('combat') or {}).get('hand') or []][:8]}")
            print(f"    遗物 {(b.get('run') or {}).get('relics')}")
    return 0


def cmd_dump(recs: list[dict], idx: int) -> int:
    if not 0 <= idx < len(recs):
        print(f"下标越界（0..{len(recs)-1}）")
        return 1
    print(json.dumps(recs[idx], ensure_ascii=False, indent=1)[:8000])
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary")
    p = sub.add_parser("errors")
    p.add_argument("--top", type=int, default=30)
    p = sub.add_parser("cases")
    p.add_argument("--kind", required=True)
    p.add_argument("--limit", type=int, default=8)
    p = sub.add_parser("dump")
    p.add_argument("--idx", type=int, required=True)
    p = sub.add_parser("replay")
    p.add_argument("--limit", type=int, default=6)
    p = sub.add_parser("verify")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--field", default="all")
    a = ap.parse_args(argv)
    recs = load()
    if not recs:
        print(f"没有日志：{LOG}")
        return 1
    if a.cmd == "summary":
        return cmd_summary(recs)
    if a.cmd == "errors":
        return cmd_errors(recs, a.top)
    if a.cmd == "cases":
        return cmd_cases(recs, a.kind, a.limit)
    if a.cmd == "replay":
        return cmd_replay(recs, a.limit)
    if a.cmd == "verify":
        return cmd_verify(recs, a.limit, a.field)
    return cmd_dump(recs, a.idx)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
