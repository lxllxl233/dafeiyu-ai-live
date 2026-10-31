"""开局设置：角色 + 进阶难度。

声明在 ``game.json`` 的 ``start`` 块（控制台按它渲染），用户选择存在
``config.json`` 的 ``start_options["slay-the-spire-2"]``。运行中的 runner 在
**每次开新局前**读一次（``load``），所以控制台改完不用重启进程，下一局就生效；
已经在打的这一局不会被改写。

核心只按声明清洗（见 ``dafeiyu.games.start_options``），这里补两件游戏包才知道的事：
默认值兜底，以及 game.json 的 mtime 缓存（每步都读一次 manifest 没必要）。
"""

from __future__ import annotations

import json
from pathlib import Path

from dafeiyu.games import start_options as core

GAME_ID = "slay-the-spire-2"
MANIFEST = Path(__file__).with_name("game.json")
# game.json 缺 start 块（旧包/文件坏掉）时的兜底，和升级前的行为一致：铁甲 + A10。
FALLBACK = {"character": "IRONCLAD", "ascension": 10}

_cache: dict = {"sig": None, "spec": {}}


def spec() -> dict:
    """game.json 的 start 声明；按 mtime 缓存。"""
    try:
        stat = MANIFEST.stat()
        sig = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    if _cache["sig"] != sig:
        try:
            raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        _cache["spec"] = core.spec_from_manifest(raw)
        _cache["sig"] = sig
    return dict(_cache["spec"])


def load() -> dict:
    """当前生效的开局设置；config.json 读不了也回默认值。"""
    try:
        values = core.load_values(GAME_ID, spec())
    except Exception:  # noqa: BLE001 - 读配置失败不该挡住开局
        values = {}
    return {**FALLBACK, **values}


def save(values: dict) -> dict:
    """按声明清洗后写回 config.json，返回实际保存的值。"""
    return core.store_values(GAME_ID, spec(), values)


__all__ = ["FALLBACK", "GAME_ID", "load", "save", "spec"]
