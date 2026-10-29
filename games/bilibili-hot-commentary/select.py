"""热门候选的过滤与评分。

播放量只作为门槛/弱信号，不作为“适合解说”的等价物。评论数量、互动率、
时长适配和简介信息量优先；已观看和失败冷却在更前面拦掉。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import CommentaryConfig
from .models import VideoRef


@dataclass
class Candidate:
    video: VideoRef
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class SelectionResult:
    candidates: list[Candidate] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _score(video: VideoRef, config: CommentaryConfig, now: float) -> tuple[float, list[str]]:
    stat = video.stat if isinstance(video.stat, dict) else {}
    view = max(1, _int(stat.get("view")))
    like = max(0, _int(stat.get("like")))
    coin = max(0, _int(stat.get("coin")))
    favorite = max(0, _int(stat.get("favorite")))
    reply = max(0, _int(stat.get("reply")))

    comment_signal = min(1.0, reply / max(1, config.target_comments))
    engagement = min(1.0, (like + 2 * coin + 2 * favorite + reply) / view * 20.0)
    target = 600
    duration_fit = max(0.0, 1.0 - abs(video.duration_s - target) / max(target, video.duration_s))
    desc_fit = min(1.0, len(video.desc) / 300.0)
    age_days = max(0.0, (now - video.pubdate) / 86400.0) if video.pubdate else 0.0
    freshness = max(0.0, min(1.0, 1.0 - age_days / 30.0))
    score = (0.35 * comment_signal + 0.25 * engagement + 0.20 * duration_fit
             + 0.15 * desc_fit + 0.05 * freshness)
    return round(score, 6), [
        f"评论信号={comment_signal:.2f}", f"互动={engagement:.2f}",
        f"时长适配={duration_fit:.2f}", f"简介={desc_fit:.2f}",
    ]


def select_candidates(
    videos: list[VideoRef],
    config: CommentaryConfig,
    seen: SeenStoreLike | None = None,
    failed: FailedStoreLike | None = None,
    *,
    now: float | None = None,
) -> SelectionResult:
    now = time.time() if now is None else now
    result = SelectionResult()
    seen_in_batch: set[str] = set()
    for video in videos:
        if video.bvid in seen_in_batch:
            result.rejected.append({"bvid": video.bvid, "reason": "本次列表重复"})
            continue
        seen_in_batch.add(video.bvid)
        reason = _reject_reason(video, config, seen, failed, now)
        if reason:
            result.rejected.append({"bvid": video.bvid, "reason": reason})
            continue
        score, reasons = _score(video, config, now)
        result.candidates.append(Candidate(video=video, score=score, reasons=reasons))
    result.candidates.sort(key=lambda item: (-item.score, item.video.bvid))
    return result


def _reject_reason(
    video: VideoRef,
    config: CommentaryConfig,
    seen: SeenStoreLike | None,
    failed: FailedStoreLike | None,
    now: float,
) -> str:
    if not video.bvid or not video.aid:
        return "缺少 bvid/aid"
    if not video.title or not video.owner_name:
        return "标题/UP 主不完整"
    if video.duration_s < config.min_duration_s:
        return f"时长不足 {config.min_duration_s}s"
    if video.duration_s > config.max_duration_s:
        return f"时长超过 {config.max_duration_s}s"
    if video.videos > 1 and not config.allow_multi_part:
        return "多 P 视频（默认跳过）"
    stat = video.stat if isinstance(video.stat, dict) else {}
    reply = max(0, _int(stat.get("reply")))
    if reply < config.min_comments:
        return f"评论数不足（{reply} < {config.min_comments}）"
    view = max(0, _int(stat.get("view")))
    like = max(0, _int(stat.get("like")))
    if view > 0 and like / view < config.min_like_view_ratio:
        return f"互动率过低（{like / view:.3f} < {config.min_like_view_ratio:.3f}）"
    text = f"{video.title} {video.desc}".lower()
    for word in config.exclude_keywords:
        if word and word.lower() in text:
            return f"命中排除词：{word}"
    if seen is not None and seen.is_seen(video.bvid, config.seen_ttl_days, now=now):
        return "已观看（去重）"
    if failed is not None and failed.is_failed(video.bvid, now=now):
        return "失败冷却中"
    return ""


class SeenStoreLike:
    def is_seen(self, bvid: str, ttl_days: int, *, now: float | None = None) -> bool: ...


class FailedStoreLike:
    def is_failed(self, bvid: str, *, now: float | None = None) -> bool: ...


__all__ = ["Candidate", "SelectionResult", "select_candidates"]
