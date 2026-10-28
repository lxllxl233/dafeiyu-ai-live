#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旧入口（保留）：一局一段 OBS 录像，收进 ``recordings/<游戏>/run-NNNN/run.mp4``。

新命令更清楚，推荐用：
  python main.py replay start --run 97 --character ironclad --ascension 10
  python main.py replay stop  --result win_a10
  python main.py replay list

这个脚本兼容老调用：
  python tools/rec_episode.py start --run 81 --note "..."
  python tools/rec_episode.py finish --run 81 --result lose_act1boss
  python tools/rec_episode.py list
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dafeiyu.recording import replay  # noqa: E402


def start(run: int | None = None, note: str = "") -> str:
    run_dir = replay.create_run("sts2", run, note=note)
    replay.set_current(run_dir)
    try:
        replay.start_recording(run_dir)
        return f"RECORDING {run_dir}"
    except Exception as exc:  # noqa: BLE001
        return f"WARN OBS 未开录（{type(exc).__name__}: {exc}）目录已建：{run_dir}"


def finish(run: int | None = None, result: str = "") -> str:
    run_dir = replay.get_current()
    if run_dir is None and run is not None:
        run_dir = replay.game_dir("sts2") / f"run-{run:04d}"
    if run_dir is None or not run_dir.is_dir():
        return "NO_RUN（先 start）"
    try:
        dst = replay.stop_recording(run_dir, result)
        replay.clear_current()
        replay.write_index()
        return f"{dst} ({replay.human_size(dst.stat().st_size)})"
    except Exception as exc:  # noqa: BLE001
        return f"ERR {type(exc).__name__}: {exc}"


def listing() -> str:
    rows = replay.scan("sts2")
    if not rows:
        return "（还没有录像）"
    out = []
    for r in rows:
        asc = f"A{r['ascension']}" if r["ascension"] is not None else "—"
        out.append(f"P{r['run'] or '—':<4} {r['character'] or '—':<10} {asc:<4} "
                   f"{(r['result'] or '—'):<22} {replay.human_size(r['size_bytes'])}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start")
    p.add_argument("--run", type=int, default=None)
    p.add_argument("--note", default="")
    p = sub.add_parser("finish")
    p.add_argument("--run", type=int, default=None)
    p.add_argument("--result", default="")
    sub.add_parser("list")
    a = ap.parse_args()
    if a.cmd == "start":
        print(start(a.run, a.note))
    elif a.cmd == "finish":
        print(finish(a.run, a.result))
    else:
        print(listing())
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
