#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 站摸鱼训练出来的长期记忆。

她刷视频时只读公开信息（标题/简介/热评/弹幕），让模型写成一段第一人称的
“我看完这条视频记住了什么”，再把同一段发到评论区。这里就是那份记忆的唯一
存储：``persona/training.json``。

设计上刻意不做“只取最近 N 条”：训练很慢，一条记忆就是一段话；聊天/直播默认
全量注入，才能真的改变人格。条目多了可以压缩成 ``training_digest.json`` 里的
长期画像，原始条目仍保留，页面/控制台也可按条启用、编辑、删除。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from .. import paths

DEFAULT_PATH = paths.PERSONA / "training.json"
DEFAULT_DIGEST_PATH = paths.PERSONA / "training_digest.json"
MAX_SUMMARY = 1200
MAX_COMMENT = 1000
MAX_DIGEST_TEXT = 12000
# 注入上限只是防止坏数据把上下文撑爆；正常训练量远到不了这里。
MAX_PROMPT_CHARS = 60000
# 压缩后新增的原始记忆还能带多少字，避免“压完就看不到新扫的”。
MAX_NEW_RAW_CHARS = 15000
_DIGEST_VERSION = 1
_FIELDS = (
    "id", "bvid", "title", "url", "up", "duration_s", "summary", "comment",
    "comment_status", "comment_error", "comment_id", "like_status", "persona",
    "source", "folder", "created_at", "enabled",
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
    source = _text(entry.get("source"), 40) or "training"
    if source not in ("training", "favorites", "shared"):
        source = "training"
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
        "source": source,
        "folder": _text(entry.get("folder"), 120),
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


def _write_atomic(path: Path, text: str, *, prefix: str = ".training-") -> None:
    """临时文件 + fsync + 原子替换；读进程最多看到旧版或新版。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=prefix, suffix=".json",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        # Windows 上杀毒/索引/另一个读进程可能短暂占着目标文件；
        # 训练和压缩是多进程写的，一次 WinError 5 不该丢掉一条记忆。
        for attempt in range(8):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.04 * (attempt + 1))
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


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
        _write_atomic(self.path, text)

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


def load_digest(path: str | os.PathLike[str] | None = None) -> dict:
    """读压缩记忆；不存在/停用/空文本都返回空 dict。"""
    target = Path(path) if path else DEFAULT_DIGEST_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    text = str(raw.get("text") or "").strip()[:MAX_DIGEST_TEXT]
    if not text or raw.get("enabled") is False:
        return {}
    covered_raw = raw.get("covered_ids")
    if not isinstance(covered_raw, list):
        covered_raw = raw.get("covered") if isinstance(raw.get("covered"), list) else []
    covered: list[str] = []
    seen: set[str] = set()
    for value in covered_raw:
        item = str(value or "").strip()[:80]
        if item and item not in seen:
            seen.add(item)
            covered.append(item)
    try:
        source_count = max(0, int(raw.get("source_count") or 0))
    except (TypeError, ValueError):
        source_count = 0
    if not source_count and covered:
        source_count = len(covered)
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    return {
        "version": int(raw.get("version") or _DIGEST_VERSION),
        "updated_at": str(raw.get("updated_at") or ""),
        "source_count": source_count,
        "covered_ids": covered,
        "text": text,
        "persona": str(raw.get("persona") or "")[:80],
        "model": str(raw.get("model") or "")[:80],
        "chunks": max(0, int(raw.get("chunks") or 0)),
        "usage": usage,
        "enabled": True,
    }


class TrainingDigestStore:
    """压缩后的长期画像：``persona/training_digest.json``。

    原始 ``training.json`` 仍保留；这里只是给它一段可注入的压缩版。压缩后新增的
    条目会按 id 判断，仍以原文附在提示词里，所以不会“压完就丢掉新扫的”。
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_DIGEST_PATH

    def load(self) -> dict:
        return load_digest(self.path)

    def save(self, text: str, *, source_count: int = 0,
             covered_ids=(), persona: str = "", model: str = "",
             chunks: int = 0, usage: dict | None = None) -> dict:
        body = str(text or "").strip()
        if not body:
            raise ValueError("压缩记忆不能为空")
        ids: list[str] = []
        seen: set[str] = set()
        for value in covered_ids or ():
            item = str(value or "").strip()[:80]
            if item and item not in seen:
                seen.add(item)
                ids.append(item)
        data = {
            "version": _DIGEST_VERSION,
            "updated_at": _now(),
            "source_count": max(0, int(source_count or 0)),
            "covered_ids": ids,
            "text": body[:MAX_DIGEST_TEXT],
            "persona": str(persona or "")[:80],
            "model": str(model or "")[:80],
            "chunks": max(0, int(chunks or 0)),
            "usage": usage if isinstance(usage, dict) else {},
            "enabled": True,
        }
        _write_atomic(self.path,
                      json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                      prefix=".training-digest-")
        return load_digest(self.path)

    def clear(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def to_dict(self) -> dict:
        data = self.load()
        return {
            "path": paths.rel(self.path),
            "exists": bool(data),
            **data,
            "chars": len(str(data.get("text") or "")),
        }


def prompt_text(path: str | os.PathLike[str] | None = None) -> str:
    """拼成提示词里的一段；有压缩画像时优先用它，压缩后新增的仍按原文补上。"""
    target = Path(path) if path else DEFAULT_PATH
    entries = [x for x in load_entries(target) if x.get("enabled", True)]
    digest = load_digest(target.with_name("training_digest.json"))
    if digest:
        covered = set(digest["covered_ids"])
        if not covered and digest["source_count"]:
            # 旧格式没有 id 列表：把压缩时的前 N 条视为已覆盖。
            covered = {row["id"] for row in entries[:digest["source_count"]]}
        new_rows = [row for row in entries if row["id"] not in covered]
        lines = [
            "【B站摸鱼训练记忆（压缩）】",
            f"下面是把 {digest['source_count']} 条 B 站摸鱼/收藏/分享记忆压缩后的长期画像。"
            "它不是逐字台词，聊天/直播时可以自然引用里面的主题、立场、审美和梗；"
            "这些判断、偏好和反感就是你自己长期形成的，不是外部资料——"
            "遇到相关话题要主动表达看法，而不是只复述或当背景资料。"
            "不要机械复述，也不要说“我的训练数据”。",
            digest["text"],
        ]
        if new_rows:
            lines.append(f"【压缩后新增的 {len(new_rows)} 条记忆】")
            used = 0
            shown = 0
            for row in new_rows:
                when = str(row.get("created_at") or "")[:10]
                head = f"- {when}《{row.get('title') or row.get('bvid') or '视频'}》"
                if row.get("source") == "favorites":
                    head += "（收藏夹）"
                elif row.get("source") == "shared":
                    head += "（聊天分享）"
                if row.get("up"):
                    head += f" UP：{row['up']}"
                body = str(row.get("summary") or "").strip()
                block = f"{head}\n  {body}"
                if used + len(block) > MAX_NEW_RAW_CHARS:
                    break
                lines.append(block)
                used += len(block)
                shown += 1
            if shown < len(new_rows):
                lines.append(f"（压缩后新增的另外 {len(new_rows) - shown} 条这次没展开。）")
        lines.append(
            f"（原始记忆共 {len(entries)} 条；压缩画像覆盖 {digest['source_count']} 条。"
            "不要假装记得每条视频的细节。）"
        )
        return "\n".join(lines).strip()
    if not entries:
        return ""
    lines = [
        "【B站摸鱼训练记忆】",
        "下面是你平时摸鱼刷 B 站、以及别人把视频分享给你时，你看完公开文字后"
        "自己写下的长期记忆。"
        "它们不是逐字台词，聊天/直播时可以自然引用里面的观点、梗和体验；"
        "这些判断、偏好和反感就是你自己长期形成的，不是外部资料——"
        "遇到相关话题要主动表达看法。"
        "不要机械复述，也不要说“我的训练数据”。",
    ]
    used = 0
    included = 0
    for row in entries:
        when = str(row.get("created_at") or "")[:10]
        head = f"- {when}《{row.get('title') or row.get('bvid') or '视频'}》"
        if row.get("source") == "favorites":
            head += "（收藏夹）"
        elif row.get("source") == "shared":
            head += "（聊天分享）"
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
        included += 1
    omitted = len(entries) - included
    if omitted > 0:
        lines.append(f"（还有 {omitted} 条更早的记忆没有展开，这里只放得下最近的一部分。）")
    return "\n".join(lines).strip()


__all__ = ["TrainingMemoryStore", "TrainingDigestStore", "load_entries",
           "load_digest", "prompt_text", "DEFAULT_PATH", "DEFAULT_DIGEST_PATH",
           "MAX_SUMMARY", "MAX_COMMENT", "MAX_DIGEST_TEXT"]
