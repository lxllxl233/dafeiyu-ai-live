"""《我的世界》插件入口：和 toy-duel / VS / STS2 共用同一套核心契约。"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from dafeiyu.games.contracts import GameManifest, GameTools

from .engine import MinecraftTools


def build_manifest() -> GameManifest:
    raw = json.loads(Path(__file__).with_name("game.json").read_text(encoding="utf-8"))
    return GameManifest.from_dict(
        raw,
        fallback_id="minecraft",
        fallback_name="我的世界",
        fallback_adapter="adapter.py:McAdapter",
    )


class McRunner:
    """把模块级 runner 包装成核心可调用的 start/stop/status。"""

    def __init__(self, module, host=None) -> None:
        self._module = module
        self._host = host

    def _bind(self) -> None:
        if self._host is not None:
            self._module.bind_host(self._host)

    def _active(self) -> bool:
        thread = getattr(self._module, "_thread", None)
        return bool(thread is not None and thread.is_alive())

    def start(self, *, game: str = "", mode: str | None = None,
              pace: str | None = None) -> dict:
        self._bind()
        return self._module.start(game or self._module.GAME_ID,
                                  mode=mode, pace=pace)

    def stop(self) -> dict:
        if not self._active():
            self._bind()
        return self._module.stop()

    def status(self) -> dict:
        if not self._active():
            self._bind()
        return self._module.status()


class McPlugin:
    def __init__(self) -> None:
        self.manifest = build_manifest()

    @property
    def runner_module(self):
        package = str(__package__ or "")
        if not package:
            raise RuntimeError("McPlugin 必须作为游戏包加载")
        return importlib.import_module(f"{package}.runner")

    def adapter(self, **kwargs):
        from .adapter import McAdapter

        return McAdapter(**kwargs)

    def tools(self) -> GameTools:
        return MinecraftTools()

    def runner(self, host=None):
        if not self.manifest.capabilities.autoplay:
            return None
        return McRunner(self.runner_module, host=host)

    def prompt(self, name: str) -> str:
        if name not in ("decision", "system"):
            return ""
        from .prompts import DECISION_PROMPT

        return DECISION_PROMPT


__all__ = ["McPlugin", "McRunner", "build_manifest"]
