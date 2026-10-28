"""事件定义：层与层之间只传事件，不互相 import 具体实现。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class Event:
    """一条直播事件。

    topic 用点号分层，订阅端可以用 ``game.*`` 这样的通配符。
    data 只放可 JSON 序列化的值（要进日志、要推 UI）。
    """

    topic: str
    data: Mapping[str, Any] = field(default_factory=dict)
    source: str = ""
    ts: float = field(default_factory=time.time)

    def with_source(self, source: str) -> "Event":
        return Event(topic=self.topic, data=self.data, source=source, ts=self.ts)

    def to_dict(self) -> dict:
        return {"topic": self.topic, "data": dict(self.data), "source": self.source, "ts": self.ts}


class Topics:
    """订阅用的主题常量（避免各层手打字符串写错）。"""

    SESSION_STARTED = "session.started"
    SESSION_STOPPED = "session.stopped"

    GAME_CONNECTED = "game.connected"
    GAME_STATE = "game.state"
    GAME_TURN = "game.turn"
    GAME_ENDED = "game.ended"
    GAME_ERROR = "game.error"

    COMMENTARY_SAY = "commentary.say"
    COMMENTARY_LINT = "commentary.lint"
    COMMENTARY_PUSHED = "commentary.pushed"

    VOICE_STARTED = "voice.started"
    VOICE_READY = "voice.ready"
    VOICE_FAILED = "voice.failed"

    UI_PUSHED = "ui.pushed"
    # 所有事件（调试用）
    ALL = "*"


__all__ = ["Event", "Topics"]
