#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""游戏包内的 ``sts2`` 入口：把 ``compat/sts2.py`` 装成稳定模块名。

决策模块（ctx / act / advisor / sim ...）都直接 ``import sts2``；它们现在位于
``games/slay-the-spire-2/decision/``，所以由本文件负责找到上一层的兼容客户端。
``tools/sts2.py`` 只是同一件事的旧路径入口，二者共享 ``sts2_compat`` 实例。
"""
from __future__ import annotations

import importlib.util
import os
import sys

PACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(PACK, "compat", "sts2.py")
_NAME = "sts2_compat"

if _NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_NAME, TARGET)
    if _spec is None or _spec.loader is None:
        raise ImportError(f"找不到 StS2 客户端：{TARGET}")
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = _mod
    _spec.loader.exec_module(_mod)

_mod = sys.modules[_NAME]

if __name__ == "__main__":
    raise SystemExit(_mod.main() or 0)

sys.modules[__name__] = _mod
