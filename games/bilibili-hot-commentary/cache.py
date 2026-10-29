"""本地 JSON 缓存 / 已观看 / 失败冷却。

不做数据库：每个 key 一个小 JSON，原子替换；只存公开元数据、评论文本和
档案，不存用户名、Cookie、midHash 或头像。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from dafeiyu import paths

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def default_root() -> Path:
    return paths.LOGS / "bilibili_commentary"


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=1)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


class JsonCache:
    """按 key 分文件的 TTL 缓存；适合详情/评论/档案，不适合高频写。"""

    def __init__(self, root: str | Path, name: str, ttl_s: float) -> None:
        self.root = Path(root) / name
        self.ttl_s = max(0.0, float(ttl_s))
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        digest = hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:24]
        return self.root / f"{digest}.json"

    def get(self, key: str, *, now: float | None = None) -> Any:
        now = time.time() if now is None else now
        path = self._path(key)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        try:
            fetched_at = float(raw.get("fetched_at") or 0)
        except (TypeError, ValueError):
            return None
        if self.ttl_s > 0 and now - fetched_at > self.ttl_s:
            return None
        return raw.get("value")

    def put(self, key: str, value: Any, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            _atomic_json(self._path(key), {"fetched_at": now, "value": value})

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except OSError:
            pass


class JsonDictStore:
    """单文件字典 store：seen/failed 这种小状态。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write(self, value: dict) -> None:
        with self._lock:
            _atomic_json(self.path, value)

    def get(self, key: str) -> Any:
        return self.load().get(str(key))

    def set(self, key: str, value: Any) -> None:
        data = self.load()
        data[str(key)] = value
        self._write(data)

    def delete(self, key: str) -> None:
        data = self.load()
        if str(key) in data:
            data.pop(str(key), None)
            self._write(data)


class SeenStore:
    def __init__(self, root: str | Path) -> None:
        self.store = JsonDictStore(Path(root) / "seen.json")

    def is_seen(self, bvid: str, ttl_days: int, *, now: float | None = None) -> bool:
        if ttl_days <= 0:
            return False
        now = time.time() if now is None else now
        try:
            at = float(self.store.get(bvid) or 0)
        except (TypeError, ValueError):
            return False
        return at > 0 and now - at <= ttl_days * 86400

    def mark(self, bvid: str, *, now: float | None = None) -> None:
        self.store.set(bvid, time.time() if now is None else now)

    def prune(self, ttl_days: int, *, now: float | None = None) -> None:
        if ttl_days <= 0:
            return
        now = time.time() if now is None else now
        data = self.store.load()
        cutoff = now - ttl_days * 86400
        kept = {}
        for key, value in data.items():
            try:
                at = float(value or 0)
            except (TypeError, ValueError):
                continue
            if at >= cutoff:
                kept[key] = at
        if kept != data:
            self.store._write(kept)


class FailedStore:
    def __init__(self, root: str | Path) -> None:
        self.store = JsonDictStore(Path(root) / "failed.json")

    def is_failed(self, bvid: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        try:
            until = float(self.store.get(bvid) or 0)
        except (TypeError, ValueError):
            return False
        return until > now

    def mark(self, bvid: str, cooldown_s: float, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.store.set(bvid, now + max(0.0, float(cooldown_s)))

    def clear(self, bvid: str) -> None:
        self.store.delete(bvid)

    def prune(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        data = self.store.load()
        kept = {}
        for key, value in data.items():
            try:
                until = float(value or 0)
            except (TypeError, ValueError):
                continue
            if until > now:
                kept[key] = until
        if kept != data:
            self.store._write(kept)


class Stores:
    """一个模式实例会用到的全部本地状态；测试可注入 tmp 根目录。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.popular = JsonCache(self.root, "popular", 180.0)
        self.videos = JsonCache(self.root, "videos", 6 * 3600.0)
        self.comments = JsonCache(self.root, "comments", 10 * 60.0)
        self.danmaku = JsonCache(self.root, "danmaku", 24 * 3600.0)
        self.profiles = JsonCache(self.root, "profiles", 7 * 86400.0)
        self.search_intents = JsonCache(self.root, "search_intents", 7 * 86400.0)
        self.seen = SeenStore(self.root)
        self.failed = FailedStore(self.root)


__all__ = [
    "FailedStore", "JsonCache", "JsonDictStore", "SeenStore", "Stores",
    "default_root",
]
