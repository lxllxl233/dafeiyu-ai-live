"""只读适配器：把玩具决斗的状态文件变成直播姬的 GAME_STATE 快照。

适配器不执行动作、不创建状态；没有状态文件时返回 ``IDLE``，所以桌宠/控制台
在没开演示时也不会报错。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dafeiyu import paths
from dafeiyu.games.base import GameAdapter, GameState

GAME_ID = "toy-duel"
DISPLAY_NAME = "玩具决斗"
STATE_ENV = "TOY_DUEL_STATE"


def _state_path() -> Path:
    override = os.environ.get(STATE_ENV)
    if override:
        return Path(override).expanduser()
    return paths.LOGS / "toy_duel_state.json"


def _read() -> dict[str, Any]:
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


class ToyAdapter(GameAdapter):
    game_id = GAME_ID
    display_name = DISPLAY_NAME

    def available(self) -> bool:
        return True

    def snapshot(self) -> GameState:
        raw = _read()
        if not raw:
            return GameState(screen="IDLE")
        hero = raw.get("hero") if isinstance(raw.get("hero"), dict) else {}
        screen = str(raw.get("screen") or "IDLE")
        turn = int(raw.get("turn") or 0)
        return GameState(
            screen=screen,
            floor=turn,
            hp=int(hero.get("hp") or 0),
            max_hp=int(hero.get("max_hp") or 0),
            character="演示勇者",
            in_combat=screen == "COMBAT",
            turn=turn,
            extra={"energy": int(raw.get("energy") or 0)},
        )

    def status(self) -> dict:
        state = self.snapshot()
        return {
            "game_id": self.game_id,
            "name": self.display_name,
            "available": True,
            "state": state.to_dict(),
        }


__all__ = ["ToyAdapter"]
