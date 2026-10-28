"""LangGraph 回合流程：observe → plan → validate → execute → reflect。

这是「小代价」的接入方式：不重写 `tools/ctx.py` / `tools/act.py` / 人设 / TTS，
只把一次决策拆成可观察的节点。外部 DSH Agent 可以直接传 plan（保持现有技能），
也可以让 `LLMPlanner` 生成 plan；验算失败会在图上回到 plan，最多重试 N 次。

透明性来自两层：
- 每个节点的耗时/状态/token 写 ``logs/agent_trace.jsonl``；
- 控制台「Agent 流程」直接读这份 JSONL，不依赖云 LangSmith。
"""
from __future__ import annotations

import time
from typing import Any, TypedDict

from .planner import ProvidedPlanner
from .tools import DemoPlanner, DemoTools, Tools
from .trace import TraceRecorder

try:  # LangGraph 是可选依赖；没装时 CLI 会给出安装提示
    from langgraph.graph import END, START, StateGraph
except Exception:  # noqa: BLE001
    END = START = StateGraph = None  # type: ignore[assignment]


class AgentState(TypedDict, total=False):
    run_id: str
    goal: str
    game: str
    dry_run: bool
    max_attempts: int
    context: dict
    plan: dict
    plan_source: str
    usage: dict
    feedback: str
    attempts: int
    validation: dict
    result: dict
    error: str
    done: bool
    status: str


def _short_ctx(ctx: Any) -> str:
    if not isinstance(ctx, dict):
        return ""
    bits = []
    for key in ("screen", "act", "floor", "hp", "max_hp", "energy"):
        if key in ctx:
            bits.append(f"{key}={ctx[key]}")
    enemies = ctx.get("enemies")
    if isinstance(enemies, list):
        bits.append(f"enemies={len(enemies)}")
    return " ".join(bits)


def _short_plan(plan: Any) -> str:
    if not isinstance(plan, dict):
        return ""
    seq = plan.get("seq")
    say = plan.get("say") or []
    n = len(seq) if isinstance(seq, list) else 0
    first = ""
    if isinstance(say, list) and say:
        first = str(say[0])[:50]
    elif isinstance(say, str):
        first = say[:50]
    return f"seq={n}" + (f" · {first}" if first else "")


def _node_summary(name: str, out: dict, state: AgentState) -> str:
    if name == "observe":
        return _short_ctx(out.get("context"))
    if name == "plan":
        return f"{out.get('plan_source') or 'plan'} · {_short_plan(out.get('plan'))}"
    if name == "validate":
        v = out.get("validation") or {}
        return "通过" if v.get("ok") else str(v.get("err") or "未通过")[:160]
    if name == "execute":
        r = out.get("result") or {}
        return str(r.get("message") or ("ok" if r.get("ok") else "失败"))
    if name == "reflect":
        return str(out.get("summary") or out.get("status") or "")
    return ""


