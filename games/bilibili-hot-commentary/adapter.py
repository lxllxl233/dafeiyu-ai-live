"""只读伪状态适配器：把云观影进度变成通用 GameState。

核心的 studio/桌宠只认 GameAdapter；这里不启动线程、不访问网络。
"""

from __future__ import annotations

import json
from pathlib import Path

from dafeiyu import paths
from dafeiyu.games.base import GameAdapter, GameState

GAME_ID = "bilibili-hot-commentary"


class BiliHotAdapter(GameAdapter):
    game_id = GAME_ID
    display_name = "B站热门视频云观影"

    def __init__(self, *, status_path: str | Path | None = None) -> None:
        self.status_path = Path(status_path) if status_path else paths.LOGS / "autoplay.json"

    def _state(self) -> dict:
        try:
            raw = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def available(self) -> bool:
        raw = self._state()
        return bool(raw.get("running") and raw.get("game") == GAME_ID)

    def snapshot(self) -> GameState:
        raw = self._state()
        if raw.get("game") != GAME_ID:
            return GameState()
        return GameState(
            screen=str(raw.get("screen") or ""),
            floor=int(raw.get("step") or 0) or None,
            character="云观影",
            extra={
                "bvid": str(raw.get("bvid") or ""),
                "video_title": str(raw.get("video_title") or ""),
                "position_s": raw.get("position_s"),
                "duration_s": raw.get("duration_s"),
                "cues_fired": raw.get("cues_fired"),
                "replies_fired": raw.get("replies_fired"),
            },
        )

    def status(self) -> dict:
        raw = self._state()
        return {
            "game_id": GAME_ID,
            "name": self.display_name,
            "available": bool(raw.get("running") and raw.get("game") == GAME_ID),
            "state": raw,
        }


__all__ = ["BiliHotAdapter"]
