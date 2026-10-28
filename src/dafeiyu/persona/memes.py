"""通用 meme 梗库：读 ``persona/memes/<name>.json``，和具体游戏解耦。

游戏专属梗不要放这里；它们属于 ``games/<id>/game-skill/``。
"""

from __future__ import annotations

import json
import random
from typing import Any

from .. import paths


def load_memes(name: str = "general") -> dict[str, Any]:
    path = paths.PERSONA / "memes" / f"{name}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"id": name, "name": name, "items": []}
    if not isinstance(raw, dict):
        return {"id": name, "name": name, "items": []}
    raw.setdefault("id", name)
    raw.setdefault("items", [])
    return raw


def pick_meme(name: str = "general", tag: str | None = None,
              rng: random.Random | None = None) -> dict[str, Any] | None:
    """按 tag 过滤后随机取一条；没有匹配返回 None。"""
    items = [x for x in load_memes(name).get("items", [])
             if isinstance(x, dict) and x.get("text")]
    if tag:
        items = [x for x in items if tag in (x.get("tags") or ())]
    if not items:
        return None
    return (rng or random).choice(items)


__all__ = ["load_memes", "pick_meme"]
