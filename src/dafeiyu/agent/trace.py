"""Agent 运行轨迹：每次 LangGraph 节点都落一行 JSONL。

为什么不用 LangSmith：API Key 和局面数据都在本机，默认不外传；JSONL 足够做
「节点耗时 / 重试次数 / token / 失败原因」的观察和回归，不增加云依赖。

轨迹只存摘要，不存完整局面和计划，避免把 key、牌局全文写进日志。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .. import paths

TRACE_PATH = paths.LOGS / "agent_trace.jsonl"
_MAX_SUMMARY = 240
_MAX_ERROR = 300
_MAX_BYTES = 3 * 1024 * 1024
_trim_lock = threading.Lock()


def _text(value: Any, limit: int = _MAX_SUMMARY) -> str:
    s = " ".join(str(value or "").split())
    return s[:limit]


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _trim_lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
        try:
            if path.stat().st_size > _MAX_BYTES:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                path.write_text("\n".join(lines[-1500:]) + "\n", encoding="utf-8")
        except OSError:
            pass


class TraceRecorder:
    """一次 run 的写入器；节点用 `event()` 记开始/结束。"""

    def __init__(self, goal: str, *, run_id: str = "", game: str = "",
                 mode: str = "demo", path: Path | None = None) -> None:
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        self.goal = goal
        self.game = game
        self.mode = mode
        self.path = Path(path) if path else TRACE_PATH
        self.started_at = time.time()
        self.tokens = {"input": 0, "output": 0, "total": 0}
        self._steps: list[dict] = []
        self._ended = False
        self.event("run", "started", summary=goal)

    def event(self, node: str, status: str, *, duration_ms: int = 0,
              summary: str = "", error: str = "", tokens: dict | None = None,
              attempt: int = 0, accumulate: bool = True) -> dict:
        row = {
            "ts": time.time(),
            "run_id": self.run_id,
            "goal": self.goal[:120],
            "game": self.game,
            "mode": self.mode,
            "node": node,
            "status": status,
            "duration_ms": int(duration_ms),
            "attempt": int(attempt or 0),
        }
        if summary:
            row["summary"] = _text(summary)
        if error:
            row["error"] = _text(error, _MAX_ERROR)
        if tokens:
            clean = {k: int(tokens.get(k) or 0) for k in ("input", "output", "total")}
            row["tokens"] = clean
            if accumulate:
                for k in clean:
                    self.tokens[k] += clean[k]
        if node != "run":
            self._steps.append({k: row[k] for k in
                                ("ts", "node", "status", "duration_ms", "attempt")
                                if k in row} | {k: row[k] for k in ("summary", "error", "tokens")
                                                if k in row})
        _append(self.path, row)
        return row

    def finish(self, status: str, *, error: str = "") -> None:
        if self._ended:
            return
        self._ended = True
        duration = int((time.time() - self.started_at) * 1000)
        summary = f"{len(self._steps)} 个节点 · {self.tokens['total']} tok"
        if error:
            summary += " · " + error
        self.event("run", status, duration_ms=duration, summary=summary,
                   error=error, tokens=self.tokens, accumulate=False)

    @property
    def steps(self) -> list[dict]:
        return list(self._steps)


def read_runs(path: Path | None = None, limit: int = 20) -> list[dict]:
    """从 JSONL 还原最近 run；控制台每 5 秒读一次，数据量很小。"""
    path = Path(path) if path else TRACE_PATH
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    runs: dict[str, dict] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        rid = str(row.get("run_id") or "")
        if not rid:
            continue
        run = runs.setdefault(rid, {
            "id": rid, "goal": row.get("goal", ""), "game": row.get("game", ""),
            "mode": row.get("mode", ""), "status": "running",
            "started_at": row.get("ts", 0), "ended_at": 0, "duration_ms": 0,
            "tokens": {"input": 0, "output": 0, "total": 0}, "steps": [], "error": "",
        })
        if row.get("goal"):
            run["goal"] = row["goal"]
        if row.get("node") == "run":
            if row.get("status") == "started":
                run["started_at"] = min(run["started_at"] or row.get("ts", 0), row.get("ts", 0))
            else:
                run["status"] = row.get("status", "ok")
                run["ended_at"] = row.get("ts", 0)
                run["duration_ms"] = row.get("duration_ms", 0)
                run["error"] = row.get("error", "")
                if row.get("tokens"):
                    run["tokens"] = row["tokens"]
        else:
            run["steps"].append({k: row[k] for k in
                                 ("ts", "node", "status", "duration_ms", "attempt",
                                  "summary", "error", "tokens") if k in row})
    rows = sorted(runs.values(), key=lambda r: r.get("started_at") or 0, reverse=True)
    return rows[:max(0, int(limit))]


def agent_summary(path: Path | None = None, limit: int = 12) -> dict:
    rows = read_runs(path, limit=limit)
    latest = rows[0] if rows else {}
    return {
        "available": True,
        "trace": paths.rel(Path(path) if path else TRACE_PATH),
        "runs": rows,
        "latest": latest,
        "count": len(rows),
    }


__all__ = ["TRACE_PATH", "TraceRecorder", "read_runs", "agent_summary"]
