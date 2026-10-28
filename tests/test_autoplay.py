"""自动玩法：只测纯选择逻辑，不启动游戏、不调 API。"""
from __future__ import annotations

import json
import os
import time

from dafeiyu.agent.planner import _extract_plan
from dafeiyu.games.registry import GameRegistry

play = GameRegistry().load_game_module("slay-the-spire-2", "autoplay.runner")
assert play is not None

import pytest


@pytest.fixture(autouse=True)
def _isolate_live_stop(tmp_path, monkeypatch):
    # 真实直播时 logs/autoplay.* 可能存在，测试不能继承也不准写回真实状态。
    host = play._host
    monkeypatch.setattr(host, "status_path", tmp_path / "autoplay.json")
    monkeypatch.setattr(host, "stop_path", tmp_path / "autoplay.stop")
    monkeypatch.setattr(host, "log_path", tmp_path / "autoplay.jsonl")
    monkeypatch.setattr(play, "STOP_PATH", host.stop_path)
    monkeypatch.setattr(play, "STATUS_PATH", host.status_path)
    monkeypatch.setattr(play, "LOG_PATH", host.log_path)
    monkeypatch.setattr(play, "_write_status", lambda **kw: dict(play._state, **kw))
    monkeypatch.setattr(play, "_log", lambda *a, **k: None)
    monkeypatch.setattr(play, "_timeline_event", lambda *a, **k: None)
    host.reset(game=play.GAME_ID)
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


def test_llm_usage_accumulates():
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
    assert play._host.status_path.exists()


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
    assert not (tmp_path / "autoplay.stop").exists()
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
    play._host.speech_until = 0.0
    play._remember_speech("它这回合打十六，我先挡满再还手，别急。")
    assert play._host.speech_until > time.time() + 1.0


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


# ---------------------------------------------------------------- 营火：只看可用项
def test_rest_never_picks_a_disabled_option():
    """run-0110 原样复现：满血 1/1、牌全升级，「锻造」被游戏禁用，只能点「休息」。"""
    state = {
        "screen": "REST", "run": {"current_hp": 1, "max_hp": 1},
        "available_actions": ["choose_rest_option", "save_and_quit", "use_potion"],
        "rest": {"options": [
            {"option_id": "HEAL", "title": "休息", "is_enabled": True,
             "description": "回复最大生命值的30%（0）。"},
            {"option_id": "SMITH", "title": "锻造", "is_enabled": False,
             "description": "你没有可以升级的牌。"},
        ]},
    }
    fake = _FakeSts2([state])
    last = play._rest(fake, state)
    assert last == "REST choose_rest_option 0"
    assert fake.calls == [("choose_rest_option", {"option_index": 0})]


def test_rest_falls_back_to_other_enabled_option():
    """想要的被禁用时退到第一个可用项，而不是死磕禁用项。"""
    state = {
        "screen": "REST", "run": {"current_hp": 70, "max_hp": 80},
        "available_actions": ["choose_rest_option"],
        "rest": {"options": [
            {"option_id": "SMITH", "title": "锻造", "is_enabled": False},
            {"option_id": "HEAL", "title": "休息", "is_enabled": True},
        ]},
    }
    fake = _FakeSts2([state])
    last = play._rest(fake, state)
    assert last == "REST choose_rest_option 1"
    assert fake.calls == [("choose_rest_option", {"option_index": 1})]


def test_rest_all_disabled_proceeds_when_possible():
    state = {
        "screen": "REST", "run": {"current_hp": 30, "max_hp": 80},
        "available_actions": ["proceed"],
        "rest": {"options": [{"option_id": "HEAL", "is_enabled": False},
                             {"option_id": "SMITH", "is_enabled": False}]},
    }
    fake = _FakeSts2([state])
    assert play._rest(fake, state) == "REST proceed"
    assert fake.calls == [("proceed", {})]


def test_rest_all_disabled_without_proceed_reports_it():
    state = {
        "screen": "REST", "run": {"current_hp": 30, "max_hp": 80},
        "available_actions": ["choose_rest_option"],
        "rest": {"options": [{"option_id": "SMITH", "is_enabled": False}]},
    }
    fake = _FakeSts2([state])
    assert play._rest(fake, state) == "REST all disabled"
    assert fake.calls == []


