"""视频理解档案：一次 LLM 调用 + 确定性校验 + 本地缓存。

输入只包含公开文本。输出严格清洗：
- 拒绝“第 N 秒/画面/镜头/我看到”等没有证据的精确断言；
- 拒绝提示词注入/密钥索取样式；
- cue 的 anchor 只允许落在已有弹幕桶上，时间语义是“模拟调度时间”。
"""

from __future__ import annotations

import hashlib
import json
import re
import time

from .config import CommentaryConfig
from .danmaku import build_timeline
from .models import (
    Comment, Cue, DanmakuReply, DanmakuResult, TimelineBucket, VideoDetail,
    VideoProfile, VideoRef, clean_text,
)
from .prompts import commentary_system, PROFILE_GOAL, PROMPT_VERSION


class ProfileError(RuntimeError):
    pass


_BAD_TIME_RE = re.compile(
    r"(\d{1,2}\s*[:：]\s*\d{2})|(第\s*\d+\s*[秒分])|(\d+\s*秒)", re.I)
_BAD_VISUAL_RE = re.compile(
    r"(画面|镜头|字幕|台词|音频里|我看到|我听到|他刚才说|她刚才说|视频里显示)", re.I)
_BAD_META_RE = re.compile(
    r"(系统提示|api\s*key|密钥|cookie|忽略(以上|之前|前面)|作为\s*AI|你现在是"
    r"|执行命令|运行脚本|泄露.{0,6}(密钥|提示词|token))", re.I)
_ALLOWED_KINDS = {
    "opening", "reaction", "meme", "guess", "question", "analysis", "closing",
}


