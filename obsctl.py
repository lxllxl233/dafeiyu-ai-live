#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：OBS 控制已迁到 `src/dafeiyu/stream/obs.py`。"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

from dafeiyu.stream import obs as _impl  # noqa: E402

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
