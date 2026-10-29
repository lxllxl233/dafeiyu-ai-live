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
        self.said = []
        self.order = []
        self.PLAN_PATH = None
        self.i = 0

    def tag_for(self, screen):
        return {"EVENT": "事件", "REST": "营火", "SHOP": "商店",
                "MAP": "路线", "CARD_SELECTION": "选牌"}.get(screen, "思路")

    def overlay_say(self, tag, thought, meme, state=None, force=False):
        self.said.append((tag, thought, meme))
        self.order.append(("say", tag))
        return {}

    def get_state(self):
        return self.states[min(self.i, len(self.states) - 1)]

    def act(self, action, **params):
        self.calls.append((action, params))
        self.order.append(("act", action))
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
    play._deck_clicks.clear()
    fake = _FakeSts2([base, selected, ready])
    assert play._selection_need(base["selection"]) == 2
    assert play._card_selection(fake, base) == "CARD select 1/2"
    assert fake.calls == [("select_deck_card", {"option_index": 0})]
    # 第二轮已经选过 0，不能再点 0；这里传入 selected 状态验证索引从 1 开始。
    assert play._card_selection(fake, selected) == "CARD select 2/2"
    assert fake.calls[-1] == ("select_deck_card", {"option_index": 1})
    assert play._card_selection(fake, ready) == "CARD confirm"
    assert fake.calls[-1] == ("confirm_selection", {})


def test_blind_multi_select_clicks_then_confirms(monkeypatch):
    """mod 修好后牌组多选不再走鼠标坐标兜底：逐张点不同牌，再 confirm_selection。"""
    monkeypatch.setattr(play, "_deck_clicks", {})
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
    ready = {**base, "available_actions": ["confirm_selection"],
             "selection": {**base["selection"], "selected_count": 2,
                           "requires_confirmation": True, "can_confirm": True}}
    fake = _FakeSts2([base, ready])
    assert play._blind_selection(base["selection"]) is True
    assert play._card_selection(fake, base) == "CARD select 1/2"
    assert play._card_selection(fake, base) == "CARD select 2/2"
    assert play._card_selection(fake, ready) == "CARD confirm"
    assert [c[0] for c in fake.calls] == [
        "select_deck_card", "select_deck_card", "confirm_selection"]
    assert fake.calls[0][1] == {"option_index": 0}
    assert fake.calls[1][1] == {"option_index": 1}


def _deck_cards():
    return [
        {"index": 0, "card_id": "BASH", "name": "痛击", "card_type": "Attack",
         "rarity": "Basic", "energy_cost": 2},
        {"index": 1, "card_id": "STRIKE_IRONCLAD", "name": "打击", "card_type": "Attack",
         "rarity": "Basic", "energy_cost": 1},
        {"index": 2, "card_id": "DEFEND_IRONCLAD", "name": "防御", "card_type": "Skill",
         "rarity": "Basic", "energy_cost": 1},
    ]


def _deck_select_state(run_id, kind, prompt, need, cards):
    return {
        "run_id": run_id,
        "screen": "CARD_SELECTION",
        "available_actions": ["select_deck_card"],
        "run": {"deck": cards},
        "selection": {
            "kind": kind,
            "prompt": prompt,
            "min_select": need,
            "max_select": need,
            "selected_count": 0,
            "cards": cards,
        },
    }


def test_removal_prefers_strikes_then_defends(monkeypatch):
    """删牌别删核心：先打击、再防御，痛击留最后。"""
    monkeypatch.setattr(play, "_deck_clicks", {})
    cards = _deck_cards()
    state = _deck_select_state("R1", "deck_card_select", "选择 2 张牌来移除", 2, cards)
    fake = _FakeSts2([state])
    assert play._card_selection(fake, state) == "CARD select 1/2"
    assert fake.calls[-1] == ("select_deck_card", {"option_index": 1})  # 打击
    assert play._card_selection(fake, state) == "CARD select 2/2"
    assert fake.calls[-1] == ("select_deck_card", {"option_index": 2})  # 防御


