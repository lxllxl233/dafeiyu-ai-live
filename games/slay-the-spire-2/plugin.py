"""《杀戮尖塔 2》插件入口：核心只通过这个文件拿到适配器、工具和自动玩循环。

加载器会把这个文件作为 ``dafeiyu_game_<id>.plugin`` 执行，因此下面的相对
import 只在游戏包上下文里解析；核心 ``src/dafeiyu`` 不需要知道这些模块。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from dafeiyu.games.contracts import GameManifest, GameTools


class Sts2Runner:
    """把游戏包里的模块级自动玩循环包装成核心可调用的 runner。"""

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
        return self._module.start_autoplay(
            game or self._module.GAME_ID, mode=mode, pace=pace)

    def stop(self) -> dict:
        # 同进程正在跑时必须沿用原 host：录像会话和停止事件都在它身上。
        if not self._active():
            self._bind()
        return self._module.stop_autoplay()

    def status(self) -> dict:
        if not self._active():
            self._bind()
        return self._module.status()


class Sts2Plugin:
    """游戏包入口；状态/停止/录像/节奏由核心 AutoplayHost 负责。"""

    def __init__(self) -> None:
        raw = json.loads(Path(__file__).with_name("game.json").read_text(encoding="utf-8"))
        self.manifest = GameManifest.from_dict(
            raw,
            fallback_id="slay-the-spire-2",
            fallback_name="杀戮尖塔 2",
            fallback_adapter="game-skill/adapter.py:Sts2Adapter",
        )

    @property
    def runner_module(self):
        package = str(__package__ or "")
        if not package:
            raise RuntimeError("Sts2Plugin 必须作为游戏包加载")
        return importlib.import_module(f"{package}.autoplay.runner")

    def adapter(self, **kwargs):
        # 适配器仍由 registry 按 game.json 的 adapter 字段加载；这里只是协议入口。
        from dafeiyu.games.registry import GameRegistry

        return GameRegistry().create_adapter(self.manifest.id, **kwargs)

    def tools(self) -> GameTools:
        from .decision.agent_tools import Sts2Tools

        return Sts2Tools(self.manifest.id)

    def runner(self, host=None):
        if not self.manifest.capabilities.autoplay:
            return None
        return Sts2Runner(self.runner_module, host=host)

    def prompt(self, name: str) -> str:
        if name not in ("decision", "system"):
            return ""
        from .decision.prompts import SYSTEM_PROMPT

        return SYSTEM_PROMPT

    def start_autoplay(self, game: str | None = None, *, mode: str | None = None,
                       pace: str | None = None) -> dict:
        runner = self.runner()
        if runner is None:
            return {"ok": False, "game": game or self.manifest.id,
                    "message": "插件没有声明自动玩能力"}
        return runner.start(game=game or self.manifest.id, mode=mode, pace=pace)

    def stop_autoplay(self) -> dict:
        runner = self.runner()
        if runner is None:
            return {"ok": False, "message": "插件没有声明自动玩能力"}
        return runner.stop()

    def status(self) -> dict:
        runner = self.runner()
        if runner is None:
            return {"running": False, "game": self.manifest.id, "error": "没有自动玩能力"}
        return runner.status()


__all__ = ["Sts2Plugin", "Sts2Runner"]
