# -*- coding: utf-8 -*-
"""B 站摸鱼训练：记忆存储、人格注入和一条视频的完整落库。"""
from __future__ import annotations

import json

from dafeiyu.core.config import LiveConfig
from dafeiyu.games.registry import GameRegistry
from dafeiyu.persona.layers import PersonaStack
from dafeiyu.persona.training import TrainingMemoryStore, prompt_text

_PACK = "bilibili-hot-commentary"
_registry = GameRegistry()


def _load(name: str):
    module = _registry.load_game_module(_PACK, name)
    assert module is not None, _registry.last_plugin_error
    return module


def _entry(**over):
    row = {
        "bvid": "BV1test",
        "title": "一条值得看的视频",
        "summary": "我看完记住了一个观点：先把问题拆小，再慢慢解决，比一上来硬扛有用。",
        "comment": "先把问题拆小，再慢慢解决。",
        "comment_status": "posted",
        "enabled": True,
    }
    row.update(over)
    return row


def test_training_store_roundtrip_and_prompt(tmp_path):
    store = TrainingMemoryStore(tmp_path / "training.json")
    row = store.add(_entry())
    assert row["id"]
    loaded = TrainingMemoryStore(tmp_path / "training.json").to_dict()
    assert loaded["count"] == 1
    text = prompt_text(tmp_path / "training.json")
    assert "【B站摸鱼训练记忆】" in text
    assert "一条值得看的视频" in text
    assert "先把问题拆小" in text

    store.update(row["id"], enabled=False)
    assert prompt_text(tmp_path / "training.json") == ""
    assert store.to_dict()["enabled"] == 0
    assert store.delete(row["id"])
    assert store.to_dict()["count"] == 0


def test_training_store_rejects_empty_summary_and_duplicate_ids(tmp_path):
    store = TrainingMemoryStore(tmp_path / "training.json")
    try:
        store.add(_entry(summary=""))
    except ValueError as exc:
        assert "summary" in str(exc)
    else:
        raise AssertionError("空 summary 应该被拒绝")

    store.add(_entry(id="same"))
    try:
        store.save([_entry(id="same"), _entry(id="same")])
    except ValueError as exc:
        assert "重复" in str(exc)
    else:
        raise AssertionError("重复 id 应该被拒绝")


def test_training_layer_is_optional_and_full(tmp_path):
    (tmp_path / "dafeiyu").mkdir()
    (tmp_path / "dafeiyu" / "card.json").write_text("{}", encoding="utf-8")
    store = TrainingMemoryStore(tmp_path / "training.json")
    store.add(_entry(summary="A" * 40, title="第一条"))
    store.add(_entry(bvid="BV2", summary="B" * 40, title="第二条"))

    off = PersonaStack.load("dafeiyu", base=tmp_path, training=False)
    assert "【摸鱼记忆】" not in off.prompt()
    on = PersonaStack.load("dafeiyu", base=tmp_path, training=True)
    assert "【摸鱼记忆】" in on.prompt()
    assert "第一条" in on.prompt() and "第二条" in on.prompt()


def test_training_config_defaults_on():
    cfg = LiveConfig()
    assert cfg.bili_training_like is True
    assert cfg.bili_training_comment is True
    assert cfg.bili_training_apply_kook is True
    assert cfg.bili_training_apply_live is True
    assert cfg.bili_training_persona == "companion"
    assert cfg.bili_training_max_minutes == 30
    assert cfg.bili_training_min_views == 0


def test_training_duration_and_view_limits(tmp_path, monkeypatch):
    mod = _load("training")
    monkeypatch.setattr(mod, "TRAINING_ROOT", tmp_path / "cache")
    cfg = LiveConfig(bili_training_max_minutes=45, bili_training_min_views=10_000)
    runner = mod.TrainingRunner(cfg, persona="companion")
    assert runner.config.max_duration_s == 45 * 60
    assert runner._passes_views(type("V", (), {"stat": {"view": 9999}})(), 10_000) is False
    assert runner._passes_views(type("V", (), {"stat": {"view": 10000}})(), 10_000) is True
    assert runner._passes_views(type("V", (), {"stat": {}})(), 0) is True


def test_training_helpers():
    mod = _load("training")

    assert mod._split_keywords("人工智能, 游戏史\n人工智能\n") == ["人工智能", "游戏史"]
    assert mod._clean_generated("```text\n好的，这是记忆。\n```", 100) == "这是记忆。"
    assert mod._clock(65) == "01:05"


