"""仓库路径解析：所有层都从这里取目录，不再各自 `dirname(__file__)`。

设置环境变量 ``DAFEIYU_HOME`` 可以把仓库挂到别处（例如打包后放到数据目录）。
"""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """仓库根目录（含 ui/、persona/、voice/、games/、src/ 的那一层）。"""
    env = os.environ.get("DAFEIYU_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # src/dafeiyu/paths.py -> src/dafeiyu -> src -> 仓库根
    return Path(__file__).resolve().parents[2]


ROOT: Path = repo_root()
SRC: Path = ROOT / "src"
UI: Path = ROOT / "ui"
PERSONA: Path = ROOT / "persona"
VOICE: Path = ROOT / "voice"
GAMES: Path = ROOT / "games"
DOCS: Path = ROOT / "docs"
TOOLS: Path = ROOT / "tools"
LOGS: Path = ROOT / "logs"
CACHE: Path = LOGS / "tts_cache"
CONFIG_FILE: Path = ROOT / "config.json"


def ensure_dirs() -> None:
    """运行期需要的目录（logs 缓存等）在启动时建好。"""
    for p in (LOGS, CACHE):
        p.mkdir(parents=True, exist_ok=True)


def rel(path: os.PathLike[str] | str) -> str:
    """相对仓库根的展示路径，日志/JSON 里更好读。"""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return str(path)


__all__ = [
    "ROOT", "SRC", "UI", "PERSONA", "VOICE", "GAMES", "DOCS", "TOOLS", "LOGS", "CACHE",
    "CONFIG_FILE", "repo_root", "ensure_dirs", "rel",
]
