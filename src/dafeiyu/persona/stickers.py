"""大肥鱼表情包：读 ``persona/stickers/stickers.json``，图片在同目录。

表情包是本地素材，不走网络；聊天发图前用 :func:`path_of` 拿文件。
LLM 只看到 id / 文案 / 适用场景，选中后由聊天服务上传发送。
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import paths

PACK = paths.PERSONA / "stickers" / "stickers.json"
_DIR = paths.PERSONA / "stickers"


def load_pack(path: str | Path | None = None) -> dict:
    file = Path(path or PACK)
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"id": "dafeiyu", "name": "大肥鱼表情包", "items": []}
    if not isinstance(raw, dict):
        return {"id": "dafeiyu", "name": "大肥鱼表情包", "items": []}
    raw.setdefault("items", [])
    return raw


def items() -> list[dict]:
    out = []
    for row in load_pack().get("items") or []:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "").strip()
        name = str(row.get("file") or "").strip()
        if not sid or not name:
            continue
        file = _DIR / name
        out.append({
            "id": sid,
            "file": name,
            "path": file,
            "text": str(row.get("text") or "").strip(),
            "when": str(row.get("when") or "").strip(),
            "tags": [str(x) for x in (row.get("tags") or [])],
            "exists": file.is_file(),
        })
    return out


def options() -> list[dict]:
    """给提示词/设置页的轻量列表（不带 Path）。"""
    return [{"id": row["id"], "text": row["text"], "when": row["when"],
             "file": row["file"], "exists": row["exists"]} for row in items()]


def get(sticker_id: str) -> dict | None:
    key = str(sticker_id or "").strip()
    for row in items():
        if row["id"] == key:
            return row
    return None


def path_of(sticker_id: str) -> Path | None:
    row = get(sticker_id)
    if row and row["exists"]:
        return row["path"]
    return None


__all__ = ["items", "load_pack", "options", "path_of", "get"]
