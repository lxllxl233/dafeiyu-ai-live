#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：桌面桌宠在 `src/dafeiyu/ui/desktop.py`。

    python tools/streamer.py --preview out.png
    import streamer             # tools 目录在 sys.path 时继续有效
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from dafeiyu.ui import desktop as _mod  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_mod.main() or 0)

sys.modules[__name__] = _mod
