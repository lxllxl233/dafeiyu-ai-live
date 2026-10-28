"""Agent 的工具层：把现有游戏 CLI 包成节点可调用的最小接口。

`Sts2Tools` 不重写局面/出牌逻辑，直接复用 `tools/ctx.py` 和
`tools/act.py --dry`；`DemoTools` 用于离线验证图本身，不碰游戏。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Protocol

from .. import paths


class Tools(Protocol):
    def observe(self) -> dict: ...
    def validate(self, plan: dict) -> dict: ...
    def execute(self, plan: dict, *, dry_run: bool = True) -> dict: ...


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
    path = paths.TOOLS / script
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


class DemoTools:
    """离线演示：不启动游戏，专门验证图的边和轨迹。"""

    def __init__(self) -> None:
        self.observed = 0
        self.validated = 0
        self.executed = 0

    def observe(self) -> dict:
        self.observed += 1
        return {"ok": True, "context": {
            "screen": "DEMO", "act": 1, "floor": 6, "hp": 52, "max_hp": 80,
            "energy": 3, "hand": [{"id": "BASH"}, {"id": "STRIKE_IRONCLAD"}],
            "enemies": [{"id": "CULTIST", "hp": 48, "intent": "A6"}],
            "note": "离线演示局面；不会真的出牌",
        }}

    def validate(self, plan: dict) -> dict:
        self.validated += 1
        seq = plan.get("seq") if isinstance(plan, dict) else None
        if not isinstance(seq, list) or not seq:
            return {"ok": False, "err": "演示计划需要非空 seq"}
        return {"ok": True, "summary": "演示验算通过", "seq": len(seq)}

    def execute(self, plan: dict, *, dry_run: bool = True) -> dict:
        self.executed += 1
        return {"ok": True, "dry_run": bool(dry_run),
                "message": "演示流程完成（未调用游戏）"}


class Sts2Tools:
    """真实游戏：observe 读 ctx，validate 走 act --dry，execute 才出牌。"""

    def __init__(self, game: str = "slay-the-spire-2") -> None:
        self.game = game

    def observe(self) -> dict:
        data, err = _run("ctx.py", [], timeout=20)
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "context": data}

    def validate(self, plan: dict) -> dict:
        return self._act(plan, dry=True)

    def execute(self, plan: dict, *, dry_run: bool = True) -> dict:
        if dry_run:
            return {"ok": True, "dry_run": True, "message": "dry-run：验算通过，未出牌"}
        return self._act(plan, dry=False)

    def _act(self, plan: dict, *, dry: bool) -> dict:
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
            return {"ok": False, "error": err}
        return data


class DemoPlanner:
    """无 LLM 的固定计划，用来演示和测试；真实决策走 LLMPlanner 或外部 plan。"""

    name = "demo"

    def plan(self, context: dict, feedback: str = "") -> dict:
        seq = [{"c": "BASH", "t": 0}, {"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}]
        if feedback:
            seq = [{"end": 1}]
        return {
            "ok": True,
            "plan": {"say": ["先算一下，这回合不贪。"], "seq": seq},
            "usage": {"input": 0, "output": 0, "total": 0},
            "planner": "demo",
        }


__all__ = ["Tools", "DemoTools", "Sts2Tools", "DemoPlanner", "_extract_json"]
