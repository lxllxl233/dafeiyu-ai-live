# -*- coding: utf-8 -*-
"""List visible top-level windows and which monitor they sit on (to find OBS projector windows)."""

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

MONITORENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.RECT), ctypes.c_double
)


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


monitors = []


def _cb(hmon, hdc, lprc, data):
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    monitors.append(
        (
            mi.rcMonitor.left,
            mi.rcMonitor.top,
            mi.rcMonitor.right,
            mi.rcMonitor.bottom,
            bool(mi.dwFlags & 1),
        )
    )
    return 1


user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
print("monitors (l,t,r,b,primary):")
for m in monitors:
    print("  ", m)


def monitor_of(x, y):
    for i, (l, t, r, b, _p) in enumerate(monitors):
        if l <= x < r and t <= y < b:
            return i
    return -1


EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
rows = []


def _enum(hwnd, _lparam):
    if not user32.IsWindowVisible(hwnd):
        return True
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return True
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    if rect.right - rect.left < 80 or rect.bottom - rect.top < 80:
        return True
    mon = monitor_of((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    rows.append((mon, buf.value[:70], rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top))
    return True


user32.EnumWindows(EnumProc(_enum), 0)
for row in sorted(rows, key=lambda r: (r[0], r[1])):
    print(f"mon={row[0]:>2} size={row[4]}x{row[5]} at({row[2]},{row[3]})  {row[1]}")
