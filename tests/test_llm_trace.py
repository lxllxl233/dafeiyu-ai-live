# -*- coding: utf-8 -*-
"""模型轨迹：图片不落 base64、分页/详情、ChatLLM 的 thought/reasoning。"""
from __future__ import annotations

import json
from pathlib import Path

from dafeiyu.core.config import LiveConfig
from dafeiyu.live import llm_trace
from dafeiyu.live.chat import ChatLLM


def _append(path: Path, **overrides) -> dict:
    data = {
        "source": "KOOK 聊天",
        "model": "deepseek-test",
        "messages": [
            {"role": "system", "content": "人设与记忆"},
            {"role": "user", "content": "在吗"},
        ],
        "reply": '{"thought": "先接一句", "messages": ["在"]}',
        "thought": "先接一句",
        "reasoning": "",
        "ok": True,
        "ms": 42,
        "usage": {"input": 12, "output": 3, "total": 15},
        "meta": {"msg_id": "m1"},
        "path": path,
    }
    data.update(overrides)
    return llm_trace.append(**data)


def test_trace_redacts_image_and_secret(tmp_path):
    path = tmp_path / "trace.jsonl"
    row = _append(path, messages=[
        {"role": "system", "content": "Key sk-1234567890abcdef SESSDATA=secret"},
        {"role": "user", "content": [
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]},
    ])
    assert row and row["id"]
    text = path.read_text(encoding="utf-8")
    assert "base64" not in text and "sk-1234567890abcdef" not in text
    assert "SESSDATA=secret" not in text and "[图片]" in text
    assert "看这张图" in text


def test_trace_page_is_newest_first_and_paginated(tmp_path):
    path = tmp_path / "trace.jsonl"
    _append(path, source="KOOK 聊天", thought="第一条")
    _append(path, source="KOOK 主动消息", thought="第二条")
    _append(path, source="B站视频学习", thought="第三条")

    first = llm_trace.page(page=1, per=2, path=path)
    assert first["ok"] is True and first["total"] == 3 and first["pages"] == 2
    assert [row["thought"] for row in first["items"]] == ["第三条", "第二条"]
    assert first["stats"]["total"] == 3

    second = llm_trace.page(page=2, per=2, path=path)
    assert [row["thought"] for row in second["items"]] == ["第一条"]

    only = llm_trace.page(page=1, per=10, source="KOOK 主动消息", path=path)
    assert only["total"] == 1
    assert only["items"][0]["source"] == "KOOK 主动消息"


def test_trace_detail_contains_full_messages(tmp_path):
    path = tmp_path / "trace.jsonl"
    row = _append(path, messages=[
        {"role": "system", "content": "完整人设" * 100},
        {"role": "user", "content": "触发消息"},
    ])
    got = llm_trace.get(row["id"], path=path)
    assert got and got["messages"][0]["content"].startswith("完整人设")
    assert got["meta"]["msg_id"] == "m1"
    assert llm_trace.get("missing", path=path) is None


def test_chat_llm_logs_thought_and_reasoning(tmp_path, monkeypatch):
    path = tmp_path / "trace.jsonl"
    monkeypatch.setattr(llm_trace, "TRACE_PATH", path)

    class Response:
        status_code = 200

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": json.dumps(
                            {"thought": "先确认她是不是在忙", "messages": ["在"]},
                            ensure_ascii=False),
                        "reasoning_content": "她问在吗，先接住。",
                    }
                }],
                "usage": {"prompt_tokens": 20, "completion_tokens": 6,
                          "total_tokens": 26},
            }

    monkeypatch.setattr("dafeiyu.live.chat.requests.post",
                        lambda *args, **kwargs: Response())
    cfg = LiveConfig(llm_base_url="https://api.deepseek.com/v1",
                     llm_api_key="test-key", llm_model="deepseek-flash")
    result = ChatLLM(cfg).complete(
        [{"role": "user", "content": "在吗"}], json_mode=True,
        trace_note="KOOK 聊天", trace_meta={"msg_id": "m1"})
    assert result["ok"] is True
    assert result["thought"] == "先确认她是不是在忙"
    assert result["reasoning"] == "她问在吗，先接住。"

    page = llm_trace.page(page=1, per=10, path=path)
    assert page["total"] == 1
    item = page["items"][0]
    assert item["source"] == "KOOK 聊天"
    assert item["has_thought"] is True and item["has_reasoning"] is True
    assert item["msg_id"] == "m1"
    assert item["preview"] == "在吗"
