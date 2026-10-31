# -*- coding: utf-8 -*-
"""曼波引擎关闭：离线/找不到进程时不误杀。"""
from __future__ import annotations

from dafeiyu.voice import startup


def test_stop_when_engine_offline(monkeypatch):
    monkeypatch.setattr(startup, "engine_online", lambda *a, **k: False)
    out = startup.stop_mambo(url="http://127.0.0.1:1")
    assert out["ok"] is True and out["stopped"] is False


def test_stop_without_python_process(monkeypatch):
    monkeypatch.setattr(startup, "engine_online", lambda *a, **k: True)
    monkeypatch.setattr(startup, "_process_name", lambda pid: "chrome.exe")
    monkeypatch.setattr(startup, "_port_owner", lambda port: 0)
    monkeypatch.setattr(startup, "PID_FILE", startup.paths.LOGS / "no_such_mambo.pid")
    out = startup.stop_mambo(url="http://127.0.0.1:1")
    assert out["ok"] is False and "找不到" in out["error"]
