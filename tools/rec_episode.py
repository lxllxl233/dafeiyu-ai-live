#!/usr/bin/env python3
"""一拍一集：把当前 OBS 录像收尾并命名为 runNN，然后开新的一段。

用法：
  python tools/rec_episode.py finish --run 81 --result lose_act1boss
  python tools/rec_episode.py start            # 开新的一段（下一局）
  python tools/rec_episode.py list             # 看现有分 P

命名：recordings/sts2_ironclad_a10_runNN_<result>.mp4
索引：recordings/INDEX.md 自动追加一行。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = os.path.join(ROOT, "recordings")
sys.path.insert(0, ROOT)

import obsctl  # noqa: E402


def _obs():
    cfg = obsctl.load_ws_config()
    return obsctl.Obs(cfg["url"], cfg.get("password", "")).connect()


def _newest_mp4():
    if not os.path.isdir(REC):
        return None
    files = [os.path.join(REC, f) for f in os.listdir(REC) if f.lower().endswith(".mp4")]
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def finish(run: int, result: str) -> str:
    obs = _obs()
    try:
        st = obs.status()
        active = st.get("outputActive")
        if active:
            print("stopping recording ...", flush=True)
            obs.stop_record()
            # 等文件句柄落盘
            for _ in range(40):
                time.sleep(0.5)
                if not obs.status().get("outputActive"):
                    break
    finally:
        obs.close()
    time.sleep(3)
    src = _newest_mp4()
    if not src:
        return "NO_FILE"
    base = os.path.basename(src)
    if re.match(r"sts2_ironclad_a10_run\d+", base):
        return f"ALREADY_NAMED {base}"
    dst = os.path.join(REC, f"sts2_ironclad_a10_run{run}_{result}.mp4")
    if os.path.exists(dst):
        dst = os.path.join(REC, f"sts2_ironclad_a10_run{run}_{result}_{int(time.time())}.mp4")
    os.replace(src, dst)
    size_gb = os.path.getsize(dst) / (1024 ** 3)
    idx = os.path.join(REC, "INDEX.md")
    with io.open(idx, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"| P{run} | {os.path.basename(dst)} | {result} | {size_gb:.1f} GB |\n")
    return f"{os.path.basename(dst)} ({size_gb:.1f} GB)"


def start() -> str:
    obs = _obs()
    try:
        obs.start_record()
    finally:
        obs.close()
    return "RECORDING"


def listing() -> str:
    idx = os.path.join(REC, "INDEX.md")
    if not os.path.exists(idx):
        with io.open(idx, "w", encoding="utf-8", newline="\n") as f:
            f.write("| 分P | 文件 | 结果 | 大小 |\n|---|---|---|---|\n")
        return "EMPTY (created INDEX.md)"
    return io.open(idx, encoding="utf-8").read()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("finish")
    p.add_argument("--run", type=int, required=True)
    p.add_argument("--result", required=True)
    sub.add_parser("start")
    sub.add_parser("list")
    a = ap.parse_args()
    if a.cmd == "finish":
        print(finish(a.run, a.result))
    elif a.cmd == "start":
        print(start())
    else:
        print(listing())


if __name__ == "__main__":
    main()
