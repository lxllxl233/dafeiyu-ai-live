"""游戏列表：读 `games/registry.json`，按 id 装配适配器。"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths
from .base import GameAdapter, GameInfo


@dataclass
class GamePack:
    info: GameInfo
    root: Path | None = None
    manifest: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = self.info.to_dict()
        out["root"] = paths.rel(self.root) if self.root else ""
        out["manifest"] = self.manifest
        return out


class GameRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.games_dir = Path(root) if root else paths.GAMES
        self.registry_file = self.games_dir / "registry.json"
        self._raw = self._load()

    def _load(self) -> dict:
        try:
            raw = json.loads(self.registry_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {"games": []}
        if isinstance(raw, list):
            raw = {"games": raw}
        raw.setdefault("games", [])
        return raw

    # ---------------------------------------------------------------- 查询
    def packs(self) -> list[GamePack]:
        out: list[GamePack] = []
        for raw in self._raw.get("games", []):
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            info = GameInfo(
                id=str(raw["id"]),
                name=str(raw.get("name") or raw["id"]),
                platform=str(raw.get("platform") or ""),
                status=str(raw.get("status") or "ready"),
                tags=tuple(str(t) for t in raw.get("tags") or ()),
                notes=str(raw.get("notes") or ""),
                adapter=str(raw.get("adapter") or ""),
                pack=str(raw.get("pack") or ""),
            )
            root = self.games_dir / info.pack if info.pack else None
            manifest = {}
            if root and (root / "game.json").exists():
                try:
                    manifest = json.loads((root / "game.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    manifest = {}
            out.append(GamePack(info, root, manifest))
        return out

    def get(self, game_id: str) -> GamePack | None:
        for pack in self.packs():
            if pack.info.id == game_id:
                return pack
        return None

    def list(self) -> list[dict]:
        return [p.to_dict() for p in self.packs()]

    # ---------------------------------------------------------------- 装配
    def create_adapter(self, game_id: str, **kwargs) -> GameAdapter | None:
        pack = self.get(game_id)
        if not pack or not pack.info.adapter:
            return None
        module_name, _, attr = pack.info.adapter.partition(":")
        module = self._load_module(pack, module_name)
        if module is None:
            return None
        cls = getattr(module, attr or "Adapter", None)
        if cls is None:
            return None
        try:
            return cls(**kwargs)  # type: ignore[call-arg]
        except TypeError:
            return cls()

    @staticmethod
    def _load_module(pack: GamePack, module_name: str):
        """支持两种写法：`dafeiyu.games.demo:DemoAdapter` 或
        `game-skill/adapter.py:MyAdapter`（游戏包自己的技能层，不进 src）。"""
        if module_name.endswith(".py") or "/" in module_name or "\\" in module_name:
            if pack.root is None:
                return None
            path = (pack.root / module_name).resolve()
            if not path.is_file():
                return None
            mod_name = f"dafeiyu_game_{pack.info.id.replace('-', '_')}_adapter"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(mod_name, None)
                return None
            return module
        try:
            return importlib.import_module(module_name)
        except ImportError:
            return None


__all__ = ["GamePack", "GameRegistry"]
