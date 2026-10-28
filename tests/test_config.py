from __future__ import annotations

from dafeiyu import paths
from dafeiyu.core.config import LiveConfig


def test_config_masks_api_key_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CONFIG_FILE", tmp_path / "config.json")
    cfg = LiveConfig()
    cfg.save_patch({"llm_model": "deepseek-reasoner", "llm_api_key": "local-test-key"})
    loaded = LiveConfig.load(tmp_path / "config.json")
    assert loaded.llm_model == "deepseek-reasoner"
    assert loaded.llm_api_key == "local-test-key"
    public = loaded.to_dict()
    assert public["llm_api_key"] == ""
    assert public["llm_api_key_set"] is True
    assert "local-test-key" in (tmp_path / "config.json").read_text(encoding="utf-8")


def test_token_estimate_is_deterministic():
    from dafeiyu.ui.overlay_server import estimate_tokens

    assert estimate_tokens("它 21，我最多挡 15") == 7
    assert estimate_tokens("hello world 123") == 3
    assert estimate_tokens("") == 0


def test_bilibili_cookie_is_masked(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CONFIG_FILE", tmp_path / "config.json")
    cfg = LiveConfig()
    cfg.save_patch({"bilibili_enabled": True, "bilibili_room_id": 123,
                    "bilibili_cookie": "SESSDATA=secret"})
    public = cfg.to_dict()
    assert public["bilibili_room_id"] == 123
    assert public["bilibili_cookie"] == ""
    assert public["bilibili_cookie_set"] is True


def test_desktop_model_switch_syncs_autoplay(monkeypatch):
    class FakeConfig:
        def __init__(self):
            self.patch = {}

        def save_patch(self, patch):
            self.patch = dict(patch)

    fake = FakeConfig()
    monkeypatch.setattr(LiveConfig, "load", classmethod(lambda cls: fake))
    from dafeiyu.ui.desktop import StreamerWindow

    StreamerWindow.set_model(None, "deepseek-flash")
    assert fake.patch == {"llm_model": "deepseek-flash",
                          "autoplay_llm_model": "deepseek-flash"}