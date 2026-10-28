"""Agent 的通用工具协议与离线演示；具体游戏工具由 game plugin 提供。

核心只认识 ``observe / validate / execute`` 这三个形状；``DemoTools`` 用于
验证 LangGraph 本身，不碰任何游戏 API。迁移期同时接受 ``ActionResult``
和旧 ``{ok, err}`` dict，图入口会用 ``as_action_result`` 统一。
"""

from __future__ import annotations

from typing import Protocol

from ..games.contracts import ActionCode, ActionResult


class Tools(Protocol):
    def observe(self) -> ActionResult | dict: ...

    def validate(self, plan: dict) -> ActionResult | dict: ...

    def execute(self, plan: dict, *, dry_run: bool = True) -> ActionResult | dict: ...


class DemoTools:
    """离线演示：不启动游戏，专门验证图的边和轨迹。"""

    def __init__(self) -> None:
        self.observed = 0
        self.validated = 0
        self.executed = 0

    def observe(self) -> ActionResult:
        self.observed += 1
        context = {
            "screen": "DEMO", "act": 1, "floor": 6, "hp": 52, "max_hp": 80,
            "energy": 3,
            "hand": [{"id": "DEMO_STRIKE"}, {"id": "DEMO_GUARD"}],
            "enemies": [{"id": "DEMO_ENEMY", "hp": 48, "intent": "ATTACK"}],
            "note": "离线演示局面；不会真的出牌",
        }
        return ActionResult.success("observe", data={"context": context})

    def validate(self, plan: dict) -> ActionResult:
        self.validated += 1
        seq = plan.get("seq") if isinstance(plan, dict) else None
        if not isinstance(seq, list) or not seq:
            return ActionResult.failure(
                "validate", code=ActionCode.INVALID_PLAN,
                message="演示计划需要非空 seq")
        return ActionResult.success(
            "validate", data={"summary": "演示验算通过", "seq": len(seq)})

    def execute(self, plan: dict, *, dry_run: bool = True) -> ActionResult:
        self.executed += 1
        return ActionResult.success(
            "execute", message="演示流程完成（未调用游戏）",
            data={"dry_run": bool(dry_run)})


class DemoPlanner:
    """无 LLM 的固定计划，用来演示和测试；真实决策由游戏插件提供。"""

    name = "demo"

    def plan(self, context: dict, feedback: str = "") -> dict:
        seq = [{"c": "DEMO_STRIKE", "t": 0}, {"end": 1}]
        if feedback:
            seq = [{"end": 1}]
        return {
            "ok": True,
            "plan": {"say": ["先算一下，这回合不贪。"], "seq": seq},
            "usage": {"input": 0, "output": 0, "total": 0},
            "planner": "demo",
        }


__all__ = ["Tools", "DemoTools", "DemoPlanner"]
