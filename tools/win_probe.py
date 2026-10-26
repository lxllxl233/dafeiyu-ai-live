#!/usr/bin/env python3
"""定位主播窗：打印 streamer.py 进程的所有顶层窗口（位置/可见/置顶/分层属性）。

排查「弹幕窗不在屏幕上」用：DWM 分层窗口如果坐标落到屏幕外，OBS/直播姬都看不到。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

user32 = ctypes.windll.user32

EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def title_of(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def info(hwnd: int) -> dict:
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    ex = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
    style = user32.GetWindowLongW(hwnd, -16)
    return {
        "hwnd": hex(hwnd),
        "title": title_of(hwnd),
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "iconic": bool(user32.IsIconic(hwnd)),
        "rect": (r.left, r.top, r.right, r.bottom),
        "size": (r.right - r.left, r.bottom - r.top),
        "topmost": bool(ex & 0x00000008),
        "layered": bool(ex & 0x00080000),
        "ws_visible": bool(style & 0x10000000),
    }


def main() -> int:
    targets = [int(a) for a in sys.argv[1:]] or []
    seen = []

    def cb(hwnd, _lparam):
        pid = pid_of(hwnd)
        if targets and pid not in targets:
            return True
        d = info(hwnd)
        if targets or d["visible"]:
            d["pid"] = pid
            seen.append(d)
        return True

    EnumWindows(EnumWindowsProc(cb), 0)
    for d in seen:
        print(d)
    if not seen:
        print("no window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
