# -*- coding: utf-8 -*-
"""预测精度统计（**唯一口径**）：入伤 / 回合末血量 / 斩杀。

run85 的复盘里出现过两套数字（入伤 13/35、血量 33/51），分母其实不是同一批记录，
所以「±1 命中率」这句话当时没有确定含义。从 run86 起只认这个脚本的输出：

    python tools/accuracy.py                 # 最近一局
    python tools/accuracy.py --run FNVZ5NS2U5
    python tools/accuracy.py --all           # 所有对局合计

口径 v1（写死，改口径必须改这里 + docs/RUN86_FIXES.md）：

1. **总体**：账本里 `outcome == "executed"` 的记录；被护栏拒绝/批量报错的记录不进
   （它们没有真实结算，拿来做精度统计等于自己给自己打分）。
2. **入伤（take）**：总体里 `ended == 1`（计划包含 end_turn）且 `forecast.take` 非空，
   并且 `before.me.hp` / `after.me.hp` 都有值。实际入伤 = `before.me.hp - after.me.hp`。
   排除 `forecast.clear == 1`（战斗结束会触发燃烧之血回血/遗物结算，血量不可比）。
3. **回合末血量（hp）**：条件同上但要求 `forecast.hp` 非空；实际 = `after.me.hp`。
4. **斩杀（kill）**：
   - 预测斩杀 = `forecast.kill` 非空的下标集合；
   - 确认斩杀 = 该下标在 `after.enemies` 里消失（引擎把死掉的怪从列表里摘掉）；
   - 假斩杀 = 该下标仍在 `after.enemies` 且 hp > 0；
   - 漏杀（保守） = 没预测斩杀、但 `after.enemies` 里确实少了怪 —— 我看漏了能杀的机会；
   - 清场（clear）单独记，不计入假斩杀。
5. 命中率一律**先给分母**（N），再给 exact / ±1；不写分母的百分比不算数。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "decisions.jsonl")


def load(run_id: str | None) -> list[dict]:
    recs = []
    if not os.path.exists(LOG):
        return recs
    with open(LOG, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if run_id and r.get("run_id") != run_id:
                continue
            recs.append(r)
    return recs


def hp_of(rec: dict, key: str):
    me = (rec.get(key) or {}).get("me") or {}
    v = me.get("hp")
    return v if isinstance(v, (int, float)) else None


def last_run(recs: list[dict]) -> str | None:
    for r in reversed(recs):
        if r.get("run_id"):
            return r["run_id"]
    return None


def pct(a: int, b: int) -> str:
    return f"{a}/{b} = {100.0 * a / b:.1f}%" if b else "0/0 = -"


def report(recs: list[dict], run_id: str | None) -> None:
    ex = [r for r in recs if r.get("outcome") == "executed"]
    print(f"== 口径 v1 ==  对局 {run_id or '(全部)'} · 记录 {len(recs)} 条 · executed {len(ex)} 条")

    # ---------------- 入伤 ----------------
    inc = []
    for r in ex:
        fc = r.get("forecast") or {}
        if not r.get("ended") or fc.get("take") is None or fc.get("clear"):
            continue
        b, a = hp_of(r, "before"), hp_of(r, "after")
        if b is None or a is None:
            continue
        inc.append((r, int(fc["take"]), b - a))
    print(f"\n-- 入伤（planned end_turn，排除 clear）N={len(inc)}")
    if inc:
        exact = sum(1 for _, p, a in inc if p == a)
        near = sum(1 for _, p, a in inc if abs(p - a) <= 1)
        err = sum(abs(p - a) for _, p, a in inc) / len(inc)
        bias = sum(p - a for _, p, a in inc) / len(inc)
        print(f"   完全命中 {pct(exact, len(inc))} · ±1 {pct(near, len(inc))} · "
              f"平均|误差| {err:.2f} · 平均偏差(预测-实际) {bias:+.2f}")
        worst = sorted(inc, key=lambda x: -abs(x[1] - x[2]))[:5]
        for r, p, a in worst:
            print(f"   f{r.get('floor')} t{r.get('turn')} 预测 {p} 实际 {a} "
                  f"(差 {p - a:+d})")

    # ---------------- 回合末血量 ----------------
    hps = []
    for r in ex:
        fc = r.get("forecast") or {}
        if not r.get("ended") or fc.get("hp") is None or fc.get("clear"):
            continue
        a = hp_of(r, "after")
        if a is None:
            continue
        hps.append((r, int(fc["hp"]), a))
    print(f"\n-- 回合末血量 N={len(hps)}")
    if hps:
        exact = sum(1 for _, p, a in hps if p == a)
        near = sum(1 for _, p, a in hps if abs(p - a) <= 1)
        err = sum(abs(p - a) for _, p, a in hps) / len(hps)
        print(f"   完全命中 {pct(exact, len(hps))} · ±1 {pct(near, len(hps))} · "
              f"平均|误差| {err:.2f}")
        worst = sorted(hps, key=lambda x: -abs(x[1] - x[2]))[:5]
        for r, p, a in worst:
            print(f"   f{r.get('floor')} t{r.get('turn')} 预测 {p} 实际 {a} "
                  f"(差 {p - a:+d})")

    # ---------------- 斩杀 ----------------
    pred_n = conf_n = false_n = miss_n = clear_n = 0
    print("\n-- 斩杀")
    for r in ex:
        fc = r.get("forecast") or {}
        kills = [int(x) for x in (fc.get("kill") or []) if isinstance(x, (int, float))]
        before_e = (r.get("before") or {}).get("enemies") or []
        after_e = (r.get("after") or {}).get("enemies") or []
        after_ids = {e.get("i") for e in after_e if isinstance(e, dict)}
        b_ids = {e.get("i") for e in before_e if isinstance(e, dict)}
        if fc.get("clear"):
            clear_n += 1
        if kills:
            pred_n += len(kills)
            hp = {e.get("i"): e.get("hp") for e in after_e if isinstance(e, dict)}
            gone = [k for k in kills if k not in after_ids]
            alive = [k for k in kills if k in after_ids and (hp.get(k) or 0) > 0]
            conf_n += len(kills) - len(alive)
            false_n += len(alive)
            if alive:
                print(f"   假斩杀 f{r.get('floor')} t{r.get('turn')} 下标 {alive} "
                      f"实际还剩 {[hp.get(k) for k in alive]} 血")
        else:
            died = sorted(i for i in (b_ids - after_ids))
            if died:
                miss_n += len(died)
                print(f"   未预测死亡 f{r.get('floor')} t{r.get('turn')} 死了 {died} 但 forecast.kill 是空的")
    print(f"   预测斩杀 {pred_n} 次 · 确认 {conf_n} · 假斩杀 {false_n} · 未预测死亡 {miss_n}")
    print(f"   清场记录 {clear_n} 条（清场里的斩杀已计入「确认」，不算假斩杀）")
    if pred_n:
        print(f"   假斩杀率 {pct(false_n, pred_n)}（分母=预测斩杀次数）")
    print("   注意：假斩杀 0 不等于模型可靠 —— 「未预测死亡」要逐条看是不是攻击之外的死因"
          "（势不可当/荆棘/毒）；真正「保守放跑能斩的机会」需要离线搜斩杀线，run85 没做这项统计。")


def main() -> int:
    ap = argparse.ArgumentParser(description="预测精度（唯一口径 v1）")
    ap.add_argument("--run", default=None)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    recs = load(None)
    if not recs:
        print("账本为空：先打一局")
        return 1
    if args.all:
        report(recs, None)
        return 0
    rid = args.run or last_run(recs)
    report([r for r in recs if r.get("run_id") == rid], rid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
