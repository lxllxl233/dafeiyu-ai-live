# -*- coding: utf-8 -*-
"""曼波引擎看护：合成卡死时把 9880 上的 MamboTTS 杀掉重起。

为什么需要：GPT-SoVITS 的 api.py 在被并发压爆/显存抖动后会「假活」——
`/control` 还回 200，但 `/`（合成）永远不回，表现就是直播里前几句有声、后面全哑。
overlay_server 连续两次合成失败就会 `tts_guard.py --auto`。

用法：
    python tools/tts_guard.py --check      # 只体检（合成一句随机文本）
    python tools/tts_guard.py --restart    # 杀 9880 上的引擎并重起，等它真的能合成
    python tools/tts_guard.py --auto       # 体检失败才重起（默认）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_DIR = os.environ.get("STS2_MAMBO_DIR", r"D:\MamboTTS\app\MamboTTS-v1.2.1-full")
ENGINE_HOST = os.environ.get("STS2_TTS_HOST", "127.0.0.1")
ENGINE_PORT = int(os.environ.get("STS2_TTS_PORT", "9880"))
OVERLAY = os.environ.get("STS2_OVERLAY_URL", "http://127.0.0.1:8730")
LOG_DIR = os.path.join(ROOT, "logs")


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe_direct(timeout: float = 90.0) -> tuple[bool, float, str]:
    """直接压引擎合成一句随机文本（绕开 overlay 缓存），返回 (ok, 秒, 说明)。"""
    text = f"体检{int(time.time() * 1000) % 100000}"
    payload = {"text": text, "text_language": "zh", "speed": 1.0,
               "cut_punc": "，。？！；：、…,.;?!"}
    req = urllib.request.Request(
        f"http://{ENGINE_HOST}:{ENGINE_PORT}/",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with _opener().open(req, timeout=timeout) as r:
            data = r.read()
    except Exception as e:
        return False, time.time() - t0, f"{type(e).__name__}: {str(e)[:80]}"
    if len(data) < 2000 or data[:4] != b"RIFF":
        return False, time.time() - t0, f"返回异常 {len(data)} 字节"
    return True, time.time() - t0, f"{len(data) // 1024} KB"


def engine_online(timeout: float = 2.0) -> bool:
    try:
        with _opener().open(f"http://{ENGINE_HOST}:{ENGINE_PORT}/control", timeout=timeout) as r:
            r.read(64)
        return True
    except Exception:
        return False


def listener_pid(port: int = ENGINE_PORT) -> int | None:
    """找监听 9880 的进程（PowerShell 版 netstat，够用且不依赖 psutil）。"""
    ps = (
        f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue "
        f"| Select-Object -First 1; if ($c) {{ $c.OwningProcess }}"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=15).stdout.strip()
        return int(out) if out.isdigit() else None
    except Exception:
        return None


def kill_engine() -> str:
    pid = listener_pid()
    if not pid:
        return "9880 上没有监听进程"
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=20)
    except Exception as e:
        return f"taskkill 失败 {e}"
    for _ in range(20):
        if listener_pid() is None:
            return f"已杀掉 pid {pid}"
        time.sleep(0.3)
    return f"pid {pid} 杀不掉"


def start_engine() -> str:
    run_py = os.path.join(ENGINE_DIR, "run_engine.py")
    if not os.path.exists(run_py):
        return f"找不到引擎启动脚本：{run_py}"
    os.makedirs(LOG_DIR, exist_ok=True)
    out = open(os.path.join(LOG_DIR, "mambo_engine.out.log"), "ab")
    err = open(os.path.join(LOG_DIR, "mambo_engine.err.log"), "ab")
    try:
        subprocess.Popen(
            [sys.executable, "run_engine.py"],
            cwd=ENGINE_DIR, stdout=out, stderr=err,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        return f"启动失败 {e}"
    return "已拉起 run_engine.py"


def wait_ready(timeout: float = 150.0) -> tuple[bool, str]:
    """等端口 + 等第一句真的合成出来（端口先开、模型后加载，只看端口会误判）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if engine_online(1.0):
            ok, dt, note = probe_direct(timeout=min(60.0, timeout))
            if ok:
                return True, f"引擎就绪，合成 {dt:.1f}s / {note}"
        time.sleep(2.0)
    return False, f"{timeout:.0f}s 内没就绪"


def restart() -> tuple[bool, str]:
    steps = [kill_engine(), start_engine()]
    ok, note = wait_ready()
    return ok, " | ".join(steps + [note])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只体检")
    ap.add_argument("--restart", action="store_true", help="强制重启引擎")
    ap.add_argument("--auto", action="store_true", help="体检失败才重启（默认）")
    ap.add_argument("--timeout", type=float, default=150.0)
    args = ap.parse_args()

    if args.check:
        ok, dt, note = probe_direct(timeout=min(90.0, args.timeout))
        print(("OK  " if ok else "BAD ") + f"曼波合成 {dt:.1f}s / {note}")
        return 0 if ok else 1

    if args.restart:
        ok, note = restart()
        print(("OK  " if ok else "BAD ") + note)
        return 0 if ok else 1

    ok, dt, note = probe_direct(timeout=45.0)
    if ok:
        print(f"OK  引擎正常，合成 {dt:.1f}s / {note}")
        return 0
    print(f"BAD 合成卡住（{note}），开始重启引擎…")
    ok, note = restart()
    print(("OK  " if ok else "BAD ") + note)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
