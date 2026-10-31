#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直播姬直接直播用的桌面透明叠加层。

OBS 走浏览器源；直播姬没有稳定的浏览器源，所以这里用 Win32 分层窗口画一块
**真透明、置顶、点击穿透**的画布。直播姬用「显示器捕获」抓这一块屏时，叠加层
会和游戏/视频一起进画面；它自己不吃鼠标，右键菜单仍然由桌宠控制。

设计取舍：
- 只读 ``/state``、``/api/bilibili/scene`` 和 ``logs/bilibili_events.jsonl``，
  不碰游戏进程，不读页面，不发弹幕；
- 模板用 Pillow 画，不引入 WebView（Chromium 窗口没有逐像素 alpha）；
- 只在状态变化时重画，空闲时几乎不占 CPU；直播推流卡顿时叠加层不能成为新瓶颈。

可以直接生成预览图：

    python -m dafeiyu.ui.stream_overlay --render out.png --template cinema
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
import urllib.request
from collections import OrderedDict
from ctypes import wintypes
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
STATE_FILE = ROOT / "logs" / "streamer_overlay.json"
CHAT_FILE = ROOT / "logs" / "bilibili_events.jsonl"

# 模板顺序就是右键菜单顺序；off 单独处理。
TEMPLATES: "OrderedDict[str, str]" = OrderedDict([
    ("bar", "状态条"),
    ("cinema", "电影框"),
    ("chat", "弹幕栏"),
    ("minimal", "极简字幕"),
])
DEFAULT_TEMPLATE = "off"

# 设计色板和 overlay/index.html 保持同一套。
GOLD = (244, 201, 104, 255)
GOLD_SOFT = (244, 201, 104, 150)
TEXT = (247, 241, 227, 255)
MUTED = (200, 207, 222, 190)
DIM = (160, 168, 184, 170)
PINK = (255, 107, 138, 255)
PANEL = (9, 11, 17, 216)
PANEL_SOFT = (8, 10, 15, 184)
LINE = (244, 201, 104, 90)

FONT_REG = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]
FONT_BOLD = [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyhbd.ttf"] + FONT_REG
_font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def get_font(size: int, bold: bool = False):
    key = (max(8, int(size)), bool(bold))
    if key in _font_cache:
        return _font_cache[key]
    for path in FONT_BOLD if bold else FONT_REG:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, key[0])
                _font_cache[key] = font
                return font
            except OSError:
                continue
    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


# ---------------------------------------------------------------- 数据
def clean_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return text[:max(1, int(limit))]


def fmt_time(value: Any) -> str:
    try:
        seconds = max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        seconds = 0
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def load_template(path: str | Path | None = None) -> str:
    try:
        raw = json.loads(Path(path or STATE_FILE).read_text(encoding="utf-8"))
        key = str(raw.get("template") or DEFAULT_TEMPLATE).strip().lower()
    except Exception:
        key = DEFAULT_TEMPLATE
    return key if key in TEMPLATES else DEFAULT_TEMPLATE


def save_template(template: str, path: str | Path | None = None) -> str:
    key = str(template or DEFAULT_TEMPLATE).strip().lower()
    if key not in TEMPLATES:
        key = DEFAULT_TEMPLATE
    target = Path(path or STATE_FILE)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"template": key}, ensure_ascii=False),
                          encoding="utf-8")
    except OSError:
        pass
    return key


def status_line(status: dict) -> str:
    title = clean_text(status.get("video_title"), 80)
    if title:
        duration = float(status.get("duration_s") or 0)
        position = float(status.get("position_s") or 0)
        if duration > 0:
            return f"视频 · {fmt_time(position)} / {fmt_time(duration)}"
        return "视频播放中"
    parts: list[str] = []
    character = clean_text(status.get("character"), 24)
    if character:
        parts.append(character)
    try:
        floor = int(status.get("floor") or 0)
    except (TypeError, ValueError):
        floor = 0
    if floor:
        parts.append(f"第 {floor} 层")
    try:
        hp, max_hp = int(status.get("hp") or 0), int(status.get("max_hp") or 0)
    except (TypeError, ValueError):
        hp = max_hp = 0
    if max_hp:
        parts.append(f"HP {hp}/{max_hp}")
    if parts:
        return " · ".join(parts)
    return clean_text(status.get("last") or status.get("screen") or "直播中", 90)


