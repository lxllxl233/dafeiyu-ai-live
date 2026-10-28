from __future__ import annotations

from dafeiyu.agent import play
from dafeiyu.games.registry import GameRegistry


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def start(self, *, game: str = "", mode: str | None = None,
              pace: str | None = None) -> dict:
        self.calls.append(("start", game, mode, pace))
        return {"ok": True, "message": "fake start"}

    def stop(self) -> dict:
        self.calls.append(("stop",))
        return {"ok": True, "message": "fake stop"}

    def status(self) -> dict:
        return {"running": False, "game": "demo", "last": "fake"}


class _FakePlugin:
    def __init__(self, runner: _FakeRunner) -> None:
        self._runner = runner

    def runner(self, host=None):
        self.host = host
        return self._runner


def test_facade_dispatches_to_the_plugin_runner(monkeypatch):
    runner = _FakeRunner()
    plugin = _FakePlugin(runner)
    monkeypatch.setattr(GameRegistry, "load_plugin",
                        lambda self, game_id: plugin)
    monkeypatch.setattr(play, "_read_status", lambda: {})

    started = play.start_autoplay("demo", mode="local", pace="voice")
    assert started["ok"] is True
    assert runner.calls == [("start", "demo", "local", "voice")]
    assert plugin.host is not None

    monkeypatch.setattr(play, "_read_status", lambda: {"game": "demo"})
    stopped = play.stop_autoplay()
    assert stopped["message"] == "fake stop"
    assert runner.calls[-1] == ("stop",)

    assert play.status()["last"] == "fake"


def test_facade_returns_stable_error_when_plugin_is_missing(monkeypatch):
    monkeypatch.setattr(GameRegistry, "load_plugin", lambda self, game_id: None)
    monkeypatch.setattr(play, "_read_status", lambda: {})
    out = play.start_autoplay("demo")
    assert out["ok"] is False
    assert "插件" in out["message"]
    assert play.status()["running"] is False
    assert play.stop_autoplay()["ok"] is True
