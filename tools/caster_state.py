#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：状态机在 `src/dafeiyu/ui/caster_state.py`。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from dafeiyu.ui import caster_state as _mod  # noqa: E402

sys.modules[__name__] = _mod
