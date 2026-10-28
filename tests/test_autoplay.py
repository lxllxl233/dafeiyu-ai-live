"""自动玩法：只测纯选择逻辑，不启动游戏、不调 API。"""
from __future__ import annotations

import json
import os
import time

from dafeiyu.agent import play
from dafeiyu.agent.planner import _extract_plan

import pytest


@pytest.fixture(autouse=True)
def _isolate_live_stop(tmp_path, monkeypatch):
    # 真实直播时 logs/autoplay.stop 可能存在，测试不能继承这个状态。
    monkeypatch.setattr(play, "STOP_PATH", tmp_path / "autoplay.stop")
    monkeypatch.setattr(play, "_write_status", lambda **kw: dict(play._state, **kw))
    monkeypatch.setattr(play, "_log", lambda *a, **k: None)
    monkeypatch.setattr(play, "_timeline_event", lambda *a, **k: None)
    play._stop.clear()
    yield
    play._stop.clear()


class _FakeSts2:
    def __init__(self, states):
        self.states = list(states)
        self.calls = []
        self.PLAN_PATH = None
        self.i = 0

    def get_state(self):
        return self.states[min(self.i, len(self.states) - 1)]

    def act(self, action, **params):
        self.calls.append((action, params))
        self.i += 1
        return {}

    def cmd_route(self, **kwargs):
        self.calls.append(("cmd_route", kwargs))
        st = self.get_state()
        run = st.get("run") or {}
        self.PLAN_PATH.write_text(json.dumps({
            "identity": {"run_id": st.get("run_id") or run.get("run_id") or "",
                         "act_id": str(run.get("act_id", ""))},
            "path": [[0, 0, "?"], [1, 1, "RestSite"]],
        }), encoding="utf-8")


def test_actions_and_meta_are_normalized():
    state = {"screen": "combat", "available_actions": [{"name": "end_turn"}],
             "run": {"floor": 3, "current_hp": 21, "max_hp": 80}}
    assert play._screen(state) == "COMBAT"
    assert play._acts(state) == ["end_turn"]
    assert play._run_meta(state) == (3, 21, 80)


def test_main_menu_starts_character_select():
    fake = _FakeSts2([
        {"screen": "MAIN_MENU", "available_actions": ["open_character_select"]},
        {"screen": "CHARACTER_SELECT", "available_actions": []},
    ])
    out = play._ensure_run(fake, fake.get_state())
    assert out["screen"] == "CHARACTER_SELECT"
    assert fake.calls == [("open_character_select", {})]


def test_character_select_embarks_ironclad():
    state = {
        "screen": "CHARACTER_SELECT",
        "available_actions": ["select_character", "increase_ascension", "embark"],
        "character_select": {
            "selected_character_id": "IRONCLAD", "ascension": 0, "max_ascension": 1,
            "characters": [{"character_id": "IRONCLAD", "index": 0}],
        },
    }
    fake = _FakeSts2([state, {**state, "character_select": {
        **state["character_select"], "ascension": 1}}, {"screen": "MAP"}])
    out = play._ensure_run(fake, state)
    assert out["screen"] == "MAP"
    assert ("increase_ascension", {}) in fake.calls
    assert ("embark", {}) in fake.calls


def test_map_follows_route_next_node(tmp_path):
    plan_file = tmp_path / "route_plan.json"
    fake = _FakeSts2([{
        "screen": "MAP", "run_id": "R", "run": {"act_id": 0, "current_hp": 70, "max_hp": 80},
        "available_actions": ["choose_map_node"],
        "map": {"nodes": [
            {"row": 0, "col": 0, "is_current": True, "node_type": "Monster"},
            {"row": 1, "col": 0, "is_available": True, "node_type": "Monster"},
            {"row": 1, "col": 1, "is_available": True, "node_type": "RestSite"},
        ]},
    }])
    fake.PLAN_PATH = plan_file
    last = play._choose_map(fake, fake.get_state())
    assert last.startswith("MAP")
    assert ("choose_map_node", {"option_index": 1}) in fake.calls


def test_route_plan_identity_and_next_target():
    state = {
        "run_id": "RUN1", "run": {"act_id": 0},
        "map": {"nodes": [
            {"row": 0, "col": 0, "is_current": True, "node_type": "Monster"},
            {"row": 0, "col": 1, "is_available": True, "node_type": "RestSite"},
        ]},
    }
    nodes, avail = play._map_nodes(state)
    plan = {"identity": {"run_id": "RUN1", "act_id": "0"},
            "path": [[0, 0], [0, 1]]}
    assert play._route_current(plan, state) is True
    assert play._route_target(plan, nodes, avail)["col"] == 1
    assert play._route_current(
        {"identity": {"run_id": "RUN2", "act_id": "0"}}, state) is False


def test_card_selection_picks_each_required_card_once():
    base = {
        "screen": "CARD_SELECTION",
        "available_actions": ["select_deck_card"],
        "selection": {
            "kind": "deck_card_select",
            "prompt": "选择[blue]2[/blue]张普通牌加入到你的[gold]牌组[/gold]。",
            "min_select": 0, "max_select": 0, "selected_count": 0,
            "cards": [{"index": 0, "name": "突破"}, {"index": 1, "name": "全身撞击"}],
        },
    }
    selected = {**base, "selection": {**base["selection"], "selected_count": 1}}
    ready = {**base, "available_actions": ["confirm_selection"],
             "selection": {**base["selection"], "selected_count": 2,
                           "requires_confirmation": True, "can_confirm": True}}
    fake = _FakeSts2([base, selected, ready])
    assert play._selection_need(base["selection"]) == 2
    assert play._card_selection(fake, base) == "CARD select 1/2"
    assert fake.calls == [("select_deck_card", {"option_index": 0})]
    # 第二轮已经选过 0，不能再点 0；这里传入 selected 状态验证索引从 1 开始。
    assert play._card_selection(fake, selected) == "CARD select 2/2"
    assert fake.calls[-1] == ("select_deck_card", {"option_index": 1})
    assert play._card_selection(fake, ready) == "CARD confirm"
    assert fake.calls[-1] == ("confirm_selection", {})


