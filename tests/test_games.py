from __future__ import annotations

import json

from dafeiyu import paths
from dafeiyu.games import GameRegistry
from dafeiyu.games.demo import DemoAdapter
from dafeiyu.games.sts2 import Sts2Adapter
from dafeiyu.core.events import Topics


def test_registry_lists_games():
    reg = GameRegistry()
    ids = {p.info.id for p in reg.packs()}
    assert {"slay-the-spire-2", "demo"} <= ids


def test_demo_adapter_emits_state():
    adapter = DemoAdapter(delay=0)
    assert adapter.available() is True
    events = list(adapter.events())
    assert events and events[0].topic == Topics.GAME_STATE
    assert events[0].data["character"] == "铁甲战士"


def test_sts2_manifest_paths_exist():
    reg = GameRegistry()
    pack = reg.get("slay-the-spire-2")
    assert pack and pack.root
    compat = pack.manifest["compat"]
    corpus = pack.manifest["corpus"]
    assert (pack.root / compat["client"]).exists()
    assert (pack.root / compat["mod"]).exists()
    assert (pack.root / corpus["db"]).exists()
    assert (pack.root / corpus["data"]).exists()


def test_sts2_adapter_offline_is_quiet():
    adapter = Sts2Adapter(api="http://127.0.0.1:1", timeout=0.2)
    assert adapter.available() is False
    state = adapter.snapshot()
    assert state.screen == ""
    assert adapter.status()["available"] is False


def test_corpus_db_counts():
    import sqlite3

    db = paths.GAMES / "slay-the-spire-2" / "corpus" / "db" / "sts2.db"
    con = sqlite3.connect(db)
    try:
        assert con.execute("select count(*) from cards").fetchone()[0] > 600
        assert con.execute("select count(*) from enemies").fetchone()[0] > 100
    finally:
        con.close()


def test_game_json_is_valid():
    raw = json.loads((paths.GAMES / "slay-the-spire-2" / "game.json").read_text(encoding="utf-8"))
    assert raw["id"] == "slay-the-spire-2"
    assert raw["adapter"].startswith("dafeiyu.games.")
