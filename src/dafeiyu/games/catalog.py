"""跨游戏的目录查询：默认游戏、别名、显示名和旧环境变量前缀。

核心不写死任何游戏 id；所有具体信息都来自 ``games/registry.json`` 与各游戏包的
``game.json``。这样加第二个游戏时，配置默认值、录像目录和旧环境变量兼容都跟着
manifest 走，不需要改内核。
"""

from __future__ import annotations

import os
import re

from .registry import GamePack, GameRegistry

_ALIAS_RE = re.compile(r"^[a-z][a-z0-9]*$")
_SAFE_RE = re.compile(r"[^a-z0-9._-]+")


def _packs() -> list[GamePack]:
    try:
        return GameRegistry().packs()
    except Exception:
        return []


def _keys(pack: GamePack) -> set[str]:
    keys = {pack.info.id.lower()}
    if pack.contract:
        keys.add(pack.contract.id.lower())
        keys.update(alias.lower() for alias in pack.contract.aliases)
    return {key for key in keys if key}


def default_game_id() -> str:
    """优先选“已就绪 + 能自动玩”的第一个游戏，否则退回第一个就绪游戏。"""
    packs = _packs()
    for pack in packs:
        caps = pack.contract.capabilities if pack.contract else None
        if pack.info.status == "ready" and caps and caps.autoplay:
            return pack.info.id
    for pack in packs:
        if pack.info.status == "ready":
            return pack.info.id
    return packs[0].info.id if packs else ""


def resolve_game_id(name: str | None = None) -> str:
    """把别名/大小写/路径安全形式解析成注册表里的游戏 id。"""
    raw = str(name or "").strip()
    if not raw:
        return default_game_id()
    key = raw.lower()
    for pack in _packs():
        if key in _keys(pack):
            return pack.info.id
    normalized = _SAFE_RE.sub("-", key).strip("-")
    return normalized or default_game_id()


def game_name(name: str | None = None) -> str:
    gid = resolve_game_id(name)
    for pack in _packs():
        if pack.info.id == gid:
            if pack.contract and pack.contract.name:
                return pack.contract.name
            return pack.info.name or gid
    return gid


def legacy_env_prefixes(name: str | None = None) -> tuple[str, ...]:
    """旧工程的环境变量前缀，例如别名 ``foo`` → ``FOO_*``。

    只认 manifest 里显式声明的 alias，不猜游戏 id 的缩写。这样旧配置能继续工作，
    新游戏也可以自己声明 ``aliases: ["foo"]`` 来接管 ``FOO_TTS_URL`` 之类的变量。
    """
    packs = _packs()
    if name:
        gid = resolve_game_id(name)
        packs = [p for p in packs if p.info.id == gid]
    out: list[str] = []
    for pack in packs:
        aliases = pack.contract.aliases if pack.contract else ()
        for alias in aliases:
            if _ALIAS_RE.fullmatch(alias) and alias.upper() not in out:
                out.append(alias.upper())
    return tuple(out)


def env_value(name: str, default: str = "", *, game: str | None = None) -> str:
    """读 ``DAFEIYU_<name>``，再退回游戏包声明的旧前缀。"""
    value = os.environ.get(f"DAFEIYU_{name}")
    if value not in (None, ""):
        return value
    for prefix in legacy_env_prefixes(game):
        value = os.environ.get(f"{prefix}_{name}")
        if value not in (None, ""):
            return value
    return default


__all__ = [
    "default_game_id", "resolve_game_id", "game_name",
    "legacy_env_prefixes", "env_value",
]
