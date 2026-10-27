#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""开局自检：一条命令回答「现在能不能开工、按什么规矩开工」。

用法：
  python tools/preflight.py            # 离线自检 + 当前局面快照
  python tools/preflight.py --fast     # 只跑测试，不读游戏状态

做四件事：
  1. 跑全部离线测试（每个独立进程，输出写临时文件，不污染控制台）；
  2. 读当前局面（MAIN_MENU / 存盘 / 战斗中 / 地图），给出下一步命令；
  3. 量牌组：张数 vs 本幕上限，超了就直说；
  4. 把该看的文档指出来（doctrine §9 / handoff / 本局笔记）。

设计原则：**只读不写**。不点按钮、不建局、不改文件（临时文件除外）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, ROOT)
sys.path.insert(0, TOOLS)

TESTS = ["test_imports.py", "test_sim.py", "test_act_guards.py",
         "test_gates.py", "test_combat_math.py", "test_ctx_act.py",
         "test_regressions.py", "test_free_attack.py"]


def run_test(name: str) -> tuple[bool, str]:
    """独立进程跑一个测试；输出落临时文件，只取最后一行。"""
    path = os.path.join(TOOLS, name)
    if not os.path.exists(path):
        return False, "MISSING"
    fd, tmp = tempfile.mkstemp(suffix=".log")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8", errors="replace") as fh:
            rc = subprocess.run([sys.executable, "-X", "utf8", path],
                                stdout=fh, stderr=subprocess.STDOUT,
                                cwd=ROOT, timeout=180).returncode
        with open(tmp, encoding="utf-8", errors="replace") as fh:
            lines = [x.strip() for x in fh if x.strip()]
        tail = lines[-1] if lines else "(no output)"
        return rc == 0 and tail.upper().startswith("ALL OK"), tail
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def main(argv: list[str]) -> int:
    fast = "--fast" in argv
    bad = 0
    print("=== 1) 离线测试 ===")
    for name in TESTS:
        good, tail = run_test(name)
        if not good:
            bad += 1
        print(f"{'PASS' if good else 'FAIL'} {name:<22} {tail}")
    print()

    if not fast:
        print("=== 2) 当前局面 ===")
        try:
            import sts2
            st = sts2.get_state()
            screen = st.get("screen")
            run = st.get("run") or {}
            deck = run.get("deck") or []
            print(f"screen = {screen}")
            if screen == "MAIN_MENU":
                legal = st.get("available_actions") or []
                if "continue_run" in legal:
                    print("有存档：python -X utf8 sts2.py do continue_run")
                else:
                    print("无存档：python -X utf8 tools/run_start.py")
            elif deck:
                act = run.get("act_id", run.get("act"))
                cap = sts2.deck_cap(act)
                print(f"第 {act} 幕 f{run.get('floor')} | HP {run.get('current_hp')}/{run.get('max_hp')} "
                      f"| 金 {run.get('gold')} | 牌组 {len(deck)} 张（上限 {cap}）")
                if len(deck) > cap:
                    print("  !! 牌组超上限：先删牌再加牌（doctrine §9.1）")
                print("读局面：python -X utf8 tools/ctx.py")
            print()
        except Exception as exc:  # noqa: BLE001
            print(f"读不到局面（{type(exc).__name__}: {exc}）—— 游戏没开也没关系，测试已经跑完")
            print()

    print("=== 3) 该看的文档 ===")
    print("docs/RUN_DOCTRINE.md §9（拿牌预算 / 血量硬线 / 数值口径 / DPS 门槛）")
    print("docs/HANDOFF_NEXT_RUN.md（接手 run 的位置和开工顺序）")
    print("logs/runNN_notes.md（本局逐层记录，打完要写死因）")
    print()
    print("PREFLIGHT OK" if bad == 0 else f"PREFLIGHT FAIL {bad} 个测试")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
