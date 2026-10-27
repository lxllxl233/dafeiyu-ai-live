# -*- coding: utf-8 -*-
"""预测精度统计（**唯一口径**）：入伤 / 回合末血量 / 斩杀 / 批次。

run85 的复盘里出现过两套数字（入伤 13/35、血量 33/51），分母其实不是同一批记录，
所以「±1 命中率」这句话当时没有确定含义。从 run86 起只认这个脚本的输出；
**run87 起口径升到 v2**（run86 报告 §3 暴露的两处口径污染 + 按下标配对）。

    python tools/accuracy.py                 # 最近一局
    python tools/accuracy.py --run FNVZ5NS2U5
    python tools/accuracy.py --all           # 所有对局合计
    python tools/accuracy.py --audit         # 逐条列出斩杀配对（旧口径 vs 新口径）

口径 v2（写死；改口径必须改这里 + docs/RUN87_FIXES.md + 更新历史对照表）：

1. **总体**：`outcome == "executed"` 的记录。被护栏拒绝/批量报错的记录没有真实结算，
   不进精度统计（但批次指标单独统计它们，见 §4）。
2. **入伤（take）**：总体里 `ended == 1`、`forecast.take` 非空、`before.me.hp`/`after.me.hp`
   都有值，**并且这一批之后战斗仍在继续**（`after.screen == "COMBAT"`）。
   实际入伤 = `before.me.hp − after.me.hp − forecast.self`。
   - **排除战斗结束后的记录**（`after.screen` 不是 COMBAT，或没有 screen 但 `forecast.clear`）：
     那些记录里血量会掺进燃烧之血 +6、遗物结算、逃跑、致死夹到 0 —— 都不是「敌方这一手的伤害」。
     它们**不丢弃**：单独计入「战斗结束/回血」桶（打印出来），原始记录仍在账本里。
   - **减掉 `forecast.self`**（血墙/放血/祭品的自扣血）：`take` 只管敌方伤害与状态税，
     自扣血是我自己选的代价，混进去就等于把「我花 2 血」记成「敌人多打 2 点」。
3. **回合末血量（hp）**：同 §2 的记录范围；实际 = `after.me.hp`（`forecast.hp` 已经把
   `self` 扣掉了：血墙是打牌时立刻扣，sim 在 play 阶段就减过血）。
   同一份记录范围、同一套排除规则，这样「入伤对了、血量也一定对」两个数才能互相印证。
4. **斩杀（kill）—— 按 enemy_id 计数配对，不按下标**：
   - 预测斩杀 = `forecast.kill` 里的下标，翻译成**该下标在 `before` 里对应的 enemy_id**；
   - 实际死亡 = 同一 enemy_id 在 `before` / `after` 两边的数量差（`before − after`，负数记 0）；
   - 身份依据只有 `enemy_id`（引擎不提供 uuid）→ 同种怪多只时**只能确认"这种怪少了一只"**，
     不能确认"少的就是我打的那一只"；这种情况在 `--audit` 里标 `count_only`；
   - **确认**：该 id 的死亡配额还没用完；**假斩杀**：配额用完而该 id 在 after 里还有活口；
   - **无法判定**：`after` 没有敌人列表（战斗结束后引擎不再返回），且不能从「清场」推断 ——
     这类不强行配对，单列 `undecidable`，**既不算确认也不算假斩杀**；
   - **清场**（`forecast.clear` + 战斗确已结束）→ 全部计确认（证据：战斗结束）。
   - 旧口径（run86 及以前）是「下标在 after 里还在 → 假斩杀」，同批次先杀死一只就会误报，
     这就是 run86 那 5 条「假斩杀」的来源（§audit 里逐条给出旧/新对照）。
5. **未预测死亡**：`after` 有列表、某 id 数量减少但没有对应预测斩杀 → 记「未预测死亡」；
   若该怪带 `MINION_POWER`（爪牙随主人死而撤）→ 单列，不算漏杀机会。
6. **批次中断**：`outcome == "batch_error"` / `executed`。`rejected_*` 是**执行前**被拦下，
   没有半路断供，不进这个分子；但 `rejected_clear_overflow`（清场后还排了牌）单独计数并打印，
   因为这个数反映的是同一类错误（排了清场之后不该排的牌）。
7. 命中率一律**先给分母**（N），再给 exact / ±1；不写分母的百分比不算数。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "decisions.jsonl")

OK_SCREENS_AFTER_END = ("REWARD", "GAME_OVER", "CARD_SELECTION", "MAP", "EVENT", "SHOP")


def _head() -> str:
    """当前工作区的 git 短 sha（和账本 rev 对得上，才知道这局是哪套代码打的）。"""
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import decision_log  # noqa: PLC0415
        return decision_log.code_rev() or "(读取失败)"
    except Exception:  # noqa: BLE001
        return "(读取失败)"


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


def _ei(e) -> str:
    return str((e or {}).get("id") or (e or {}).get("n") or "?")


def _enemies(blob) -> list | None:
    """取敌人列表；键不存在返回 None（和「空列表」区分开：空列表=真的没怪了）。"""
    if not isinstance(blob, dict):
        return None
    e = blob.get("enemies")
    return e if isinstance(e, list) else None


def _screen(rec: dict, key: str = "after") -> str:
    return str(((rec.get(key) or {}).get("screen") or "")).upper()


def _is_minion(e) -> bool:
    for b in (e or {}).get("b") or []:
        if isinstance(b, dict) and str(b.get("id") or "").upper().startswith("MINION"):
            return True
    return False


# ------------------------------------------------------------------ 斩杀配对
def kill_cases(rec: dict) -> list[dict]:
    """把一条记录的 `forecast.kill` 翻译成「按 enemy_id 计数」的判定。

    返回每条预测斩杀一个 dict：idx / id / verdict / evidence / old_verdict / count_only
    verdict ∈ confirmed / false / undecidable。
    """
    fc = rec.get("forecast") or {}
    kills = [int(x) for x in (fc.get("kill") or []) if isinstance(x, (int, float))]
    if not kills:
        return []
    before_e = _enemies(rec.get("before")) or _enemies(rec.get("state")) or []
    after_e = _enemies(rec.get("after"))
    clear = bool(fc.get("clear"))
    screen = _screen(rec, "after")
    by_idx = {e.get("i"): e for e in before_e if isinstance(e, dict)}
    bcnt = Counter(_ei(e) for e in before_e if isinstance(e, dict))
    acnt = Counter(_ei(e) for e in (after_e or []) if isinstance(e, dict))
    hp_after = {e.get("i"): e.get("hp") for e in (after_e or []) if isinstance(e, dict)}
    after_ids = {e.get("i") for e in (after_e or []) if isinstance(e, dict)}

    out = []
    for k in kills:
        src = by_idx.get(k)
        eid = _ei(src) if src else None
        old_alive = k in after_ids and (hp_after.get(k) or 0) > 0
        old = "false" if old_alive else "confirmed"
        # 身份基础：before 里这种怪只有 1 只 → 「它没了」等价于「我打的那只没了」；
        # 多只同种怪 → 只能数出「少了一只」，标签单列，别冒充一对一确认。
        case = {"idx": k, "id": eid, "old": old,
                "basis": "id" if bcnt.get(eid, 0) == 1 else "count"}
        if eid is None:
            case.update(verdict="undecidable",
                        evidence=f"下标 {k} 在 before 里没有对应敌人（账本敌人列表不完整）")
            out.append(case)
            continue
        if after_e is None:
            # 战斗结束后引擎不再返回敌人列表：只有「预测清场 + 战斗确实结束」能当证据
            if clear and (not screen or screen in OK_SCREENS_AFTER_END):
                case.update(verdict="confirmed",
                            evidence=f"清场后战斗结束（after.screen={screen or '?'}），{eid} 不可能存活")
            else:
                case.update(verdict="undecidable",
                            evidence=f"after 没有敌人列表（screen={screen or '?'}），无法确认 {eid} 是否死亡")
            out.append(case)
            continue
        left = bcnt.get(eid, 0) - acnt.get(eid, 0)
        if left > 0:
            case.update(verdict="confirmed",
                        evidence=f"{eid} {bcnt.get(eid, 0)}→{acnt.get(eid, 0)}（少 1 只）")
            bcnt[eid] -= 1
        else:
            case.update(verdict="false",
                        evidence=f"{eid} 仍是 {acnt.get(eid, 0)} 只（after 血量 "
                                 f"{[e.get('hp') for e in after_e if _ei(e) == eid]}），这一批没少它")
        out.append(case)
    return out


def missed_cases(rec: dict) -> dict:
    """本批「少了怪但没预测斩杀」的分类：miss / minion / ended。"""
    fc = rec.get("forecast") or {}
    if fc.get("kill"):
        return {"miss": [], "minion": [], "ended": False}
    before_e = _enemies(rec.get("before")) or _enemies(rec.get("state")) or []
    after_e = _enemies(rec.get("after"))
    if after_e is None or _screen(rec, "after") not in ("", "COMBAT"):
        # 战斗结束了（清场/逃跑/死亡）：谁没了说不清 —— 逃跑的贼和被打死的怪在账本里长得一样。
        return {"miss": [], "minion": [], "ended": True}
    bcnt = Counter(_ei(e) for e in before_e if isinstance(e, dict))
    acnt = Counter(_ei(e) for e in (after_e or []) if isinstance(e, dict))
    minion = Counter()
    for e in before_e:
        if isinstance(e, dict) and _is_minion(e):
            minion[_ei(e)] = 1
    miss, mini = [], []
    for eid, n in bcnt.items():
        gone = n - acnt.get(eid, 0)
        if gone <= 0:
            continue
        if minion.get(eid):
            mini.append((eid, gone))
        else:
            miss.append((eid, gone))
    return {"miss": miss, "minion": mini, "ended": False}


def incoming_rows(ex: list[dict]) -> tuple[list[tuple], Counter]:
    """口径 v2 §2：战斗内、有真实结算的入伤样本 + 排除桶（排除的只是不进分子，不丢记录）。"""
    inc, excl = [], Counter()
    for r in ex:
        fc = r.get("forecast") or {}
        if not r.get("ended") or fc.get("take") is None:
            continue
        b, a = hp_of(r, "before"), hp_of(r, "after")
        if b is None or a is None:
            excl["无血量读数"] += 1
            continue
        screen = _screen(r, "after")
        if screen and screen != "COMBAT":
            excl[f"战斗结束（after={screen}）"] += 1
            continue
        if not screen and fc.get("clear"):
            excl["战斗结束（无 screen + clear）"] += 1
            continue
        self_hp = int(fc.get("self") or 0)
        inc.append((r, int(fc["take"]), (b - a) - self_hp, self_hp, b - a))
    return inc, excl


def hp_rows(ex: list[dict]) -> tuple[list[tuple], Counter]:
    """口径 v2 §3：和入伤同一批记录，比较「回合末血量」。"""
    hps, hexcl = [], Counter()
    for r in ex:
        fc = r.get("forecast") or {}
        if not r.get("ended") or fc.get("hp") is None:
            continue
        a = hp_of(r, "after")
        if a is None:
            hexcl["无血量读数"] += 1
            continue
        screen = _screen(r, "after")
        if screen and screen != "COMBAT":
            hexcl[f"战斗结束（after={screen}）"] += 1
            continue
        if not screen and fc.get("clear"):
            hexcl["战斗结束（无 screen + clear）"] += 1
            continue
        hps.append((r, int(fc["hp"]), a))
    return hps, hexcl


# ------------------------------------------------------------------ 报告
def report(recs: list[dict], run_id: str | None, audit: bool = False) -> None:
    ex = [r for r in recs if r.get("outcome") == "executed"]
    print(f"== 口径 v2 ==  对局 {run_id or '(全部)'} · 记录 {len(recs)} 条 · executed {len(ex)} 条")
    revs = sorted({str(r.get("rev") or "") for r in recs if r.get("rev")})
    if revs:
        print(f"   代码版本（账本 rev）: {', '.join(revs)}")
    else:
        print("   代码版本（账本 rev）: 无此字段（run85 及更早的账本没有版本戳，跨版本统计无效）")
    print(f"   当前工作区 HEAD: {_head()}")

    # ---------------- 入伤 ----------------
    inc, excl = incoming_rows(ex)
    n_inc = len(inc)
    print(f"\n-- 入伤（战斗内预测，口径 v2）N={n_inc}")
    if excl:
        print("   排除（不丢弃，原始记录仍在账本）: "
              + " · ".join(f"{k} {v}" for k, v in sorted(excl.items())))
    if inc:
        exact = sum(1 for _, p, a, *_ in inc if p == a)
        near = sum(1 for _, p, a, *_ in inc if abs(p - a) <= 1)
        err = sum(abs(p - a) for _, p, a, *_ in inc) / n_inc
        bias = sum(p - a for _, p, a, *_ in inc) / n_inc
        print(f"   完全命中 {pct(exact, n_inc)} · ±1 {pct(near, n_inc)} · "
              f"平均|误差| {err:.2f} · 平均偏差(预测-实际) {bias:+.2f}")
        worst = [x for x in sorted(inc, key=lambda x: -abs(x[1] - x[2])) if x[1] != x[2]][:5]
        for r, p, a, s, raw in worst:
            tag = f"，含自扣 {s}" if s else ""
            print(f"   f{r.get('floor')} t{r.get('turn')} 预测 {p} 实际 {a} "
                  f"(差 {p - a:+d}{tag}；血量 {raw})")
        # v1 对照（旧公式：不排除战斗结束、不减自扣血）
        v1 = []
        for r in ex:
            fc = r.get("forecast") or {}
            if not r.get("ended") or fc.get("take") is None or fc.get("clear"):
                continue
            b, a = hp_of(r, "before"), hp_of(r, "after")
            if b is None or a is None:
                continue
            v1.append((r, int(fc["take"]), b - a))
        if v1:
            near1 = sum(1 for _, p, a in v1 if abs(p - a) <= 1)
            err1 = sum(abs(p - a) for _, p, a in v1) / len(v1)
            print(f"   v1 对照（旧公式，含战斗结束/自扣血）: ±1 {pct(near1, len(v1))} · 平均|误差| {err1:.2f}")

    # ---------------- 回合末血量 ----------------
    hps, hexcl = hp_rows(ex)
    print(f"\n-- 回合末血量（同一记录范围）N={len(hps)}")
    if hexcl:
        print("   排除: " + " · ".join(f"{k} {v}" for k, v in sorted(hexcl.items())))
    if hps:
        exact = sum(1 for _, p, a in hps if p == a)
        near = sum(1 for _, p, a in hps if abs(p - a) <= 1)
        err = sum(abs(p - a) for _, p, a in hps) / len(hps)
        print(f"   完全命中 {pct(exact, len(hps))} · ±1 {pct(near, len(hps))} · "
              f"平均|误差| {err:.2f}")
        worst = [x for x in sorted(hps, key=lambda x: -abs(x[1] - x[2])) if x[1] != x[2]][:5]
        for r, p, a in worst:
            print(f"   f{r.get('floor')} t{r.get('turn')} 预测 {p} 实际 {a} (差 {p - a:+d})")

    # ---------------- 斩杀 ----------------
    print("\n-- 斩杀（按 enemy_id 计数配对，口径 v2）")
    pred = conf = false = undec = 0
    conf_id = conf_cnt = 0
    old_conf = old_false = 0
    shift = 0
    rows = []
    for r in ex:
        cases = kill_cases(r)
        if not cases:
            continue
        for c in cases:
            pred += 1
            if c["verdict"] == "confirmed":
                conf += 1
                if c.get("basis") == "id":
                    conf_id += 1
                else:
                    conf_cnt += 1
            elif c["verdict"] == "false":
                false += 1
            else:
                undec += 1
            if c["old"] == "confirmed":
                old_conf += 1
            else:
                old_false += 1
            if c["old"] == "false" and c["verdict"] == "confirmed":
                shift += 1
            if c["verdict"] == "false":
                print(f"   假斩杀 f{r.get('floor')} t{r.get('turn')} {c['id']}（下标 {c['idx']}）"
                      f": {c['evidence']}")
            rows.append((r, c))
    print(f"   预测斩杀 {pred} 次 · 确认 {conf}（按 id 唯一 {conf_id} + 同种怪计数 {conf_cnt}）"
          f" · 假斩杀 {false} · 无法判定 {undec}")
    if shift:
        print(f"   其中「旧口径按下标算成假斩杀、新口径按 id 计数算确认」= {shift} 条（统计误报，不是模型错误）")
    print(f"   旧口径（按下标，仅作对照）: 确认 {old_conf} · 假斩杀 {old_false}")

    miss, mini, ended_n = 0, 0, 0
    miss_rows = []
    for r in ex:
        m = missed_cases(r)
        if m["ended"] and not (r.get("forecast") or {}).get("kill"):
            ended_n += 1
            continue
        for eid, n in m["miss"]:
            miss += n
            miss_rows.append((r, eid, n))
        for eid, n in m["minion"]:
            mini += n
    print(f"   未预测死亡 {miss} 只 · 爪牙随主人撤场 {mini} 只（不算漏杀） · "
          f"战斗结束不可判定 {ended_n} 条")
    for r, eid, n in miss_rows:
        print(f"   未预测死亡 f{r.get('floor')} t{r.get('turn')} {eid}×{n}")
    clear_n = sum(1 for r in ex if (r.get("forecast") or {}).get("clear"))
    print(f"   清场记录 {clear_n} 条（清场里的斩杀按「战斗结束」证据计确认）")
    if pred:
        print(f"   假斩杀率 {pct(false, pred)}（分母=预测斩杀次数；无法判定 {undec} 条不在分母里）")
    print("   注意：假斩杀 0 不等于模型可靠 —— 「未预测死亡」要逐条看是不是攻击之外的死因"
          "（势不可挡/荆棘/毒）；真正「保守放跑能斩的机会」需要离线搜斩杀线，至今未做。")

    # ---------------- 批次 ----------------
    n_ex = len(ex)
    be = [r for r in recs if r.get("outcome") == "batch_error"]
    overflow = [r for r in recs if r.get("outcome") == "rejected_clear_overflow"]
    other = Counter(str(r.get("outcome")) for r in recs
                    if r.get("outcome") not in ("executed", "batch_error", "rejected_clear_overflow"))
    print("\n-- 批次")
    print(f"   batch 中断 {pct(len(be), n_ex)}（outcome=batch_error / executed）")
    for r in be:
        log = (r.get("batch") or {}).get("log") or []
        first = next((str(x) for x in log if "BATCH" in str(x) or "STOP" in str(x)), "")
        print(f"   中断 f{r.get('floor')} t{r.get('turn')}: {str(r.get('err'))[:60]}"
              + (f" | {first[:110]}" if first else ""))
    print(f"   清场后越界（执行前被拒，不进上面的分子）{len(overflow)} 条")
    if other:
        print("   其他非执行结果: " + " · ".join(f"{k} {v}" for k, v in sorted(other.items())))

    if audit:
        print("\n-- audit：逐条斩杀配对（旧口径 vs 新口径）")
        print(f"   {'楼层':>6} {'预测':>4} {'id':<22} {'依据':<6} {'旧':<10} {'新':<12} 证据")
        for r, c in rows:
            print(f"   f{r.get('floor')} t{r.get('turn'):<3} {c['idx']:>4} {str(c['id']):<22} "
                  f"{c.get('basis', '?'):<6} {c['old']:<10} {c['verdict']:<12} {c['evidence']}")
        if inc:
            print("\n-- audit：入伤逐条（预测 / 实际 / 差）")
            for r, p, a, s, raw in sorted(inc, key=lambda x: -abs(x[1] - x[2])):
                if p == a:
                    continue
                print(f"   f{r.get('floor')} t{r.get('turn')} 预测 {p} 实际 {a} 差 {p - a:+d}"
                      f"{f'（自扣 {s}，原始血量变化 {raw}）' if s else ''}")


def main() -> int:
    ap = argparse.ArgumentParser(description="预测精度（唯一口径 v2）")
    ap.add_argument("--run", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--audit", action="store_true", help="逐条列出斩杀配对与入伤偏差")
    args = ap.parse_args()
    recs = load(None)
    if not recs:
        print("账本为空：先打一局")
        return 1
    if args.all:
        report(recs, None, audit=args.audit)
        return 0
    rid = args.run or last_run(recs)
    report([r for r in recs if r.get("run_id") == rid], rid, audit=args.audit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
