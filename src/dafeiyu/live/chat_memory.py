"""聊天长期记忆：本地 SQLite，不上云。

三层：

1. ``turns`` —— 最近的原文对话（短期上下文，按条数截断）；
2. ``facts`` —— 从对话里抽出来的长期事实（偏好、身份、约定），可以手动
   「记住 / 忘掉」；
3. ``seen`` —— KOOK 事件去重（事件会重投，同一条消息只处理一次）。

检索优先用 FTS5 的 ``trigram`` 分词（中文按三字滑窗，能搜任意子串）；
老 SQLite 没有 trigram 时退回 ``LIKE``。DB 默认在 ``logs/kook/memory.db``，
和仓库「轨迹/日志只留本机」的约定一致。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .. import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    role    TEXT NOT NULL,
    text    TEXT NOT NULL,
    msg_id  TEXT,
    image   TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    text      TEXT NOT NULL,
    weight    REAL NOT NULL DEFAULT 1.0,
    hits      INTEGER NOT NULL DEFAULT 0,
    last_used REAL
);

CREATE TABLE IF NOT EXISTS seen (
    msg_id TEXT PRIMARY KEY,
    ts     REAL NOT NULL
);

-- 压缩后的长期对话摘要；原文 turn 不删，摘要只负责进上下文。
CREATE TABLE IF NOT EXISTS summaries (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    first_id INTEGER NOT NULL,
    last_id  INTEGER NOT NULL,
    turns    INTEGER NOT NULL,
    text     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS turns_ts ON turns(ts);
CREATE INDEX IF NOT EXISTS facts_used ON facts(last_used);
"""