def build_graph(tools: Tools, planner: Any, recorder: TraceRecorder,
                *, max_attempts: int = 2):
    if StateGraph is None:
        raise RuntimeError("没有安装 langgraph；运行 pip install -e .[agent]")

    def traced(name: str, fn):
        def wrapper(state: AgentState) -> dict:
            t0 = time.time()
            try:
                out = fn(state) or {}
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                recorder.event(name, "error", duration_ms=int((time.time() - t0) * 1000),
                               error=err, attempt=int(state.get("attempts") or 0))
                return {"error": err, "done": True}
            err = str(out.get("error") or "")
            recorder.event(
                name, "error" if err else "ok",
                duration_ms=int((time.time() - t0) * 1000),
                summary=_node_summary(name, out, state),
                error=err, tokens=out.get("usage"),
                attempt=int(out.get("attempts") or state.get("attempts") or 0),
            )
            return out
        wrapper.__name__ = f"node_{name}"
        return wrapper

    def observe(state: AgentState) -> dict:
        res = tools.observe()
        if not res.get("ok"):
            return {"error": str(res.get("error") or "观察失败"), "done": True}
        ctx = res.get("context") if isinstance(res.get("context"), dict) else res
        return {"context": ctx or {}, "game": state.get("game") or ""}

    def plan(state: AgentState) -> dict:
        if state.get("error"):
            return {}
        attempt = int(state.get("attempts") or 0) + 1
        if planner is None:
            return {"error": "没有 planner：请传 --plan，或配置 LLM", "done": True}
        try:
            res = planner.plan(state.get("context") or {},
                               feedback=str(state.get("feedback") or ""),
                               goal=str(state.get("goal") or ""))
        except TypeError:
            # 兼容只接受 (context, feedback) 的轻量 planner
            res = planner.plan(state.get("context") or {},
                               feedback=str(state.get("feedback") or ""))
        if not res.get("ok"):
            return {"error": str(res.get("error") or "规划失败"), "done": True}
        return {
            "plan": res.get("plan") or {},
            "plan_source": str(res.get("planner") or "planner"),
            "usage": res.get("usage") or {},
            "attempts": attempt,
        }

    def validate(state: AgentState) -> dict:
        res = tools.validate(state.get("plan") or {})
        if not res.get("ok"):
            return {"validation": res,
                    "feedback": str(res.get("err") or res.get("error") or "验算未通过")}
        return {"validation": res, "feedback": ""}

    def execute(state: AgentState) -> dict:
        res = tools.execute(state.get("plan") or {},
                            dry_run=bool(state.get("dry_run", True)))
        if not res.get("ok"):
            return {"result": res, "feedback": str(res.get("err") or res.get("error") or "执行失败")}
        return {"result": res, "feedback": ""}

    def reflect(state: AgentState) -> dict:
        v = state.get("validation") or {}
        r = state.get("result") or {}
        if state.get("error"):
            return {"done": True, "status": "error",
                    "summary": str(state.get("error") or "")[:160]}
        if v and not v.get("ok"):
            return {"done": True, "status": "error",
                    "summary": str(v.get("err") or "验算未通过")[:160]}
        if r and not r.get("ok"):
            return {"done": True, "status": "error",
                    "summary": str(r.get("err") or r.get("error") or "执行失败")[:160]}
        return {"done": True, "status": "ok",
                "summary": f"attempts={state.get('attempts') or 0}"}

    builder = StateGraph(AgentState)
    builder.add_node("observe", traced("observe", observe))
    builder.add_node("plan", traced("plan", plan))
    builder.add_node("validate", traced("validate", validate))
    builder.add_node("execute", traced("execute", execute))
    builder.add_node("reflect", traced("reflect", reflect))

    builder.add_edge(START, "observe")
    builder.add_conditional_edges("observe", lambda s: "reflect" if s.get("error") else "plan",
                                  {"reflect": "reflect", "plan": "plan"})
    builder.add_conditional_edges("plan", lambda s: "reflect" if s.get("error") else "validate",
                                  {"reflect": "reflect", "validate": "validate"})
    def after_validate(s: AgentState) -> str:
        if s.get("error"):
            return "reflect"
        if (s.get("validation") or {}).get("ok"):
            return "execute"
        return "plan" if int(s.get("attempts") or 0) < max_attempts else "reflect"

    def after_execute(s: AgentState) -> str:
        if (s.get("result") or {}).get("ok"):
            return "reflect"
        return "plan" if int(s.get("attempts") or 0) < max_attempts else "reflect"

    builder.add_conditional_edges(
        "validate", after_validate,
        {"execute": "execute", "plan": "plan", "reflect": "reflect"},
    )
    builder.add_conditional_edges(
        "execute", after_execute,
        {"plan": "plan", "reflect": "reflect"},
    )
    builder.add_edge("reflect", END)
    return builder.compile()


def run_turn(goal: str, *, tools: Tools | None = None, planner: Any = None,
             plan: dict | None = None, game: str = "slay-the-spire-2",
             dry_run: bool = True, max_attempts: int = 2,
             trace_path=None, mode: str = "demo") -> dict:
    """跑一轮图；`plan` 给了就优先用它（外部 Agent 模式）。"""
    if plan is not None and planner is None:
        planner = ProvidedPlanner(plan)
    if tools is None:
        tools = DemoTools()
    if planner is None:
        planner = DemoPlanner()
    recorder = TraceRecorder(goal, game=game, mode=mode, path=trace_path)
    try:
        graph = build_graph(tools, planner, recorder, max_attempts=max_attempts)
    except Exception as exc:  # noqa: BLE001
        recorder.finish("error", error=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "status": "error", "error": str(exc),
                "run_id": recorder.run_id, "steps": recorder.steps, "trace": str(recorder.path)}
    init: AgentState = {
        "run_id": recorder.run_id, "goal": goal, "game": game,
        "dry_run": bool(dry_run), "max_attempts": int(max_attempts),
        "attempts": 0,
    }
    try:
        final = graph.invoke(init, {"recursion_limit": max(10, max_attempts * 6)})
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        recorder.finish("error", error=err)
        return {"ok": False, "status": "error", "error": err,
                "run_id": recorder.run_id, "steps": recorder.steps, "trace": str(recorder.path)}
    status = "ok" if not final.get("error") and final.get("status") != "error" else "error"
    err = str(final.get("error") or "")
    recorder.finish(status, error=err)
    return {
        "ok": status == "ok",
        "status": status,
        "run_id": recorder.run_id,
        "goal": goal,
        "game": game,
        "mode": mode,
        "dry_run": bool(dry_run),
        "steps": recorder.steps,
        "tokens": dict(recorder.tokens),
        "context": final.get("context") or {},
        "plan": final.get("plan") or {},
        "result": final.get("result") or {},
        "error": err,
        "trace": str(recorder.path),
    }


def graph_mermaid() -> str:
    import tempfile
    from pathlib import Path

    path = Path(tempfile.gettempdir()) / "dafeiyu-agent-topology.jsonl"
    graph = build_graph(DemoTools(), DemoPlanner(),
                        TraceRecorder("topology", path=path), max_attempts=2)
    try:
        return graph.get_graph().draw_mermaid()
    except Exception:  # noqa: BLE001
        return "observe -> plan -> validate -> execute -> reflect"


__all__ = ["AgentState", "build_graph", "run_turn", "graph_mermaid"]
