"""只读适配器：把 Minecraft 桥的状态变成直播姬的 ``GAME_STATE``。

适配器只认真机桥（``http``）：仿真世界只用于自动玩/离线验收，不会让
``python main.py games`` 把「仿真在线」当成真游戏在跑。桥没连上时返回空状态，
不会伪造血量，也不会退化成窗口/键盘。

加载方式有两种：作为游戏包的一部分（``plugin.adapter()``）时用相对 import；
被 ``GameRegistry.create_adapter()`` 按文件路径单独加载时没有父包，这里用
``_sibling()`` 把 ``engine`` 挂到同一个合成包名下，保证相对 import 仍成立。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from dafeiyu.games.base import GameAdapter, GameState

PACKAGE = "dafeiyu_game_minecraft"
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
    """按合成包名加载兄弟模块，让 engine 里的相对 import 能工作。"""
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


ENGINE = _engine()
GAME_ID = ENGINE.GAME_ID
DISPLAY_NAME = "我的世界"


class McAdapter(GameAdapter):
    """真机桥只读状态；``source=http`` 才算在线。"""

    game_id = GAME_ID
    display_name = DISPLAY_NAME

    def __init__(self, tools=None) -> None:
        self._tools = tools or ENGINE.MinecraftTools(mode="http")

    def _context(self) -> dict:
        try:
            return self._tools.snapshot()
        except Exception:  # noqa: BLE001
            return ENGINE.context({}, source="offline")

    def available(self) -> bool:
        return str(self._context().get("source") or "") == "http"

    def snapshot(self) -> GameState:
        ctx = self._context()
        if not ctx.get("screen"):
            return GameState(screen="")
        online = str(ctx.get("source") or "") == "http"
        nearby = ctx.get("nearby") or {}
        mobs = nearby.get("mobs") or []
        return GameState(
            screen=str(ctx.get("screen") or ""),
            floor=int(ctx.get("day") or 0) if online else None,
            hp=int(ctx.get("hp") or 0) if online else None,
            max_hp=int(ctx.get("max_hp") or 0) if online else None,
            gold=None,
            character=str(ctx.get("player_name") or ""),
            in_combat=bool(online and any(mob.get("hostile") for mob in mobs)),
            turn=int(ctx.get("day") or 0) if online else None,
            extra={
                "day": int(ctx.get("day") or 0),
                "clock": str(ctx.get("clock") or ""),
                "phase": str(ctx.get("phase") or ""),
                "food": int(ctx.get("food") or 0),
                "shelter": float(ctx.get("shelter") or 0.0),
                "difficulty": str(ctx.get("difficulty") or ""),
                "source": str(ctx.get("source") or ""),
                "bridge": dict(ctx.get("bridge") or {}),
            },
        )

    def status(self) -> dict:
        ctx = self._context()
        return {
            "game_id": self.game_id,
            "name": self.display_name,
            "available": str(ctx.get("source") or "") == "http",
            "state": self.snapshot().to_dict(),
            "source": ctx.get("source"),
            "bridge": dict(ctx.get("bridge") or {}),
        }


__all__ = ["McAdapter"]
