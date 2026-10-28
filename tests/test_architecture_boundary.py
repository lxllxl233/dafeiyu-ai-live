"""架构门禁：核心协议层与 Agent 编排层不得再出现具体游戏标识。

`KNOWN_DEBT` 是迁移前的已知债务清单，不是永久白名单：
- 新文件出现游戏标识 → 失败；
- P1/P2 把某个文件迁走或清理后，这里必须同步缩小，否则也会失败。
这样核心边界只能单向收紧，不能悄悄重新长回来。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
SRC = SRC_ROOT / "dafeiyu"

TOKENS = {
    "game_id": "slay-the-spire-2",
    "sts2": "sts2",
    "ironclad": "ironclad",
    "kill_text": "杀戮尖塔",
    "card": "卡牌",
    "toy": "toy-duel",
    "toy_cn": "玩具决斗",
}

# 迁移债务；每搬走一项就同步删除，只能收紧不能回涨。
KNOWN_DEBT: dict[str, set[str]] = {}

# 这些文件是跨游戏协议/内核基础，必须从现在起保持游戏无关。
PROTOCOL_FILES = (
    "src/dafeiyu/games/contracts.py",
    "src/dafeiyu/games/base.py",
    "src/dafeiyu/games/catalog.py",
    "src/dafeiyu/games/registry.py",
    "src/dafeiyu/games/launch.py",
    "src/dafeiyu/core/events.py",
    "src/dafeiyu/core/bus.py",
    "src/dafeiyu/agent/__init__.py",
    "src/dafeiyu/agent/graph.py",
    "src/dafeiyu/agent/host.py",
    "src/dafeiyu/agent/planner.py",
    "src/dafeiyu/agent/play.py",
    "src/dafeiyu/agent/tools.py",
    "src/dafeiyu/persona/card.py",
    "src/dafeiyu/persona/pacing.py",
    "src/dafeiyu/persona/memes.py",
)


def _hits(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8", errors="replace").lower()
    return {name for name, token in TOKENS.items() if token.lower() in text}


def _actual_debt() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        hits = _hits(path)
        if hits:
            found[path.relative_to(ROOT).as_posix()] = hits
    return found


def test_only_known_game_identifier_debt_remains():
    assert _actual_debt() == KNOWN_DEBT


def test_protocol_files_stay_game_agnostic():
    dirty = {rel: _hits(ROOT / rel) for rel in PROTOCOL_FILES}
    assert {rel: hits for rel, hits in dirty.items() if hits} == {}


def test_importing_agent_play_does_not_eagerly_load_game_tools():
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(SRC_ROOT)!r})\n"
        "import dafeiyu.agent.play\n"
        "print('sts2' in sys.modules)\n"
        "print('advisor' in sys.modules)\n"
        "print('dafeiyu.games.contracts' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    loaded = result.stdout.splitlines()
    # contracts 是跨游戏协议，会随 agent.tools 的 ActionResult 类型一起加载；
    # 这里只要求具体游戏工具（sts2 / advisor）不被提前导入。
    assert loaded == ["False", "False", "True"]
