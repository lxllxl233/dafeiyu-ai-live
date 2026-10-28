"""性格层：人设卡加载。

人设卡放在仓库根 ``persona/<id>/card.json``，改文案不用改代码。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths


@dataclass(frozen=True, slots=True)
class Style:
    """台词规则。数值和 `tools/check_caster.py` 的 lint 口径保持一致。"""

    max_len: int = 60
    soft_len: int = 85
    max_thought: int = 120
    max_meme: int = 70
    max_total: int = 170
    max_lines: int = 3
    max_fillers: int = 3
    min_gap: float = 1.2
    quip_cooldown: float = 600.0
    max_quips_per_10min: int = 2

    @classmethod
    def from_dict(cls, raw: dict | None) -> "Style":
        raw = raw or {}
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass(frozen=True, slots=True)
class PersonaCard:
    id: str = "dafeiyu"
    name: str = "大肥鱼"
    title: str = "DeepSeek 大肥鱼"
    tagline: str = "一条把算账当正经事的鱼"
    voice: str = "mambo"
    traits: tuple[str, ...] = ()
    style: Style = field(default_factory=Style)
    banned_prefixes: tuple[str, ...] = ()
    banned_labels: tuple[str, ...] = ()
    repeat_words: tuple[str, ...] = ()
    quips: tuple[str, ...] = ()
    openers: tuple[str, ...] = ()
    closers: tuple[str, ...] = ()
    source: str = ""

    @classmethod
    def from_dict(cls, raw: dict, source: str = "") -> "PersonaCard":
        raw = raw or {}
        return cls(
            id=str(raw.get("id") or "dafeiyu"),
            name=str(raw.get("name") or "大肥鱼"),
            title=str(raw.get("title") or "DeepSeek 大肥鱼"),
            tagline=str(raw.get("tagline") or ""),
            voice=str(raw.get("voice") or "mambo"),
            traits=tuple(str(x) for x in raw.get("traits") or ()),
            style=Style.from_dict(raw.get("style")),
            banned_prefixes=tuple(str(x) for x in raw.get("banned_prefixes") or ()),
            banned_labels=tuple(str(x) for x in raw.get("banned_labels") or ()),
            repeat_words=tuple(str(x) for x in raw.get("repeat_words") or ()),
            quips=tuple(str(x) for x in raw.get("quips") or ()),
            openers=tuple(str(x) for x in raw.get("openers") or ()),
            closers=tuple(str(x) for x in raw.get("closers") or ()),
            source=source,
        )

    @classmethod
    def load(cls, persona_id: str = "dafeiyu", base: Path | None = None) -> "PersonaCard":
        base = base or paths.PERSONA
        path = base / persona_id / "card.json"
        if not path.exists():
            raise FileNotFoundError(f"人设卡不存在：{path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.setdefault("id", persona_id)
        return cls.from_dict(raw, source=paths.rel(path))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "tagline": self.tagline,
            "voice": self.voice,
            "traits": list(self.traits),
            "style": {k: getattr(self.style, k) for k in self.style.__dataclass_fields__},
            "quips": list(self.quips),
            "source": self.source,
        }


__all__ = ["Style", "PersonaCard"]
