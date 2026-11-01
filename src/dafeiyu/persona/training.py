#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 站摸鱼训练出来的长期记忆。

她刷视频时只读公开信息（标题/简介/热评/弹幕），让模型写成一段第一人称的
“我看完这条视频记住了什么”，再把同一段发到评论区。这里就是那份记忆的唯一
存储：``persona/training.json``。

设计上刻意不做“只取最近 N 条”：训练很慢，一条记忆就是一段话，量不会大；
聊天/直播时全量注入，才能真的改变人格。页面/控制台仍可按条启用、编辑、删除。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

from .. import paths

DEFAULT_PATH = paths.PERSONA / "training.json"
MAX_SUMMARY = 1200
MAX_COMMENT = 1000
# 注入上限只是防止坏数据把上下文撑爆；正常训练量远到不了这里。
MAX_PROMPT_CHARS = 60000
_FIELDS = (
    "id", "bvid", "title", "url", "up", "duration_s", "summary", "comment",
    "comment_status", "comment_error", "comment_id", "like_status", "persona",
    "created_at", "enabled",
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _safe_id(value) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "", str(value or ""))[:80]
    return text or uuid.uuid4().hex[:16]


def _clean(entry: dict, *, keep_id: str = "") -> dict:
    if not isinstance(entry, dict):
        raise ValueError("训练记忆每一条都必须是 JSON 对象")
    summary = _text(entry.get("summary"), MAX_SUMMARY)
    title = _text(entry.get("title"), 200)
    if not summary:
        raise ValueError("训练记忆缺 summary（她看完视频总结的那段话）")
    if not title:
        title = _text(entry.get("bvid"), 40) or "未命名视频"
    try:
        duration = max(0, int(float(entry.get("duration_s") or 0)))
    except (TypeError, ValueError):
        duration = 0
    out = {
        "id": _safe_id(keep_id or entry.get("id")),
        "bvid": _text(entry.get("bvid"), 40),
        "title": title,
        "url": _text(entry.get("url"), 500),
        "up": _text(entry.get("up"), 100),
        "duration_s": duration,
        "summary": summary,
        "comment": _text(entry.get("comment"), MAX_COMMENT),
        "comment_status": _text(entry.get("comment_status"), 40) or "disabled",
        "comment_error": _text(entry.get("comment_error"), 300),
        "comment_id": _text(entry.get("comment_id"), 100),
        "like_status": _text(entry.get("like_status"), 40) or "skipped",
        "persona": _text(entry.get("persona"), 80),
        "created_at": _text(entry.get("created_at"), 40) or _now(),
        "enabled": bool(entry.get("enabled", True)),
    }
    return out


def _load_raw(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "entries": []}
    if isinstance(raw, list):
        return {"version": 1, "entries": raw}
    if not isinstance(raw, dict):
        return {"version": 1, "entries": []}
    entries = raw.get("entries")
    return {"version": int(raw.get("version") or 1),
            "updated_at": str(raw.get("updated_at") or ""),
            "entries": entries if isinstance(entries, list) else []}


def load_entries(path: str | os.PathLike[str] | None = None) -> list[dict]:
    """读全部条目；坏条目跳过，不让一条脏数据打断聊天。"""
    raw = _load_raw(Path(path) if path else DEFAULT_PATH)
    out: list[dict] = []
    for item in raw["entries"]:
        try:
            out.append(_clean(item))
        except ValueError:
            continue
    return out


class TrainingMemoryStore:
    """线程/进程间共用的文件存储；写入走临时文件 + 原子替换。"""

    def __init__(self, path: str | os.PathLike[str] | None = None,
                 lock: threading.Lock | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self._lock = lock or threading.RLock()

    def entries(self) -> list[dict]:
        return load_entries(self.path)

    def save(self, entries: list[dict]) -> list[dict]:
        if not isinstance(entries, list):
            raise ValueError("训练记忆必须是数组")
        clean = []
        seen: set[str] = set()
        for item in entries:
            row = _clean(item)
            if row["id"] in seen:
                raise ValueError(f"训练记忆 id 重复：{row['id']}")
            seen.add(row["id"])
            clean.append(row)
        data = {"version": 1, "updated_at": _now(), "entries": clean}
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        with self._lock:
            self._save_text(text)
        return clean

    def _save_text(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".training-", suffix=".json",
                                   dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _save_rows(self, rows: list[dict]) -> list[dict]:
        clean = []
        seen: set[str] = set()
        for item in rows:
            row = _clean(item)
            if row["id"] in seen:
                raise ValueError(f"训练记忆 id 重复：{row['id']}")
            seen.add(row["id"])
            clean.append(row)
        data = {"version": 1, "updated_at": _now(), "entries": clean}
        self._save_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return clean

    def add(self, entry: dict) -> dict:
        row = _clean(entry)
        with self._lock:
            rows = self.entries()
            if any(x["id"] == row["id"] for x in rows):
                row["id"] = uuid.uuid4().hex[:16]
            rows.append(row)
            self._save_rows(rows)
        return row

    def update(self, entry_id: str, **changes) -> dict:
        with self._lock:
            rows = self.entries()
            for idx, row in enumerate(rows):
                if row["id"] != entry_id:
                    continue
                merged = dict(row)
                merged.update(changes)
                clean = _clean(merged, keep_id=entry_id)
                rows[idx] = clean
                self._save_rows(rows)
                return clean
        raise ValueError("找不到这条训练记忆")

    def delete(self, entry_id: str) -> bool:
        with self._lock:
            rows = self.entries()
            kept = [x for x in rows if x["id"] != entry_id]
            if len(kept) == len(rows):
                return False
            self._save_rows(kept)
            return True

    def enabled(self) -> list[dict]:
        return [x for x in self.entries() if x.get("enabled", True)]

    def to_dict(self) -> dict:
        entries = self.entries()
        return {
            "path": paths.rel(self.path),
            "entries": entries,
            "count": len(entries),
            "enabled": sum(1 for x in entries if x.get("enabled", True)),
        }


def prompt_text(path: str | os.PathLike[str] | None = None) -> str:
    """全量拼成提示词里的一段；一条都没有就返回空串。"""
    entries = [x for x in load_entries(path) if x.get("enabled", True)]
    if not entries:
        return ""
    lines = [
        "【B站摸鱼训练记忆】",
        "下面是你平时摸鱼刷 B 站时，看完视频后自己写下的长期记忆。"
        "它们不是逐字台词，聊天/直播时可以自然引用里面的观点、梗和体验；"
        "不要机械复述，也不要说“我的训练数据”。",
    ]
    used = 0
    for row in entries:
        when = str(row.get("created_at") or "")[:10]
        head = f"- {when}《{row.get('title') or row.get('bvid') or '视频'}》"
        if row.get("up"):
            head += f" UP：{row['up']}"
        body = str(row.get("summary") or "").strip()
        comment = str(row.get("comment") or "").strip()
        if comment and comment != body and row.get("comment_status") == "posted":
            body += f"\n  （当时在评论区留下：{comment[:300]}）"
        block = f"{head}\n  {body}"
        if used + len(block) > MAX_PROMPT_CHARS:
            break
        lines.append(block)
        used += len(block)
    return "\n".join(lines).strip()


__all__ = ["TrainingMemoryStore", "load_entries", "prompt_text", "DEFAULT_PATH",
           "MAX_SUMMARY", "MAX_COMMENT"]
