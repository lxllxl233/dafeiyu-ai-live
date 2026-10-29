"""控制台「游戏 → 详情」：策略文档编辑、资料笔记白名单、原始资料只读预览。"""
from __future__ import annotations

import pytest

from dafeiyu.games.registry import GameRegistry
from dafeiyu.ui import studio


def _skills():
    return GameRegistry().load_game_module("slay-the-spire-2", "skills")


def _runtime():
    rt = studio.StudioRuntime.__new__(studio.StudioRuntime)
    rt.cfg = type("C", (), {"persona": "dafeiyu", "game": "slay-the-spire-2"})()
    rt.persona = studio.Persona.load("dafeiyu")
    rt.registry = GameRegistry()
    rt._sync_config = lambda: None
    return rt


def test_game_files_are_grouped_and_whitelisted():
    mod = _skills()
    rows = mod.files()
    groups = {r["group"] for r in rows}
    assert {"strategy", "notes", "caster"} <= groups
    assert any(r["name"].endswith("OS.md") for r in rows if r["group"] == "strategy")
    assert any(r["group"] == "notes" for r in rows)
    assert any(r["group"] == "caster" for r in rows)
    with pytest.raises(ValueError):
        mod.path_of("../config.json")
    with pytest.raises(ValueError):
        mod.path_of("decision/prompts.py")


def test_material_index_and_preview_are_read_only():
    mod = _skills()
    mats = mod.materials()
    assert mats["counts"].get("cards", 0) > 0
    assert any(f["name"].endswith("cards.json") for f in mats["files"])
    out = mod.material_text("corpus/data/eng/cards.json")
    assert out["ok"] and out["text"]
    with pytest.raises(ValueError):
        mod.material_text("decision/knowledge/ironclad/OS.md")


def test_skill_limits_reject_oversize():
    mod = _skills()
    assert mod.validate("decision/knowledge/ironclad/OS.md", "短") is None
    err = mod.validate("decision/knowledge/ironclad/OS.md", "长" * 4601)
    assert err and "token" in err


def test_game_payload_and_save(tmp_path, monkeypatch):
    mod = _skills()
    target = tmp_path / "decision/knowledge/ironclad"
    target.mkdir(parents=True)
    (target / "OS.md").write_text("旧内容\n", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    rt = _runtime()
    payload = rt.skill_payload("slay-the-spire-2")
    assert payload["ok"] and payload["game"] == "slay-the-spire-2"
    assert payload["info"]["id"] == "slay-the-spire-2"
    name = "decision/knowledge/ironclad/OS.md"
    assert payload["texts"][name] == "旧内容\n"

    out = rt.save_skill(name, "第一行\n第二行\n", "slay-the-spire-2")
    assert out["ok"] and out["game"] == "slay-the-spire-2"
    assert (target / "OS.md").read_bytes() == "第一行\n第二行\n".encode("utf-8")
    assert rt.skill_payload("slay-the-spire-2")["texts"][name] == "第一行\n第二行\n"


def test_game_without_skills_module_returns_error():
    rt = _runtime()
    payload = rt.skill_payload("toy-duel")
    assert payload["ok"] is False
    assert payload["files"] == []
