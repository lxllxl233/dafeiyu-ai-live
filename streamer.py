#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主播模式：一个可拖动的透明弹幕窗，和 OBS 里那块「同款」。

为什么要单独写一个窗：OBS 浏览器源能吃到透明通道，但普通浏览器窗口不行
（Chromium 的窗口不支持逐像素 alpha，色键在 WebView2/Edge 上也不稳）。
所以这里用 Win32 分层窗口 + Pillow 现画：真透明、无边框、置顶、可拖，
数据还是从 `overlay_server.py` 的 `/state` 拿，和 OBS 里那条气泡同一份。

用法：
    python streamer.py                 # 起弹幕窗（自动拉起 overlay server）
    python streamer.py --demo          # 不接游戏，先放一条示例弹幕试位置
    python streamer.py --click-through # 开局就点击穿透（打游戏时鼠标直接穿过去）
    python streamer.py --preview out.png   # 只渲染一张图（不看窗口，用于验收/截图）

窗口里能做的事：
    左键拖动  → 挪位置（松手自动记住，下次原地开）
    右下角拖动 → 缩放（等比，0.5×–1.8×；缩放后大小也记住）
    滚轮      → 缩放（同一个比例，懒得找角就用它）
    右键      → 菜单：置顶 / 点击穿透 / 声音 / 录屏里隐藏 / 重新载入示例 / 关闭
    Ctrl+Alt+D 点击穿透开关（穿透后鼠标点不到窗口，用它切回来）
    Ctrl+Alt+H 隐藏/显示
    Ctrl+Alt+S 声音开关
    Ctrl+Alt+Q 退出

