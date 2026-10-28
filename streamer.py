#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：桌面透明弹幕窗已迁到 `ui/desktop/streamer.py`。

    python streamer.py --demo
    import streamer             # 继续用（真模块）
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(ROOT, "ui", "desktop", "streamer.py")
_NAME = "dafeiyu_desktop_streamer"

if _NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_NAME, TARGET)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = _mod
    _spec.loader.exec_module(_mod)

_mod = sys.modules[_NAME]

if __name__ == "__main__":
    raise SystemExit(_mod.main() or 0)

sys.modules[__name__] = _mod
