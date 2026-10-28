#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：单实例锁在 `src/dafeiyu/ui/instance_lock.py`。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from dafeiyu.ui import instance_lock as _mod  # noqa: E402

sys.modules[__name__] = _mod
