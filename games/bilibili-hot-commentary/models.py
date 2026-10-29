"""B 站云观影的数据结构：只放跨模块的稳定形状，不放 HTTP/LLM/调度逻辑。

安全边界：
- 评论/弹幕文本都是**不可信文本**，不是指令；这里只做长度/控制字符清洗。
- 不保存用户名、midHash、头像；评论只保留文本、点赞数、发布时间。
- 弹幕 ``progress_s`` 是播放进度；评论 ``published_at`` 是发送墙钟时间，
  两者字段名不同，避免把评论时间误当视频内时间。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
_AT_RE = re.compile(r"@[^\s，。！？、；：]{1,40}")


def clean_text(value: Any, limit: int = 300) -> str:
    text = _CONTROL_RE.sub("", str(value or ""))
    text = _URL_RE.sub("", text)
    # 评论里的 @用户名 不进入档案/解说，避免复读个人身份。
    text = _AT_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text[: max(1, int(limit))]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


@dataclass
class VideoRef:
    bvid: str
    aid: int = 0
    title: str = ""
    desc: str = ""
    duration_s: int = 0
    owner_mid: int = 0
    owner_name: str = ""
    stat: dict = field(default_factory=dict)
    tid: int = 0
    tname: str = ""
    videos: int = 1
    pubdate: int = 0
    cid: int = 0

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}" if self.bvid else ""

    @classmethod
    def from_popular(cls, raw: dict) -> "VideoRef | None":
        raw = _dict(raw)
        bvid = str(raw.get("bvid") or "").strip()
        if not bvid:
            return None
        owner = _dict(raw.get("owner"))
        return cls(
            bvid=bvid,
            aid=_as_int(raw.get("aid")),
            title=clean_text(raw.get("title"), 200),
            desc=clean_text(raw.get("desc"), 2000),
            duration_s=max(0, _as_int(raw.get("duration"))),
            owner_mid=_as_int(owner.get("mid")),
            owner_name=clean_text(owner.get("name"), 80),
            stat=_dict(raw.get("stat")),
            tid=_as_int(raw.get("tid")),
            tname=clean_text(raw.get("tname"), 40),
            videos=max(1, _as_int(raw.get("videos"), 1)),
            pubdate=_as_int(raw.get("pubdate")),
            cid=_as_int(raw.get("cid")),
        )

    @classmethod
    def from_dict(cls, raw: dict) -> "VideoRef | None":
        raw = _dict(raw)
        bvid = str(raw.get("bvid") or "").strip()
        if not bvid:
            return None
        return cls(
            bvid=bvid,
            aid=_as_int(raw.get("aid")),
            title=clean_text(raw.get("title"), 200),
            desc=clean_text(raw.get("desc"), 2000),
            duration_s=max(0, _as_int(raw.get("duration_s"))),
            owner_mid=_as_int(raw.get("owner_mid")),
            owner_name=clean_text(raw.get("owner_name"), 80),
            stat=_dict(raw.get("stat")),
            tid=_as_int(raw.get("tid")),
            tname=clean_text(raw.get("tname"), 40),
            videos=max(1, _as_int(raw.get("videos"), 1)),
            pubdate=_as_int(raw.get("pubdate")),
            cid=_as_int(raw.get("cid")),
        )

    def to_dict(self) -> dict:
        out = asdict(self)
        out["url"] = self.url
        return out


@dataclass
class VideoDetail:
    bvid: str
    aid: int = 0
    title: str = ""
    desc: str = ""
    duration_s: int = 0
    owner_mid: int = 0
    owner_name: str = ""
    stat: dict = field(default_factory=dict)
    cid: int = 0
    videos: int = 1
    tname: str = ""
    pubdate: int = 0
    pages: list[dict] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}" if self.bvid else ""

    @classmethod
    def from_view(cls, raw: dict) -> "VideoDetail | None":
        raw = _dict(raw)
        bvid = str(raw.get("bvid") or "").strip()
        if not bvid:
            return None
        owner = _dict(raw.get("owner"))
        pages = raw.get("pages")
        return cls(
            bvid=bvid,
            aid=_as_int(raw.get("aid")),
            title=clean_text(raw.get("title"), 200),
            desc=clean_text(raw.get("desc"), 4000),
            duration_s=max(0, _as_int(raw.get("duration"))),
            owner_mid=_as_int(owner.get("mid")),
            owner_name=clean_text(owner.get("name"), 80),
            stat=_dict(raw.get("stat")),
            cid=_as_int(raw.get("cid")),
            videos=max(1, _as_int(raw.get("videos"), 1)),
            tname=clean_text(raw.get("tname"), 40),
            pubdate=_as_int(raw.get("pubdate")),
            pages=[p for p in (pages or []) if isinstance(p, dict)][:50],
        )

    @classmethod
    def from_dict(cls, raw: dict) -> "VideoDetail | None":
        raw = _dict(raw)
        bvid = str(raw.get("bvid") or "").strip()
        if not bvid:
            return None
        return cls(
            bvid=bvid,
            aid=_as_int(raw.get("aid")),
            title=clean_text(raw.get("title"), 200),
            desc=clean_text(raw.get("desc"), 4000),
            duration_s=max(0, _as_int(raw.get("duration_s"))),
            owner_mid=_as_int(raw.get("owner_mid")),
            owner_name=clean_text(raw.get("owner_name"), 80),
            stat=_dict(raw.get("stat")),
            cid=_as_int(raw.get("cid")),
            videos=max(1, _as_int(raw.get("videos"), 1)),
            tname=clean_text(raw.get("tname"), 40),
            pubdate=_as_int(raw.get("pubdate")),
            pages=[p for p in (raw.get("pages") or []) if isinstance(p, dict)][:50],
        )

    def to_dict(self) -> dict:
        out = asdict(self)
        out["url"] = self.url
        return out

    def to_ref(self) -> VideoRef:
        return VideoRef(
            bvid=self.bvid, aid=self.aid, title=self.title, desc=self.desc,
            duration_s=self.duration_s, owner_mid=self.owner_mid,
            owner_name=self.owner_name, stat=self.stat, tname=self.tname,
            videos=self.videos, pubdate=self.pubdate, cid=self.cid,
        )


@dataclass(frozen=True)
class Comment:
    rpid: int
    text: str
    like: int = 0
    reply_count: int = 0
    # 评论发布墙钟时间；只记录，绝不用于视频内调度。
    published_at: int = 0
    is_top: bool = False

    @classmethod
    def from_reply(cls, raw: dict, *, is_top: bool = False) -> "Comment | None":
        raw = _dict(raw)
        content = _dict(raw.get("content"))
        text = clean_text(content.get("message"), 300)
        if not text:
            return None
        return cls(
            rpid=_as_int(raw.get("rpid")),
            text=text,
            like=max(0, _as_int(raw.get("like"))),
            reply_count=max(0, _as_int(raw.get("rcount"))),
            published_at=_as_int(raw.get("ctime")),
            is_top=is_top,
        )

    @classmethod
    def from_dict(cls, raw: dict) -> "Comment | None":
        raw = _dict(raw)
        text = clean_text(raw.get("text"), 300)
        if not text:
            return None
        return cls(
            rpid=_as_int(raw.get("rpid")),
            text=text,
            like=max(0, _as_int(raw.get("like"))),
            reply_count=max(0, _as_int(raw.get("reply_count"))),
            published_at=_as_int(raw.get("published_at")),
            is_top=bool(raw.get("is_top")),
        )

    def to_prompt(self) -> dict:
        return {"text": self.text, "like": self.like}

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Danmaku:
    dmid: str
    # 视频播放进度（秒）。不是墙钟时间。
    progress_s: float
    text: str
    mode: int = 1
    sent_at: int = 0

    @classmethod
    def from_proto(cls, raw: dict) -> "Danmaku | None":
        text = clean_text(raw.get("content"), 120)
        if not text:
            return None
        progress_ms = max(0, _as_int(raw.get("progress")))
        return cls(
            dmid=str(raw.get("id_str") or raw.get("id") or ""),
            progress_s=progress_ms / 1000.0,
            text=text,
            mode=_as_int(raw.get("mode"), 1),
            sent_at=_as_int(raw.get("ctime")),
        )

    @classmethod
    def from_dict(cls, raw: dict) -> "Danmaku | None":
        raw = _dict(raw)
        text = clean_text(raw.get("text"), 120)
        if not text:
            return None
        return cls(
            dmid=str(raw.get("dmid") or ""),
            progress_s=max(0.0, _as_float(raw.get("progress_s"))),
            text=text,
            mode=_as_int(raw.get("mode"), 1),
            sent_at=_as_int(raw.get("sent_at")),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DanmakuResult:
    items: list[Danmaku] = field(default_factory=list)
    partial: bool = False
    source: str = "protobuf"
    segments_ok: int = 0
    segments_expected: int = 0
    error: str = ""

    @property
    def coverage(self) -> float:
        if self.segments_expected <= 0:
            return 1.0 if self.items else 0.0
        return max(0.0, min(1.0, self.segments_ok / self.segments_expected))

    def to_dict(self) -> dict:
        return {
            "items": [x.to_dict() for x in self.items],
            "partial": self.partial,
            "source": self.source,
            "segments_ok": self.segments_ok,
            "segments_expected": self.segments_expected,
            "coverage": round(self.coverage, 3),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "DanmakuResult":
        raw = _dict(raw)
        items: list[Danmaku] = []
        for row in raw.get("items") or []:
            item = Danmaku.from_dict(row) if isinstance(row, dict) else None
            if item is not None:
                items.append(item)
        return cls(
            items=items,
            partial=bool(raw.get("partial")),
            source=str(raw.get("source") or "protobuf"),
            segments_ok=_as_int(raw.get("segments_ok")),
            segments_expected=_as_int(raw.get("segments_expected")),
            error=str(raw.get("error") or ""),
        )


@dataclass
class TimelineBucket:
    index: int
    start_s: int
    end_s: int
    count: int = 0
    unique_ratio: float = 0.0
    intensity: float = 0.0
    spike: bool = False
    samples: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    laughter_count: int = 0
    question_count: int = 0
    coverage: float = 1.0
    summary: str = ""
    audience_reaction: str = ""
    confidence: dict = field(default_factory=dict)

    def to_prompt(self) -> dict:
        return {
            "index": self.index,
            "range_s": [self.start_s, self.end_s],
            "count": self.count,
            "intensity": round(self.intensity, 2),
            "spike": self.spike,
            "samples": list(self.samples),
            "keywords": list(self.keywords),
            "laughter": self.laughter_count,
            "questions": self.question_count,
        }

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Cue:
    cue_id: str
    kind: str
    text: str
    anchor_bucket: int = 0
    offset_s: float = 0.0
    # 模拟解说调度时间；不是视频真实内容时间戳。
    schedule_s: float = 0.0
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    fired: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DanmakuReply:
    reply_id: str
    bucket_index: int
    text: str
    confidence: float = 0.0
    used: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VideoProfile:
    bvid: str
    url: str
    title: str
    duration_s: int
    owner: dict = field(default_factory=dict)
    topic: str = ""
    summary: str = ""
    audience: dict = field(default_factory=dict)
    talking_points: list[str] = field(default_factory=list)
    tease_targets: list[str] = field(default_factory=list)
    guesses: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    confidence: dict = field(default_factory=dict)
    timeline: list[TimelineBucket] = field(default_factory=list)
    cues: list[Cue] = field(default_factory=list)
    replies: list[DanmakuReply] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema": "bili.commentary.profile/v1",
            "bvid": self.bvid,
            "url": self.url,
            "title": self.title,
            "duration_s": self.duration_s,
            "owner": dict(self.owner),
            "topic": self.topic,
            "summary": self.summary,
            "audience": dict(self.audience),
            "talking_points": list(self.talking_points),
            "tease_targets": list(self.tease_targets),
            "guesses": list(self.guesses),
            "open_questions": list(self.open_questions),
            "confidence": dict(self.confidence),
            "timeline": [x.to_dict() for x in self.timeline],
            "cues": [x.to_dict() for x in self.cues],
            "replies": [x.to_dict() for x in self.replies],
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "VideoProfile | None":
        raw = _dict(raw)
        if not raw.get("bvid"):
            return None
        timeline: list[TimelineBucket] = []
        for row in raw.get("timeline") or []:
            if not isinstance(row, dict):
                continue
            timeline.append(TimelineBucket(
                index=_as_int(row.get("index")),
                start_s=_as_int(row.get("start_s")),
                end_s=_as_int(row.get("end_s")),
                count=_as_int(row.get("count")),
                unique_ratio=_as_float(row.get("unique_ratio")),
                intensity=_as_float(row.get("intensity")),
                spike=bool(row.get("spike")),
                samples=[str(x) for x in (row.get("samples") or [])][:10],
                keywords=[str(x) for x in (row.get("keywords") or [])][:10],
                laughter_count=_as_int(row.get("laughter_count")),
                question_count=_as_int(row.get("question_count")),
                coverage=_as_float(row.get("coverage"), 1.0),
                summary=str(row.get("summary") or ""),
                audience_reaction=str(row.get("audience_reaction") or ""),
                confidence=dict(row.get("confidence") or {}),
            ))
        cues: list[Cue] = []
        for row in raw.get("cues") or []:
            if not isinstance(row, dict):
                continue
            cues.append(Cue(
                cue_id=str(row.get("cue_id") or ""),
                kind=str(row.get("kind") or "reaction"),
                text=str(row.get("text") or ""),
                anchor_bucket=_as_int(row.get("anchor_bucket")),
                offset_s=_as_float(row.get("offset_s")),
                schedule_s=_as_float(row.get("schedule_s")),
                evidence=[str(x) for x in (row.get("evidence") or [])],
                confidence=_as_float(row.get("confidence")),
                fired=bool(row.get("fired")),
            ))
        replies: list[DanmakuReply] = []
        for row in raw.get("replies") or []:
            if not isinstance(row, dict):
                continue
            replies.append(DanmakuReply(
                reply_id=str(row.get("reply_id") or ""),
                bucket_index=_as_int(row.get("bucket_index")),
                text=str(row.get("text") or ""),
                confidence=_as_float(row.get("confidence")),
                used=bool(row.get("used")),
            ))
        return cls(
            bvid=str(raw.get("bvid") or ""),
            url=str(raw.get("url") or ""),
            title=str(raw.get("title") or ""),
            duration_s=_as_int(raw.get("duration_s")),
            owner=dict(raw.get("owner") or {}),
            topic=str(raw.get("topic") or ""),
            summary=str(raw.get("summary") or ""),
            audience=dict(raw.get("audience") or {}),
            talking_points=[str(x) for x in (raw.get("talking_points") or [])],
            tease_targets=[str(x) for x in (raw.get("tease_targets") or [])],
            guesses=[str(x) for x in (raw.get("guesses") or [])],
            open_questions=[str(x) for x in (raw.get("open_questions") or [])],
            confidence=dict(raw.get("confidence") or {}),
            timeline=timeline,
            cues=cues,
            replies=replies,
            meta=dict(raw.get("meta") or {}),
        )


__all__ = [
    "Comment", "Cue", "Danmaku", "DanmakuReply", "DanmakuResult",
    "TimelineBucket", "VideoDetail", "VideoProfile", "VideoRef", "clean_text",
]
