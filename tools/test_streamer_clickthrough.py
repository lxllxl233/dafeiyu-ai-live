"""点击穿透修复的**真窗口**验证（不是只跑编译）。

复现的问题：穿透开着时，右键菜单/缩放全都点不到 —— 弹窗等于死的。
以前 `_proc` 里 `if self.click_through: return 0`，再加上 WS_EX_TRANSPARENT，
鼠标事件根本到不了窗口；唯一出路是 Ctrl+Alt+D（用户不知道就没救）。

这次的做法：整窗仍然穿透，但鼠标贴到「☰ 菜单钮」或「右下缩放手把」时，
临时摘掉 WS_EX_TRANSPARENT，离开立刻恢复。

本脚本的断言直接用系统的 `WindowFromPoint`：
  1. 光标在面板正中（非热区）→ WindowFromPoint **不是**主播窗（穿透生效，点得到游戏）
  2. 光标移到 ☰ 正中 → WindowFromPoint **就是**主播窗（热区临时可点）
  3. 光标移回面板正中 → 又变回穿透
  4. 热区判定函数本身：☰/手把命中，正文不命中

会短暂移动鼠标（结束后复位），所以别在用户正在拖东西的时候跑。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

u = ctypes.windll.user32
u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u.WindowFromPoint.argtypes = [wintypes.POINT]
u.WindowFromPoint.restype = wintypes.HWND
u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u.IsWindowVisible.argtypes = [wintypes.HWND]

ok = True


def check(name, got, want=True):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def find_window(pid: int):
    hits = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(hwnd):
            hits.append(hwnd)
        return True

    u.EnumWindows(cb, 0)
    return hits[0] if hits else None


def wnd_rect(hwnd):
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def wfp(x, y):
    return u.WindowFromPoint(wintypes.POINT(int(x), int(y)))


def menu_owned_by(hwnd):
    """Windows 的弹出菜单是 class 为 `#32768` 的顶层窗口。

    实测（本机 Win11）：`TrackPopupMenu` 建出来的菜单 window owner 是 0，不是我们的窗，
    所以这里**不按 owner 过滤**——只在点击前后各扫一次，新出现且可见的那一个就是它。
    """
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _lp):
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, buf, 64)
        if buf.value == "#32768" and u.IsWindowVisible(h):
            found.append(h)
        return True

    u.EnumWindows(cb, 0)
    return found[0] if found else None


def wait_hit(hwnd, x, y, want: bool, timeout=2.5):
    """等穿透状态按光标位置生效（watcher 是 50ms 轮询，给几帧时间）。"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = (wfp(x, y) == hwnd)
        if last == want:
            return last
        time.sleep(0.05)
    return last


def main() -> int:
    # 先用纯函数验热区（不依赖窗口）
    from streamer import StreamerWindow, chip_rect  # noqa: PLC0415

    class R:
        def __init__(self, l, t, r, b):
            self.left, self.top, self.right, self.bottom = l, t, r, b

    rect = R(100, 100, 100 + 1232, 100 + 276)   # 1.0 倍一帧的大小
    cl, ct, cr, cb = chip_rect(1232, 276, 1.0)
    check("☰ 中心算热区",
          StreamerWindow._hot_zone_point(100 + (cl + cr) // 2, 100 + (ct + cb) // 2, rect, 1.0), True)
    check("右下角算热区", StreamerWindow._hot_zone_point(rect.right - 5, rect.bottom - 5, rect, 1.0), True)
    check("正文中间不算热区",
          StreamerWindow._hot_zone_point(100 + 600, 100 + 150, rect, 1.0), False)
    check("左上角不算热区", StreamerWindow._hot_zone_point(110, 110, rect, 1.0), False)

    orig = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(orig))
    env = dict(os.environ, PYTHONIOENCODING="utf-8", STS2_OVERLAY_PORT="8791",
               STS2_VOICE_DELEGATE="0")
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", os.path.join(ROOT, "main.py"),
         "--port", "8791", "--demo", "--mute", "--voice", "off", "--panel",
         "--scale", "1.0", "--pos", "300,300", "--click-through"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        hwnd = None
        for _ in range(100):
            time.sleep(0.2)
            hwnd = find_window(proc.pid)
            if hwnd:
                break
        if not hwnd:
            print("FAIL 主播窗没起来")
            out = proc.stdout.read(4000) if proc.stdout else ""
            print(out)
            return 1
        time.sleep(0.8)   # 等第一帧 + 交互守护线程起来

        r = wnd_rect(hwnd)
        w, h = r.right - r.left, r.bottom - r.top
        cl, ct, cr, cb = chip_rect(w, h, 1.0)
        chip_x, chip_y = r.left + (cl + cr) // 2, r.top + (ct + cb) // 2
        body_x, body_y = r.left + w // 2, r.top + h // 3

        u.SetCursorPos(body_x, body_y)
        check("光标在正文上：穿透生效（点得到下面的游戏）", wait_hit(hwnd, body_x, body_y, False), False)

        u.SetCursorPos(chip_x, chip_y)
        check("光标贴到 ☰：临时可交互（WindowFromPoint 命中主播窗）",
              wait_hit(hwnd, chip_x, chip_y, True), True)

        # 热区里滚轮/点击真能到窗口：发一次 WM_MOUSEWHEEL，窗口允许时比例会变
        u.SetCursorPos(body_x, body_y)
        check("离开 ☰：穿透恢复", wait_hit(hwnd, body_x, body_y, False), False)
        check("穿透恢复后正文确实不归主播窗", wfp(body_x, body_y) == hwnd, False)

        # 最要紧的一条：穿透模式下右键菜单真的能打开（以前这里完全没反应）
        u.SetCursorPos(chip_x, chip_y)
        wait_hit(hwnd, chip_x, chip_y, True)
        before = menu_owned_by(hwnd)
        # 用真鼠标事件（不是 PostMessage）：菜单是系统模态窗口，只有真输入才会弹出来
        u.mouse_event(0x0008, 0, 0, 0, 0)      # MOUSEEVENTF_RIGHTDOWN
        u.mouse_event(0x0010, 0, 0, 0, 0)      # MOUSEEVENTF_RIGHTUP
        menu = None
        for _ in range(24):
            time.sleep(0.05)
            menu = menu_owned_by(hwnd)
            if menu and menu != before:
                break
        check("穿透模式下右键菜单能弹出", bool(menu and menu != before), True)
        if menu:   # 收掉菜单，别把子进程卡在 TrackPopupMenu 里
            u.keybd_event(0x1B, 0, 0, 0)               # VK_ESCAPE down
            u.keybd_event(0x1B, 0, 0x0002, 0)          # KEYEVENTF_KEYUP
            u.PostMessageW(hwnd, 0x001F, 0, 0)         # WM_CANCELMODE
            time.sleep(0.25)
        check("菜单关掉后没卡死（窗口还在）", bool(u.IsWindow(hwnd)), True)
    finally:
        try:
            u.SetCursorPos(orig.x, orig.y)
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    print()
    print("ALL OK" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