def test_transform_prefers_strike_over_core(monkeypatch):
    monkeypatch.setattr(play, "_deck_clicks", {})
    cards = _deck_cards()
    state = _deck_select_state("R2", "deck_transform_select", "选择 1 张牌来变化", 1, cards)
    fake = _FakeSts2([state])
    assert play._card_selection(fake, state) == "CARD select 1/1"
    assert fake.calls[-1] == ("select_deck_card", {"option_index": 1})  # 打击


def test_upgrade_prefers_core_card(monkeypatch):
    """升级先升核心牌（BASH），而不是打击/防御。"""
    monkeypatch.setattr(play, "_deck_clicks", {})
    cards = _deck_cards()
    state = _deck_select_state("R3", "deck_upgrade_select", "选择 1 张牌来升级", 1, cards)
    fake = _FakeSts2([state])
    assert play._card_selection(fake, state) == "CARD select 1/1"
    assert fake.calls[-1] == ("select_deck_card", {"option_index": 0})  # 痛击


def _decision_module(name):
    import importlib
    import sys
    from pathlib import Path

    dec = Path(play.__file__).resolve().parent.parent / "decision"
    if str(dec) not in sys.path:
        sys.path.insert(0, str(dec))
    return importlib.import_module(name)


def test_stunned_turn_just_ends():
    """BOSS 眩晕：手牌全不可打，只 end_turn，别去问模型（run-0114 卡死）。"""
    state = {
        "screen": "COMBAT",
        "available_actions": ["end_turn", "use_potion", "discard_potion"],
        "run": {"floor": 17, "current_hp": 76, "max_hp": 85},
        "combat": {
            "player": {"current_hp": 76, "max_hp": 85, "energy": 2},
            "hand": [{"name": "打击", "card_id": "STRIKE_IRONCLAD", "playable": False},
                     {"name": "巨像", "card_id": "COLOSSUS", "playable": False}],
        },
    }
    fake = _FakeSts2([state])
    out = play._combat(fake, None, state, mode="llm", llm_tools=object(),
                       llm_planner=object(), game=play.GAME_ID)
    assert out == "COMBAT pass"
    assert fake.calls == [("end_turn", {})]


def test_cli_system_exit_becomes_runtime_error():
    """sts2.py 的 CLI 用 sys.exit(3) 拒绝动作；不能让 BaseException 逃出主循环。"""

    def boom():
        raise SystemExit(3)

    with pytest.raises(RuntimeError):
        play._call(None, boom)


def test_run_meta_prefers_combat_hp():
    state = {
        "screen": "COMBAT",
        "run": {"floor": 17, "current_hp": 76, "max_hp": 85},
        "combat": {"player": {"current_hp": 64, "max_hp": 85}},
    }
    assert play._run_meta(state) == (17, 64, 85)


def test_run_meta_matches_when_in_sync():
    state = {
        "screen": "COMBAT",
        "run": {"floor": 17, "current_hp": 64, "max_hp": 85},
        "combat": {"player": {"current_hp": 64, "max_hp": 85}},
    }
    assert play._run_meta(state) == (17, 64, 85)


def test_validate_says_unplayable_not_999():
    """眩晕时整手牌不可打；报错别写「要 999 费」，否则模型以为补能量就行。"""
    sim = _decision_module("sim")
    state = {
        "screen": "COMBAT",
        "run": {"floor": 17, "current_hp": 64, "max_hp": 85},
        "combat": {
            "player": {"current_hp": 64, "max_hp": 85, "energy": 2},
            "hand": [{"name": "打击", "card_id": "STRIKE_IRONCLAD",
                      "energy_cost": 1, "card_type": "Attack", "playable": False}],
        },
    }
    ok, err, _sim, _steps = sim.validate(state, [{"c": "STRIKE_IRONCLAD"}])
    assert ok is False
    assert "本回合不能打" in err


