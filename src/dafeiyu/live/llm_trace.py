"""本机模型轨迹：每轮对话实际发给模型的上下文、thought / reasoning 和回复。

控制台「模型思考」页只读这份 JSONL。它和 Token 账单分开：账单只需要数字，
这里要能回答「她回这句话之前看到了什么、想了什么」。图片只留 ``[图片]``，
不把 base64 写进日志；API Key / Cookie 之类的串统一打码。

聊天为了 1–2 秒内开口，``ChatLLM`` 默认关着 provider 的 thinking，所以
``reasoning_content`` 不一定有。KOOK 提示词会让模型先写一句 ``thought``
（只给本机看，不发给对方），页面优先展示它；两者都没有时如实显示「本轮
没留思考」，而不是编一段内心戏。
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from .. import paths

TRACE_PATH = paths.LOGS / "llm_trace.jsonl"
_MAX_ROWS = 3000
_MAX_MESSAGE_CHARS = 12_000
_MAX_TOTAL_CHARS = 60_000
_MAX_REPLY = 8_000
_MAX_REASONING = 16_000
_MAX_THOUGHT = 400
_MAX_ERROR = 2_000
_MAX_META = 1_200
_CACHE: dict[str, tuple[tuple[int, int], list[dict]]] = {}
_CACHE_LOCK = threading.Lock()
_WRITE_LOCK = threading.Lock()

# 这些串不该出现在控制台里；即使调用方误传，也在落盘前打码。
_SECRET = re.compile(
    r"(?i)(sk-[a-z0-9_-]{8,}|bearer\s+[a-z0-9._~+/=-]+|"
    r"SESSDATA=[^;\s]+|bili_jct=[^;\s]+|kook_bot_token\s*[:=]\s*\S+)"
)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _clip(text: str, limit: int) -> str:
    body = str(text or "").replace("\x00", "")
    body = _SECRET.sub("[已隐藏]", body)
    if limit > 0 and len(body) > limit:
        return body[:limit] + "…"
    return body


def _content(value: Any) -> str:
    """把 OpenAI 风格 content 压成可读文字；图片不落 base64。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                kind = str(item.get("type") or "")
                if kind in ("image_url", "input_image"):
                    parts.append("[图片]")
                else:
                    text = item.get("text") or item.get("content")
                    if text:
                        parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts)
    if isinstance(value, dict):
        if str(value.get("type") or "") in ("image_url", "input_image"):
            return "[图片]"
        return str(value.get("text") or value.get("content") or "")
    return "" if value is None else str(value)


def _messages(messages: list[dict] | None) -> list[dict]:
    rows: list[dict] = []
    total = 0
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        raw = _content(message.get("content"))
        body = _clip(raw, _MAX_MESSAGE_CHARS)
        total += len(body)
        rows.append({"role": str(message.get("role") or ""), "content": body,
                     "chars": len(raw)})
        if total >= _MAX_TOTAL_CHARS:
            rows.append({"role": "system",
                         "content": f"（上下文过长，只保留前 {total} 字）", "chars": 0})
            break
    return rows


def _meta(meta: dict | None) -> dict:
    if not isinstance(meta, dict):
        return {}
    out: dict = {}
    for key, value in list(meta.items())[:20]:
        name = _clip(str(key), 40)
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[name] = _clip(str(value), 240) if isinstance(value, str) else value
    encoded = json.dumps(out, ensure_ascii=False)
    if len(encoded) > _MAX_META:
        return {"note": encoded[:_MAX_META] + "…"}
    return out


def append(*, source: str, model: str, messages: list[dict] | None,
           reply: str = "", reasoning: str = "", thought: str = "",
           ok: bool = True, error: str = "", ms: int = 0,
           usage: dict | None = None, meta: dict | None = None,
           path: str | Path | None = None) -> dict | None:
    """追加一轮轨迹；任何 IO 错误都吞掉，绝不能影响对话本身。"""
    usage = usage if isinstance(usage, dict) else {}
    ts = time.time()
    row = {
        "id": f"{int(ts * 1000)}-{uuid.uuid4().hex[:8]}",
        "at": ts,
        "source": _clip(str(source or "模型对话"), 40),
        "model": _clip(str(model or ""), 80),
        "ok": bool(ok),
        "ms": _int(ms),
        "input": _int(usage.get("input")),
        "output": _int(usage.get("output")),
        "total": _int(usage.get("total")) or _int(usage.get("input")) + _int(usage.get("output")),
        "thought": _clip(thought, _MAX_THOUGHT),
        "reasoning": _clip(reasoning, _MAX_REASONING),
        "reply": _clip(reply, _MAX_REPLY),
        "error": _clip(error, _MAX_ERROR),
        "meta": _meta(meta),
        "messages": _messages(messages),
    }
    target = Path(path) if path else TRACE_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _WRITE_LOCK:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except OSError:
        return None
    with _CACHE_LOCK:
        _CACHE.pop(str(target), None)
    return row


