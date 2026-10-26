# -*- coding: utf-8 -*-
"""P3-1 统一测试入口：发现并逐个运行 `tools/test_*.py`。

用法：
  python tools/test_all.py
  python tools/test_all.py --verbose
  python tools/test_all.py --pattern 'test_*combat*.py'
  python tools/test_all.py --fail-fast

每个测试都是独立子进程；任一失败返回 1，全部通过返回 0。
不自动包含本文件，避免递归。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
SELF = os.path.abspath(__file__)


def discover(pattern="test_*.py"):
    import fnmatch
    out = []
    for name in sorted(os.listdir(TOOLS)):
        if not name.endswith(".py"):
            continue
        p = os.path.abspath(os.path.join(TOOLS, name))
        if p == SELF or not fnmatch.fnmatch(name, pattern):
            continue
        out.append(p)
    return out


def run_one(path, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(
            [sys.executable, path],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "ok": p.returncode == 0,
            "code": p.returncode,
            "out": p.stdout or "",
            "err": p.stderr or "",
            "sec": round(time.time() - t0, 2),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "timeout", "out": "", "err": "",
                "sec": round(time.time() - t0, 2)}


def _tail(s, n=20):
    lines = (s or "").rstrip().splitlines()
    return "\n".join(lines[-n:])


def main():
    ap = argparse.ArgumentParser(description="运行所有 tools/test_*.py")
    ap.add_argument("--pattern", default="test_*.py", help="测试文件名 glob")
    ap.add_argument("--verbose", action="store_true", help="打印每个测试的输出尾部")
    ap.add_argument("--fail-fast", action="store_true", help="首个失败就停")
    ap.add_argument("--timeout", type=float, default=180.0, help="单测超时秒数")
    ap.add_argument("--list", action="store_true", help="只列出测试")
    args = ap.parse_args()

    tests = discover(args.pattern)
    if not tests:
        print(f"NO_TESTS pattern={args.pattern!r}")
        return 1
    if args.list:
        for p in tests:
            print(os.path.basename(p))
        return 0

    bad = []
    ran = 0
    t0 = time.time()
    for p in tests:
        name = os.path.basename(p)
        r = run_one(p, args.timeout)
        ran += 1
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"{mark} {name} ({r['sec']}s)")
        if not r["ok"]:
            bad.append(name)
            if r["out"]:
                print(_tail(r["out"], 30))
            if r["err"]:
                print(_tail(r["err"], 30))
            if args.fail_fast:
                break
        elif args.verbose and (r["out"] or r["err"]):
            print(_tail(r["out"], 5))
            if r["err"]:
                print(_tail(r["err"], 5))
    total = ran
    print(f"\nTOTAL {total} | FAILED {len(bad)} | {round(time.time() - t0, 2)}s")
    if bad:
        print("FAILED: " + ", ".join(bad))
        return 1
    print(f"ALL {total} TEST FILES PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
