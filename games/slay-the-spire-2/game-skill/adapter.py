"""《杀戮尖塔 2》适配器：只读兼容层（mod 本地 HTTP :8080）的公开接口。

兼容层本体在 `games/slay-the-spire-2/compat/`（客户端 `sts2.py` + mod DLL）；
这里只做「状态 → 事件」，不替玩家出牌——出牌仍走兼容层 CLI / Agent。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Iterator

from dafeiyu.core.events import Event, Topics
from dafeiyu.games.base import GameAdapter, GameState


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class Sts2Adapter(GameAdapter):
    game_id = "slay-the-spire-2"
    display_name = "杀戮尖塔 2"

    def __init__(self, api: str | None = None, timeout: float = 1.0) -> None:
        self.api = (api or os.environ.get("DAFEIYU_STS2_API")
                    or os.environ.get("STS2_API")
                    or "http://127.0.0.1:8080").rstrip("/")
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last_error = ""

    # ---------------------------------------------------------------- 读状态
    def _get(self, path: str, timeout: float | None = None) -> dict | None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(self.api + path, timeout=timeout or self.timeout) as r:
                raw = r.read()
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as exc:
            with self._lock:
                self._last_error = f"{type(exc).__name__}: {exc}"
            return None

    @staticmethod
    def _unwrap(payload: dict) -> dict:
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def available(self) -> bool:
        return self._get("/state", timeout=self.timeout) is not None

    def snapshot(self) -> GameState:
        payload = self._get("/state")
        if not payload:
            return GameState()
        d = self._unwrap(payload)
        run = d.get("run") if isinstance(d.get("run"), dict) else {}
        state = GameState(
            screen=str(d.get("screen") or ""),
            floor=_int(run.get("floor")),
            hp=_int(run.get("current_hp")),
            max_hp=_int(run.get("max_hp")),
            gold=_int(run.get("gold")),
            character=str(run.get("character_name") or run.get("character_id") or ""),
            ascension=_int(run.get("ascension")),
            in_combat=bool(d.get("in_combat")),
            turn=_int(d.get("turn")),
        )
        state.extra.update({
            "run_id": d.get("run_id"),
            "act_id": run.get("act_id"),
            "boss_id": run.get("boss_id"),
        })
        return state

    def events(self, stop: threading.Event | None = None, interval: float = 1.0) -> Iterator[Event]:
        last = None
        while not (stop and stop.is_set()):
            state = self.snapshot()
            digest = state.digest()
            if digest != last:
                last = digest
                yield Event(Topics.GAME_STATE, state.to_dict(), source=self.game_id)
                if state.in_combat:
                    yield Event(Topics.GAME_TURN, state.to_dict(), source=self.game_id)
            if stop is None:
                return
            if stop.wait(interval):
                return

    def status(self) -> dict:
        state = self.snapshot()
        return {
            "game_id": self.game_id,
            "name": self.display_name,
            "available": bool(state.screen),
            "api": self.api,
            "state": state.to_dict(),
            "error": "" if state.screen else self._last_error,
        }


__all__ = ["Sts2Adapter"]
