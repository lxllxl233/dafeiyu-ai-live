#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：StS2 客户端已迁到 `games/slay-the-spire-2/compat/sts2.py`。

    python sts2.py state        # 继续用
    import sts2                 # 继续用（拿到的是真模块，不是这个 shim）

新代码请直接 import `dafeiyu.games.sts2` 的适配器；这个 shim 只为不打断老命令/老测试。
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
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
