"""B 站云观影模式的配置：默认值在 game.json，用户覆盖在 config.json。

核心不新增配置字段；用户把覆盖写进 config.json 的顶层 ``bilibili_commentary``，
``LiveConfig`` 会把它收进 ``extra``，本模块只读不改。这样不需要为了这个模式
去改核心配置 dataclass。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


DEFAULT_EXCLUDE = (
    "政治", "选举", "灾难", "空难", "血腥", "自杀", "赌博", "医疗建议",
    "恐怖袭击", "枪击", "战争",
)


@dataclass(frozen=True)
class CommentaryConfig:
    speed: float = 1.0
    min_duration_s: int = 120
    # 用户确认：最多 30 分钟，不刷太长。
    max_duration_s: int = 1800
    min_comments: int = 12
    target_comments: int = 30
    min_like_view_ratio: float = 0.005
    max_danmaku_per_video: int = 10000
    max_candidates_per_cycle: int = 3
    seen_ttl_days: int = 7
    failed_cooldown_min: int = 30
    skip_cooldown_min: int = 60
    min_gap_s: float = 45.0
    max_cues: int = 8
    max_replies: int = 4
    reply_min_gap_s: float = 60.0
    dynamic_reactions: bool = False
    prewarm_tts: bool = True
    allow_multi_part: bool = False
    search_enabled: bool = False
    search_keywords: tuple[str, ...] = ()
    # 云观影不应该被当前游戏污染：例如在 B 站刷到正在玩的《杀戮尖塔2》。
    # 这些词同时约束搜索意图和候选标题；用户可在 config.json 覆盖。
    search_exclude: tuple[str, ...] = ()
    # 搜索页首屏有 40+ 张卡；一次多取一些，配合更大的补详情上限。
    search_prefetch: int = 16
    search_limit: int = 40
    # 除了人设的搞笑/高能搜索，每隔 high_view_every 条补一条高播放量的
    # “大肥鱼”视频；high_view_every=0 关闭。默认每条都先试一次，
    # 没有未看过的大肥鱼时才回落到普通候选。
    high_view_keywords: tuple[str, ...] = ()
    high_view_min_view: int = 100_000
    high_view_every: int = 1
    open_browser: bool = False
    browser_path: str = ""
    browser_window: bool = True
    # B 模式默认用独立 Chrome profile + app 窗口：只有一个页面、不碰用户正在
    # 浏览的标签页，也不需要抢前台/键盘输入。
    browser_isolated: bool = True
    browser_profile_dir: str = ""
    browser_fullscreen: bool = True
    # secondary = 优先副屏；primary = 主屏；数字 = 显示器索引（0 起）。
    browser_monitor: str = "secondary"
    like_on_watch: bool = False
    # 刷到“大肥鱼同类”视频时，模型可以自己决定要不要在视频评论区留一句；
    # 不是每条都评，另有每日上限和最小间隔。需要登录 Cookie 才会真正发。
    video_comment_enabled: bool = True
    video_comment_max_chars: int = 60
    # 0 = 不限每日条数；仍受 video_comment_min_gap_s 控制最小间隔。
    video_comment_max_per_day: int = 0
    video_comment_min_gap_s: float = 600.0
    # 直播弹幕互动：事件来自桌宠监听进程写的 logs/bilibili_events.jsonl。
    live_chat_enabled: bool = False
    live_reply_min_interval_s: float = 20.0
    live_reply_sc_min_interval_s: float = 5.0
    live_reply_max_per_min: int = 3
    live_reply_user_cooldown_s: float = 60.0
    live_reply_max_queue: int = 6
    live_reply_max_input_chars: int = 180
    live_reply_max_chars: int = 60
    live_reply_danmu_chars: int = 20
    live_reply_max_tokens_per_hour: int = 5000
    live_reply_max_tokens_per_day: int = 20000
    live_reply_send_danmu: bool = False
    live_reply_speak: bool = True
    exclude_keywords: tuple[str, ...] = DEFAULT_EXCLUDE
    llm_timeout_s: float = 45.0

    @classmethod
    def from_dict(cls, raw: dict | None) -> "CommentaryConfig":
        raw = raw if isinstance(raw, dict) else {}
        known = {f.name for f in fields(cls)}
        values: dict[str, Any] = {}
        for key, value in raw.items():
            if key not in known or value is None:
                continue
            values[key] = value
        for key in ("exclude_keywords", "search_keywords", "search_exclude",
                    "high_view_keywords"):
            if key not in values:
                continue
            value = values[key]
            if isinstance(value, str):
                values[key] = tuple(
                    x.strip() for x in value.replace("，", ",").split(",") if x.strip()
                )
            elif isinstance(value, (list, tuple)):
                values[key] = tuple(str(x).strip() for x in value if str(x).strip())
            else:
                values.pop(key, None)
        try:
            cfg = cls(**values)
        except TypeError:
            cfg = cls()
        return cfg.normalized()

    def normalized(self) -> "CommentaryConfig":
        """把危险/不合理值夹到可执行范围，避免配置把时钟或 LLM 拖死。"""
        speed = _clamp_float(self.speed, 0.25, 4.0, 1.0)
        min_dur = _clamp_int(self.min_duration_s, 1, 1800, 120)
        max_dur = _clamp_int(self.max_duration_s, min_dur, 1800, 1800)
        return CommentaryConfig(
            speed=speed,
            min_duration_s=min_dur,
            max_duration_s=max_dur,
            min_comments=_clamp_int(self.min_comments, 0, 100, 12),
            target_comments=_clamp_int(self.target_comments, 1, 50, 30),
            min_like_view_ratio=_clamp_float(
                self.min_like_view_ratio, 0.0, 0.5, 0.005),
            max_danmaku_per_video=_clamp_int(self.max_danmaku_per_video, 100, 50000, 10000),
            max_candidates_per_cycle=_clamp_int(self.max_candidates_per_cycle, 1, 10, 3),
            seen_ttl_days=_clamp_int(self.seen_ttl_days, 0, 365, 7),
            failed_cooldown_min=_clamp_int(self.failed_cooldown_min, 1, 1440, 30),
            skip_cooldown_min=_clamp_int(self.skip_cooldown_min, 1, 1440, 60),
            min_gap_s=_clamp_float(self.min_gap_s, 10.0, 300.0, 45.0),
            max_cues=_clamp_int(self.max_cues, 1, 12, 8),
            max_replies=_clamp_int(self.max_replies, 0, 12, 4),
            reply_min_gap_s=_clamp_float(self.reply_min_gap_s, 10.0, 300.0, 60.0),
            dynamic_reactions=bool(self.dynamic_reactions),
            prewarm_tts=bool(self.prewarm_tts),
            allow_multi_part=bool(self.allow_multi_part),
            search_enabled=bool(self.search_enabled),
            search_keywords=tuple(self.search_keywords or ()),
            search_exclude=tuple(self.search_exclude or ()),
            search_prefetch=_clamp_int(self.search_prefetch, 1, 30, 16),
            search_limit=_clamp_int(self.search_limit, 1, 50, 40),
            high_view_keywords=tuple(self.high_view_keywords or ()),
            high_view_min_view=_clamp_int(
                self.high_view_min_view, 0, 1_000_000_000, 100_000),
            high_view_every=_clamp_int(self.high_view_every, 0, 20, 1),
            open_browser=bool(self.open_browser),
            browser_path=str(self.browser_path or "").strip(),
            browser_window=bool(self.browser_window),
            browser_isolated=bool(self.browser_isolated),
            browser_profile_dir=str(self.browser_profile_dir or "").strip(),
            browser_fullscreen=bool(self.browser_fullscreen),
            browser_monitor=str(self.browser_monitor or "secondary").strip().lower(),
            like_on_watch=bool(self.like_on_watch),
            video_comment_enabled=bool(self.video_comment_enabled),
            video_comment_max_chars=_clamp_int(
                self.video_comment_max_chars, 10, 200, 60),
            video_comment_max_per_day=_clamp_int(
                self.video_comment_max_per_day, 0, 100000, 0),
            video_comment_min_gap_s=_clamp_float(
                self.video_comment_min_gap_s, 0.0, 86400.0, 600.0),
            live_chat_enabled=bool(self.live_chat_enabled),
            live_reply_min_interval_s=_clamp_float(
                self.live_reply_min_interval_s, 5.0, 300.0, 20.0),
            live_reply_sc_min_interval_s=_clamp_float(
                self.live_reply_sc_min_interval_s, 1.0, 60.0, 5.0),
            live_reply_max_per_min=_clamp_int(self.live_reply_max_per_min, 1, 12, 3),
            live_reply_user_cooldown_s=_clamp_float(
                self.live_reply_user_cooldown_s, 0.0, 600.0, 60.0),
            live_reply_max_queue=_clamp_int(self.live_reply_max_queue, 1, 20, 6),
            live_reply_max_input_chars=_clamp_int(
                self.live_reply_max_input_chars, 40, 500, 180),
            live_reply_max_chars=_clamp_int(self.live_reply_max_chars, 10, 120, 60),
            live_reply_danmu_chars=_clamp_int(self.live_reply_danmu_chars, 1, 40, 20),
            live_reply_max_tokens_per_hour=_clamp_int(
                self.live_reply_max_tokens_per_hour, 500, 50000, 5000),
            live_reply_max_tokens_per_day=_clamp_int(
                self.live_reply_max_tokens_per_day, 1000, 200000, 20000),
            live_reply_send_danmu=bool(self.live_reply_send_danmu),
            live_reply_speak=bool(self.live_reply_speak),
            exclude_keywords=tuple(self.exclude_keywords or DEFAULT_EXCLUDE),
            llm_timeout_s=_clamp_float(self.llm_timeout_s, 10.0, 120.0, 45.0),
        )

    def bucket_seconds(self, duration_s: float) -> int:
        """16 分钟 -> 60 秒；短视频 -> 30 秒；超过 20 分钟 -> 120 秒。

        用户确认 60 秒是默认；30 分钟上限时 120 秒可控制在 15 个桶，
        避免把 LLM 输入撑得过大。
        """
        duration = max(1.0, float(duration_s))
        if duration <= 300:
            return 30
        if duration <= 1200:
            return 60
        return 120

    def to_dict(self) -> dict:
        out = asdict(self)
        out["exclude_keywords"] = list(self.exclude_keywords)
        out["search_keywords"] = list(self.search_keywords)
        out["search_exclude"] = list(self.search_exclude)
        out["high_view_keywords"] = list(self.high_view_keywords)
        return out


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def load_commentary_config(
    pack_dir: str | Path | None = None,
    *,
    live_config=None,
) -> CommentaryConfig:
    """game.json 的 commentary 作为默认，config.json 的 extra 覆盖。"""
    base: dict = {}
    if pack_dir:
        candidate = Path(pack_dir)
        # 兼容传 game.json 文件本身；runner 以前传文件时被当成目录，配置全部丢失。
        path = candidate if candidate.is_file() or candidate.name == "game.json" else candidate / "game.json"
    else:
        path = Path(__file__).with_name("game.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("commentary"), dict):
            base.update(raw["commentary"])
    except (OSError, ValueError):
        pass
    if live_config is None:
        try:
            from dafeiyu.core.config import LiveConfig

            live_config = LiveConfig.load()
        except Exception:
            live_config = None
    extra = getattr(live_config, "extra", None)
    if isinstance(extra, dict) and isinstance(extra.get("bilibili_commentary"), dict):
        base.update(extra["bilibili_commentary"])
    return CommentaryConfig.from_dict(base)


__all__ = ["CommentaryConfig", "DEFAULT_EXCLUDE", "load_commentary_config"]
