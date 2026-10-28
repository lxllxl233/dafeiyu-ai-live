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
