"""玩具决斗插件入口：第二个游戏，不依赖 Steam / STS2 / 外部进程。

它故意和 STS2 走完全相同的核心契约：

- ``adapter.py`` 只读状态；
- ``engine.py`` 的 ``ToyTools`` 返回 ``ActionResult``；
- ``runner.py`` 只通过 ``AutoplayHost`` 写状态/日志/停止；
- ``prompt()`` 声明游戏自己的提示词，核心不内置任何玩具规则。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from dafeiyu.games.contracts import GameManifest, GameTools

from .engine import ToyTools


class ToyRunner:
    """把模块级循环包装成核心可调用的 runner。"""

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


class ToyPlugin:
    def __init__(self) -> None:
        raw = json.loads(Path(__file__).with_name("game.json").read_text(encoding="utf-8"))
        self.manifest = GameManifest.from_dict(
            raw,
            fallback_id="toy-duel",
            fallback_name="玩具决斗",
            fallback_adapter="adapter.py:ToyAdapter",
        )

    @property
    def runner_module(self):
        package = str(__package__ or "")
        if not package:
            raise RuntimeError("ToyPlugin 必须作为游戏包加载")
        return importlib.import_module(f"{package}.runner")

    def adapter(self, **kwargs):
        from .adapter import ToyAdapter

        return ToyAdapter(**kwargs)

    def tools(self) -> GameTools:
        return ToyTools(self.manifest.id)

    def runner(self, host=None):
        if not self.manifest.capabilities.autoplay:
            return None
        return ToyRunner(self.runner_module, host=host)

    def prompt(self, name: str) -> str:
        if name not in ("decision", "system"):
            return ""
        return (
            "你是《玩具决斗》的演示 AI。每回合根据手牌和能量输出 JSON："
            '{"seq": [{"play": "STRIKE"}, {"play": "GUARD"}, {"end": true}]}。'
            "只能使用手牌里存在的牌，能量不足时不要硬打；未知界面停下来，不要猜。"
        )

    def start_autoplay(self, game: str | None = None, *, mode: str | None = None,
                       pace: str | None = None) -> dict:
        runner = self.runner()
        if runner is None:
            return {"ok": False, "game": game or self.manifest.id,
                    "message": "插件没有声明自动玩能力"}
        return runner.start(game=game or self.manifest.id, mode=mode, pace=pace)

    def stop_autoplay(self) -> dict:
        return self.runner().stop()

    def status(self) -> dict:
        return self.runner().status()


__all__ = ["ToyPlugin", "ToyRunner"]
