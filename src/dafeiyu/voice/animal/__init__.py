"""动物语（Animalese）：每个字一个短音节，五声音阶随机上下行。

规则和 `ui/overlay/index.html` 里 JS 版一致（页面直接 WebAudio 合成，不需要音频素材）。
这里用纯 Python 生成 WAV：桌面弹幕窗、控制台试听、离线验收都走它。
"""

from __future__ import annotations

import io
import json
import math
import random
import re
import struct
import wave
from pathlib import Path

from ..base import VoiceResult, wav_duration

SPOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
PUNCT_RE = re.compile(r"[，,、。；;：:！!？?…—]")

DEFAULTS = {
    "rate": 44100,
    "base_freq": 520.0,
    "scale": [0, 2, 4, 7, 9, 12],
    "max_spoken": 70,
    "volume": 0.55,
    "sub_gain": 0.35,
    "octave_jump": 0.12,
}


def _load_config(path: Path | None) -> dict:
    cfg = dict(DEFAULTS)
    if path and path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
    return cfg


def _blip(freq: float, dur: float, rate: int, sub_gain: float = 0.35) -> list[float]:
    """一个音节：三角波 + 低八度方波 → 低通 → 6ms 起音 / 指数衰减。"""
    n = max(1, int(rate * dur))
    out: list[float] = []
    lp = 0.0
    alpha = 1.0 - math.exp(-2.0 * math.pi * min(3200.0, freq * 3.2) / rate)
    phase = 0.0
    phase_sub = 0.0
    for i in range(n):
        t = i / rate
        f = freq * (1.0 - 0.06 * (t / max(dur, 1e-6)))
        phase = (phase + f / rate) % 1.0
        phase_sub = (phase_sub + (f * 0.5) / rate) % 1.0
        tri = 4.0 * abs(phase - 0.5) - 1.0
        sq = 1.0 if phase_sub < 0.5 else -1.0
        x = tri + sub_gain * sq
        lp += alpha * (x - lp)
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


def synth_wav(
    text: str,
    volume: float = 0.55,
    *,
    rate: int = 44100,
    base_freq: float = 520.0,
    scale: tuple[int, ...] = (0, 2, 4, 7, 9, 12),
    max_spoken: int = 70,
    rng: random.Random | None = None,
) -> bytes | None:
    """把一句话合成成 WAV；只有标点/空白返回 None。"""
    chars = list(text or "")
    total = len(SPOKEN_RE.findall(text or ""))
    if not total:
        return None
    rng = rng or random.Random()
    spoken_max = min(max_spoken, total)
    step = max(0.03, min(0.06, 2.3 / max(6, spoken_max)))
    dur = min(0.06, step * 0.92)

    buf: list[float] = [0.0] * int(rate * 0.05)
    degree = 2 + int(rng.random() * 2)
    spoken = 0
    for ch in chars:
        if ch.isspace():
            buf.extend([0.0] * int(rate * step * 0.55))
            continue
        if PUNCT_RE.match(ch):
            buf.extend([0.0] * int(rate * step * 1.5))
            continue
        if not SPOKEN_RE.match(ch):
            continue
        if spoken >= spoken_max:
            break
        spoken += 1
        degree = max(0, min(len(scale) - 1, degree + (1 if rng.random() < 0.5 else -1)))
        semis = scale[degree] + (12 if rng.random() < 0.12 else 0)
        freq = base_freq * (2.0 ** (semis / 12.0)) * (0.985 + rng.random() * 0.03)
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


class AnimalVoice:
    """动物语语音：不依赖外部引擎，永远可用（作为曼波掉线时的回退）。"""

    name = "animal"

    def __init__(self, config_path: str | Path | None = None, volume: float | None = None,
                 seed: int | None = None) -> None:
        self.cfg = _load_config(Path(config_path) if config_path else None)
        self.volume = float(volume if volume is not None else self.cfg["volume"])
        self._rng = random.Random(seed)
        self._count = 0

    def available(self) -> bool:
        return True

    def synth(self, text: str, volume: float | None = None) -> VoiceResult:
        text = (text or "").strip()
        if not text:
            return VoiceResult(voice=self.name, error="空台词")
        vol = self.volume if volume is None else volume
        wav = synth_wav(
            text, vol,
            rate=int(self.cfg["rate"]),
            base_freq=float(self.cfg["base_freq"]),
            scale=tuple(self.cfg["scale"]),
            max_spoken=int(self.cfg["max_spoken"]),
            rng=self._rng,
        )
        if not wav:
            return VoiceResult(voice=self.name, error="没有可读字符")
        self._count += 1
        return VoiceResult(wav=wav, voice=self.name, duration=wav_duration(wav))

    def status(self) -> dict:
        return {
            "name": self.name,
            "available": True,
            "engine": "内置五声音阶合成",
            "rate": self.cfg["rate"],
            "scale": self.cfg["scale"],
            "volume": self.volume,
            "synth_count": self._count,
        }


__all__ = ["AnimalVoice", "synth_wav"]