直播时怎么用：OBS 的 `StS2 Game` 是 game_capture（只钩游戏进程），本来就不会录到这个窗；
另外窗口默认带 WDA_EXCLUDEFROMCAPTURE，就算以后切成显示器采集也拍不到它（要拍就 --capture）。
声音是页面同款动物语（同一个五声音阶合成），本地播放，不进录像。
"""
from __future__ import annotations

import argparse
import ctypes
import io
import json
import math
import os
import random
import re
import struct
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from ctypes import wintypes

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
except ImportError:  # pragma: no cover
    print("需要 Pillow：pip install -r requirements.txt")
    raise SystemExit(2)

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, "overlay", "assets")
STATE_FILE = os.path.join(ROOT, "logs", "streamer_window.json")
PORT = int(os.environ.get("STS2_OVERLAY_PORT", "8730"))
PLACEHOLDER = "（还没收到弹幕 —— agent 一开口，这里就换成它说的话）"

# ---------------------------------------------------------------- 设计参数
# 数值直接抄 overlay/index.html 的 CSS，改这里等于改「同款」的还原度。
PANEL_W = 1180
BASE_W = PANEL_W + 2 * 26        # 1.0 倍时的窗口宽（含两侧留白）：缩放以它为准
PAD = (12, 26, 16, 16)          # top right bottom left
RADIUS = 24
BORDER = 1.5
AVATAR_COL = 150
PORTRAIT = 144
GAP = 18
HEAD_H = 20
FEED_GAP = 6

C_PANEL_TOP = (30, 27, 35, 242)      # rgba(30,27,35,.95)
C_PANEL_BOT = (12, 13, 20, 224)      # rgba(12,13,20,.88)
C_BORDER = (216, 168, 74, 158)       # rgba(216,168,74,.62)
C_GOLD = (244, 201, 104, 255)        # #f4c968
C_TEXT = (251, 246, 234, 255)        # #fbf6ea
C_STATUS = (207, 214, 230, 209)      # rgba(207,214,230,.82)
C_STATUS_B = (255, 255, 255, 255)
C_NAME = (244, 201, 104, 255)
C_TAGLINE = (255, 255, 255, 107)     # rgba(255,255,255,.42)

FONT_REG = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]
FONT_BOLD = [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyhbd.ttf"] + FONT_REG

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32


def dpi_scale() -> float:
    try:
        dc = user32.GetDC(None)
        dpi = gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
        user32.ReleaseDC(None, dc)
        return max(1.0, dpi / 96.0)
    except Exception:
        return 1.0


_font_cache: dict = {}


def get_font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    for path in FONT_BOLD if bold else FONT_REG:
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size)
                _font_cache[key] = f
                return f
            except OSError:
                continue
    f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def vgradient(w: int, h: int, top, bot) -> Image.Image:
    """竖直渐变：先用 1px 宽的列画，再横向拉满（不引 numpy）。"""
    col = Image.new("RGBA", (1, max(1, h)))
    px = col.load()
    for y in range(max(1, h)):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(4))
    return col.resize((max(1, w), max(1, h)))


_portrait_cache: dict = {}


def load_portrait(size: int) -> Image.Image:
    """圆形头像：等价 CSS object-fit: cover + object-position: center 26% + scale(1.06)。"""
    if size in _portrait_cache:
        return _portrait_cache[size]
    path = os.path.join(ASSETS, "deepseek_fish.jpg")
    if not os.path.exists(path):
        path = os.path.join(ASSETS, "deepseek_fish.png")
    try:
        src = Image.open(path).convert("RGB")
    except Exception:
        src = Image.new("RGB", (size, size), (50, 90, 134))
    sw, sh = src.size
    zoom = 1.06
    scale = max(size / sw, size / sh) * zoom
    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
    src = src.resize((nw, nh), Image.LANCZOS)
    left = (nw - size) // 2
    top = int((nh - size) * 0.26)
    src = src.crop((left, top, left + size, top + size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(src, (0, 0), mask)
    _portrait_cache[size] = out
    return out


def wrap_cjk(text: str, font, max_w: float, draw: ImageDraw.ImageDraw) -> list[str]:
    """中文按字断行，英文/数字尽量整词。"""
    lines, cur = [], ""
    for ch in text:
        trial = cur + ch
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
            continue
        # 行尾是标点就允许挂出去，读起来更像人话
        if ch in "，。、；：！？…）】」'\"%":
            cur = trial
            lines.append(cur)
            cur = ""
            continue
        lines.append(cur)
        cur = ch
    if cur:
        lines.append(cur)
    return lines


def compose(thought: str, meme: str) -> str:
    """和页面里的 composeSentence 一致：思路收句号，吐槽接在后面。"""
    def stop(s: str) -> str:
        return s if s and s[-1] in "。！？!?…”）)" else s + "。"

    t = (thought or "").strip().rstrip("。 ")
    m = (meme or "").strip().lstrip("，,、。；;—–- ")
    if not t:
        return stop(m) if m else ""
    if not m:
        return stop(t)
    return stop(t) + stop(m)


MAX_THOUGHT = 120   # 和 index.html 一致：超长宁可省略，也不让气泡铺满屏
MAX_MEME = 70


def clip(s: str, n: int) -> str:
    t = (s or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 1].rstrip(" ，,、") + "…"


# ---------------------------------------------------------------- 声音
# 和 overlay/index.html 的 Animalese 同一套规则：每个字一个短音节、五声音阶随机上下行。
# 页面用 Web Audio 合成；窗口这边用纯 Python 合成成一小段 WAV，winsound 异步播放。
SCALE = (0, 2, 4, 7, 9, 12)
SPEAK_RATE = 44100
_SPOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_PUNCT_RE = re.compile(r"[，,、。；;：:！!？?…—]")


def _blip(freq: float, dur: float, rate: int) -> list[float]:
    """一个音节：三角波 + 低八度方波，过一个低通，再套 6ms 起音 / 指数衰减。"""
    n = max(1, int(rate * dur))
    out = []
    sub_gain = 0.35
    lp = 0.0
    alpha = 1.0 - math.exp(-2.0 * math.pi * min(3200.0, freq * 3.2) / rate)
    phase = 0.0
    phase_sub = 0.0
    for i in range(n):
        t = i / rate
        # 频率轻微下滑（freq → freq*0.94），和页面一致
        f = freq * (1.0 - 0.06 * (t / max(dur, 1e-6)))
        phase = (phase + f / rate) % 1.0
        phase_sub = (phase_sub + (f * 0.5) / rate) % 1.0
        tri = 4.0 * abs(phase - 0.5) - 1.0
        sq = 1.0 if phase_sub < 0.5 else -1.0
        x = tri + sub_gain * sq
        lp += alpha * (x - lp)
        # 包络：6ms 起音到 0.2，55% 处 0.05，结尾归零
        if t < 0.006:
            env = 0.2 * (t / 0.006)
        elif t < dur * 0.55:
            k = (t - 0.006) / max(dur * 0.55 - 0.006, 1e-6)
            env = 0.2 + (0.05 - 0.2) * k
        else:
            k = (t - dur * 0.55) / max(dur * 0.45, 1e-6)
            env = 0.05 * math.exp(-4.0 * k)
        out.append(lp * env)
    return out


def animalese_wav(text: str, volume: float = 0.55, rate: int = SPEAK_RATE) -> bytes | None:
    """把一句话合成成 WAV（16bit 单声道）。空句/没有可读字符返回 None。"""
    chars = list(text or "")
    total = len(_SPOKEN_RE.findall(text or ""))
    if not total:
        return None
    spoken_max = min(70, total)
    step = max(0.03, min(0.06, 2.3 / max(6, spoken_max)))
    dur = min(0.06, step * 0.92)

    buf: list[float] = [0.0] * int(rate * 0.05)   # 一点起手留白
    degree = 2 + int(random.random() * 2)
    spoken = 0
    for ch in chars:
        if ch.isspace():
            buf.extend([0.0] * int(rate * step * 0.55))
            continue
        if _PUNCT_RE.match(ch):
            buf.extend([0.0] * int(rate * step * 1.5))
            continue
        if not _SPOKEN_RE.match(ch):
            continue
        if spoken >= spoken_max:
            break
        spoken += 1
        degree = max(0, min(len(SCALE) - 1, degree + (1 if random.random() < 0.5 else -1)))
        semis = SCALE[degree] + (12 if random.random() < 0.12 else 0)
        freq = 520.0 * (2.0 ** (semis / 12.0)) * (0.985 + random.random() * 0.03)
        buf.extend(_blip(freq, dur, rate))
        buf.extend([0.0] * int(rate * max(0.0, step - dur)))

    peak = max((abs(v) for v in buf), default=0.0) or 1.0
    gain = (min(1.5, max(0.0, volume)) * 0.7) / max(peak, 0.9)
    frames = bytearray()
    for v in buf:
        s = int(max(-1.0, min(1.0, v * gain)) * 32767)
        frames += struct.pack("<h", s)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return out.getvalue()


class Speaker:
    """异步播放：合成放后台线程，别卡住 30fps 的重绘。

    注意：Win32 的 PlaySound 不支持「内存 + 异步」这个组合（会报
    Cannot play asynchronously from memory），所以先落一个临时 WAV 再
    用 SND_FILENAME|SND_ASYNC 播；两个文件轮着用，避免覆盖正在播的那个。
    """

    def __init__(self, volume: float = 0.55, enabled: bool = True):
        self.volume = volume
        self.enabled = enabled
        self._lock = threading.Lock()
        self._slot = 0
        self._paths = [os.path.join(tempfile.gettempdir(), f"sts2_voice_{i}.wav") for i in (0, 1)]
        self._ok = True
        try:
            import winsound  # noqa: F401
            self._winsound = winsound
        except Exception:
            self._ok = False

    def speak(self, text: str):
        if not (self.enabled and self._ok and text):
            return
        threading.Thread(target=self._play, args=(text,), daemon=True).start()

    def _play(self, text: str):
        try:
            wav = animalese_wav(text, self.volume)
            if not wav:
                return
            with self._lock:
                self._slot = 1 - self._slot
                path = self._paths[self._slot]
                with open(path, "wb") as f:
                    f.write(wav)
            self._winsound.PlaySound(
                path, self._winsound.SND_FILENAME | self._winsound.SND_ASYNC | self._winsound.SND_NODEFAULT
            )
        except Exception as e:
            print("音效播放失败：", e)

    def toggle(self) -> bool:
        self.enabled = not self.enabled
        if not self.enabled:
            try:
                self._winsound.PlaySound(None, 0)
            except Exception:
                pass
        return self.enabled



def status_segments(st: dict) -> list[tuple[str, tuple, bool]]:
    """右上角状态行：数字加粗，和页面里 <b> 的排版一致。"""
    st = st or {}
    segs: list[tuple[str, tuple, bool]] = []
    sep = (" · ", C_STATUS, False)
    if st.get("character"):
        segs += [(str(st["character"]), C_STATUS_B, True), sep]
    if st.get("ascension") is not None:
        segs += [(f"A{st['ascension']}", C_STATUS, False), sep]
    if st.get("floor"):
        segs += [(f"第 {st['floor']} 层", C_STATUS, False), sep]
    if st.get("hp") is not None:
        segs += [("HP ", C_STATUS, False), (str(st["hp"]), C_STATUS_B, True),
                 (f"/{st.get('max_hp', '?')}", C_STATUS, False), sep]
    if st.get("gold") is not None:
        segs += [("金币 ", C_STATUS, False), (str(st["gold"]), C_STATUS_B, True), sep]
    while segs and segs[-1][0] == " · ":
        segs.pop()
    return segs


_layout_cache: dict = {}
_bg_cache: dict = {}
_static_cache: dict = {}


def _layout(text: str, scale: float) -> dict:
    """算一次版式并缓存：换行很贵（每字一次 textlength），不能每帧重算。"""
    key = (text, round(scale, 3))
    hit = _layout_cache.get(key)
    if hit:
        return hit
    s = scale
    pad_t, pad_r, pad_b, pad_l = (int(v * s) for v in PAD)
    avatar_col = int(AVATAR_COL * s)
    portrait = int(PORTRAIT * s)
    gap = int(GAP * s)
    head_h = int(HEAD_H * s)
    feed_gap = int(FEED_GAP * s)
    panel_w = int(PANEL_W * s)

    f_status = get_font(int(13 * s))
    f_status_b = get_font(int(13 * s), bold=True)
    if len(text) > 170:
        f_text = get_font(int(16.5 * s))
    elif len(text) > 110:
        f_text = get_font(int(18.5 * s))
    else:
        f_text = get_font(int(20 * s))

    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    text_w = panel_w - pad_l - avatar_col - gap - pad_r
    lines = wrap_cjk(text, f_text, text_w, probe) if text.strip() else []
    line_h = int(round(f_text.size * 1.5))
    text_h = line_h * len(lines)

    avatar_h = portrait + int(6 * s) + int(17 * s) + int(13 * s)
    body_h = max(head_h + (feed_gap + text_h if lines else 0), avatar_h)
    panel_h = pad_t + body_h + pad_b

    margin = int(26 * s)
    L = {
        "s": s, "pad_t": pad_t, "pad_r": pad_r, "pad_b": pad_b, "pad_l": pad_l,
        "radius": int(RADIUS * s), "avatar_col": avatar_col, "portrait": portrait,
        "gap": gap, "head_h": head_h, "feed_gap": feed_gap,
        "panel_w": panel_w, "panel_h": panel_h, "margin": margin,
        "W": panel_w + margin * 2, "H": panel_h + margin * 2 + int(16 * s),
        "f_status": f_status, "f_status_b": f_status_b, "f_text": f_text,
        "f_name": get_font(int(13 * s)), "f_tag": get_font(int(10 * s)),
        "lines": lines, "line_h": line_h, "text_h": text_h,
    }
    L["px"], L["py"] = margin, margin
    L["tail_x"] = margin + int(78 * s) + int(9 * s)
    L["tail_y"] = margin + panel_h
    if len(_layout_cache) > 32:
        _layout_cache.clear()
    _layout_cache[key] = L
    return L


def _background(L: dict) -> Image.Image:
    """面板 + 金边 + 小尾巴 + 投影：和文字无关，缓存起来每帧只 copy。"""
    key = (L["panel_w"], L["panel_h"], L["radius"], round(L["s"], 3))
    hit = _bg_cache.get(key)
    if hit is not None:
        return hit
    s = L["s"]
    W, H = L["W"], L["H"]
    px, py = L["px"], L["py"]
    panel_w, panel_h, radius = L["panel_w"], L["panel_h"], L["radius"]

    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # 小尾巴（CSS 的 ::after 菱形），先画在面板下面，面板盖住上半
    tail_x, tail_y = L["tail_x"], L["tail_y"]
    d = int(9 * s)
    pd2 = ImageDraw.Draw(canvas)
    pd2.polygon(
        [(tail_x, tail_y - d), (tail_x + d, tail_y), (tail_x, tail_y + d), (tail_x - d, tail_y)],
        fill=(12, 13, 20, 232),
    )
    bw = max(1, int(BORDER * s))
    pd2.line([(tail_x, tail_y - d), (tail_x + d, tail_y)], fill=C_BORDER, width=bw)
    pd2.line([(tail_x + d, tail_y), (tail_x, tail_y + d)], fill=C_BORDER, width=bw)

    # 面板：渐变 + 圆角 + 金边 + 内高光
    panel = Image.new("RGBA", (panel_w, panel_h), (0, 0, 0, 0))
    mask = Image.new("L", (panel_w, panel_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, panel_w - 1, panel_h - 1), radius=radius, fill=255)
    panel.paste(vgradient(panel_w, panel_h, C_PANEL_TOP, C_PANEL_BOT), (0, 0), mask)
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle((0, 0, panel_w - 1, panel_h - 1), radius=radius, outline=C_BORDER, width=bw)
    pd.rounded_rectangle((2, 2, panel_w - 3, panel_h - 3), radius=max(2, radius - 2),
                         outline=(255, 255, 255, 13), width=1)

    # 阴影：CSS 0 14px 38px rgba(0,0,0,.55)
    blur = panel.getchannel("A").filter(ImageFilter.GaussianBlur(19 * s))
    sh_mask = Image.new("L", (W, H), 0)
    sh_mask.paste(blur.point(lambda v: int(v * 0.55)), (px, py + int(14 * s)))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow.paste(Image.new("RGBA", (W, H), (0, 0, 0, 255)), (0, 0), sh_mask)
    canvas = Image.alpha_composite(canvas, shadow)
    canvas.alpha_composite(panel, (px, py))
    if len(_bg_cache) > 8:
        _bg_cache.clear()
    _bg_cache[key] = canvas
    return canvas


def render(text: str, status: dict, scale: float = 1.0, talk: float = 0.0) -> Image.Image:
    """画一帧：返回带阴影的 RGBA 图（尺寸=窗口尺寸）。

    talk: 0~1，说话中头像轻微上下跳（对应页面里的 bounce 动画）。
    talk=0 的帧整张缓存，静止时不重复渲染。
    """
    L = _layout(text, scale)
    skey = tuple(sorted((k, str(v)) for k, v in (status or {}).items()))
    if talk <= 0:
        cached = _static_cache.get((text, skey, round(scale, 3)))
        if cached is not None:
            return cached

    s = L["s"]
    canvas = _background(L).copy()
    px, py = L["px"], L["py"]
    pad_t, pad_l = L["pad_t"], L["pad_l"]
    portrait = L["portrait"]

    draw = ImageDraw.Draw(canvas)

    # ---- 头像 + 名字 + 副标题
    bob = int(math.sin(talk * math.pi * 4) * 3 * s) if talk > 0 else 0
    ax = px + pad_l
    ay = py + pad_t + bob
    ring = max(2, int(3 * s))
    draw.ellipse((ax - ring, ay - ring, ax + portrait + ring, ay + portrait + ring),
                 outline=(20, 33, 58, 217), width=ring)
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((ax - ring * 2, ay - ring * 2, ax + portrait + ring * 2, ay + portrait + ring * 2),
               outline=(127, 214, 208, 46), width=max(1, int(2 * s)))
    canvas = Image.alpha_composite(canvas, glow.filter(ImageFilter.GaussianBlur(3 * s)))
    canvas.alpha_composite(load_portrait(portrait), (ax, ay))
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((ax, ay, ax + portrait, ay + portrait), outline=C_GOLD, width=ring + 1)

    name = "大肥鱼 · DeepSeek"
    nw = draw.textlength(name, font=L["f_name"])
    ny = ay + portrait + int(6 * s)
    draw.text((ax + portrait / 2 - nw / 2, ny), name, font=L["f_name"], fill=C_NAME)
    tag = "正在替你爬塔"
    tw = draw.textlength(tag, font=L["f_tag"])
    draw.text((ax + portrait / 2 - tw / 2, ny + int(17 * s)), tag, font=L["f_tag"], fill=C_TAGLINE)

    # ---- 右上状态
    segs = status_segments(status)
    total = sum(draw.textlength(t, font=(L["f_status_b"] if b else L["f_status"])) for t, _, b in segs)
    cx = px + L["panel_w"] - L["pad_r"] - total
    cy = py + pad_t
    for t, color, bold in segs:
        f = L["f_status_b"] if bold else L["f_status"]
        draw.text((cx, cy), t, font=f, fill=color)
        cx += draw.textlength(t, font=f)

    # ---- 正文：head 以下垂直居中（对应 CSS #feed{align-items:center}）
    if L["lines"]:
        feed_top = py + pad_t + L["head_h"] + L["feed_gap"]
        feed_bottom = py + L["panel_h"] - L["pad_b"]
        ty = feed_top + max(0, (feed_bottom - feed_top - L["text_h"]) // 2)
        for i, ln in enumerate(L["lines"]):
            draw.text((px + pad_l + L["avatar_col"] + L["gap"], ty + i * L["line_h"]),
                      ln, font=L["f_text"], fill=C_TEXT)

    if talk <= 0:
        if len(_static_cache) > 16:
            _static_cache.clear()
        _static_cache[(text, skey, round(scale, 3))] = canvas
    return canvas

# ---------------------------------------------------------------- Win32
ULW_ALPHA = 0x02
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WM_DESTROY = 0x0002
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_NCLBUTTONDOWN = 0x00A1
WM_HOTKEY = 0x0312
WM_EXITSIZEMOVE = 0x0232
WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011   # Win10 2004+：录屏/采集里看不到这个窗
WM_SETCURSOR = 0x0020
WM_MOUSEWHEEL = 0x020A
HTCAPTION = 2
HTBOTTOMRIGHT = 17
IDC_SIZEALL = 32646
IDC_SIZENWSE = 32642
MIN_SCALE = 0.50          # 再小字就看不清了
MAX_SCALE = 1.80
RESIZE_GRIP = 20          # 右下角多少像素算「缩放把手」
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MF_STRING = 0x0000
MF_CHECKED = 0x0008
TPM_RETURNCMD = 0x0100
TPM_LEFTALIGN = 0x0000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


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


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


def _init_winapi():
    """统一声明 argtypes：不声明的话，64 位下 HWND/WPARAM 会被截成 32 位，
    表现为「窗口位置乱飞、标题读成空、回调里 DefWindowProc 报 OverflowError」。"""
    u, g, k = user32, gdi32, kernel32
    u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    u.SetProcessDPIAware.argtypes = []
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
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetWindowLongW.restype = ctypes.c_long
    u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
    u.UpdateLayeredWindow.argtypes = [
        wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE),
        wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
        ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
    ]
    u.GetDC.argtypes = [wintypes.HWND]
    u.GetDC.restype = wintypes.HDC
    u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    u.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    u.LoadCursorW.restype = wintypes.HANDLE
    u.SetCursor.argtypes = [wintypes.HANDLE]
    u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    u.CreatePopupMenu.restype = wintypes.HMENU
    u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    u.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.TrackPopupMenu.restype = ctypes.c_int
    u.DestroyMenu.argtypes = [wintypes.HMENU]
    u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetSystemMetrics.argtypes = [ctypes.c_int]
    u.GetSystemMetrics.restype = ctypes.c_int
    u.ReleaseCapture.argtypes = []
    u.SetCapture.argtypes = [wintypes.HWND]
    u.SetCapture.restype = wintypes.HWND
    u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.SendMessageW.restype = ctypes.c_ssize_t
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u.DispatchMessageW.restype = ctypes.c_ssize_t
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    g.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
    g.CreateCompatibleDC.argtypes = [wintypes.HDC]
    g.CreateCompatibleDC.restype = wintypes.HDC
    g.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    g.CreateDIBSection.restype = wintypes.HBITMAP
    g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    g.SelectObject.restype = wintypes.HGDIOBJ
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteDC.argtypes = [wintypes.HDC]
    k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    k.GetModuleHandleW.restype = wintypes.HINSTANCE


def _enable_dpi_awareness():
    """必须在建窗前调用：PER_MONITOR_AWARE_V2（-4），失败退回系统级 DPI aware。"""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def _sizeall_cursor():
    return user32.LoadCursorW(None, ctypes.c_void_p(IDC_SIZEALL))


def _sizenwse_cursor():
    return user32.LoadCursorW(None, ctypes.c_void_p(IDC_SIZENWSE))


def premultiplied_bgra(img: Image.Image) -> bytes:
    """UpdateLayeredWindow 要预乘 alpha 的 BGRA。"""
    a = img.getchannel("A")
    r = ImageChops.multiply(img.getchannel("R"), a)
    g = ImageChops.multiply(img.getchannel("G"), a)
    b = ImageChops.multiply(img.getchannel("B"), a)
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


_init_winapi()
_enable_dpi_awareness()


class StreamerWindow:
    def __init__(self, scale: float = 0.0, pos: tuple[int, int] | None = None,
                 click_through: bool = False, topmost: bool = True, toolwindow: bool = True,
                 volume: float = 0.55, muted: bool = False, exclude_capture: bool = True):
        self.toolwindow = toolwindow
        self.topmost = topmost
        self.click_through = click_through
        self.exclude_capture = exclude_capture
        self.speaker = Speaker(volume, enabled=not muted)
        self.text = ""
        self.status = {}
        self.talk_until = 0.0
        self.enter_at = 0.0
        self.dirty = True
        self.running = True
        self._memdc = None
        self._hbmp = None
        self._dib = None
        self._dib_size = (0, 0)
        self._hwnd = None
        self._resizing = False
        self._resize_start = (0, 0, 0, 0)   # 按下时的 (鼠标x, 窗口left, 窗口top, 窗口宽)
        self._wndproc = WNDPROC(self._proc)  # 保引用，否则回调被回收会崩
        # 尺寸优先级：命令行 --scale > 上次拖出来的 > 系统 DPI
        saved = self._load_state()
        self.scale = self._clamp_scale(scale or float(saved.get("scale") or dpi_scale()))
        if pos:
            self.pos = self._clamp(*pos)
        elif saved.get("x") is not None:
            self.pos = self._clamp(int(saved["x"]), int(saved["y"]))
        else:
            sw = user32.GetSystemMetrics(0)
            self.pos = self._clamp((sw - int(BASE_W * self.scale)) // 2, int(60 * self.scale))
        self._cls = f"StS2Streamer{os.getpid()}"
        self._register()
        self._create()

    # ---- 窗口生命周期
    def _register(self):
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.hCursor = _sizeall_cursor()
        wc.lpszClassName = self._cls
        user32.RegisterClassW(ctypes.byref(wc))
        self._hinst = hinst

    def _create(self):
        ex = WS_EX_LAYERED | WS_EX_NOACTIVATE
        if self.toolwindow:
            ex |= WS_EX_TOOLWINDOW   # 不进任务栏/Alt-Tab；OBS 窗口采集找不到时用 --taskbar
        if self.topmost:
            ex |= WS_EX_TOPMOST
        if self.click_through:
            ex |= WS_EX_TRANSPARENT
        img = render(self.text or PLACEHOLDER, self.status, self.scale)
        x, y = self.pos
        self._hwnd = user32.CreateWindowExW(
            ex, self._cls, "大肥鱼 · 主播模式", WS_POPUP,
            x, y, img.width, img.height, None, None, self._hinst, None,
        )
        if not self._hwnd:
            raise OSError("CreateWindowExW failed")
        user32.ShowWindow(self._hwnd, 8)  # SW_SHOWNA
        if self.exclude_capture:
            self.apply_capture_exclusion(True)
        for hid, key in ((1, ord("D")), (2, ord("H")), (3, ord("Q")), (4, ord("S"))):
            user32.RegisterHotKey(self._hwnd, hid, MOD_CONTROL | MOD_ALT, key)
        self._paint(img)

    def apply_capture_exclusion(self, on: bool) -> bool:
        """WDA_EXCLUDEFROMCAPTURE：这个窗对录屏/采集 API 不可见（Win10 2004+）。

        OBS 现在的 `StS2 Game` 是 game_capture（只钩游戏进程的 swapchain），本来就不会
        录到这个窗；这句是给「以后切成显示器采集」留的保险——照样不会录进去。
        """
        ok = bool(user32.SetWindowDisplayAffinity(self._hwnd, WDA_EXCLUDEFROMCAPTURE if on else WDA_NONE))
        if on and not ok:
            print("WARN 这个系统不支持采集排除（需要 Win10 2004+）：录屏仍可能拍到这个窗")
        self.exclude_capture = on
        return ok

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_LBUTTONDOWN:
                if self.click_through:
                    return 0
                if self._hit_test() == HTBOTTOMRIGHT:
                    # 右下角：自己拉，别用系统的 sizing 循环（WS_POPUP 不带边框，SC_SIZE 不生效）
                    pt = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    self._resizing = True
                    self._resize_start = (pt.x, rect.left, rect.top, rect.right - rect.left)
                    user32.SetCapture(hwnd)
                    return 0
                user32.ReleaseCapture()
                user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)
                return 0
            if msg == WM_MOUSEMOVE:
                if self._resizing:
                    pt = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    self._resize_to_cursor(pt.x)
                    user32.SetCursor(_sizenwse_cursor())   # 拖出把手区也保持缩放光标
                return 0
            if msg == WM_LBUTTONUP:
                if self._resizing:
                    self._resizing = False
                    user32.ReleaseCapture()
                    self.apply_scale(self.scale)   # 吸附到这一档比例的精确尺寸
                    self._save_pos()
                    print(f"尺寸：{self.scale:.2f}×")
                return 0
            if msg == WM_MOUSEWHEEL:
                if not self.click_through:
                    delta = ctypes.c_short((wparam >> 16) & 0xFFFF).value
                    self.zoom(1.08 if delta > 0 else 1 / 1.08)
                    self._save_pos()
                return 0
            if msg == WM_EXITSIZEMOVE:
                self._save_pos()   # 拖完就记位置，别等退出
                return 0
            if msg == WM_RBUTTONUP:
                self._menu()
                return 0
            if msg == WM_HOTKEY:
                if wparam == 1:
                    self.toggle_click_through()
                elif wparam == 2:
                    self.toggle_visible()
                elif wparam == 3:
                    self.close()
                elif wparam == 4:
                    self.toggle_sound()
                return 0
            if msg == WM_SETCURSOR:
                if self.click_through:
                    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
                if self._hit_test() == HTBOTTOMRIGHT:
                    user32.SetCursor(_sizenwse_cursor())
                else:
                    user32.SetCursor(_sizeall_cursor())
                return 1
            if msg == WM_DESTROY:
                self.running = False
                return 0
        except Exception as e:  # 回调里异常不能外抛，否则进程直接死
            print("wndproc error:", e)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self):
        hmenu = user32.CreatePopupMenu()
        user32.AppendMenuW(hmenu, MF_STRING | (MF_CHECKED if self.topmost else 0), 1, "置顶")
        user32.AppendMenuW(hmenu, MF_STRING | (MF_CHECKED if self.click_through else 0), 2, "点击穿透")
        user32.AppendMenuW(hmenu, MF_STRING | (MF_CHECKED if self.speaker.enabled else 0), 3, "声音")
        user32.AppendMenuW(hmenu, MF_STRING | (MF_CHECKED if self.exclude_capture else 0), 4, "录屏里隐藏")
        user32.AppendMenuW(hmenu, MF_STRING, 5, "重新显示示例")
        user32.AppendMenuW(hmenu, MF_STRING, 6, "关闭")
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # 不加这两句，菜单点到别处不会收起（Win32 的老毛病）
        user32.SetForegroundWindow(self._hwnd)
        user32.PostMessageW(self._hwnd, 0x0000, 0, 0)  # WM_NULL
        cmd = user32.TrackPopupMenu(hmenu, TPM_RETURNCMD | TPM_LEFTALIGN, pt.x, pt.y, 0, self._hwnd, None)
        user32.DestroyMenu(hmenu)
        if cmd == 1:
            self.topmost = not self.topmost
            user32.SetWindowPos(self._hwnd, -1 if self.topmost else -2, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE)
        elif cmd == 2:
            self.toggle_click_through()
        elif cmd == 3:
            self.toggle_sound()
        elif cmd == 4:
            self.apply_capture_exclusion(not self.exclude_capture)
        elif cmd == 5:
            self.push("这一手先算账：它 21，我最多挡 15。", "差 6，嗨呀。",
                      {"character": "铁甲战士", "ascension": 10, "floor": 8, "hp": 23, "max_hp": 80, "gold": 20})
        elif cmd == 6:
            self.close()

    def toggle_sound(self):
        on = self.speaker.toggle()
        print("声音：" + ("开" if on else "关（Ctrl+Alt+S 切回）"))
        if on:
            self.speaker.speak("声音开了。")

    def toggle_click_through(self):
        self.click_through = not self.click_through
        ex = user32.GetWindowLongW(self._hwnd, -20)
        ex = (ex | WS_EX_TRANSPARENT) if self.click_through else (ex & ~WS_EX_TRANSPARENT)
        user32.SetWindowLongW(self._hwnd, -20, ex)
        print("点击穿透：" + ("开（Ctrl+Alt+D 关）" if self.click_through else "关"))

    def toggle_visible(self):
        if user32.IsWindowVisible(self._hwnd):
            user32.ShowWindow(self._hwnd, 0)
        else:
            user32.ShowWindow(self._hwnd, 8)

    def close(self):
        self._save_pos()
        self.running = False
        user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)

    # ---- 画面
    def _paint(self, img: Image.Image, at: tuple[int, int] | None = None, resize: bool = True):
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
            bmi.bmiHeader.biHeight = -h  # top-down
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0
            self._dib = ctypes.c_void_p()
            self._hbmp = gdi32.CreateDIBSection(
                self._memdc, ctypes.byref(bmi), 0, ctypes.byref(self._dib), None, 0
            )
            gdi32.SelectObject(self._memdc, self._hbmp)
            self._dib_size = (w, h)
        buf = premultiplied_bgra(img)
        ctypes.memmove(self._dib, buf, len(buf))
        rect = wintypes.RECT()
        user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        if resize and (rect.right - rect.left, rect.bottom - rect.top) != (w, h):
            # 文案变长 / 缩放过：窗口跟着改尺寸，别把字裁掉
            user32.SetWindowPos(self._hwnd, None, rect.left, rect.top, w, h,
                                SWP_NOZORDER | SWP_SHOWWINDOW)
            user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        pos = at or (rect.left, rect.top)
        pt_dst = wintypes.POINT(pos[0], pos[1])
        size = wintypes.SIZE(w, h)
        pt_src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(self._hwnd, hdc_screen, ctypes.byref(pt_dst), ctypes.byref(size),
                                   self._memdc, ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA)
        user32.ReleaseDC(None, hdc_screen)

    def push(self, thought: str, meme: str, status: dict | None = None):
        text = compose(clip(thought, MAX_THOUGHT), clip(meme, MAX_MEME))
        if not text:
            return
        self.text = text
        if status:
            self.status = dict(status)
        elif self.status:
            self.status = dict(self.status)
        self.enter_at = time.perf_counter()
        self.talk_until = self.enter_at + min(8.5, max(2.4, 1.5 + len(text) * 0.07))
        self.dirty = True
        self.speaker.speak(text)   # 和页面一样：说的就是气泡里这一句

    def update_status(self, status: dict):
        if status and status != self.status:
            self.status = dict(status)
            self.dirty = True

    def frame(self) -> Image.Image:
        now = time.perf_counter()
        talk = 0.0
        if now < self.talk_until:
            talk = 1.0 - (self.talk_until - now) / max(0.001, self.talk_until - self.enter_at)
        img = render(self.text or PLACEHOLDER, self.status, self.scale, talk)
        # 入场：滑 6px + 渐显（对应 CSS slidein .32s）
        dt = now - self.enter_at
        if 0 <= dt < 0.32 and self.text:
            k = dt / 0.32
            k = 1 - (1 - k) ** 3
            off = int((1 - k) * 6 * self.scale)
            moved = Image.new("RGBA", img.size, (0, 0, 0, 0))
            moved.alpha_composite(img, (0, -off) if off else (0, 0))
            if k < 1:
                moved.putalpha(moved.getchannel("A").point(lambda v: int(v * k)))
            img = moved
        return img

    # ---- 尺寸（右下角拖动 / 滚轮）
    def _render_at(self, scale: float) -> Image.Image:
        return render(self.text or PLACEHOLDER, self.status, scale)

    @staticmethod
    def _clamp_scale(scale: float) -> float:
        screen_w = user32.GetSystemMetrics(0)
        hi = min(MAX_SCALE, screen_w * 0.95 / BASE_W)
        return max(MIN_SCALE, min(hi, float(scale)))

    def zoom(self, factor: float):
        self.apply_scale(self.scale * factor)

    def _resize_to_cursor(self, cursor_x: int):
        """拖动把手时的换算：宽度跟手走，高度跟宽度走（等比）。"""
        x0, left, top, w0 = self._resize_start
        self.apply_scale((w0 + (cursor_x - x0)) / BASE_W, anchor=(left, top))

    def apply_scale(self, scale: float, anchor: tuple[int, int] | None = None):
        """改整体大小：等比缩，宽度决定，高度按新字号重算。"""
        self.scale = self._clamp_scale(scale)
        img = self._render_at(self.scale)
        if anchor is None:
            rect = wintypes.RECT()
            user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
            anchor = (rect.left, rect.top)
        self._paint(img, at=anchor)

    @staticmethod
    def _hit_test_point(px: int, py: int, rect, scale: float) -> int:
        """右下角一圈算缩放把手，其余地方整块可拖。"""
        grip = max(10, int(RESIZE_GRIP * scale))
        if px >= rect.right - grip and py >= rect.bottom - grip:
            return HTBOTTOMRIGHT
        return HTCAPTION

    def _hit_test(self) -> int:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        rect = wintypes.RECT()
        user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        return self._hit_test_point(pt.x, pt.y, rect, self.scale)

    # ---- 位置/尺寸记忆
    @staticmethod
    def _load_state() -> dict:
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _clamp(x: int, y: int) -> tuple[int, int]:
        """上次记住的位置可能在已拔掉的副屏上：夹回可见区域，别让窗口消失。"""
        vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        x = max(vx, min(x, vx + vw - 200))
        y = max(vy, min(y, vy + vh - 80))
        return x, y

    def _save_pos(self):
        try:
            rect = wintypes.RECT()
            user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"x": rect.left, "y": rect.top, "scale": round(self.scale, 3)}, f)
        except Exception:
            pass

    def run(self):
        print("主播模式已启动：左键拖 · 右下角拖=缩放 · 滚轮=缩放 · 右键菜单 · "
              "Ctrl+Alt+D 穿透 · Ctrl+Alt+S 声音 · Ctrl+Alt+H 隐藏 · Ctrl+Alt+Q 退出")
        print("（默认不出现在录屏里：WDA_EXCLUDEFROMCAPTURE，要拍就加 --capture）")
        last = 0.0
        while self.running:
            msg = wintypes.MSG()
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            now = time.perf_counter()
            animating = now < max(self.talk_until, self.enter_at + 0.32)
            if self.dirty or animating:
                if animating or now - last > 0.05:
                    self._paint(self.frame())
                    self.dirty = False
                    last = now
                time.sleep(0.033)   # 30fps 够用：Pillow 每帧 ~10ms，别再翻倍烧 CPU
            else:
                time.sleep(0.05)
        self._save_pos()


# ---------------------------------------------------------------- 数据
def fetch_state(port: int, after: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/state?after={after}", timeout=1.0) as r:
        return json.load(r)


def poll_loop(win: StreamerWindow, port: int, stop: threading.Event, demo: bool):
    last_id = 0
    if demo:
        win.push("这一手先算账：它 21，我最多挡 15。", "差 6，嗨呀。",
                 {"character": "铁甲战士", "ascension": 10, "floor": 8, "hp": 23, "max_hp": 80, "gold": 20})
    while not stop.is_set():
        try:
            d = fetch_state(port, last_id)
        except Exception:
            stop.wait(1.0)
            continue
        if isinstance(d.get("seq"), int) and d["seq"] < last_id:
            last_id = 0
        win.update_status(d.get("status") or {})
        for c in d.get("comments") or []:
            last_id = max(last_id, int(c.get("id", 0)))
        if d.get("comments"):
            latest = d["comments"][-1]
            win.push(latest.get("thought", ""), latest.get("meme", ""), d.get("status") or {})
        stop.wait(0.35)


def ensure_server(port: int) -> bool:
    """overlay server 没起就拉起来（和 OBS 用的是同一个服务）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as r:
            if r.status == 200:
                return True
    except Exception:
        pass
    import subprocess

    print(f"overlay server 没在跑，正在启动（端口 {port}）…")
    env = dict(os.environ, STS2_OVERLAY_PORT=str(port))
    subprocess.Popen([sys.executable, os.path.join(ROOT, "overlay_server.py")],
                     cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(0.2)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as r:
                if r.status == 200:
                    print("overlay server 就绪")
                    return True
        except Exception:
            continue
    print("WARN overlay server 没起来：窗口还能显示，但收不到新弹幕")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="主播模式：可拖动的透明弹幕窗")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--scale", type=float, default=0.0,
                    help="缩放 0.42~1.8（默认用上次拖出来的大小，没有就跟系统 DPI；1.0=1180px 宽）")
    ap.add_argument("--pos", help="初始位置 x,y")
    ap.add_argument("--click-through", action="store_true", help="启动即点击穿透")
    ap.add_argument("--demo", action="store_true", help="先放一条示例弹幕（不接游戏也能试位置）")
    ap.add_argument("--taskbar", action="store_true", help="在任务栏/Alt-Tab 里显示（OBS 窗口采集找不到时用）")
    ap.add_argument("--mute", action="store_true", help="不出声（默认和页面同款动物语）")
    ap.add_argument("--vol", type=float, default=0.55, help="音量 0~1.5（默认 0.55，和页面一致）")
    ap.add_argument("--capture", action="store_true", help="允许录屏拍到它（默认对采集隐藏）")
    ap.add_argument("--preview", help="只渲染一张 PNG 就退出（验收/截图用）")
    a = ap.parse_args()

    # 预览图的缩放：命令行优先，否则跟 DPI，屏宽装不下就自动缩
    scale = a.scale or dpi_scale()
    screen_w = user32.GetSystemMetrics(0)
    if PANEL_W * scale > screen_w * 0.78:
        scale = screen_w * 0.78 / PANEL_W
        print(f"窗口比屏幕宽，缩到 {scale:.2f}×")

    if a.preview:
        img = render("它 21，我最多挡 15。差 6，嗨呀。",
                     {"character": "铁甲战士", "ascension": 10, "floor": 8,
                      "hp": 23, "max_hp": 80, "gold": 20}, scale)
        img.save(a.preview)
        print(f"PREVIEW {a.preview} {img.width}x{img.height}")
        return 0

    ensure_server(a.port)
    pos = None
    if a.pos:
        try:
            x, y = (int(v) for v in a.pos.split(","))
            pos = (x, y)
        except ValueError:
            print("--pos 需要 x,y")
            return 2

    win = StreamerWindow(scale=a.scale, pos=pos, click_through=a.click_through,
                         toolwindow=not a.taskbar, volume=a.vol, muted=a.mute,
                         exclude_capture=not a.capture)
    stop = threading.Event()
    th = threading.Thread(target=poll_loop, args=(win, a.port, stop, a.demo), daemon=True)
    th.start()
    try:
        win.run()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        win._save_pos()
    return 0


if __name__ == "__main__":
    sys.exit(main())
