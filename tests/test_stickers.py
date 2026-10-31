# -*- coding: utf-8 -*-
"""大肥鱼表情包：素材、清单、聊天回复协议。"""
from __future__ import annotations

from PIL import Image

from dafeiyu.live import chat as chat_mod
from dafeiyu.persona import stickers


def test_pack_has_images_and_text():
    items = stickers.items()
    assert len(items) >= 8
    for row in items:
        assert row["exists"], row
        assert row["text"]
        with Image.open(row["path"]) as img:
            assert img.size == (512, 512)
            assert img.format == "PNG"


def test_path_of_unknown_is_none():
    assert stickers.path_of("nope") is None
    assert stickers.path_of("one-bowl") is not None


def test_parse_reply_keeps_sticker_id():
    raw = '{"reply": "稳的", "voice": false, "voice_text": "", "sticker": "one-bowl"}'
    got = chat_mod.parse_reply(raw)
    assert got["text"] == "稳的" and got["sticker"] == "one-bowl"
    # 没选表情包时不多加字段，旧调用方不用改。
    assert "sticker" not in chat_mod.parse_reply("普通一句")


def test_parse_reply_supports_message_array():
    got = chat_mod.parse_reply(
        '{"messages": ["在", "刚看到一只猫"], "voice": false}')
    assert got["texts"] == ["在", "刚看到一只猫"]
    assert got["text"] == "在\n刚看到一只猫"
    assert got["voice"] is False


def test_parse_reply_empty_object_is_empty():
    got = chat_mod.parse_reply("{}")
    assert got["text"] == "" and got["texts"] == []


def test_prompt_lists_sticker_ids():
    from dafeiyu.live.chat import build_system_prompt
    from dafeiyu.persona.card import PersonaCard

    text = build_system_prompt(PersonaCard.load("companion"), [],
                               sticker_options=stickers.options())
    assert "sticker" in text and "one-bowl" in text and "我能去你家吃饭吗" in text
    assert '"messages"' in text and "1–3 条短消息" in text
