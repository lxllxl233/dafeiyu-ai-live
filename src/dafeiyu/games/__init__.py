"""游戏列表：每个游戏一个包（兼容层 + 语料库），统一注册表。"""

from __future__ import annotations

from .base import GameAdapter, GameInfo, GameState
from .registry import GamePack, GameRegistry

__all__ = ["GameAdapter", "GameInfo", "GameState", "GamePack", "GameRegistry", "registry"]


def registry(root=None) -> GameRegistry:
    """每次新建（读盘很便宜），避免多进程/热重载时拿着旧缓存。"""
    return GameRegistry(root)