def test_training_process_start_builds_train_command(tmp_path, monkeypatch):
    from dafeiyu.live import bili_training as proc

    monkeypatch.setattr(proc, "ROOT", tmp_path)
    monkeypatch.setattr(proc, "STATUS_FILE", tmp_path / "status.json")
    monkeypatch.setattr(proc, "STOP_FILE", tmp_path / "stop")
    monkeypatch.setattr(proc, "PID_FILE", tmp_path / "pid")
    monkeypatch.setattr(proc, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(proc, "_alive", lambda pid: True)
    calls = []

    class FakeProc:
        pid = 12345

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeProc()

    monkeypatch.setattr(proc.subprocess, "Popen", fake_popen)
    result = proc.start(persona="companion", keywords="人工智能,游戏史")
    assert result["ok"] is True
    cmd = calls[0][0]
    assert cmd[-1] == "人工智能,游戏史"
    assert "train" in cmd
    assert "--persona" in cmd and "companion" in cmd
    assert proc.status()["running"] is True


def test_training_atomic_status_retries_without_raising(tmp_path, monkeypatch):
    from dafeiyu.live import bili_training as proc

    target = tmp_path / "status.json"
    real_replace = proc.os.replace
    attempts = []

    def flaky_replace(src, dst):
        attempts.append(src)
        if len(attempts) < 3:
            raise PermissionError("locked")
        return real_replace(src, dst)

    monkeypatch.setattr(proc.os, "replace", flaky_replace)
    monkeypatch.setattr(proc.time, "sleep", lambda _seconds: None)
    assert proc._atomic_json(target, {"running": True}) is True
    assert json.loads(target.read_text(encoding="utf-8"))["running"] is True
    assert len(attempts) == 3
    assert not list(tmp_path.glob("status.json.*.tmp"))

    monkeypatch.setattr(proc.os, "replace",
                        lambda src, dst: (_ for _ in ()).throw(PermissionError("locked")))
    assert proc._atomic_json(target, {"running": False}) is False


def test_training_token_usage_merges_into_agent_ledger(tmp_path):
    from dafeiyu.core.usage import merge_usage
    from dafeiyu.live import bili_training as proc

    ledger = tmp_path / "usage.jsonl"
    rows = [
        {"ts": "2026-08-18T10:00:00+08:00", "source": "bili_training",
         "bvid": "BV1", "title": "第一条", "model": "deepseek-chat", "ok": True,
         "usage": {"input": 100, "output": 20, "total": 120}, "ms": 900},
        {"at": 1787000000.0, "source": "bili_training", "bvid": "BV2",
         "title": "第二条", "kind": "B站训练", "note": "训练《第二条》",
         "input": 50, "output": 10, "total": 60, "ms": 300},
    ]
    ledger.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                      encoding="utf-8")

    usage = proc.token_usage(limit=10, path=ledger)
    assert usage["source"] == "bili_training"
    assert usage["total"] == 180 and usage["calls"] == 2
    assert usage["input"] == 150 and usage["output"] == 30
    assert {step["note"] for step in usage["steps"]} == {"训练《第一条》", "训练《第二条》"}
    assert usage["by_run"][0]["run_id"] == "bili_training"

    merged = merge_usage({}, {}, usage, limit=10)
    assert merged["total"] == 180
    assert any(source["label"] == "B站训练" for source in merged["sources"])
    assert merged["training"]["total"] == 180
    assert merged["steps"][-1]["source"] == "bili_training"
    assert merged["steps"][-1]["cumulative"] == 180


def test_watch_failure_keeps_training_loop_alive(tmp_path, monkeypatch):
    mod = _load("training")
    monkeypatch.setattr(mod.proc, "write_status", lambda **kw: None)
    monkeypatch.setattr(mod.proc, "should_stop", lambda: False)
    monkeypatch.setattr(mod.proc, "clear_stop", lambda: None)
    monkeypatch.setattr(mod, "TRAINING_ROOT", tmp_path / "cache")
    runner = mod.TrainingRunner(LiveConfig(), persona="companion")
    monkeypatch.setattr(runner.llm, "available", lambda: (True, ""))
    video = type("V", (), {"bvid": "BVfail", "title": "失败视频"})()
    calls = []

    def candidates():
        calls.append(1)
        if len(calls) == 1:
            return [video]
        runner.stop.set()
        return []

    failures = []
    monkeypatch.setattr(runner, "_candidates", candidates)
    monkeypatch.setattr(runner, "_watch",
                        lambda v: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(runner, "_fail", lambda bvid, reason: failures.append((bvid, reason)))
    monkeypatch.setattr(runner, "_sleep", lambda *a, **k: True)

    assert runner.run() == 0
    assert len(calls) >= 2
    assert failures and failures[0][0] == "BVfail"
    assert "boom" in failures[0][1]


def test_watch_writes_memory_after_full_duration(tmp_path, monkeypatch):
    mod = _load("training")
    models = _load("models")
    VideoDetail, VideoRef = models.VideoDetail, models.VideoRef

    writes = []
    monkeypatch.setattr(mod.proc, "write_status",
                        lambda **kw: writes.append(kw))
    monkeypatch.setattr(mod, "TRAINING_ROOT", tmp_path / "cache")
    cfg = LiveConfig(bili_training_like=True, bili_training_comment=True)
    runner = mod.TrainingRunner(cfg, persona="companion")
    runner.memory = TrainingMemoryStore(tmp_path / "training.json")
    runner.stores = mod.Stores(tmp_path / "cache")

    video = VideoRef(bvid="BV1test", aid=1, title="测试视频",
                     owner_name="UP", duration_s=60)
    detail = VideoDetail(bvid="BV1test", aid=1, title="测试视频",
                         owner_name="UP", duration_s=1, cid=2,
                         stat={"view": 1000, "reply": 20})
    monkeypatch.setattr(runner.client, "view", lambda bvid: detail)
    monkeypatch.setattr(runner, "_summarize",
                        lambda d: ("这是一段足够长的第一人称记忆，" * 3, []))
    monkeypatch.setattr(runner, "_watch_wait", lambda d, **kw: True)
    monkeypatch.setattr(runner, "_live_flags", lambda: (True, True))
    monkeypatch.setattr(runner.liker, "like",
                        lambda aid, bvid: {"ok": True})
    monkeypatch.setattr(runner.commenter, "comment",
                        lambda aid, bvid, text: {"ok": True, "rpid": "9"})

    assert runner._watch(video) is True
    rows = runner.memory.entries()
    assert len(rows) == 1
    assert rows[0]["bvid"] == "BV1test"
    assert rows[0]["comment_status"] == "posted"
    assert rows[0]["like_status"] == "liked"
    assert json.loads(json.dumps(rows[0], ensure_ascii=False))["title"] == "测试视频"
    assert runner.stores.seen.is_seen("BV1test", 7)
    assert any(x.get("phase") == "watching" or x.get("phase") == "saved"
               for x in writes)
