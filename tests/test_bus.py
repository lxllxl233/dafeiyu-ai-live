from __future__ import annotations

from dafeiyu.core.bus import EventBus
from dafeiyu.core.events import Event, Topics


def test_publish_and_wildcard_subscribe():
    bus = EventBus()
    seen = []
    bus.subscribe("game.*", lambda ev: seen.append(ev.topic))
    bus.publish(Topics.GAME_STATE, {"floor": 1}, source="test")
    bus.publish("voice.ready", {"voice": "animal"})
    assert seen == [Topics.GAME_STATE]


def test_history_and_stats():
    bus = EventBus()
    bus.publish("a.b", {"x": 1})
    bus.publish("a.c", {"x": 2})
    assert [h["topic"] for h in bus.history(10)] == ["a.b", "a.c"]
    assert bus.stats()["published"] == 2


def test_handler_error_does_not_break_others():
    bus = EventBus()
    seen = []

    def boom(_ev):
        raise RuntimeError("handler bug")

    bus.subscribe("*", boom)
    bus.subscribe("*", lambda ev: seen.append(ev.topic))
    bus.publish("x.y")
    assert seen == ["x.y"]
    assert bus.stats()["errors"] == 1


def test_wait_for_returns_matching_event():
    import threading

    bus = EventBus()
    threading.Timer(0.05, lambda: bus.publish("voice.ready", {"voice": "mambo"})).start()
    ev = bus.wait_for("voice.ready", timeout=1.0)
    assert isinstance(ev, Event)
    assert ev.data["voice"] == "mambo"
