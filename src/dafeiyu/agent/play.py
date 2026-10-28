"""自动玩 facade：核心只负责找游戏插件并转发 start/stop/status。

具体整局循环、界面策略和动作执行属于对应游戏包。P1 先保持原来的
``start_autoplay / stop_autoplay / status`` 公开形状，UI、CLI 和右键菜单不用改。
"""

from __future__ import annotations

import json
from typing import Any

from .. import paths
from ..core.config import LiveConfig
from .host import AutoplayHost

STATUS_PATH = paths.LOGS / "autoplay.json"
STOP_PATH = paths.LOGS / "autoplay.stop"


def _read_status() -> dict:
    try:
        raw = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _game_id(game: str | None = None) -> str:
    """优先读运行中的状态文件，避免切换 config.game 后停错插件。"""
    if game:
        return str(game).strip()
    current = _read_status().get("game")
    if current:
        return str(current).strip()
    return str(LiveConfig.load().game or "").strip()


def _plugin(game: str | None = None):
    game_id = _game_id(game)
    if not game_id:
        return None, game_id
    try:
        from ..games.registry import GameRegistry

        return GameRegistry().load_plugin(game_id), game_id
    except Exception:
        return None, game_id


def _runner(plugin, game_id: str):
    """每次控制都新建一个宿主；跨进程状态仍然靠文件，内存不共享。"""
    return plugin.runner(host=AutoplayHost(game_id))


def _empty_status(game_id: str = "", error: str = "") -> dict[str, Any]:
    return {
        "running": False, "screen": "", "floor": 0, "hp": 0, "max_hp": 0,
        "step": 0, "last": "", "error": error, "game": game_id,
        "mode": "", "pace": "", "stopping": False, "pid": 0,
        "started_at": 0.0, "updated_at": 0.0,
        "recording": False, "record_path": "", "record_error": "",
        "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
        "tokens_last": 0, "llm_calls": 0,
        "route_seq": "", "route_option": None, "route_note": "", "route_floor": 0,
        "stop_requested": STOP_PATH.exists(),
    }


def start_autoplay(game: str | None = None, *, mode: str | None = None,
                   pace: str | None = None) -> dict:
    """启动游戏并开始自动玩；实际循环由游戏插件提供。"""
    plugin, game_id = _plugin(game)
    if plugin is None:
        return {"ok": False, "game": game_id,
                "message": f"当前游戏没有可自动玩的插件：{game_id or '未配置'}"}
    runner = _runner(plugin, game_id)
    if runner is None:
        return {"ok": False, "game": game_id,
                "message": f"游戏插件没有声明自动玩能力：{game_id}"}
    return runner.start(game=game_id, mode=mode, pace=pace)


def stop_autoplay() -> dict:
    """跨进程停手；由当前局所属的游戏插件执行收尾。"""
    plugin, game_id = _plugin()
    if plugin is None:
        if _read_status().get("running"):
            return {"ok": False, "game": game_id,
                    "message": f"找不到正在运行的游戏插件：{game_id or '未配置'}"}
        return {"ok": True, "message": "已停手", "status": _empty_status(game_id)}
    runner = _runner(plugin, game_id)
    if runner is None:
        if _read_status().get("running"):
            return {"ok": False, "game": game_id,
                    "message": f"游戏插件没有声明自动玩能力：{game_id}"}
        return {"ok": True, "message": "已停手", "status": _empty_status(game_id)}
    return runner.stop()


def status() -> dict:
    """当前自动玩状态；没有插件时返回稳定的空状态，不抛给 UI。

    游戏列表里的纯观察游戏（如 demo）本来就没有自动玩，空闲时不应在控制台
    显示错误；真正的启动失败由 ``start_autoplay`` 的 message 说明。
    """
    plugin, game_id = _plugin()
    if plugin is None:
        return _empty_status(game_id)
    runner = _runner(plugin, game_id)
    if runner is None:
        return _empty_status(game_id)
    return runner.status()


__all__ = ["start_autoplay", "stop_autoplay", "status"]
