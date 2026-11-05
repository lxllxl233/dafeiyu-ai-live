"""《我的世界》第四个游戏包的接入验收：不启动游戏、不发真实按键。

重点验证三件事：

1. 仿真世界能完整跑 observe → validate → execute，且验算和真跑同一套规则；
2. 没装真机桥时**绝不退化成窗口/键盘**，`source` 会诚实标成 offline；
3. `mc-bridge/v1` 的 HTTP 客户端和适配器能读同一个协议。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dafeiyu import paths
from dafeiyu.agent.host import AutoplayHost
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


@pytest.fixture(scope="module", autouse=True)
def _load_pack():
    GameRegistry().load_plugin("minecraft")


def _engine():
    return sys.modules["dafeiyu_game_minecraft.engine"]


def _rules():
    return sys.modules["dafeiyu_game_minecraft.rules"]


def _runner():
    plugin = GameRegistry().load_plugin("minecraft")
    plugin.runner()
    return sys.modules["dafeiyu_game_minecraft.runner"]


def _tools(tmp_path, mode: str = "sim"):
    return _engine().MinecraftTools(mode=mode,
                                    sim_path=tmp_path / "world.json")


def _host(tmp_path) -> AutoplayHost:
    host = AutoplayHost("minecraft")
    host.status_path = tmp_path / "autoplay.json"
    host.stop_path = tmp_path / "autoplay.stop"
    host.log_path = tmp_path / "autoplay.jsonl"
    host.reset("minecraft")
    return host


# ---------------------------------------------------------------- 清单/协议
def test_manifest_and_plugin_match_contracts():
    registry = GameRegistry()
    assert catalog.resolve_game_id("mc") == "minecraft"
    assert catalog.resolve_game_id("Minecraft") == "minecraft"
    assert catalog.game_name("mc") == "我的世界"

    pack = registry.get("minecraft")
    assert pack is not None and pack.contract is not None
    assert pack.contract.errors == ()
    caps = pack.contract.capabilities
    assert caps.observe and caps.control and caps.autoplay
    assert caps.modes == ("local", "llm")
    assert pack.manifest["bridge"]["protocol"] == "mc-bridge/v1"
    assert (pack.manifest.get("live") or {}).get("enabled") is True
    assert pack.manifest["launch"]["url"] == "minecraft://"
    assert pack.manifest["start"]["options"][0]["key"] == "difficulty"

    plugin = registry.load_plugin("minecraft")
    assert isinstance(plugin, GamePlugin)
    assert plugin.manifest.entry == "plugin.py:McPlugin"
    assert isinstance(plugin.tools(), GameTools)
    assert isinstance(plugin.adapter(), GameAdapter)
    assert isinstance(plugin.runner(), AutoplayRunner)
    assert "minecraft.action/v1" in plugin.prompt("decision")
    assert plugin.prompt("unknown") == ""


def test_pack_has_no_window_or_keyboard_backend():
    root = paths.GAMES / "minecraft"
    assert (root / "game.json").is_file()
    assert (root / "README.md").is_file()
    assert (root / "game-skill" / "obs.json").is_file()
    assert (root / "knowledge" / "OS.md").is_file()
    assert (root / "persona" / "layer.md").is_file()
    assert not (root / "window.py").exists()
    assert not (root / "keyboard.py").exists()


# ---------------------------------------------------------------- 仿真世界
def test_sim_observe_builds_context(tmp_path):
    tools = _tools(tmp_path)
    observed = tools.observe()
    assert isinstance(observed, ActionResult) and observed.ok is True
    ctx = observed.data["context"]
    assert ctx["source"] == "sim"
    assert ctx["screen"] == "PLAYING"
    assert ctx["day"] == 1
    assert ctx["hp"] == 20 and ctx["max_hp"] == 20
    assert ctx["food"] == 20
    assert ctx["inventory"]["bread"] == 2
    assert ctx["crafting_table"] is False and ctx["furnace"] is False
    names = {block["name"] for block in ctx["nearby"]["blocks"]}
    assert {"oak_log", "cobblestone"} <= names
    assert ctx["phase"] == "day"


def test_plan_roundtrip_builds_pickaxe(tmp_path):
    tools = _tools(tmp_path)
    plan = {"seq": [
        {"type": "mine", "block": "oak_log", "count": 6},
        {"type": "craft", "item": "oak_planks", "count": 3},
        {"type": "craft", "item": "crafting_table", "count": 1},
        {"type": "place", "block": "crafting_table"},
        {"type": "craft", "item": "stick", "count": 2},
        {"type": "craft", "item": "wooden_pickaxe", "count": 1},
        {"type": "equip", "item": "wooden_pickaxe"},
    ]}
    assert tools.validate(plan).ok is True
    result = tools.execute(plan, dry_run=False)
    assert result.ok is True
    ctx = result.data["context"]
    assert ctx["crafting_table"] is True
    assert ctx["held"] == "wooden_pickaxe"
    assert ctx["inventory"]["wooden_pickaxe"] == 1
    assert ctx["stats"]["mined"] == 6
    assert result.data["receipt"].startswith("mc-1-")


def test_validate_rejects_impossible_plans(tmp_path):
    tools = _tools(tmp_path)

    no_table = tools.validate(
        {"seq": [{"type": "craft", "item": "wooden_pickaxe"}]})
    assert no_table.ok is False
    assert no_table.code == ActionCode.INVALID_PLAN
    assert "工作台" in no_table.message

    bare_hand = tools.validate(
        {"seq": [{"type": "mine", "block": "cobblestone", "count": 1}]})
    assert bare_hand.ok is False and "木镐" in bare_hand.message

    sleep = tools.validate({"seq": [{"type": "sleep"}]})
    assert sleep.ok is False and "床" in sleep.message

    unknown = tools.validate({"seq": [{"type": "fly"}]})
    assert unknown.ok is False and "动作" in unknown.message

    full = tools.validate({"seq": [{"type": "eat", "item": "bread"}]})
    assert full.ok is False and "不饿" in full.message

    too_many = tools.validate(
        {"seq": [{"type": "wait", "seconds": 0.2}] * 9})
    assert too_many.ok is False and "8 步" in too_many.message

    empty = tools.validate({"seq": []})
    assert empty.ok is False


def test_validate_is_sequential_and_matches_execution(tmp_path):
    """先砍树再合成木板：验算必须按顺序算材料，不能只看当前背包。"""
    tools = _tools(tmp_path)
    plan = {"seq": [
        {"type": "mine", "block": "oak_log", "count": 1},
        {"type": "craft", "item": "oak_planks", "count": 1},
    ]}
    assert tools.validate(plan).ok is True
    assert tools.execute(plan, dry_run=False).ok is True


def test_dry_run_does_not_touch_the_world(tmp_path):
    tools = _tools(tmp_path)
    before = tools.snapshot()
    dry = tools.execute({"seq": [{"type": "mine", "block": "oak_log",
                                  "count": 3}]}, dry_run=True)
    assert dry.ok is True and dry.data["dry_run"] is True
    assert dry.data["sent"] == []
    after = tools.snapshot()
    assert after["inventory"] == before["inventory"]
    assert after["stats"]["actions"] == before["stats"]["actions"]


def test_invalid_plan_does_not_partially_execute(tmp_path):
    tools = _tools(tmp_path)
    result = tools.execute({"seq": [
        {"type": "mine", "block": "oak_log", "count": 3},
        {"type": "craft", "item": "wooden_pickaxe"},
    ]}, dry_run=False)
    assert result.ok is False and result.code == ActionCode.INVALID_PLAN
    assert tools.snapshot()["inventory"].get("oak_log") in (None, 0)


def test_night_and_death_rules(tmp_path):
    """不吃不喝不搭棚：夜里会被僵尸打倒；有棚就不掉血。"""
    tools = _tools(tmp_path)
    tools.start_run("normal")
    assert tools.snapshot()["screen"] == "PLAYING"

    rules = _rules()
    state = rules.new_world("normal", time_s=rules.DUSK_END + 10)
    state["player"]["hp"] = 3
    rules.tick(state, 60.0)
    assert state["screen"] == "dead"
    assert state["player"]["dead"] is True

    # 有棚就不该在夜里掉血。
    safe = rules.new_world("normal", time_s=rules.DUSK_END + 10)
    safe["player"]["shelter"] = 1.0
    before_hp = safe["player"]["hp"]
    rules.tick(safe, 120.0)
    assert safe["player"]["hp"] == before_hp
    assert safe["screen"] == "playing"


# ---------------------------------------------------------------- 离线/HTTP
class FakeBridge:
    """一个最小的 mc-bridge/v1 HTTP 服务；只验证客户端，不模拟完整规则。"""

    def __init__(self) -> None:
        self.state = {
            "ok": True, "ready": True, "api": 1, "bridge": "mc-bridge/v1",
            "screen": "playing", **json.loads(json.dumps(
                _rules().new_world("normal"))),
        }
        self.actions: list[dict] = []
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                return

            def _send(self, payload: dict, status: int = 200) -> None:
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/state":
                    self._send(bridge.state)
                    return
                self._send({"ok": False, "error": "not found"}, 404)

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/act":
                    self._send({"ok": False, "error": "not found"}, 404)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                bridge.actions.append(payload)
                self._send({"ok": True, "message": "ok"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
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
def fake_bridge():
    bridge = FakeBridge()
    try:
        yield bridge
    finally:
        bridge.close()


def _http_tools(fake_bridge, tmp_path):
    from dafeiyu_game_minecraft.compat.mc import Bridge

    return _engine().MinecraftTools(
        bridge=Bridge(mode="http", base_url=fake_bridge.url, timeout=1.0,
                      sim_path=tmp_path / "unused.json"))


def test_http_bridge_reads_state_and_posts_actions(fake_bridge, tmp_path):
    tools = _http_tools(fake_bridge, tmp_path)
    observed = tools.observe()
    assert observed.ok is True
    ctx = observed.data["context"]
    assert ctx["source"] == "http"
    assert ctx["screen"] == "PLAYING"

    result = tools.execute({"seq": [{"type": "mine", "block": "oak_log",
                                     "count": 2}]}, dry_run=False)
    assert result.ok is True
    assert fake_bridge.actions == [{"type": "mine", "block": "oak_log",
                                    "count": 2}]


def test_http_bridge_failure_is_unavailable_not_keyboard(tmp_path):
    from dafeiyu_game_minecraft.compat.mc import Bridge

    tools = _engine().MinecraftTools(
        bridge=Bridge(mode="http", base_url="http://127.0.0.1:1",
                      timeout=0.4, sim_path=tmp_path / "unused.json"))
    observed = tools.observe()
    assert observed.ok is False
    assert observed.code == ActionCode.UNAVAILABLE
    assert observed.data["context"]["source"] == "offline"

    executed = tools.execute({"seq": [{"type": "mine", "block": "oak_log"}]},
                             dry_run=False)
    assert executed.ok is False
    assert executed.code == ActionCode.UNAVAILABLE


def test_adapter_reads_http_state(fake_bridge, tmp_path):
    adapter = _engine_adapter(_http_tools(fake_bridge, tmp_path))
    assert adapter.available() is True
    state = adapter.snapshot()
    assert state.screen == "PLAYING"
    assert state.floor == 1
    assert state.hp == 20
    assert state.in_combat is False
    assert state.extra["source"] == "http"


def _engine_adapter(tools):
    adapter_cls = type(GameRegistry().create_adapter("minecraft"))
    return adapter_cls(tools=tools)


def test_adapter_offline_is_quiet(monkeypatch, tmp_path):
    from dafeiyu_game_minecraft.compat.mc import Bridge

    monkeypatch.setenv("MINECRAFT_API", "http://127.0.0.1:1")
    tools = _engine().MinecraftTools(
        bridge=Bridge(mode="http", base_url="http://127.0.0.1:1",
                      timeout=0.4, sim_path=tmp_path / "unused.json"))
    adapter = _engine_adapter(tools)
    assert adapter.available() is False
    assert adapter.snapshot().screen == ""
    assert adapter.status()["available"] is False


# ---------------------------------------------------------------- 自动玩
class FakeTools:
    calls = 0

    def __init__(self, *args, **kwargs) -> None:
        self._started = 0
        self._stopped = 0

    def available(self) -> bool:
        return True

    def start_run(self, difficulty: str = "normal") -> dict:
        self._started += 1
        return {"ok": True}

    def stop_control(self) -> dict:
        self._stopped += 1
        return {"ok": True}

    def snapshot(self) -> dict:
        return {
            "game": "minecraft", "source": "sim", "screen": "PLAYING",
            "day": 1, "clock": "07:10", "phase": "day", "hp": 20,
            "max_hp": 20, "food": 18, "shelter": 0.0,
            "inventory": {"oak_log": 1, "bread": 2},
            "crafting_table": False, "furnace": False,
            "nearby": {"blocks": [{"name": "oak_log", "kind": "tree",
                                   "dist": 2.0}], "mobs": []},
            "bridge": {"source": "sim"},
        }

    def validate(self, plan: dict) -> ActionResult:
        return ActionResult.success("validate",
                                    data={"seq": plan.get("seq") or []})

    def execute(self, plan: dict, *, dry_run: bool = True) -> ActionResult:
        FakeTools.calls += 1
        return ActionResult.success(
            "execute", message="执行 1 步：挖到 3×橡木原木",
            data={"context": self.snapshot(), "sent": ["mine:oak_log"]})


def test_local_plan_starts_with_wood_and_survives_night():
    runner = _runner()
    plan = runner._local_plan({
        "screen": "PLAYING", "hp": 20, "food": 20, "shelter": 0.0,
        "phase": "day", "inventory": {"bread": 2},
        "crafting_table": False, "furnace": False,
        "nearby": {"blocks": [{"name": "oak_log", "dist": 2.0}], "mobs": []},
    }, 1)
    assert plan["seq"][0] == {"type": "mine", "block": "oak_log", "count": 3}

    night = runner._local_plan({
        "screen": "PLAYING", "hp": 12, "food": 12, "shelter": 0.3,
        "phase": "night", "inventory": {"cobblestone": 2},
        "crafting_table": True, "furnace": True,
        "nearby": {"blocks": [], "mobs": [
            {"name": "zombie", "hostile": True, "dist": 2.0, "hp": 20}]},
    }, 5)
    assert night["seq"][0] == {"type": "place", "block": "cobblestone"}

    eat = runner._local_plan({
        "screen": "PLAYING", "hp": 20, "food": 4, "shelter": 0.9,
        "phase": "day", "inventory": {"bread": 1},
        "crafting_table": True, "furnace": True,
        "nearby": {"blocks": [], "mobs": []},
    }, 6)
    assert eat["seq"][0] == {"type": "eat", "item": "bread"}


def test_runner_start_status_stop(tmp_path, monkeypatch):
    runner = _runner()
    monkeypatch.setattr(runner, "MinecraftTools", FakeTools)
    FakeTools.calls = 0
    host = _host(tmp_path)
    monkeypatch.setattr(host, "start_recording",
                        lambda note="": {"ok": False, "skipped": True})
    monkeypatch.setattr(host, "stop_recording",
                        lambda result="": {"ok": True, "active": False})
    runner.bind_host(host)

    started = runner.start(mode="local", pace="normal")
    assert started["ok"] is True
    assert started["game"] == "minecraft"
    assert started["difficulty"] in ("peaceful", "normal", "hard")

    deadline = time.time() + 3.0
    latest = runner.status()
    while time.time() < deadline and not latest.get("step"):
        time.sleep(0.05)
        latest = runner.status()
    assert latest["step"] >= 1
    assert latest["screen"] == "PLAYING"
    assert latest["source"] == "sim"

    stopped = runner.stop()
    assert stopped["ok"] is True
    assert runner.status()["running"] is False
    assert (tmp_path / "autoplay.jsonl").exists()
    assert FakeTools.calls >= 1


def test_runner_rejects_llm_without_key(tmp_path, monkeypatch):
    runner = _runner()

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
    assert "Key" in result["message"] or "API" in result["message"]


def test_auto_sim_llm_requires_real_bridge(tmp_path, monkeypatch):
    """auto 掉进仿真时，LLM 不能假装在玩：没拉起真机就停手并说明原因。"""
    runner = _runner()
    monkeypatch.setenv("MINECRAFT_BRIDGE", "auto")
    host = _host(tmp_path)
    runner.bind_host(host)
    tools = runner.MinecraftTools(mode="auto")
    monkeypatch.setattr(tools, "snapshot", lambda: {"source": "sim"})
    monkeypatch.setattr(runner, "_wait_for_bridge", lambda tools, seconds: False)
    monkeypatch.setattr("dafeiyu.games.launch.launch_game",
                        lambda game_id: {"ok": False, "error": "no launcher"})

    assert runner._maybe_launch_real(host, tools, "normal", "llm") is None
    latest = host.status()
    assert latest["running"] is False
    assert "真机" in str(latest.get("last") or latest.get("error") or "")