def test_rest_option_enabled_reads_client_aliases():
    assert play._rest_option_enabled({"is_enabled": False}) is False
    assert play._rest_option_enabled({"enabled": False}) is False
    assert play._rest_option_enabled({"ok": False}) is False
    assert play._rest_option_enabled({"title": "休息"}) is True
    assert play._rest_option_matches({"option_id": "SMITH", "title": "锻造"},
                                     play._REST_HEAL_WORDS, play._REST_HEAL_IDS) is False
    assert play._rest_option_matches({"option_id": "SMITH"}, ("睡觉",), ("HEAL",)) is False


# ---------------------------------------------------------------- 卡住兜底
class _RejectAct(_FakeSts2):
    """游戏拒绝每个 choose_rest_option（invalid_target），模拟 run-0110 的实机行为。"""

    def act(self, action, **params):
        self.calls.append((action, params))
        raise RuntimeError("choose_rest_option failed: invalid_target "
                           "The selected rest option is disabled.")


_STUCK_REST = {
    "screen": "REST", "run": {"current_hp": 1, "max_hp": 1, "floor": 7, "run_id": "r1"},
    "available_actions": ["choose_rest_option"],
    "rest": {"options": [{"option_id": "HEAL", "title": "休息", "is_enabled": True}]},
}


def test_stall_key_groups_the_same_failure_across_screens():
    k1 = play._stall_key("REST", "ERROR RuntimeError: choose_rest_option failed: "
                                 "invalid_target The selected rest option is disabled.")
    k2 = play._stall_key("REST", "ERROR RuntimeError: choose_rest_option failed: "
                                 "invalid_target The selected rest option is disabled.")
    k3 = play._stall_key("REST", "ERROR RuntimeError: choose_rest_option failed: "
                                 "invalid_target something else")
    assert k1 == k2 == k3 == ("REST", "ERROR invalid_target")
    assert play._stall_key("REST", "REST choose_rest_option 0") is None


def test_stall_guard_trips_after_limit_and_resets_on_progress():
    guard = play._StallGuard(limit=3)
    key = ("REST", "REST all disabled")
    assert [guard.hit(key) for _ in range(3)] == [1, 2, 3]
    guard.clear()
    assert guard.hit(key) == 1
    assert play._stall_key("REST", "REST no options") == ("REST", "REST no options")


def test_loop_stops_instead_of_replaying_a_rejected_action(monkeypatch):
    """回归：被拒绝的动作不再重放几百次，3 次原地打转就收手。"""
    import threading

    fake = _RejectAct([_STUCK_REST])
    monkeypatch.setattr(play, "_import_tools", lambda: (fake, None))
    monkeypatch.setattr(play, "_start_autoplay_recording", lambda *a, **k: None)
    monkeypatch.setattr(play, "_finish_autoplay_recording", lambda *a, **k: None)
    writes = []
    monkeypatch.setattr(play, "_write_status",
                        lambda **kw: writes.append(dict(play._state, **kw)) or dict(kw))

    play._run(play.GAME_ID, mode="local")

    assert len(fake.calls) == 3, fake.calls
    assert all(c[0] == "choose_rest_option" for c in fake.calls)
    assert any(w.get("stopping") and "界面" in str(w.get("last") or "") for w in writes)
    assert play._thread is None


# ---------------------------------------------------------------- 界面决策：LLM 优先
class _FakePlanner:
    """只回放预设 plan 的假规划器，记录每次收到的 context。"""

    def __init__(self, plans):
        self.plans = list(plans)
        self.calls = []

    def plan(self, context, feedback="", goal=""):
        self.calls.append({"context": context, "feedback": feedback, "goal": goal})
        plan = self.plans[min(len(self.calls) - 1, len(self.plans) - 1)]
        if isinstance(plan, dict) and plan.get("__error__"):
            return {"ok": False, "error": plan["__error__"], "planner": "fake"}
        return {"ok": True, "plan": plan, "planner": "fake",
                "usage": {"input": 10, "output": 5, "total": 15}}


_EVENT_STATE = {
    "screen": "EVENT", "run_id": "r1",
    "run": {"floor": 6, "act_id": "0", "current_hp": 57, "max_hp": 80, "gold": 131,
            "deck": [{"card_id": "STRIKE_IRONCLAD", "name": "打击",
                      "energy_cost": 1}],
            "relics": [], "potions": []},
    "available_actions": ["choose_event_option"],
    "event": {"event_id": "TABLET_OF_TRUTH", "title": "真理石板",
              "options": [{"title": "解读", "description": "失去3点最大生命。随机升级一张牌。"},
                          {"title": "砸碎", "description": "回复20点生命。"}]},
}