def _read(path: Path) -> list[dict]:
    rows: deque[str] = deque(maxlen=_MAX_ROWS)
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            rows.extend(handle)
    except OSError:
        return []
    out: list[dict] = []
    for line in rows:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and str(row.get("id") or ""):
            out.append(row)
    return out


def _cached(path: Path) -> list[dict]:
    try:
        stat = path.stat()
    except OSError:
        return []
    signature = (stat.st_mtime_ns, stat.st_size)
    with _CACHE_LOCK:
        cached = _CACHE.get(str(path))
        if cached and cached[0] == signature:
            return cached[1]
    rows = _read(path)
    with _CACHE_LOCK:
        _CACHE[str(path)] = (signature, rows)
    return rows


def _preview(row: dict) -> str:
    for message in reversed(row.get("messages") or []):
        if not isinstance(message, dict) or str(message.get("role")) != "user":
            continue
        text = str(message.get("content") or "").strip()
        if text:
            return text
    return ""


def _summary(row: dict) -> dict:
    messages = row.get("messages") if isinstance(row.get("messages"), list) else []
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    thought = str(row.get("thought") or "")
    reasoning = str(row.get("reasoning") or "")
    return {
        "id": str(row.get("id") or ""),
        "at": float(row.get("at") or 0.0),
        "source": str(row.get("source") or "模型对话"),
        "model": str(row.get("model") or ""),
        "ok": bool(row.get("ok")),
        "ms": _int(row.get("ms")),
        "input": _int(row.get("input")),
        "output": _int(row.get("output")),
        "total": _int(row.get("total")),
        "thought": _clip(thought, 360),
        "reasoning": _clip(reasoning, 480),
        "has_thought": bool(thought.strip()),
        "has_reasoning": bool(reasoning.strip()),
        "preview": _clip(_preview(row), 280),
        "reply": _clip(str(row.get("reply") or ""), 320),
        "error": _clip(str(row.get("error") or ""), 320),
        "message_count": len(messages),
        "msg_id": str(meta.get("msg_id") or ""),
    }


def page(page: int = 1, per: int = 10, source: str = "",
         path: str | Path | None = None) -> dict:
    """分页读取最近轨迹；最新的在前。"""
    target = Path(path) if path else TRACE_PATH
    rows = _cached(target)
    source = str(source or "").strip()
    filtered = [row for row in reversed(rows)
                if not source or str(row.get("source") or "") == source]
    try:
        per = max(1, min(50, int(per or 10)))
    except (TypeError, ValueError):
        per = 10
    total = len(filtered)
    pages = max(1, math.ceil(total / per))
    try:
        page = int(page or 1)
    except (TypeError, ValueError):
        page = 1
    page = max(1, min(page, pages))
    start = (page - 1) * per
    counts: dict[str, int] = {}
    for row in rows:
        label = str(row.get("source") or "模型对话")
        counts[label] = counts.get(label, 0) + 1
    sources = [{"source": name, "count": count}
               for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
    return {
        "ok": True,
        "page": page,
        "per": per,
        "pages": pages,
        "total": total,
        "stats": {
            "total": len(rows),
            "thought": sum(1 for row in rows if str(row.get("thought") or "").strip()),
            "reasoning": sum(1 for row in rows
                             if str(row.get("reasoning") or "").strip()),
            "errors": sum(1 for row in rows if not row.get("ok", True)),
        },
        "sources": sources,
        "items": [_summary(row) for row in filtered[start:start + per]],
        "path": str(target),
    }


def get(trace_id: str, path: str | Path | None = None) -> dict | None:
    """按 id 取一轮完整轨迹（列表接口只给摘要）。"""
    wanted = str(trace_id or "").strip()
    if not wanted:
        return None
    target = Path(path) if path else TRACE_PATH
    for row in reversed(_cached(target)):
        if str(row.get("id") or "") != wanted:
            continue
        out = dict(row)
        out["messages"] = [dict(item) for item in row.get("messages") or []
                           if isinstance(item, dict)]
        out["meta"] = dict(row.get("meta") or {})
        return out
    return None


__all__ = ["TRACE_PATH", "append", "get", "page"]
