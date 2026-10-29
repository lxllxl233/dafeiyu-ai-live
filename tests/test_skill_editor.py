"""控制台「对战手册」编辑：白名单、限额校验、LF 写入。"""
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


def test_skill_files_are_whitelisted():
    mod = _skills()
    names = {f["name"] for f in mod.files()}
    assert "decision/knowledge/ironclad/OS.md" in names
    assert any(n.startswith("decision/knowledge/ironclad/references/") for n in names)
    assert any(n.startswith("persona/") for n in names)
    with pytest.raises(ValueError):
        mod.path_of("../config.json")
    with pytest.raises(ValueError):
        mod.path_of("decision/prompts.py")


def test_skill_limits_reject_oversize():
    mod = _skills()
    assert mod.validate("decision/knowledge/ironclad/OS.md", "短") is None
    err = mod.validate("decision/knowledge/ironclad/OS.md", "长" * 4601)
    assert err and "token" in err


def test_save_skill_writes_lf_and_payload_reads_it(tmp_path, monkeypatch):
    mod = _skills()
    target = tmp_path / "decision/knowledge/ironclad"
    target.mkdir(parents=True)
    (target / "OS.md").write_text("旧内容\n", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    rt = _runtime()
    payload = rt.skill_payload()
    assert payload["ok"]
    name = "decision/knowledge/ironclad/OS.md"
    assert payload["texts"][name] == "旧内容\n"

    out = rt.save_skill(name, "第一行\n第二行\n")
    assert out["ok"] and out["bytes"] > 0
    assert (target / "OS.md").read_bytes() == "第一行\n第二行\n".encode("utf-8")
    assert rt.skill_payload()["texts"][name] == "第一行\n第二行\n"
