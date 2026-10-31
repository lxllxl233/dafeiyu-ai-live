"""控制台直播间面板 + API Token 账单（离线，不连 B 站）。"""
from __future__ import annotations

import json
import threading
import types

import pytest

import dafeiyu.live.bilibili as bili
from dafeiyu import paths
from dafeiyu.agent.host import AutoplayHost
from dafeiyu.core.usage import merge_usage
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


def test_merge_usage_adds_kook_chat_source():
    play = {
        "total": 150, "input": 100, "output": 50, "calls": 1, "last": 150,
        "steps": [{"at": 1.0, "note": "战斗 f1 t1", "kind": "战斗",
                   "input": 100, "output": 50, "total": 150, "ms": 10}],
        "kinds": [{"kind": "战斗", "total": 150, "calls": 1}],
        "by_run": [{"run_id": "run-a", "total": 150, "calls": 1, "last_at": 1.0}],
        "path": "logs/autoplay.jsonl", "paths": ["logs/autoplay.jsonl"],
    }
    chat = {
        "total": 60, "input": 50, "output": 10, "calls": 2, "last": 30,
        "steps": [
            {"at": 2.0, "note": "KOOK 聊天", "kind": "KOOK 聊天",
             "input": 25, "output": 5, "total": 30, "ms": 5},
            {"at": 3.0, "note": "KOOK 记忆抽取", "kind": "KOOK 记忆抽取",
             "input": 25, "output": 5, "total": 30, "ms": 6},
        ],
        "kinds": [{"kind": "KOOK 聊天", "total": 30, "calls": 1},
                  {"kind": "KOOK 记忆抽取", "total": 30, "calls": 1}],
        "label": "KOOK 聊天", "path": "logs/kook/usage.jsonl",
        "paths": ["logs/kook/usage.jsonl"],
    }

    merged = merge_usage(play, chat, limit=10)
    assert merged["total"] == 210 and merged["calls"] == 3
    assert merged["last"] == 30
    assert [step["note"] for step in merged["steps"]] == [
        "战斗 f1 t1", "KOOK 聊天", "KOOK 记忆抽取"]
    assert merged["steps"][-1]["cumulative"] == 210
    assert {source["label"] for source in merged["sources"]} == {"自动玩", "KOOK 聊天"}
    assert merged["chat"]["total"] == 60
    assert merged["paths"] == ["logs/autoplay.jsonl", "logs/kook/usage.jsonl"]


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


def test_bili_comment_settings_read_and_save():
    rt = StudioRuntime.__new__(StudioRuntime)
    rt.cfg = types.SimpleNamespace(
        game="bilibili-hot-commentary",
        bilibili_cookie="SESSDATA=s; bili_jct=jct",
        extra={},
    )
    rt.cfg.save_patch = lambda patch: None
    rt._sync_config = lambda: None
    rt._config_mtime = lambda: 0
    current = types.SimpleNamespace(
        video_comment_enabled=True, video_comment_max_chars=60,
        video_comment_max_per_day=0, video_comment_min_gap_s=600.0)
    module = types.SimpleNamespace(load_commentary_config=lambda: current)
    rt.registry = types.SimpleNamespace(
        load_game_module=lambda game, name: module)

    settings = rt.bilibili_comment_settings()
    assert settings["ok"] is True
    assert settings["unlimited"] is True
    assert settings["can_post"] is True

    rt.save_bilibili_comment_settings({
        "enabled": False, "max_chars": 50,
        "max_per_day": 0, "min_gap_s": 0,
    })
    nested = rt.cfg.extra["bilibili_commentary"]
    assert nested["video_comment_enabled"] is False
    assert nested["video_comment_max_chars"] == 50
    assert nested["video_comment_max_per_day"] == 0
    assert nested["video_comment_min_gap_s"] == 0.0


# ---------------------------------------------------------------- 开局设置
_START_SPEC = {
    "title": "开局设置",
    "note": "下一局生效",
    "options": [
        {"key": "character", "label": "角色", "type": "choice", "default": "IRONCLAD",
         "choices": [{"value": "IRONCLAD", "label": "铁甲战士"},
                     {"value": "SILENT", "label": "静默猎手"}]},
        {"key": "ascension", "label": "进阶难度", "type": "int",
         "default": 10, "min": 0, "max": 20},
    ],
}


