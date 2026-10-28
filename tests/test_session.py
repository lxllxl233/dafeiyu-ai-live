from __future__ import annotations

import time

from dafeiyu.core.bus import EventBus
from dafeiyu.core.config import LiveConfig
from dafeiyu.core.events import Topics
from dafeiyu.core.session import LiveSession
from dafeiyu.persona import Persona
from dafeiyu.voice.base import VoiceResult


class FakeVoice:
    name = "fake"

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def available(self) -> bool:
        return True

    def synth(self, text: str, volume: float | None = None) -> VoiceResult:
        self.spoken.append(text)
        return VoiceResult(wav=b"RIFF" + b"0" * 60, voice="fake", duration=1.23)

    def speak(self, text: str, voice: str | None = None, volume: float | None = None) -> VoiceResult:
        return self.synth(text, volume)

    def status(self) -> dict:
        return {"name": "fake", "available": True}


class FakePublisher:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def push(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return {"ok": True, "id": 42}

    def ping(self) -> dict:
        return {"ok": True, "clients": 2, "seq": 9}


def test_say_pushes_and_speaks():
    bus = EventBus()
    voice = FakeVoice()
    publisher = FakePublisher()
    cfg = LiveConfig()
    cfg.voice = "fake"
    cfg.push_overlay = True
    session = LiveSession(cfg, bus=bus, persona=Persona.load(), voice=voice, publisher=publisher)
    got: list[str] = []
    bus.subscribe(Topics.VOICE_READY, lambda ev: got.append(ev.data["voice"]))
    session.start()
    try:
        res = session.say("它 21，我最多挡 15", "差 6，嗨呀。")
        assert res.ok and res.text.endswith("嗨呀。")
        assert publisher.payloads[-1]["speech"] == res.text
        assert publisher.payloads[-1]["client_id"] == res.client_id
        assert session.wait_idle(3)
        assert voice.spoken == [res.text]
        assert got == ["fake"]
        assert session.status()["session"]["voice_ok"] == 1
    finally:
        session.stop()


def test_say_without_push_and_empty_text():
    cfg = LiveConfig()
    cfg.push_overlay = False
    session = LiveSession(cfg, persona=Persona.load(), voice=FakeVoice(), publisher=FakePublisher())
    session.start()
    try:
        res = session.say(text="它 21。", push=False, speak=False)
        assert res.ok and res.pushed.get("skipped") is True
        empty = session.say("", "")
        assert not empty.ok
    finally:
        session.stop()


def test_state_merges_into_comment_payload():
    cfg = LiveConfig()
    cfg.voice = "fake"
    publisher = FakePublisher()
    session = LiveSession(cfg, persona=Persona.load(), voice=FakeVoice(), publisher=publisher)
    session.update_state({"character": "铁甲战士", "floor": 6, "hp": 52, "max_hp": 80,
                          "screen": "COMBAT"})
    session.start()
    try:
        session.say(text="它 21。", speak=False)
        payload = publisher.payloads[-1]
        assert payload["character"] == "铁甲战士"
        assert payload["floor"] == 6
        assert payload["hp"] == 52
    finally:
        session.stop()


def test_status_shape():
    session = LiveSession(LiveConfig(), persona=Persona.load(), voice=FakeVoice(),
                          publisher=FakePublisher())
    status = session.status()
    for key in ("session", "config", "persona", "voice", "publisher", "game", "bus"):
        assert key in status
    assert status["publisher"]["ok"] is True
    assert status["session"]["started"] is False