def title_text(status: dict) -> str:
    return clean_text(
        status.get("video_title") or status.get("character") or "大肥鱼", 90)


def latest_speech(state: dict, limit: int = 140) -> str:
    comments = state.get("comments")
    if isinstance(comments, list):
        for row in reversed(comments[-6:]):
            if not isinstance(row, dict):
                continue
            for key in ("speech", "thought", "meme"):
                text = clean_text(row.get(key), limit)
                if text:
                    return text
    status = state.get("status") if isinstance(state.get("status"), dict) else {}
    for key in ("last", "idle_text"):
        text = clean_text(status.get(key), limit)
        if text and not text.startswith("（还没收到"):
            return text
    return "直播中"


def recent_speeches(state: dict, limit: int = 4) -> list[str]:
    out: list[str] = []
    comments = state.get("comments")
    if isinstance(comments, list):
        for row in comments:
            if not isinstance(row, dict):
                continue
            text = clean_text(row.get("speech") or row.get("thought") or row.get("meme"), 90)
            if text:
                out.append(text)
    return out[-max(1, int(limit)):]


def fetch_state(timeout: float = 0.8) -> dict:
    port = os.environ.get("OVERLAY_PORT", "8730")
    url = f"http://127.0.0.1:{int(port) if str(port).isdigit() else 8730}/state"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_chats(path: str | Path | None = None, limit: int = 8) -> list[dict]:
    """只读最近几条弹幕/礼物；文件由桌宠监听线程写，读到半行就跳过。"""
    target = Path(path or CHAT_FILE)
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
    except OSError:
        return []
    rows: list[dict] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("kind") or "") not in {"chat", "gift", "super_chat", "guard"}:
            continue
        rows.append(row)
    return rows[-max(1, int(limit)):]


def chat_line(row: dict) -> tuple[str, str, bool]:
    """返回 (用户, 正文, 是否礼物)。"""
    kind = str(row.get("kind") or "")
    user = clean_text(row.get("user"), 18) or "观众"
    if kind == "gift":
        return user, f"送出 {clean_text(row.get('gift'), 18)} ×{int(row.get('count') or 1)}", True
    if kind == "super_chat":
        return user, f"SC ¥{int(row.get('coin') or 0)} {clean_text(row.get('text'), 70)}", True
    if kind == "guard":
        return user, f"上舰 {clean_text(row.get('gift'), 18)}", True
    return user, clean_text(row.get("text"), 80) or "…", False


# ---------------------------------------------------------------- 绘制
def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> str:
    text = clean_text(text, 240)
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    left, right = 0, len(text)
    while left < right:
        mid = (left + right + 1) // 2
        if draw.textlength(text[:mid] + ellipsis, font=font) <= max_width:
            left = mid
        else:
            right = mid - 1
    return text[:left].rstrip() + ellipsis


def _panel(draw: ImageDraw.ImageDraw, box, radius: int, fill, outline=LINE,
           width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=max(2, int(radius)), fill=fill,
                           outline=outline, width=max(1, int(width)))


def render_overlay(template: str, state: dict | None = None,
                   chats: list[dict] | None = None,
                   width: int = 1920, height: int = 1080,
                   now: float | None = None) -> Image.Image:
    """把模板画到一张全屏 RGBA 图；透明区域保持 0 alpha。"""
    width, height = max(320, int(width)), max(240, int(height))
    state = state if isinstance(state, dict) else {}
    chats = chats if isinstance(chats, list) else []
    status = state.get("status") if isinstance(state.get("status"), dict) else {}
    scale = max(0.68, min(2.0, height / 1080.0))
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    template = str(template or "").strip().lower()
    if template == "cinema":
        _draw_cinema(draw, img, status, state, scale, width, height)
    elif template == "chat":
        _draw_chat(draw, img, status, state, chats, scale, width, height)
    elif template == "minimal":
        _draw_minimal(draw, img, status, state, scale, width, height)
    else:
        _draw_bar(draw, img, status, state, scale, width, height)
    return img


