"""给主播窗拍一张验收截图（窗口是分层透明窗，必须用带 alpha 的屏幕抓取）。

为什么要这个工具：主播窗默认 `WDA_EXCLUDEFROMCAPTURE`（录屏里隐藏），普通截屏/PrintWindow
拍出来是空的。做视觉验收（「思考中……」有没有出来、☰ 菜单钮长什么样）时，临时用
`--capture` 起一个实例，再用这个脚本按窗口矩形抓屏。

用法：
    python tools/shot_window.py out.png [--pid 1234] [--class StS2Streamer]
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import streamer  # noqa: F401,E402  (import 副作用：开 DPI 感知，坐标才是物理像素)

from PIL import ImageGrab  # noqa: E402

u = ctypes.windll.user32
u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.IsWindowVisible.argtypes = [wintypes.HWND]


def find(pid: int = 0, cls_prefix: str = "StS2Streamer"):
    hits = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _lp):
        if not u.IsWindowVisible(h):
            return True
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if pid and p.value != pid:
            return True
        buf = ctypes.create_unicode_buffer(128)
        u.GetClassNameW(h, buf, 128)
        if buf.value.startswith(cls_prefix):
            hits.append((h, p.value))
        return True

    u.EnumWindows(cb, 0)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--class", dest="cls", default="StS2Streamer")
    a = ap.parse_args()
    hits = find(a.pid, a.cls)
    if not hits:
        print("找不到主播窗（先起 streamer.py --capture）")
        return 1
    h, pid = hits[0]
    r = wintypes.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    box = (r.left, r.top, r.right, r.bottom)
    img = ImageGrab.grab(bbox=box, all_screens=True)
    img.save(a.out)
    print(f"SHOT {a.out} {img.width}x{img.height} pid={pid} hwnd={h} box={box}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
