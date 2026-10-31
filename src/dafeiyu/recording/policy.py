"""OBS 录像策略：每个游戏默认录不录、一局一集还是不分集。

策略来源分两层，后者覆盖前者：

1. ``games/<id>/game.json`` 的 ``recording`` 块（游戏包自己的默认值）；
2. ``config.json`` 的 ``recording.games.<id>``（后台/控制台保存的本机覆盖）。

``ensure_script`` 负责检查该游戏的 OBS 场景脚本（``game-skill/obs.json``），
没有就按游戏名生成一份最小配置。真正的场景/采集源创建仍由
``recording.obs.Obs.ensure_*`` 完成。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .. import paths

DEFAULT = {"enabled": False, "split": "none"}
SPLITS = ("run", "none")
SPLIT_LABELS = {"run": "一局一集", "none": "不分集"}
_GAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _require_game(game: str) -> str:
    gid = str(game or "").strip()
    if not _GAME_RE.match(gid):
        raise ValueError(f"游戏 id 不合法：{game!r}")
    return gid


def _clean_split(value: Any, fallback: str = "none") -> str:
    text = str(value or "").strip().lower()
    if text in ("run", "per_run", "episode", "one_run"):
        return "run"
    if text in ("none", "off", "continuous", "single"):
        return "none"
    return fallback if fallback in SPLITS else "none"


def normalize(raw: Any, *, fallback: dict | None = None) -> dict:
    base = dict(fallback or DEFAULT)
    raw = raw if isinstance(raw, dict) else {}
    enabled = raw.get("enabled", base.get("enabled", False))
    return {
        "enabled": bool(enabled),
        "split": _clean_split(raw.get("split"), str(base.get("split") or "none")),
    }


def _manifest(game: str) -> dict:
    try:
        path = paths.GAMES / str(game) / "game.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def default_for(game: str) -> dict:
    """游戏包声明的默认策略；没声明就是关闭 + 不分集。"""
    raw = _manifest(game).get("recording")
    return normalize(raw)


def _configured(cfg: Any = None) -> dict:
    if cfg is None:
        try:
            from ..core.config import LiveConfig

            cfg = LiveConfig.load()
        except Exception:  # noqa: BLE001 - 配置坏了也不能让录像设置 500
            cfg = None
    raw = getattr(cfg, "recording", None) if cfg is not None else None
    if not isinstance(raw, dict):
        return {}
    games = raw.get("games")
    if isinstance(games, dict):
        return games
    # 兼容直接写 {"game-id": {...}} 的早期配置。
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def policy_for(game: str, cfg: Any = None) -> dict:
    try:
        gid = _require_game(game)
    except ValueError:
        return {**DEFAULT, "game": str(game or ""), "default": dict(DEFAULT)}
    default = default_for(gid)
    override = _configured(cfg).get(gid)
    out = normalize(override, fallback=default) if isinstance(override, dict) else dict(default)
    out["game"] = gid
    out["default"] = dict(default)
    return out


def all_policies(cfg: Any = None) -> dict[str, dict]:
    """所有注册游戏的有效策略（控制台只展示当前游戏，但 API 可查全量）。"""
    out: dict[str, dict] = {}
    try:
        from ..games.registry import GameRegistry

        for pack in GameRegistry().packs():
            out[pack.info.id] = policy_for(pack.info.id, cfg)
    except Exception:  # noqa: BLE001
        pass
    for gid in _configured(cfg):
        out.setdefault(gid, policy_for(gid, cfg))
    return out


def master_enabled(cfg: Any = None) -> bool:
    if cfg is None:
        try:
            from ..core.config import LiveConfig

            cfg = LiveConfig.load()
        except Exception:  # noqa: BLE001
            return True
    return bool(getattr(cfg, "recording_enabled", True))


def set_master(enabled: bool) -> bool:
    from ..core.config import LiveConfig

    cfg = LiveConfig.load()
    cfg.save_patch({"recording_enabled": bool(enabled)})
    return bool(enabled)


def save_policy(game: str, *, enabled: bool | None = None,
                split: str | None = None, cfg: Any = None) -> dict:
    """保存某游戏策略到 config.json；只允许 run / none。"""
    gid = _require_game(game)
    from ..core.config import LiveConfig

    cfg = cfg or LiveConfig.load()
    current = policy_for(gid, cfg)
    new = {
        "enabled": bool(current["enabled"] if enabled is None else enabled),
        "split": _clean_split(current["split"] if split is None else split),
    }
    raw = cfg.recording if isinstance(cfg.recording, dict) else {}
    games_raw = raw.get("games") if isinstance(raw.get("games"), dict) else {}
    games = dict(games_raw)
    games[gid] = new
    cfg.save_patch({"recording": {"games": games}})
    return policy_for(gid, cfg)


def script_path(game: str) -> Path:
    gid = _require_game(game)
    return paths.GAMES / gid / "game-skill" / "obs.json"


def script_state(game: str) -> dict:
    try:
        gid = _require_game(game)
    except ValueError:
        return {"path": "", "exists": False, "error": "invalid game id"}
    path = script_path(gid)
    return {
        "path": f"games/{gid}/game-skill/obs.json",
        "exists": path.is_file(),
    }


def ensure_script(game: str) -> dict:
    """有 ``game-skill/obs.json`` 就复用，没有就按游戏名创建最小场景脚本。"""
    gid = _require_game(game)
    path = script_path(gid)
    state = script_state(gid)
    if path.is_file():
        return {**state, "created": False}

    manifest = _manifest(gid)
    if not manifest:
        raise ValueError(f"找不到游戏包：{gid}")
    name = str(manifest.get("name") or manifest.get("name_en") or gid)
    profile = {
        "scene": name[:48],
        "game_source": f"{name[:32]} Game",
        "window_source": f"{name[:32]} Window",
        "monitor_source": f"{name[:32]} Monitor",
        "audio_source": f"{name[:32]} Audio",
        "overlay_source": "AI Commentary",
        "ticker_source": "AI Ticker",
        "ticker_text": f"AI 实况 · {name[:32]}",
        "window_match": [name, gid],
        "capture_mode": "game",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return {**script_state(gid), "created": True, "profile": profile}