def test_calc_line_reports_end_hp():
    """模型自己减血量老差 5（run-0114 写 74、账本 69）；把回合末血直接写进 calc.line。"""
    ctx = _decision_module("ctx")
    line = ctx._calc_line(64, 85, {"blk": 13, "inc": 19, "net": 6,
                                   "hp": 64, "hp_after": 58})
    assert "回合末血58" in line


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


# ---------------------------------------------------------------- LLM 失败兜底
def test_combat_llm_transport_failure_falls_back_to_local(monkeypatch):
    """Read timeout / 连接重置这类异常也要本地兜底，不能把整局停掉。

    以前只认 LLM_FAILED_PLAN/VALIDATE/EXECUTE 三种前缀，run_turn 直接抛异常
    返回的「LLM_FAILED ReadTimeout」会落到主循环的 LLM_FAILED 分支收尾。
    """
    monkeypatch.setattr(play, "_combat_llm",
                        lambda *a, **k: "LLM_FAILED ReadTimeout: read timed out")
    monkeypatch.setattr(play, "_combat_local",
                        lambda sts2, advisor, st: "COMBAT 6dmg")
    state = {"screen": "COMBAT", "available_actions": ["play_card", "end_turn"]}
    fake = _FakeSts2([state])
    last = play._combat(fake, None, state, mode="llm", llm_tools=object(),
                        llm_planner=object(), game=play.GAME_ID)
    assert last == "LLM 失败，本地兜底（COMBAT 6dmg）"


def test_screen_llm_transient_error_does_not_double_retry():
    """planner 内部已重试 3 次；再过一层就是 6 次请求，一步能拖几分钟。"""
    calls = []

    class _Planner:
        def plan(self, brief, feedback="", goal=""):
            calls.append(feedback)
            return {"ok": False, "error": "ReadTimeout: read timed out"}

    state = {"screen": "MAP", "available_actions": ["choose_map_node"],
             "map": {"nodes": [{"node_type": "Monster", "row": 0, "col": 0,
                                "is_available": True}]}}
    fake = _FakeSts2([state])
    assert play._screen_llm(fake, _Planner(), state, "MAP") == ""
    assert len(calls) == 1


def test_screen_llm_invalid_plan_is_asked_again():
    calls = []

    class _Planner:
        def plan(self, brief, feedback="", goal=""):
            calls.append(feedback)
            return {"ok": True, "plan": {"act": "choose_map_node", "i": 9}}

    state = {"screen": "MAP", "available_actions": ["choose_map_node"],
             "map": {"nodes": [{"node_type": "Monster", "row": 0, "col": 0,
                                "is_available": True}]}}
    fake = _FakeSts2([state])
    assert play._screen_llm(fake, _Planner(), state, "MAP") == ""
    assert len(calls) == 2
    assert "不能执行" in calls[1]


def test_blind_deck_multi_select_clicks_different_cards(monkeypatch):
    """牌组多选要一张一张换着点：重复点 0 会取消选择（run-0113 的 6 小时空转）。"""
    monkeypatch.setattr(play, "_deck_clicks", {})
    state = {"screen": "CARD_SELECTION", "run_id": "runA",
             "available_actions": ["select_deck_card"],
             "selection": {"prompt": "选择 2 张牌来移除", "min_select": 0,
                           "max_select": 0,
                           "cards": [{"card_id": f"C{i}", "name": f"牌{i}"}
                                     for i in range(10)]}}
    fake = _FakeSts2([state])
    first = play._card_selection(fake, state)
    second = play._card_selection(fake, state)
    third = play._card_selection(fake, state)
    assert [c[0] for c in fake.calls] == ["select_deck_card", "select_deck_card"]
    assert fake.calls[0][1] == {"option_index": 0}
    assert fake.calls[1][1] == {"option_index": 1}
    assert (first, second) == ("CARD select 1/2", "CARD select 2/2")
    assert third == "CARD wait 2/2"


