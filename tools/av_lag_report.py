"""A/V 对齐报表：发布 → 起播的延迟（按页面角色/前端版本分段）+ 过期丢弃统计。

数据源就是仓库里已有的两份观测日志（不新增埋点）：
  logs/av_client.jsonl  sts2 客户端：say_begin / prewarm_end / published（带 cid）
  logs/av_marks.jsonl   页面自报：audio（起播，带 dur）/ audio_end / skip(why) / hi(ver)

用法：
  python -X utf8 tools/av_lag_report.py            # 全量 + 最近 20 条
  python -X utf8 tools/av_lag_report.py --since "2026-10-27T21:10"
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")


def rows(name: str) -> list[dict]:
    out = []
    path = os.path.join(LOGS, name)
    try:
        f = open(path, encoding="utf-8")
    except OSError:
        return out
    with f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def parse_ts(s: str) -> float:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M").timestamp()


def stats(name: str, vals: list[float]) -> str:
    if not vals:
        return f"{name:12s} n=0"
    v = sorted(vals)
    n = len(v)
    p90 = v[min(n - 1, int(n * 0.9))]
    return (f"{name:12s} n={n:4d} median={statistics.median(v):5.2f}s "
            f"p90={p90:5.2f}s max={v[-1]:5.2f}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="")
    a = ap.parse_args()
    since = parse_ts(a.since) if a.since else 0.0

    cl = rows("av_client.jsonl")
    mk = rows("av_marks.jsonl")

    pub: dict[str, float] = {}
    chars: dict[str, int] = {}
    pre_ms: dict[str, float] = {}
    for r in cl:
        cid = r.get("cid")
        if not cid or r.get("t", 0) < since:
            continue
        if r.get("ev") == "published":
            pub[cid] = r["t"]
        elif r.get("ev") in ("say_begin", "prewarm_begin") and r.get("chars"):
            chars.setdefault(cid, int(r["chars"]))
        elif r.get("ev") == "prewarm_end" and isinstance(r.get("ms"), (int, float)):
            pre_ms[cid] = float(r["ms"]) / 1000.0

    # 每个前端版本的首个 hi 时刻：之后用新版前端，之前是旧版
    ver_at: dict[str, float] = {}
    for r in mk:
        if r.get("ev") == "hi" and r.get("ver"):
            ver_at[os.path.basename(str(r.get("ver")))] = min(
                ver_at.get(str(r.get("ver")), 1e18), r["t"])
    new_js_at = min(ver_at.values()) if ver_at else float("inf")

    # 同一句话重复出现时，页面有自己的内存缓存（mamboCache）→ 起播几乎是瞬时的。
    # 「解说及不及时」要看的只有**新句子**，所以按 text 去重，只留每句第一次。
    text_of: dict[str, str] = {}
    for r in mk:
        if r.get("ev") == "comment" and r.get("cid") and r.get("speech"):
            text_of.setdefault(str(r["cid"]), str(r["speech"]))
    first_cid: dict[str, str] = {}
    for cid in sorted(text_of, key=lambda c: pub.get(c, 0.0)):
        first_cid.setdefault(text_of[cid], cid)
    novel = set(first_cid.values())

    per_role: dict[str, list[float]] = collections.defaultdict(list)
    per_role_new: dict[str, list[float]] = collections.defaultdict(list)
    queue_wait_by_cid: dict[str, float] = {}
    prewarm_vals: list[float] = []
    skips = collections.Counter()
    late_by_est: list[float] = []
    first_play: dict[str, dict] = {}
    for r in mk:
        cid = r.get("cid")
        if not cid or cid not in pub:
            continue
        if r.get("ev") == "skip":
            skips[str(r.get("why") or "-")] += 1
        if r.get("ev") == "audio" and (r.get("dur") or 0) > 0:
            role = str(r.get("role") or "-")
            lag = r["t"] - pub[cid]
            per_role[role].append(lag)
            if r["t"] >= new_js_at:
                per_role_new[role].append(lag)
            queue_wait_by_cid[cid] = min(queue_wait_by_cid.get(cid, 9e9),
                                         max(0.0, lag - pre_ms.get(cid, 0.0)))
            first_play.setdefault(cid, {})[role] = r
    for cid, ms in pre_ms.items():
        if cid in pub:
            prewarm_vals.append(ms)

    # 旧逻辑（字数估时长，从发布起算）会提前多久切「思考中」——只在新句子上算才有意义
    late_old: list[float] = []
    late_new: list[float] = []
    for cid, roles in first_play.items():
        r = roles.get("live") or roles.get("record")
        if not r or cid not in chars or cid not in novel:
            continue
        ch = chars[cid]
        est = min(12.0, max(1.2, 0.9 + ch * 0.16))
        real_end = r["t"] + float(r.get("dur") or 0.0)
        if real_end - (pub[cid] + est) > 0.5:
            (late_new if r["t"] >= new_js_at else late_old).append(real_end - (pub[cid] + est))

    print(f"统计区间 since={a.since or '全部'}  cid={len(pub)}  "
          f"新前端(ver)起于={'无' if new_js_at == float('inf') else dt.datetime.fromtimestamp(new_js_at).strftime('%H:%M:%S')}")
    print(stats("TTS合成", prewarm_vals))
    for role in sorted(per_role):
        print(stats(f"发布→起播[{role}]", per_role[role]))
    for role in sorted(per_role_new):
        print(stats(f"新前端[{role}]", per_role_new[role]))
    queue_wait = list(queue_wait_by_cid.values())
    if queue_wait:
        print(stats("排队等待", queue_wait),
              f"(>1.5s 占 {sum(1 for x in queue_wait if x > 1.5) / len(queue_wait):.1%})")
    print("skip:", dict(skips) or "无")
    if late_old:
        print(stats("旧前端·估时长提前切", late_old), f"({len(late_old)} 句)")
    if late_new:
        print(stats("新前端·估时长提前切", late_new), f"({len(late_new)} 句)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
