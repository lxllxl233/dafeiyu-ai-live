"""插件一致性：工具返回值必须是 ActionResult，同时保留旧 dict 兼容。"""

from __future__ import annotations

import sys

import pytest

from dafeiyu import paths
from dafeiyu.agent.tools import DemoPlanner, DemoTools
from dafeiyu.games.contracts import (
    ActionCode,
    ActionResult,
    AutoplayRunner,
    GamePlugin,
    GameTools,
    as_action_result,
)
from dafeiyu.games.registry import GameRegistry


def _sts2_tools(monkeypatch, calls: list, *, fail: str = ""):
    plugin = GameRegistry().load_plugin("slay-the-spire-2")
    assert plugin is not None
    tools = plugin.tools()
    module = sys.modules[type(tools).__module__]

    def fake_run(script, args, *, timeout=30.0):
        calls.append((script, list(args)))
        if fail:
            return {}, fail
        if script == "ctx.py":
            return {"screen": "COMBAT", "floor": 2, "hand": []}, ""
        if script == "act.py":
            if "--dry" in args:
                return {"ok": True, "summary": "验算通过"}, ""
            return {"ok": True, "receipt": "acted"}, ""
        return {}, f"unexpected script: {script}"

    monkeypatch.setattr(module, "_run", fake_run)
    return tools


def test_sts2_plugin_and_tools_match_protocols(monkeypatch):
    plugin = GameRegistry().load_plugin("slay-the-spire-2")
    assert plugin is not None
    assert isinstance(plugin, GamePlugin)
    assert plugin.manifest.capabilities.autoplay is True
    assert isinstance(plugin.tools(), GameTools)


def test_every_plugin_pack_matches_game_plugin_protocol():
    registry = GameRegistry()
    checked = 0
    ids: set[str] = set()
    for pack in registry.packs():
        contract = pack.contract
        if contract is None or contract.entry_fallback:
            continue
        plugin = registry.load_plugin(pack.info.id)
        assert plugin is not None, registry.last_plugin_error
        assert isinstance(plugin, GamePlugin), pack.info.id
        if contract.capabilities.control:
            assert isinstance(plugin.tools(), GameTools), pack.info.id
        if contract.capabilities.autoplay:
            runner = plugin.runner()
            assert runner is not None, pack.info.id
            assert isinstance(runner, AutoplayRunner), pack.info.id
        checked += 1
        ids.add(pack.info.id)
    assert checked >= 2
    assert {"slay-the-spire-2", "toy-duel"} <= ids


def test_sts2_tools_return_action_result_and_keep_legacy_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "LOGS", tmp_path)
    calls: list = []
    tools = _sts2_tools(monkeypatch, calls)

    observed = tools.observe()
    assert isinstance(observed, ActionResult) and observed.ok is True
    assert observed.action == "observe"
    assert observed.to_legacy()["context"]["screen"] == "COMBAT"

    validated = tools.validate({"seq": [{"end": 1}]})
    assert isinstance(validated, ActionResult) and validated.ok is True
    assert validated.action == "validate"
    assert calls[-1][0] == "act.py" and "--dry" in calls[-1][1]

    before = len(calls)
    dry = tools.execute({"seq": [{"end": 1}]}, dry_run=True)
    assert isinstance(dry, ActionResult) and dry.ok is True
    assert dry.data["dry_run"] is True
    assert len(calls) == before  # dry-run 不再调用 act.py

    real = tools.execute({"seq": [{"end": 1}]}, dry_run=False)
    assert isinstance(real, ActionResult) and real.ok is True
    assert real.data["receipt"] == "acted"
    assert calls[-1][0] == "act.py" and "--dry" not in calls[-1][1]


def test_sts2_tools_map_tool_failure_to_action_code(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "LOGS", tmp_path)
    tools = _sts2_tools(monkeypatch, [], fail="ctx.py 超时（20s）")

    result = tools.observe()
    assert isinstance(result, ActionResult) and result.ok is False
    assert result.code == ActionCode.UNAVAILABLE
    assert result.to_legacy()["err"] == "ctx.py 超时（20s）"


def test_action_result_legacy_roundtrip():
    failed = ActionResult.from_legacy(
        {"ok": False, "err": "费用不够", "ctx": {"e": 1}}, action="play")
    legacy = failed.to_legacy()
    assert legacy["ok"] is False
    assert legacy["err"] == "费用不够"
    assert legacy["error"] == "费用不够"
    assert legacy["ctx"] == {"e": 1}
    assert as_action_result(legacy, action="play").code == ActionCode.INVALID_PLAN

    unknown = as_action_result(None, action="observe")
    assert unknown.ok is False
    assert unknown.code == ActionCode.INTERNAL


def test_demo_tools_now_return_action_result(tmp_path):
    pytest.importorskip("langgraph")
    from dafeiyu.agent.graph import run_turn

    result = run_turn("demo", tools=DemoTools(), planner=DemoPlanner(),
                      trace_path=tmp_path / "trace.jsonl", mode="test")
    assert result["ok"] is True
    assert result["result"]["ok"] is True
    assert result["result"]["message"] == "演示流程完成（未调用游戏）"


def test_graph_accepts_legacy_tool_dict(tmp_path):
    pytest.importorskip("langgraph")
    from dafeiyu.agent.graph import run_turn

    class Mixed(DemoTools):
        def validate(self, plan):
            return {"ok": True, "summary": "旧工具返回 dict"}

    result = run_turn("mixed", tools=Mixed(), planner=DemoPlanner(),
                      trace_path=tmp_path / "trace.jsonl", mode="test")
    assert result["ok"] is True
    assert any(step["node"] == "validate" and step["status"] == "ok"
               for step in result["steps"])
