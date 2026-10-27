"""连着发两条台词，验证「最新优先」：老台词被切掉、新台词立刻起播（不再排队等整句）。

用法：python -X utf8 tools/verify_preempt.py "第一句……" "第二句……"
读 logs/av_marks.jsonl 里这两条 cid 的 audio / audio_end / skip 时间线。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))


def post(text: str) -> dict:
    payload = json.dumps({"tag": "战斗", "thought": text, "speech": text,
                          "client_id": f"preempt{int(time.time() * 1000) % 100000}"},
                         ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8730/comment", data=payload,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read().decode("utf-8"))["comment"]


def main() -> int:
    a = sys.argv[1] if len(sys.argv) > 1 else "先挂甲，别急着换血，这回合它打不满。"
    b = sys.argv[2] if len(sys.argv) > 2 else "算了，直接换：一刀十二，它先倒。"
    t0 = time.time()
    c1 = post(a)
    time.sleep(1.3)
    c2 = post(b)
    print("posted", c1["id"], c2["id"], "cid", c1["client_id"], c2["client_id"])
    time.sleep(7.5)
    rows = []
    for ln in open(os.path.join(ROOT, "logs", "av_marks.jsonl"), encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("cid") in (c1["client_id"], c2["client_id"]) and r.get("ev") in (
                "audio", "audio_end", "skip", "audio"):
            rows.append(r)
    for r in rows:
        tag = "A" if r.get("cid") == c1["client_id"] else "B"
        print(f"{tag} t={r['t'] - t0:5.2f}s {r.get('ev'):9s} role={r.get('role')} dur={r.get('dur')} why={r.get('why')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
