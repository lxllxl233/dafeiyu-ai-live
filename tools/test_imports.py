# -*- coding: utf-8 -*-
"""导入冒烟：每个工具模块都能单独导入，关键 API 没有被改名/删掉。

为什么要有这一条：`tools/noncombat.py` 曾经只能被 ctx 导入，直接
`python tools/noncombat.py` 会 ModuleNotFoundError: No module named 'sts2'，
白丢一次调用；改完路径又容易碰坏别处，所以用一条测试钉住。

    python tools/test_imports.py
"""
from __future__ import annotations

import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, ROOT)
sys.path.insert(0, TOOLS)

ok = True


def check(name, got, want=True):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


# 纯离线、导入期不碰网络的模块（run_start / rec_episode 会碰 OBS 和游戏 API，跳过）
MODS = ["sts2", "sim", "ctx", "act", "gates", "dps", "path", "noncombat",
        "mech", "advisor", "decision_log"]

for name in MODS:
    try:
        importlib.import_module(name)
        got = True
    except Exception as exc:  # noqa: BLE001
        print(f"  !! {name}: {type(exc).__name__}: {exc}")
        got = False
    check(f"import {name}", got, True)

import act  # noqa: E402
import ctx as ctxmod  # noqa: E402
import gates  # noqa: E402
import sim  # noqa: E402

check("act._dry 存在（--dry 验算）", callable(getattr(act, "_dry", None)), True)
check("ctx._mods 存在（柔嫩/缩小/缠结口径）", callable(getattr(ctxmod, "_mods", None)), True)
check("sim.Sim 有 tender 字段", hasattr(sim.Sim(), "tender"), True)
check("sim.parse_potion 认得 blk_mul", "blk_mul" in
      (sim.parse_potion({"occupied": True, "can_use": True, "potion_id": "X",
                         "description": "把你的格挡变为三倍。"}) or {}), True)
check("gates.is_free_output 存在", callable(getattr(gates, "is_free_output", None)), True)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
