"""Agent 层：LangGraph 回合流程 + JSONL 轨迹 + 现有游戏工具适配。

这里只做「观察 / 规划 / 验算 / 执行 / 反思」的编排；决策规则来自各游戏包自带的
`decision/knowledge/`，出牌仍走 `tools/act.py`，人设和语音仍走原内核。
"""
from __future__ import annotations

from .graph import AgentState, build_graph, graph_mermaid, run_turn
from .planner import LLMPlanner, ProvidedPlanner
from .play import start_autoplay, status as play_status, stop_autoplay
from .tools import DemoTools, DemoPlanner
from .trace import TraceRecorder, agent_summary, read_runs

__all__ = [
    "AgentState", "build_graph", "graph_mermaid", "run_turn",
    "LLMPlanner", "ProvidedPlanner", "DemoTools", "DemoPlanner",
    "TraceRecorder", "agent_summary", "read_runs",
    "start_autoplay", "stop_autoplay", "play_status",
]
