"""LLM 直播导演：模块发现、决定解析、自动切换和停止。"""

from __future__ import annotations

import json
import time

from dafeiyu.agent import director, play
from dafeiyu.games.base import GameInfo
from dafeiyu.games.contracts import GameCapabilities, GameManifest


class FakePack:
    def __init__(self, game_id: str, name: str, *, live=None,
                 autoplay: bool = True, modes=("local", "llm"), tags=()) -> None:
        self.info = GameInfo(id=game_id, name=name, status="ready",
                             tags=tuple(tags), notes="")
        self.manifest = {"live": live} if live else {}
        self.contract = GameManifest(
            schema=2, id=game_id, version="1.0.0", name=name,
            entry="plugin.py:P", adapter="",
            capabilities=GameCapabilities(observe=True, control=True,
                                          autoplay=autoplay, modes=tuple(modes)),
        )


class FakeRegistry:
    def __init__(self, *packs) -> None:
        self._packs = list(packs)

    def packs(self):
        return list(self._packs)


def _registry() -> FakeRegistry:
    return FakeRegistry(
        FakePack("alpha", "甲频道", live={"enabled": True, "label": "甲",
                                         "role": "慢节奏"}, tags=("t1",)),
        FakePack("beta", "乙频道", live={"enabled": True, "label": "乙",
                                        "role": "快节奏", "weight": 2}),
        FakePack("gamma", "丙频道"),                       # 没声明 live
        FakePack("broken", "丁频道", live={"enabled": True},
                 autoplay=False),                           # 不能自动玩
    )


def test_modules_only_live_and_autoplay_capable():
    items = director.modules(_registry())
    assert [item.id for item in items] == ["alpha", "beta"]
    assert items[0].label == "甲"
    assert items[1].weight == 2
    assert items[0].modes == ("local", "llm")


def test_real_registry_lists_three_modules_and_skips_acceptance_toy():
    ids = {item.id for item in director.modules()}
    assert {"slay-the-spire-2", "bilibili-hot-commentary",
            "vampire-survivors"} <= ids
    assert "toy-duel" not in ids


def test_build_prompt_lists_labels_and_protocol():
    text = director.build_prompt(director.modules(_registry()))
    assert "甲" in text and "乙" in text
    assert '"module"' in text
    assert str(director.MIN_SEGMENT_MIN) in text


def test_parse_decision_accepts_id_or_label_and_clamps_minutes():
    items = director.modules(_registry())
    chosen = director.parse_decision(
        {"module": "alpha", "minutes": 999, "say": " 换个口味 ", "reason": "r"},
        items)
    assert chosen["module"].id == "alpha"
    assert chosen["minutes"] == director.MAX_SEGMENT_MIN
    assert chosen["say"] == "换个口味"

    by_label = director.parse_decision({"module": "乙", "minutes": 1}, items)
    assert by_label["module"].id == "beta"
    assert by_label["minutes"] == director.MIN_SEGMENT_MIN

    assert director.parse_decision({"module": "nope"}, items) is None
    assert director.parse_decision({"minutes": 10}, items) is None
    assert director.parse_decision("not-a-dict", items) is None


class ScriptedPlanner:
    def __init__(self, ids) -> None:
        self.ids = list(ids)
        self.n = 0
        self.contexts = []

    def available(self):
        return True, ""

    def plan(self, context, feedback="", goal=""):
        self.contexts.append(dict(context))
        game_id = self.ids[self.n % len(self.ids)]
        self.n += 1
        return {
            "ok": True,
            "plan": {"module": game_id, "minutes": 5, "say": "",
                     "reason": "脚本选择"},
            "usage": {"input": 10, "output": 5, "total": 15},
        }


class BadPlanner:
    def available(self):
        return True, ""

    def plan(self, context, feedback="", goal=""):
        return {"ok": False, "error": "timeout"}


class StubUsage:
    def __init__(self) -> None:
        self.rows: list[tuple[str, int]] = []

    def add_usage(self, usage=None, run_id="", note="", step=None):
        self.rows.append((str(note), int((usage or {}).get("total") or 0)))
        return usage or {"input": 0, "output": 0, "total": 0}

    def log(self, *args, **kwargs) -> None:
        pass


