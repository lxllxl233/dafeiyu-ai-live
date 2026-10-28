from __future__ import annotations

from dafeiyu.agent.host import AutoplayHost
from dafeiyu.games.registry import GameRegistry


def test_sts2_plugin_entry_exposes_manifest_tools_prompt_runner():
    registry = GameRegistry()
    plugin = registry.load_plugin("slay-the-spire-2")
    assert plugin is not None
    assert plugin.manifest.id == "slay-the-spire-2"
    assert plugin.manifest.entry == "plugin.py:Sts2Plugin"
    assert plugin.manifest.capabilities.autoplay is True

    prompt = plugin.prompt("decision")
    assert prompt.startswith("你是《杀戮尖塔 2》")
    assert plugin.prompt("unknown") == ""

    tools = plugin.tools()
    assert tools is not None
    assert tools.__class__.__name__ == "Sts2Tools"

    runner = plugin.runner()
    assert runner is not None
    status = runner.status()
    assert isinstance(status, dict) and "running" in status

    module = registry.load_game_module("slay-the-spire-2", "autoplay.runner")
    assert module is not None
    assert module is plugin.runner_module


def test_plugin_runner_accepts_injected_host(tmp_path):
    plugin = GameRegistry().load_plugin("slay-the-spire-2")
    assert plugin is not None
    host = AutoplayHost("slay-the-spire-2")
    host.status_path = tmp_path / "autoplay.json"
    host.stop_path = tmp_path / "autoplay.stop"
    host.log_path = tmp_path / "autoplay.jsonl"
    host.reset("slay-the-spire-2")
    runner = plugin.runner(host=host)
    assert runner is not None
    status = runner.status()
    assert status["game"] == "slay-the-spire-2"
    assert plugin.runner_module._host is host


def test_demo_is_observation_only_not_a_plugin_entry():
    registry = GameRegistry()
    assert registry.load_plugin("demo") is None
    assert "adapter" in registry.last_plugin_error
