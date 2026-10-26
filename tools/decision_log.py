# -*- coding: utf-8 -*-
"""决策账本：一次战斗决策一条 JSONL，另写每局 run summary。

设计原则
--------
* 只记录，不参与决策、不改状态、不发网络请求。
* 任何异常都在本模块吞掉：账本坏了不能影响出牌。
* `STS2_DECISION_LOG=0` 关闭；`STS2_DECISION_LOG_DIR` 可改目录（测试用）。
* `STS2_DECISION_STATE=0` 时不写 state 快照，只留摘要（省空间）。

一条记录至少包含：
  run_id / act_id / floor / turn / 输入 seq / 选中 steps
  候选 plans / forecast / outcome / err
  before / after 摘要 / 战斗 state 快照（可关）
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOG_DIR = os.path.join(ROOT, "logs")
DECISIONS_NAME = "decisions.jsonl"


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    return os.environ.get("STS2_DECISION_LOG", "1") != "0"


def log_dir() -> str:
    p = os.environ.get("STS2_DECISION_LOG_DIR") or DEFAULT_LOG_DIR
    return os.path.abspath(p)


def decisions_path() -> str:
    return os.path.join(log_dir(), DECISIONS_NAME)


def _run_id(st: dict) -> str:
    run = st.get("run") or {}
    rid = st.get("run_id") or run.get("run_id")
    return str(rid) if rid not in (None, "") else "unknown"


def _safe_id(s: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(s))
    return (out or "unknown")[:80]


def _act_id(st: dict):
    run = st.get("run") or {}
    raw = run.get("act_id", run.get("act"))
    return raw


def _act_display(st: dict):
    raw = _act_id(st)
    try:
        return int(raw) + 1
    except (TypeError, ValueError):
        return raw


def _floor(st: dict):
    return (st.get("run") or {}).get("floor")


def _turn(st: dict):
    c = st.get("combat") or {}
    t = c.get("turn", st.get("turn"))
    return _int(t, 0) if t is not None else None


def _screen(st: dict):
    return st.get("screen")


def _state_hash(st: dict) -> str:
    """只 hash 会影响战斗决策的字段，避免整包排序开销。"""
    c = st.get("combat") or {}
    run = st.get("run") or {}
    hand = []
    for x in c.get("hand") or []:
        if isinstance(x, dict):
            hand.append([x.get("card_id") or x.get("id"), x.get("energy_cost"),
                         x.get("upgraded"), x.get("playable")])
    enemies = []
    for x in (c.get("enemies") or []):
        if isinstance(x, dict):
            enemies.append([x.get("index"), x.get("current_hp"), x.get("block"),
                            x.get("intents")])
    p = c.get("player") or {}
    payload = {
        "run_id": _run_id(st), "floor": run.get("floor"), "act": _act_id(st),
        "turn": _turn(st), "hp": run.get("current_hp", p.get("current_hp")),
        "block": p.get("block"), "energy": p.get("energy"), "hand": hand,
        "enemies": enemies,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _cards_brief(cards) -> list:
    out = []
    for x in cards or []:
        if not isinstance(x, dict):
            continue
        out.append({
            "id": x.get("card_id") or x.get("id"),
            "n": x.get("name"),
            "c": x.get("energy_cost", x.get("cost")),
            "up": bool(x.get("upgraded") or x.get("is_upgraded")),
            "play": x.get("playable"),
        })
    return out


def _powers_brief(powers) -> list:
    out = []
    for p in powers or []:
        if not isinstance(p, dict):
            continue
        out.append({
            "id": p.get("power_id") or p.get("id"),
            "n": p.get("name"),
            "amt": p.get("amount", p.get("stacks", p.get("value"))),
        })
    return out


def ctx_summary(st: dict) -> dict:
    """给账本看的紧凑局面摘要（不是 ctx v3，不调用 ctx.build）。"""
    if not isinstance(st, dict):
        return {}
    run = st.get("run") or {}
    c = st.get("combat") or {}
    p = c.get("player") or {}
    enemies = []
    for i, e in enumerate(c.get("enemies") or []):
        if not isinstance(e, dict):
            continue
        intents = []
        for it in e.get("intents") or []:
            if not isinstance(it, dict):
                continue
            intents.append({
                "t": it.get("intent_type") or it.get("type"),
                "d": it.get("damage", it.get("value")),
                "h": it.get("hits"),
                "tot": it.get("total_damage"),
            })
        enemies.append({
            "i": e.get("index", i),
            "id": e.get("enemy_id") or e.get("id"),
            "n": e.get("name"),
            "hp": e.get("current_hp", e.get("hp")),
            "max": e.get("max_hp"),
            "blk": e.get("block"),
            "it": intents,
            "b": _powers_brief(e.get("powers")),
        })
    deck = run.get("deck") or []
    relics = run.get("relics") or []
    potions = run.get("potions") or []
    return {
        "screen": st.get("screen"),
        "run": {
            "run_id": _run_id(st),
            "floor": run.get("floor"),
            "act_id": _act_id(st),
            "act": _act_display(st),
            "asc": run.get("ascension"),
            "hp": run.get("current_hp", p.get("current_hp")),
            "max": run.get("max_hp", p.get("max_hp")),
            "gold": run.get("gold"),
            "deck_size": len(deck),
            "potions": [x.get("potion_id") for x in potions
                        if isinstance(x, dict) and (x.get("occupied") or x.get("potion_id"))],
            "relics": [x.get("relic_id") if isinstance(x, dict) else x for x in relics],
        },
        "turn": _turn(st),
        "me": {
            "hp": p.get("current_hp", run.get("current_hp")),
            "max": p.get("max_hp", run.get("max_hp")),
            "blk": p.get("block"),
            "e": p.get("energy"),
            "b": _powers_brief(p.get("powers")),
        },
        "hand": _cards_brief(c.get("hand")),
        "enemies": enemies,
        "available_actions": st.get("available_actions") or [],
    }


def compact_state(st: dict) -> dict:
    """保留重放一场战斗所需的字段，去掉地图/商店/事件等大块。"""
    if not isinstance(st, dict):
        return {}
    out = {}
    for k in ("screen", "run_id", "turn", "available_actions", "run", "combat", "piles"):
        if k in st:
            out[k] = copy.deepcopy(st[k])
    return out


def _plan_brief(plans) -> list:
    out = []
    for p in plans or []:
        if not isinstance(p, dict):
            continue
        out.append({
            "k": p.get("k"),
            "s": p.get("seq"),
            "d": p.get("dmg"),
            "take": p.get("taken"),
            "hp": p.get("hp"),
            "e": p.get("e"),
            "kill": p.get("kill"),
            "stun": p.get("stun"),
            "score": p.get("score"),
        })
    return out


def _append_jsonl(path: str, rec: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def _write_json(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


def _steps_key(steps) -> tuple:
    out = []
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        k = s.get("k")
        if k == "end":
            out.append(("end",))
        elif k == "p":
            out.append(("p", s.get("p"), s.get("t")))
        else:
            out.append((s.get("id"), s.get("t")))
    return tuple(out)


def _plan_key(plan: dict) -> tuple:
    out = []
    for s in plan.get("seq") or []:
        if not isinstance(s, (list, tuple)) or not s:
            continue
        if str(s[0]).startswith("@"):
            slot = str(s[0])[1:]
            try:
                slot = int(slot)
            except ValueError:
                pass
            out.append(("p", slot, s[1] if len(s) > 1 else None))
        else:
            out.append((s[0], s[1] if len(s) > 1 else None))
    return tuple(out)


def _chosen_plan(steps, plans) -> dict | None:
    key = tuple(x for x in _steps_key(steps) if x and x[0] != "end")
    if not key:
        return None
    for p in plans or []:
        if not isinstance(p, dict):
            continue
        if _plan_key(p) == key:
            return {"k": p.get("k"), "score": p.get("score"), "dmg": p.get("dmg"),
                    "taken": p.get("taken"), "hp": p.get("hp"), "e": p.get("e")}
    return None


def record_decision(
    st: dict,
    seq,
    lines,
    fx,
    *,
    outcome: str,
    err=None,
    steps=None,
    forecast=None,
    plans=None,
    batch=None,
    after=None,
    retargets=None,
    mode: str = "model",
) -> dict | None:
    """写一条决策记录；任何异常返回 None，绝不向调用方抛出。"""
    if not enabled():
        return None
    try:
        rec = {
            "v": 1,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "outcome": outcome,
            "run_id": _run_id(st),
            "act_id": _act_id(st),
            "act": _act_display(st),
            "floor": _floor(st),
            "turn": _turn(st),
            "state_hash": _state_hash(st),
            "ended": bool(steps and isinstance(steps[-1], dict)
                          and steps[-1].get("k") == "end"),
            "mid": bool(steps and not (isinstance(steps[-1], dict)
                                       and steps[-1].get("k") == "end")),
            "input": {
                "seq": seq,
                "say": lines,
                "fx": fx,
            },
            "steps": steps,
            "chosen_plan": _chosen_plan(steps, plans),
            "retargets": retargets,
            "plans": _plan_brief(plans),
            "forecast": forecast,
            "before": ctx_summary(st),
            "after": ctx_summary(after) if after else None,
            "batch": batch,
            "err": str(err)[:500] if err else None,
        }
        if os.environ.get("STS2_DECISION_STATE", "1") != "0":
            rec["state"] = compact_state(st)
        _append_jsonl(decisions_path(), rec)
        if after and after.get("screen") == "GAME_OVER":
            record_run_summary(after)
        return rec
    except Exception:
        return None


def record_rejection(st: dict, seq, err, *, stage: str, lines=None, fx=None) -> dict | None:
    return record_decision(
        st, seq, lines or [], fx or {}, outcome=stage, err=err,
        plans=None, after=None,
    )


def _decision_stats(run_id: str) -> dict:
    """从已写账本汇总：决策回合、掉血、药水使用、被拒次数。"""
    try:
        recs = read_decisions()
    except Exception:
        recs = []
    out = {"turns": 0, "rejected": 0, "hp_lost": 0, "by_floor": {},
           "potions_used": 0}
    for r in recs:
        if str(r.get("run_id")) != str(run_id):
            continue
        outcome = r.get("outcome")
        if outcome == "executed":
            out["turns"] += 1
            b = r.get("before") or {}
            a = r.get("after") or {}
            bhp = ((b.get("run") or {}).get("hp") if isinstance(b, dict) else None)
            ahp = ((a.get("run") or {}).get("hp") if isinstance(a, dict) else None)
            if isinstance(bhp, (int, float)) and isinstance(ahp, (int, float)):
                lost = max(0, int(bhp) - int(ahp))
                out["hp_lost"] += lost
                f = b.get("run", {}).get("floor") if isinstance(b, dict) else None
                key = str(f if f is not None else "?")
                out["by_floor"][key] = out["by_floor"].get(key, 0) + lost
            seq = ((r.get("input") or {}).get("seq") or [])
            for s in seq:
                if isinstance(s, dict) and ("p" in s):
                    out["potions_used"] += 1
        elif outcome in ("rejected_validate", "rejected_fx", "batch_error",
                         "stale_end", "missing_seq", "not_combat", "no_actions"):
            out["rejected"] += 1
    return out


def _sl_count() -> int | None:
    """SL 次数从动作 session 读；读不到就 None，不猜。"""
    try:
        import sts2  # 延迟导入，避免 decision_log 反向依赖
        entries = sts2._session_entries()
        return sum(1 for e in entries if e.get("action") == "save_and_quit")
    except Exception:
        return None


def record_run_summary(st: dict) -> dict | None:
    """GAME_OVER 时写每局 summary + 最新指针；重复调用会覆盖同一局。"""
    if not enabled():
        return None
    try:
        if st.get("screen") != "GAME_OVER":
            return None
        run = st.get("run") or {}
        go = st.get("game_over") or {}
        win = go.get("is_victory")
        if win is True:
            result = "victory"
        elif win is False:
            result = "defeat"
        else:
            result = "unknown"
        rid = _safe_id(_run_id(st))
        deck = run.get("deck") or []
        relics = run.get("relics") or []
        potions = run.get("potions") or []
        stats = _decision_stats(_run_id(st))
        summary = {
            "v": 1,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "run_id": _run_id(st),
            "result": result,
            "is_victory": win,
            "floor": run.get("floor", go.get("floor")),
            "act_id": _act_id(st),
            "act": _act_display(st),
            "ascension": run.get("ascension"),
            "hp": run.get("current_hp", go.get("hp")),
            "max_hp": run.get("max_hp"),
            "gold": run.get("gold"),
            "deck_size": len(deck),
            "deck": [x.get("card_id") if isinstance(x, dict) else x for x in deck],
            "relics": [x.get("relic_id") if isinstance(x, dict) else x for x in relics],
            "potions": [x.get("potion_id") for x in potions
                        if isinstance(x, dict) and (x.get("occupied") or x.get("potion_id"))],
            "decisions": stats["turns"],
            "rejected": stats["rejected"],
            "hp_lost": stats["hp_lost"],
            "hp_lost_by_floor": stats["by_floor"],
            "potions_used": stats["potions_used"],
            "sl": _sl_count(),
        }
        path = os.path.join(log_dir(), f"run_summary_{rid}.json")
        _write_json(path, summary)
        _write_json(os.path.join(log_dir(), "run_summary.json"), summary)
        _append_jsonl(os.path.join(log_dir(), "run_summaries.jsonl"), summary)
        return summary
    except Exception:
        return None


def read_decisions(path: str | None = None) -> list[dict]:
    p = path or decisions_path()
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out
