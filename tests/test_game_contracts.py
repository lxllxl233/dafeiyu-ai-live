from __future__ import annotations

import json

from dafeiyu.core.events import Topics
from dafeiyu.games import GameRegistry
from dafeiyu.games.contracts import (
    ActionCode,
    ActionResult,
    GameCapabilities,
    GameEvent,
    GameManifest,
    GameStateEnvelope,
)


def test_legacy_manifest_gets_safe_defaults():
    manifest = GameManifest.from_dict({
        "id": "demo",
        "name": "演示信号源",
        "adapter": "dafeiyu.games.demo:DemoAdapter",
    })
    assert manifest.schema == 1
    assert manifest.entry == manifest.adapter
    assert manifest.entry_fallback is True
    assert manifest.capabilities.observe is True
    assert manifest.capabilities.autoplay is False
    assert manifest.errors == ()
    assert {issue.field for issue in manifest.warnings} >= {"schema", "version", "entry"}


def test_v2_manifest_declares_capabilities():
    manifest = GameManifest.from_dict({
        "schema": 2,
        "id": "example-game",
        "version": "1.2.3",
        "name": "示例",
        "entry": "plugin.py:Plugin",
        "capabilities": {
            "observe": True,
            "autoplay": True,
            "modes": ["local", "llm"],
            "state_schema": "example.state/v1",
        },
    })
    assert manifest.errors == ()
    assert manifest.capabilities.supports("observe") is True
    assert manifest.capabilities.supports("autoplay") is True
    assert manifest.capabilities.supports("replay") is False
    assert manifest.capabilities.supports("unknown") is False
    assert manifest.capabilities.modes == ("local", "llm")


def test_manifest_rejects_invalid_id_and_missing_entry():
    bad_id = GameManifest.from_dict({
        "schema": 2, "id": "Bad ID", "version": "1", "name": "坏 ID",
        "entry": "plugin.py:Plugin", "capabilities": {"observe": True},
    })
    assert any(issue.field == "id" for issue in bad_id.errors)

    no_entry = GameManifest.from_dict({
        "schema": 2, "id": "no-entry", "version": "1", "name": "无入口",
        "capabilities": {"autoplay": True},
    })
    assert any(issue.field == "entry" for issue in no_entry.errors)


def test_state_envelope_digest_includes_extension_data():
    first = GameStateEnvelope(
        game_id="demo", revision="run-1",
        summary={"screen": "COMBAT", "hp": 40},
        data={"map_sig": "A", "floor_nodes": [1, 2]},
    )
    same = GameStateEnvelope(
        game_id="demo", revision="run-1",
        summary={"screen": "COMBAT", "hp": 40},
        data={"map_sig": "A", "floor_nodes": [1, 2]},
    )
    changed = GameStateEnvelope(
        game_id="demo", revision="run-1",
        summary={"screen": "COMBAT", "hp": 40},
        data={"map_sig": "B", "floor_nodes": [1, 2]},
    )
    assert first.digest() == same.digest()
    assert first.digest() != changed.digest()


def test_state_envelope_from_legacy_keeps_summary_and_extra():
    state = GameStateEnvelope.from_legacy({
        "screen": "MAP", "floor": 3, "hp": 51, "max_hp": 80,
        "extra": {"run_id": "R1", "act_id": 0},
    }, game_id="demo", revision="R1")
    assert state.summary["screen"] == "MAP"
    assert state.summary["floor"] == 3
    assert state.data == {"run_id": "R1", "act_id": 0}


def test_game_event_maps_to_bus_topics():
    phase = GameEvent(
        game_id="demo", kind="phase", seq=7, at=123.0,
        summary="进入第二幕", facts={"act": 2},
    )
    event = phase.to_bus()
    assert event.topic == Topics.GAME_EVENT
    assert event.source == "demo"
    assert event.ts == 123.0
    assert event.data["kind"] == "phase"
    assert event.data["seq"] == 7
    assert event.data["act"] == 2

    state = GameEvent(game_id="demo", kind="state", seq=8, at=124.0,
                      facts={"screen": "COMBAT"}).to_bus()
    assert state.topic == Topics.GAME_STATE


def test_action_result_maps_legacy_dict():
    failed = ActionResult.from_legacy(
        {"ok": False, "err": "费用不够", "ctx": {"e": 1}}, action="play")
    assert failed.ok is False
    assert failed.code == ActionCode.INVALID_PLAN
    assert failed.message == "费用不够"
    assert failed.retryable is True
    assert failed.data["ctx"] == {"e": 1}

    passed = ActionResult.from_legacy({"ok": True, "receipt": "ok"}, action="end")
    assert passed.ok is True
    assert passed.code == ActionCode.OK
    assert passed.data["receipt"] == "ok"

    assert GameCapabilities(observe=True).supports("observe") is True


def test_registry_exposes_contract_and_issues():
    registry = GameRegistry()
    pack = registry.get("slay-the-spire-2")
    assert pack is not None
    assert pack.contract is not None
    assert pack.contract.version == "2.5.0"
    assert pack.contract.capabilities.autoplay is True
    assert pack.contract.errors == ()
    assert pack.contract.entry == "plugin.py:Sts2Plugin"
    assert not any(issue.field == "entry" for issue in pack.manifest_issues)
    assert pack.to_dict()["contract"]["id"] == "slay-the-spire-2"

    demo = registry.get("demo")
    assert demo is not None and demo.contract is not None
    assert demo.contract.schema == 1
    assert demo.contract.capabilities.observe is True


def test_registry_records_bad_manifest_without_crashing(tmp_path):
    (tmp_path / "registry.json").write_text(json.dumps({
        "games": [{
            "id": "bad-pack", "name": "坏包", "pack": "bad-pack",
            "adapter": "adapter.py:Bad",
        }],
    }), encoding="utf-8")
    pack_dir = tmp_path / "bad-pack"
    pack_dir.mkdir()
    (pack_dir / "game.json").write_text(json.dumps({
        "schema": 2, "id": "Bad ID", "version": "1", "name": "坏包",
        "entry": "plugin.py:Bad", "capabilities": {"autoplay": True},
    }), encoding="utf-8")

    registry = GameRegistry(tmp_path)
    pack = registry.get("bad-pack")
    assert pack is not None and pack.contract is not None
    assert any(issue.field == "id" for issue in pack.contract.errors)
    assert pack.to_dict()["manifest_issues"]
