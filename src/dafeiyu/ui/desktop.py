#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主播模式：一个可拖动的透明弹幕窗，和 OBS 里那块「同款」。

为什么要单独写一个窗：OBS 浏览器源能吃到透明通道，但普通浏览器窗口不行
（Chromium 的窗口不支持逐像素 alpha，色键在 WebView2/Edge 上也不稳）。
所以这里用 Win32 分层窗口 + Pillow 现画：真透明、无边框、置顶、可拖，
数据还是从 `overlay_server.py` 的 `/state` 拿，和 OBS 里那条气泡同一份。

用法：
    python main.py                 # 起弹幕窗（自动拉起 overlay server）
    python main.py --demo          # 不接游戏，先放一条示例弹幕试位置
    python main.py --click-through # 开局就点击穿透（打游戏时鼠标直接穿过去）
    python main.py --preview out.png   # 只渲染一张图（不看窗口，用于验收/截图）

窗口里能做的事：
    左键拖动  → 挪位置（松手自动记住，下次原地开）
    右下角拖动 → 缩放（等比，0.5×–1.8×；缩放后大小也记住）
    滚轮      → 缩放（同一个比例，懒得找角就用它）
    右键      → 菜单：置顶 / 点击穿透 / 声音 / 录屏里隐藏 / 语音 / 模型 / 配置 API / 管理端 / 示例 / 关闭
    ☰（右下角金钮）→ 穿透模式下也能点：左键拖、右键菜单
    Ctrl+Alt+D 点击穿透开关（穿透后只有 ☰ 和右下缩放把手可点，其余照常穿过去）
    Ctrl+Alt+H 隐藏/显示
    Ctrl+Alt+S 声音开关
    Ctrl+Alt+Q 退出

主播状态（气泡里显示什么，状态机在 dafeiyu/ui/caster_state.py）：
    说话中  → 台词（喇叭真的开始播才抖，时长用 WAV 实测）
    思考中  → 「思考ing」（overlay 的 /thinking 由 tools/ctx.py、tools/act.py 上报）
    空闲    → 「吃饭ing」
    注意：思考态没有「N 秒后自动消失」这种假逻辑；只有 240s 存活超时（Agent 崩了才回空闲）。

直播时怎么用：OBS 的 `StS2 Game` 是 game_capture（只钩游戏进程），本来就不会录到这个窗；
另外窗口默认能被录屏拍到（要隐藏可勾右键「录屏里隐藏」或加 --exclude-capture）。
声音是页面同款动物语（同一个五声音阶合成），本地播放，不进录像。
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
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
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps
except ImportError:  # pragma: no cover
    print("需要 Pillow：pip install -r requirements.txt")
    raise SystemExit(2)

# 布局：src/dafeiyu/ui/desktop.py（仓库根 = 上三级）
from .. import paths
from . import instance_lock              # 单实例锁：屏幕上不许出现两个弹窗
from .caster_state import CasterState, IDLE_TEXT, THINKING_TEXT  # 主播三态状态机

ROOT = os.fspath(paths.ROOT)
ASSETS = os.path.join(ROOT, "ui", "overlay", "assets")
STATE_FILE = os.path.join(ROOT, "logs", "streamer_window.json")
TTS_CACHE = os.path.join(ROOT, "logs", "tts_cache")
PORT = int(os.environ.get("STS2_OVERLAY_PORT", "8730"))
# 可选：游戏窗口标题（只用于「把桌宠放到游戏旁边」；不填就按主屏居中）
GAME_WINDOW_TITLE = os.environ.get("DAFEIYU_GAME_WINDOW_TITLE", "")
PLACEHOLDER = "（还没收到弹幕 —— agent 一开口，这里就换成它说的话）"
_VOICE_CHOICES = (("mambo", "曼波语"), ("animalese", "动物语"), ("off", "静音"))

# ---------------------------------------------------------------- 桌宠形象
# 精灵来自 https://github.com/1190fasheqi/dafeiyu-pet（MIT，见 assets/pet/SOURCE.md）。
# 三视图没有逐帧动画：呼吸/摇摆/拖拽倾斜/拉伸/速度线/回弹都在这里实时画。
PET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "pet")
PET_STATE_FILE = os.path.join(ROOT, "logs", "streamer_pet.json")
PET_BASE_W, PET_BASE_H = 560, 500      # 1.0× 时的画布（气泡区 + 精灵区）
PET_FISH_H = 300                       # 1.0× 时的精灵高度
PET_FACING = {"front": "正面", "side": "侧面", "back": "背面"}

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
TEXT_ZOOM = 1.12                # 弹幕正文字号倍率（面板宽度不变，字大一点更好读）

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


# ---------------------------------------------------------------- 曼波配音（MamboTTS / GPT-SoVITS）
# 引擎默认地址 127.0.0.1:9880（见 MamboTTS engine_contract.py），POST / + JSON。
# 环境变量：STS2_TTS_URL 覆盖地址；STS2_TTS_REF 覆盖参考音频路径；STS2_TTS_PROMPT 覆盖参考文本。
MAMBO_URL = os.environ.get("STS2_TTS_URL", "http://127.0.0.1:9880")
MAMBO_REF = os.environ.get("STS2_TTS_REF", "")
MAMBO_PROMPT = os.environ.get(
    "STS2_TTS_PROMPT", "大家好，欢迎来到我的频道，今天给大家分享一个有趣的内容"
)
MAMBO_CUT = "，。？！；：、…,.;?!"


