"""LangGraph 流程与轨迹：不碰游戏、不调 LLM。"""
from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

from dafeiyu.agent.graph import graph_mermaid, run_turn
from dafeiyu.agent.planner import _extract_plan
from dafeiyu.agent.tools import DemoPlanner, DemoTools
from dafeiyu.agent.trace import read_runs


def test_demo_turn_records_all_nodes(tmp_path):
    trace = tmp_path / "trace.jsonl"
    result = run_turn("demo", tools=DemoTools(), planner=DemoPlanner(),
                      trace_path=trace, mode="test")
    assert result["ok"] is True
    assert result["status"] == "ok"
    assert [s["node"] for s in result["steps"]] == [
        "observe", "plan", "validate", "execute", "reflect"]
    assert result["plan"]["seq"]
    runs = read_runs(trace, limit=5)
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"
    assert runs[0]["tokens"]["total"] == 0


def test_validation_failure_retries_plan(tmp_path):
    class RetryTools(DemoTools):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def validate(self, plan):
            self.calls += 1
            if self.calls == 1:
                return {"ok": False, "err": "第一次不通过"}
            return super().validate(plan)

    tools = RetryTools()
    result = run_turn("retry", tools=tools, planner=DemoPlanner(),
                      trace_path=tmp_path / "trace.jsonl", max_attempts=2)
    assert result["ok"] is True
    assert tools.calls == 2
    plans = [s for s in result["steps"] if s["node"] == "plan"]
    assert len(plans) == 2
    assert result["plan"]["seq"][-1] == {"end": 1}


def test_validation_failure_marks_run_error(tmp_path):
    class BadTools(DemoTools):
        def validate(self, plan):
            return {"ok": False, "err": "始终不通过"}

    result = run_turn("bad", tools=BadTools(), planner=DemoPlanner(),
                      trace_path=tmp_path / "trace.jsonl", max_attempts=1)
    assert result["ok"] is False
    assert result["status"] == "error"
    assert "始终不通过" in result["steps"][-1]["summary"]


def test_tokens_are_not_double_counted(tmp_path):
    class UsagePlanner(DemoPlanner):
        def plan(self, context, feedback=""):
            out = super().plan(context, feedback)
            out["usage"] = {"input": 7, "output": 3, "total": 10}
            return out

    result = run_turn("tokens", tools=DemoTools(), planner=UsagePlanner(),
                      trace_path=tmp_path / "trace.jsonl")
    assert result["tokens"] == {"input": 7, "output": 3, "total": 10}
    assert read_runs(tmp_path / "trace.jsonl", limit=1)[0]["tokens"]["total"] == 10


def test_external_plan_is_used_without_llm(tmp_path):
    plan = {"say": ["外部计划"], "seq": [{"end": 1}]}
    result = run_turn("external", tools=DemoTools(), planner=None, plan=plan,
                      trace_path=tmp_path / "trace.jsonl")
    assert result["ok"] is True
    assert result["plan"] == plan


def test_planner_json_extraction_handles_fence():
    assert _extract_plan('```json\n{"seq":[{"end":1}]}\n```') == {"seq": [{"end": 1}]}
    assert _extract_plan("前缀 {\"say\":[\"好\"]} 后缀") == {"say": ["好"]}
    assert _extract_plan("not json") == {}


def test_graph_topology_contains_nodes():
    text = graph_mermaid()
    for node in ("observe", "plan", "validate", "execute", "reflect"):
        assert node in text
