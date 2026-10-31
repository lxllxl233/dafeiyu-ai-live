"""无人值守连播的磁盘保护：录像盘快满了就不录，但游戏照打。"""
from __future__ import annotations

from dafeiyu.recording import auto


class _Usage:
    total = 4 * 1024 ** 3
    used = 4 * 1024 ** 3
    free = 100 * 1024 ** 2          # 只剩 100MB


def test_recording_skips_when_disk_is_low(monkeypatch):
    monkeypatch.setattr(auto, "_enabled", lambda: True)
    monkeypatch.setattr(auto.policy, "master_enabled", lambda: True)
    monkeypatch.setattr(auto.shutil, "disk_usage", lambda _p: _Usage())
    out = auto.start_session("slay-the-spire-2", note="自动玩")
    assert out["ok"] is False
    assert "磁盘" in out["error"]


def test_recording_does_not_start_obs_when_skipped(monkeypatch):
    called = []
    monkeypatch.setattr(auto, "_enabled", lambda: True)
    monkeypatch.setattr(auto.policy, "master_enabled", lambda: True)
    monkeypatch.setattr(auto.shutil, "disk_usage", lambda _p: _Usage())
    monkeypatch.setattr(auto, "ensure_obs", lambda game="": called.append(game) or (True, ""))
    auto.start_session("slay-the-spire-2", note="自动玩")
    assert called == []


def test_force_stop_finishes_recording_left_by_killed_process(monkeypatch, tmp_path):
    run_dir = tmp_path / "run-0001"
    run_dir.mkdir()
    (run_dir / "a.mp4").write_bytes(b"x" * 10)
    auto.replay.write_meta(run_dir, status="recording", source="autoplay")
    monkeypatch.setattr(auto, "status", lambda: {"active": False})
    monkeypatch.setattr(auto.replay, "get_current", lambda: run_dir)
    monkeypatch.setattr(auto.replay, "is_recording", lambda: True)
    monkeypatch.setattr(auto.replay, "stop_recording",
                        lambda d, result="": auto.replay.finalize(d, result=result))
    monkeypatch.setattr(auto.replay, "write_index", lambda *a, **k: None)
    monkeypatch.setattr(auto, "write_timeline", lambda *a, **k: {"events": 0})
    out = auto.force_stop("stopped")
    assert out["ok"] is True and out["recovered"] is True
    assert auto.replay.read_meta(run_dir)["status"] == "done"


def test_force_stop_leaves_foreign_recording_alone(monkeypatch, tmp_path):
    run_dir = tmp_path / "run-0002"
    run_dir.mkdir()
    auto.replay.write_meta(run_dir, status="done", source="autoplay")
    stopped = []
    monkeypatch.setattr(auto, "status", lambda: {"active": False})
    monkeypatch.setattr(auto.replay, "get_current", lambda: run_dir)
    monkeypatch.setattr(auto.replay, "is_recording", lambda: True)
    monkeypatch.setattr(auto.replay, "stop_recording",
                        lambda *a, **k: stopped.append(1))
    out = auto.force_stop("stopped")
    assert out["ok"] is True and out.get("recovered") is False
    assert stopped == []
