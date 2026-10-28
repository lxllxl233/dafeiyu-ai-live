#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 兼容入口：实现已迁到 games/slay-the-spire-2/decision/decision_log.py。
from __future__ import annotations

import sys

from _game_impl import load, run

_mod = load("decision_log")
if __name__ != "__main__":
    sys.modules[__name__] = _mod
