"""控制台「人设/梗」编辑：文件白名单、JSON 校验、梗列表同步 SQLite。"""
from __future__ import annotations

import json
import sqlite3

import pytest

from dafeiyu.ui import studio


def test_persona_files_are_whitelisted():
    files = {f["name"] for f in studio.persona_files("dafeiyu", "slay-the-spire-2")}
    assert {"card.json", "memes/general.json", "db/memes.json"} <= files
    assert any(name.startswith("caster/") for name in files)
    with pytest.raises(ValueError):
        studio.persona_file_path("../config.json", "dafeiyu", "slay-the-spire-2")
    with pytest.raises(ValueError):
        studio.persona_file_path("db/mech.json", "dafeiyu", "slay-the-spire-2")


def _runtime():
    rt = studio.StudioRuntime.__new__(studio.StudioRuntime)
    rt.cfg = type("C", (), {"persona": "dafeiyu", "game": "slay-the-spire-2"})()
    rt.persona = studio.Persona.load("dafeiyu")
    rt._sync_config = lambda: None
    return rt


def test_persona_payload_returns_texts():
    rt = _runtime()
    payload = rt.persona_payload()
    assert payload["ok"] and payload["persona"] == "dafeiyu"
    assert '"大肥鱼"' in payload["texts"]["card.json"]
    assert "items" in payload["texts"]["memes/general.json"]


def test_training_payload_marks_compressed(tmp_path, monkeypatch):
    from dafeiyu.persona import training as pt

    monkeypatch.setattr(pt, "DEFAULT_PATH", tmp_path / "training.json")
    monkeypatch.setattr(pt, "DEFAULT_DIGEST_PATH", tmp_path / "training_digest.json")
    store = pt.TrainingMemoryStore()
    old = store.add({"bvid": "BVold", "title": "旧记忆", "summary": "旧的摘要"})
    new = store.add({"bvid": "BVnew", "title": "新记忆", "summary": "新的摘要"})
    long_text = "这是压缩后的长期画像，保留主题、立场和审美。" * 8
    pt.TrainingDigestStore().save(
        long_text, source_count=1, covered_ids=[old["id"]], chunks=1)
    payload = _runtime()._training_payload()
    rows = {row["id"]: row for row in payload["entries"]}
    assert rows[old["id"]]["compressed"] is True
    assert rows[new["id"]]["compressed"] is False
    compression = payload["compression"]
    assert compression["exists"] is True
    assert compression["source_count"] == 1
    assert compression["text"] == long_text
    assert compression["chars"] == len(long_text)
    # 保存原始条目后，返回给控制台的 payload 仍要带压缩状态。
    saved = _runtime().save_training(payload["entries"])
    saved_rows = {row["id"]: row for row in saved["training"]["entries"]}
    assert saved_rows[old["id"]]["compressed"] is True
    assert saved_rows[new["id"]]["compressed"] is False


def test_validate_card_rejects_broken_shapes():
    assert studio.validate_card({"id": "dafeiyu", "name": "大肥鱼"})["name"] == "大肥鱼"
    with pytest.raises(ValueError):
        studio.validate_card({"id": "dafeiyu"})
    with pytest.raises(ValueError):
        studio.validate_card({"id": "dafeiyu", "name": "鱼", "quips": "不是数组"})


def test_parse_memes_normalizes_tags():
    items = studio.parse_memes(json.dumps(
        [{"id": "a", "text": "梗", "tags": "x,y", "intensity": 2}], ensure_ascii=False))
    assert items[0]["tags"] == ["x", "y"]
    assert items[0]["intensity"] == 2 and items[0]["cooldown"] == 10
    with pytest.raises(ValueError):
        studio.parse_memes(json.dumps([{"id": "a"}]))
    with pytest.raises(ValueError):
        studio.parse_memes("不是 JSON")


def test_sync_memes_db_writes_rows(tmp_path):
    db = tmp_path / "sts2.db"
    n = studio.sync_memes_db(
        [{"id": "a", "text": "梗A", "category": "测试", "game": "both",
          "tags": ["x", "y"], "trigger": "t", "example": "e",
          "intensity": 2, "cooldown": 5}], db)
    assert n == 1
    con = sqlite3.connect(db)
    try:
        row = con.execute("select id,text,tags,intensity,cooldown from memes").fetchone()
    finally:
        con.close()
    assert row == ("a", "梗A", "x,y", 2, 5)


def test_save_rejects_invalid_json_without_writing():
    rt = _runtime()
    with pytest.raises(ValueError):
        rt.save_persona("card.json", "{ 不是 JSON")
    with pytest.raises(ValueError):
        rt.save_persona("db/memes.json", "{}")
    with pytest.raises(ValueError):
        rt.save_persona("nope.json", "x")

def test_game_declares_memes_db():
    from dafeiyu.games.registry import GameRegistry

    mod = GameRegistry().load_game_module("slay-the-spire-2", "memes")
    assert mod is not None
    assert mod.DB.exists() and mod.JSON.exists()

def test_atomic_write_keeps_lf(tmp_path):
    p = tmp_path / "card.json"
    studio._atomic_write(p, '{\n  "id": "dafeiyu"\n}\n')
    assert b"\r\n" not in p.read_bytes()
    assert p.read_text(encoding="utf-8").endswith("}\n")
