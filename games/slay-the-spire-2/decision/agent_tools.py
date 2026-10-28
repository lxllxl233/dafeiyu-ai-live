"""《杀戮尖塔 2》的 Agent 工具：把游戏 CLI 包成 observe/validate/execute。

核心只认识 ``dafeiyu.games.contracts.GameTools`` 的形状；具体调用
``decision/ctx.py`` 与 ``decision/act.py --dry`` 的知识留在本文件。返回值统一为
``ActionResult``，旧调用方仍可通过 ``result.to_legacy()`` 读 ``{ok, err}``。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dafeiyu import paths
from dafeiyu.games.contracts import ActionCode, ActionResult


def _extract_json(text: str) -> dict:
    """工具输出前面可能有日志；从最后一行往前找完整 JSON。"""
    text = (text or "").strip()
    if not text:
        return {}
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                return value
        except ValueError:
            continue
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _run(script: str, args: list[str], *, timeout: float = 30.0) -> tuple[dict, str]:
    path = Path(__file__).resolve().parent / script
    if not path.exists():
        return {}, f"缺少工具脚本：{path}"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [sys.executable, str(path), *args],
            cwd=str(paths.ROOT), capture_output=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {}, f"{script} 超时（{timeout:.0f}s）"
    except OSError as exc:
        return {}, f"{script} 启动失败：{exc}"
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    data = _extract_json(out)
    if proc.returncode != 0 and not data:
        detail = (err or out).strip().splitlines()
        return {}, f"{script} exit {proc.returncode}：{detail[-1] if detail else '无输出'}"
    if not data:
        return {}, f"{script} 没有返回 JSON"
    return data, ""


class Sts2Tools:
    """真实游戏：observe 读 ctx，validate 走 act --dry，execute 才出牌。"""

    def __init__(self, game: str = "slay-the-spire-2") -> None:
        self.game = game

    def observe(self) -> ActionResult:
        data, err = _run("ctx.py", [], timeout=20)
        if err:
            return ActionResult.failure(
                "observe", code=ActionCode.UNAVAILABLE, message=err)
        return ActionResult.success("observe", data={"context": data})

    def validate(self, plan: dict) -> ActionResult:
        return self._act(plan, dry=True, action="validate")

    def execute(self, plan: dict, *, dry_run: bool = True) -> ActionResult:
        if dry_run:
            return ActionResult.success(
                "execute",
                message="dry-run：验算通过，未出牌",
                data={"dry_run": True},
            )
        return self._act(plan, dry=False, action="execute")

    def _act(self, plan: dict, *, dry: bool, action: str) -> ActionResult:
        paths.LOGS.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="dafeiyu-plan-", suffix=".json", dir=str(paths.LOGS))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False)
            args = ["--file", name] + (["--dry"] if dry else [])
            data, err = _run("act.py", args, timeout=45)
        finally:
            try:
                Path(name).unlink()
            except OSError:
                pass
        if err:
            return ActionResult.failure(
                action, code=ActionCode.UNAVAILABLE, message=err)
        return ActionResult.from_legacy(data, action=action)


__all__ = ["Sts2Tools"]