def test_llm_mode_asks_the_model_for_event_choice(monkeypatch):
    """llm 模式下事件由模型选：它选 1 就点 1（本地兜底才会永远点 0）。"""
    seen = []
    monkeypatch.setattr(play, "_remember_speech", lambda t: seen.append(t))
    fake = _FakeSts2([_EVENT_STATE])
    planner = _FakePlanner([{"say": ["石板还能砸，回 20 点更稳。"],
                             "act": "choose_event_option", "i": 1}])
    last = play._one_step(fake, None, _EVENT_STATE, mode="llm", screen_planner=planner)
    assert last == "EVENT choose_event_option 1"
    assert fake.calls == [("choose_event_option", {"option_index": 1})]
    assert seen and "石板" in seen[-1]
    ctx = planner.calls[0]
    assert ctx["goal"].startswith("第 6 层")
    assert [a["i"] for a in ctx["context"]["acts"]] == [0, 1]
    assert play._host.state.get("tokens_total", 0) >= 15


def test_llm_mode_rejects_actions_outside_the_menu_then_falls_back(monkeypatch):
    """模型写白名单外的动作 → 拒掉、带原因重问，仍不行才落回本地。"""
    monkeypatch.setattr(play, "_remember_speech", lambda t: None)
    fake = _FakeSts2([_EVENT_STATE])
    planner = _FakePlanner([{"act": "save_and_quit"},
                            {"act": "choose_event_option", "i": 99}])
    last = play._one_step(fake, None, _EVENT_STATE, mode="llm", screen_planner=planner)
    assert last == "EVENT 0"  # 本地兜底
    assert fake.calls == [("choose_event_option", {"option_index": 0})]
    assert len(planner.calls) == 2
    assert "不能执行" in planner.calls[1]["feedback"]


def test_llm_mode_retries_once_when_index_is_out_of_range(monkeypatch):
    monkeypatch.setattr(play, "_remember_speech", lambda t: None)
    fake = _FakeSts2([_EVENT_STATE])
    planner = _FakePlanner([{"act": "choose_event_option", "i": 9},
                            {"act": "choose_event_option", "i": 1}])
    last = play._one_step(fake, None, _EVENT_STATE, mode="llm", screen_planner=planner)
    assert last == "EVENT choose_event_option 1"
    assert fake.calls == [("choose_event_option", {"option_index": 1})]


def test_llm_mode_planner_failure_retries_then_falls_back(monkeypatch):
    """超时/网络错也重试一次；两次都失败才落本地（run-0111 商店就是死在这）。"""
    monkeypatch.setattr(play, "_remember_speech", lambda t: None)
    monkeypatch.setattr(play.time, "sleep", lambda s: None)
    fake = _FakeSts2([_EVENT_STATE])
    planner = _FakePlanner([{"__error__": "Read timed out"}])
    last = play._one_step(fake, None, _EVENT_STATE, mode="llm", screen_planner=planner)
    assert last == "EVENT 0"
    assert len(planner.calls) == 2
    assert "超时" in planner.calls[1]["feedback"]


_SHOP_STATE = {
    "screen": "SHOP", "run_id": "r1",
    "run": {"floor": 8, "act_id": "0", "current_hp": 55, "max_hp": 80, "gold": 34,
            "deck": [], "relics": [], "potions": []},
    "available_actions": ["close_shop_inventory", "buy_card"],
    "shop": {"is_open": True,
             "cards": [{"name": "余烬", "price": 130, "available": True}]},
}


def test_shop_with_shelf_open_closes_it_when_the_model_fails(monkeypatch):
    """货架开着时本地兜底必须能关货架，否则整局停在商店（run-0111）。"""
    monkeypatch.setattr(play, "_remember_speech", lambda t: None)
    monkeypatch.setattr(play.time, "sleep", lambda s: None)
    fake = _FakeSts2([_SHOP_STATE])
    planner = _FakePlanner([{"__error__": "Read timed out"}])
    last = play._one_step(fake, None, _SHOP_STATE, mode="llm", screen_planner=planner)
    assert last == "SHOP close"
    assert fake.calls == [("close_shop_inventory", {})]