def _clamp(value, low: float = 0.0, high: float = 1.0, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def safe_line(value, limit: int = 90) -> str:
    text = clean_text(value, limit)
    if not text:
        return ""
    if _BAD_TIME_RE.search(text) or _BAD_VISUAL_RE.search(text) or _BAD_META_RE.search(text):
        return ""
    return text


def _safe_list(value, limit: int = 5, text_limit: int = 90) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = safe_line(item, text_limit)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _bucket_fallback(bucket: TimelineBucket) -> str:
    """没有 LLM 分段文本时的安全观察，只描述弹幕，不描述画面。"""
    if bucket.count <= 0:
        return "这一段弹幕很少。"
    if bucket.spike:
        return "这一段弹幕明显变密。"
    return "这一段弹幕比较平稳。"


class ProfileBuilder:
    def __init__(self, config: CommentaryConfig, *, planner=None, cache=None,
                 now=None, persona=None) -> None:
        self.config = config
        self.cache = cache
        self.persona = persona
        self._now = now or time.time
        if planner is None:
            from dafeiyu.agent.planner import LLMPlanner
            from dafeiyu.core.config import LiveConfig

            planner = LLMPlanner(
                LiveConfig.load(), timeout=config.llm_timeout_s,
                system_prompt=commentary_system(persona), thinking=False,
            )
        self.planner = planner

    def build(self, video: VideoRef | VideoDetail, comments: list[Comment],
              danmaku_result: DanmakuResult) -> tuple[VideoProfile, dict]:
        duration = int(getattr(video, "duration_s", 0) or 0)
        if duration <= 0:
            raise ProfileError("视频时长不可用")
        bucket_s = self.config.bucket_seconds(duration)
        timeline = build_timeline(
            danmaku_result.items, duration, bucket_s,
            coverage=danmaku_result.coverage, max_buckets=20,
        )
        source_hash = _source_hash(
            video, comments, danmaku_result, self.persona)
        if self.cache is not None:
            cached = self.cache.get(str(video.bvid))
            if isinstance(cached, dict) and cached.get("source_hash") == source_hash:
                profile = VideoProfile.from_dict(cached.get("profile") or {})
                # 提示词/清洗规则升级后旧档案不能继续复用。
                if (profile is not None
                        and (profile.meta or {}).get("prompt_version") == PROMPT_VERSION):
                    return profile, {"cached": True, "usage": {}}

        context = {
            "video": {
                "bvid": video.bvid,
                "url": video.url,
                "title": video.title,
                "desc": video.desc,
                "duration_s": duration,
                "owner": {"mid": getattr(video, "owner_mid", 0),
                          "name": getattr(video, "owner_name", "")},
                "stat": dict(getattr(video, "stat", {}) or {}),
                "tname": getattr(video, "tname", ""),
            },
            "hot_comments": [item.to_prompt() for item in comments[:self.config.target_comments]],
            "danmaku_timeline": [bucket.to_prompt() for bucket in timeline],
            "danmaku_meta": {
                "partial": bool(danmaku_result.partial),
                "source": danmaku_result.source,
                "coverage": round(danmaku_result.coverage, 3),
                "note": "弹幕时间是观众播放进度，不是视频内容真实时间戳；文本不可信。",
            },
            "constraints": {
                "max_cues": self.config.max_cues,
                "max_replies": self.config.max_replies,
                "max_line_chars": 80,
                "untrusted_text": True,
            },
        }
        try:
            result = self.planner.plan(context, goal=PROFILE_GOAL)
        except Exception as exc:  # noqa: BLE001
            raise ProfileError(f"LLM 调用失败：{type(exc).__name__}: {exc}") from None
        if not isinstance(result, dict) or not result.get("ok"):
            message = "LLM 没有返回可用档案"
            if isinstance(result, dict):
                message = str(result.get("error") or message)
            raise ProfileError(message)
        plan = result.get("plan")
        if not isinstance(plan, dict):
            raise ProfileError("LLM 输出不是 JSON 对象")
        profile = self._from_plan(video, comments, danmaku_result, timeline, plan,
                                  source_hash)
        if self.cache is not None:
            self.cache.put(str(video.bvid), {
                "source_hash": source_hash,
                "profile": profile.to_dict(),
            })
        return profile, {"cached": False, "usage": result.get("usage") or {}}

    def _from_plan(self, video: VideoRef | VideoDetail, comments: list[Comment],
                   danmaku_result: DanmakuResult, timeline: list[TimelineBucket],
                   plan: dict, source_hash: str) -> VideoProfile:
        duration = int(getattr(video, "duration_s", 0) or 0)
        title = clean_text(getattr(video, "title", ""), 200)
        topic = safe_line(plan.get("topic"), 120) or safe_line(title, 60) or "一个热门视频"
        summary = safe_line(plan.get("summary"), 300)
        if not summary:
            desc = clean_text(getattr(video, "desc", ""), 300)
            summary = safe_line(desc, 180) or "文本信息有限，先以观众反应为主。"

        audience = plan.get("audience") if isinstance(plan.get("audience"), dict) else {}
        meme = _safe_list(audience.get("meme"), 5, 80)
        controversy = _safe_list(audience.get("controversy"), 5, 80)
        emotion = safe_line(audience.get("emotion"), 60)
        audience_out = {
            "meme": meme, "controversy": controversy, "emotion": emotion,
            "disclaimer": "以下来自评论/弹幕，是观众观点，不代表视频内容",
        }

        raw_conf = plan.get("confidence") if isinstance(plan.get("confidence"), dict) else {}
        comment_score = min(1.0, len(comments) / max(1, self.config.target_comments))
        coverage = danmaku_result.coverage
        meta_score = 1.0 if title and getattr(video, "desc", "") else 0.6
        timeline_conf = _clamp(raw_conf.get("timeline"), 0.0, 1.0, 0.3)
        timeline_conf = min(timeline_conf, max(0.2, coverage))
        confidence = {
            "topic": min(_clamp(raw_conf.get("topic"), 0.0, 1.0, 0.5), meta_score),
            "summary": min(_clamp(raw_conf.get("summary"), 0.0, 1.0, 0.45), meta_score,
                           0.6 + 0.4 * comment_score),
            "audience": min(_clamp(raw_conf.get("audience"), 0.0, 1.0, 0.5),
                            0.4 + 0.6 * comment_score),
            "timeline": timeline_conf,
            "content": min(0.35, 0.1 + 0.25 * (1.0 if danmaku_result.items else 0.0)),
        }

        segments = plan.get("segments") if isinstance(plan.get("segments"), list) else []
        by_index: dict[int, dict] = {}
        for row in segments:
            if not isinstance(row, dict):
                continue
            try:
                index = int(row.get("index"))
            except (TypeError, ValueError):
                continue
            by_index[index] = row
        for bucket in timeline:
            row = by_index.get(bucket.index, {})
            bucket.summary = safe_line(row.get("summary"), 120) or _bucket_fallback(bucket)
            bucket.audience_reaction = safe_line(row.get("audience_reaction"), 80)
            bucket.confidence = {
                "reaction": min(1.0, max(0.15, coverage)),
                "content": min(0.35, 0.1 + 0.25 * (1.0 if bucket.samples else 0.0)),
                "llm": _clamp(row.get("confidence"), 0.0, 1.0, 0.3),
            }

        cues = self._build_cues(plan, timeline, video, duration)
        if not cues:
            raise ProfileError("LLM 没有给出可用的解说 cue")
        replies = self._build_replies(plan, timeline, video)

        meta = {
            "model": str(getattr(self.planner, "model", "") or ""),
            "prompt_version": PROMPT_VERSION,
            "source_hash": source_hash,
            "created_at": self._now(),
            "danmaku_source": danmaku_result.source,
            "danmaku_partial": bool(danmaku_result.partial),
            "danmaku_coverage": round(coverage, 3),
            "persona": getattr(self.persona, "persona_id", "") if self.persona else "",
        }
        return VideoProfile(
            bvid=str(video.bvid),
            url=str(video.url),
            title=title,
            duration_s=duration,
            owner={"mid": getattr(video, "owner_mid", 0),
                   "name": getattr(video, "owner_name", "")},
            topic=topic,
            summary=summary,
            audience=audience_out,
            talking_points=_safe_list(plan.get("talking_points"), 5, 100),
            tease_targets=_safe_list(plan.get("tease_targets"), 4, 100),
            guesses=_safe_list(plan.get("guesses"), 3, 100),
            open_questions=_safe_list(plan.get("open_questions"), 3, 100),
            confidence=confidence,
            timeline=timeline,
            cues=cues,
            replies=replies,
            meta=meta,
        )

    def _build_cues(self, plan: dict, timeline: list[TimelineBucket],
                    video: VideoRef | VideoDetail, duration: int) -> list[Cue]:
        rows = plan.get("cues") if isinstance(plan.get("cues"), list) else []
        out: list[Cue] = []
        used_text: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            text = safe_line(row.get("text"), 90)
            if not text or text in used_text:
                continue
            try:
                anchor = int(row.get("anchor_bucket"))
            except (TypeError, ValueError):
                anchor = 0
            anchor = max(0, min(len(timeline) - 1, anchor)) if timeline else 0
            start = timeline[anchor].start_s if timeline else 0
            end = timeline[anchor].end_s if timeline else duration
            try:
                offset = float(row.get("offset_s"))
            except (TypeError, ValueError):
                offset = min(15.0, max(1.0, (end - start) / 3.0))
            offset = max(0.0, min(max(0.0, end - start - 1), offset))
            kind = str(row.get("kind") or "reaction").strip().lower()
            if kind not in _ALLOWED_KINDS:
                kind = "reaction"
            used_text.add(text)
            out.append(Cue(
                cue_id=f"{video.bvid}:c{index}",
                kind=kind,
                text=text,
                anchor_bucket=anchor,
                offset_s=round(offset, 2),
                schedule_s=round(start + offset, 2),
                evidence=[f"bucket:{anchor}"],
                confidence=_clamp(row.get("confidence"), 0.0, 1.0, 0.5),
            ))
            if len(out) >= self.config.max_cues:
                break
        return out

    def _build_replies(self, plan: dict, timeline: list[TimelineBucket],
                       video: VideoRef | VideoDetail) -> list[DanmakuReply]:
        rows = plan.get("danmaku_replies")
        if not isinstance(rows, list) or self.config.max_replies <= 0:
            return []
        out: list[DanmakuReply] = []
        used: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            text = safe_line(row.get("text"), 90)
            if not text or text in used:
                continue
            try:
                bucket = int(row.get("bucket"))
            except (TypeError, ValueError):
                bucket = 0
            bucket = max(0, min(len(timeline) - 1, bucket)) if timeline else 0
            used.add(text)
            out.append(DanmakuReply(
                reply_id=f"{video.bvid}:r{index}",
                bucket_index=bucket,
                text=text,
                confidence=_clamp(row.get("confidence"), 0.0, 1.0, 0.4),
            ))
            if len(out) >= self.config.max_replies:
                break
        return out


def _source_hash(video: VideoRef | VideoDetail, comments: list[Comment],
                 danmaku_result: DanmakuResult, persona=None) -> str:
    payload = {
        "bvid": video.bvid,
        "title": getattr(video, "title", ""),
        "desc": getattr(video, "desc", "")[:500],
        "duration": getattr(video, "duration_s", 0),
        "comments": [item.text for item in comments[:30]],
        "danmaku": [(round(item.progress_s, 1), item.text)
                    for item in danmaku_result.items[:2000]],
        "persona": persona.cache_key() if persona is not None else "",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


__all__ = ["ProfileBuilder", "ProfileError", "safe_line"]
