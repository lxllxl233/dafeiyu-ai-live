"""游戏启动：不真的拉起 Steam，只验证选路和错误分支。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from dafeiyu.games.launch import launch_game


class _Registry:
    def __init__(self, pack):
        self.pack = pack

    def get(self, game_id):
        return self.pack if game_id == self.pack.info.id else None

    def create_adapter(self, game_id):
        return None


def _pack(manifest: dict, root: Path | None = None):
    return SimpleNamespace(
        info=SimpleNamespace(id="test-game", name="测试游戏"),
        manifest=manifest,
        root=root or Path.cwd(),
    )


def test_missing_launch_config_is_reported():
    result = launch_game("demo")
    assert result["ok"] is False
    assert "未配置启动方式" in result["error"]


def test_steam_launch_builds_uri(monkeypatch):
    opened = []
    monkeypatch.setattr("os.startfile", lambda uri: opened.append(uri), raising=False)
    result = launch_game(
        "test-game",
        _Registry(_pack({"launch": {"type": "steam", "app_id": "2868840"}})),
        force=True,
    )
    assert result["ok"] is True
    assert result["uri"] == "steam://rungameid/2868840"
    assert opened == ["steam://rungameid/2868840"]


def test_unknown_game_is_reported():
    result = launch_game("nope", _Registry(_pack({})))
    assert result["ok"] is False
    assert "没有注册游戏" in result["error"]


def test_unknown_launch_type_is_reported(monkeypatch):
    result = launch_game(
        "test-game",
        _Registry(_pack({"launch": {"type": "magic"}})),
        force=True,
    )
    assert result["ok"] is False
    assert "不支持的启动类型" in result["error"]
