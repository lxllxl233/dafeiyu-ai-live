"""语音路由：首选 + 回退，保证「直播间永远有声音」。

    router = VoiceRouter({"mambo": MamboVoice(), "animal": AnimalVoice()},
                         primary="mambo", fallback="animal")
    result = router.speak("它 21，我最多挡 15。")

曼波引擎没起 / 合成超时 / 返回坏 WAV → 自动回退动物语，并在 status() 里记一笔。
"""

from __future__ import annotations

import threading
import time
from typing import Iterable

from .base import Voice, VoiceResult


class VoiceRouter:
    def __init__(
        self,
        voices: dict[str, Voice],
        primary: str = "mambo",
        fallback: str = "animal",
        volume: float = 0.55,
    ) -> None:
        self.voices = dict(voices)
        self.primary = primary
        self.fallback = fallback
        self.volume = volume
        self._lock = threading.Lock()
        self._counts = {"attempts": 0, "ok": 0, "fallbacks": 0, "failed": 0}
        self._last: dict = {}

    def _order(self, voice: str | None) -> Iterable[str]:
        first = (voice or self.primary or "").lower()
        order: list[str] = []
        for name in (first, self.primary, self.fallback):
            if name and name not in order:
                order.append(name)
        return order

    def speak(self, text: str, voice: str | None = None, volume: float | None = None) -> VoiceResult:
        if (voice or self.primary) in ("off", "none", "silent"):
            return VoiceResult(voice="off", error="语音已关闭")
        vol = self.volume if volume is None else volume
        errors: list[str] = []
        for name in self._order(voice):
            impl = self.voices.get(name)
            if impl is None:
                errors.append(f"{name}: 未安装")
                continue
            if not impl.available():
                errors.append(f"{name}: 离线")
                continue
            result = impl.synth(text, vol)
            if result.ok:
                with self._lock:
                    self._counts["attempts"] += 1
                    self._counts["ok"] += 1
                    if name != (voice or self.primary):
                        self._counts["fallbacks"] += 1
                    self._last = {"voice": name, "duration": round(result.duration, 3),
                                  "cached": result.cached, "at": time.time()}
                return result
            errors.append(f"{name}: {result.error or '合成失败'}")
        with self._lock:
            self._counts["attempts"] += 1
            self._counts["failed"] += 1
            self._last = {"voice": "", "error": "；".join(errors), "at": time.time()}
        return VoiceResult(voice="", error="；".join(errors) or "没有可用语音")

    def status(self) -> dict:
        voices = {}
        for name, impl in self.voices.items():
            try:
                voices[name] = impl.status()
            except Exception as exc:  # 状态查询不能弄崩直播
                voices[name] = {"name": name, "available": False, "error": str(exc)}
        with self._lock:
            counts = dict(self._counts)
            last = dict(self._last)
        return {
            "primary": self.primary,
            "fallback": self.fallback,
            "volume": self.volume,
            "active": self.primary if voices.get(self.primary, {}).get("available") else self.fallback,
            "voices": voices,
            "counts": counts,
            "last": last,
        }


__all__ = ["VoiceRouter"]