def test_blind_deck_multi_select_skips_llm_and_does_not_click():
    """run-0113：删牌多选屏只有 pending，模型点了 1332 次、烧 530 万 token。

    识别出「min/max 都是 0 的多选」后不再问模型，也不空点 1000 次，交给本地
    逐张点选；点完仍不推进就由卡住守卫回滚。
    """
    called = []

    class _Planner:
        def plan(self, *a, **k):
            called.append(1)
            return {"ok": True, "plan": {"act": "select_deck_card", "i": 0}}

    state = {"screen": "CARD_SELECTION", "run_id": "runB",
             "available_actions": ["select_deck_card"],
             "selection": {"prompt": "选择 2 张牌来移除", "min_select": 0,
                           "max_select": 0,
                           "cards": [{"card_id": f"C{i}", "name": f"牌{i}"}
                                     for i in range(10)]}}
    fake = _FakeSts2([state])
    last = play._one_step(fake, None, state, mode="llm", llm_tools=object(),
                          llm_planner=object(), screen_planner=_Planner(),
                          game=play.GAME_ID)
    assert called == []
    # 本地只点一次就停手等卡住守卫（不会再点 1000 多次）
    assert last == "CARD select 1/2"
    assert fake.calls == [("select_deck_card", {"option_index": 0})]


def test_repeated_same_card_click_counts_as_stall():
    same = play._stall_key("CARD_SELECTION", "CARD_SELECTION select_deck_card 0")
    other = play._stall_key("CARD_SELECTION", "CARD_SELECTION select_deck_card 1")
    assert same is not None and same != other


def test_blind_selection_rolls_back_only_once(monkeypatch):
    monkeypatch.setattr(play, "_rollback_tried", False)
    state = {"screen": "CARD_SELECTION",
             "selection": {"prompt": "选择 2 张牌来移除"}}
    fake = _FakeSts2([state])
    assert play._recover_blind_screen(fake, state, "CARD ui wait 2") is True
    assert fake.calls == [("save_and_quit", {})]
    assert play._recover_blind_screen(fake, state, "CARD ui wait 2") is False
    assert len(fake.calls) == 1


def test_recover_does_not_trigger_on_unrelated_screens(monkeypatch):
    monkeypatch.setattr(play, "_rollback_tried", False)
    fake = _FakeSts2([{"screen": "SHOP"}])
    assert play._recover_blind_screen(fake, {"screen": "SHOP"}, "SHOP buy_card 0") is False
    assert fake.calls == []


def test_repeated_screen_speech_is_not_spoken_twice(monkeypatch):
    """B 站把「长时间重复同一内容」算挂机；同一句台词别刷第二遍。"""
    monkeypatch.setattr(play, "_recent_speech", {})
    fake = _FakeSts2([{"screen": "REST"}])
    play._say_screen(fake, "先睡一口，血太少了", "REST")
    play._say_screen(fake, "先睡一口，血太少了", "REST")
    assert len(fake.said) == 1


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


def test_stall_key_catches_selection_and_wait_loops():
    # 选牌选够但没确认按钮、主菜单没动作：原来不算卡住，会一直转到 5000 步
    assert play._stall_key("CARD_SELECTION", "CARD wait 2/2") == ("CARD_SELECTION", "CARD wait 2/2")
    assert play._stall_key("CARD_SELECTION", "CARD ui wait 2") == ("CARD_SELECTION", "CARD ui wait 2")
    assert play._stall_key("CARD_SELECTION", "CARD select 1/2") == ("CARD_SELECTION", "CARD select 1/2")
    assert play._stall_key("CARD_SELECTION", "CARD select 2/2") != ("CARD_SELECTION", "CARD select 1/2")
    assert play._stall_key("MAIN_MENU", "WAIT_MAIN_MENU") == ("MAIN_MENU", "WAIT_MAIN_MENU")


