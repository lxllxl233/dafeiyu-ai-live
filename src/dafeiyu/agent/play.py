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


def _game_id(game: str | None = None, *, trust_status: bool = False) -> str:
    """当前该操作哪个游戏。

    - ``start`` 用默认值：以 ``config.game`` 为准。旧版本这里先读
      ``logs/autoplay.json`` 的 ``game``，而另一个插件跑完验收后这份状态文件会
      一直留着它的 id，于是「让她玩」会启动错的游戏，还提示该游戏不支持 llm 模式。
    - ``stop`` 用 ``trust_status=True``：停手要停状态文件里那一局，即使
      ``config.game`` 已经切走。
    - ``status`` 用默认值：只有状态文件说 ``running`` 时才信它的 ``game``，
      否则同样回落到 ``config.game``。
    """
    if game:
        return str(game).strip()
    state = _read_status()
    current = str(state.get("game") or "").strip()
    if trust_status and current:
        return current
    if state.get("running") and current:
        return current
    cfg_game = str(LiveConfig.load().game or "").strip()
    return cfg_game or current


def _plugin(game: str | None = None, *, trust_status: bool = False):
    game_id = _game_id(game, trust_status=trust_status)
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
    plugin, game_id = _plugin(game, trust_status=False)
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
    plugin, game_id = _plugin(trust_status=True)
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


_USAGE_HOST: AutoplayHost | None = None


def token_usage(limit: int = 40) -> dict:
    """API Token 账单（跨进程读 ``logs/autoplay.jsonl``）：总数 + 每步明细。

    控制台「Agent 流程」用它；不依赖自动玩进程还活着。宿主只建一次，
    这样 3 秒轮询能命中 ``AutoplayHost`` 里的 (mtime, size) 缓存。
    """
    global _USAGE_HOST
    try:
        if _USAGE_HOST is None:
            _USAGE_HOST = AutoplayHost()
        return _USAGE_HOST.token_usage(limit=limit)
    except Exception as exc:  # noqa: BLE001
        return {"total": 0, "input": 0, "output": 0, "calls": 0, "steps": [],
                "kinds": [], "by_run": [], "error": f"{type(exc).__name__}: {exc}"}


__all__ = ["start_autoplay", "stop_autoplay", "status", "token_usage"]
