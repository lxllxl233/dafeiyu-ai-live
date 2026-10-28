#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：StS2 客户端在 `games/slay-the-spire-2/compat/sts2.py`。

    python tools/sts2.py state
    import sts2                 # tools 目录在 sys.path 时继续有效
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "games", "slay-the-spire-2", "compat", "sts2.py")
_NAME = "sts2_compat"

if _NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_NAME, TARGET)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = _mod
    _spec.loader.exec_module(_mod)

_mod = sys.modules[_NAME]

if __name__ == "__main__":
    raise SystemExit(_mod.main() or 0)

sys.modules[__name__] = _mod