def test_chest_and_modal_fallbacks_use_the_real_action_names():
    fake = _FakeSts2([])
    chest = {"screen": "CHEST", "available_actions": ["claim_all_rewards"]}
    assert play._chest(fake, chest) == "CHEST claim all"
    assert fake.calls[-1] == ("claim_all_rewards", {})
    chest2 = {"screen": "CHEST", "available_actions": ["close_chest"]}
    assert play._chest(fake, chest2) == "CHEST close"
    modal = {"screen": "MODAL", "available_actions": ["dismiss_modal"]}
    assert play._modal(fake, modal) == "MODAL dismiss_modal"


def test_unknown_screen_is_retried_before_stopping(monkeypatch):
    """一次未知不应直接判死：连续 3 次同画面才收尾（run-0111 的教训）。"""
    state = {"screen": "SOMETHING_NEW", "available_actions": []}
    fake = _FakeSts2([state])
    monkeypatch.setattr(play, "_import_tools", lambda: (fake, None))
    monkeypatch.setattr(play, "_start_autoplay_recording", lambda *a, **k: None)
    monkeypatch.setattr(play, "_finish_autoplay_recording", lambda *a, **k: None)
    monkeypatch.setattr(play, "_pace", lambda *a, **k: None)
    monkeypatch.setattr(play.time, "sleep", lambda s: None)
    logs = []
    monkeypatch.setattr(play, "_log", lambda msg, **kw: logs.append((msg, kw)))
    writes = []
    monkeypatch.setattr(play, "_write_status",
                        lambda **kw: writes.append(dict(kw)) or dict(kw))

    play._run(play.GAME_ID, mode="local")

    hits = [kw.get("hits") for msg, kw in logs if msg == "unknown_screen"]
    assert hits == [1, 2, 3]
    assert fake.calls == []
    assert play._thread is None


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
    # 只 remember 不说话是旧 bug：必须真的推给 overlay（桌面 OBS 才有字幕/配音）。
    assert fake.said and fake.said[-1][0] == "事件" and "石板" in fake.said[-1][1]
    # 先说话再点：观众先听到理由，再看到画面变化
    assert [x[0] for x in fake.order] == ["say", "act"]
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


_BUNDLE_STATE = {
    "screen": "BUNDLE_SELECTION", "run_id": "r1",
    "run": {"floor": 1, "act_id": "0", "current_hp": 64, "max_hp": 80, "gold": 99,
            "deck": [], "relics": [], "potions": []},
    "available_actions": ["choose_bundle", "confirm_bundle"],
    "bundles": [
        {"index": 0, "cards": [
            {"name": "痛击", "card_type": "Attack", "energy_cost": 2,
             "resolved_rules_text": "造成10点伤害。"},
            {"name": "防御", "card_type": "Skill", "energy_cost": 1,
             "resolved_rules_text": "获得5点格挡。"}]},
        {"index": 1, "cards": [
            {"name": "打击", "card_type": "Attack", "energy_cost": 1,
             "resolved_rules_text": "造成6点伤害。"}]},
    ],
}


def test_bundle_menu_lists_bundles_and_confirm():
    """开局选牌包没有菜单时整局停在第 1 层（run-0116 之前那次）。"""
    actions = _decision_module("actions")
    menu = actions.available(_BUNDLE_STATE)
    picks = [e for e in menu if e["a"] == "choose_bundle"]
    assert [e["i"] for e in picks] == [0, 1]
    assert "痛击" in picks[0]["n"] and "10点伤害" in picks[0]["fx"]
    assert [e for e in menu if e["a"] == "confirm_bundle"]
    entry, err = actions.check({"act": "choose_bundle", "i": 1}, _BUNDLE_STATE)
    assert err == "" and entry["i"] == 1


def test_one_step_bundle_picks_first(monkeypatch):
    monkeypatch.setattr(play, "_remember_speech", lambda t: None)
    fake = _FakeSts2([_BUNDLE_STATE])
    last = play._one_step(fake, None, _BUNDLE_STATE, mode="local")
    assert last == "BUNDLE pick"
    assert fake.calls == [("choose_bundle", {"option_index": 0})]


