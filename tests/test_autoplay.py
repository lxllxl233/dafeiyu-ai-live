"""自动玩法：只测纯选择逻辑，不启动游戏、不调 API。"""
from __future__ import annotations

import json
import time

from dafeiyu.agent import play


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
        self.PLAN_PATH.write_text(json.dumps({
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
        "screen": "MAP", "run": {"current_hp": 70, "max_hp": 80},
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


def test_unknown_screen_does_not_guess_a_button():
    state = {"screen": "SOMETHING_NEW", "available_actions": ["proceed", "confirm"]}
    fake = _FakeSts2([state])
    last = play._one_step(fake, None, state)
    assert last == "UNKNOWN SOMETHING_NEW"
    assert fake.calls == []
