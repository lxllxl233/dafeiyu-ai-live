"""启动游戏：桌面右键、管理端和 Agent CLI 共用。

游戏自己的 `game.json` 里写 `launch`：

    {"type": "steam", "app_id": "2868840"}
    {"type": "exe", "path": "bin/game.exe", "args": ["--windowed"]}
    {"type": "url", "url": "https://example.com/game"}

只做「启动」，不替游戏做操作；已在运行时不会重复拉起。
"""
from __future__ import annotations

import os
import subprocess
import webbrowser
from pathlib import Path

from .registry import GameRegistry, GamePack


def _spec(pack: GamePack) -> dict:
    raw = pack.manifest.get("launch") if isinstance(pack.manifest, dict) else None
    if isinstance(raw, dict):
        return dict(raw)
    # 兼容直接在 game.json 顶层写 steam_appid 的老包。
    appid = (pack.manifest or {}).get("steam_appid")
    return {"type": "steam", "app_id": str(appid)} if appid else {}


def _already_running(registry: GameRegistry, game_id: str) -> bool:
    try:
        adapter = registry.create_adapter(game_id)
        return bool(adapter and adapter.available())
    except Exception:
        return False


def _launch_exe(spec: dict, root: Path | None) -> subprocess.Popen:
    raw = Path(os.path.expandvars(os.path.expanduser(str(spec.get("path") or ""))))
    if not raw.is_absolute() and root is not None:
        raw = root / raw
    if not raw.exists():
        raise FileNotFoundError(str(raw))
    args = spec.get("args") or []
    if isinstance(args, str):
        args = [args]
    cwd_raw = str(spec.get("cwd") or "").strip()
    if cwd_raw:
        cwd = Path(os.path.expandvars(os.path.expanduser(cwd_raw)))
        if not cwd.is_absolute() and root is not None:
            cwd = root / cwd
    else:
        cwd = raw.parent
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(
        [str(raw), *[str(a) for a in args]],
        cwd=str(cwd), creationflags=flags,
    )


def _launch_steam(app_id: str) -> str:
    if not str(app_id or "").strip():
        raise ValueError("steam launch 缺少 app_id")
    uri = f"steam://rungameid/{str(app_id).strip()}"
    if os.name == "nt":
        os.startfile(uri)  # type: ignore[attr-defined]
    else:
        webbrowser.open(uri)
    return uri


def launch_game(game_id: str | None = None, registry: GameRegistry | None = None, *,
                force: bool = False) -> dict:
    """按当前游戏配置启动；返回结构化结果，不抛异常给 UI。"""
    registry = registry or GameRegistry()
    game_id = str(game_id or "").strip()
    if not game_id:
        return {"ok": False, "game": "", "error": "没有指定游戏"}
    pack = registry.get(game_id)
    if pack is None:
        return {"ok": False, "game": game_id, "error": f"没有注册游戏：{game_id}"}

    spec = _spec(pack)
    if not spec:
        return {"ok": False, "game": game_id, "name": pack.info.name,
                "error": "该游戏未配置启动方式（在 game.json 写 launch）"}
    if not force and _already_running(registry, game_id):
        return {"ok": True, "game": game_id, "name": pack.info.name, "already": True,
                "message": f"{pack.info.name} 已在运行"}

    kind = str(spec.get("type") or "").strip().lower()
    try:
        if kind == "steam":
            uri = _launch_steam(str(spec.get("app_id") or ""))
            return {"ok": True, "game": game_id, "name": pack.info.name,
                    "type": "steam", "message": f"已请求 Steam 启动 {pack.info.name}",
                    "uri": uri}
        if kind == "exe":
            proc = _launch_exe(spec, pack.root)
            return {"ok": True, "game": game_id, "name": pack.info.name,
                    "type": "exe", "pid": proc.pid,
                    "message": f"已启动 {pack.info.name}（pid {proc.pid}）"}
        if kind in ("url", "uri"):
            url = str(spec.get("url") or spec.get("uri") or "").strip()
            if not url:
                raise ValueError("url launch 缺少 url")
            webbrowser.open(url)
            return {"ok": True, "game": game_id, "name": pack.info.name,
                    "type": "url", "message": f"已打开 {url}"}
        return {"ok": False, "game": game_id, "name": pack.info.name,
                "error": f"不支持的启动类型：{kind or '空'}"}
    except Exception as exc:
        return {"ok": False, "game": game_id, "name": pack.info.name,
                "error": f"{type(exc).__name__}: {exc}"}


__all__ = ["launch_game"]