def test_one_step_bundle_confirms_when_only_confirm(monkeypatch):
    monkeypatch.setattr(play, "_remember_speech", lambda t: None)
    st = dict(_BUNDLE_STATE, available_actions=["confirm_bundle"])
    fake = _FakeSts2([st])
    assert play._one_step(fake, None, st, mode="local") == "BUNDLE confirm"
    assert fake.calls == [("confirm_bundle", {})]


def test_modal_menu_hides_underlying_options():
    """确认框弹着时底层屏幕的选项点不动，菜单只能给确认/取消。"""
    actions = _decision_module("actions")
    st = {"screen": "EVENT", "modal": {"title": "放弃本局？"},
          "available_actions": ["confirm_modal", "dismiss_modal"],
          "event": {"options": [{"title": "离开", "description": "x"}]}}
    menu = actions.available(st)
    assert [e["a"] for e in menu] == ["confirm_modal", "dismiss_modal"]


def test_one_step_confirms_modal(monkeypatch):
    monkeypatch.setattr(play, "_remember_speech", lambda t: None)
    st = {"screen": "MAIN_MENU", "modal": {"title": "放弃本局？"},
          "available_actions": ["confirm_modal", "dismiss_modal"]}
    fake = _FakeSts2([st])
    assert play._one_step(fake, None, st, mode="local") == "MODAL confirm_modal"
    assert fake.calls == [("confirm_modal", {})]


def test_map_menu_uses_available_node_index():
    """模组 choose_map_node 的 option_index 是 available_nodes 的下标。"""
    actions = _decision_module("actions")
    st = {"screen": "MAP", "available_actions": ["choose_map_node"],
          "map": {"nodes": [{"row": 0, "col": 0, "is_available": True,
                             "node_type": "Monster"}],
                  "available_nodes": [{"index": 0, "row": 1, "col": 2,
                                       "node_type": "Shop"}]}}
    menu = actions.available(st)
    assert menu == [{"a": "choose_map_node", "i": 0, "n": "Shop (1,2)", "fx": ""}]


def test_select_menu_offers_close_cards_view():
    actions = _decision_module("actions")
    st = {"screen": "CARD_SELECTION", "available_actions": ["close_cards_view"],
          "selection": {"cards": []}}
    assert [e["a"] for e in actions.available(st)] == ["close_cards_view"]


# mod（STS2-Agent v0.8.0 + compat 补丁）在 GameActionService 里实际支持的界面动作。
# 白名单菜单只能引用这些名字：多写一个是死项，少写一个可能让整屏没有出口。
_MOD_SCREEN_ACTIONS = {
    "abandon_run", "buy_card", "buy_potion", "buy_relic", "choose_bundle",
    "choose_capstone_option", "choose_event_option", "choose_map_node",
    "choose_rest_option", "choose_reward_card", "choose_timeline_epoch",
    "choose_treasure_relic", "claim_reward", "close_cards_view",
    "close_main_menu_submenu", "close_shop_inventory",
    "collect_rewards_and_proceed", "confirm_bundle", "confirm_modal",
    "confirm_selection", "confirm_timeline_overlay", "continue_run",
    "decrease_ascension", "discard_potion", "disconnect_multiplayer_lobby",
    "dismiss_modal", "embark", "end_turn", "host_multiplayer_lobby",
    "increase_ascension", "join_multiplayer_lobby", "open_character_select",
    "open_chest", "open_shop_inventory", "open_timeline", "play_card",
    "proceed", "ready_multiplayer_lobby", "remove_card_at_shop",
    "resolve_rewards", "return_to_main_menu", "select_character",
    "select_deck_card", "skip_reward_cards", "unready", "use_potion",
}


def test_menu_only_uses_real_mod_actions():
    """菜单里写错/写死一个动作名就会像商店删牌那样整屏没有出口。"""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "games/slay-the-spire-2/decision/actions.py"
    used = set(re.findall(r'"a":\s*"([a-z_]+)"', src.read_text(encoding="utf-8")))
    assert used <= _MOD_SCREEN_ACTIONS, sorted(used - _MOD_SCREEN_ACTIONS)