def _wav_gain(data: bytes, volume: float) -> bytes:
    """把 WAV 的 16bit 样本按 volume 缩放；只处理标准 PCM，失败就原样返回。"""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getsampwidth() != 2:
                return data
            params = w.getparams()
            frames = w.readframes(w.getnframes())
    except Exception:
        return data
    k = min(1.5, max(0.0, volume))
    if abs(k - 1.0) < 0.01:
        return data
    out = bytearray(len(frames))
    for i in range(0, len(frames) - 1, 2):
        s = int.from_bytes(frames[i:i + 2], "little", signed=True)
        s = max(-32768, min(32767, int(s * k)))
        out[i:i + 2] = s.to_bytes(2, "little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams(params)
        w.writeframes(bytes(out))
    return buf.getvalue()


def mambo_wav(text: str, volume: float = 0.55, timeout: float = 30.0) -> bytes | None:
    """调用本地 MamboTTS（GPT-SoVITS）合成一句配音，返回 WAV；引擎不在线返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    payload = {
        "text": text,
        "text_language": "zh",
        "speed": float(os.environ.get("STS2_TTS_SPEED", "1.0")),
        "cut_punc": MAMBO_CUT,
    }
    if MAMBO_REF:
        payload["refer_wav_path"] = MAMBO_REF
        payload["prompt_text"] = MAMBO_PROMPT
        payload["prompt_language"] = "zh"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        MAMBO_URL.rstrip("/") + "/",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(req, timeout=timeout) as r:
            data = r.read()
    except Exception:
        return None
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    return _wav_gain(data, volume)


def mambo_cached_wav(text: str, volume: float = 0.55, timeout: float = 30.0) -> bytes | None:
    """先读 overlay 预热好的磁盘缓存，窗口不再为同一句重新合成。

    `sts2.overlay_say()` 在 POST comment 之前已经把 WAV 写进
    `logs/tts_cache/<sha1(text)[:20]>.wav`；窗口和页面共用这份缓存。
    `--demo` / 直接调试没有预热时，才退回实时调引擎。
    """
    text = (text or "").strip()
    if not text:
        return None
    key = hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]
    path = os.path.join(TTS_CACHE, key + ".wav")
    raw = None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        pass
    if raw and (len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE"):
        raw = None
    if not raw:
        raw = mambo_wav(text, 1.0, timeout)
        if not raw:
            return None
        try:
            os.makedirs(TTS_CACHE, exist_ok=True)
            with open(path, "wb") as f:
                f.write(raw)
        except OSError:
            pass
    if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None
    return _wav_gain(raw, volume)


def _wav_seconds(data: bytes) -> float:
    """WAV 的真实时长；窗口动画按它抖，不再按字数猜。"""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / float(rate) if rate > 0 else 0.0
    except Exception:
        return 0.0


def mambo_online(timeout: float = 1.5) -> bool:
    """引擎是否在线（GET /control 返回 JSON 即视为在线）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(MAMBO_URL.rstrip("/") + "/control", timeout=timeout) as r:
            r.read(64)
        return True
    except Exception:
        return False


class Speaker:
    """异步播放：合成放后台线程，别卡住 30fps 的重绘。

    注意：Win32 的 PlaySound 不支持「内存 + 异步」这个组合（会报
    Cannot play asynchronously from memory），所以先落一个临时 WAV 再
    用 SND_FILENAME|SND_ASYNC 播；两个文件轮着用，避免覆盖正在播的那个。
    """

    def __init__(self, volume: float = 0.55, enabled: bool = True, voice: str = "mambo",
                 on_start=None):
        self.volume = volume
        self.enabled = enabled
        self.voice = (voice or "mambo").lower()
        # on_start(duration, text)：真正调用 PlaySound 之后才回调，窗口拿它开始抖。
        # 合成/预热期间绝不提前动画。
        self.on_start = on_start
        self._offline_warned = False
        self._lock = threading.Lock()
        self._slot = 0
        self._paths = [os.path.join(tempfile.gettempdir(), f"sts2_voice_{i}.wav") for i in (0, 1)]
        self._ok = True
        # 直播间页面（直播姬里那个 browser_source）本来就在本地出声；
        # 窗口再播一遍就是同一句说两次。默认让页面当唯一喇叭，窗口只在没页面时补位。
        self.delegate = os.environ.get("STS2_VOICE_DELEGATE", "1").lower() not in ("0", "false", "no")
        self._live_ts = 0.0
        self._live_val = False
        try:
            import winsound  # noqa: F401
            self._winsound = winsound
        except Exception:
            self._ok = False

    def _live_page_present(self) -> bool:
        """直播姬里那个页面最近真的在出声吗？

        只在「有 live 页面 且 90 秒内它真的播过音」时才让位——页面连上了却没在渲染
        （被直播姬暂停、或服务端重启后游标卡住）时，窗口照常自己播，不能一起哑掉。
        """
        now = time.time()
        if now - self._live_ts < 5.0:
            return self._live_val
        self._live_ts = now
        self._live_val = False
        try:
            url = os.environ.get("STS2_OVERLAY_URL", "http://127.0.0.1:8730") + "/health"
            with urllib.request.urlopen(url, timeout=0.35) as r:
                d = json.load(r)
            live = int((d.get("roles") or {}).get("live", 0))
            ts = float((d.get("audio_ts") or {}).get("live", 0) or 0)
            self._live_val = live > 0 and (now - ts) < 90.0
        except Exception:
            self._live_val = False
        return self._live_val

    def speak(self, text: str):
        if not (self.enabled and self._ok and text):
            return
        if self.delegate and self._live_page_present():
            return
        threading.Thread(target=self._play_delegated, args=(text,), daemon=True).start()

    def _play_delegated(self, text: str):
        # 只在「live 页面真的在出声」时让位。以前这里先睡 0.9s，窗口没页面时
        # 白等 0.9s，再叠加合成，观感就是头像先抖、声音晚两秒。
        if self.delegate:
            # speak() 刚查过；这里不再睡 0.9s 重查，避免白等。
            if self._live_page_present():
                return
        self._play(text)

    def _play(self, text: str):
        try:
            wav = None
            if self.voice == "mambo":
                wav = mambo_cached_wav(text, self.volume)
                if wav is None and not self._offline_warned:
                    self._offline_warned = True
                    print("曼波配音：本地引擎没响应（127.0.0.1:9880），先静音；启动 MamboTTS 后自动恢复")
            elif self.voice == "animalese":
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
            # PlaySound 返回时异步播放已经开始；这一刻才让头像抖，和声音同一时钟。
            if self.on_start:
                try:
                    self.on_start(_wav_seconds(wav), text)
                except Exception as e:
                    print("窗口动画回调失败：", e)
            try:  # 自报一声，日志里能看清「这一句到底谁在响」
                url = os.environ.get("STS2_OVERLAY_URL", "http://127.0.0.1:8730") + "/mark?ev=audio&role=window"
                urllib.request.urlopen(url, timeout=0.5).read()
            except Exception:
                pass
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


def chip_rect(W: int, H: int, scale: float) -> tuple[int, int, int, int]:
    """「菜单钮」矩形，窗口坐标 = 图像坐标（穿透模式下唯一能点的东西）。

    画在面板右下角的内衬里（缩放把手在窗口最角落的透明区，看不见）。
    尺寸 20*s，两边各留 8*s，正好落在 pad_b/pad_r 的空白上，不压正文。
    """
    s = scale
    m = int(26 * s)
    g = max(12, int(24 * s))
    x2 = W - m - int(8 * s)
    y2 = H - m - int(16 * s) - int(8 * s)
    return (x2 - g, y2 - g, x2, y2)


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
        f_text = get_font(int(16.5 * s * TEXT_ZOOM))
    elif len(text) > 110:
        f_text = get_font(int(18.5 * s * TEXT_ZOOM))
    else:
        f_text = get_font(int(20 * s * TEXT_ZOOM))

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


def render(text: str, status: dict, scale: float = 1.0, talk: float = 0.0,
           chip: bool = False, layout_text: str | None = None) -> Image.Image:
    """画一帧：返回带阴影的 RGBA 图（尺寸=窗口尺寸）。

    talk: 0~1，说话中头像轻微上下跳（对应页面里的 bounce 动画）。
    talk=0 的帧整张缓存，静止时不重复渲染。
    chip: 点击穿透开着时画出右下角「菜单钮」，告诉用户这里可以点。
    layout_text: 用另一段文字的版式（尺寸）来排版，只换显示的内容。
                 思考中用它保持窗口大小不变——否则每说一句话窗口就缩一次。
    """
    L = _layout(layout_text or text, scale)
    skey = tuple(sorted((k, str(v)) for k, v in (status or {}).items()))
    if talk <= 0:
        cached = _static_cache.get((text, skey, round(scale, 3), chip, layout_text))
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
    lines = L["lines"]
    text_h = L["text_h"]
    if layout_text and text != layout_text:
        # 只换内容不换尺寸：按这段文字的字体/宽度重新折行，但面板高度沿用 layout_text
        probe2 = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        inner_w = L["panel_w"] - L["pad_l"] - L["avatar_col"] - L["gap"] - L["pad_r"]
        lines = wrap_cjk(text, L["f_text"], inner_w, probe2) if text.strip() else []
        text_h = L["line_h"] * len(lines)
    if lines:
        feed_top = py + pad_t + L["head_h"] + L["feed_gap"]
        feed_bottom = py + L["panel_h"] - L["pad_b"]
        ty = feed_top + max(0, (feed_bottom - feed_top - text_h) // 2)
        for i, ln in enumerate(lines):
            draw.text((px + pad_l + L["avatar_col"] + L["gap"], ty + i * L["line_h"]),
                      ln, font=L["f_text"], fill=C_TEXT)

    if chip:
        # 穿透模式下的唯一可点区：☰ 菜单钮（左键=拖动，右键=菜单；这块区域会自动摘掉穿透）
        cl, ct, cr, cb = chip_rect(L["W"], L["H"], s)
        if os.environ.get("STS2_STREAMER_DEBUG"):
            print(f"chip draw=({cl},{ct},{cr},{cb}) canvas={L['W']}x{L['H']} s={s:.3f}",
                  flush=True)
        draw.rounded_rectangle((cl, ct, cr, cb), radius=max(3, int(5 * s)),
                               fill=(216, 168, 74, 52), outline=(216, 168, 74, 170),
                               width=max(1, int(1.2 * s)))
        mid = (ct + cb) // 2
        bar = max(1, int(1.8 * s))
        step = max(3, int(5 * s))
        for i in (-1, 0, 1):
            yv = mid + i * step
            draw.line([(cl + int(5 * s), yv), (cr - int(5 * s), yv)],
                      fill=(244, 201, 104, 240), width=bar)

    if talk <= 0:
        if len(_static_cache) > 16:
            _static_cache.clear()
        _static_cache[(text, skey, round(scale, 3), chip, layout_text)] = canvas
    return canvas

_PET_CACHE: dict[tuple[str, int], Image.Image] = {}


def load_pet_sprite(facing: str, height: int) -> Image.Image:
    """按朝向/高度取精灵（缓存），侧面默认朝左，向右拖动时镜像。"""
    key = (facing, int(height))
    hit = _PET_CACHE.get(key)
    if hit is not None:
        return hit
    name = PET_FACING.get(facing, "正面")
    path = os.path.join(PET_DIR, f"{name}_306.png")
    im = Image.open(path).convert("RGBA")
    if int(height) != im.height:
        im = im.resize((max(1, round(im.width * int(height) / im.height)), int(height)),
                       Image.LANCZOS)
    if len(_PET_CACHE) > 24:
        _PET_CACHE.clear()
    _PET_CACHE[key] = im
    return im


def render_pet(text: str, mode: str, scale: float, now: float, pet) -> Image.Image:
    """桌宠一帧：精灵 + 气泡。

    - 朝向：拖动方向决定正面 / 侧面 / 背面（侧面自动镜像）
    - 动态：呼吸、说话弹跳、拖拽倾斜 / 压扁 / 拉长、速度线、松手回弹
    - 气泡：漫画式圆角气泡 + 尾巴，出现时淡入上浮，说完 1.6s 后淡出
    """
    scale = max(MIN_SCALE, float(scale))
    W, H = int(PET_BASE_W * scale), int(PET_BASE_H * scale)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    facing = getattr(pet, "pet_facing", "front") or "front"
    face_right = bool(getattr(pet, "pet_face_right", False))
    energy = float(getattr(pet, "pet_energy", 0.0) or 0.0)
    lean = float(getattr(pet, "pet_lean", 0.0) or 0.0)
    fish_h = int(PET_FISH_H * scale)
    sprite = load_pet_sprite(facing, fish_h)
    if facing == "side" and face_right:
        sprite = ImageOps.mirror(sprite)

    # 呼吸 + 说话弹跳（脚底固定，向上长）
    breathe = 1.0 + 0.012 * math.sin(now * 2.2)
    talk = 0.0
    if getattr(pet, "talk_start_at", 0.0) and now < getattr(pet, "talk_until", 0.0):
        span = max(0.001, pet.talk_until - pet.talk_start_at)
        talk = min(1.0, max(0.0, (now - pet.talk_start_at) / span))
    bounce = abs(math.sin((now - pet.talk_start_at) * 9.0)) * 4.0 * scale if talk else 0.0

    sx = 1.0 - 0.035 * energy
    sy = breathe + 0.06 * energy
    sprite = sprite.resize((max(1, int(sprite.width * sx)), max(1, int(sprite.height * sy))),
                           Image.LANCZOS)
    if abs(lean) > 0.2:
        sprite = sprite.rotate(lean, resample=Image.BICUBIC, expand=True)

    sway = 2.0 * scale * math.sin(now * 1.3)
    x = (W - sprite.width) // 2 + int(sway)
    y = H - sprite.height - int(10 * scale) + int(bounce)

    # 拖动速度线（只在侧面、真有速度时出现，很淡）
    if energy > 0.25 and facing == "side":
        d = ImageDraw.Draw(canvas)
        dirn = 1 if face_right else -1
        body = sprite.getbbox() or (0, 0, sprite.width, sprite.height)
        for k, frac in enumerate((0.34, 0.52, 0.70)):
            ln = int((12 + 30 * energy) * (1.0 - 0.18 * abs(k - 1)) * scale)
            y0 = y + body[1] + int((body[3] - body[1]) * frac)
            x0 = (x + body[0] - int(4 * scale)) if dirn > 0 else (x + body[2] + int(4 * scale))
            d.line([(x0, y0), (x0 - dirn * ln, y0)],
                   fill=(255, 255, 255, int(80 * energy)), width=max(1, int(2 * scale)))
    canvas.alpha_composite(sprite, (x, y))

    # ---- 气泡
    if mode == "thinking":
        text = THINKING_TEXT
        alpha = 1.0
    elif mode == "idle":
        text = IDLE_TEXT
        alpha = 1.0
    elif not text or text == PLACEHOLDER:
        return canvas
    else:
        age = now - float(getattr(pet, "enter_at", 0.0) or 0.0)
        alpha = min(1.0, max(0.0, age / 0.14))
        until = float(getattr(pet, "bubble_until", 0.0) or 0.0)
        if until and now > until:
            alpha *= max(0.0, 1.0 - (now - until) / 0.35)
    if alpha <= 0.02:
        return canvas

    font = get_font(max(11, int(round(16 * scale))))
    pad_x, pad_y = int(16 * scale), int(12 * scale)
    max_tw = max(120, min(int(420 * scale), W - int(28 * scale) - 2 * pad_x))
    probe = ImageDraw.Draw(canvas)
    lines = wrap_cjk(text, font, max_tw, probe)
    line_h = int(font.size * 1.42) if hasattr(font, "size") else int(23 * scale)
    tw = max(int(probe.textlength(line, font=font)) for line in lines)
    bw = min(W - int(16 * scale), max(int(120 * scale), tw + 2 * pad_x))
    bh = len(lines) * line_h + 2 * pad_y

    fish_cx = x + sprite.width // 2
    bbox = sprite.getbbox() or (0, 0, sprite.width, sprite.height)
    head_y = y + bbox[1]                      # 精灵透明边不算，气泡贴着头顶
    fish_cx = x + (bbox[0] + bbox[2]) // 2
    by = max(int(8 * scale), head_y + int(2 * scale) - bh)
    bx = max(int(8 * scale), min(fish_cx - bw // 2, W - bw - int(8 * scale)))
    tail_h = int(16 * scale)
    ty = by + bh
    rise = int((1.0 - alpha) * 6 * scale)

    fill = (253, 249, 240, int(246 * alpha))
    edge = (216, 168, 74, int(230 * alpha))
    d = ImageDraw.Draw(canvas)
    # 尾巴（先画，气泡矩形盖住上沿）；尖端追着鱼头中心走
    tip_x = min(max(fish_cx, bx + int(bw * 0.24)), bx + int(bw * 0.76))
    tw = max(6, int(10 * scale))
    tail = [(tip_x - tw, ty - 2), (tip_x + tw, ty - 2),
            (tip_x, min(ty + tail_h, head_y + int(20 * scale)) + rise)]
    d.polygon(tail, fill=fill)
    d.line([tail[0], tail[2]], fill=edge, width=max(1, int(1.4 * scale)))
    d.line([tail[1], tail[2]], fill=edge, width=max(1, int(1.4 * scale)))
    # 阴影 + 气泡
    d.rounded_rectangle([bx + 2, by + rise + 4, bx + bw + 2, by + rise + bh + 4],
                        radius=int(16 * scale), fill=(0, 0, 0, int(60 * alpha)))
    d.rounded_rectangle([bx, by + rise, bx + bw, by + rise + bh],
                        radius=int(16 * scale), fill=fill, outline=edge,
                        width=max(1, int(1.5 * scale)))
    color = (110, 116, 135, int(255 * alpha)) if mode == "thinking" else (32, 34, 44, int(255 * alpha))
    text_y = by + rise + pad_y
    for line in lines:
        d.text((bx + pad_x, text_y), line, font=font, fill=color)
        text_y += line_h
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
WM_NCHITTEST = 0x0084
WM_SETCURSOR = 0x0020
WM_MOUSEWHEEL = 0x020A
HTCAPTION = 2
HTCLIENT = 1
HTTRANSPARENT = -1
HTBOTTOMRIGHT = 17
IDC_SIZEALL = 32646
IDC_SIZENWSE = 32642
MIN_SCALE = 0.50          # 再小字就看不清了
MAX_SCALE = 1.80
RESIZE_GRIP = 20          # 右下角多少像素算「缩放把手」
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MF_STRING = 0x0000
MF_GRAYED = 0x0001
MF_CHECKED = 0x0008
MF_POPUP = 0x0010
MF_SEPARATOR = 0x0800
TPM_RETURNCMD = 0x0100
TPM_LEFTALIGN = 0x0000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040
SWP_NOACTIVATE = 0x0010


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
    u.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p, wintypes.LPARAM]
    u.EnumDisplayMonitors.restype = ctypes.c_bool
    u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    u.GetMonitorInfoW.restype = ctypes.c_bool
    u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    u.FindWindowW.restype = wintypes.HWND
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
                 volume: float = 0.55, muted: bool = False, exclude_capture: bool = False,
                 voice: str = "mambo"):
        self.toolwindow = toolwindow
        self.topmost = topmost
        self.click_through = click_through
        self.exclude_capture = exclude_capture
        self.speaker = Speaker(volume, enabled=not muted, voice=voice,
                               on_start=self.start_talking)
        self.text = ""
        self.status = {}
        self.presence = CasterState()      # 说话/思考/空闲三态（真实信号驱动）
        self._think_seq = 0
        self._audio_seq = -1               # 真实播放事件的序号（旧帧不许覆盖新状态）
        self._pb_playing = False
        self._pb_speaking = False
        self.talk_start_at = 0.0
        self.talk_until = 0.0
        self.enter_at = 0.0
        self.dirty = True
        self.running = True
        # 穿透模式下的「临时可交互」：鼠标贴到 ☰/缩放手把时摘掉 WS_EX_TRANSPARENT，
        # 离开就恢复。保留整窗穿透（不为了能操作就把穿透关掉）。
        self._hot = False
        self._menu_open = False
        self._menu_models: list[str] = []
        self._menu_games: list[tuple[str, str]] = []
        self._transparent = bool(click_through)
        self._watch_stop = threading.Event()
        self._memdc = None
        self._hit_alpha = None
        self._hbmp = None
        self._dib = None
        self._dib_size = (0, 0)
        self._hwnd = None
        self._resizing = False
        self._resize_start = (0, 0, 0, 0)   # 按下时的 (鼠标x, 窗口left, 窗口top, 窗口宽)
        self._wndproc = WNDPROC(self._proc)  # 保引用，否则回调被回收会崩
        # 尺寸优先级：命令行 --scale > 上次拖出来的 > 系统 DPI
        saved = self._load_state()
        self.scale = self._clamp_scale(scale or float(saved.get("scale") or self._default_scale()))
        base_w, base_h = self._base_wh()
        _w, _h = int(base_w * self.scale), int(base_h * self.scale)
        if pos:
            self.pos = self._clamp(*pos, _w, _h)
        elif saved.get("x") is not None:
            self.pos = self._clamp(int(saved["x"]), int(saved["y"]), _w, _h)
        else:
            # 没存档就跟着游戏窗口所在的显示器放（副屏打游戏时弹幕窗也在副屏）
            gx, gy = self._game_window_center()
            self.pos = self._clamp(gx - _w // 2, gy - int(120 * self.scale), _w, _h)
        self._cls = f"StS2Streamer{os.getpid()}"
        self._register()
        self._create()

    # ---- 形态钩子：面板（默认）/ 桌宠（PetWindow 覆盖）
    @staticmethod
    def _base_wh() -> tuple[int, int]:
        return BASE_W, 420

    @staticmethod
    def _default_scale() -> float:
        return dpi_scale()

    @staticmethod
    def _state_path() -> str:
        return STATE_FILE

    @staticmethod
    def _title() -> str:
        return "大肥鱼 · 主播模式"

    def _render_frame(self, text: str, status: dict, scale: float, talk: float,
                      layout_text: str | None = None, chip: bool = False) -> Image.Image:
        return render(text, status, scale, talk, chip=chip, layout_text=layout_text)

    def _is_animating(self, now: float) -> bool:
        return now < max(self.talk_until, self.enter_at + 0.32)

    def _frame_interval(self, now: float) -> float:
        return 0.033

    def _run_hint(self) -> str:
        return ("主播模式已启动：左键拖 · 右下角拖=缩放 · 滚轮=缩放 · 右键菜单 · "
                "穿透时点右下 ☰ · Ctrl+Alt+D 穿透 · Ctrl+Alt+S 声音 · Ctrl+Alt+H 隐藏 · Ctrl+Alt+Q 退出")

    @staticmethod
    def _game_window_center() -> tuple[int, int]:
        """游戏窗口中心（物理像素）；找不到就退回主屏中心。"""
        try:
            h = user32.FindWindowW(None, GAME_WINDOW_TITLE) if GAME_WINDOW_TITLE else 0
            if h:
                r = wintypes.RECT()
                user32.GetWindowRect(h, ctypes.byref(r))
                return (r.left + r.right) // 2, (r.top + r.bottom) // 2
        except Exception:
            pass
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        return sw // 2, sh // 3

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
        img = self._render_at(self.scale)
        x, y = self.pos
        self._hwnd = user32.CreateWindowExW(
            ex, self._cls, self._title(), WS_POPUP,
            x, y, img.width, img.height, None, None, self._hinst, None,
        )
        if not self._hwnd:
            raise OSError("CreateWindowExW failed")
        # CreateWindowExW 的坐标偶尔会被系统按虚拟屏/DPI 再挪一次（x≈2564 的旧存档
        # 能把窗口 creat 到主屏外面，弹幕一条都看不见），所以显式再钉一次位置。
        user32.SetWindowPos(self._hwnd, 0, x, y, 0, 0, 0x0001 | 0x0004)  # SWP_NOSIZE|SWP_NOZORDER
        if os.environ.get("STS2_STREAMER_DEBUG"):
            _r = wintypes.RECT()
            user32.GetWindowRect(self._hwnd, ctypes.byref(_r))
            print(f"_create want=({x},{y}) got=({_r.left},{_r.top},{_r.right},{_r.bottom})")
        user32.ShowWindow(self._hwnd, 8)  # SW_SHOWNA
        if self.exclude_capture:
            self.apply_capture_exclusion(True)
        for hid, key in ((1, ord("D")), (2, ord("H")), (3, ord("Q")), (4, ord("S"))):
            user32.RegisterHotKey(self._hwnd, hid, MOD_CONTROL | MOD_ALT, key)
        self._paint(img)
        threading.Thread(target=self._watch_interaction, daemon=True).start()

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
        # 穿透模式下只有「鼠标贴到 ☰/手把」时窗口才临时接收鼠标（见 _watch_interaction）。
        # 以前这里是 if self.click_through: return 0 —— 穿透开着就永远点不到，弹窗成了死的。
        blocked = self.click_through and not self._hot
        try:
            if msg == WM_NCHITTEST and not self.click_through:
                # 透明像素不吃鼠标：桌宠窗口是 560×500 的矩形，鱼只占下面一块，
                # 空白处若照样接收点击，打游戏时点到空气也会点到窗口。
                # 返回 HTTRANSPARENT 让点击落到底下的游戏/OBS 上；右下缩放手把例外。
                try:
                    sx = ctypes.c_short(lparam & 0xFFFF).value
                    sy = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                    pt = wintypes.POINT(sx, sy)
                    user32.ScreenToClient(hwnd, ctypes.byref(pt))
                    hit = getattr(self, "_hit_alpha", None)
                    if hit:
                        hw, hh, alpha = hit
                        grip = max(10, int(RESIZE_GRIP * self.scale))
                        in_grip = pt.x >= hw - grip and pt.y >= hh - grip
                        if not in_grip and 0 <= pt.x < hw and 0 <= pt.y < hh:
                            if alpha[pt.y * hw + pt.x] < 20:
                                return HTTRANSPARENT
                except Exception:
                    pass
                return HTCLIENT
            if msg == WM_LBUTTONDOWN:
                if blocked:
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
                if not self.click_through or self._hot:
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
                if blocked:
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
        # 菜单开着的时候必须保持可交互：光标会从 ☰ 移开，若这时恢复穿透，
        # 菜单点一半就被「点穿」了。
        self._menu_open = True
        self.tick_interaction()
        hmenu = user32.CreatePopupMenu()
        user32.AppendMenuW(hmenu, MF_STRING | (MF_CHECKED if self.topmost else 0), 1, "置顶")
        user32.AppendMenuW(hmenu, MF_STRING | (MF_CHECKED if self.click_through else 0), 2, "点击穿透")
        user32.AppendMenuW(hmenu, MF_STRING | (MF_CHECKED if self.speaker.enabled else 0), 3, "声音")
        user32.AppendMenuW(hmenu, MF_STRING | (MF_CHECKED if self.exclude_capture else 0), 4, "录屏里隐藏")

        # 语音 / 模型 / 游戏：配置写本机 config.json（.gitignore 已忽略，API Key 不在这里）
        try:
            from ..core.config import LiveConfig

            cfg = LiveConfig.load()
            models = [str(m) for m in (cfg.llm_models or []) if str(m).strip()]
            if cfg.llm_model and cfg.llm_model not in models:
                models.insert(0, cfg.llm_model)
        except Exception:
            cfg, models = None, []
        self._menu_models = models
        voice_menu = user32.CreatePopupMenu()
        for idx, (key, label) in enumerate(_VOICE_CHOICES):
            user32.AppendMenuW(
                voice_menu,
                MF_STRING | (MF_CHECKED if self.speaker.voice == key else 0),
                10 + idx, label)
        user32.AppendMenuW(voice_menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(voice_menu, MF_STRING, 13, "启动曼波引擎")
        user32.AppendMenuW(hmenu, MF_POPUP, voice_menu, "语音")
        model_menu = user32.CreatePopupMenu()
        for idx, model in enumerate(models[:12]):
            user32.AppendMenuW(
                model_menu,
                MF_STRING | (MF_CHECKED if cfg and cfg.llm_model == model else 0),
                20 + idx, model)
        user32.AppendMenuW(model_menu, MF_STRING, 40, "自定义模型…")
        user32.AppendMenuW(hmenu, MF_POPUP, model_menu, "模型")

        # 游戏：从 games/registry.json 读；切换只写 config.game，由 Agent/游戏层消费。
        games: list[tuple[str, str]] = []
        try:
            from ..games.registry import GameRegistry

            games = [(p.info.id, p.info.name) for p in GameRegistry().packs()]
        except Exception:
            games = []
        self._menu_games = games
        game_menu = user32.CreatePopupMenu()
        for idx, (game_id, name) in enumerate(games[:12]):
            user32.AppendMenuW(
                game_menu,
                MF_STRING | (MF_CHECKED if cfg and cfg.game == game_id else 0),
                50 + idx, name)
        if not games:
            user32.AppendMenuW(game_menu, MF_STRING | MF_GRAYED, 0, "没有注册游戏")
        user32.AppendMenuW(hmenu, MF_POPUP, game_menu, "游戏")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, 41, "配置 API…")
        user32.AppendMenuW(hmenu, MF_STRING, 42, "打开管理端")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, 5, "重新显示示例")
        user32.AppendMenuW(hmenu, MF_STRING, 6, "关闭")
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # 不加这两句，菜单点到别处不会收起（Win32 的老毛病）
        user32.SetForegroundWindow(self._hwnd)
        user32.PostMessageW(self._hwnd, 0x0000, 0, 0)  # WM_NULL
        cmd = user32.TrackPopupMenu(hmenu, TPM_RETURNCMD | TPM_LEFTALIGN, pt.x, pt.y, 0, self._hwnd, None)
        user32.DestroyMenu(hmenu)
        self._menu_open = False
        self.tick_interaction()
        if 10 <= cmd < 10 + len(_VOICE_CHOICES):
            self.set_voice(_VOICE_CHOICES[cmd - 10][0])
        elif cmd == 13:
            self.start_mambo()
        elif 20 <= cmd < 20 + len(self._menu_models):
            self.set_model(self._menu_models[cmd - 20])
        elif 50 <= cmd < 50 + len(self._menu_games):
            self.set_game(self._menu_games[cmd - 50][0])
        elif cmd == 40:
            self.open_studio("#settings")
        elif cmd == 41:
            self.open_studio("#settings")
        elif cmd == 42:
            self.open_studio()
        elif cmd == 1:
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
                      {"character": "演示角色", "ascension": 10, "floor": 8, "hp": 23, "max_hp": 80, "gold": 20})
        elif cmd == 6:
            self.close()

    def set_voice(self, voice: str) -> None:
        """切换桌宠发声并持久化；曼波未就绪时顺手拉起引擎。"""
        self.speaker.voice = voice
        self.speaker.enabled = voice != "off"
        try:
            from ..core.config import LiveConfig

            cfg = LiveConfig.load()
            cfg.save_patch({"voice": "animal" if voice == "animalese" else voice})
            if voice == "mambo":
                self.start_mambo(cfg)
        except Exception as exc:
            print(f"语音配置保存失败：{exc}")
        print(f"语音：{dict(_VOICE_CHOICES).get(voice, voice)}")

    def start_mambo(self, cfg=None) -> None:
        """点击「曼波语 / 启动曼波引擎」时后台拉起 MamboTTS，不阻塞桌宠。"""
        threading.Thread(target=self._start_mambo_bg, args=(cfg,), daemon=True).start()

    def _start_mambo_bg(self, cfg=None) -> None:
        try:
            from ..core.config import LiveConfig
            from ..voice.startup import start_from_config

            result = start_from_config(cfg or LiveConfig.load())
            print("曼波：" + str(result.get("message") or result))
        except Exception as exc:
            print(f"曼波引擎启动失败：{exc}")

    def set_game(self, game_id: str) -> None:
        """切换当前游戏（写 config.game；游戏适配器/Agent 下一轮读取）。"""
        try:
            from ..core.config import LiveConfig

            cfg = LiveConfig.load()
            if cfg.game == game_id:
                return
            cfg.save_patch({"game": game_id})
            name = dict(self._menu_games).get(game_id, game_id)
            print(f"游戏：{name}（{game_id}）")
        except Exception as exc:
            print(f"游戏配置保存失败：{exc}")

    def set_model(self, model: str) -> None:
        """切换模型（只写本机 config.json；API Key 不进菜单）。"""
        try:
            from ..core.config import LiveConfig

            LiveConfig.load().save_patch({"llm_model": model})
            print(f"模型：{model}")
        except Exception as exc:
            print(f"模型配置保存失败：{exc}")

    def open_studio(self, anchor: str = "") -> None:
        """右键菜单直达管理端（配置 API / 看决策和日志）。"""
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{PORT}/studio{anchor}")

    def toggle_sound(self):
        on = self.speaker.toggle()
        print("声音：" + ("开" if on else "关（Ctrl+Alt+S 切回）"))
        if on:
            self.speaker.speak("声音开了。")

    def toggle_click_through(self):
        self.click_through = not self.click_through
        self._hot = False        # 切回来时先按「不在热区」算，交给 _watch_interaction 纠正
        self._apply_transparent(self.click_through)
        self.dirty = True
        print("点击穿透：" + ("开（☰ 或 Ctrl+Alt+D 关）" if self.click_through else "关"))

    def _apply_transparent(self, on: bool):
        """只改 WS_EX_TRANSPARENT 这一位；状态没变就不碰窗口（SetWindowLongW 会触发重排）。"""
        ex = user32.GetWindowLongW(self._hwnd, -20)
        want = (ex | WS_EX_TRANSPARENT) if on else (ex & ~WS_EX_TRANSPARENT)
        if want != ex:
            user32.SetWindowLongW(self._hwnd, -20, want)
        self._transparent = bool(on)

    def _cursor_in_hot_zone(self) -> bool:
        try:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            rect = wintypes.RECT()
            user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
            return self._hot_zone_point(pt.x, pt.y, rect, self.scale)
        except Exception:
            return False

    @staticmethod
    def _hot_zone_point(px: int, py: int, rect, scale: float) -> bool:
        """穿透模式下「临时可交互」的两块区域：☰ 菜单钮 + 右下缩放手把。

        故意只留这两个小块：整块面板都可交互的话，打游戏时鼠标经过弹窗就会
        被它吃掉点击（这正是「为了能操作而关掉穿透」的坏做法）。
        """
        w, h = rect.right - rect.left, rect.bottom - rect.top
        cl, ct, cr, cb = chip_rect(w, h, scale)
        if rect.left + cl <= px < rect.left + cr and rect.top + ct <= py < rect.top + cb:
            return True
        grip = max(10, int(RESIZE_GRIP * scale))
        return px >= rect.right - grip and py >= rect.bottom - grip

    def tick_interaction(self) -> bool:
        """穿透模式下按鼠标位置临时开/关穿透；返回当前是否在热区。"""
        want = self._cursor_in_hot_zone() if self.click_through else False
        if self._menu_open:
            want = True
        if want != self._hot:
            self._hot = want
            self._apply_transparent(self.click_through and not want)
            self.dirty = True
            if os.environ.get("STS2_STREAMER_DEBUG"):
                print(f"穿透交互区：{'进入' if want else '离开'}")
        return want

    def _watch_interaction(self):
        """50ms 轮询鼠标位置。只在状态翻转时调 SetWindowLongW，不打热循环。"""
        while not self._watch_stop.is_set() and self.running:
            try:
                self.tick_interaction()
            except Exception:
                pass
            self._watch_stop.wait(0.03)

    def toggle_visible(self):
        if user32.IsWindowVisible(self._hwnd):
            user32.ShowWindow(self._hwnd, 0)
        else:
            user32.ShowWindow(self._hwnd, 8)

    def close(self):
        self._save_pos()
        self.running = False
        self._watch_stop.set()
        user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)

    # ---- 画面
    def _paint(self, img: Image.Image, at: tuple[int, int] | None = None, resize: bool = True):
        w, h = img.size
        self._hit_alpha = (w, h, img.getchannel("A").tobytes())
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

    def start_talking(self, duration: float, text: str = ""):
        """在 PlaySound 真正开始后由 Speaker 回调，动画和声音同一时刻起。

        文案可以立刻出现（和页面一样先看到字），但头像不抖；旧实现 push()
        一开始就设 talk_until，合成/等待页面期间头像白抖 2 秒。
        """
        if text and text != self.text:
            return  # 上一句的迟到回调，不能拿来抖新弹幕
        now = time.perf_counter()
        dur = float(duration or 0.0)
        if dur <= 0:
            dur = min(8.5, max(2.4, 1.5 + len(self.text or "") * 0.07))
        self.talk_start_at = now
        self.talk_until = now + min(30.0, max(0.12, dur))
        presence = getattr(self, "presence", None)
        if presence is not None:
            presence.speech_started(self.talk_until - now)
        self.dirty = True

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
        # 文案入场立刻开始；喊话/头像动画等 Speaker 的 on_start 回调。
        self.talk_start_at = 0.0
        self.talk_until = 0.0
        self.dirty = True
        # 台词一到就算「说话状态」：字立刻出现，声音（曼波合成 0.5–2.5s）随后跟上。
        # 真实起播时 start_talking 会用 WAV 实测时长覆盖这个估计值。
        presence = getattr(self, "presence", None)
        if presence is not None:
            presence.on_line(text)
        self.speaker.speak(text)   # 和页面一样：说的就是气泡里这一句

    def set_thinking(self, on: bool, seq: int | None = None, note: str = "") -> None:
        """overlay `/thinking` 的落点：seq 迟到的事件由 CasterState 自己丢掉。"""
        before = self.presence.state
        self.presence.set_thinking(bool(on), seq, note)
        if os.environ.get("STS2_STREAMER_DEBUG"):
            print(f"思考状态：on={bool(on)} seq={seq} {before}->{self.presence.state}")
        self.dirty = True

    def set_playback(self, pb: dict | None) -> None:
        """overlay `playback` 的落点：**真实音频生命周期**。

        页面（直播姬/OBS browser source）才是唯一的喇叭时，窗口拿不到
        Speaker 的 on_start，只能靠这些自报事件：正在播/还剩多久/队列里还有几条。
        旧实现只按字数估时长，配音要等 2–6 秒合成才起播，于是「话没说完就
        显示思考中」——实测 40/40 句都提前切，中位早 4.2 秒。
        """
        if not isinstance(pb, dict):
            return
        try:
            seq = int(pb.get("seq") or 0)
        except (TypeError, ValueError):
            seq = self._audio_seq + 1      # 脏数据当最新帧处理，但绝不许崩
        if seq < self._audio_seq:
            return          # 迟到的旧帧：不许覆盖更新的播放状态
        self._audio_seq = seq
        before = self.presence.state
        speaking = bool(pb.get("speaking"))
        playing = bool(pb.get("playing"))
        if playing:
            try:
                left = float(pb.get("remaining") or 0.0)
            except (TypeError, ValueError):
                left = 0.0
            self.presence.audio_started(left, str(pb.get("cid") or ""))
            self.sync_talk(left, restart=not self._pb_playing)
        elif speaking:
            # 合成/排队空档：队列里确实还有台词，说话状态不能断
            self.presence.audio_queue(True)
        elif self._pb_speaking:
            # 从「在说/排队」落到「真结束」才清，避免每轮空闲都把状态重置一遍
            self.presence.audio_ended()
            self.sync_talk(0.0)
        self._pb_playing = playing
        self._pb_speaking = speaking
        if os.environ.get("STS2_STREAMER_DEBUG") and before != self.presence.state:
            print(f"播放状态：{before}->{self.presence.state} {pb}", flush=True)
        if before != self.presence.state:
            self.dirty = True

    def sync_talk(self, remaining: float, restart: bool = False) -> None:
        """头像抖动跟着**真实播放**走：页面在响，窗口就该抖，不用等自己的 Speaker。"""
        now = time.perf_counter()
        if remaining and remaining > 0:
            if restart or not self.talk_until or now >= self.talk_until:
                self.talk_start_at = now
                self.dirty = True
            self.talk_until = now + min(30.0, max(0.12, float(remaining)))
        else:
            if self.talk_until or self.talk_start_at:
                self.dirty = True
            self.talk_start_at = 0.0
            self.talk_until = 0.0

    def display_text(self, mode: str) -> str:
        """气泡里该显示什么：说话显示台词，思考「思考ing」，空闲「吃饭ing」。"""
        if mode == "thinking":
            return THINKING_TEXT
        if mode == "speaking":
            return self.text or IDLE_TEXT
        return IDLE_TEXT

    def update_status(self, status: dict):
        if status and status != self.status:
            self.status = dict(status)
            self.dirty = True

    def tick_presence(self) -> str:
        """结算三态，并保证「状态机说停 → 头像立刻停抖」。主循环和 frame() 都走这里。"""
        mode = self.presence.tick()
        if mode != "speaking" and self.talk_until:
            # 宁可少抖一拍：声音没了就不许还在说话
            self.sync_talk(0.0)
        return mode

    def frame(self) -> Image.Image:
        now = time.perf_counter()
        mode = self.tick_presence()
        if self.presence.dirty:
            self.presence.dirty = False
            self.dirty = True
        talk = 0.0
        if self.talk_start_at and now < self.talk_until:
            span = max(0.001, self.talk_until - self.talk_start_at)
            talk = min(1.0, max(0.0, (now - self.talk_start_at) / span))
        text = self.display_text(mode)
        layout_text = self.text if mode == "thinking" and self.text else None
        if os.environ.get("STS2_STREAMER_DEBUG"):
            print(f"frame mode={mode} ct={self.click_through} layout={None if not layout_text else len(layout_text)}",
                  flush=True)
        img = self._render_frame(text, self.status, self.scale, talk,
                                 layout_text=layout_text, chip=self.click_through)
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
        return self._render_frame(self.display_text(self.presence.state), self.status,
                                  scale, 0.0, chip=self.click_through)

    @classmethod
    def _clamp_scale(cls, scale: float) -> float:
        screen_w = user32.GetSystemMetrics(0)
        base_w = cls._base_wh()[0]
        hi = min(MAX_SCALE, screen_w * 0.95 / base_w)
        return max(MIN_SCALE, min(hi, float(scale)))

    def zoom(self, factor: float):
        self.apply_scale(self.scale * factor)

    def _resize_to_cursor(self, cursor_x: int):
        """拖动把手时的换算：宽度跟手走，高度跟宽度走（等比）。"""
        x0, left, top, w0 = self._resize_start
        self.apply_scale((w0 + (cursor_x - x0)) / self._base_wh()[0], anchor=(left, top))

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
    def _load_state(self) -> dict:
        try:
            with open(self._state_path(), encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _monitors() -> list[tuple[int, int, int, int]]:
        """所有显示器的物理像素矩形（可能不在 (0,0) 起）。"""
        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        out = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        def cb(hmon, hdc, lprc, lparam):
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                m = mi.rcMonitor
                out.append((m.left, m.top, m.right, m.bottom))
            return True

        user32.EnumDisplayMonitors(0, 0, cb, 0)
        return out or [(0, 0) + (int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1)))]

    @classmethod
    def _clamp(cls, x: int, y: int, w: int = 0, h: int = 0) -> tuple[int, int]:
        """把窗口夹到「至少有一部分在某块显示器上」。

        游戏在副屏时弹幕窗也必须能待在副屏——这里只防「完全跑到所有显示器之外」，
        绝不把窗口从副屏拽回主屏。窗口比显示器还宽/高时，贴齐显示器左上角。
        """
        w = int(w or 200)
        h = int(h or 80)
        best = None          # (overlap_area, rect)
        nearest = None       # (dist, rect)
        for (l, t, r, b) in cls._monitors():
            ov_w = min(x + w, r) - max(x, l)
            ov_h = min(y + h, b) - max(y, t)
            area = max(0, ov_w) * max(0, ov_h)
            dist = 0 if area else min(abs(x - l), abs(x - r)) + min(abs(y - t), abs(y - b))
            if ov_w >= min(w, 120) and ov_h >= 40:          # 已经可见
                return (x, y)
            if best is None or area > best[0]:
                best = (area, (l, t, r, b))
            if nearest is None or dist < nearest[0]:
                nearest = (dist, (l, t, r, b))
        target = (best[1] if best and best[0] > 0 else (nearest[1] if nearest else (0, 0, 1920, 1080)))
        l, t, r, b = target
        nx = max(l, min(x, r - min(w, r - l)))
        ny = max(t, min(y, b - min(h, b - t)))
        return (nx, ny)

    def _save_pos(self):
        try:
            rect = wintypes.RECT()
            user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
            path = self._state_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"x": rect.left, "y": rect.top, "scale": round(self.scale, 3)}, f)
        except Exception:
            pass

    def _ensure_on_screen(self):
        """主循环每 2s 兜一次：窗口如果在主屏工作区外（旧存档/系统挪动），挪回来。"""
        try:
            rect = wintypes.RECT()
            user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
            w, h = rect.right - rect.left, rect.bottom - rect.top
            nx, ny = self._clamp(rect.left, rect.top, w, h)
            if (nx, ny) != (rect.left, rect.top):
                user32.SetWindowPos(self._hwnd, 0, nx, ny, 0, 0, 0x0001 | 0x0004)
                self.pos = (nx, ny)
                self._save_pos()
        except Exception:
            pass

    def run(self):
        print(self._run_hint())
        print("（默认能被录屏拍到；要隐藏请右键勾「录屏里隐藏」或加 --exclude-capture）")
        last = 0.0
        last_guard = 0.0
        last_mode = self.presence.state
        while self.running:
            msg = wintypes.MSG()
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            now = time.perf_counter()
            # 三态的到期结算（说话窗口结束 / 思考超时）必须在主循环里跑：
            # 只在 frame() 里 tick 的话，静止时根本不会重画，状态永远停在旧值。
            mode = self.tick_presence()
            if mode != last_mode:
                last_mode = mode
                self.dirty = True
            if now - last_guard > 2.0:
                last_guard = now
                self._ensure_on_screen()
            animating = self._is_animating(now)
            if self.dirty or animating:
                if animating or now - last > 0.05:
                    self._paint(self.frame())
                    self.dirty = False
                    last = now
                time.sleep(self._frame_interval(now))   # 面板 30fps；桌宠空闲会降帧
            else:
                time.sleep(0.05)
        self._save_pos()


