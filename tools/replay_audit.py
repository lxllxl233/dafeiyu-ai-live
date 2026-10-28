# -*- coding: utf-8 -*-
"""历史场景回放 + 回归审计（不改动游戏、不调用 mod）。

拿 `logs/decisions.jsonl` 里**每一行决策自带的原始 state**（不是摘要），在今天这套
工具链上重放同一回合，量四件事：

1. **规则注入**：这个局面今天会注入哪几条硬闸（`hard_rules` 5 条常驻）+ 哪几条规则
   的**条件真的成立**（血闸/自伤闸/药水闸/伤害闸）；
2. **经验检索**：这个局面今天会注入哪几张经验卡（`lesson_index`）；
3. **动作模拟**：当时执行器的 `forecast`（预测）与实际 `after`（落地）差多少；
4. **回执/拦截**：当时被判 `executed` 的牌序，今天的 `validate_plan` + `_guards` 还通不通；
   当时被拒的牌序，今天还拒不拒（回归：保护不能退化）。

指标口径全部写在 `docs/REPLAY_AUDIT.md`，**违规判定是审计侧对规则文字的解读**，
不是引擎行为；每条判定都能回放到具体行（ts + rule id + 触发值）。

用法：
    python tools/replay_audit.py                      # 本赛季 run96–run106
    python tools/replay_audit.py --all                # 全部账本行
    python tools/replay_audit.py --json logs/replay_audit.json
    python tools/replay_audit.py --md docs/REPLAY_AUDIT.md
    python tools/replay_audit.py --runs 5UMKQR2A1K    # 指定 run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("STS2_DECISION_LOG", "0")

import act  # noqa: E402
import hard_rules  # noqa: E402
import lesson_index  # noqa: E402

LEDGER = os.path.join(ROOT, "logs", "decisions.jsonl")

# 本赛季（有复盘、有账本的 11 局）
SEASON = {
    "run96": "BSZ3HC4QR4", "run97": "V020HZGZTM", "run98": "NL379UQDMC",
    "run99": "TVNG2SEZZH", "run100": "5M1JDNLKXN", "run101": "5V1DCM61ZS",
    "run102": "108VPCY1CE", "run103": "EXH7P5V56J", "run104": "M8D7WN3957",
    "run105": "5UMKQR2A1K", "run106": "2CVLNZLG0B",
}
RUN_NAME = {v: k for k, v in SEASON.items()}

SELF_DMG_CARDS = ("BLOOD_WALL", "BLOODLETTING", "OFFERING", "BREAKTHROUGH")
POTION_STEP = "p"


# ---------------------------------------------------------------- 事实
def _int(v, d: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def facts_from_state(st: dict) -> dict:
    """把原始 /state 压成经验卡/规则用的事实（和 lesson_index.facts_from_ctx 同构）。"""
    st = st or {}
    run = st.get("run") or {}
    c = st.get("combat") or {}
    p = c.get("player") or {}
    hp = _int(p.get("current_hp", run.get("current_hp")))
    mx = _int(p.get("max_hp", run.get("max_hp"))) or 80
    enemies, inc, ekeys = [], 0, []
    for e in (c.get("enemies") or []):
        if not isinstance(e, dict):
            continue
        if e.get("is_alive") is False or _int(e.get("current_hp")) <= 0:
            continue
        eid = str(e.get("enemy_id") or e.get("id") or "")
        if not eid:
            continue
        ekeys.append(eid)
        enemies.append({"id": eid, "hp": _int(e.get("current_hp")), "max": _int(e.get("max_hp"))})
        for it in (e.get("intents") or []):
            if not isinstance(it, dict):
                continue
            tot = it.get("total_damage")
            if tot is None:
                tot = _int(it.get("damage")) * max(1, _int(it.get("hits"), 1))
            inc += _int(tot)
    hand = [str(x.get("card_id") or x.get("id") or "") for x in (c.get("hand") or [])
            if isinstance(x, dict)]
    potions = [x for x in (run.get("potions") or [])
               if isinstance(x, dict) and (x.get("occupied") or x.get("potion_id"))]
    nodes = []
    for n in ((st.get("map") or {}).get("nodes") or []):
        if isinstance(n, dict) and n.get("is_available"):
            nodes.append(str(n.get("node_type") or "?"))
    act_no = _int(run.get("act"))
    floor = _int(run.get("floor"))
    turn = _int(st.get("turn") or c.get("turn"), 1)
    return {
        "screen": str(st.get("screen") or ""),
        "me": {"hp": hp, "max": mx, "blk": _int(p.get("block")), "e": _int(p.get("energy"))},
        "run": {"hp": hp, "max": mx, "act": act_no, "f": floor, "floor": floor},
        "combat": {"turn": turn},
        "enemies": enemies,
        "enemy_ids": ekeys,
        "hand": [{"id": x} for x in hand if x],
        "hand_ids": [x for x in hand if x],
        "potions": potions,
        "choice": {"opts": [{"t": t} for t in nodes]},
        "calc": {"inc": inc},
        "incoming": inc,
        "hp_pct": 100.0 * hp / mx,
    }


def enemy_rank(ids: list[str]) -> str:
    """一场战斗里最高的敌人类别：boss > elite > normal。"""
    ranks = {lesson_index.enemy_type(i) for i in ids}
    for r in ("boss", "elite"):
        if r in ranks:
            return r
    return "normal" if ranks else ""


def is_boss(ids: list[str]) -> bool:
    return enemy_rank(ids) == "boss"


def is_elite(ids: list[str]) -> bool:
    return enemy_rank(ids) == "elite"


def step_cards(row: dict) -> list[str]:
    return [str(s.get("id") or "") for s in (row.get("steps") or [])
            if isinstance(s, dict) and s.get("k") == "c"]


def step_potions(row: dict) -> list[str]:
    return [str(s.get("id") or "") for s in (row.get("steps") or [])
            if isinstance(s, dict) and s.get("k") == POTION_STEP]


# ---------------------------------------------------------------- 规则判定
# 每条返回 (applies, violated, note)。这是**审计侧解读**，见 docs/REPLAY_AUDIT.md。
def rule_dmg(f: dict, row: dict, fc: dict) -> tuple[bool, bool, str]:
    inc = _int(f.get("incoming"))
    if inc < 15:
        return False, False, ""
    take = _int(fc.get("take"), 0)
    killed = bool(fc.get("kill"))
    pot = bool(step_potions(row))
    if fc.get("clear") or fc.get("approx"):
        return True, False, "未建模/清场不判"
    bad = take >= 15 and not killed
    return True, bad, f"入伤{inc} 净掉{take}" + (" 有斩杀" if killed else "") + (
        "（已喝药仍掉）" if pot and bad else "")


def rule_hp_entry(f: dict, row: dict) -> tuple[bool, bool, str]:
    """血闸的**进战**面：这一回合是不是一场精英/Boss 的第一回合，且血低于阈值。

    注意：能证明「进战时血量」；不能证明「是不是玩家主动选的节点」（事件/被推图也算），
    所以单独记 `entry_low`，不计入决策违规率。
    """
    if not row.get("_fight_start"):
        return False, False, ""
    ids = f.get("enemy_ids") or []
    pct = float(f.get("hp_pct") or 0)
    if is_elite(ids) and pct < 60:
        return True, True, f"精英进战 {pct:.0f}%<60%"
    if is_boss(ids) and pct < 70:
        return True, True, f"Boss 进战 {pct:.0f}%<70%"
    return False, False, ""


def rule_self(f: dict, row: dict) -> tuple[bool, bool, str]:
    pct = float(f.get("hp_pct") or 0)
    hand = set(f.get("hand_ids") or [])
    risky = sorted(hand & set(SELF_DMG_CARDS))
    if pct > 40 or not risky:
        return False, False, ""
    played = [c for c in step_cards(row) if c in SELF_DMG_CARDS]
    return True, bool(played), f"{pct:.0f}% 手握 {'/'.join(risky)}" + (
        f"，仍打 {'/'.join(played)}" if played else "，没打")


def rule_boss_hp(f: dict, row: dict) -> tuple[bool, bool, str]:
    if not row.get("_fight_start"):
        return False, False, ""
    if not is_boss(f.get("enemy_ids") or []):
        return False, False, ""
    pct = float(f.get("hp_pct") or 0)
    return True, pct < 70, f"Boss 首回合 {pct:.0f}%"


def rule_potion(f: dict, row: dict) -> tuple[bool, bool, str]:
    """药水闸的 Bot-侧：Boss 战带了药、整场没喝 = 违反「Boss 不省药」。"""
    if not row.get("_fight_start") or not is_boss(f.get("enemy_ids") or []):
        return False, False, ""
    n = len(f.get("potions") or [])
    if n < 1:
        return False, False, ""
    used = row.get("_fight_potions") or []
    return True, len(used) == 0, f"带 {n} 瓶，整场用 {len(used)} 瓶"


RULE_CHECKS = (
    ("dmg", rule_dmg),
    ("self", rule_self),
    ("potion", rule_potion),
    ("boss", rule_boss_hp),
)


# ---------------------------------------------------------------- 今天重放
def today_verdict(row: dict) -> dict:
    """用今天的 validate_plan/forecast/_guards 重放同一 seq（不发请求）。"""
    st = row.get("state") or {}
    inp = row.get("input") or {}
    seq = inp.get("seq") or []
    # v22 起账本会记 allow；老行没有这个键 → 无法区分「护栏漏了」和「模型显式豁免」。
    allow = {str(k): bool(v) for k, v in (inp.get("allow") or {}).items()
             if str(k).startswith("allow")}
    out = {"ok": None, "err": "", "guard": None, "warn": None, "fx": {}, "took": "",
           "allow_known": "allow" in inp, "allow": allow}
    if not isinstance(st, dict) or not st or st.get("screen") != "COMBAT" or not seq:
        out["took"] = "skip"
        return out
    try:
        ok, err, sm, steps, ended = act.validate_plan(st, seq)
    except Exception as e:  # noqa: BLE001
        out["ok"] = False
        out["err"] = f"{type(e).__name__}: {e}"
        return out
    if not ok:
        out["ok"] = False
        out["err"] = err
        return out
    out["ok"] = True
    try:
        fc = act.forecast(st, sm, steps, ended)
        out["fx"] = fc
        guard_err, meta = act._guards(st, sm, ended, allow,
                                      played=act._played_counts(st, seq))
        out["guard"] = guard_err
        out["warn"] = (meta or {}).get("warn")
        out["fx_err"] = act.fx_error(inp.get("fx") or {}, fc)
        out["ovf"] = act.clear_overflow_error(steps, getattr(sm, "cleared_at", None))
    except Exception as e:  # noqa: BLE001
        out["err"] = f"{type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------- 模拟一致性
def sim_gap(row: dict) -> dict:
    """当时的 forecast 预测 vs after 落地：hp/take 差多少。"""
    fc = row.get("forecast") or {}
    b = row.get("before") or {}
    a = row.get("after") or {}
    me_b = b.get("me") or {}
    me_a = a.get("me") or {}
    out = {"cmp": False, "hp_pred": None, "hp_got": None, "take_pred": None, "take_got": None}
    if not fc or not me_a or "hp" not in me_a or "hp" not in me_b:
        return out
    if row.get("outcome") != "executed":
        return out
    for flag in ("clear", "approx", "revive", "tick", "draw_unknown"):
        if fc.get(flag):
            out["skip"] = flag
            return out
    hp_pred = _int(fc.get("hp"), -1)
    hp_b = _int(me_b.get("hp"))
    hp_a = _int(me_a.get("hp"))
    if hp_a > hp_b:
        out["skip"] = "heal"      # 战后回血/遗物回复：这一手不可对账
        return out
    take_pred = _int(fc.get("take"), -1) if "take" in fc else None
    out.update({"cmp": True, "hp_pred": hp_pred, "hp_got": hp_a,
                "take_pred": take_pred, "take_got": hp_b - hp_a})
    # 分类：预测死亡 vs 实际；预测血量 vs 实际（分保守/危险方向）
    if hp_pred < 0:
        out["cat"] = "death" if hp_a == 0 else "death_missed"   # 预测死→真死 / 预测死→没死
        out["bad"] = out["cat"] == "death_missed"
        return out
    if hp_pred == hp_a:
        out["cat"] = "ok"
        out["bad"] = False
        return out
    if hp_pred < hp_a:
        out["cat"], out["bad"] = "conservative", True     # 预测更惨：方向安全
    else:
        out["cat"], out["bad"] = "optimistic", True       # 预测更好：会误判「扛得住」，要盯
    return out


# ---------------------------------------------------------------- 今天的计划
def _plan_cards(p: dict) -> list:
    seq = p.get("s") or p.get("seq") or []
    out = []
    for x in seq:
        if isinstance(x, (list, tuple)):
            out.append(str(x[0]) if x else "")
        elif isinstance(x, dict):
            out.append(str(x.get("c") or x.get("id") or ""))
    return out


def plan_check(row: dict) -> dict:
    """今天这套管线在同一局面推荐的牌序，会不会又踩硬闸（规则→计划的最后一公里）。"""
    st = row.get("state") or {}
    if not isinstance(st, dict) or st.get("screen") != "COMBAT":
        return {"skip": "非战斗"}
    import ctx as ctxmod  # 延迟导入：只有 --plans 才需要跑 sim 搜索

    try:
        d = ctxmod.build_with_mem(st, {})
    except Exception as e:  # noqa: BLE001
        return {"skip": f"构建失败 {type(e).__name__}"}
    f = facts_from_state(st)
    pct = float(f.get("hp_pct") or 0)
    if pct > 40:                      # 规则只管 ≤40%：高血交自伤牌不算踩线
        return {"skip": "血够"}
    hand = set(f.get("hand_ids") or [])
    risky = sorted(hand & set(SELF_DMG_CARDS))
    if not risky:
        return {"skip": "手里没有自伤牌"}
    plans = d.get("plans") or []
    if isinstance(plans, dict):
        plans = list(plans.values())
    plans = [p for p in plans if isinstance(p, dict)]
    bad = []
    for p in plans:
        cards = _plan_cards(p)
        if any(c in SELF_DMG_CARDS for c in cards):
            bad.append(p.get("k"))
    clean = [p.get("k") for p in plans if p not in bad and
             not any(c in SELF_DMG_CARDS for c in _plan_cards(p))]
    best = plans[0].get("k") if plans else None
    return {"hp_pct": pct, "risky_hand": risky, "plans": len(plans),
            "self_in_plan": bad, "self_in_best": best in bad,
            "has_alt": bool(clean), "alt": clean[:4],
            "rules": len(d.get("rules") or []),
            "lessons": [x.get("id") for x in (d.get("lessons") or [])]}


def load_rows(path=LEDGER) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def annotate(rows: list[dict]) -> None:
    """给每行挂上：fight 起点、本场战斗的药水用量。"""
    last_sig = None
    cur = []
    for r in rows:
        st = r.get("state") or {}
        c = st.get("combat") or {}
        ids = tuple(sorted(str(e.get("enemy_id") or e.get("id") or "")
                           for e in (c.get("enemies") or []) if isinstance(e, dict)))
        sig = (r.get("run_id"), r.get("floor"), ids)
        turn = _int(st.get("turn") or c.get("turn"), 1)
        r["_fight_start"] = sig != last_sig or turn == 1
        if sig != last_sig:
            cur = []
            last_sig = sig
        pot = step_potions(r)
        cur.extend(pot)
        r["_fight_potions"] = list(cur)


def audit(rows: list[dict], *, check_today: bool = True) -> dict:
    total = Counter()
    per_run = defaultdict(Counter)
    violations: list[dict] = []
    lessons: list[dict] = []
    today_changes: list[dict] = []
    gap_rows: list[dict] = []

    for r in rows:
        run = RUN_NAME.get(str(r.get("run_id")), str(r.get("run_id")))
        st = r.get("state") or {}
        total[f"历史:{r.get('outcome')}"] += 1
        per_run[run][f"历史:{r.get('outcome')}"] += 1
        if st.get("screen") != "COMBAT":
            total["非战斗行"] += 1
            per_run[run]["非战斗行"] += 1
            continue
        total["行数"] += 1
        per_run[run]["行数"] += 1
        f = facts_from_state(st)
        fc = r.get("forecast") or {}

        # 1) 规则：5 条常驻 + 条件成立的那几条
        total["规则注入条数"] += len(hard_rules.for_ctx())
        for rid, fn in RULE_CHECKS:
            applies, bad, note = fn(f, r, fc) if fn is rule_dmg else fn(f, r)
            if not applies:
                continue
            total[f"规则触发:{rid}"] += 1
            per_run[run][f"规则触发:{rid}"] += 1
            if bad:
                total[f"规则违反:{rid}"] += 1
                per_run[run][f"规则违反:{rid}"] += 1
                violations.append({"run": run, "ts": r.get("ts"), "rule": rid,
                                   "note": note, "floor": r.get("floor"),
                                   "turn": f.get("combat", {}).get("turn"),
                                   "outcome": r.get("outcome")})
        # 血闸进战面单独计（不计入违规率）
        ap, bad, note = rule_hp_entry(f, r)
        if ap:
            total["血闸反例(进战)"] += 1
            per_run[run]["血闸反例(进战)"] += 1
            violations.append({"run": run, "ts": r.get("ts"), "rule": "hp_entry",
                               "note": note, "floor": r.get("floor"), "turn": 1,
                               "outcome": r.get("outcome")})

        # 2) 经验
        hit = (lesson_index.for_ctx(f) or {}).get("lessons") or []
        if hit:
            total["经验命中行"] += 1
            total["经验注入条数"] += len(hit)
            per_run[run]["经验命中行"] += 1
            for c in hit:
                lessons.append({"run": run, "ts": r.get("ts"), "id": c.get("id")})

        # 3) 模拟一致性
        g = sim_gap(r)
        if g.get("cmp"):
            total["可对账行"] += 1
            cat = g.get("cat") or "?"
            total[f"模拟:{cat}"] += 1
            per_run[run][f"模拟:{cat}"] += 1
            if g.get("bad"):
                total["模拟偏差"] += 1
                per_run[run]["模拟偏差"] += 1
                gap_rows.append({"run": run, "ts": r.get("ts"), **g})

        # 4) 今天重放
        if check_today:
            v = today_verdict(r)
            r["_today"] = v
            if v.get("took") == "skip":
                total["重放跳过"] += 1
            elif v.get("ok") is None:
                total["重放异常"] += 1
                today_changes.append({"run": run, "ts": r.get("ts"), "why": "异常",
                                      "err": v.get("err"), "hist": r.get("outcome")})
            else:
                hist = r.get("outcome")
                if hist == "executed" and not v["ok"]:
                    total["回归:历史执行今天被拒"] += 1
                    today_changes.append({"run": run, "ts": r.get("ts"), "why": "今天拒绝",
                                          "err": v.get("err"), "hist": hist})
                elif hist in ("rejected_validate", "rejected_guard", "rejected_fx",
                              "rejected_clear_overflow") and v["ok"] and not (
                        v.get("guard") or v.get("fx_err") or v.get("ovf")):
                    total["回归:历史被拒今天全放行"] += 1
                    today_changes.append({"run": run, "ts": r.get("ts"), "why": "今天放行",
                                          "hist": hist})
                if v["ok"] and v.get("guard"):
                    if hist == "rejected_guard":
                        total["护栏保持:guard"] += 1
                    elif v.get("allow_known"):
                        total["今天新拦:guard"] += 1
                        per_run[run]["今天新拦:guard"] += 1
                        today_changes.append({"run": run, "ts": r.get("ts"),
                                              "why": "guard(新)", "err": v["guard"],
                                              "hist": hist})
                    else:
                        total["护栏疑漏:allow 未入账"] += 1
                        per_run[run]["护栏疑漏"] += 1
                if v["ok"] and v.get("fx_err"):
                    if hist == "rejected_fx":
                        total["护栏保持:fx"] += 1
                    elif v.get("allow_known"):
                        total["今天新拦:fx"] += 1
                    else:
                        total["fx 疑漏:allow 未入账"] += 1
                if v["ok"] and v.get("warn") and not v.get("guard"):
                    total["新增警告:warn"] += 1

    return {"total": dict(total), "per_run": {k: dict(v) for k, v in per_run.items()},
            "violations": violations, "lessons": lessons,
            "today_changes": today_changes, "gaps": gap_rows}


def summarize(res: dict) -> str:
    t = res["total"]
    lines = []
    n = t.get("行数", 0)
    hist_bad = sum(v for k, v in t.items()
                   if k.startswith("历史:") and k != "历史:executed")
    lines.append(f"回放行数 {n}（非战斗 {t.get('非战斗行', 0)} 行跳过）")
    lines.append(f"无效动作（历史判定）：{hist_bad} / {n} 行"
                 f"（{100.0 * hist_bad / max(1, n):.1f}%）")
    for k in sorted(k for k in t if k.startswith("历史:") and k != "历史:executed"):
        lines.append(f"  {k[3:]} {t[k]}")
    lines.append(f"规则注入：每行 {t.get('规则注入条数', 0) / max(1, n):.1f} 条（应为 5.0）")
    tot_ap = sum(v for k, v in t.items() if k.startswith("规则触发:"))
    tot_bad = sum(v for k, v in t.items() if k.startswith("规则违反:"))
    lines.append(f"规则触发 {tot_ap} 次 / 违反 {tot_bad} 次"
                 f"（违规率 {100.0 * tot_bad / tot_ap:.1f}%）" if tot_ap else "规则触发 0 次")
    for rid in ("dmg", "self", "potion", "boss"):
        ap = t.get(f"规则触发:{rid}", 0)
        bad = t.get(f"规则违反:{rid}", 0)
        if ap:
            lines.append(f"  {rid}: 触发 {ap} / 违反 {bad}"
                         f"（{100.0 * bad / ap:.0f}%）")
    lines.append(f"血闸反例（进战时已低于阈值）{t.get('血闸反例(进战)', 0)} 次")
    lines.append(f"经验：命中 {t.get('经验命中行', 0)} 行 / 注入 {t.get('经验注入条数', 0)} 条")
    cmp_n = t.get("可对账行", 0)
    bad_n = t.get("模拟偏差", 0)
    lines.append(f"模拟对账 {cmp_n} 行 / 不一致 {bad_n} 行"
                 f"（{100.0 * bad_n / max(1, cmp_n):.1f}%）"
                 f"｜一致 {t.get('模拟:ok', 0)}、预测死且真死 {t.get('模拟:death', 0)}、"
                 f"保守 {t.get('模拟:conservative', 0)}、危险 {t.get('模拟:optimistic', 0)}、"
                 f"预测死但没死 {t.get('模拟:death_missed', 0)}")
    lines.append(f"今天重放：历史执行今天被拒 {t.get('回归:历史执行今天被拒', 0)}、"
                 f"历史被拒今天全放行 {t.get('回归:历史被拒今天全放行', 0)}")
    lines.append(f"  护栏保持 guard {t.get('护栏保持:guard', 0)} / fx {t.get('护栏保持:fx', 0)}；"
                 f"今天新拦 guard {t.get('今天新拦:guard', 0)} / fx {t.get('今天新拦:fx', 0)}；"
                 f"allow 未入账的疑漏 {t.get('护栏疑漏:allow 未入账', 0)}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="全部账本行（默认只跑本赛季 11 局）")
    ap.add_argument("--runs", nargs="*", default=None, help="指定 run_id（默认本赛季）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 行（抽样）")
    ap.add_argument("--json", default="", help="把结果写到 json")
    ap.add_argument("--md", default="", help="把摘要写成 markdown")
    ap.add_argument("--no-today", action="store_true", help="跳过今天重放（更快）")
    ap.add_argument("--plans", action="store_true",
                    help="再跑一遍今天的 ctx：看推荐牌序里还有没有自伤牌（规则→计划的最后一公里）")
    ap.add_argument("--show", type=int, default=12, help="打印多少条明细")
    args = ap.parse_args()

    rows = load_rows()
    if args.runs:
        want = set(args.runs)
        rows = [r for r in rows if str(r.get("run_id")) in want]
    elif not args.all:
        want = set(SEASON.values())
        rows = [r for r in rows if str(r.get("run_id")) in want]
    if args.limit:
        rows = rows[:args.limit]
    rows.sort(key=lambda r: (str(r.get("ts")), str(r.get("run_id"))))
    annotate(rows)

    res = audit(rows, check_today=not args.no_today)
    print(summarize(res))
    print()
    print("按局：")
    for run in sorted(res["per_run"], key=lambda k: (k not in SEASON, k)):
        c = res["per_run"][run]
        bad = sum(v for k, v in c.items() if k.startswith("规则违反:"))
        ap_n = sum(v for k, v in c.items() if k.startswith("规则触发:"))
        print(f"  {run:<10} 行 {c.get('行数', 0):>4}  规则触发 {ap_n:>3}  违反 {bad:>3}"
              f"  血闸反例 {c.get('血闸反例(进战)', 0):>2}  经验行 {c.get('经验命中行', 0):>3}"
              f"  模拟偏差 {c.get('模拟偏差', 0):>2}"
              f"  护栏保持 {c.get('护栏保持:guard', 0):>2}")

    if res["violations"] and args.show:
        print()
        print(f"规则违反明细（前 {args.show} 条）：")
        for v in [x for x in res["violations"] if x["rule"] != "hp_entry"][:args.show]:
            print(f"  {v['run']} {v['ts']} f{v['floor']} turn{v['turn']} [{v['rule']}] {v['note']}")
    if res["today_changes"] and args.show:
        print()
        print(f"今天重放变化（前 {args.show} 条）：")
        for v in res["today_changes"][:args.show]:
            print(f"  {v['run']} {v['ts']} {v['why']}: {str(v.get('err'))[:90]}")
    if res["gaps"] and args.show:
        print()
        print(f"模拟偏差明细（前 {args.show} 条，危险方向优先）：")
        for g in sorted(res["gaps"], key=lambda x: x.get("cat") != "optimistic")[:args.show]:
            print(f"  {g['run']} {g['ts']} [{g.get('cat')}] 预测 hp{g['hp_pred']}/掉{g['take_pred']}"
                  f" 实际 hp{g['hp_got']}/掉{g['take_got']}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
        print(f"\nJSON → {args.json}")
    if args.plans:
        print()
        print("规则→计划（≤40% 血、手握自伤牌）：今天的推荐线还含自伤牌的行")
        n_chk = n_bad = n_alt = 0
        for r in rows:
            p = plan_check(r)
            if p.get("skip"):
                continue
            n_chk += 1
            if p["self_in_plan"]:
                n_bad += 1
                n_alt += 1 if p.get("has_alt") else 0
                print(f"  {RUN_NAME.get(str(r.get('run_id')), r.get('run_id'))} {r.get('ts')} "
                      f"{p['hp_pct']:.0f}% 手牌 {p['risky_hand']} → 含自伤 {p['self_in_plan']}"
                      f"{'  [best]' if p['self_in_best'] else ''}"
                      f"  可替代线 {p['alt'] if p.get('has_alt') else '无'}")
        print(f"  共 {n_chk} 行可比：{n_bad} 行的推荐线仍含自伤牌"
              f"（其中 {n_alt} 行本来还有不自伤的替代线）")
    if args.md:
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write("# 历史回放审计结果（tools/replay_audit.py 生成）\n\n")
            fh.write("```\n" + summarize(res) + "\n```\n")
        print(f"MD → {args.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