_REST_STATE = {
    "screen": "REST", "run_id": "r1",
    "run": {"floor": 33, "act_id": "0", "current_hp": 70, "max_hp": 80, "gold": 200,
            "deck": [], "relics": [], "potions": []},
    "available_actions": ["choose_rest_option", "proceed"],
    "rest": {"options": [
        {"index": 0, "option_id": "HEAL", "title": "休息", "is_enabled": True},
        {"index": 1, "option_id": "SMITH", "title": "锻造", "is_enabled": True}]},
}


def test_rest_menu_lists_forge_option():
    """锻造不是独立动作：和休息一样走 choose_rest_option，靠 i 区分。"""
    actions = _decision_module("actions")
    menu = actions.available(_REST_STATE)
    assert [(e["a"], e["i"], e["n"]) for e in menu] == [
        ("choose_rest_option", 0, "休息"), ("choose_rest_option", 1, "锻造")]
    entry, err = actions.check({"act": "choose_rest_option", "i": 1}, _REST_STATE)
    assert err == "" and entry["i"] == 1


def test_rest_menu_hides_disabled_forge():
    st = {"screen": "REST", "run": {"current_hp": 80, "max_hp": 80},
          "available_actions": ["choose_rest_option", "proceed"],
          "rest": {"options": [
              {"index": 0, "option_id": "HEAL", "title": "休息", "is_enabled": True},
              {"index": 1, "option_id": "SMITH", "title": "锻造", "is_enabled": False}]}}
    actions = _decision_module("actions")
    assert [e["n"] for e in actions.available(st)] == ["休息"]


def test_local_rest_prefers_forge_when_healthy(monkeypatch):
    monkeypatch.setattr(play, "_remember_speech", lambda t: None)
    fake = _FakeSts2([_REST_STATE])
    assert play._one_step(fake, None, _REST_STATE, mode="local") == "REST choose_rest_option 1"
    assert fake.calls == [("choose_rest_option", {"option_index": 1})]


def test_bundle_ctx_has_card_text():
    ctx = _decision_module("ctx")
    ch = ctx._choice(_BUNDLE_STATE)
    assert ch["kind"] == "bundle"
    assert ch["items"][0]["cards"][0]["n"] == "痛击"
    assert "10点伤害" in ch["items"][0]["cards"][0]["fx"]


def test_shop_with_shelf_open_closes_it_when_the_model_fails(monkeypatch):
    """货架开着时本地兜底必须能关货架，否则整局停在商店（run-0111）。"""
    monkeypatch.setattr(play, "_remember_speech", lambda t: None)
    monkeypatch.setattr(play.time, "sleep", lambda s: None)
    fake = _FakeSts2([_SHOP_STATE])
    planner = _FakePlanner([{"__error__": "Read timed out"}])
    last = play._one_step(fake, None, _SHOP_STATE, mode="llm", screen_planner=planner)
    assert last == "SHOP close"
    assert fake.calls == [("close_shop_inventory", {})]


_SHOP_REMOVAL_STATE = {
    "screen": "SHOP", "run_id": "r1",
    "run": {"floor": 30, "act_id": "1", "current_hp": 82, "max_hp": 95, "gold": 446,
            "deck": [], "relics": [], "potions": []},
    "available_actions": ["close_shop_inventory", "buy_card", "remove_card_at_shop"],
    "shop": {"is_open": True,
             "cards": [{"name": "余烬", "price": 130, "available": True}],
             "card_removal": {"price": 100, "available": True, "used": False,
                              "enough_gold": True}},
}


def test_shop_menu_offers_card_removal():
    """删牌服务不进菜单时模型只能开/关货架，整局卡在商店（run-0116 18 分钟）。"""
    actions = _decision_module("actions")
    menu = actions.available(_SHOP_REMOVAL_STATE)
    e = [x for x in menu if x["a"] == "remove_card_at_shop"]
    assert len(e) == 1
    assert e[0]["i"] is None          # 模组接口不收参数，别传 option_index
    assert e[0]["ok"] is True
    entry, err = actions.check({"act": "remove_card_at_shop"}, _SHOP_REMOVAL_STATE)
    assert err == "" and entry["a"] == "remove_card_at_shop"


