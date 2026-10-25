# -*- coding: utf-8 -*-
"""Bring the Slay the Spire 2 window to the foreground (monitor capture must see the game)."""

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
targets = []


def _enum(hwnd, _lparam):
    if not user32.IsWindowVisible(hwnd):
        return True
    n = user32.GetWindowTextLengthW(hwnd)
    if n == 0:
        return True
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    if "Slay the Spire 2" in buf.value:
        targets.append((hwnd, buf.value))
    return True


user32.EnumWindows(EnumProc(_enum), 0)
print("found:", targets)
for hwnd, title in targets:
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    print("focused:", title)
