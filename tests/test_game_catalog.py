from __future__ import annotations

from dafeiyu.core.config import LiveConfig
from dafeiyu.games import catalog


def test_catalog_resolves_default_and_aliases():
    assert catalog.default_game_id() == "slay-the-spire-2"
    assert catalog.resolve_game_id("sts2") == "slay-the-spire-2"
    assert catalog.resolve_game_id("SPIRE2") == "slay-the-spire-2"
    assert catalog.resolve_game_id("demo") == "demo"
    assert catalog.game_name("sts2") == "杀戮尖塔 2"


def test_legacy_env_prefixes_come_from_manifest_aliases():
    assert "STS2" in catalog.legacy_env_prefixes()
    assert catalog.legacy_env_prefixes("sts2") == ("STS2", "SPIRE2")


def test_env_value_prefers_generic_then_game_alias(monkeypatch):
    monkeypatch.setenv("STS2_TTS_URL", "http://legacy:9880")
    monkeypatch.delenv("DAFEIYU_TTS_URL", raising=False)
    assert catalog.env_value("TTS_URL", game="sts2") == "http://legacy:9880"

    monkeypatch.setenv("DAFEIYU_TTS_URL", "http://generic:9880")
    assert catalog.env_value("TTS_URL", game="sts2") == "http://generic:9880"
    assert catalog.env_value("TTS_URL", "fallback") == "http://generic:9880"


def test_empty_config_game_falls_back_to_catalog(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"game": ""}', encoding="utf-8")
    monkeypatch.delenv("DAFEIYU_GAME", raising=False)
    assert LiveConfig.load(path).game == catalog.default_game_id()


def test_config_game_alias_is_normalized(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"game": "sts2"}', encoding="utf-8")
    monkeypatch.delenv("DAFEIYU_GAME", raising=False)
    assert LiveConfig.load(path).game == "slay-the-spire-2"
