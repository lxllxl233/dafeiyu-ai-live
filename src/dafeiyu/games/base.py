"""游戏适配器协议：新游戏只需要实现这几样，就能接进直播姬。

    兼容层（compat）  ←→  GameAdapter  ←→  直播姬内核（事件总线）
                             ↑
                     语料库（corpus，给性格层/决策层用）

适配器只负责「把游戏状态变成事件」，不负责说话、不负责配音、不负责 UI。
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator

from ..core.events import Event, Topics


@dataclass(frozen=True, slots=True)
class GameInfo:
    id: str
    name: str
    platform: str = ""
    status: str = "ready"
    tags: tuple[str, ...] = ()
    notes: str = ""
    adapter: str = ""
    pack: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "platform": self.platform,
            "status": self.status, "tags": list(self.tags), "notes": self.notes,
            "adapter": self.adapter, "pack": self.pack,
        }


@dataclass
class GameState:
    """一次游戏快照（适配器能拿到多少填多少）。"""

    screen: str = ""
    floor: int | None = None
    hp: int | None = None
    max_hp: int | None = None
    gold: int | None = None
    character: str = ""
    ascension: int | None = None
    in_combat: bool = False
    turn: int | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = {
            "screen": self.screen, "floor": self.floor, "hp": self.hp,
            "max_hp": self.max_hp, "gold": self.gold, "character": self.character,
            "ascension": self.ascension, "in_combat": self.in_combat, "turn": self.turn,
        }
        out.update(self.extra)
        return out

    def digest(self) -> tuple:
        return (self.screen, self.floor, self.hp, self.max_hp, self.gold,
                self.character, self.ascension, self.in_combat, self.turn)


class GameAdapter(ABC):
    """游戏兼容层的统一入口。"""

    game_id: str = ""
    display_name: str = ""

    @abstractmethod
    def available(self) -> bool:
        """游戏/兼容层现在能用吗（不抛异常，超时要短）。"""

    @abstractmethod
    def snapshot(self) -> GameState:
        """读一帧状态；读不到返回空 GameState。"""

    def events(self, stop: threading.Event | None = None, interval: float = 1.0) -> Iterator[Event]:
        """轮询并把状态变化变成事件；默认实现只发 GAME_STATE。"""
        last = None
        while not (stop and stop.is_set()):
            state = self.snapshot()
            digest = state.digest()
            if digest != last:
                last = digest
                yield Event(Topics.GAME_STATE, state.to_dict(), source=self.game_id)
                if state.in_combat:
                    yield Event(Topics.GAME_TURN, {"turn": state.turn, **state.to_dict()},
                                source=self.game_id)
            if stop is None:
                break
            if stop.wait(interval):
                break

    def status(self) -> dict:
        return {
            "game_id": self.game_id,
            "name": self.display_name,
            "available": False,
            "state": {},
        }


__all__ = ["GameAdapter", "GameInfo", "GameState"]
