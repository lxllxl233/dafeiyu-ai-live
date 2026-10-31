# -*- coding: utf-8 -*-
"""聊天长期记忆：原文、压缩摘要、上下文窗口。"""
from __future__ import annotations

from dafeiyu.live.chat_memory import Memory


def _fill(memory: Memory, n: int) -> None:
    for i in range(n):
        memory.add_turn("user" if i % 2 == 0 else "assistant", f"第 {i} 句")


def test_compression_batch_keeps_recent_turns(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    try:
        _fill(memory, 30)
        assert memory.pending_turns() == 30
        batch = memory.compression_batch(trigger=20, keep=6)
        assert batch["count"] == 24
        assert batch["first_id"] == 1 and batch["last_id"] == 24
        assert memory.compression_batch(trigger=20, keep=6)["count"] == 24  # 未提交前可重算
    finally:
        memory.close()


def test_add_summary_advances_context_window(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    try:
        _fill(memory, 30)
        batch = memory.compression_batch(trigger=20, keep=6)
        assert memory.add_summary("对方在准备直播，约定周末一起看片。",
                                  batch["first_id"], batch["last_id"], batch["count"])
        assert memory.pending_turns() == 6
        context = memory.context(turns=8)
        assert len(context["summaries"]) == 1
        assert "周末" in context["summaries"][0]["text"]
        assert len(context["turns"]) == 6
        assert context["turns"][0]["text"] == "第 24 句"
        stats = memory.stats()
        assert stats["summaries"] == 1 and stats["pending"] == 6
    finally:
        memory.close()


def test_summary_requires_text(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    try:
        _fill(memory, 4)
        assert memory.add_summary("", 1, 2, 2) == 0
        assert memory.summary_upto() == 0
    finally:
        memory.close()
