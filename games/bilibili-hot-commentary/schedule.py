"""虚拟播放时钟与解说调度。

时钟只用 ``time.monotonic`` 推进，和语音状态完全解耦；到点只检查已生成的
cue，不调用 LLM。cue 的 ``schedule_s`` 是模拟调度时间，不是视频真实时间戳。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

from .config import CommentaryConfig
from .models import Cue, DanmakuReply, VideoProfile


class VirtualClock:
    def __init__(self, duration_s: float, speed: float = 1.0, *, now=None) -> None:
        self.duration_s = max(0.1, float(duration_s))
        self.speed = max(0.1, float(speed or 1.0))
        self.position_s = 0.0
        self._now = now or time.monotonic
        self._last = self._now()
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        self._last = self._now()

    def resume(self) -> None:
        self._paused = False
        self._last = self._now()

    def tick(self, now: float | None = None) -> float:
        now = self._now() if now is None else now
        if not self._paused:
            delta = max(0.0, float(now) - self._last)
            self.position_s = min(self.duration_s, self.position_s + delta * self.speed)
        self._last = float(now)
        return self.position_s

    @property
    def done(self) -> bool:
        return self.position_s >= self.duration_s - 1e-6


class CueScheduler:
    def __init__(self, cues: list[Cue]) -> None:
        self.cues = sorted(cues, key=lambda cue: cue.schedule_s)
        self.fired: set[str] = set()

    def due(self, position_s: float) -> list[Cue]:
        return [cue for cue in self.cues
                if cue.schedule_s <= position_s and cue.cue_id not in self.fired]

    def mark_fired(self, cue: Cue) -> None:
        self.fired.add(cue.cue_id)
        cue.fired = True

    def next_due_in(self, position_s: float) -> float | None:
        pending = [cue.schedule_s - position_s for cue in self.cues
                   if cue.cue_id not in self.fired and cue.schedule_s > position_s]
        return min(pending) if pending else None

    @property
    def fired_count(self) -> int:
        return len(self.fired)


@dataclass
class PlaybackPlan:
    bvid: str
    duration_s: int
    speed: float
    cues: list[Cue] = field(default_factory=list)
    replies: list[DanmakuReply] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "bvid": self.bvid,
            "duration_s": self.duration_s,
            "speed": self.speed,
            "cues": [cue.to_dict() for cue in self.cues],
            "replies": [reply.to_dict() for reply in self.replies],
        }


def _greeting_text(profile: VideoProfile, kind: str) -> str:
    title = " ".join(str(profile.title or "").split())[:36]
    if kind == "opening":
        return f"兄弟们，开看《{title}》。" if title else "兄弟们，开刷。"
    return "这条看完了，下一个继续。"


def _looks_like_greeting(text: str, kind: str) -> bool:
    text = str(text or "")
    words = ("开看", "开刷", "来了", "大家好", "兄弟们") if kind == "opening" \
        else ("看完", "结束", "下一个", "再见", "拜拜")
    return any(word in text for word in words)


def _ensure_greetings(
    profile: VideoProfile,
    ordered: list[Cue],
    duration: int,
    min_gap: float,
    limit: int,
) -> list[Cue]:
    """保证开场/收尾各有一条；原 cue 已经是问候就复用，不是就换成问候。"""
    opening = next((cue for cue in ordered if cue.kind == "opening"), None)
    closing = next((cue for cue in ordered if cue.kind == "closing"), None)
    open_at = min(1.5, max(0.2, duration * 0.1))
    close_at = max(open_at + 0.8, duration - 2.0)
    close_at = min(close_at, max(0.8, duration - 0.5))

    def _at(cue: Cue, text: str, at: float, suffix: str) -> Cue:
        if cue is None:
            return Cue(
                cue_id=f"{profile.bvid}:greet-{suffix}", kind=suffix,
                text=text, anchor_bucket=0, offset_s=round(at, 2),
                schedule_s=round(at, 2), evidence=["greeting"],
                confidence=1.0, fired=False,
            )
        keep = str(cue.text or "") if _looks_like_greeting(cue.text, suffix) else text
        return replace(cue, text=keep, offset_s=round(at, 2),
                       schedule_s=round(at, 2), fired=False)

    opening = _at(opening, _greeting_text(profile, "opening"), open_at, "opening")
    closing = _at(closing, _greeting_text(profile, "closing"), close_at, "closing")
    kept: list[Cue] = [opening]
    last = opening.schedule_s
    # 名额先给两条问候，剩下才分给普通 cue。
    for cue in ordered:
        if cue.kind in ("opening", "closing"):
            continue
        if len(kept) >= max(2, limit) - 1:
            break
        wanted = max(0.0, float(cue.schedule_s))
        if wanted < last + min_gap:
            wanted = last + min_gap
        # 收尾前留一点空间，避免最后一个普通 cue 把“看完了”挤掉。
        if wanted > closing.schedule_s - max(1.0, min_gap * 0.5):
            continue
        copied = replace(cue, schedule_s=round(wanted, 2), fired=False)
        kept.append(copied)
        last = wanted
    close_at = max(closing.schedule_s, last + 0.5)
    close_at = min(close_at, max(0.8, duration - 0.5))
    closing = replace(closing, schedule_s=round(close_at, 2),
                      offset_s=round(close_at, 2))
    kept.append(closing)
    return kept


def build_plan(profile: VideoProfile, config: CommentaryConfig,
               *, speed: float | None = None) -> PlaybackPlan:
    duration = max(1, int(profile.duration_s or 0))
    min_gap = max(1.0, float(config.min_gap_s))
    ordered = sorted(profile.cues, key=lambda cue: cue.schedule_s)
    kept = _ensure_greetings(profile, ordered, duration, min_gap, config.max_cues)
    replies = [replace(reply, used=False) for reply in profile.replies[:config.max_replies]]
    return PlaybackPlan(
        bvid=profile.bvid,
        duration_s=duration,
        speed=max(0.25, min(4.0, float(speed if speed is not None else config.speed))),
        cues=kept,
        replies=replies,
    )


class ReplyScheduler:
    """空闲时回答弹幕：每个桶最多一条，且只在桶中后段触发。"""

    def __init__(self, replies: list[DanmakuReply], buckets, *,
                 min_gap_s: float = 60.0) -> None:
        self.replies = list(replies)
        self.buckets = list(buckets or [])
        self.min_gap_s = max(1.0, float(min_gap_s))
        self.used: set[str] = set()
        self.last_at = -1e9

    def due(self, position_s: float, *, spoken_until: float = 0.0) -> DanmakuReply | None:
        if position_s + 0.01 < spoken_until:
            return None
        index = self.current_bucket(position_s)
        if index < 0:
            return None
        if self.last_at >= 0 and position_s - self.last_at < self.min_gap_s:
            return None
        bucket = self.buckets[index]
        if position_s < bucket.start_s + max(10.0, (bucket.end_s - bucket.start_s) * 0.25):
            return None
        for reply in self.replies:
            if reply.used or reply.reply_id in self.used:
                continue
            if reply.bucket_index == index:
                return reply
        return None

    def mark_used(self, reply: DanmakuReply, position_s: float) -> None:
        reply.used = True
        self.used.add(reply.reply_id)
        self.last_at = float(position_s)

    def current_bucket(self, position_s: float) -> int:
        for index, bucket in enumerate(self.buckets):
            if bucket.start_s <= position_s < bucket.end_s:
                return index
        return len(self.buckets) - 1 if self.buckets else -1


__all__ = ["CueScheduler", "PlaybackPlan", "ReplyScheduler", "VirtualClock",
           "build_plan"]
