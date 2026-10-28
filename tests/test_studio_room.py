"""控制台直播间面板 + API Token 账单（离线，不连 B 站）。"""
from __future__ import annotations

import json
import threading
import types

import pytest

import dafeiyu.live.bilibili as bili
from dafeiyu import paths
from dafeiyu.agent.host import AutoplayHost
from dafeiyu.live.bilibili import RoomEvent, RoomLog
from dafeiyu.ui.studio import StudioRuntime


# ---------------------------------------------------------------- Token 账单
def _host(tmp_path, monkeypatch) -> AutoplayHost:
    host = AutoplayHost("toy-duel")
    monkeypatch.setattr(host, "log_path", tmp_path / "autoplay.jsonl")
    return host


def test_token_usage_totals_steps_and_kinds(tmp_path, monkeypatch):
    host = _host(tmp_path, monkeypatch)
    host.add_usage({"input": 100, "output": 50, "total": 150}, "r1",
                   note="战斗 f1 t1", step=3)
    host.add_usage({"input": 200, "output": 80, "total": 280}, "r2",
                   note="地图 choose_map_node 0", step=4)
    host.add_usage({"input": 10, "output": 5, "total": 15}, "r2",
                   note="事件 choose_event_option 1", step=5)

    usage = host.token_usage(limit=2)
    assert usage["total"] == 445
    assert usage["input"] == 310 and usage["output"] == 135
    assert usage["calls"] == 3 and usage["last"] == 15
    assert [s["note"] for s in usage["steps"]] == [
        "地图 choose_map_node 0", "事件 choose_event_option 1"]
    assert usage["steps"][-1]["cumulative"] == 445
    assert {k["kind"] for k in usage["kinds"]} == {"战斗", "地图", "事件"}
    assert usage["by_run"][0]["run_id"] == "r2"     # 最近的排在前面
    assert usage["path"].endswith("autoplay.jsonl")


def test_token_usage_refreshes_when_log_grows(tmp_path, monkeypatch):
    host = _host(tmp_path, monkeypatch)
    host.add_usage({"input": 10, "output": 5, "total": 15}, "r1", note="战斗 f1 t1")
    assert host.token_usage()["total"] == 15
    host.add_usage({"input": 20, "output": 5, "total": 25}, "r1", note="战斗 f1 t2")
    assert host.token_usage()["total"] == 40
    assert len(host.token_usage()["steps"]) == 2


def test_token_usage_ignores_zero_and_bad_rows(tmp_path, monkeypatch):
    host = _host(tmp_path, monkeypatch)
    host.log_path.write_text(
        '{"msg":"llm_usage","total":0,"input":0,"output":0}\n'
        'not json\n'
        '{"msg":"llm_usage","total":12,"input":7,"output":5,"note":"地图 x"}\n'
        '{"msg":"step"}\n', encoding="utf-8")
    usage = host.token_usage()
    assert usage["total"] == 12 and usage["calls"] == 1
    assert usage["steps"][0]["kind"] == "地图"


# ---------------------------------------------------------------- 直播间面板
def _runtime(tmp_path, monkeypatch, *, room=9527,
             cookie="SESSDATA=s; bili_jct=jct", enabled=True) -> StudioRuntime:
    monkeypatch.setattr(paths, "LOGS", tmp_path)
    rt = StudioRuntime.__new__(StudioRuntime)
    rt.cfg = types.SimpleNamespace(bilibili_room_id=room, bilibili_cookie=cookie,
                                   bilibili_enabled=enabled)
    rt._lock = threading.Lock()
    rt._room_cache = ((0, 0), [])
    rt._bili_sent = []
    rt._sync_config = lambda: None          # 不读真实 config.json
    return rt


def test_room_status_reads_only_last_120(tmp_path, monkeypatch):
    rt = _runtime(tmp_path, monkeypatch)
    log = RoomLog(tmp_path / "bilibili_events.jsonl")
    for i in range(130):
        log.append(RoomEvent(room_id=9527, kind="chat", username=f"u{i}", text=f"m{i}"))
    (tmp_path / "bilibili.json").write_text(
        json.dumps({"connected": True, "gifts": 2, "counts": {"chat": 130}}),
        encoding="utf-8")

    status = rt._bilibili_status()
    assert status["connected"] is True
    assert len(status["events"]) == 120
    assert status["events"][-1]["text"] == "m129"
    assert status["room_id"] == 9527 and status["max"] == 120
    assert status["can_send"] is True and status["cookie_set"] is True


def test_room_status_flags_missing_cookie(tmp_path, monkeypatch):
    rt = _runtime(tmp_path, monkeypatch, cookie="")
    status = rt._bilibili_status()
    assert status["can_send"] is False
    assert status["cookie_set"] is False


def test_room_status_merges_local_send_and_caps(tmp_path, monkeypatch):
    rt = _runtime(tmp_path, monkeypatch)
    log = RoomLog(tmp_path / "bilibili_events.jsonl")
    for i in range(120):
        log.append(RoomEvent(room_id=9527, kind="like", text=f"赞{i}"))
    rt._bili_sent = [{"at": 1.0 + i, "kind": "self", "text": f"我说{i}"} for i in range(5)]

    events = rt._bilibili_status()["events"]
    assert len(events) == 120                       # 只保留最近 120 条
    assert events[-1]["kind"] == "self" and events[-1]["text"] == "我说4"
    assert events[0]["text"] == "赞5"               # 最老的 5 条被挤掉


def test_send_to_room_uses_config_and_tracks_event(tmp_path, monkeypatch):
    rt = _runtime(tmp_path, monkeypatch)
    seen = {}

    def fake_send(room, text, cookie):
        seen.update(room=room, text=text, cookie=cookie)
        return {"ok": True, "text": text, "room_id": room}

    monkeypatch.setattr(bili, "send_danmu", fake_send)
    res = rt.send_to_room("  这手不亏  ")
    assert res["ok"] and res["text"] == "这手不亏"
    assert res["event"]["kind"] == "self" and res["event"]["local"] is True
    assert seen["room"] == 9527 and seen["cookie"].endswith("bili_jct=jct")
    assert rt._bili_sent[-1]["text"] == "这手不亏"


def test_send_to_room_without_room_reports_it(tmp_path, monkeypatch):
    rt = _runtime(tmp_path, monkeypatch, room=0)
    with pytest.raises(ValueError, match="直播间号"):
        rt.send_to_room("hi")


def test_bili_send_endpoint_contract():
    """控制台 JS 和 server 的约定：POST /api/bili/send {text} -> {ok, text, event}。"""
    import inspect

    from dafeiyu.ui import overlay_server

    src = inspect.getsource(overlay_server)
    assert '"/api/bili/send"' in src
    assert "send_to_room" in src
