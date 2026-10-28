"""无人值守连播的磁盘保护：录像盘快满了就不录，但游戏照打。"""
from __future__ import annotations

from dafeiyu.recording import auto


class _Usage:
    total = 4 * 1024 ** 3
    used = 4 * 1024 ** 3
    free = 100 * 1024 ** 2          # 只剩 100MB


def test_recording_skips_when_disk_is_low(monkeypatch):
    monkeypatch.setattr(auto, "_enabled", lambda: True)
    monkeypatch.setattr(auto.shutil, "disk_usage", lambda _p: _Usage())
    out = auto.start_session("slay-the-spire-2", note="自动玩")
    assert out["ok"] is False
    assert "磁盘" in out["error"]


def test_recording_does_not_start_obs_when_skipped(monkeypatch):
    called = []
    monkeypatch.setattr(auto, "_enabled", lambda: True)
    monkeypatch.setattr(auto.shutil, "disk_usage", lambda _p: _Usage())
    monkeypatch.setattr(auto, "ensure_obs", lambda game="": called.append(game) or (True, ""))
    auto.start_session("slay-the-spire-2", note="自动玩")
    assert called == []
