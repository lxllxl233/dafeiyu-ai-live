"""演示信号源：没有游戏也能跑通「游戏 → 性格 → 语音 → UI」整条链路。

    dafeiyu demo --voice animal --once

它不是玩具：`dafeiyu demo` 走的是和真游戏完全相同的事件总线 / 性格层 / 语音层 / overlay
（只是游戏适配器换成了脚本），所以能当直播姬的离线验收。
"""

from __future__ import annotations

import threading
import time
from typing import Iterator

from ..core.events import Event, Topics
from .base import GameAdapter, GameState

# 每一步：状态 + 这一步说什么（thought / meme）
SCENARIO: list[dict] = [
    {
        "delay": 0.4,
        "state": {"screen": "MAP", "floor": 1, "hp": 80, "max_hp": 80, "gold": 99,
                  "character": "演示角色", "ascension": 10},
        "say": ("上路，80 血，目标活过第二幕。", "先算账再摸牌。"),
    },
    {
        "delay": 1.4,
        "state": {"screen": "COMBAT", "floor": 6, "hp": 63, "max_hp": 80, "gold": 99,
                  "character": "演示角色", "ascension": 10, "in_combat": True, "turn": 1},
        "say": ("两只，11 和 34，入伤 9。", "先掐会塞牌的那只。"),
    },
    {
        "delay": 1.4,
        "state": {"screen": "COMBAT", "floor": 6, "hp": 52, "max_hp": 80, "gold": 99,
                  "character": "演示角色", "ascension": 10, "in_combat": True, "turn": 2},
        "say": ("它 21，我最多挡 15。", "差 6，嗨呀。"),
    },
    {
        "delay": 1.4,
        "state": {"screen": "COMBAT", "floor": 6, "hp": 47, "max_hp": 80, "gold": 99,
                  "character": "演示角色", "ascension": 10, "in_combat": True, "turn": 3},
        "say": ("9 点，它 9 血。", "刚好够。"),
    },
    {
        "delay": 1.4,
        "state": {"screen": "REWARD", "floor": 6, "hp": 47, "max_hp": 80, "gold": 114,
                  "character": "演示角色", "ascension": 10},
        "say": ("三选一，全是白板。", "这抓位还用想。"),
    },
]


def _state(raw: dict) -> GameState:
    return GameState(
        screen=str(raw.get("screen") or ""),
        floor=raw.get("floor"),
        hp=raw.get("hp"),
        max_hp=raw.get("max_hp"),
        gold=raw.get("gold"),
        character=str(raw.get("character") or ""),
        ascension=raw.get("ascension"),
        in_combat=bool(raw.get("in_combat")),
        turn=raw.get("turn"),
    )


class DemoAdapter(GameAdapter):
    game_id = "demo"
    display_name = "演示信号源"

    def __init__(self, delay: float = 0.0, loop: bool = False) -> None:
        self.delay = max(0.0, delay)
        self.loop = loop
        self.scenario = SCENARIO
        self._i = 0

    def available(self) -> bool:
        return True

    def snapshot(self) -> GameState:
        step = SCENARIO[min(self._i, len(SCENARIO) - 1)]
        return _state(step["state"])

    def events(self, stop: threading.Event | None = None, interval: float = 1.0) -> Iterator[Event]:
        while not (stop and stop.is_set()):
            for step in SCENARIO:
                if stop and stop.is_set():
                    return
                self._i = SCENARIO.index(step)
                yield Event(Topics.GAME_STATE, _state(step["state"]).to_dict(), source=self.game_id)
                if stop is None:
                    return
                if stop.wait(max(0.0, step.get("delay", interval))):
                    return
            if not self.loop:
                return

    def status(self) -> dict:
        return {"game_id": self.game_id, "name": self.display_name,
                "available": True, "state": self.snapshot().to_dict()}


__all__ = ["DemoAdapter", "SCENARIO"]
