"""第二个游戏（玩具决斗）的接入验收：不碰 STS2，也不启动外部程序。"""

from __future__ import annotations

import time

import pytest

from dafeiyu.agent import play
from dafeiyu.agent.host import AutoplayHost
from dafeiyu.games import catalog
from dafeiyu.games.base import GameAdapter
from dafeiyu.games.contracts import ActionCode, ActionResult, AutoplayRunner, GamePlugin, GameTools
from dafeiyu.games.registry import GameRegistry


@pytest.fixture
def toy_state(tmp_path, monkeypatch):
    path = tmp_path / "toy_state.json"
    monkeypatch.setenv("TOY_DUEL_STATE", str(path))
    return path


def _host(tmp_path, game: str = "toy-duel") -> AutoplayHost:
    host = AutoplayHost(game)
    host.status_path = tmp_path / "autoplay.json"
    host.stop_path = tmp_path / "autoplay.stop"
    host.log_path = tmp_path / "autoplay.jsonl"
    host.reset(game)
    return host


def test_toy_manifest_and_plugin_match_contracts(toy_state):
    registry = GameRegistry()
    assert catalog.resolve_game_id("toy") == "toy-duel"
    assert catalog.game_name("toy") == "玩具决斗"
    pack = registry.get("toy-duel")
    assert pack is not None and pack.contract is not None
    assert pack.contract.errors == ()
    assert pack.contract.capabilities.observe is True
    assert pack.contract.capabilities.control is True
    assert pack.contract.capabilities.autoplay is True
    assert pack.contract.capabilities.modes == ("local",)

    plugin = registry.load_plugin("toy-duel")
    assert plugin is not None
    assert isinstance(plugin, GamePlugin)
    assert plugin.manifest.id == "toy-duel"
    assert plugin.manifest.entry == "plugin.py:ToyPlugin"
    assert plugin.prompt("decision")
    assert plugin.prompt("unknown") == ""

    tools = plugin.tools()
    assert isinstance(tools, GameTools)
    adapter = plugin.adapter()
    assert isinstance(adapter, GameAdapter)
    assert adapter.available() is True
    runner = plugin.runner()
    assert isinstance(runner, AutoplayRunner)


def test_toy_tools_observe_validate_execute_roundtrip(toy_state):
    tools = GameRegistry().load_plugin("toy-duel").tools()

    observed = tools.observe()
    assert isinstance(observed, ActionResult) and observed.ok is True
    context = observed.data["context"]
    assert context["screen"] == "COMBAT"
    assert context["hero"]["hp"] == 30
    assert context["enemy"]["hp"] == 18

    plan = {"seq": [{"play": "STRIKE"}, {"play": "STRIKE"}, {"end": True}]}
    checked = tools.validate(plan)
    assert checked.ok is True
    assert checked.data["energy_after"] == 1

    dry = tools.execute(plan, dry_run=True)
    assert dry.ok is True and dry.data["dry_run"] is True
    # dry-run 不写状态：敌人仍是满血。
    assert tools.observe().data["context"]["enemy"]["hp"] == 18

    real = tools.execute(plan, dry_run=False)
    assert real.ok is True
    assert real.data["dry_run"] is False
    assert real.data["receipt"].startswith("toy-")
    after = real.data["context"]
    assert after["enemy"]["hp"] == 6
    assert after["hero"]["hp"] == 24
    assert after["turn"] == 2

    lethal = tools.execute(
        {"seq": [{"play": "STRIKE"}, {"play": "STRIKE"}, {"end": True}]},
        dry_run=False,
    )
    assert lethal.ok is True
    assert lethal.data["context"]["screen"] == "REWARD"


def test_toy_tools_reject_illegal_plan_without_writing_state(toy_state):
    tools = GameRegistry().load_plugin("toy-duel").tools()
    tools.observe()
    before = toy_state.read_text(encoding="utf-8")

    result = tools.execute(
        {"seq": [{"play": "STRIKE"}] * 4}, dry_run=False)
    assert result.ok is False
    assert result.code == ActionCode.INVALID_PLAN
    assert toy_state.read_text(encoding="utf-8") == before


def test_toy_adapter_reads_the_same_state(toy_state):
    plugin = GameRegistry().load_plugin("toy-duel")
    plugin.tools().observe()

    adapter = GameRegistry().create_adapter("toy-duel")
    assert adapter is not None
    assert adapter.available() is True
    state = adapter.snapshot()
    assert state.screen == "COMBAT"
    assert state.hp == 30
    assert state.max_hp == 30
    assert state.turn == 1
    assert state.in_combat is True
    assert adapter.status()["game_id"] == "toy-duel"


def test_toy_runner_start_status_stop(toy_state, tmp_path):
    plugin = GameRegistry().load_plugin("toy-duel")
    host = _host(tmp_path)
    runner = plugin.runner(host=host)

    rejected = runner.start(mode="llm")
    assert rejected["ok"] is False
    assert "local" in rejected["message"]

    started = runner.start(mode="local", pace="normal")
    assert started["ok"] is True
    assert started["game"] == "toy-duel"

    deadline = time.time() + 2.0
    latest = runner.status()
    while time.time() < deadline and not latest.get("step"):
        time.sleep(0.05)
        latest = runner.status()
    assert latest["game"] == "toy-duel"
    assert latest["step"] >= 1
    assert latest["screen"] in ("COMBAT", "REWARD")

    stopped = runner.stop()
    assert stopped["ok"] is True
    assert runner.status()["running"] is False
    assert (tmp_path / "autoplay.jsonl").exists()


def test_facade_dispatches_to_second_game(toy_state, tmp_path, monkeypatch):
    host = _host(tmp_path)
    monkeypatch.setattr(play, "AutoplayHost", lambda game_id: host)
    monkeypatch.setattr(play, "_read_status", lambda: {"game": "toy-duel"})

    started = play.start_autoplay("toy-duel", mode="local", pace="normal")
    assert started["ok"] is True
    assert started["status"]["game"] == "toy-duel"

    stopped = play.stop_autoplay()
    assert stopped["ok"] is True
    assert play.status()["running"] is False
