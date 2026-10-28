"""跨游戏 AutoplayHost：状态、停止、录像、token、语音节奏。"""

from __future__ import annotations

import json

from dafeiyu.agent.host import AutoplayHost
from dafeiyu.recording import auto


def _host(tmp_path) -> AutoplayHost:
    host = AutoplayHost("demo")
    host.status_path = tmp_path / "autoplay.json"
    host.stop_path = tmp_path / "autoplay.stop"
    host.log_path = tmp_path / "autoplay.jsonl"
    return host


def test_host_status_stop_and_tokens(tmp_path):
    host = _host(tmp_path)
    assert host.status()["game"] == "demo"
    host.reset("demo", mode="llm", pace="normal")
    assert host.status()["game"] == "demo"
    assert host.status()["stop_requested"] is False

    host.set_status(running=True, pid=123, last="打牌")
    assert host.status()["running"] is True
    assert host.status()["last"] == "打牌"

    host.request_stop()
    assert host.stopping() is True
    assert host.status()["stop_requested"] is True
    host.clear_stop()
    assert host.stopping() is False

    host.add_usage({"input": 120, "output": 30, "total": 150}, "run-a")
    host.add_usage({"input": 80, "output": 20, "total": 100}, "run-b")
    st = host.status()
    assert (st["tokens_in"], st["tokens_out"], st["tokens_total"]) == (200, 50, 250)
    assert (st["llm_calls"], st["tokens_last"]) == (2, 100)


def test_status_ignores_stale_game_from_finished_run(tmp_path):
    """上一局（toy-duel）留下的状态文件不能盖住当前宿主的游戏。"""
    host = _host(tmp_path)
    host.reset("demo", mode="llm", pace="normal")
    host.status_path.write_text(json.dumps({
        "game": "toy-duel", "mode": "local", "pace": "voice",
        "running": False, "last": "上一局",
    }), encoding="utf-8")

    st = host.status()
    assert st["game"] == "demo"
    assert st["mode"] == "llm"
    assert st["last"] == "上一局"        # 上次跑到哪仍然可看

    host.status_path.write_text(json.dumps({
        "game": "toy-duel", "mode": "local", "running": True,
    }), encoding="utf-8")
    st = host.status()
    assert st["game"] == "toy-duel"     # 真在跑就以文件为准
    assert st["mode"] == "local"


def test_host_recording_lifecycle(tmp_path, monkeypatch):
    host = _host(tmp_path)
    host.reset("demo")
    calls = []

    def fake_start(game, note=""):
        calls.append(("start", game, note))
        return {"ok": True, "path": str(tmp_path / "run-0001")}

    def fake_stop(result=""):
        calls.append(("stop", result))
        return {"ok": True, "path": str(tmp_path / "run-0001" / "run.mp4")}

    monkeypatch.setattr(auto, "start_session", fake_start)
    monkeypatch.setattr(auto, "stop_session", fake_stop)

    assert host.start_recording(note="自动玩")["ok"] is True
    assert host.status()["recording"] is True
    assert host.stop_recording("done")["ok"] is True
    assert host.status()["recording"] is False
    assert calls == [("start", "demo", "自动玩"), ("stop", "done")]


def test_host_voice_pace_and_say(tmp_path, monkeypatch):
    host = _host(tmp_path)
    host.reset("demo")
    sleeps = []
    monkeypatch.setattr(host, "settings", lambda: ("local", "voice"))
    monkeypatch.setattr(host, "sleep", lambda seconds: sleeps.append(seconds))
    said = []

    host.say("它这回合打十六，我先挡满再还手。", sender=said.append)
    host.wait_voice()
    assert said and "挡满" in said[0]
    assert sleeps and sleeps[0] >= 2.0

    monkeypatch.setattr(host, "settings", lambda: ("local", "normal"))
    host.wait_voice()
    assert sleeps[-1] == 0.7
