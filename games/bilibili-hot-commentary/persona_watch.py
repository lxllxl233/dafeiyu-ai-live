"""读取 B 站云观影自己的鱼人设，不修改核心 PersonaCard。

B 模式默认只读本包 ``persona/card.json`` / ``style.md``，与全局
``persona/dafeiyu`` 隔离；``load_persona_watch`` 仅保留给通用/旧调用和测试。

人设卡的 ``traits`` / ``tagline`` / ``quips`` 和 ``style.md`` 是默认输入；
可选的 ``watch`` 字段允许用户直接写偏好：

    "watch": {
      "keywords": ["高能整活", "游戏翻车"],
      "avoid": ["恐怖", "政治"],
      "prefer": "喜欢看高能、翻车和能吐槽的视频"
    }

这里只读文件，不调用 LLM、不访问网络。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from dafeiyu import paths

from .models import clean_text

_WORD_SPLIT = re.compile(r"[,，、;；\n]+")
_BAD_WORD = re.compile(r"(https?://|www\.|@\S+|系统提示|api\s*key|密钥)", re.I)


@dataclass(frozen=True)
class PersonaWatch:
    persona_id: str = "dafeiyu"
    name: str = "大肥鱼"
    tagline: str = ""
    traits: tuple[str, ...] = ()
    quips: tuple[str, ...] = ()
    style_text: str = ""
    keywords: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    prefer: str = ""
    source: str = ""

    def to_prompt(self) -> dict:
        return {
            "id": self.persona_id,
            "name": self.name,
            "tagline": self.tagline,
            "traits": list(self.traits),
            "quips": list(self.quips)[:20],
            "style": self.style_text[:3000],
            "watch": {
                "keywords": list(self.keywords),
                "avoid": list(self.avoid),
                "prefer": self.prefer,
            },
        }

    def cache_key(self) -> str:
        raw = json.dumps({
            "id": self.persona_id,
            "name": self.name,
            "tagline": self.tagline,
            "traits": list(self.traits),
            "quips": list(self.quips),
            "style": self.style_text,
            "keywords": list(self.keywords),
            "avoid": list(self.avoid),
            "prefer": self.prefer,
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _words(value) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = _WORD_SPLIT.split(value)
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        return ()
    out: list[str] = []
    for item in raw:
        text = clean_text(item, 24)
        if not text or _BAD_WORD.search(text):
            continue
        if text not in out:
            out.append(text)
        if len(out) >= 8:
            break
    return tuple(out)


def _load_paths(card_path: Path, style_path: Path, pid: str) -> PersonaWatch:
    raw: dict = {}
    try:
        value = json.loads(card_path.read_text(encoding="utf-8"))
        raw = value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        raw = {}
    try:
        style_text = style_path.read_text(encoding="utf-8")[:5000]
    except OSError:
        style_text = ""
    watch = raw.get("watch") if isinstance(raw.get("watch"), dict) else {}
    return PersonaWatch(
        persona_id=str(raw.get("id") or pid),
        name=clean_text(raw.get("name"), 40) or "大肥鱼",
        tagline=clean_text(raw.get("tagline"), 120),
        traits=tuple(clean_text(x, 120) for x in (raw.get("traits") or []) if clean_text(x, 120)),
        quips=tuple(clean_text(x, 80) for x in (raw.get("quips") or []) if clean_text(x, 80)),
        style_text=style_text,
        keywords=_words(watch.get("keywords")),
        avoid=_words(watch.get("avoid")),
        prefer=clean_text(watch.get("prefer"), 240),
        source=str(card_path) if card_path.exists() else "",
    )


def load_persona_watch(persona_id: str = "", base: str | Path | None = None) -> PersonaWatch:
    """读 ``persona/<id>/card.json``/``style.md``；供全球主播人设使用。"""
    pid = str(persona_id or "").strip()
    if not pid:
        try:
            from dafeiyu.core.config import LiveConfig

            pid = str(LiveConfig.load().persona or "dafeiyu")
        except Exception:
            pid = "dafeiyu"
    root = Path(base) if base is not None else paths.PERSONA
    return _load_paths(root / pid / "card.json", root / pid / "style.md", pid)


BILI_PERSONA_ID = "bili-xqc"
BILI_PERSONA_DIR = Path(__file__).with_name("persona")


def load_bili_persona(directory: str | Path | None = None) -> PersonaWatch:
    """B 站模式专用人设：只读本游戏包 ``persona/``，不碰全球 ``persona/``。

    默认文件是 ``games/bilibili-hot-commentary/persona/{card.json,style.md}``，
    控制台「游戏 → B站云观影」详情里可以查看/编辑。
    """
    folder = Path(directory) if directory is not None else BILI_PERSONA_DIR
    return _load_paths(folder / "card.json", folder / "style.md", BILI_PERSONA_ID)


__all__ = [
    "BILI_PERSONA_DIR", "BILI_PERSONA_ID", "PersonaWatch",
    "load_bili_persona", "load_persona_watch",
]
