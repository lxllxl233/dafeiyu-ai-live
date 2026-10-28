"""节奏：让「大肥鱼」话多但不刷屏。

三件事：
1. 两条台词之间至少隔 `min_gap` 秒（默认 1.2s，给页面排队留气口）；
2. 招牌稿冷却（同一句 10 分钟内最多 2 次）；
3. 重复检测：最近 10 条说过同样的话就提示换一句。
"""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass, field

from .card import Style


@dataclass
class Pacing:
    style: Style = field(default_factory=Style)
    _last_sent: float = 0.0
    _sent_times: deque = field(default_factory=lambda: deque(maxlen=64))
    _recent: deque = field(default_factory=lambda: deque(maxlen=10))
    _quip_used: dict = field(default_factory=dict)
    _repeat_hits: int = 0

    # ---------------------------------------------------------------- 判断
    def gap_remaining(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        return max(0.0, self._last_sent + self.style.min_gap - now)

    def advice(self, text: str, quips: tuple[str, ...] = (), now: float | None = None) -> dict:
        now = time.time() if now is None else now
        window = [t for t in self._sent_times if now - t <= 600]
        repeat = (text or "").strip() in self._recent
        hits = [q for q in quips if q and q in (text or "")]
        return {
            "wait": round(self.gap_remaining(now), 2),
            "sent_last_10min": len(window),
            "repeat": repeat,
            "recent": list(self._recent),
            "quips": {q: self.quip_allowed(q, now) for q in hits},
        }

    def quip_allowed(self, quip: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        window = [t for t in self._sent_times if now - t <= 600]
        if len(window) >= self.style.max_quips_per_10min:
            return False
        return (now - self._quip_used.get(quip, -1e9)) >= self.style.quip_cooldown

    # ---------------------------------------------------------------- 记账
    def note_sent(self, text: str, quips: tuple[str, ...] = (), now: float | None = None) -> dict:
        now = time.time() if now is None else now
        t = (text or "").strip()
        self._last_sent = now
        self._sent_times.append(now)
        if t in self._recent:
            self._repeat_hits += 1
        self._recent.append(t)
        for quip in quips:
            if quip and quip in t:
                self._quip_used[quip] = now
        return {"wait": 0.0, "repeat": self._repeat_hits, "at": now}

    def stats(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        return {
            "min_gap": self.style.min_gap,
            "sent": len(self._sent_times),
            "last_10min": len([t for t in self._sent_times if now - t <= 600]),
            "repeat_hits": self._repeat_hits,
            "quips_used": len(self._quip_used),
        }


__all__ = ["Pacing"]
