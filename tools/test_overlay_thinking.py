"""思考状态端点联调测试（真起一个 overlay_server 子进程，别 mock）。

验收点（对应需求「不讲话时显示思考中……，讲话时切换到说话状态」）：
  1. `/thinking on` 之后 `/state` 里必须带着 thinking.on=True 且 seq 变大；
  2. 重复上报 on **不许**再涨 seq（否则窗口会把「续命」当成状态变化）；
  3. `/thinking off` 之后 on=False，seq 再涨一次；
  4. `/health` 也能看到 thinking（`tools/live.py check` 用它做体检）；
  5. 迟到的旧 seq 事件不会把新状态改回去——这条在 state machine 里测（见 test_caster_state.py），
     这里保证服务端**只按真实变化涨 seq**，是那条规则的前提。

用随机端口起独立实例：不动正在跑直播的那个 8730，也不碰 logs/seq。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
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
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2.0) as r:
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


def main() -> int:
    port = _free_port()
    env = dict(os.environ, STS2_OVERLAY_PORT=str(port), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "-X", "utf8", os.path.join(ROOT, "overlay_server.py")],
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

        th0 = get(port, "/state")["thinking"]
        check("初始 thinking.on", th0["on"], False)
        check("初始 seq=0", th0["seq"], 0)

        r1 = post(port, "/thinking", {"on": True, "note": "读局面"})["thinking"]
        check("on 之后 on=True", r1["on"], True)
        check("on 之后 seq=1", r1["seq"], 1)
        st = get(port, "/state")["thinking"]
        check("/state 带上 thinking", st["on"], True)
        check("/state 的 seq 一致", st["seq"], 1)
        check("/state 带 note", st["note"], "读局面")

        r2 = post(port, "/thinking", {"on": True, "note": "还在算"})["thinking"]
        check("重复 on 不涨 seq", r2["seq"], 1)
        check("重复 on 会续 beat", r2["beat"] > r1["beat"], True)
        check("重复 on 更新 note", r2["note"], "还在算")

        r3 = post(port, "/thinking", {"on": False})["thinking"]
        check("off 之后 on=False", r3["on"], False)
        check("off 涨 seq=2", r3["seq"], 2)

        r4 = post(port, "/thinking", {"on": True, "note": "下一回合"})["thinking"]
        check("再 on seq=3", r4["seq"], 3)

        h = get(port, "/health")
        check("/health 带 thinking", h["thinking"]["on"], True)
        check("/health thinking.seq=3", h["thinking"]["seq"], 3)

        # 端点必须容忍空 body（producer 出错不该把服务打挂）
        req = urllib.request.Request(f"http://127.0.0.1:{port}/thinking", data=b"",
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3.0) as r:
            check("空 body 不报 500", r.status, 200)
        check("空 body 当成 off", get(port, "/state")["thinking"]["on"], False)
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
