"""语音层公共件：WAV 时长/增益/磁盘缓存，以及语音接口的形状。"""

from __future__ import annotations

import hashlib
import io
import os
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class VoiceResult:
    wav: bytes | None = None
    voice: str = ""
    cached: bool = False
    error: str = ""
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.wav)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "voice": self.voice,
            "cached": self.cached,
            "bytes": len(self.wav or b""),
            "duration": round(self.duration, 3),
            "error": self.error,
        }


@runtime_checkable
class Voice(Protocol):
    name: str

    def available(self) -> bool: ...
    def synth(self, text: str, volume: float | None = None) -> VoiceResult: ...
    def status(self) -> dict: ...


def is_wav(data: bytes | None) -> bool:
    return bool(data) and len(data) >= 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def wav_duration(data: bytes | None) -> float:
    if not is_wav(data):
        return 0.0
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / float(rate) if rate > 0 else 0.0
    except (wave.Error, OSError, EOFError):
        return 0.0


def wav_gain(data: bytes, volume: float) -> bytes:
    """把 16bit PCM 样本按 volume 缩放；非标准 PCM 原样返回。"""
    if not is_wav(data):
        return data
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getsampwidth() != 2:
                return data
            params = w.getparams()
            frames = w.readframes(w.getnframes())
    except (wave.Error, OSError, EOFError):
        return data
    k = min(1.5, max(0.0, volume))
    if abs(k - 1.0) < 0.01:
        return data
    out = bytearray(bytes(frames))
    for i in range(0, len(out) - 1, 2):
        s = int.from_bytes(out[i:i + 2], "little", signed=True)
        s = max(-32768, min(32767, int(s * k)))
        out[i:i + 2] = s.to_bytes(2, "little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams(params)
        w.writeframes(bytes(out))
    return buf.getvalue()


def cache_key(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:20]


def cache_path(cache_dir: str | os.PathLike[str], text: str) -> Path:
    return Path(cache_dir) / (cache_key(text) + ".wav")


def read_cache(cache_dir: str | os.PathLike[str], text: str) -> bytes | None:
    path = cache_path(cache_dir, text)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data if is_wav(data) else None


def write_cache(cache_dir: str | os.PathLike[str], text: str, data: bytes) -> None:
    if not is_wav(data):
        return
    try:
        path = cache_path(cache_dir, text)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".wav.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        pass


__all__ = [
    "Voice", "VoiceResult", "is_wav", "wav_duration", "wav_gain",
    "cache_key", "cache_path", "read_cache", "write_cache",
]
