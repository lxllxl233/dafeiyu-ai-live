"""桌宠轻点：LLM 回复的提示词、清洗、失败回退和 UI 线程落点。"""
from __future__ import annotations

import threading

from dafeiyu.core.config import LiveConfig
from dafeiyu.live import pet_chat
from dafeiyu.ui import desktop


class FakeLLM:
    def __init__(self, result=None):
        self.result = result or {"ok": True, "text": '{"reply": "唔…你戳我干嘛。"}'}
        self.messages = None
        self.kwargs = None

    def complete(self, messages, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        return self.result


def test_click_reply_uses_persona_prompt_and_cleans_json():
    cfg = LiveConfig(llm_api_key="test", persona="dafeiyu")
    llm = FakeLLM()
    result = pet_chat.click_reply(cfg, llm=llm, prompt="（主人戳了戳你）")

    assert result["ok"] is True
    assert result["text"] == "唔…你戳我干嘛。"
    system = llm.messages[0]["content"]
    assert "大肥鱼" in system
    assert "桌宠" in system
    assert "只输出这一句话" in system
    assert llm.messages[-1]["content"] == "（主人戳了戳你）"
    assert llm.kwargs["max_tokens"] == 120
    assert llm.kwargs["trace_note"] == "桌宠点击"


def test_click_reply_accepts_multiple_messages_and_plain_text():
    cfg = LiveConfig(llm_api_key="test")
    multi = FakeLLM({"ok": True, "text": '{"messages": ["在呢。", "怎么啦？"]}'})
    assert pet_chat.click_reply(cfg, llm=multi)["text"] == "在呢。 怎么啦？"

    plain = FakeLLM({"ok": True, "text": "别戳啦，痒。"})
    assert pet_chat.click_reply(cfg, llm=plain)["text"] == "别戳啦，痒。"


def test_click_reply_reports_model_failures_and_empty_text():
    cfg = LiveConfig(llm_api_key="test")
    failed = FakeLLM({"ok": False, "error": "HTTP 500"})
    result = pet_chat.click_reply(cfg, llm=failed)
    assert result["ok"] is False
    assert result["error"] == "HTTP 500"

    empty = FakeLLM({"ok": True, "text": "```json\n{}\n```"})
    assert pet_chat.click_reply(cfg, llm=empty)["ok"] is False


def test_click_reply_without_key_does_not_hit_network():
    cfg = LiveConfig(llm_api_key="", persona="dafeiyu")
    result = pet_chat.click_reply(cfg)
    assert result["ok"] is False
    assert "API Key" in result["error"]


def test_click_fallback_is_a_local_line():
    assert pet_chat.fallback_line() in pet_chat.FALLBACK_LINES


def test_pet_reply_worker_pushes_text_and_clears_busy(monkeypatch):
    monkeypatch.setattr(pet_chat, "click_reply",
                        lambda: {"ok": True, "text": "在呢。", "error": ""})
    win = desktop.PetWindow.__new__(desktop.PetWindow)
    win.running = True
    win._pet_chat_busy = True
    win._pet_chat_lock = threading.Lock()
    win.dirty = False
    pushed = []
    win.push = lambda thought, meme, status=None: pushed.append((thought, meme))

    win._pet_reply_worker()

    assert pushed == [("", "在呢。")]
    assert win._pet_chat_busy is False
    assert win.dirty is True


def test_pet_reply_worker_falls_back_when_model_fails(monkeypatch):
    monkeypatch.setattr(pet_chat, "click_reply",
                        lambda: {"ok": False, "text": "", "error": "no key"})
    monkeypatch.setattr(pet_chat, "fallback_line", lambda: "在呢，怎么啦？")
    win = desktop.PetWindow.__new__(desktop.PetWindow)
    win.running = True
    win._pet_chat_busy = True
    win._pet_chat_lock = threading.Lock()
    win.dirty = False
    pushed = []
    win.push = lambda thought, meme, status=None: pushed.append((thought, meme))

    win._pet_reply_worker()

    assert pushed == [("", "在呢，怎么啦？")]
    assert win._pet_chat_busy is False
