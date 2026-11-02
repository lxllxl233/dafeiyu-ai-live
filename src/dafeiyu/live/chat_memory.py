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


_PUNCT = "，。！？、；：,.!?;:\"'“”‘’（）()【】[]{}<>《》 \t\n"
# 分簇用的高频功能字；剩下的汉字当作主题字（猫/麦/班/offer 之类）。
_TOPIC_COMMON = set(
    "的一是不了在人有我他她它这那为之大来以个中上们到说国和地也子时道出而要"
    "于就下得可你年生自会后能对着事其里所去行过家十用发天如然作方成者多日都"
    "三二无同么经法当起与好看学进种将还分此心前又定见只主没公从想什怎样现点"
    "觉真做给对开很太先再才已经因所但刚今明昨早晚午秒份号岁次些每各另别外最"
    "更挺蛮特别比较稍微差点儿几乎差不多小大老新和与及或等")


def _norm(text: str) -> str:
    return "".join(ch for ch in _clean(text) if ch not in _PUNCT)


def _grams(text: str, size: int = 2) -> set[str]:
    body = _norm(text)
    if len(body) < size:
        return {body} if body else set()
    return {body[i:i + size] for i in range(len(body) - size + 1)}


def _topic_chars(text: str) -> set[str]:
    """事实里的主题字；用来把“同一只猫的不同细节”聚成一簇。"""
    return {ch for ch in str(text or "")
            if "\u4e00" <= ch <= "\u9fff" and ch not in _TOPIC_COMMON}


def _similar(a: str, b: str) -> bool:
    """中文短事实的近义判断：包含关系或二元组 Jaccard 足够高。"""
    left, right = _norm(a), _norm(b)
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    if abs(len(left) - len(right)) / max(len(left), len(right)) > 0.75:
        return False
    ga, gb = _grams(left), _grams(right)
    if not ga or not gb:
        return False
    return len(ga & gb) / max(1, len(ga | gb)) >= 0.45


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
            # 近似去重：旧事实被新事实包含、包含新事实，或二元组高度重合，
            # 都只留一条。否则「养了一只猫」「养了两只猫」「猫会半夜喂」
            # 会攒成一堆，主动消息检索时全被同一只猫占满。
            for old in self._db.execute(
                    "SELECT id, text FROM facts ORDER BY id DESC LIMIT 500").fetchall():
                if _similar(old["text"], body):
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
            # 没有命中时不要直接甩“权重最高的几条”（往往全是猫），
            # 走一次主题分簇，普通聊天也不至于每次都接同一件旧事。
            for extra in self.diverse_facts(limit, avoid_texts=[q]):
                if int(extra["id"]) not in seen:
                    found.append(extra)
        if found:
            self._touch([int(r["id"]) for r in found])
        return found

    def diverse_facts(self, limit: int = 6,
                      avoid_texts: list[str] | tuple[str, ...] = ()) -> list[dict]:
        """主动消息用的事实：按「久未用 + 权重」排序，再按主题分簇。

        普通 ``search`` 会按 weight/last_used 把最常被引用的几条一直排在最前，
        主动消息没话找话时就容易反复讲同一只猫。这里：
        - 越久没用过的事实分越高（避免同一条一直霸榜）；
        - 和最近已发内容高度重合的降权；
        - 同一簇只取一条（猫/工作/家庭各留一条），让模型有换话题的空间。
        """
        limit = max(1, min(int(limit or 6), 30))
        with self._lock:
            rows = self._db.execute(
                "SELECT id, text, weight, hits, last_used, ts FROM facts "
                "ORDER BY weight DESC, COALESCE(last_used, ts) ASC LIMIT 80").fetchall()
        if not rows:
            return []
        now = time.time()
        avoid_grams: set[str] = set()
        for text in avoid_texts or ():
            avoid_grams |= _grams(str(text or ""))
        scored: list[tuple[float, dict]] = []
        for row in rows:
            item = dict(row)
            last = float(item.get("last_used") or item.get("ts") or 0.0)
            age_days = max(0.0, (now - last) / 86400.0)
            try:
                weight = float(item.get("weight") or 1.0)
            except (TypeError, ValueError):
                weight = 1.0
            score = min(weight, 5.0) * 0.4 + min(age_days, 7.0) * 0.6
            if avoid_grams:
                grams = _grams(item.get("text") or "")
                if grams:
                    score -= 8.0 * len(grams & avoid_grams) / max(1, len(grams))
            scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        # 在全部候选里高频出现的主题字（猫/班/游戏）才适合当“簇”的标记；
        # “白”这种只出现在两条里的字不能用来聚类。
        char_counts: dict[str, int] = {}
        for _score, item in scored:
            for char in _topic_chars(item.get("text") or ""):
                char_counts[char] = char_counts.get(char, 0) + 1
        strong_topics = {char for char, count in char_counts.items() if count >= 3}
        chosen: list[dict] = []
        chosen_topics: set[str] = set()
        for _score, item in scored:
            text = item.get("text") or ""
            topics = _topic_chars(text)
            shared = topics & chosen_topics
            # 同一簇（比如所有“猫”的事实）只取一条，否则六条里五条是猫。
            # 至少共享两个主题字，或共享一个跨多条事实的高频主题字（猫），
            # 才算同簇；避免“橘白”和“白领”因为一个“白”字被误判。
            if len(shared) >= 2 or (shared & strong_topics):
                continue
            if any(_similar(text, row.get("text") or "") for row in chosen):
                continue
            chosen.append(item)
            chosen_topics |= topics
            if len(chosen) >= limit:
                break
        if chosen:
            self._touch([int(row["id"]) for row in chosen])
        return chosen

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
