"""OBS 录像策略：游戏默认值、本机覆盖、脚本创建和手动开关。"""
from __future__ import annotations

import json
import types

from dafeiyu.recording import auto, manual, policy


def test_game_defaults_are_one_run_per_episode_except_bilibili():
    assert policy.default_for("vampire-survivors") == {"enabled": True, "split": "run"}
    assert policy.default_for("slay-the-spire-2") == {"enabled": True, "split": "run"}
    assert policy.default_for("bilibili-hot-commentary") == {
        "enabled": False, "split": "none"}
    assert policy.default_for("not-a-game") == {"enabled": False, "split": "none"}


def test_config_override_and_master_switch():
    cfg = types.SimpleNamespace(
        recording_enabled=False,
        recording={"games": {"vampire-survivors": {"enabled": False, "split": "none"}}},
    )
    out = policy.policy_for("vampire-survivors", cfg)
    assert out["enabled"] is False and out["split"] == "none"
    assert out["default"] == {"enabled": True, "split": "run"}
    assert policy.master_enabled(cfg) is False


def test_ensure_script_creates_missing_game_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(policy.paths, "GAMES", tmp_path)
    game = tmp_path / "demo-game"
    game.mkdir()
    (game / "game.json").write_text(
        json.dumps({"id": "demo-game", "name": "演示游戏"}), encoding="utf-8")

    out = policy.ensure_script("demo-game")
    assert out["created"] is True
    assert out["exists"] is True
    script = game / "game-skill" / "obs.json"
    profile = json.loads(script.read_text(encoding="utf-8"))
    assert profile["scene"] == "演示游戏"
    assert profile["window_match"] == ["演示游戏", "demo-game"]

    again = policy.ensure_script("demo-game")
    assert again["created"] is False


def test_save_policy_writes_nested_override():
    class Cfg:
        recording_enabled = True
        recording = {}

        def save_patch(self, patch):
            self.recording = dict(patch.get("recording") or {})

    cfg = Cfg()
    out = policy.save_policy("vampire-survivors", enabled=False, split="none", cfg=cfg)
    assert out["enabled"] is False and out["split"] == "none"
    assert cfg.recording["games"]["vampire-survivors"] == {
        "enabled": False, "split": "none"}


def test_auto_session_respects_master_switch(monkeypatch):
    monkeypatch.setattr(auto.policy, "master_enabled", lambda: False)
    monkeypatch.setattr(auto, "_session", None)
    out = auto.start_session("vampire-survivors")
    assert out["ok"] is False
    assert out["skipped"] is True
    assert "总开关" in out["error"]


def test_auto_session_respects_per_game_default(monkeypatch):
    monkeypatch.setattr(auto.policy, "master_enabled", lambda: True)
    monkeypatch.setattr(auto, "_session", None)
    out = auto.start_session("bilibili-hot-commentary")
    assert out["ok"] is False
    assert out["skipped"] is True
    assert "默认不录像" in out["error"]


def test_auto_session_supports_continuous_mode(monkeypatch):
    monkeypatch.setattr(auto, "_session", None)
    monkeypatch.setattr(auto, "_recover_stale_recording", lambda: {"ok": True})
    monkeypatch.setattr(auto, "ensure_obs", lambda game="": (True, ""))
    monkeypatch.setattr(auto, "_prepare_scene", lambda game: (True, ""))
    monkeypatch.setattr(auto, "_start_continuous",
                        lambda game: {"ok": True, "continuous": True,
                                      "path": "recordings/x/continuous"})
    out = auto.start_session("bilibili-hot-commentary", force=True, split="none")
    assert out["ok"] is True and out["continuous"] is True
    assert out["policy"]["split"] == "none"


def test_manual_toggle_game_uses_current_game(monkeypatch):
    calls: list[tuple] = []

    def start(game="", *, force=True):
        calls.append(("start", game, force))
        return {"ok": True, "recording": True, "action": "start"}

    def stop(result="manual"):
        calls.append(("stop", result))
        return {"ok": True, "recording": False, "action": "stop"}

    monkeypatch.setattr(manual, "start_game", start)
    monkeypatch.setattr(manual, "stop_game", stop)
    monkeypatch.setattr(manual, "probe",
                        lambda timeout=2.0: {"ok": True, "recording": False})
    assert manual.toggle_game("vampire-survivors")["recording"] is True
    assert calls == [("start", "vampire-survivors", True)]

    monkeypatch.setattr(manual, "probe",
                        lambda timeout=2.0: {"ok": True, "recording": True})
    assert manual.toggle_game("vampire-survivors")["recording"] is False
    assert calls[-1][0] == "stop"
