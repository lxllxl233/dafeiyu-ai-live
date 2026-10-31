# -*- coding: utf-8 -*-
"""性格层：个人 → 直播 → 游戏 的固定叠加顺序。"""
from __future__ import annotations

from dafeiyu.core.config import LiveConfig
from dafeiyu.persona.layers import PersonaStack, prompt_prefix


def test_three_layers_in_order_with_game_layer():
    stack = PersonaStack.load("dafeiyu", game_id="slay-the-spire-2", live=True,
                              include_inactive=True)
    assert [layer.scope for layer in stack.layers] == ["personal", "live", "game"]
    assert all(layer.active for layer in stack.layers)
    prompt = stack.prompt()
    assert prompt.index("【个人性格】") < prompt.index("【直播性格】") < prompt.index("【游戏性格】")
    assert "杀戮尖塔" in prompt
    assert "大肥鱼" in stack.card.name


def test_live_and_game_are_optional():
    stack = PersonaStack.load("dafeiyu", game_id="", live=False)
    assert [layer.scope for layer in stack.layers] == ["personal"]
    assert "【直播性格】" not in stack.prompt()


def test_persona_live_switch_respected():
    cfg = LiveConfig(persona="dafeiyu", persona_live=False, game="slay-the-spire-2")
    text = prompt_prefix(cfg, game_id="slay-the-spire-2", live=True)
    assert "【直播性格】" not in text
    assert "【游戏性格】" in text


def test_game_layer_missing_is_skipped():
    stack = PersonaStack.load("dafeiyu", game_id="toy-duel", live=False)
    assert [layer.scope for layer in stack.layers] == ["personal"]


def test_companion_has_chat_style_layer():
    stack = PersonaStack.load("companion", live=False)
    assert stack.layers[0].scope == "personal"
    assert stack.layers[0].text.strip()
    assert "私聊" in stack.prompt() or "聊天" in stack.prompt()