_SHOP_INFO_STATE = {
    "screen": "SHOP", "run_id": "r1",
    "run": {"floor": 30, "act_id": "1", "current_hp": 82, "max_hp": 95, "gold": 446,
            "deck": [], "relics": [], "potions": [{"potion_id": None}]},
    "available_actions": ["close_shop_inventory", "buy_card", "buy_relic", "buy_potion"],
    "shop": {"is_open": True,
             "cards": [{"name": "无情猛攻", "card_id": "UNRELENTING", "price": 76,
                        "available": True,
                        "resolved_rules_text": "造成14点伤害。"}],
             "relics": [{"relic_id": "RIPPLE_BASIN", "name": "波纹水盆",
                         "price": 208, "available": True}],
             "potions": [{"potion_id": "CLARITY", "name": "明晰提取物", "price": 76,
                          "available": True, "usage": "CombatOnly"}]},
}


def test_shop_ctx_shows_relic_and_potion_effects():
    """商店 payload 只有名字/价格；不补效果，模型只能凭名字猜（历史上一件遗物都没买过）。"""
    ctx = _decision_module("ctx")
    ch = ctx._choice(_SHOP_INFO_STATE)
    relic = [x for x in ch["items"] if x["sec"] == "relics"][0]
    potion = [x for x in ch["items"] if x["sec"] == "potions"][0]
    card = [x for x in ch["items"] if x["sec"] == "cards"][0]
    assert "格挡" in relic["fx"]           # 从 RELIC_DB 补的
    assert potion["fx"]                    # 从 POTION_DB 补的
    assert potion["use"] == "仅战斗"
    assert card["fx"] == "造成14点伤害。"


def test_shop_menu_labels_relic_and_potion_effects():
    actions = _decision_module("actions")
    menu = actions.available(_SHOP_INFO_STATE)
    relic = [x for x in menu if x["a"] == "buy_relic"][0]
    potion = [x for x in menu if x["a"] == "buy_potion"][0]
    card = [x for x in menu if x["a"] == "buy_card"][0]
    assert "格挡" in relic["fx"]
    assert "仅战斗" in potion["fx"]
    assert card["fx"] == "造成14点伤害。"


def test_shop_menu_hides_used_or_unaffordable_removal():
    actions = _decision_module("actions")
    used = {**_SHOP_REMOVAL_STATE,
            "shop": {**_SHOP_REMOVAL_STATE["shop"],
                     "card_removal": {"price": 100, "available": False,
                                      "used": True, "enough_gold": False}}}
    assert not [x for x in actions.available(used) if x["a"] == "remove_card_at_shop"]
    poor = {**_SHOP_REMOVAL_STATE,
            "run": {**_SHOP_REMOVAL_STATE["run"], "gold": 30}}
    # 买不起时 available() 整项过滤掉（和其它商品一致），别留 ok:False 的诱饵。
    assert not [x for x in actions.available(poor) if x["a"] == "remove_card_at_shop"]


def test_shop_toggle_counts_as_no_progress():
    assert play._stall_key("SHOP", "SHOP open") == ("SHOP", "SHOP toggle")
    assert play._stall_key("SHOP", "SHOP close") == ("SHOP", "SHOP toggle")


def test_shop_model_can_buy_card_removal(monkeypatch):
    monkeypatch.setattr(play, "_remember_speech", lambda t: None)
    fake = _FakeSts2([_SHOP_REMOVAL_STATE])
    planner = _FakePlanner([{"say": ["删一张打击，100 金。"],
                             "act": "remove_card_at_shop"}])
    last = play._one_step(fake, None, _SHOP_REMOVAL_STATE, mode="llm",
                          screen_planner=planner)
    assert last == "SHOP remove_card_at_shop"
    assert fake.calls == [("remove_card_at_shop", {})]


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