def _start_rt(game="slay-the-spire-2", stored=None, spec=_START_SPEC):
    rt = StudioRuntime.__new__(StudioRuntime)
    saved = {}
    rt.cfg = types.SimpleNamespace(
        game=game, start_options=dict(stored or {}),
        save_patch=lambda patch: saved.update(patch))
    rt._sync_config = lambda: None
    rt._games = lambda force=False: [{
        "id": game, "name": "杀戮尖塔 2",
        "manifest": {"start": spec} if spec else {}}]
    return rt, saved


def test_game_start_reads_declared_spec_and_stored_values():
    rt, _ = _start_rt(stored={"slay-the-spire-2": {
        "character": "SILENT", "ascension": 4}})
    got = rt.game_start()
    assert got["ok"] is True and got["game"] == "slay-the-spire-2"
    assert [opt["key"] for opt in got["spec"]["options"]] == ["character", "ascension"]
    assert got["values"] == {"character": "SILENT", "ascension": 4}


def test_game_start_without_declaration_is_empty():
    rt, _ = _start_rt(game="toy-duel", spec=None)
    assert rt.game_start() == {"ok": True, "game": "toy-duel",
                               "spec": {}, "values": {}}


def test_save_game_start_cleans_values_and_persists():
    rt, saved = _start_rt()
    got = rt.save_game_start({"values": {"character": "SILENT", "ascension": 99}})
    assert got["values"] == {"character": "SILENT", "ascension": 20}
    assert saved["start_options"]["slay-the-spire-2"] == got["values"]

    got = rt.save_game_start({"values": {"character": "没人", "ascension": -3}})
    assert got["values"] == {"character": "IRONCLAD", "ascension": 0}


def test_save_game_start_requires_declared_options():
    rt, _ = _start_rt(game="toy-duel", spec=None)
    with pytest.raises(ValueError, match="开局设置"):
        rt.save_game_start({"values": {"character": "X"}})


def test_bili_posted_comments_are_read_from_engine_log():
    """已发评论（链接/标题/内容）由引擎账本提供，控制台只读；非 B 模式给空。"""
    rt = StudioRuntime.__new__(StudioRuntime)
    rt.cfg = types.SimpleNamespace(game="bilibili-hot-commentary")
    rt._sync_config = lambda: None
    seen = {}

    def fake_read(limit=20):
        seen["limit"] = limit
        return {"ok": True, "count": 1, "items": [{
            "at": 2.0, "bvid": "BV1POST", "title": "标题",
            "url": "https://www.bilibili.com/video/BV1POST",
            "owner": "UP", "text": "评论内容", "rpid": "9"}]}

    module = types.SimpleNamespace(read_video_comments=fake_read)
    rt.registry = types.SimpleNamespace(
        load_game_module=lambda game, name: module)

    got = rt.bilibili_video_comments()
    assert got["ok"] is True and got["count"] == 1
    assert got["items"][0]["text"] == "评论内容"
    assert seen["limit"] == 20

    rt.cfg.game = "slay-the-spire-2"
    assert rt.bilibili_video_comments() == {"ok": True, "items": [], "count": 0}


def test_bili_comment_endpoint_exposes_posted_log():
    import inspect

    from dafeiyu.ui import overlay_server

    src = inspect.getsource(overlay_server)
    assert '"/api/bilibili/comment"' in src
    assert 'payload["posted"] = runtime.bilibili_video_comments()' in src


def test_bili_send_endpoint_contract():
    """控制台 JS 和 server 的约定：POST /api/bili/send {text} -> {ok, text, event}。"""
    import inspect

    from dafeiyu.ui import overlay_server

    src = inspect.getsource(overlay_server)
    assert '"/api/bili/send"' in src
    assert "send_to_room" in src


def test_recording_endpoint_contract():
    """录像策略/开关：GET 读状态，POST 走 recording_action。"""
    import inspect

    from dafeiyu.ui import overlay_server
    from dafeiyu.ui import studio

    src = inspect.getsource(overlay_server)
    assert '"/api/recording"' in src
    assert "recording_action" in src
    assert "recording_payload" in inspect.getsource(studio)
    assert "recording_enabled" in inspect.getsource(studio)
