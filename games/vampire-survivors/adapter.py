"""只读适配器：把《吸血鬼幸存者》的本地 API 状态变成直播姬的 GAME_STATE。

适配器不启动游戏、不发按键；桥没连上时返回空状态（离线），不会伪造血量。
加载方式有两种：作为游戏包的一部分（``plugin.adapter()``）时用相对 import；
被 ``GameRegistry.create_adapter()`` 按文件路径单独加载时没有父包，这里用
``_sibling()`` 把 ``engine`` 挂到同一个合成包名下，保证相对 import 仍然成立。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from dafeiyu.games.base import GameAdapter, GameState

PACKAGE = "dafeiyu_game_vampire_survivors"
_HERE = Path(__file__).resolve().parent


def _ensure_package() -> str:
    package = sys.modules.get(PACKAGE)
    if package is None:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(_HERE)]
        package.__package__ = PACKAGE
        sys.modules[PACKAGE] = package
    return PACKAGE


def _sibling(name: str):
    """按合成包名加载兄弟模块，让引擎里的相对 import 能工作。"""
    package = _ensure_package()
    full_name = f"{package}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    path = _HERE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(full_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"找不到游戏包模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(full_name, None)
        raise
    return module


def _engine():
    try:
        from . import engine  # 作为游戏包的一部分加载

        return engine
    except ImportError:
        return _sibling("engine")


GAME_ID = _engine().GAME_ID
DISPLAY_NAME = "吸血鬼幸存者"


class VampireSurvivorsAdapter(GameAdapter):
    game_id = GAME_ID
    display_name = DISPLAY_NAME

    def __init__(self, tools=None) -> None:
        engine = _engine()
        self._tools = tools or engine.VampireSurvivorsTools()

    def _context(self) -> dict:
        try:
            return self._tools.snapshot()
        except Exception:
            return _engine().context({}, {"ok": False})

    def available(self) -> bool:
        return str(self._context().get("source") or "") == "api"

    def snapshot(self) -> GameState:
        ctx = self._context()
        if not ctx.get("screen"):
            return GameState(screen="")
        online = str(ctx.get("source") or "") == "api"
        return GameState(
            screen=str(ctx.get("screen") or ""),
            floor=int(ctx.get("level") or 0) if online else None,
            hp=int(ctx.get("hp") or 0) if online else None,
            max_hp=int(ctx.get("max_hp") or 0) if online else None,
            gold=int(ctx.get("gold") or 0) if online else None,
            character=str(ctx.get("character") or ""),
            in_combat=str(ctx.get("screen") or "") == "PLAYING",
            turn=int(ctx.get("level") or 0) if online else None,
            extra={
                "kills": int(ctx.get("kills") or 0),
                "stage": str(ctx.get("stage") or ""),
                "time_s": float(ctx.get("time_s") or 0.0),
                "source": str(ctx.get("source") or ""),
                "api": dict(ctx.get("api") or {}),
            },
        )

    def status(self) -> dict:
        ctx = self._context()
        return {
            "game_id": self.game_id,
            "name": self.display_name,
            "available": str(ctx.get("source") or "") == "api",
            "state": self.snapshot().to_dict(),
            "source": ctx.get("source"),
            "api": dict(ctx.get("api") or {}),
        }


__all__ = ["VampireSurvivorsAdapter"]