class FakeConfig:
    saved: list[dict] = []
    llm_api_key = "test-key"

    @classmethod
    def load(cls):
        return cls()

    def save_patch(self, patch: dict):
        type(self).saved.append(dict(patch))
        return self


def _director(tmp_path, planner_factory=None) -> director.LiveDirector:
    return director.LiveDirector(
        planner_factory=planner_factory,
        registry=_registry(),
        status_path=tmp_path / "live_director.json",
        stop_path=tmp_path / "live_director.stop",
        global_stop_path=tmp_path / "autoplay.stop",
        sleep=lambda seconds: time.sleep(min(0.02, max(0.0, float(seconds)))),
    )


def test_fallback_rotates_when_model_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(director, "_UsageHost", StubUsage)
    d = _director(tmp_path, planner_factory=lambda items: BadPlanner())

    first = d._decide(d.modules(), "")
    assert first["fallback"] is True
    assert first["module"].id == "alpha"

    d._last["history"] = [{"module": "alpha"}]
    second = d._decide(d.modules(), "")
    assert second["module"].id == "beta"


def test_start_switches_modules_then_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(director, "LiveConfig", FakeConfig)
    usage = StubUsage()
    monkeypatch.setattr(director, "_UsageHost", lambda: usage)
    FakeConfig.saved = []

    started: list[tuple[str, str]] = []
    stops: list[int] = []

    def fake_start(*, game=None, mode=None, pace=None):
        started.append((str(game), str(mode)))
        return {"ok": True, "game": game, "message": "已开始"}

    def fake_stop():
        stops.append(1)
        return {"ok": True, "message": "已停手"}

    monkeypatch.setattr(play, "start_autoplay", fake_start)
    monkeypatch.setattr(play, "stop_autoplay", fake_stop)
    monkeypatch.setattr(play, "status", lambda: {"running": True})
    monkeypatch.setattr(
        director.LiveDirector, "_wait_segment",
        lambda self, deadline, module_id: time.sleep(0.03))

    planner = ScriptedPlanner(["alpha", "beta", "alpha"])
    d = _director(tmp_path, planner_factory=lambda items: planner)
    result = d.start()
    assert result["ok"] is True

    deadline = time.time() + 3.0
    while time.time() < deadline and d.status().get("segments", 0) < 2:
        time.sleep(0.02)
    assert d.status().get("segments", 0) >= 2

    stopped = d.stop()
    assert stopped["ok"] is True
    state = d.status()
    assert state["running"] is False

    assert started[0] == ("alpha", "llm")
    assert [game for game, _mode in started[:2]] == ["alpha", "beta"]
    assert stops  # 每次切换前都停上一个模块
    assert {"game": "alpha"} in FakeConfig.saved
    assert {"game": "beta"} in FakeConfig.saved
    assert usage.rows and usage.rows[0][0] == "直播导演 选择模块"
    assert usage.rows[0][1] == 15
    assert state["tokens"] >= 15
    assert state["history"][:2][0]["module"] == "alpha"


def test_start_requires_api_key(tmp_path, monkeypatch):
    class NoKeyConfig:
        llm_api_key = ""

        @classmethod
        def load(cls):
            return cls()

    monkeypatch.setattr(director, "LiveConfig", NoKeyConfig)
    d = _director(tmp_path)
    result = d.start()
    assert result["ok"] is False
    assert "Key" in result["message"] or "API" in result["message"]


def test_global_stop_file_is_honored(tmp_path):
    d = _director(tmp_path)
    assert d._stopping() is False
    (tmp_path / "autoplay.stop").write_text("1", encoding="utf-8")
    assert d._stopping() is True


def test_status_marks_dead_pid_as_stopped(tmp_path):
    status_path = tmp_path / "live_director.json"
    status_path.write_text(json.dumps({
        "running": True, "pid": 2_000_000_000, "phase": "playing",
        "module": "alpha",
    }), encoding="utf-8")
    d = director.LiveDirector(
        registry=_registry(), status_path=status_path,
        stop_path=tmp_path / "live_director.stop",
        global_stop_path=tmp_path / "autoplay.stop")
    state = d.status()
    assert state["running"] is False
    assert state["alive"] is False
