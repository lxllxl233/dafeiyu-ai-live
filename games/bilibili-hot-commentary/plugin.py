"""B 站云观影插件入口：核心只通过这个文件拿到适配器和运行器。"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from dafeiyu.games.contracts import GameManifest, GameTools


class BiliHotRunner:
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
        return self._module.start(game or self._module.GAME_ID, mode=mode, pace=pace)

    def stop(self) -> dict:
        if not self._active():
            self._bind()
        return self._module.stop()

    def status(self) -> dict:
        if not self._active():
            self._bind()
        return self._module.status()


class BiliHotPlugin:
    def __init__(self) -> None:
        raw = json.loads(Path(__file__).with_name("game.json").read_text(encoding="utf-8"))
        self.manifest = GameManifest.from_dict(
            raw,
            fallback_id="bilibili-hot-commentary",
            fallback_name="B站热门视频云观影",
            fallback_adapter="adapter.py:BiliHotAdapter",
        )

    @property
    def runner_module(self):
        package = str(__package__ or "")
        if not package:
            raise RuntimeError("BiliHotPlugin 必须作为游戏包加载")
        return importlib.import_module(f"{package}.runner")

    def adapter(self, **kwargs):
        from .adapter import BiliHotAdapter

        return BiliHotAdapter(**kwargs)

    def tools(self) -> GameTools | None:
        # 云观影没有可执行控制面：不实现 GameTools。
        return None

    def runner(self, host=None):
        if not self.manifest.capabilities.autoplay:
            return None
        return BiliHotRunner(self.runner_module, host=host)

    def prompt(self, name: str) -> str:
        if name not in ("commentary", "system"):
            return ""
        from .persona_watch import load_bili_persona
        from .prompts import commentary_system

        return commentary_system(load_bili_persona())


__all__ = ["BiliHotPlugin", "BiliHotRunner"]