def _draw_bar(draw, img, status, state, scale, width, height) -> None:
    margin = int(28 * scale)
    panel_w = min(width - 2 * margin, int(980 * scale))
    panel_h = int(138 * scale)
    x, y = margin, margin
    _panel(draw, (x, y, x + panel_w, y + panel_h), int(26 * scale), PANEL)
    draw.rounded_rectangle(
        (x + int(20 * scale), y + int(28 * scale),
         x + int(25 * scale), y + panel_h - int(28 * scale)),
        radius=int(3 * scale), fill=GOLD)
    left = x + int(44 * scale)
    right = x + panel_w - int(20 * scale)
    draw.text((left, y + int(18 * scale)), "大肥鱼",
              font=get_font(int(29 * scale), True), fill=GOLD)
    draw.text((right, y + int(24 * scale)), "AI 生成", anchor="ra",
              font=get_font(int(14 * scale)), fill=DIM)
    detail = _fit(draw, status_line(status), get_font(int(18 * scale)),
                  right - left)
    draw.text((left, y + int(60 * scale)), detail,
              font=get_font(int(18 * scale)), fill=MUTED)
    speech = _fit(draw, latest_speech(state, 110), get_font(int(23 * scale), True),
                  right - left)
    draw.text((left, y + int(94 * scale)), speech,
              font=get_font(int(23 * scale), True), fill=TEXT)
    draw.ellipse((right - int(10 * scale), y + int(17 * scale),
                  right - int(3 * scale), y + int(24 * scale)), fill=PINK)


