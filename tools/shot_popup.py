# -*- coding: utf-8 -*-
"""主播窗（弹窗）像素探针：证明窗口**真的画出了东西**，不是只看进程在不在。

为什么需要它：主播窗默认带 `WDA_EXCLUDEFROMCAPTURE`（"录屏里隐藏"），
用 `CopyFromScreen` / OBS / 直播姬抓屏**永远拍不到它**——抓出来是桌面背景。
2026-10-27 排查"界面看不到弹窗"时就吃过这个亏：截图一片空，差点误判成
"窗口没画"。所以验证弹窗的判据只能是问窗口自己：`PrintWindow(PW_RENDERFULLCONTENT)`
取它自己的分层位图，数一下有多少不透明像素。

    python tools/shot_popup.py                     # 打印状态 + 存 logs/popup_shot.png
    python tools/shot_popup.py --out logs/x.png    # 指定输出
    python tools/shot_popup.py --quiet             # 只打印一行结论（体检用）

退出码：0 = 窗口在且画出了像素；1 = 找不到窗口 / 位图全透明。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEARTBEAT = os.path.join(ROOT, "logs", "streamer_heartbeat.json")
TITLE = "大肥鱼"

u = ctypes.windll.user32
g = ctypes.windll.gdi32


class _BMI(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.UINT), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.UINT),
        ("biSizeImage", wintypes.UINT), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.UINT),
        ("biClrImportant", wintypes.UINT),
    ]


_ENUM_CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def heartbeat_pid() -> int | None:
    """主播窗每轮询都会写心跳（≤2s 一次），里面带 pid。"""
    try:
        with open(HEARTBEAT, encoding="utf-8") as f:
            pid = json.load(f).get("pid")
        return int(pid) if pid else None
    except (OSError, ValueError, TypeError):
        return None


def find_windows(pid: int | None = None) -> list[tuple[int, str, int]]:
    """找主播窗（有标题的顶层窗口）。pid 为空则按标题认。"""
    found: list[tuple[int, str, int]] = []

    def _cb(hwnd, _lparam):
        wpid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if pid is not None and wpid.value != pid:
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        if pid is None and TITLE not in buf.value:
            return True
        found.append((hwnd, buf.value, wpid.value))
        return True

    u.EnumWindows(_ENUM_CB(_cb), 0)
    return found


def grab(hwnd: int):
    """取窗口自己的位图（RGBA）。拍不到就返回 None。"""
    from PIL import Image

    rect = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None, (rect.left, rect.top, w, h)
    hdc = u.GetWindowDC(hwnd)
    if not hdc:
        return None, (rect.left, rect.top, w, h)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    try:
        if not u.PrintWindow(hwnd, mdc, 2):      # 2 = PW_RENDERFULLCONTENT
            return None, (rect.left, rect.top, w, h)
        bi = _BMI()
        bi.biSize = ctypes.sizeof(_BMI)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        buf = ctypes.create_string_buffer(w * h * 4)
        g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
        return img.copy(), (rect.left, rect.top, w, h)
    finally:
        g.DeleteObject(bmp)
        g.DeleteDC(mdc)
        u.ReleaseDC(hwnd, hdc)


def probe(out: str | None = None) -> dict:
    """体检用：返回 {ok, msg, ...}。ok 只在「窗口在且画出像素」时为 True。"""
    pid = heartbeat_pid()
    wins = find_windows(pid)
    if not wins:
        who = f"pid {pid}" if pid else "按标题"
        return {"ok": False, "msg": f"没找到主播窗（{who}）", "pid": pid}
    hwnd, title, wpid = wins[0]
    img, box = grab(hwnd)
    if img is None:
        return {"ok": False, "msg": f"窗口 {wpid} 在，但抓不到位图", "pid": wpid,
                "hwnd": hwnd, "box": box}
    alpha = img.getchannel("A")
    opaque = sum(1 for v in alpha.get_flattened_data() if v > 8) if hasattr(alpha, "get_flattened_data") \
        else sum(1 for v in alpha.getdata() if v > 8)
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        img.save(out)
    ok = opaque > 500
    return {"ok": ok, "pid": wpid, "hwnd": hwnd, "box": box, "title": title,
            "opaque_px": opaque, "out": out,
            "msg": f"{wpid} 弹出 {box[2]}x{box[3]}，不透明像素 {opaque}" +
                   ("" if ok else "（窗口在但没画出东西）")}


def main() -> int:
    ap = argparse.ArgumentParser(description="主播窗像素探针（抓自己的分层位图）")
    ap.add_argument("--out", default=os.path.join(ROOT, "logs", "popup_shot.png"))
    ap.add_argument("--quiet", action="store_true", help="只打一行结论")
    args = ap.parse_args()
    r = probe(args.out)
    if args.quiet:
        print(r["msg"])
    else:
        print(json.dumps({k: v for k, v in r.items() if k != "title"}, ensure_ascii=False))
        if r.get("out"):
            print("SHOT", r["out"])
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
