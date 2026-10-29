"""B 模式的控制面：播放列表快照 + 控制台命令文件。

B 自动玩可能在另一个进程里跑，控制台不能直接摸到 ``BiliEngine``。这里只用
两个本地文件通信：

- ``bilibili_commentary.playlist.json``：引擎写，控制台读，展示候选/当前视频；
- ``bilibili_commentary.command.json``：控制台写，引擎消费，play/close/refresh。

文件都在 ``paths.LOGS`` 下（测试可传 ``root``），只含 bvid/标题/URL/窗口号，
不含 Cookie。命令是用户明确点击产生的，不来自视频/弹幕文本。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from dafeiyu import paths

PLAYLIST_FILE = "bilibili_commentary.playlist.json"
COMMAND_FILE = "bilibili_commentary.command.json"
ACTIONS = frozenset({"play", "close", "refresh"})
_ITEM_LIMIT = 40


def _root(root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else paths.LOGS


def playlist_path(root: str | Path | None = None) -> Path:
    return _root(root) / PLAYLIST_FILE


def command_path(root: str | Path | None = None) -> Path:
    return _root(root) / COMMAND_FILE


def _read_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_json(path: Path, payload: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _text(value, limit: int = 200) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[: max(1, int(limit))]


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def item_from_ref(ref, *, status: str = "") -> dict:
    """把 VideoRef/VideoDetail 缩成控制台需要的字段（不写简介/评论原文）。"""
    bvid = _text(getattr(ref, "bvid", ""), 20)
    out = {
        "bvid": bvid,
        "title": _text(getattr(ref, "title", ""), 160),
        "owner": _text(getattr(ref, "owner_name", ""), 60),
        "duration_s": max(0, _as_int(getattr(ref, "duration_s", 0))),
        "url": _text(getattr(ref, "url", "") or
                     (f"https://www.bilibili.com/video/{bvid}" if bvid else ""), 300),
    }
    if status:
        out["status"] = status
    return out


def publish_playlist(
    items,
    *,
    current_bvid: str = "",
    state: str = "",
    browser: dict | None = None,
    root: str | Path | None = None,
) -> dict:
    """覆盖写播放列表快照；只保留有限条，避免控制台轮询越读越大。"""
    clean: list[dict] = []
    seen: set[str] = set()
    for raw in list(items or []):
        if not isinstance(raw, dict):
            continue
        bvid = _text(raw.get("bvid"), 20)
        if not bvid or bvid in seen:
            continue
        seen.add(bvid)
        clean.append({
            "bvid": bvid,
            "title": _text(raw.get("title"), 160),
            "owner": _text(raw.get("owner"), 60),
            "duration_s": max(0, _as_int(raw.get("duration_s"))),
            "url": _text(raw.get("url") or
                         f"https://www.bilibili.com/video/{bvid}", 300),
            **({"status": _text(raw.get("status"), 20)}
               if raw.get("status") else {}),
        })
        if len(clean) >= _ITEM_LIMIT:
            break
    payload = {
        "ok": True,
        "updated_at": time.time(),
        "state": _text(state, 40),
        "current_bvid": _text(current_bvid, 20),
        "items": clean,
        "browser": dict(browser) if isinstance(browser, dict) else {},
    }
    _write_json(playlist_path(root), payload)
    return payload


def read_playlist(root: str | Path | None = None) -> dict:
    raw = _read_json(playlist_path(root))
    if not raw:
        return {"ok": True, "state": "missing", "current_bvid": "",
                "items": [], "browser": {}, "updated_at": 0}
    items = raw.get("items")
    raw["items"] = items if isinstance(items, list) else []
    browser = raw.get("browser")
    raw["browser"] = browser if isinstance(browser, dict) else {}
    raw.setdefault("state", "")
    raw.setdefault("current_bvid", "")
    raw.setdefault("updated_at", 0)
    return raw


def send_command(
    action: str,
    bvid: str = "",
    *,
    root: str | Path | None = None,
) -> dict:
    action = str(action or "").strip().lower()
    if action not in ACTIONS:
        raise ValueError(f"未知播放列表操作：{action}")
    bvid = _text(bvid, 20)
    if action == "play" and not bvid:
        raise ValueError("播放指定视频需要 bvid")
    payload = {
        "ok": True,
        "action": action,
        "bvid": bvid,
        "id": uuid.uuid4().hex[:16],
        "at": time.time(),
    }
    if not _write_json(command_path(root), payload):
        raise OSError("控制命令写入失败")
    return payload


def consume_command(root: str | Path | None = None) -> dict | None:
    """读取并删除一条命令；返回 None 表示没有命令。"""
    path = command_path(root)
    raw = _read_json(path)
    action = str(raw.get("action") or "").strip().lower()
    if action not in ACTIONS:
        return None
    try:
        path.unlink()
    except OSError:
        pass
    return raw


__all__ = [
    "ACTIONS", "COMMAND_FILE", "PLAYLIST_FILE", "command_path", "consume_command",
    "item_from_ref", "playlist_path", "publish_playlist", "read_playlist",
    "send_command",
]