def _clean(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


class Memory:
    """一个进程一个实例；worker 线程和主线程共用，内部加锁。"""

    def __init__(self, path: str | Path | None = None) -> None:
        raw = path if path is not None else paths.LOGS / "kook" / "memory.db"
        self.path = Path(raw)
        if str(raw) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.executescript(SCHEMA)
        self.fts = self._init_fts()

    # ---------------------------------------------------------------- 初始化
    def _init_fts(self) -> bool:
        try:
            with self._db:
                self._db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5("
                    "text, content='facts', content_rowid='id', tokenize='trigram')")
            return True
        except sqlite3.Error:
            return False       # 没有 trigram 就走 LIKE，不挡主流程

    # ---------------------------------------------------------------- 对话
    def add_turn(self, role: str, text: str, msg_id: str = "",
                 image: str = "") -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO turns(ts, role, text, msg_id, image) VALUES(?,?,?,?,?)",
                (time.time(), str(role), str(text), msg_id or None, image or None))
            return int(cur.lastrowid or 0)

    def recent(self, limit: int = 12) -> list[dict]:
        limit = max(1, min(int(limit or 12), 100))
        with self._lock:
            rows = self._db.execute(
                "SELECT role, text, image, ts FROM turns ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def dialogue(self, limit: int = 16) -> str:
        lines = []
        for turn in self.recent(limit):
            who = "用户" if turn["role"] == "user" else "你"
            lines.append(f"{who}：{turn['text']}")
        return "\n".join(lines)

    # ---------------------------------------------------------------- 压缩
    def summary_upto(self) -> int:
        """已经被摘要覆盖到的最大 turn id；0 = 还没压缩过。"""
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM meta WHERE key = 'summary_upto'").fetchone()
        try:
            return int(row["value"]) if row else 0
        except (TypeError, ValueError):
            return 0

    def pending_turns(self) -> int:
        upto = self.summary_upto()
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) n FROM turns WHERE id > ?", (upto,)).fetchone()
        return int(row["n"] or 0)

    def recent_after_summary(self, limit: int = 12) -> list[dict]:
        """摘要之后、最近的原文（进上下文的那一段）。"""
        limit = max(1, min(int(limit or 12), 100))
        upto = self.summary_upto()
        with self._lock:
            rows = self._db.execute(
                "SELECT role, text, image, ts FROM turns WHERE id > ? "
                "ORDER BY id DESC LIMIT ?", (upto, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def compression_batch(self, trigger: int = 24, keep: int = 8,
                          max_batch: int = 40) -> dict:
        """未压缩原文超过 trigger 时，取最老的一批（保留最近 keep 条）去压缩。"""
        trigger = max(4, int(trigger or 24))
        keep = max(2, int(keep or 8))
        upto = self.summary_upto()
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) n FROM turns WHERE id > ?", (upto,)).fetchone()
            pending = int(row["n"] or 0)
            if pending <= trigger:
                return {}
            take = min(pending - keep, max(1, int(max_batch or 40)))
            rows = self._db.execute(
                "SELECT id, role, text FROM turns WHERE id > ? ORDER BY id ASC LIMIT ?",
                (upto, take)).fetchall()
        if not rows:
            return {}
        return {
            "turns": [dict(r) for r in rows],
            "first_id": int(rows[0]["id"]),
            "last_id": int(rows[-1]["id"]),
            "count": len(rows),
        }

    def add_summary(self, text: str, first_id: int, last_id: int,
                    turns: int = 0) -> int:
        body = _clean(text)
        if len(body) < 4:
            return 0
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO summaries(ts, first_id, last_id, turns, text) "
                "VALUES(?,?,?,?,?)",
                (time.time(), int(first_id), int(last_id), int(turns or 0), body))
            current = self._db.execute(
                "SELECT value FROM meta WHERE key = 'summary_upto'").fetchone()
            try:
                old = int(current["value"]) if current else 0
            except (TypeError, ValueError):
                old = 0
            self._db.execute(
                "INSERT INTO meta(key, value) VALUES('summary_upto', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(max(old, int(last_id))),))
            return int(cur.lastrowid or 0)

    def summaries(self, limit: int = 4) -> list[dict]:
        limit = max(1, min(int(limit or 4), 20))
        with self._lock:
            rows = self._db.execute(
                "SELECT id, ts, first_id, last_id, turns, text FROM summaries "
                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def context(self, turns: int = 12, summaries: int = 4) -> dict:
        return {"summaries": self.summaries(summaries),
                "turns": self.recent_after_summary(turns)}

    # ---------------------------------------------------------------- 事实
    def remember(self, text: str, weight: float = 1.0) -> int | None:
        body = _clean(text)
        if len(body) < 2:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT id, text FROM facts WHERE text = ?", (body,)).fetchone()
            if row:
                with self._db:
                    self._db.execute(
                        "UPDATE facts SET weight = weight + ?, last_used = ? WHERE id = ?",
                        (max(0.0, float(weight)), time.time(), row["id"]))
                return int(row["id"])
            # 近似去重：旧事实被新事实包含（或反过来）就只留一条。
            for old in self._db.execute(
                    "SELECT id, text FROM facts ORDER BY id DESC LIMIT 500").fetchall():
                a, b = _clean(old["text"]), body
                if len(a) >= 6 and (a in b or (len(b) >= 6 and b in a)):
                    with self._db:
                        self._db.execute(
                            "UPDATE facts SET weight = weight + ?, last_used = ? WHERE id = ?",
                            (max(0.0, float(weight)), time.time(), old["id"]))
                    return int(old["id"])
            with self._db:
                cur = self._db.execute(
                    "INSERT INTO facts(ts, text, weight, last_used) VALUES(?,?,?,?)",
                    (time.time(), body, max(0.1, float(weight)), time.time()))
                fact_id = int(cur.lastrowid or 0)
                if self.fts:
                    self._db.execute(
                        "INSERT INTO facts_fts(rowid, text) VALUES(?, ?)", (fact_id, body))
            return fact_id

    def facts(self, limit: int = 10) -> list[dict]:
        limit = max(1, min(int(limit or 10), 100))
        with self._lock:
            rows = self._db.execute(
                "SELECT id, text, weight FROM facts "
                "ORDER BY weight DESC, COALESCE(last_used, ts) DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 6) -> list[dict]:
        q = _clean(query)
        limit = max(1, min(int(limit or 6), 30))
        if not q:
            return self.facts(limit)
        rows = []
        if self.fts and len(q) >= 3:
            try:
                with self._lock:
                    rows = self._db.execute(
                        "SELECT f.id, f.text, f.weight FROM facts_fts "
                        "JOIN facts f ON f.id = facts_fts.rowid "
                        "WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",
                        ('"' + q.replace('"', '""') + '"', limit)).fetchall()
            except sqlite3.Error:
                rows = []
        if not rows:
            with self._lock:
                rows = self._db.execute(
                    "SELECT id, text, weight FROM facts WHERE text LIKE ? "
                    "ORDER BY weight DESC, ts DESC LIMIT ?",
                    (f"%{q}%", limit)).fetchall()
        found = [dict(r) for r in rows]
        seen = {int(r["id"]) for r in found}
        if len(found) < min(3, limit):
            for extra in self.facts(limit):
                if int(extra["id"]) not in seen:
                    found.append(extra)
        if found:
            self._touch([int(r["id"]) for r in found])
        return found

    def _touch(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._lock, self._db:
            self._db.executemany(
                "UPDATE facts SET hits = hits + 1, last_used = ? WHERE id = ?",
                [(time.time(), i) for i in ids])

    def forget(self, keyword: str) -> int:
        q = _clean(keyword)
        if not q:
            return 0
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM facts WHERE text LIKE ?", (f"%{q}%",))
            n = int(cur.rowcount or 0)
            if n and self.fts:
                self._db.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
        return n

    # ---------------------------------------------------------------- 去重
    def first_seen(self, msg_id: str) -> bool:
        """True = 第一次见。空 id 一律当新消息（没法去重）。"""
        if not msg_id:
            return True
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO seen(msg_id, ts) VALUES(?, ?)",
                (str(msg_id), time.time()))
            first = int(cur.rowcount or 0) > 0
            if first:
                self._db.execute("DELETE FROM seen WHERE ts < ?",
                                 (time.time() - 30 * 86400,))
        return first

    # ---------------------------------------------------------------- 诊断
    def stats(self) -> dict:
        with self._lock:
            turns = self._db.execute("SELECT COUNT(*) n FROM turns").fetchone()["n"]
            facts = self._db.execute("SELECT COUNT(*) n FROM facts").fetchone()["n"]
            summaries = self._db.execute(
                "SELECT COUNT(*) n FROM summaries").fetchone()["n"]
            meta = self._db.execute(
                "SELECT value FROM meta WHERE key = 'summary_upto'").fetchone()
            try:
                upto = int(meta["value"]) if meta else 0
            except (TypeError, ValueError):
                upto = 0
            pending = self._db.execute(
                "SELECT COUNT(*) n FROM turns WHERE id > ?", (upto,)).fetchone()["n"]
        return {"path": str(self.path), "turns": int(turns), "facts": int(facts),
                "summaries": int(summaries), "pending": int(pending or 0),
                "fts": bool(self.fts)}

    def close(self) -> None:
        try:
            self._db.close()
        except sqlite3.Error:
            pass


__all__ = ["Memory"]
