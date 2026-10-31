"""《吸血鬼幸存者》第三个游戏包的接入验收：不启动 Steam、不发真实按键。

游戏侧桥用本地 HTTP 假服务器代替；测试只验证 Python 侧的 API 契约，
以及「桥没连上时绝不退化成窗口/键盘」。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dafeiyu import paths
from dafeiyu.games import catalog
from dafeiyu.games.base import GameAdapter
from dafeiyu.games.contracts import (
    ActionCode,
    ActionResult,
    AutoplayRunner,
    GamePlugin,
    GameTools,
)
from dafeiyu.games.registry import GameRegistry


def _engine():
    return sys.modules["dafeiyu_game_vampire_survivors.engine"]


def _vs_module(name: str):
    """游戏包是合成包名，只有在 registry 加载过插件后才在 sys.modules 里。"""
    plugin = GameRegistry().load_plugin("vampire-survivors")
    if name == "runner":
        plugin.runner()
    elif name == "adapter":
        plugin.adapter()
    return sys.modules[f"dafeiyu_game_vampire_survivors.{name}"]


class FakeBridge:
    """一个内存里的 VS 桥：GET /state + POST /act。"""

    def __init__(self) -> None:
        self.state: dict = {
            "ok": True,
            "ready": True,
            "api": 1,
            "state": "playing",
            "automation": False,
            "player": {
                "x": 1.0,
                "y": -0.5,
                "hp": 90,
                "max_hp": 100,
                "level": 7,
                "xp": 12,
                "xp_to_next": 20,
                "minute": 3,
                "seconds": 181.2,
                "character": "ANTONIO",
                "weapons": [{"id": "WHIP", "level": 3}],
                "passives": [{"id": "SPINACH", "level": 2}],
                "stage": "Mad Forest",
                "kills": 123,
                "gold": 45,
            },
            "enemies": [
                {"x": 0.5, "y": 1.0, "hp": 20, "type": "BAT", "boss": False},
            ],
            "gems": [{"x": 0.2, "y": 0.3, "value": 1}],
            "pickups": [],
            "screen": {"half_w": 8.0, "half_h": 4.5},
            "menu": None,
            "run": {"active": True, "started": True},
        }
        self.actions: list[dict] = []
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # 安静
                return

            def _send(self, payload: dict) -> None:
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/state":
                    self._send(bridge.state)
                    return
                self._send({"ok": False, "error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/act":
                    self._send({"ok": False, "error": "not found"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                bridge.actions.append(body)
                kind = body.get("type")
                if kind == "move":
                    bridge.state["last_move"] = body
                elif kind == "choose":
                    bridge.state["menu"] = None
                elif kind == "control":
                    bridge.state["automation"] = bool(body.get("automation"))
                elif kind == "start":
                    bridge.state["automation"] = True
                    bridge.state["state"] = "playing"
                elif kind == "stop":
                    bridge.state["automation"] = False
                self._send({"ok": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.bridge = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)


@pytest.fixture
def bridge():
    fake = FakeBridge()
    try:
        yield fake
    finally:
        fake.close()


@pytest.fixture(scope="module", autouse=True)
def _load_pack():
    GameRegistry().load_plugin("vampire-survivors")


def _vs_client(bridge):
    from dafeiyu_game_vampire_survivors.compat.vs import VsClient

    return VsClient(base_url=bridge.url, timeout=1.0)


def test_manifest_and_plugin_match_contracts():
    registry = GameRegistry()
    assert catalog.resolve_game_id("vs") == "vampire-survivors"
    assert catalog.resolve_game_id("vampire") == "vampire-survivors"
    assert catalog.game_name("vs") == "吸血鬼幸存者"

    pack = registry.get("vampire-survivors")
    assert pack is not None and pack.contract is not None
    assert pack.contract.errors == ()
    caps = pack.contract.capabilities
    assert caps.observe and caps.control and caps.autoplay
    assert caps.modes == ("local", "llm")
    assert (pack.manifest.get("live") or {}).get("enabled") is True
    assert pack.manifest["launch"]["app_id"] == "1794680"

    plugin = registry.load_plugin("vampire-survivors")
    assert isinstance(plugin, GamePlugin)
    assert isinstance(plugin.tools(), GameTools)
    assert isinstance(plugin.adapter(), GameAdapter)
    assert isinstance(plugin.runner(), AutoplayRunner)
    assert plugin.prompt("decision")
    assert plugin.prompt("unknown") == ""


def test_api_client_reads_state_and_posts_actions(bridge):
    client = _vs_client(bridge)
    state = client.state()
    assert state["ready"] is True
    assert state["player"]["level"] == 7
    assert client.available() is True

    client.move(1.0, 0.0, seconds=0.2, choice="right")
    client.choose(2)
    client.control(True)
    client.start()
    assert bridge.actions[0]["type"] == "move"
    assert bridge.actions[0]["dx"] == 1.0
    assert bridge.actions[1] == {"type": "choose", "index": 2}
    assert bridge.state["automation"] is True


def test_observe_builds_context_from_api(bridge):
    engine = _engine()
    tools = engine.VampireSurvivorsTools(client=_vs_client(bridge))
    observed = tools.observe()
    assert isinstance(observed, ActionResult) and observed.ok is True
    ctx = observed.data["context"]
    assert ctx["source"] == "api"
    assert ctx["screen"] == "PLAYING"
    assert ctx["level"] == 7
    assert ctx["kills"] == 123
    assert ctx["hp"] == 90
    assert ctx["weapons"] == ["WHIP L3"]
    assert ctx["enemies"][0]["type"] == "BAT"
    assert ctx["gems"][0]["x"] == 0.2
    assert ctx["position"] == {"x": 1.0, "y": -0.5}


def test_validate_rejects_bad_plans(bridge):
    engine = _engine()
    tools = engine.VampireSurvivorsTools(client=_vs_client(bridge))

    assert tools.validate({"seq": [{"move": "left", "seconds": 0.5}]}).ok is True
    assert tools.validate({"seq": [{"press": "3"}]}).ok is True

    bad_dir = tools.validate({"seq": [{"move": "jump"}]})
    assert bad_dir.ok is False and bad_dir.code == ActionCode.INVALID_PLAN
    bad_key = tools.validate({"seq": [{"press": "q"}]})
    assert bad_key.ok is False and "按键" in bad_key.message
    too_long = tools.validate({"seq": [{"move": "up", "seconds": 3.0}] * 8})
    assert too_long.ok is False and "总时长" in too_long.message
    empty = tools.validate({"seq": []})
    assert empty.ok is False


def test_execute_dry_run_never_calls_api(bridge):
    engine = _engine()
    tools = engine.VampireSurvivorsTools(client=_vs_client(bridge))
    result = tools.execute({"seq": [{"move": "right", "seconds": 0.05}]},
                           dry_run=True)
    assert result.ok is True
    assert result.data["dry_run"] is True
    assert bridge.actions == []


def test_execute_sends_move_and_choose(bridge):
    engine = _engine()
    tools = engine.VampireSurvivorsTools(client=_vs_client(bridge))
    result = tools.execute(
        {"seq": [{"move": "right", "seconds": 0.05}, {"press": "2"}]},
        dry_run=False,
    )
    assert result.ok is True
    assert result.data["sent"] == ["move:right:0.05", "press:2"]
    assert [a["type"] for a in bridge.actions] == ["move", "choose"]
    assert bridge.actions[0]["dx"] == 1.0
    assert bridge.actions[1]["index"] == 1


def test_execute_offline_never_falls_back_to_keyboard():
    engine = _engine()
    from dafeiyu_game_vampire_survivors.compat.vs import VsClient

    client = VsClient(base_url="http://127.0.0.1:1", timeout=0.3)
    tools = engine.VampireSurvivorsTools(client=client)
    result = tools.execute({"seq": [{"move": "right", "seconds": 0.05}]},
                           dry_run=False)
    assert result.ok is False and result.code == ActionCode.UNAVAILABLE
    assert "API" in result.message
    ctx = result.data["context"]
    assert ctx["source"] == "offline"
    assert ctx["api"]["ok"] is False


def test_adapter_reads_api_state(bridge):
    engine = _engine()
    adapter_cls = type(GameRegistry().create_adapter("vampire-survivors"))
    adapter = adapter_cls(tools=engine.VampireSurvivorsTools(
        client=_vs_client(bridge)))
    assert isinstance(adapter, GameAdapter)
    assert adapter.available() is True
    state = adapter.snapshot()
    assert state.screen == "PLAYING"
    assert state.floor == 7
    assert state.hp == 90
    assert state.extra["kills"] == 123
    assert state.extra["source"] == "api"


class FakeTools:
    calls = 0

    def __init__(self, *args, **kwargs) -> None:
        self._started = False
        self._stopped = False

    def available(self) -> bool:
        return True

    def start_run(self) -> dict:
        self._started = True
        return {"ok": True}

    def stop_control(self) -> dict:
        self._stopped = True
        return {"ok": True}

    def snapshot(self) -> dict:
        return {
            "screen": "PLAYING", "level": 4, "kills": 66, "hp": 100,
            "max_hp": 100, "stage": "", "time_s": 1.0, "source": "api",
            "api": {"ok": True},
        }

    def validate(self, plan: dict) -> ActionResult:
        return ActionResult.success("validate", data={"seq": plan.get("seq") or []})

    def execute(self, plan: dict, *, dry_run: bool = True) -> ActionResult:
        FakeTools.calls += 1
        return ActionResult.success(
            "execute", message="已发送 1 步",
            data={"context": self.snapshot(), "sent": ["move:right:0.9"]},
        )


def _host(tmp_path):
    from dafeiyu.agent.host import AutoplayHost

    host = AutoplayHost("vampire-survivors")
    host.status_path = tmp_path / "autoplay.json"
    host.stop_path = tmp_path / "autoplay.stop"
    host.log_path = tmp_path / "autoplay.jsonl"
    host.reset("vampire-survivors")
    return host


def test_runner_start_status_stop(tmp_path, monkeypatch):
    runner = _vs_module("runner")

    monkeypatch.setattr(runner, "VampireSurvivorsTools", FakeTools)
    FakeTools.calls = 0
    host = _host(tmp_path)
    # 录像本身由 recording 层单测覆盖；这里只验证 runner 的观察/执行/停手。
    monkeypatch.setattr(host, "start_recording",
                        lambda note="": {"ok": False, "skipped": True})
    monkeypatch.setattr(host, "stop_recording",
                        lambda result="": {"ok": True, "active": False})
    runner.bind_host(host)

    started = runner.start(mode="local", pace="normal")
    assert started["ok"] is True
    assert started["game"] == "vampire-survivors"

    deadline = time.time() + 3.0
    latest = runner.status()
    while time.time() < deadline and not latest.get("step"):
        time.sleep(0.05)
        latest = runner.status()
    assert latest["step"] >= 1
    assert latest["screen"] == "PLAYING"

    stopped = runner.stop()
    assert stopped["ok"] is True
    assert runner.status()["running"] is False
    assert (tmp_path / "autoplay.jsonl").exists()
    assert FakeTools.calls >= 1


def test_runner_rejects_llm_without_key(tmp_path, monkeypatch):
    runner = _vs_module("runner")

    class Cfg:
        llm_api_key = ""
        llm_model = "deepseek-chat"
        autoplay_llm_model = "deepseek-chat"

    monkeypatch.setattr(runner, "LiveConfig", type(
        "FakeLiveConfig", (), {"load": staticmethod(lambda: Cfg())}))
    host = _host(tmp_path)
    runner.bind_host(host)
    result = runner.start(mode="llm", pace="normal")
    assert result["ok"] is False
    assert "API Key" in result["message"] or "Key" in result["message"]


def test_local_plan_collects_gems_and_retreats():
    runner = _vs_module("runner")
    pickup = runner._local_plan(
        {"screen": "PLAYING", "hp": 100, "max_hp": 100,
         "enemies": [], "gems": [{"x": 1.0, "y": 0.0}]}, 0)
    assert pickup["seq"][0] == {"move": "right", "seconds": 0.9}

    escape = runner._local_plan(
        {"screen": "PLAYING", "hp": 10, "max_hp": 100,
         "enemies": [{"x": -1.0, "y": 0.0}], "gems": []}, 0)
    assert escape["seq"][0]["move"] == "right"


def test_no_window_keyboard_module():
    assert not (paths.GAMES / "vampire-survivors" / "window.py").exists()


def test_play_alias_resolves_to_canonical_game_id():
    from dafeiyu.agent import play

    assert play._game_id("vs") == "vampire-survivors"
    assert play._game_id("vampire") == "vampire-survivors"


def test_game_pack_listed_under_games():
    assert (paths.GAMES / "vampire-survivors" / "game.json").is_file()
    assert (paths.GAMES / "vampire-survivors" / "README.md").is_file()
    assert (paths.GAMES / "vampire-survivors" / "compat" / "vs.py").is_file()
