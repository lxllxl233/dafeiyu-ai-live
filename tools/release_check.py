#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布门禁：版本同步、单元测试、契约/文档/编译检查。

    python tools/release_check.py                 # 本地默认
    python tools/release_check.py --full          # 加 v1 工具全量回归
    python tools/release_check.py --ci            # GitHub Actions 轻量组合
    python tools/release_check.py --metadata-only # 只查版本串

不访问游戏、不调用模型、不读 API Key；失败返回 1。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _one(pattern: str, text: str) -> str:
    found = re.findall(pattern, text or "")
    return found[0] if found else ""


def _same_version(left: str, right: str) -> bool:
    """README 允许 ``v2.2`` 简写，其余位置要求完整版本号。"""
    left, right = str(left or "").strip(), str(right or "").strip()
    if not left or not right:
        return False
    return (left == right or left.startswith(right + ".")
            or right.startswith(left + "."))


def version_info() -> dict:
    pyproject = _one(r'(?m)^version\s*=\s*"([^"]+)"', _read(ROOT / "pyproject.toml"))
    package = _one(r'__version__\s*=\s*"([^"]+)"', _read(ROOT / "src/dafeiyu/__init__.py"))
    readme = _one(r"(?m)^`v(\d+\.\d+(?:\.\d+)?)`", _read(ROOT / "README.md"))
    try:
        game = str(json.loads(_read(ROOT / "games/slay-the-spire-2/game.json")).get("version") or "")
    except (TypeError, ValueError):
        game = ""
    changelog = re.findall(r"(?m)^##\s+\[([0-9]+\.[0-9]+\.[0-9]+)\]",
                           _read(ROOT / "CHANGELOG.md"))
    return {
        "expected": package,
        "package": package,
        "pyproject": pyproject,
        "readme": readme,
        "game": game,
        "changelog": changelog,
    }


def version_issues(info: dict | None = None) -> list[str]:
    info = info or version_info()
    expected = info["expected"]
    issues: list[str] = []
    if not expected:
        issues.append("src/dafeiyu/__init__.py 缺少 __version__")
    for key, label in (("pyproject", "pyproject.toml"),
                       ("package", "src/dafeiyu/__init__.py"),
                       ("game", "games/slay-the-spire-2/game.json")):
        if info[key] != expected:
            issues.append(f"{label}: {info[key] or '(空)'} != {expected or '(空)'}")
    if not _same_version(info["readme"], expected):
        issues.append(f"README.md: v{info['readme'] or '(空)'} != v{expected}")
    if expected not in info["changelog"]:
        issues.append(f"CHANGELOG.md 缺少 ## [{expected}]")
    return issues


def _run(label: str, cmd: list[str], *, capture: bool = False) -> bool:
    print(f"\n=== {label} ===")
    try:
        proc = subprocess.run(
            cmd, cwd=str(ROOT), env=ENV,
            capture_output=capture, text=True,
        )
    except OSError as exc:
        print(f"FAIL 无法执行：{exc}")
        return False
    if proc.returncode != 0:
        print(f"FAIL exit={proc.returncode}")
        if capture and proc.stdout:
            print(proc.stdout[-2000:])
        if capture and proc.stderr:
            print(proc.stderr[-2000:])
        return False
    print("PASS")
    return True


def run_checks(*, ci: bool = False, full: bool = False) -> list[str]:
    failures: list[str] = []
    info = version_info()
    print("版本：", info["expected"] or "(未取到)")
    print("  pyproject :", info["pyproject"] or "(空)")
    print("  package   :", info["package"] or "(空)")
    print("  README    :", info["readme"] or "(空)")
    print("  game.json :", info["game"] or "(空)")
    issues = version_issues(info)
    if issues:
        failures.extend(issues)
        for issue in issues:
            print("FAIL", issue)
    else:
        print("PASS 版本串同步")

    py = sys.executable
    commands = [
        ("单元测试", [py, "-m", "pytest", "-q", "tests"]),
        ("导入检查", [py, "tools/test_imports.py"]),
        ("ctx/act 回归", [py, "tools/test_ctx_act.py"]),
        ("文档一致性", [py, "tools/test_docs_consistency.py"]),
        ("skill 校验", [py, "tools/check_skill.py"]),
        ("编译", [py, "-m", "compileall", "-q", "src/dafeiyu", "games", "tools", "tests"]),
    ]
    if full:
        commands.append(("v1 工具全量回归", [py, "tools/test_all.py"]))
    for label, cmd in commands:
        if not _run(label, cmd):
            failures.append(label)

    if not ci:
        if (ROOT / ".git").exists():
            if not _run("git diff --check", ["git", "diff", "--check"], capture=True):
                failures.append("git diff --check")
        else:
            print("\nSKIP git diff --check（不是 git 仓库）")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="大肥鱼直播姬发布门禁")
    parser.add_argument("--ci", action="store_true", help="CI 轻量模式，不跑 git 和全量工具")
    parser.add_argument("--full", action="store_true", help="加 tools/test_all.py")
    parser.add_argument("--metadata-only", action="store_true", help="只检查版本串")
    args = parser.parse_args(argv)

    info = version_info()
    print(f"release_check · v{info['expected'] or '?'} · {ROOT}")
    if args.metadata_only:
        issues = version_issues(info)
        for issue in issues:
            print("FAIL", issue)
        if issues:
            return 1
        print("PASS 版本串同步")
        return 0

    failures = run_checks(ci=args.ci, full=args.full)
    print("\n" + ("FAIL " + " / ".join(failures) if failures else "ALL PASS"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
