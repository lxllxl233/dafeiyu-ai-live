"""连续玩不继承上一局上下文：每次请求只有 system + 当前局面 + 本轮目标。

用户关心的是「换一局以后会不会把上一局的楼层/牌组/台词塞进 prompt」。
答案必须一直是「不会」：``LLMPlanner`` 每次只发一条 user 消息（goal/feedback/context），
``context`` 由当前 state 现场构建；跨局共享的只有 5 条硬闸、≤3 张经验卡和提示词。
"""
from __future__ import annotations

import json

from dafeiyu.agent.planner import LLMPlanner
from dafeiyu.core.config import LiveConfig
from dafeiyu.games.registry import GameRegistry

ctx = GameRegistry().load_game_module("slay-the-spire-2", "decision.ctx")
assert ctx is not None


class _Resp:
    status_code = 200
    text = ""

    def __init__(self, content: str):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "total_tokens": 15}}


def _planner(system: str = "SYSTEM") -> LLMPlanner:
    cfg = LiveConfig.load()
    cfg.llm_api_key = "test-key"       # 只在内存里替换，不写回 config.json
    return LLMPlanner(cfg, system_prompt=system)


def _capture(monkeypatch, replies: list[str]) -> list[dict]:
    sent: list[dict] = []
    queue = list(replies)

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return _Resp(queue.pop(0) if queue else '{"act":"proceed"}')

    monkeypatch.setattr("dafeiyu.agent.planner.requests.post", fake_post)
    return sent


def test_each_plan_is_one_system_plus_one_current_turn(monkeypatch):
    sent = _capture(monkeypatch, ['{"act":"choose_map_node","i":0}',
                                  '{"act":"choose_map_node","i":2}'])
    planner = _planner()
    first = planner.plan({"screen": "MAP", "run": {"floor": 24, "run_id": "runA"}},
                         goal="上一局第 24 层")
    second = planner.plan({"screen": "MAP", "run": {"floor": 1, "run_id": "runB"}},
                          goal="新一局第 1 层")
    assert first["ok"] and second["ok"]
    assert len(sent) == 2
    for payload in sent:
        assert len(payload["messages"]) == 2
        assert payload["messages"][0] == {"role": "system", "content": "SYSTEM"}
    one = json.loads(sent[0]["messages"][1]["content"])
    two = json.loads(sent[1]["messages"][1]["content"])
    assert set(one) == {"goal", "feedback", "context"}
    assert set(two) == {"goal", "feedback", "context"}
    assert one["context"]["run"]["run_id"] == "runA"
    assert two["context"]["run"]["run_id"] == "runB"
    assert two["feedback"] == ""
    # 上一局的 run_id / 楼层 / 目标不会出现在第二局的请求里
    body = json.dumps(two, ensure_ascii=False)
    assert "runA" not in body and "24" not in body
    assert "上一局" not in body


def test_previous_plan_text_is_not_replayed(monkeypatch):
    sent = _capture(monkeypatch, ['{"say":["上一局最后一句"],"act":"proceed"}',
                                  '{"act":"proceed"}'])
    planner = _planner()
    planner.plan({"screen": "EVENT", "run": {"run_id": "runA"}}, goal="A")
    planner.plan({"screen": "EVENT", "run": {"run_id": "runB"}}, goal="B")
    assert "上一局最后一句" not in json.dumps(sent[1], ensure_ascii=False)


def _map_state(run_id: str, *, floor: int, deck_n: int, relics: int, hp: int):
    return {
        "screen": "MAP", "run_id": run_id,
        "run": {"run_id": run_id, "floor": floor, "act_id": "0",
                "current_hp": hp, "max_hp": 80, "gold": 99,
                "deck": [{"card_id": "STRIKE_IRONCLAD", "name": f"打击{floor}",
                          "energy_cost": 1} for _ in range(deck_n)],
                "relics": [{"relic_id": f"R{i}", "name": f"遗物{i}"}
                           for i in range(relics)],
                "potions": []},
        "available_actions": ["save_and_quit", "choose_map_node"],
        "map": {"nodes": [{"row": 0, "col": 0, "node_type": "Monster",
                           "is_available": True, "children": []}]},
    }


def test_brief_is_built_from_the_current_run_only():
    a = ctx.build(_map_state("runA", floor=24, deck_n=20, relics=6, hp=30))
    b = ctx.build(_map_state("runB", floor=1, deck_n=12, relics=1, hp=80))

    body_b = json.dumps(b, ensure_ascii=False)
    assert "runA" not in body_b
    assert b["run"]["f"] == 1 and b["run"]["hp"] == 80
    assert b["run"]["deck"] == 12
    assert sum(int(x.get("q") or 1) for x in b["deck"]) == 12
    assert len(b["relics"]) == 1
    assert a["run"]["f"] == 24 and a["run"]["deck"] == 20
    assert sum(int(x.get("q") or 1) for x in a["deck"]) == 20
    # 局面包里根本没有 run_id：模型拿不到上一局的任何标识
    assert "run_id" not in body_b


def test_cross_run_shared_inputs_are_capped():
    """跨局只共享硬闸与经验卡，且经验卡上限 3 条。"""
    brief = ctx.build(_map_state("runB", floor=1, deck_n=12, relics=1, hp=80))
    assert len(brief.get("rules") or []) == 5
    assert len(brief.get("lessons") or []) <= 3
    assert "journal" not in json.dumps(brief)      # 没有整局历史/日记注入
