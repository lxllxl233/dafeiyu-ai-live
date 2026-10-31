# -*- coding: utf-8 -*-
"""开机自启动：命令拼装、注册表状态、设置页副作用。"""
from __future__ import annotations

import json

import pytest

from dafeiyu import paths
from dafeiyu.core import autostart
from dafeiyu.ui import settings


@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"persona": "dafeiyu", "port": 8730,
                                "game": "slay-the-spire-2"}), encoding="utf-8")
    monkeypatch.setattr(paths, "CONFIG_FILE", path)
    return path


def test_command_quotes_pythonw_and_main():
    cmd = autostart.command()
    assert "main.py" in cmd and '"' in cmd
    assert autostart.pythonw().lower().endswith(("pythonw.exe", "python.exe"))


def test_status_shape(monkeypatch):
    monkeypatch.setattr(autostart, "current", lambda: "")
    out = autostart.status()
    assert {"available", "enabled", "command", "expected"} <= set(out)


def test_enabled_requires_current_path(monkeypatch):
    monkeypatch.setattr(autostart, "current", lambda: '"C:\\old\\pythonw.exe" "C:\\old\\main.py"')
    assert autostart.enabled() is False
    monkeypatch.setattr(autostart, "current", autostart.command)
    assert autostart.enabled() is True


def test_settings_save_toggles_registry(cfg_file, monkeypatch):
    calls = []

    def fake(on):
        calls.append(bool(on))
        return {"ok": True, "enabled": bool(on), "command": "x"}

    monkeypatch.setattr(autostart, "set_enabled", fake)
    result = settings.save({"pet_autostart": True})
    assert calls == [True]
    assert result["config"]["pet_autostart"] is True


def test_settings_save_rejects_registry_failure(cfg_file, monkeypatch):
    monkeypatch.setattr(autostart, "set_enabled",
                        lambda on: {"ok": False, "error": "no registry"})
    with pytest.raises(ValueError, match="no registry"):
        settings.save({"pet_autostart": True})
