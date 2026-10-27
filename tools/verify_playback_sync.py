"""实测一条台词的「播放 vs 窗口状态」时间线（真发一条，真合成，真截图）。

用法：python -X utf8 tools/verify_playback_sync.py "台词" [--pid 36208] [--out logs/sync_probe]

输出：
  · 每 0.2s 的 /state playback 与 thinking 采样（写 logs/sync_probe.jsonl）
  · 三个关键截图：排队中（还没出声）/ 正在播 / 播完
  · 一行总结：发布→起播、播放时长、窗口在播期间是否显示「思考中……」

判据（需求验收）：
  · 起播前：speaking=True, playing=False（合成空档不算思考中）
  · 播放中：speaking=True, playing=True, remaining>0
  · 播完：speaking=False → 有待办则进思考中
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))


def state() -> dict:
    with urllib.request.urlopen("http://127.0.0.1:8730/state?after=1", timeout=2.0) as r:
        return json.load(r)


def shot(path: str, pid: int | None) -> bool:
    args = [sys.executable, "-X", "utf8", os.path.join(ROOT, "tools", "shot_window.py"), path]
    if pid:
        args += ["--pid", str(pid)]
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    ok = r.returncode == 0 and os.path.isfile(path)
    if not ok:
        print("SHOT_FAIL", path, (r.stdout or "")[-200:], (r.stderr or "")[-200:])
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="这层先别硬碰，甲够了再上。")
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(ROOT, "logs", "sync_probe"))
    ap.add_argument("--seconds", type=float, default=16.0)
    ap.add_argument("--thinking", action="store_true",
                    help="先上报 /thinking on：模拟真实打牌时「播完该回到思考中」")
    a = ap.parse_args()

    import sts2  # noqa: E402

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    rows = []
    shots = {}
    t0 = time.time()
    if a.thinking:
        sts2.overlay_thinking(True, "验证：播完该回思考中")
    sts2.overlay_say("combat", a.text, "", None)
    pub = time.time()
    first_play = None
    first_speak = None
    play_end = None
    while time.time() - t0 < a.seconds:
        try:
            d = state()
        except Exception:
            time.sleep(0.2)
            continue
        pb = d.get("playback") or {}
        th = d.get("thinking") or {}
        t = time.time() - pub
        rows.append({"t": round(t, 2), "playing": pb.get("playing"),
                     "remaining": pb.get("remaining"), "pending": pb.get("pending"),
                     "speaking": pb.get("speaking"), "cid": pb.get("cid"),
                     "think": bool(th.get("on"))})
        if pb.get("speaking") and first_speak is None:
            first_speak = t
        if pb.get("playing") and first_play is None:
            first_play = t
            time.sleep(0.6)
            p = a.out + "_playing.png"
            if shot(p, a.pid):
                shots["playing"] = p
            continue
        if first_play is not None and play_end is None and not pb.get("playing") and not pb.get("speaking"):
            play_end = t
            p = a.out + "_done.png"
            if shot(p, a.pid):
                shots["done"] = p
            break
        if first_speak is not None and first_play is None and "queued" not in shots:
            p = a.out + "_queued.png"
            if shot(p, a.pid):
                shots["queued"] = p
        time.sleep(0.2)

    with open(a.out + ".jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"发布→起播 {first_play:.2f}s" if first_play else "发布→起播 没起播")
    print(f"起播→播完 {play_end - first_play:.2f}s" if (play_end and first_play) else "播完 未捕获")
    print("截图:", json.dumps(shots, ensure_ascii=False))
    # 播完之后窗口应当离开说话态（有待办 → 思考中；服务端 thinking 也可能是 off）
    tail = [r for r in rows if r["t"] > (play_end or 0)]
    print("播完后采样:", json.dumps(tail[:3], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
