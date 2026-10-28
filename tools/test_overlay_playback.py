"""真实播放生命周期联调测试（真起一个 overlay_server 子进程，不 mock）。

对应需求「语音未播完不能显示思考中…… / 音频块间隙不能闪 / 取消与过期要能自愈」：
  1. 新台词发布 → playback.pending=1、speaking=True（合成/排队中，算「在说话」）；
  2. 页面 /mark?ev=audio&dur=N → playing=True、remaining≈N、pending 归零；
  3. /mark?ev=audio_end → playing=False、speaking=False（这一段真的说完了）；
  4. 页面判定过期没播 /mark?ev=skip → 队列回落，不会永远「在说话」；
  5. 队列 TTL：只发布、没有人报播放（页面关了/断线）→ 超时后自动回落；
  6. playback.seq 只在真实变化时涨（窗口靠它丢弃迟到的旧帧）；
  7. /health 也带 playback（tools/live.py 体检用）。

用随机端口 + 临时日志目录起独立实例：不碰正在直播的 8730，也不改 logs/overlay_seq.json。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = True


def check(name, got, want=True):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3.0) as r:
        return json.load(r)


def post(port: int, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3.0) as r:
        return json.load(r)


def pb(port: int) -> dict:
    return get(port, "/state")["playback"]


def main() -> int:
    port = _free_port()
    logdir = tempfile.mkdtemp(prefix="sts2_pb_")
    env = dict(os.environ, STS2_OVERLAY_PORT=str(port), PYTHONIOENCODING="utf-8",
               STS2_OVERLAY_LOG_DIR=logdir, STS2_AUDIO_PENDING_TTL="2",
               STS2_TTS_URL="http://127.0.0.1:1")
    proc = subprocess.Popen([sys.executable, "-X", "utf8", os.path.join(ROOT, "main.py"), "studio"],
                            cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            try:
                get(port, "/health")
                break
            except Exception:
                time.sleep(0.15)
        else:
            print("FAIL 服务没起来")
            return 1

        p0 = pb(port)
        check("初始不说话", p0["speaking"], False)
        check("初始 pending=0", p0["pending"], 0)
        seq0 = p0["seq"]

        # 1) 发布台词 = 进入待播队列（合成中/排队中都算在说话）
        post(port, "/comment", {"thought": "先算账。", "speech": "先算账。", "client_id": "c1"})
        p1 = pb(port)
        check("发布后 pending=1", p1["pending"], 1)
        check("发布后 speaking=True（合成空档不许显示思考中）", p1["speaking"], True)
        check("还没起播 playing=False", p1["playing"], False)
        check("发布让 playback.seq 变大（窗口据此丢迟到帧）", p1["seq"] > seq0, True)

        # 2) 页面真的起播（dur=3s）
        get(port, "/mark?ev=audio&role=live&cid=c1&dur=3")
        p2 = pb(port)
        check("起播 playing=True", p2["playing"], True)
        check("起播后 pending=0", p2["pending"], 0)
        check("remaining 接近 3s", 2.0 < p2["remaining"] <= 3.0, True)
        check("起播让 playback.seq 变大", p2["seq"] > seq0, True)
        check("起播时仍在说话", p2["speaking"], True)

        # 3) 播完
        get(port, "/mark?ev=audio_end&role=live&cid=c1&dur=3")
        p3 = pb(port)
        check("播完 playing=False", p3["playing"], False)
        check("播完 speaking=False（这才允许显示思考中）", p3["speaking"], False)
        check("播完 seq 继续变大", p3["seq"] > p2["seq"], True)

        # 4) 过期未播（页面判定 stale 丢弃）
        post(port, "/comment", {"thought": "旧的。", "speech": "旧的。", "client_id": "c2"})
        check("过期台词先算 pending", pb(port)["pending"], 1)
        get(port, "/mark?ev=skip&role=live&cid=c2&why=stale")
        p4 = pb(port)
        check("skip 后 pending 归零", p4["pending"], 0)
        check("skip 后不再假装在说话", p4["speaking"], False)

        # 5) 队列 TTL：发布后没人报播放（页面关了/断线）
        post(port, "/comment", {"thought": "没人播。", "speech": "没人播。", "client_id": "c3"})
        check("TTL 前 speaking=True", pb(port)["speaking"], True)
        time.sleep(2.4)
        p5 = pb(port)
        check("超过 TTL 自动回落 pending=0", p5["pending"], 0)
        check("超过 TTL 不再假装在说话（断线自愈）", p5["speaking"], False)

        # 6) 长句中途：remaining 随真实时长递减，不会因为字数估计提前结束
        post(port, "/comment", {"thought": "长句。", "speech": "长句。", "client_id": "c4"})
        get(port, "/mark?ev=audio&role=live&cid=c4&dur=9")
        a = pb(port)["remaining"]
        time.sleep(0.6)
        b = pb(port)["remaining"]
        check("remaining 递减", b < a, True)
        check("0.6s 后仍剩 > 8s（真实 9s 的长句不会提前结束）", b > 8.0, True)
        get(port, "/mark?ev=audio_end&role=live&cid=c4&dur=9")

        # 7) /health 带 playback
        h = get(port, "/health")
        check("/health 带 playback", isinstance(h.get("playback"), dict), True)
        check("/health playback.speaking=False", h["playback"]["speaking"], False)

        # 8) /state 带 ui_rev（前端换了页面能自己刷新）
        check("/state 带 ui_rev", bool(get(port, "/state").get("ui_rev")), True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    print()
    print("ALL OK" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
