"""游戏列表：读 `games/registry.json`，按 id 装配适配器。"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths
from .base import GameAdapter, GameInfo
from .contracts import GameManifest, ManifestIssue


@dataclass
class GamePack:
    info: GameInfo
    root: Path | None = None
    manifest: dict = field(default_factory=dict)
    contract: GameManifest | None = None
    manifest_issues: tuple[ManifestIssue, ...] = ()

    def to_dict(self) -> dict:
        out = self.info.to_dict()
        out["root"] = paths.rel(self.root) if self.root else ""
        out["manifest"] = self.manifest
        out["contract"] = self.contract.to_dict() if self.contract else {}
        out["manifest_issues"] = [issue.to_dict() for issue in self.manifest_issues]
        return out


class GameRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.games_dir = Path(root) if root else paths.GAMES
        self.registry_file = self.games_dir / "registry.json"
        self._raw = self._load()
        self.last_plugin_error = ""

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
            if not isinstance(manifest, dict):
                manifest = {}
            contract = GameManifest.from_dict(
                manifest,
                fallback_id=info.id,
                fallback_name=info.name,
                fallback_adapter=info.adapter,
            )
            out.append(GamePack(
                info=info,
                root=root,
                manifest=manifest,
                contract=contract,
                manifest_issues=contract.validate(),
            ))
        return out

    def get(self, game_id: str) -> GamePack | None:
        for pack in self.packs():
            if pack.info.id == game_id:
                return pack
        return None

    def list(self) -> list[dict]:
        return [p.to_dict() for p in self.packs()]

    # ---------------------------------------------------------------- 插件
    def load_game_module(self, game_id: str, module: str = "plugin"):
        """按游戏包路径加载模块，并给它一个稳定的合成包名。

        目录名可能带连字符，不能当普通 Python 包名；
        用 ``dafeiyu_game_<id>`` 作为合成包，子模块的相对 import 才能工作。
        """
        pack = self.get(game_id)
        if pack is None or pack.root is None:
            return None
        name = str(module or "").strip().replace("\\", "/")
        if name.endswith(".py"):
            name = name[:-3]
        name = name.replace("/", ".").strip(".")
        package_name = f"dafeiyu_game_{pack.info.id.replace('-', '_').replace('.', '_')}"
        package = sys.modules.get(package_name)
        if package is None:
            package = types.ModuleType(package_name)
            package.__path__ = [str(pack.root)]
            package.__package__ = package_name
            sys.modules[package_name] = package
        if not name:
            return package

        parts = name.split(".")
        for index in range(1, len(parts)):
            parent_name = f"{package_name}.{'.'.join(parts[:index])}"
            if parent_name in sys.modules:
                continue
            parent_path = pack.root.joinpath(*parts[:index]) / "__init__.py"
            if not parent_path.is_file():
                return None
            self._load_file(parent_name, parent_path)

        full_name = f"{package_name}.{name}"
        if full_name in sys.modules:
            return sys.modules[full_name]
        path = pack.root.joinpath(*parts)
        if path.is_dir():
            path = path / "__init__.py"
        else:
            path = path.with_suffix(".py")
        if not path.is_file():
            return None
        return self._load_file(full_name, path)

    @staticmethod
    def _load_file(full_name: str, path: Path):
        spec = importlib.util.spec_from_file_location(full_name, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[full_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(full_name, None)
            raise
        return module

    def load_plugin(self, game_id: str):
        """加载 game.json 的 entry；没有显式 plugin 入口时返回 None。"""
        self.last_plugin_error = ""
        pack = self.get(game_id)
        if pack is None or pack.contract is None:
            self.last_plugin_error = f"没有注册游戏：{game_id}"
            return None
        if pack.contract.entry_fallback:
            self.last_plugin_error = f"{game_id} 仍是 adapter 入口，没有 plugin"
            return None
        entry = pack.contract.entry
        module_name, _, attr = entry.partition(":")
        module_name, attr = module_name.strip(), attr.strip()
        if not module_name:
            self.last_plugin_error = f"{game_id} 的 entry 为空"
            return None
        try:
            if module_name.endswith(".py") or "/" in module_name or "\\" in module_name:
                module = self.load_game_module(game_id, module_name)
            else:
                module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            self.last_plugin_error = f"{type(exc).__name__}: {exc}"
            return None
        if module is None:
            self.last_plugin_error = f"插件模块不存在：{module_name}"
            return None
        cls = getattr(module, attr or "Plugin", None)
        if cls is None:
            self.last_plugin_error = f"插件入口缺少类：{attr or 'Plugin'}"
            return None
        try:
            return cls()
        except Exception as exc:  # noqa: BLE001
            self.last_plugin_error = f"{type(exc).__name__}: {exc}"
            return None

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