# ---------------------------------------------------------------- 桌宠形态
class PetWindow(StreamerWindow):
    """用三视图精灵的桌面桌宠：拖动换朝向 + 实时形变，说话走漫画气泡。

    「鼠标往对应方向拖动」不是播动画，而是按窗口位移实时选朝向：
    左右 → 侧面（自动镜像）、向上 → 背面、向下 → 正面；同时按速度倾斜/压扁/拉长，
    松手回正，带一点回弹。精灵本身只有三张静态图，动态都是画出来的。
    """

    def __init__(self, *args, **kwargs):
        self.pet_facing = "front"
        self.pet_face_right = False
        self.pet_lean = 0.0
        self.pet_energy = 0.0
        self.bubble_until = 0.0
        self._drag_prev: tuple[int, int] | None = None
        self._drag_until = 0.0
        self._release_at = 0.0
        # 原生 HTCAPTION 拖动会让系统模态移动循环卡住本线程的主循环，
        # 拖动过程中一帧都画不出来。桌宠改成自己 SetWindowPos，消息循环不断。
        self._dragging = False
        self._drag_anchor = (0, 0)
        self._drag_origin = (0, 0)
        self._drag_last_cursor = (0, 0)
        super().__init__(*args, **kwargs)
        # 初始按「鼠标在鱼身上」算：先保证可交互，第一帧轮询再把空白区域设成穿透
        self._hot = True
        self._transparent = False
        self._apply_transparent(False)

    # ---- 形态
    @staticmethod
    def _base_wh() -> tuple[int, int]:
        return PET_BASE_W, PET_BASE_H

    @staticmethod
    def _default_scale() -> float:
        # 桌宠别跟着 175% DPI 长到半个屏：默认封顶 1.15×，用户仍可滚轮放大
        return min(1.15, dpi_scale())

    @staticmethod
    def _state_path() -> str:
        return PET_STATE_FILE

    @staticmethod
    def _title() -> str:
        return "大肥鱼 · 桌宠"

    def _render_frame(self, text: str, status: dict, scale: float, talk: float,
                      layout_text: str | None = None, chip: bool = False) -> Image.Image:
        return render_pet(text, self.presence.state, scale, time.perf_counter(), self)

    def display_text(self, mode: str) -> str:
        if mode == "thinking":
            return THINKING_TEXT
        if mode == "speaking":
            return self.text or IDLE_TEXT
        return IDLE_TEXT

    def _is_animating(self, now: float) -> bool:
        return True   # 呼吸/摇摆是常驻的

    def _frame_interval(self, now: float) -> float:
        if now < self._drag_until or now < self.talk_until:
            return 0.025
        return 0.05

    # ---- 拖动 → 朝向 / 形变
    def _proc(self, hwnd, msg, wparam, lparam):
        """桌宠自己拖：不调 HTCAPTION，否则系统移动循环会卡住渲染主循环。"""
        if msg == WM_LBUTTONDOWN and (not self.click_through or self._hot) \
                and self._hit_test() != HTBOTTOMRIGHT:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            self._dragging = True
            self._drag_anchor = (pt.x, pt.y)
            self._drag_origin = (rect.left, rect.top)
            self._drag_last_cursor = (pt.x, pt.y)
            self._drag_prev = (rect.left, rect.top)
            user32.SetCapture(hwnd)
            return 0
        if msg == WM_MOUSEMOVE and self._dragging:
            self._manual_drag()
            return 0
        if msg == WM_LBUTTONUP and self._dragging:
            self._dragging = False
            self._drag_prev = None
            self._release_at = time.perf_counter()
            user32.ReleaseCapture()
            self._save_pos()
            return 0
        return super()._proc(hwnd, msg, wparam, lparam)

    def _manual_drag(self) -> None:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        dx = pt.x - self._drag_anchor[0]
        dy = pt.y - self._drag_anchor[1]
        user32.SetWindowPos(self._hwnd, None, self._drag_origin[0] + dx,
                            self._drag_origin[1] + dy, 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        sx = pt.x - self._drag_last_cursor[0]
        sy = pt.y - self._drag_last_cursor[1]
        self._drag_last_cursor = (pt.x, pt.y)
        if abs(sx) + abs(sy) >= 1:
            now = time.perf_counter()
            self.pet_energy = min(1.0, (abs(sx) + abs(sy)) / 10.0)
            if abs(sx) >= abs(sy) * 0.85:
                self.pet_facing = "side"
                self.pet_face_right = sx > 0
                self.pet_lean = max(-14.0, min(14.0, -sx * 2.0))
            else:
                self.pet_facing = "back" if sy < 0 else "front"
                self.pet_lean = max(-8.0, min(8.0, -sx * 0.5))
            self._drag_until = now + 0.18
            self.dirty = True

    def _track_drag(self, now: float) -> None:
        if self._dragging:
            return
        try:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            rect = wintypes.RECT()
            user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        except Exception:
            return
        down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
        margin = int(40 * self.scale)
        inside = (rect.left - margin <= pt.x <= rect.right + margin
                  and rect.top - margin <= pt.y <= rect.bottom + margin)
        prev = self._drag_prev
        moved = False
        if down and inside and prev is not None:
            dx = rect.left - prev[0]
            dy = rect.top - prev[1]
            if abs(dx) + abs(dy) >= 1:
                moved = True
                self.pet_energy = min(1.0, (abs(dx) + abs(dy)) / 6.0)
                if abs(dx) >= abs(dy) * 0.85:
                    self.pet_facing = "side"
                    self.pet_face_right = dx > 0
                    self.pet_lean = max(-14.0, min(14.0, -dx * 2.2))
                else:
                    self.pet_facing = "back" if dy < 0 else "front"
                    self.pet_lean = max(-8.0, min(8.0, -dx * 0.5))
                self._drag_until = now + 0.18
        if down and inside:
            self._drag_prev = (rect.left, rect.top)
        else:
            if prev is not None:
                self._release_at = now
            self._drag_prev = None
        if not moved:
            self.pet_lean *= 0.86
            self.pet_energy *= 0.88
        if now > self._drag_until and now > self._release_at + 0.45:
            self.pet_facing = "front"
            self.pet_face_right = False

    def _hot_zone_point(self, px: int, py: int, rect, scale: float) -> bool:
        """穿透时只有「鱼身+右下把手」能临时点：别整窗吃鼠标。"""
        grip = max(10, int(RESIZE_GRIP * scale))
        if px >= rect.right - grip and py >= rect.bottom - grip:
            return True
        w, h = rect.right - rect.left, rect.bottom - rect.top
        fx, fy = rect.left + w * 0.18, rect.top + h * 0.32
        return fx <= px <= fx + w * 0.64 and fy <= py <= rect.bottom

    def _cursor_on_pet(self) -> bool:
        """光标是否压在鱼/气泡的不透明像素上（含右下缩放手把）。"""
        hit = self._hit_alpha
        if not hit:
            return True
        try:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            rect = wintypes.RECT()
            user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
            w, h = rect.right - rect.left, rect.bottom - rect.top
            hw, hh, alpha = hit
            grip = max(10, int(RESIZE_GRIP * self.scale))
            x, y = pt.x - rect.left, pt.y - rect.top
            if x >= w - grip and y >= h - grip:
                return True
            if not (0 <= x < hw and 0 <= y < hh):
                return False
            return alpha[y * hw + x] >= 20
        except Exception:
            return True

    def tick_interaction(self) -> bool:
        """非穿透模式下也让透明像素穿透：只有鱼身/气泡吃鼠标。

        窗口是 560×500 的矩形，鱼只占下面一块；不做逐像素判定的话，
        鱼头顶上的空白会挡游戏点击。用 50ms 轮询切 WS_EX_TRANSPARENT，
        比 WM_NCHITTEST 的 HTTRANSPARENT 跨进程更可靠。
        """
        if self.click_through:
            return super().tick_interaction()
        want = bool(self._menu_open or self._resizing or self._cursor_on_pet())
        if want != self._hot:
            self._hot = want
            self._apply_transparent(not want)
            self.dirty = True
            if os.environ.get("STS2_STREAMER_DEBUG"):
                print(f"桌宠交互区：{'进入' if want else '离开'}")
        return want

    def frame(self) -> Image.Image:
        now = time.perf_counter()
        self._track_drag(now)
        mode = self.tick_presence()
        if self.presence.dirty:
            self.presence.dirty = False
        return render_pet(self.display_text(mode), mode, self.scale, now, self)

    def start_talking(self, duration: float, text: str = ""):
        super().start_talking(duration, text)
        if self.text and (not text or text == self.text):
            self.bubble_until = max(self.bubble_until,
                                    time.perf_counter() + max(1.0, float(duration or 0)) + 1.6)

    def push(self, thought: str, meme: str, status: dict | None = None):
        super().push(thought, meme, status)
        if self.text:
            self.bubble_until = time.perf_counter() + 4.5

    def _run_hint(self) -> str:
        return ("桌宠已启动：左键拖（左右/上下换朝向，带倾斜和速度线）· 滚轮/右下角=缩放 · "
                "右键菜单 · 穿透时鼠标贴鱼身 · Ctrl+Alt+D 穿透 · Ctrl+Alt+S 声音 · "
                "Ctrl+Alt+H 隐藏 · Ctrl+Alt+Q 退出")


# ---------------------------------------------------------------- 数据
def fetch_state(port: int, after: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/state?after={after}", timeout=1.0) as r:
        return json.load(r)


def poll_loop(win: StreamerWindow, port: int, stop: threading.Event, demo: bool):
    last_id = 0
    fails = 0
    beat_at = 0.0
    if demo:
        win.push("这一手先算账：它 21，我最多挡 15。", "差 6，嗨呀。",
                 {"character": "演示角色", "ascension": 10, "floor": 8, "hp": 23, "max_hp": 80, "gold": 20})
    while not stop.is_set():
        try:
            d = fetch_state(port, last_id)
        except Exception:
            fails += 1
            if fails == 5:
                print(f"主播窗：拿不到 /state（连续 {fails} 次），后台继续重试…")
            stop.wait(1.0)
            continue
        if fails >= 5:
            print("主播窗：数据恢复")
        fails = 0
        try:
            if isinstance(d.get("seq"), int) and d["seq"] < last_id:
                last_id = 0
            win.update_status(d.get("status") or {})
            # 真实播放状态先落：说话/思考由「真的在响吗」决定，不由字数估。
            win.set_playback(d.get("playback"))
            th = d.get("thinking")
            if isinstance(th, dict):
                # seq 单调：轮询偶尔乱序/重连时，旧状态不许把新的盖回去
                win.set_thinking(bool(th.get("on")), th.get("seq"), str(th.get("note") or ""))
            for c in d.get("comments") or []:
                last_id = max(last_id, int(c.get("id", 0)))
            if d.get("comments"):
                latest = d["comments"][-1]
                win.push(latest.get("thought", ""), latest.get("meme", ""), d.get("status") or {})
        except Exception as e:  # 单帧画崩不许带走整个轮询循环
            print(f"主播窗：这一帧没画出来（{type(e).__name__}: {e}），继续轮询")
        now = time.time()
        if now - beat_at > 2.0:
            beat_at = now
            try:  # 心跳：tools/live.py check 靠它判断「窗口还活着吗」
                os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
                with open(os.path.join(ROOT, "logs", "streamer_heartbeat.json"), "w", encoding="utf-8") as f:
                    json.dump({"ts": now, "seq": last_id, "pid": os.getpid()}, f)
            except Exception:
                pass
        stop.wait(0.35)


def ensure_server(port: int) -> bool:
    """overlay server 没起就拉起来（和 OBS 用的是同一个服务）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as r:
            if r.status == 200:
                return True
    except Exception:
        pass

    print(f"overlay server 没在跑，正在启动（端口 {port}）…")
    try:
        from .overlay_server import start_background

        start_background(port)
    except Exception as exc:
        print(f"WARN overlay server 起不来：{exc}")
        return False
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


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description="大肥鱼桌宠：透明窗 + 漫画气泡 + 右键菜单")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--scale", type=float, default=0.0,
                    help="缩放 0.5~1.8（默认用上次拖出来的大小；桌宠 1.0≈560px 宽，面板 1.0=1180px 宽）")
    ap.add_argument("--panel", action="store_true",
                    help="用旧版横向弹幕面板（默认是桌宠：三视图精灵 + 漫画气泡）")
    ap.add_argument("--pos", help="初始位置 x,y")
    ap.add_argument("--click-through", action="store_true", help="启动即点击穿透")
    ap.add_argument("--demo", action="store_true", help="先放一条示例弹幕（不接游戏也能试位置）")
    ap.add_argument("--taskbar", action="store_true", help="在任务栏/Alt-Tab 里显示（OBS 窗口采集找不到时用）")
    ap.add_argument("--mute", action="store_true", help="不出声")
    ap.add_argument("--voice", default="auto", choices=["auto", "mambo", "animalese", "off"],
                    help="配音：auto=跟随 config.json；mambo=曼波音色；animalese=动物语；off=静音")
    ap.add_argument("--vol", type=float, default=0.55, help="音量 0~1.5（默认 0.55，和页面一致）")
    ap.add_argument("--capture", action="store_true",
                    help="兼容旧参数：现在默认就能被录屏拍到（等于不加 --exclude-capture）")
    ap.add_argument("--exclude-capture", action="store_true",
                    help="把桌宠从录屏里隐藏（默认不勾）")
    ap.add_argument("--multi", action="store_true",
                    help="允许多开（默认：发现旧窗口还活着就关掉它，避免屏幕上出现两个弹窗）")
    ap.add_argument("--preview", help="只渲染一张 PNG 就退出（验收/截图用）")
    ap.add_argument("--no-server", action="store_true", help="不自动拉起 overlay 服务")
    ap.add_argument("--think", action="store_true",
                    help="和 --preview 一起用：截「思考ing」那一帧")
    a = ap.parse_args(raw_argv)

    if a.voice == "auto":
        try:
            from ..core.config import LiveConfig

            configured = LiveConfig.load().voice
            a.voice = {"animal": "animalese", "animalese": "animalese", "off": "off"}.get(
                configured, "mambo")
        except Exception:
            a.voice = "mambo"

    # 预览图的缩放：命令行优先，否则跟 DPI；装不下就自动缩（按目标显示器宽度算）
    scale = a.scale or dpi_scale()

    def _target_screen_w() -> int:
        try:
            g = user32.FindWindowW(None, GAME_WINDOW_TITLE) if GAME_WINDOW_TITLE else 0
            if g:
                r = wintypes.RECT()
                user32.GetWindowRect(g, ctypes.byref(r))
                cx = (r.left + r.right) // 2
                for (l, t, rr, b) in StreamerWindow._monitors():
                    if l <= cx <= rr:
                        return rr - l
        except Exception:
            pass
        return user32.GetSystemMetrics(0)

    screen_w = _target_screen_w()
    base_w = PANEL_W if a.panel else PET_BASE_W
    if base_w * scale > screen_w * 0.9:
        scale = screen_w * 0.9 / base_w
        print(f"窗口比屏幕宽，缩到 {scale:.2f}×")

    if a.preview:
        # --think / --click-through 也能进预览：验收截图要看到「思考ing」和气泡
        if a.panel:
            mode = "thinking" if a.think else "speaking"
            text = THINKING_TEXT if a.think else "它 21，我最多挡 15。差 6，嗨呀。"
            img = render(text, {"character": "演示角色", "ascension": 10, "floor": 8,
                                "hp": 23, "max_hp": 80, "gold": 20}, scale,
                         chip=a.click_through,
                         layout_text=None if mode != "thinking" else "它 21，我最多挡 15。差 6，嗨呀。")
        else:
            fake = type("_PreviewPet", (), {})()
            fake.pet_facing = "front"
            fake.pet_face_right = False
            fake.pet_lean = 0.0
            fake.pet_energy = 0.0
            fake.enter_at = time.perf_counter() - 1.0
            fake.bubble_until = time.perf_counter() + 30.0
            fake.talk_start_at = 0.0
            fake.talk_until = 0.0
            text = THINKING_TEXT if a.think else "它 21，我最多挡 15。差 6，嗨呀。"
            img = render_pet(text, "thinking" if a.think else "speaking",
                             scale, time.perf_counter(), fake)
        img.save(a.preview)
        print(f"PREVIEW {a.preview} {img.width}x{img.height}")
        return 0

    # 单实例：屏幕上一个弹窗就够（历史坑：pythonw 起的旧进程没关，再开一个 → 两个弹窗）
    instance_lock.take_over(instance_lock.LOCK_PATH, argv=["main.py"] + raw_argv,
                            multi=a.multi, note=lambda m: print(m, flush=True))

    if not a.no_server:
        ensure_server(a.port)
    pos = None
    if a.pos:
        try:
            x, y = (int(v) for v in a.pos.split(","))
            pos = (x, y)
        except ValueError:
            print("--pos 需要 x,y")
            return 2

    cls = StreamerWindow if a.panel else PetWindow
    win = cls(scale=a.scale, pos=pos, click_through=a.click_through,
              toolwindow=not a.taskbar, volume=a.vol, muted=a.mute,
              exclude_capture=a.exclude_capture, voice=a.voice)
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
        instance_lock.release(instance_lock.LOCK_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
