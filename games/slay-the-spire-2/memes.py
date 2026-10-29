"""STS2 梗库路径（控制台编辑「梗列表」用）。

核心 `ui/studio.py` 只认游戏包声明的路径，不硬编码具体游戏；这个文件和
`tools/memedb.py` 读的是同一份 JSON + SQLite：

- `db/memes.json`：梗列表源数据；
- `db/sts2.db` 的 `memes` 表：`tools/memedb.py` 查询的库。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

JSON = ROOT / "db" / "memes.json"
DB = ROOT / "db" / "sts2.db"

__all__ = ["JSON", "DB"]
