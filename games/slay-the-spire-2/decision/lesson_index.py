# -*- coding: utf-8 -*-
"""经验卡检索：从**已核实的失败案例**里抽「触发条件 → 建议动作」，每回合只注入命中的那几条。

为什么要有这个（2026-10-28，任务 3 的后续）
------------------------------------------
任务 3 把 run96–run106 的复盘逐条对过账本，发现两类问题：

1. **经验是散文**：`docs/LESSONS.md` 1600 多行、run 战报 40 多份，模型每回合不可能读，
   实际上一局只读了开头的 skill，后面的教训从没进过输入（run105 还是 30% 血进精英）。
2. **散文里混着没核实的话**：run105 旧版「同归于尽」「两瓶药都用在这场精英」都是错的。
   直接把这些文本塞进注入层，等于把错误记忆复制到每一局。

所以这里把「教训」变成**结构化卡片**（`logs/lessons.jsonl`）：

    {"id":"L105_eel", "kind":"case",
     "when":{"enemy_any":["TERROR_EEL"], "hp_pct_max":45},
     "t":"骇鳗精英：24/80 进场吃 18→6，只打全挡+药水（run105）",
     "why":"...", "src":{"run":"run105","file":"logs/decisions.jsonl","ts":"..."} }

- `when` 是**触发条件**（全 AND；列表内部 OR），字段见 `COND_KEYS`；
- `t` 是**建议动作**，≤60 字，只有它进上下文；
- `src` 是账本坐标，`tools/test_lesson_index.py` 会检查文件存在、`verified` 为真；
- 未核实（`verified:false`）的卡片**不注入**——宁可不说，也不给错记忆。

检索：`facts_from_ctx(ctx)` 把局面包压成事实，`match()` 打分（命中条件多者优先，
敌人/手牌这类具体条件额外加权），`render()` 取前 3 条并卡字节预算。

用法
----
    python tools/lesson_index.py --check              # 校验卡片（证据/字段/长度）
    python tools/lesson_index.py --all                # 列出全部卡片
    python tools/lesson_index.py --file tmp_ctx.json  # 用真实局面包试检索
    python tools/lesson_index.py --enemy TERROR_EEL --hp 24 --max-hp 80 --screen COMBAT
    python tools/lesson_index.py --screen MAP --node Elite --hp 24 --max-hp 80
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_PACK = os.path.dirname(_HERE)
ROOT = os.path.dirname(os.path.dirname(_PACK))
CARDS_PATH = os.path.join(ROOT, "logs", "lessons.jsonl")
DB = os.path.join(_PACK, "corpus", "db", "sts2.db")

VERSION = 1
MAX_N = 3
MAX_BYTES = 420
MAX_T = 60

# when 里允许的条件键。加新键要同时改 facts_from_ctx 和测试。
COND_KEYS = (
    "screen",          # 界面：COMBAT / MAP / SHOP / REST / REWARD / EVENT …
    "hp_pct_max", "hp_pct_min",   # 当前血量百分比（0-100）
    "enemy_any",       # 活着的敌人 id（任一命中）
    "enemy_type",      # elite / boss / normal（任一命中）
    "enemy_n_min", "enemy_n_max",
    "hand_any",        # 手牌 id（任一命中）
    "incoming_min",    # 本回合总入伤
    "potion_max",      # 剩余药水数
    "node_any",        # MAP 上可走的节点类型（任一命中）
    "act_min", "act_max", "floor_min", "floor_max",
)

# 条件越具体，权重越高：敌人/手牌 > 节点/敌人类型 > 血量/界面。
_BONUS = {"enemy_any": 2, "hand_any": 2, "node_any": 1, "enemy_type": 1}

# db 读不到时的兜底（只列这一季真遇到过的）。
_TYPE_FALLBACK = {
    "TERROR_EEL": "elite", "PHROG_PARASITE": "elite", "SKULKING_COLONY": "elite",
    "PHANTASMAL_GARDENER": "elite", "WRIGGLER": "elite", "BYRDONIS": "elite",
    "WATERFALL_GIANT": "boss", "CEREMONIAL_BEAST": "boss", "SOUL_FYSH": "boss",
    "LAGAVULIN_MATRIARCH": "boss",
}

_TYPES: dict[str, str] = {}


def enemy_type(eid: str) -> str:
    """enemy_type('TERROR_EEL') -> 'elite'。db 优先，读不到用兜底表。"""
    eid = str(eid or "")
    if eid in _TYPES:
        return _TYPES[eid]
    t = ""
    try:
        con = sqlite3.connect(DB)
        row = con.execute("select type from enemies where id=?", (eid,)).fetchone()
        con.close()
        t = str((row or [""])[0] or "").lower()
    except Exception:
        t = ""
    t = t if t in ("elite", "boss", "normal") else _TYPE_FALLBACK.get(eid, "")
    _TYPES[eid] = t
    return t


def load(path: str = CARDS_PATH) -> list[dict]:
    """读卡片。坏行跳过但记在 stderr，别让一行手滑毁掉整个注入。"""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception as e:  # noqa: BLE001
                print(f"WARN lessons.jsonl:{n} 坏行 {e}", file=sys.stderr)
    return out


# ---------------------------------------------------------------- 事实压缩
def _pct(hp, max_hp) -> float:
    try:
        hp, max_hp = float(hp), float(max_hp)
    except (TypeError, ValueError):
        return -1.0
    return 100.0 * hp / max_hp if max_hp > 0 else -1.0


def facts_from_ctx(d: dict) -> dict:
    """把 ctx 局面包压成匹配用的事实（战斗/非战斗都吃）。"""
    d = d or {}
    me = d.get("me") or {}
    run = d.get("run") or {}
    hp = me.get("hp", run.get("hp"))
    max_hp = me.get("max", run.get("max"))
    enemies = [str(e.get("id")) for e in (d.get("enemies") or []) if isinstance(e, dict)]
    nodes = []
    for o in ((d.get("choice") or {}).get("opts") or []):
        if isinstance(o, dict) and o.get("t"):
            nodes.append(str(o["t"]))
    hand = [str(c.get("id")) for c in (d.get("hand") or []) if isinstance(c, dict)]
    f = {
        "screen": str(d.get("screen") or ""),
        "hp": _int(hp), "max_hp": _int(max_hp), "hp_pct": _pct(hp, max_hp),
        "act": _int(run.get("act")), "floor": _int(run.get("f")),
        "enemies": enemies,
        "enemy_types": sorted({t for t in (enemy_type(e) for e in enemies) if t}),
        "enemy_n": len(enemies),
        "hand": hand,
        "potions": len(d.get("potions") or []),
        "node": nodes,
    }
    calc = d.get("calc") or {}
    if isinstance(calc, dict) and "inc" in calc:
        f["incoming"] = _int(calc.get("inc"))
    return f


def _int(v, d: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


# ---------------------------------------------------------------- 匹配
def _hit(cond: dict, facts: dict) -> tuple[bool, int]:
    """返回 (是否命中, 得分)。任一条件不满足即不命中（AND）。"""
    score = 0
    for k, want in cond.items():
        if k == "screen":
            if str(facts.get("screen") or "").upper() not in {str(x).upper() for x in want}:
                return False, 0
        elif k == "enemy_any":
            if not (set(facts.get("enemies") or []) & set(want)):
                return False, 0
            score += _BONUS["enemy_any"]
        elif k == "enemy_type":
            if not (set(facts.get("enemy_types") or []) & {str(x).lower() for x in want}):
                return False, 0
            score += _BONUS["enemy_type"]
        elif k == "hand_any":
            if not (set(facts.get("hand") or []) & set(want)):
                return False, 0
            score += _BONUS["hand_any"]
        elif k == "node_any":
            if not ({str(x).lower() for x in (facts.get("node") or [])}
                    & {str(x).lower() for x in want}):
                return False, 0
            score += _BONUS["node_any"]
        elif k == "hp_pct_max":
            if not (0 <= facts.get("hp_pct", -1) <= float(want)):
                return False, 0
        elif k == "hp_pct_min":
            if not (facts.get("hp_pct", -1) >= float(want) >= 0):
                return False, 0
        elif k == "enemy_n_min":
            if facts.get("enemy_n", 0) < int(want):
                return False, 0
        elif k == "enemy_n_max":
            if facts.get("enemy_n", 0) > int(want):
                return False, 0
        elif k == "incoming_min":
            # 没有 calc（非战斗）不能算「入伤小」，直接不命中。
            if facts.get("incoming") is None or facts["incoming"] < int(want):
                return False, 0
        elif k == "potion_max":
            if facts.get("potions", 0) > int(want):
                return False, 0
        elif k == "act_min":
            if facts.get("act", 0) < int(want):
                return False, 0
        elif k == "act_max":
            if facts.get("act", 99) > int(want):
                return False, 0
        elif k == "floor_min":
            if facts.get("floor", 0) < int(want):
                return False, 0
        elif k == "floor_max":
            if facts.get("floor", 99) > int(want):
                return False, 0
        else:  # 未知键：宁可不命中，也不猜
            return False, 0
        score += 1
    return True, score


def match(facts: dict, cards: list[dict] | None = None, limit: int = MAX_N) -> list[dict]:
    """打分排序：命中条件多者优先，同分按 prio 降序，再按 id 保证稳定。"""
    cards = load() if cards is None else cards
    hits = []
    for c in cards:
        if not c.get("verified"):
            continue
        ok, score = _hit(c.get("when") or {}, facts)
        if ok:
            hits.append((score, _int(c.get("prio"), 5), str(c.get("id")), c))
    hits.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [h[3] for h in hits[:limit]]


def render(matches: list[dict], limit_bytes: int = MAX_BYTES) -> list[dict]:
    """压成注入载荷 [{"id","t","s"}]，超字节预算就少给（宁少勿超）。"""
    out: list[dict] = []
    total = 0
    for c in matches:
        item = {"id": c["id"], "t": c["t"], "s": str((c.get("src") or {}).get("run") or "")}
        blob = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        if out and total + len(blob.encode("utf-8")) > limit_bytes:
            break
        out.append(item)
        total += len(blob.encode("utf-8")) + 1
    return out


def for_ctx(d: dict, cards: list[dict] | None = None) -> dict:
    """给 ctx 用：命中就返回 {"lesson_v":1,"lessons":[...]}，否则空 dict（不占字节）。"""
    if os.environ.get("STS2_LESSONS") == "0":
        return {}
    got = render(match(facts_from_ctx(d), cards))
    return {"lesson_v": VERSION, "lessons": got} if got else {}


# ---------------------------------------------------------------- 自检
def validate(cards: list[dict] | None = None) -> list[str]:
    cards = load() if cards is None else cards
    bad = []
    if not cards:
        return [f"没有卡片（{CARDS_PATH}）"]
    seen = set()
    for c in cards:
        cid = str(c.get("id") or "")
        if not cid:
            bad.append("有卡片缺 id")
            continue
        if cid in seen:
            bad.append(f"{cid}: id 重复")
        seen.add(cid)
        if c.get("kind") not in ("case", "rule"):
            bad.append(f"{cid}: kind 必须是 case/rule")
        when = c.get("when") or {}
        if not isinstance(when, dict) or not when:
            bad.append(f"{cid}: when 空")
        for k in when:
            if k not in COND_KEYS:
                bad.append(f"{cid}: 未知条件 {k}")
        t = str(c.get("t") or "")
        if not t:
            bad.append(f"{cid}: 缺注入文本 t")
        elif len(t) > MAX_T:
            bad.append(f"{cid}: t {len(t)} 字 > {MAX_T}")
        if c.get("kind") == "case":
            src = c.get("src") or {}
            if not c.get("verified"):
                bad.append(f"{cid}: case 但 verified!=true")
            if not str(src.get("run") or "").startswith("run"):
                bad.append(f"{cid}: src.run 缺失/格式错")
            f = str(src.get("file") or "")
            if not f or not os.path.exists(os.path.join(ROOT, f)):
                bad.append(f"{cid}: src.file 不存在 {f!r}")
            if not src.get("ts"):
                bad.append(f"{cid}: src.ts 缺失（账本坐标）")
    return bad


# ---------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser(description="经验卡检索（logs/lessons.jsonl）")
    ap.add_argument("--file", help="用 ctx 局面包 JSON 试检索")
    ap.add_argument("--enemy", help="逗号分隔的敌人 id")
    ap.add_argument("--hp", type=int)
    ap.add_argument("--max-hp", type=int, default=80)
    ap.add_argument("--screen", default="COMBAT")
    ap.add_argument("--node", help="逗号分隔的 MAP 节点类型（Elite/Rest/Shop…）")
    ap.add_argument("--hand", help="逗号分隔的手牌 id")
    ap.add_argument("--incoming", type=int)
    ap.add_argument("--potion", type=int, default=0)
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--all", action="store_true", help="列出全部卡片")
    ap.add_argument("--check", action="store_true", help="校验卡片")
    a = ap.parse_args()

    if a.check:
        bad = validate()
        if bad:
            print("FAIL")
            for b in bad:
                print(" -", b)
            return 1
        cards = load()
        print(f"OK {VERSION} {len(cards)} 条（case "
              f"{sum(1 for c in cards if c.get('kind') == 'case')} / rule "
              f"{sum(1 for c in cards if c.get('kind') == 'rule')}）")
        return 0

    if a.all:
        for c in load():
            print(f"{c['id']:22s} {json.dumps(c['when'], ensure_ascii=False)}")
            print(f"  {c['t']}")
        return 0

    if a.file:
        with open(a.file, encoding="utf-8-sig") as f:
            d = json.load(f)
        facts = facts_from_ctx(d)
    else:
        facts = {
            "screen": a.screen, "hp": a.hp, "max_hp": a.max_hp,
            "hp_pct": _pct(a.hp, a.max_hp), "act": 1, "floor": 0,
            "enemies": [x for x in (a.enemy or "").split(",") if x],
            "enemy_types": [], "enemy_n": 0, "hand": [x for x in (a.hand or "").split(",") if x],
            "potions": a.potion, "node": [x for x in (a.node or "").split(",") if x],
        }
        facts["enemy_types"] = sorted({t for t in (enemy_type(e) for e in facts["enemies"]) if t})
        facts["enemy_n"] = len(facts["enemies"])
        if a.incoming is not None:
            facts["incoming"] = a.incoming

    got = match(facts)
    if a.json:
        print(json.dumps({"facts": facts, "lessons": render(got)}, ensure_ascii=False, indent=1))
    else:
        print("facts:", json.dumps(facts, ensure_ascii=False))
        if not got:
            print("命中 0 条")
        for c in got:
            print(f"  [{c['id']}] {c['t']}  <- {c.get('src', {}).get('run')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
