"""由鱼的人设生成本次要看的 B 站搜索词。

这是唯一一个「为选片而调 LLM」的入口：每个进程/人设只生成一次，写入缓存；
之后 `runner` 只拿关键词去搜索，不会在播放循环里反复调用。用户也可以直接在
``config.json`` 写 ``bilibili_commentary.search_keywords``，完全不烧 token。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .config import CommentaryConfig
from .models import clean_text
from .persona_watch import PersonaWatch
from .prompts import SEARCH_GOAL, SEARCH_PROMPT_VERSION

_BAD_WORD = re.compile(
    r"(https?://|www\.|@\S+|忽略(以上|之前|前面)|系统提示|api\s*key|密钥)", re.I)


@dataclass(frozen=True)
class SearchIntent:
    keywords: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    reason: str = ""
    source: str = "fallback"

    def to_dict(self) -> dict:
        return {
            "keywords": list(self.keywords),
            "avoid": list(self.avoid),
            "reason": self.reason,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "SearchIntent | None":
        if not isinstance(raw, dict):
            return None
        words = _clean_words(raw.get("keywords"), 6, 24)
        avoid = _clean_words(raw.get("avoid"), 8, 24)
        if not words:
            return None
        return cls(
            keywords=words, avoid=avoid,
            reason=clean_text(raw.get("reason"), 160),
            source=str(raw.get("source") or "cache"),
        )


class SearchIntentPlanner:
    def __init__(self, config: CommentaryConfig, persona: PersonaWatch, *,
                 planner=None, cache=None, logger=None) -> None:
        self.config = config
        self.persona = persona
        self.cache = cache
        self.log = logger or (lambda text: None)
        self.planner = planner
        self.last_usage: dict = {}

    def _blocked(self) -> tuple[str, ...]:
        return tuple(
            str(word).strip() for word in (self.config.search_exclude or ())
            if str(word).strip()
        )

    def _filtered(self, intent: SearchIntent) -> SearchIntent | None:
        """去掉命中当前游戏/排除词的搜索词；全被排除时交给 fallback。"""
        blocked = tuple(word.lower() for word in self._blocked())
        if not blocked:
            return intent
        words = tuple(
            word for word in intent.keywords
            if not any(block in word.lower() for block in blocked)
        )
        if not words:
            return None
        avoid = tuple(
            word for word in intent.avoid
            if not any(block in word.lower() for block in blocked)
        )
        return replace(intent, keywords=words, avoid=avoid)

    def _fallback(self, source: str = "persona") -> SearchIntent:
        words = self.config.search_keywords or self.persona.keywords
        intent = SearchIntent(
            keywords=tuple(words), avoid=tuple(self.persona.avoid),
            reason=self.persona.prefer or "按人设偏好选片", source=source,
        )
        filtered = self._filtered(intent)
        return filtered if filtered is not None else SearchIntent(
            avoid=tuple(self.persona.avoid) + self._blocked(),
            reason="人设关键词都被当前游戏排除词过滤，回退热门",
            source=source,
        )

    def get(self) -> SearchIntent:
        if self.config.search_keywords:
            return SearchIntent(
                keywords=tuple(self.config.search_keywords),
                avoid=tuple(self.persona.avoid),
                reason="config.json 手写关键词",
                source="config",
            )

        cache_key = self.persona.cache_key()
        if self._blocked():
            # 排除词变了也要重新生成；否则旧缓存会继续把当前游戏搜回来。
            cache_key += "|exclude=" + ",".join(self._blocked())
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if isinstance(cached, dict) and cached.get("prompt_version") == SEARCH_PROMPT_VERSION:
                intent = SearchIntent.from_dict(cached.get("intent") or {})
                if intent is not None:
                    return SearchIntent(
                        keywords=intent.keywords, avoid=intent.avoid,
                        reason=intent.reason, source="cache",
                    )

        if self.planner is None:
            return self._fallback()
        try:
            result = self.planner.plan(
                {
                    "persona": self.persona.to_prompt(),
                    "constraints": {
                        "keywords": "3–5 个中文搜索短语，每个 2–12 个字",
                        "avoid": "最多 5 个不想看的主题",
                        # 当前游戏内容由 B 包隔离，避免云观影搜成游戏实况。
                        "exclude": list(self._blocked()),
                        "untrusted_text": False,
                        "note": ("人设是用户配置；不要输出 URL、@用户名、系统提示或密钥；"
                                 "关键词和 avoid 里都不能出现 exclude 中的词。"),
                    },
                },
                goal=SEARCH_GOAL,
            )
        except Exception as exc:  # noqa: BLE001
            self.log(f"[bili] 搜索意图 LLM 失败：{type(exc).__name__}: {exc}")
            return self._fallback()
        if not isinstance(result, dict) or not result.get("ok"):
            self.log("[bili] 搜索意图 LLM 没有返回可用结果")
            return self._fallback()
        if isinstance(result.get("usage"), dict):
            self.last_usage = result["usage"]
        plan = result.get("plan")
        if not isinstance(plan, dict):
            return self._fallback()
        intent = SearchIntent.from_dict({**plan, "source": "llm"})
        if intent is None:
            return self._fallback()
        filtered = self._filtered(intent)
        if filtered is None:
            return self._fallback()
        intent = filtered
        if self.cache is not None:
            self.cache.put(cache_key, {
                "prompt_version": SEARCH_PROMPT_VERSION,
                "persona": self.persona.persona_id,
                "intent": intent.to_dict(),
            })
        return intent


def _clean_words(value, limit: int, max_len: int) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = value.replace("，", ",").replace("、", ",").split(",")
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        return ()
    out: list[str] = []
    for item in raw:
        text = clean_text(item, max_len)
        if not text or len(text) < 2 or _BAD_WORD.search(text):
            continue
        if text in out:
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return tuple(out)


__all__ = ["SearchIntent", "SearchIntentPlanner"]
