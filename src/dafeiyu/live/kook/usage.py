"""KOOK 聊天的 Token 账本。

聊天服务跑在独立进程里，不能直接改自动玩的 ``logs/autoplay.jsonl``；所以每次
LLM 调用（回复、记忆抽取、记忆压缩）都往 ``logs/kook/usage.jsonl`` 追加一行，
控制台再把两份账本合并展示。行格式和 ``AutoplayHost.add_usage`` 对齐：
``msg=llm_usage / note / model / input / output / total / ms``，另带消息 id。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ... import paths

_CACHE: dict[str, tuple[tuple[int, int], dict]] = {}
_CACHE_LOCK = threading.Lock()
_WRITE_LOCK = threading.Lock()
_MAX_STEPS = 4000


def default_path() -> Path:
    """账本默认位置；放在 KOOK 自己的日志目录，不和自动玩账本混写。"""
    return paths.LOGS / "kook" / "usage.jsonl"


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _kind_of(note: str) -> str:
    """「KOOK 聊天」→「KOOK 聊天」；自动玩的短说明也按同样思路归类。"""
    text = " ".join(str(note or "").split())
    if not text:
        return "KOOK"
    if text.upper().startswith("KOOK "):
        rest = text[5:].strip()
        return f"KOOK {rest}" if rest else "KOOK"
    first = text.split(" ", 1)[0]
    return first if len(first) <= 8 else first[:8]


def _empty(path: Path) -> dict:
    return {
        "total": 0, "input": 0, "output": 0, "calls": 0, "last": 0,
        "steps": [], "kinds": [], "by_run": [],
        "source": "kook", "label": "KOOK 聊天",
        "path": str(path), "paths": [str(path)],
    }


def append_usage(result: dict, note: str, *, path: str | Path | None = None,
                 msg_id: str = "", target_id: str = "") -> dict | None:
    """把一次 LLM 返回的 usage 追加到账本；没有 token 数据时不写空行。"""
    usage = result.get("usage") if isinstance(result, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    tin = _int(usage.get("input"))
    tout = _int(usage.get("output"))
    total = _int(usage.get("total")) or tin + tout
    if total <= 0:
        return None

    row = {
        "ts": time.time(),
        "msg": "llm_usage",
        "run_id": "kook",
        "source": "kook",
        "note": str(note or "KOOK 聊天"),
        "model": str(result.get("model") or ""),
        "input": tin,
        "output": tout,
        "total": total,
        "ms": _int(result.get("ms")),
        "msg_id": str(msg_id or ""),
        "target_id": str(target_id or ""),
    }
    target = Path(path) if path else default_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with _WRITE_LOCK:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except OSError:
        return None
    with _CACHE_LOCK:
        _CACHE.pop(str(target), None)
    return row


def _read(path: Path) -> dict:
    out = _empty(path)
    steps: list[dict] = []
    total = total_in = total_out = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("msg") not in (None, "llm_usage"):
                    continue
                value = _int(row.get("total"))
                if value <= 0:
                    continue
                tin = _int(row.get("input"))
                tout = _int(row.get("output"))
                total += value
                total_in += tin
                total_out += tout
                note = str(row.get("note") or "KOOK 聊天")
                steps.append({
                    "at": float(row.get("ts") or 0.0),
                    "kind": _kind_of(note),
                    "note": note,
                    "model": str(row.get("model") or ""),
                    "input": tin,
                    "output": tout,
                    "total": value,
                    "ms": _int(row.get("ms")),
                    "msg_id": str(row.get("msg_id") or ""),
                    "target_id": str(row.get("target_id") or ""),
                    "source": "kook",
                })
    except OSError:
        return out

    # 累计按整份账本算，页面最后一步的累计等于总数。
    running = total
    for row in reversed(steps):
        row["cumulative"] = running
        running -= row["total"]

    kinds: dict[str, dict] = {}
    for row in steps:
        slot = kinds.setdefault(row["kind"],
                                {"kind": row["kind"], "total": 0, "calls": 0})
        slot["total"] += row["total"]
        slot["calls"] += 1

    out.update({
        "total": total,
        "input": total_in,
        "output": total_out,
        "calls": len(steps),
        "last": steps[-1]["total"] if steps else 0,
        "steps": steps[-_MAX_STEPS:],
        "kinds": sorted(kinds.values(), key=lambda item: -item["total"]),
    })
    return out


def summary(path: str | Path | None = None, limit: int = 40) -> dict:
    """读账本；控制台 3 秒轮询用 (mtime, size) 缓存，不重复解析。"""
    target = Path(path) if path else default_path()
    try:
        stat = target.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return _empty(target)

    with _CACHE_LOCK:
        cached = _CACHE.get(str(target))
        if cached and cached[0] == signature:
            data = cached[1]
        else:
            data = _read(target)
            _CACHE[str(target)] = (signature, data)

    result = dict(data)
    result["steps"] = [dict(row) for row in data.get("steps", [])[-max(1, int(limit or 40)):]]
    return result


__all__ = ["append_usage", "default_path", "summary"]
