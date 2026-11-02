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


def test_near_duplicate_facts_merge(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    try:
        first = memory.remember("对方来自单亲家庭，小时候没人管")
        second = memory.remember("对方是单亲家庭长大，小时候没人管")
        assert first == second
        assert len(memory.facts()) == 1
    finally:
        memory.close()


def test_diverse_facts_dedupes_cat_cluster(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    try:
        memory.remember("对方养了两只猫，分别叫小新和小麦，小麦是橘白的大面包", 3.0)
        memory.remember("对方养了一只猫，会半夜起来喂猫", 2.0)
        memory.remember("对方正在等一个口头承诺的 offer，尚未收到正式通知", 1.0)
        memory.remember("对方是白领，坐办公室工作", 1.0)
        picked = memory.diverse_facts(3, avoid_texts=["刚又看到猫在窗台"])
        texts = [row["text"] for row in picked]
        assert len(picked) == 3
        assert any("offer" in text for text in texts)
        # 猫这一簇最多出现一条，不能三条全是猫。
        assert sum("猫" in text for text in texts) <= 1
    finally:
        memory.close()