def test_blind_multi_select_uses_ui_fallback(monkeypatch):
    base = {
        "run_id": "RUN1",
        "screen": "CARD_SELECTION",
        "available_actions": ["select_deck_card"],
        "selection": {
            "kind": "deck_card_select",
            "prompt": "选择[blue]2[/blue]张普通牌加入到你的[gold]牌组[/gold]。",
            "min_select": 0, "max_select": 0, "selected_count": 0,
            "cards": [{"index": 0, "card_id": "A"}, {"index": 1, "card_id": "B"}],
        },
    }
    fake = _FakeSts2([base, {"screen": "EVENT", "available_actions": ["choose_event_option"]}])

    def ui_pick(n):
        assert n == 2
        fake.i = 1
        return True

    fake.ui_pick_cards = ui_pick
    monkeypatch.setattr(play.time, "sleep", lambda *_: None)
    play._ui_picks.clear()
    assert play._blind_selection(base["selection"]) is True
    assert play._card_selection(fake, base) == "CARD ui 2 -> EVENT"
    assert fake.calls == []


def test_llm_usage_accumulates(tmp_path, monkeypatch):
    monkeypatch.setattr(play, "STATUS_PATH", tmp_path / "autoplay.json")
    monkeypatch.setattr(play, "LOG_PATH", tmp_path / "autoplay.jsonl")
    with play._lock:
        for key, value in (("tokens_in", 0), ("tokens_out", 0),
                           ("tokens_total", 0), ("llm_calls", 0), ("tokens_last", 0)):
            play._state[key] = value
    play._add_llm_usage({"input": 120, "output": 30, "total": 150}, "run-a")
    play._add_llm_usage({"input": 80, "output": 20, "total": 100}, "run-b")
    st = play.status()
    assert st["tokens_in"] == 200
    assert st["tokens_out"] == 50
    assert st["tokens_total"] == 250
    assert st["tokens_last"] == 100
    assert st["llm_calls"] == 2
    assert play.STATUS_PATH.exists()


def test_stop_from_other_process_keeps_remote_running(monkeypatch, tmp_path):
    monkeypatch.setattr(play, "STOP_PATH", tmp_path / "autoplay.stop")
    monkeypatch.setattr(play, "_thread", None)
    monkeypatch.setattr(play, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(play, "status", lambda: {
        "running": True, "pid": 999999, "mode": "llm", "last": "LLM 思考…"})
    writes = []

    def fake_write(**kw):
        writes.append(kw)
        return {}

    monkeypatch.setattr(play, "_write_status", fake_write)
    play._stop.clear()
    out = play.stop_autoplay()
    assert "正在停手" in out["message"]
    assert any(w.get("stopping") is True for w in writes)
    assert not any(w.get("running") is False for w in writes)


def test_stop_in_same_process_finalizes_recording(monkeypatch, tmp_path):
    monkeypatch.setattr(play, "STOP_PATH", tmp_path / "autoplay.stop")
    monkeypatch.setattr(play, "_thread", None)
    monkeypatch.setattr(play, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(play, "status", lambda: {
        "running": True, "pid": os.getpid(), "mode": "llm", "last": "LLM 思考…"})
    finalized = []
    monkeypatch.setattr(play, "_finish_autoplay_recording",
                        lambda result: finalized.append(result) or {"ok": True})
    writes = []

    def fake_write(**kw):
        writes.append(kw)
        return {}

    monkeypatch.setattr(play, "_write_status", fake_write)
    play._stop.clear()
    out = play.stop_autoplay()
    assert finalized == ["stopped"]
    assert out["message"] == "已停手"
    assert any(w.get("running") is False and w.get("stopping") is False
               for w in writes)


def test_rest_prefers_heal_when_low():
    state = {
        "screen": "REST", "run": {"current_hp": 20, "max_hp": 80},
        "available_actions": ["choose_rest_option"],
        "rest": {"options": [{"title": "锻造"}, {"title": "休息"}]},
    }
    fake = _FakeSts2([state])
    last = play._rest(fake, state)
    assert "1" in last
    assert ("choose_rest_option", {"option_index": 1}) in fake.calls


def test_rest_proceeds_when_option_already_resolved():
    state = {"screen": "REST", "available_actions": ["proceed"],
             "rest": {"options": []}}
    fake = _FakeSts2([state])
    last = play._rest(fake, state)
    assert last == "REST proceed"
    assert fake.calls == [("proceed", {})]


def test_speech_pace_sets_a_wait_window():
    play._speech_until = 0.0
    play._remember_speech("它这回合打十六，我先挡满再还手，别急。")
    assert play._speech_until > time.time() + 1.0


def test_malformed_json_extra_quote_is_repaired():
    raw = ('{"say":["先打"],"seq":[{"c":"STRIKE_IRONCLAD","t":0},'
           '{"end":1}]}')
    bad = raw.replace('"],', '"]",', 1)
    plan = _extract_plan(bad)
    assert plan["seq"][0]["c"] == "STRIKE_IRONCLAD"


def test_unknown_screen_does_not_guess_a_button():
    state = {"screen": "SOMETHING_NEW", "available_actions": ["proceed", "confirm"]}
    fake = _FakeSts2([state])
    last = play._one_step(fake, None, state)
    assert last == "UNKNOWN SOMETHING_NEW"
    assert fake.calls == []
