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


# ---------------------------------------------------------------- 起错游戏（实机反馈）
class _RecordingRegistry:
    def __init__(self, runner: _FakeRunner) -> None:
        self.runner = runner
        self.asked: list[str] = []

    def load_plugin(self, game_id: str):
        self.asked.append(game_id)
        return _FakePlugin(self.runner)


def _cfg(game: str = "slay-the-spire-2"):
    from types import SimpleNamespace

    return SimpleNamespace(game=game, autoplay_mode="llm", autoplay_pace="voice")


def test_start_uses_config_game_not_stale_status(monkeypatch):
    """toy-duel 验收跑完后状态文件还留着 game=toy-duel；点「让她玩」必须启动配置里的游戏。"""
    runner = _FakeRunner()
    reg = _RecordingRegistry(runner)
    monkeypatch.setattr(GameRegistry, "load_plugin", reg.load_plugin)
    monkeypatch.setattr(play.LiveConfig, "load", lambda: _cfg())
    monkeypatch.setattr(play, "_read_status",
                        lambda: {"game": "toy-duel", "running": False})

    out = play.start_autoplay(mode="llm")
    assert out["ok"] is True
    assert reg.asked == ["slay-the-spire-2"]
    assert runner.calls == [("start", "slay-the-spire-2", "llm", None)]


def test_status_ignores_stale_status_when_nothing_is_running(monkeypatch):
    runner = _FakeRunner()
    reg = _RecordingRegistry(runner)
    monkeypatch.setattr(GameRegistry, "load_plugin", reg.load_plugin)
    monkeypatch.setattr(play.LiveConfig, "load", lambda: _cfg())
    monkeypatch.setattr(play, "_read_status",
                        lambda: {"game": "toy-duel", "running": False})

    play.status()
    assert reg.asked == ["slay-the-spire-2"]


def test_stop_still_follows_the_running_game(monkeypatch):
    """停手要停正在跑的那一局，即使 config.game 已经切走。"""
    runner = _FakeRunner()
    reg = _RecordingRegistry(runner)
    monkeypatch.setattr(GameRegistry, "load_plugin", reg.load_plugin)
    monkeypatch.setattr(play.LiveConfig, "load", lambda: _cfg())
    monkeypatch.setattr(play, "_read_status",
                        lambda: {"game": "toy-duel", "running": True})

    out = play.stop_autoplay()
    assert out["ok"] is True
    assert reg.asked == ["toy-duel"]
    assert runner.calls[-1] == ("stop",)


def test_explicit_game_still_wins(monkeypatch):
    runner = _FakeRunner()
    reg = _RecordingRegistry(runner)
    monkeypatch.setattr(GameRegistry, "load_plugin", reg.load_plugin)
    monkeypatch.setattr(play.LiveConfig, "load", lambda: _cfg())
    monkeypatch.setattr(play, "_read_status", lambda: {"game": "toy-duel",
                                                       "running": True})

    play.start_autoplay("toy-duel", mode="local")
    assert reg.asked == ["toy-duel"]
