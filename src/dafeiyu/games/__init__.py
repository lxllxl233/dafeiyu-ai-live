"""游戏列表：每个游戏一个包（兼容层 + 语料库），统一注册表。"""

from __future__ import annotations

from .base import GameAdapter, GameInfo, GameState
from .catalog import (
    default_game_id,
    env_value,
    game_name,
    legacy_env_prefixes,
    resolve_game_id,
)
from .contracts import (
    ActionCode,
    ActionResult,
    AutoplayHost,
    AutoplayRunner,
    GameCapabilities,
    GameEvent,
    GameManifest,
    GamePlugin,
    GameStateEnvelope,
    GameTools,
    ManifestIssue,
    as_action_result,
)
from .registry import GamePack, GameRegistry

__all__ = [
    "ActionCode", "ActionResult", "AutoplayHost", "AutoplayRunner",
    "GameAdapter", "GameCapabilities", "GameEvent", "GameInfo", "GameManifest",
    "GamePack", "GamePlugin", "GameRegistry", "GameState", "GameStateEnvelope",
    "GameTools", "ManifestIssue", "as_action_result", "default_game_id",
    "env_value", "game_name", "legacy_env_prefixes", "registry",
    "resolve_game_id",
]


def registry(root=None) -> GameRegistry:
    """每次新建（读盘很便宜），避免多进程/热重载时拿着旧缓存。"""
    return GameRegistry(root)