def _draw_cinema(draw, img, status, state, scale, width, height) -> None:
    top_h = max(int(64 * scale), int(86 * scale))
    bottom_h = max(int(98 * scale), int(154 * scale))
    draw.rectangle((0, 0, width, top_h), fill=(5, 7, 11, 214))
    draw.rectangle((0, height - bottom_h, width, height), fill=(5, 7, 11, 230))
    draw.line((0, top_h - 1, width, top_h - 1), fill=GOLD_SOFT, width=max(1, int(scale)))
    draw.line((0, height - bottom_h, width, height - bottom_h), fill=GOLD_SOFT,
              width=max(1, int(scale)))
    pad = int(34 * scale)
    title = _fit(draw, title_text(status), get_font(int(25 * scale), True),
                 width - pad * 2 - int(260 * scale))
    draw.text((pad, top_h // 2), title, anchor="lm",
              font=get_font(int(25 * scale), True), fill=TEXT)
    draw.text((width - pad, top_h // 2), "直播中 · AI 生成", anchor="rm",
              font=get_font(int(15 * scale)), fill=DIM)
    speech = latest_speech(state, 150)
    draw.text((width // 2, height - bottom_h + int(42 * scale)),
              _fit(draw, speech, get_font(int(30 * scale), True), width - pad * 2),
              anchor="mm", font=get_font(int(30 * scale), True), fill=TEXT)
    detail = _fit(draw, status_line(status), get_font(int(16 * scale)),
                  width - pad * 2)
    draw.text((width // 2, height - int(34 * scale)), detail, anchor="mm",
              font=get_font(int(16 * scale)), fill=MUTED)
    duration = float(status.get("duration_s") or 0)
    position = float(status.get("position_s") or 0)
    if duration > 0:
        ratio = max(0.0, min(1.0, position / duration))
        draw.rectangle((0, height - max(3, int(4 * scale)), int(width * ratio), height),
                       fill=PINK)


def _draw_chat(draw, img, status, state, chats, scale, width, height) -> None:
    margin = int(28 * scale)
    panel_w = min(int(width * 0.30), int(450 * scale))
    x = width - panel_w - margin
    y = int(74 * scale)
    bottom = height - int(68 * scale)
    _panel(draw, (x, y, x + panel_w, bottom), int(22 * scale), PANEL_SOFT)
    left = x + int(24 * scale)
    right = x + panel_w - int(20 * scale)
    draw.text((left, y + int(18 * scale)), "直播弹幕",
              font=get_font(int(22 * scale), True), fill=GOLD)
    draw.ellipse((right - int(12 * scale), y + int(22 * scale),
                  right - int(4 * scale), y + int(30 * scale)), fill=PINK)
    rows = chats[-6:]
    if not rows:
        rows = [{"kind": "chat", "user": "大肥鱼", "text": text}
                for text in recent_speeches(state, 3)]
    row_h = int(58 * scale)
    top = y + int(58 * scale)
    for index, row in enumerate(rows[:6]):
        row_y = top + index * row_h
        if row_y + row_h > bottom - int(32 * scale):
            break
        user, text, gift = chat_line(row)
        draw.text((left, row_y), _fit(draw, user, get_font(int(14 * scale), True),
                                      right - left),
                  font=get_font(int(14 * scale), True), fill=GOLD if not gift else PINK)
        draw.text((left, row_y + int(21 * scale)),
                  _fit(draw, text, get_font(int(16 * scale)), right - left),
                  font=get_font(int(16 * scale)), fill=TEXT)
    draw.text((right, bottom - int(22 * scale)), "AI 生成", anchor="ra",
              font=get_font(int(12 * scale)), fill=DIM)


def _draw_minimal(draw, img, status, state, scale, width, height) -> None:
    margin = int(28 * scale)
    panel_w = min(width - 2 * margin, int(980 * scale))
    panel_h = int(64 * scale)
    x, y = margin, height - panel_h - margin
    _panel(draw, (x, y, x + panel_w, y + panel_h), int(18 * scale), PANEL_SOFT)
    draw.ellipse((x + int(20 * scale), y + int(25 * scale),
                  x + int(29 * scale), y + int(34 * scale)), fill=GOLD)
    name = "大肥鱼"
    name_font = get_font(int(17 * scale), True)
    draw.text((x + int(42 * scale), y + int(20 * scale)), name,
              font=name_font, fill=GOLD)
    left = x + int(42 * scale) + int(draw.textlength(name, font=name_font)) + int(14 * scale)
    right = x + panel_w - int(18 * scale)
    text = _fit(draw, latest_speech(state, 120), get_font(int(21 * scale), True),
                right - left)
    draw.text((left, y + int(18 * scale)), text,
              font=get_font(int(21 * scale), True), fill=TEXT)
    detail = _fit(draw, status_line(status), get_font(int(13 * scale)),
                  right - left)
    draw.text((left, y + int(43 * scale)), detail,
              font=get_font(int(13 * scale)), fill=MUTED)


# ---------------------------------------------------------------- Win32 窗口
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WM_DESTROY = 0x0002
WM_DISPLAYCHANGE = 0x007E
ULW_ALPHA = 0x02
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


def _init_winapi() -> None:
    u, g = user32, gdi32
    u.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    u.CreateWindowExW.restype = wintypes.HWND
    u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    u.RegisterClassW.restype = wintypes.ATOM
    u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.DefWindowProcW.restype = ctypes.c_ssize_t
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.DestroyWindow.argtypes = [wintypes.HWND]
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u.UpdateLayeredWindow.argtypes = [
        wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
        ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
        wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
    ]
    u.GetDC.argtypes = [wintypes.HWND]
    u.GetDC.restype = wintypes.HDC
    u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT, wintypes.UINT]
    u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u.DispatchMessageW.restype = ctypes.c_ssize_t
    u.GetSystemMetrics.argtypes = [ctypes.c_int]
    u.GetSystemMetrics.restype = ctypes.c_int
    u.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p,
                                      ctypes.c_void_p, wintypes.LPARAM]
    u.EnumDisplayMonitors.restype = ctypes.c_bool
    u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    u.GetMonitorInfoW.restype = ctypes.c_bool
    g.CreateCompatibleDC.argtypes = [wintypes.HDC]
    g.CreateCompatibleDC.restype = wintypes.HDC
    g.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
    ]
    g.CreateDIBSection.restype = wintypes.HBITMAP
    g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    g.SelectObject.restype = wintypes.HGDIOBJ
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteDC.argtypes = [wintypes.HDC]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
    try:
        u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            u.SetProcessDPIAware()
        except Exception:
            pass


_init_winapi()


def premultiplied_bgra(img: Image.Image) -> bytes:
    a = img.getchannel("A")
    r = ImageChops.multiply(img.getchannel("R"), a)
    g = ImageChops.multiply(img.getchannel("G"), a)
    b = ImageChops.multiply(img.getchannel("B"), a)
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


def monitor_rects() -> list[tuple[bool, tuple[int, int, int, int]]]:
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    found: list[tuple[bool, tuple[int, int, int, int]]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC,
                        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def collect(hmonitor, _hdc, _rect, _data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            m = info.rcMonitor
            found.append((bool(info.dwFlags & 1),
                          (int(m.left), int(m.top), int(m.right), int(m.bottom))))
        return True

    try:
        user32.EnumDisplayMonitors(0, 0, collect, 0)
    except Exception:
        pass
    if found:
        return found
    return [(True, (0, 0, int(user32.GetSystemMetrics(0)),
                    int(user32.GetSystemMetrics(1))))]


def choose_rect(monitor: str = "secondary") -> tuple[int, int, int, int]:
    items = monitor_rects()
    key = str(monitor or "secondary").strip().lower()
    if key.isdigit():
        index = int(key)
        if 0 <= index < len(items):
            return items[index][1]
    if key == "primary":
        for primary, rect in items:
            if primary:
                return rect
    for primary, rect in items:
        if not primary:
            return rect
    return items[0][1]


class OverlayFeed:
    """后台拉 `/state` 和弹幕文件：HTTP 慢不能卡桌宠主循环。"""

    def __init__(self, interval: float = 0.8) -> None:
        self.interval = max(0.2, float(interval))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._state: dict = {}
        self._chats: list[dict] = []
        self._thread = threading.Thread(target=self._loop, name="dafeiyu-overlay-feed",
                                        daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            state = fetch_state()
            chats = read_chats()
            with self._lock:
                self._state = state if isinstance(state, dict) else {}
                self._chats = chats if isinstance(chats, list) else []
            self._stop.wait(self.interval)

    def snapshot(self) -> tuple[dict, list[dict]]:
        with self._lock:
            return dict(self._state), list(self._chats)

    def stop(self) -> None:
        self._stop.set()


class OverlayWindow:
    """一个全屏透明窗口；由桌宠主循环调用 ``tick``，不自己开线程泵消息。"""

    def __init__(self, template: str, monitor: str = "secondary") -> None:
        self.template = template if template in TEMPLATES else "minimal"
        self.monitor = monitor
        self.rect = choose_rect(monitor)
        self._wndproc = WNDPROC(self._proc)
        self._hwnd = 0
        self._memdc = None
        self._hbmp = None
        self._dib = None
        self._dib_size = (0, 0)
        self._feed = None
        self._last_state: dict = {}
        self._last_chats: list[dict] = []
        self._dirty = True
        self._register()
        self._create()
        self._feed = OverlayFeed()

    def _register(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = f"DafeiyuOverlay{os.getpid()}"
        user32.RegisterClassW(ctypes.byref(wc))
        self._hinst = hinst

    def _create(self) -> None:
        ex = (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
              | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        left, top, right, bottom = self.rect
        self._hwnd = user32.CreateWindowExW(
            ex, f"DafeiyuOverlay{os.getpid()}", "大肥鱼叠加层", WS_POPUP,
            left, top, max(320, right - left), max(240, bottom - top),
            None, None, self._hinst, None)
        if not self._hwnd:
            raise OSError("创建叠加层窗口失败")
        user32.ShowWindow(self._hwnd, 8)  # SW_SHOWNA
        user32.SetWindowPos(self._hwnd, HWND_TOPMOST, left, top,
                            max(320, right - left), max(240, bottom - top),
                            SWP_SHOWWINDOW | SWP_NOACTIVATE)
        self._paint(render_overlay(self.template, width=right - left,
                                   height=bottom - top))

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DESTROY:
            self._hwnd = 0
        elif msg == WM_DISPLAYCHANGE:
            self._dirty = True
        try:
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception:
            return 0

    def set_template(self, template: str) -> None:
        if template in TEMPLATES and template != self.template:
            self.template = template
            self._dirty = True

    def tick(self) -> None:
        if not self._hwnd:
            return
        state, chats = self._feed.snapshot()
        if state != self._last_state or chats != self._last_chats:
            self._last_state = state
            self._last_chats = chats
            self._dirty = True
        if not self._dirty:
            return
        left, top, right, bottom = self.rect
        img = render_overlay(self.template, state, chats,
                             max(320, right - left), max(240, bottom - top))
        self._paint(img)
        self._dirty = False

    def _paint(self, img: Image.Image) -> None:
        if not self._hwnd:
            return
        w, h = img.size
        hdc_screen = user32.GetDC(None)
        if self._memdc is None:
            self._memdc = gdi32.CreateCompatibleDC(hdc_screen)
        if self._dib_size != (w, h):
            if self._hbmp:
                gdi32.DeleteObject(self._hbmp)
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0
            self._dib = ctypes.c_void_p()
            self._hbmp = gdi32.CreateDIBSection(
                self._memdc, ctypes.byref(bmi), 0, ctypes.byref(self._dib), None, 0)
            gdi32.SelectObject(self._memdc, self._hbmp)
            self._dib_size = (w, h)
        buf = premultiplied_bgra(img)
        ctypes.memmove(self._dib, buf, len(buf))
        pt_dst = wintypes.POINT(self.rect[0], self.rect[1])
        size = wintypes.SIZE(w, h)
        pt_src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(self._hwnd, hdc_screen, ctypes.byref(pt_dst),
                                   ctypes.byref(size), self._memdc,
                                   ctypes.byref(pt_src), 0, ctypes.byref(blend),
                                   ULW_ALPHA)
        user32.ReleaseDC(None, hdc_screen)

    def close(self) -> None:
        if self._feed is not None:
            self._feed.stop()
            self._feed = None
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = 0
        if self._hbmp:
            try:
                gdi32.DeleteObject(self._hbmp)
            except Exception:
                pass
            self._hbmp = None
        if self._memdc:
            try:
                gdi32.DeleteDC(self._memdc)
            except Exception:
                pass
            self._memdc = None
        self._dib_size = (0, 0)


# ---------------------------------------------------------------- CLI
def _preview_state() -> dict:
    return {
        "comments": [
            {"speech": "这波它 21，我最多挡 15，先破甲再算。"},
            {"speech": "差 6 点，嗨呀，但是还能打。"},
        ],
        "status": {
            "character": "铁甲战士", "floor": 8, "hp": 23, "max_hp": 80,
            "screen": "COMBAT", "last": "第 8 层 · 战斗",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="大肥鱼直播姬桌面叠加层")
    parser.add_argument("--template", choices=list(TEMPLATES), default="bar")
    parser.add_argument("--monitor", default="secondary",
                        help="secondary / primary / 显示器索引")
    parser.add_argument("--render", help="只渲染一张 PNG 就退出")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    args = parser.parse_args(argv)
    if args.render:
        img = render_overlay(args.template, _preview_state(),
                             [{"kind": "chat", "user": "观众", "text": "这手稳"}],
                             args.width, args.height)
        img.save(args.render)
        print(f"PREVIEW {args.render} {img.width}x{img.height}")
        return 0
    overlay = OverlayWindow(args.template, args.monitor)
    try:
        while overlay._hwnd:
            # 独立运行时自己泵消息，方便手工调试；桌宠内嵌时由桌宠主循环调用 tick。
            msg = wintypes.MSG()
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            overlay.tick()
            time.sleep(0.08)
    except KeyboardInterrupt:
        pass
    finally:
        overlay.close()
    return 0


__all__ = [
    "TEMPLATES", "OverlayWindow", "choose_rect", "fetch_state", "load_template",
    "read_chats", "render_overlay", "save_template",
]


if __name__ == "__main__":
    raise SystemExit(main())
