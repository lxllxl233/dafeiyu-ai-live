"""P6 迁移门禁：tools/ 里的旧入口只是 shim，真实现在游戏包里。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DECISION = ROOT / "games" / "slay-the-spire-2" / "decision"
COMPAT = ROOT / "games" / "slay-the-spire-2" / "compat"

MOVED = (
    "ctx", "act", "advisor", "sim", "gates", "dps", "path",
    "noncombat", "mech", "decision_log", "hard_rules", "lesson_index", "perf",
)


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def test_tools_shims_load_implementations_from_the_game_package():
    lines = ["import json, sys", f"sys.path.insert(0, {str(TOOLS)!r})"]
    lines.extend(f"import {name}" for name in MOVED)
    lines.append("import sts2")
    lines.append(
        "print('RESULT:' + json.dumps({"
        "name: sys.modules[name].__file__ for name in "
        f"{list(MOVED) + ['sts2']!r}"
        "}, ensure_ascii=False))"
    )
    result = _run("\n".join(lines))
    payload = next(
        line[len("RESULT:"):] for line in result.stdout.splitlines()
        if line.startswith("RESULT:")
    )
    paths = {name: Path(path).resolve() for name, path in json.loads(payload).items()}

    for name in MOVED:
        assert paths[name] == (DECISION / f"{name}.py").resolve(), name
    assert paths["sts2"] == (COMPAT / "sts2.py").resolve()


def test_moved_engine_files_and_thin_shims_exist():
    for name in MOVED:
        assert (DECISION / f"{name}.py").is_file(), name
        shim = (TOOLS / f"{name}.py").read_text(encoding="utf-8")
        assert "from _game_impl import load" in shim, name
        assert len(shim) < 1000, name


def test_tools_ctx_entry_still_answers_help():
    result = subprocess.run(
        [sys.executable, str(TOOLS / "ctx.py"), "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    assert "局面包" in result.stdout