def test_shop_model_can_pick_close_shelf(monkeypatch):
    monkeypatch.setattr(play, "_remember_speech", lambda t: None)
    fake = _FakeSts2([_SHOP_STATE])
    planner = _FakePlanner([{"say": ["没缺口了，关货架走人。"],
                             "act": "close_shop_inventory"}])
    last = play._one_step(fake, None, _SHOP_STATE, mode="llm", screen_planner=planner)
    assert last == "SHOP close_shop_inventory"
    assert fake.calls == [("close_shop_inventory", {})]


def test_shop_after_buying_leaves_when_no_shelf_is_open(monkeypatch):
    state = {"screen": "SHOP",
             "run": {"floor": 8, "current_hp": 55, "max_hp": 80, "gold": 10,
                     "deck": [], "relics": [], "potions": []},
             "available_actions": ["leave_shop", "buy_card"],
             "shop": {"is_open": False, "cards": []}}
    fake = _FakeSts2([state])
    last = play._one_step(fake, None, state, mode="local")
    assert last == "SHOP leave"
    assert fake.calls == [("leave_shop", {})]


def test_local_mode_never_calls_the_screen_planner():
    fake = _FakeSts2([_EVENT_STATE])
    planner = _FakePlanner([{"act": "choose_event_option", "i": 1}])
    last = play._one_step(fake, None, _EVENT_STATE, mode="local", screen_planner=planner)
    assert last == "EVENT 0"
    assert planner.calls == []


def test_plumbing_screens_stay_local_even_in_llm_mode():
    """主菜单/角色选择是过场管道，不算决策，不喂给模型。"""
    planner = _FakePlanner([{"act": "open_character_select"}])
    state = {"screen": "MAIN_MENU", "available_actions": ["open_character_select"]}
    fake = _FakeSts2([state])
    last = play._one_step(fake, None, state, mode="llm", screen_planner=planner)
    assert last.startswith("START_RUN") or last.startswith("WAIT")
    assert planner.calls == []


# ---------------------------------------------------------------- run-0110 全景回归
_RUN0110_REST = {
    "screen": "REST", "run_id": "r0110",
    "run": {"floor": 7, "act_id": "0", "current_hp": 1, "max_hp": 1, "gold": 0,
            "deck": [{"card_id": "STRIKE_IRONCLAD", "name": "打击", "energy_cost": 1}],
            "relics": [], "potions": []},
    "available_actions": ["save_and_quit", "choose_rest_option", "use_potion",
                          "discard_potion"],
    "rest": {"options": [
        {"index": 0, "option_id": "HEAL", "title": "休息", "is_enabled": True,
         "description": "回复最大生命值的30%（0）。"},
        {"index": 1, "option_id": "SMITH", "title": "锻造", "is_enabled": False,
         "description": "你没有可以升级的牌。"},
    ]},
}


def test_run0110_rest_state_is_reachable_by_the_llm_layer(monkeypatch):
    """真机快照能构建局面包，菜单里只剩可用的「休息」。"""
    monkeypatch.setattr(play, "_remember_speech", lambda t: None)
    from dafeiyu.games.registry import GameRegistry

    actions = GameRegistry().load_game_module("slay-the-spire-2", "decision.actions")
    ctxmod = GameRegistry().load_game_module("slay-the-spire-2", "decision.ctx")
    menu = actions.available(_RUN0110_REST)
    assert [(e["a"], e["i"]) for e in menu] == [("choose_rest_option", 0)]
    brief = ctxmod.build(_RUN0110_REST)
    assert brief.get("screen") == "REST"
    assert brief.get("v")


def test_run0110_model_asking_for_the_disabled_smith_falls_back_to_heal(monkeypatch):
    """模型偏要「锻造」时：拒掉（禁用项不在菜单）→ 本地兜底点「休息」，不再卡死。"""
    monkeypatch.setattr(play, "_remember_speech", lambda t: None)
    fake = _FakeSts2([_RUN0110_REST])
    planner = _FakePlanner([{"act": "choose_rest_option", "i": 1}])
    last = play._one_step(fake, None, _RUN0110_REST, mode="llm", screen_planner=planner)
    assert last == "REST choose_rest_option 0"
    assert fake.calls == [("choose_rest_option", {"option_index": 0})]
    assert len(planner.calls) == 2  # 重问一次后才兜底
