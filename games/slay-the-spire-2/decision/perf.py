# -*- coding: utf-8 -*-
"""统一轻量计时层：把「一次上层决策 → 多次底层调用」串成一条 trace。

为什么要它
----------
run92 的教训：结算「卡很久」查了半天才发现是 `_player_turn_state` 对着非战斗
屏幕空转 8 秒 × 11 次 = 90 秒。**没有全链路计时，就只能靠猜。** 所以这里给
执行链路加一层统一的、几乎零开销的观测：

  · 每条 trace 一行 JSON → `logs/perf.jsonl`（默认开，`STS2_PERF=0` 可关）
  · stage 之间自动算「实际执行时间」；等待类 stage 另外记 polls/sleep_ms
  · `perf.count()` 记全局计数（状态读取次数、动作次数），trace 结束时快照差值
  · 只写阶段名和毫秒，不写游戏状态快照，不碰 stdout（stdout 是给模型的 JSON）

口径
----
`act.py` 一次调用 = 一条 trace：
  import / state / validate / run_batch / ctx / settle / total
嵌套计时只记**叶子**耗时，父级 duration 自己算，不做简单相加，避免重复计算。
"""
from __future__ import annotations

import json
import os
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_PACK = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(_PACK))
LOG = os.path.join(_ROOT, "logs", "perf.jsonl")
_ENABLED = os.environ.get("STS2_PERF", "1") != "0"
_LOCK = threading.Lock()
_COUNTERS: dict[str, int] = {}
_CURRENT = threading.local()


def enabled() -> bool:
    return _ENABLED


def count(name: str, n: int = 1) -> None:
    """全局计数（状态读取、动作发送、轮询）。开销：一次字典加法。"""
    if not _ENABLED:
        return
    _COUNTERS[name] = _COUNTERS.get(name, 0) + n


def snapshot() -> dict:
    return dict(_COUNTERS)


def current():
    return getattr(_CURRENT, "trace", None)


class Stage:
    __slots__ = ("name", "t0", "trace", "sleep_ms", "polls")

    def __init__(self, trace: "Trace", name: str):
        self.trace = trace
        self.name = name
        self.t0 = time.time()
        self.sleep_ms = 0.0
        self.polls = 0

    def slept(self, sec: float) -> None:
        self.sleep_ms += sec * 1000.0

    def poll(self, n: int = 1) -> None:
        self.polls += n

    def __enter__(self) -> "Stage":
        return self

    def __exit__(self, *exc) -> None:
        self.trace._close(self)


class Trace:
    def __init__(self, name: str, meta: dict | None = None):
        self.name = name
        self.meta = meta or {}
        self.t0 = time.time()
        self.t_end = 0.0
        self.stages: list[dict] = []
        self.counters: dict[str, int] = {}
        self.base = snapshot() if _ENABLED else {}
        self.exit = "ok"

    def stage(self, name: str) -> Stage:
        return Stage(self, name)

    def _close(self, st: Stage) -> None:
        ms = (time.time() - st.t0) * 1000.0
        self.stages.append({
            "name": st.name,
            "ms": round(ms, 1),
            # 主动等待（time.sleep）占比：区分「真的在干活」和「白等」
            "sleep_ms": round(st.sleep_ms, 1),
            "polls": st.polls,
        })

    def note(self, **kw) -> None:
        self.meta.update(kw)

    def __enter__(self) -> "Trace":
        _CURRENT.trace = self
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.exit = "error"
            self.meta.setdefault("err", f"{exc_type.__name__}: {exc}")
        try:
            if _ENABLED:
                now = snapshot()
                self.counters = {k: now.get(k, 0) - self.base.get(k, 0)
                                 for k in now if now.get(k, 0) != self.base.get(k, 0)}
                self.t_end = time.time()
                row = {
                    "t": round(self.t0, 3),
                    "trace": self.name,
                    "ms": round((self.t_end - self.t0) * 1000.0, 1),
                    "exit": self.exit,
                    "stages": self.stages,
                    "counters": self.counters,
                }
                if self.meta:
                    row["meta"] = self.meta
                _write(row)
        finally:
            _CURRENT.trace = None


def _write(row: dict) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        line = json.dumps(row, ensure_ascii=False)
        with _LOCK:
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def trace(name: str, **meta) -> Trace:
    return Trace(name, meta)


def stage(name: str):
    """在已有 trace 里开一个 stage；没有 trace 时退化成空操作。"""
    tr = current()
    if tr is None:
        return _NullStage()
    return tr.stage(name)


class _NullStage:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def slept(self, sec: float) -> None:
        pass

    def poll(self, n: int = 1) -> None:
        pass
