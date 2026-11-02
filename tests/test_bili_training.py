# -*- coding: utf-8 -*-
"""B 站摸鱼训练：记忆存储、人格注入和一条视频的完整落库。"""
from __future__ import annotations

import json

from dafeiyu.core.config import LiveConfig
from dafeiyu.games.registry import GameRegistry
from dafeiyu.persona.layers import PersonaStack
from dafeiyu.persona.training import (TrainingDigestStore, TrainingMemoryStore,
                                      prompt_text)

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
    assert cfg.bili_training_comment_chars == 160
    assert cfg.bili_favorites_id == 0
    assert cfg.bili_favorites_batch == 50


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

    sentences = ("甲" * 45 + "。") * 4
    medium = mod._excerpt_comment(sentences, 120)
    assert 45 < len(medium) <= 120 and medium.endswith("。")
    long = "这一句真的很长，" * 20
    clipped = mod._excerpt_comment(long, 50)
    assert 0 < len(clipped) <= 51 and clipped.endswith("…")
    assert len(mod._excerpt_comment("没有标点" * 30, 40)) <= 40


def test_comment_payload_is_written_separately():
    mod = _load("training")

    memory, comment = mod._parse_summary_payload(
        '{"memory": "这条视频让我更确定八小时不是恩赐。", '
        '"comment": "我记住的是那句‘八小时不是恩赐’，弹幕也都在刷。"}',
        memory_limit=600, comment_limit=80)
    assert "八小时" in memory
    assert "我记住的是" not in comment
    assert "弹幕" not in comment and len(comment) <= 80

    # 旧模型/坏格式退回整段记忆，评论留空由 _watch 回退。
    memory, comment = mod._parse_summary_payload("这是一段没有 JSON 的旧记忆。",
                                                 memory_limit=600, comment_limit=80)
    assert memory == "这是一段没有 JSON 的旧记忆。"
    assert comment == ""


def test_summarize_returns_comment_and_includes_digest(tmp_path, monkeypatch):
    mod = _load("training")
    models = _load("models")
    runner = mod.TrainingRunner(LiveConfig(bili_training_comment_chars=80),
                                persona="companion")
    detail = models.VideoDetail(bvid="BVtest", aid=1, title="测试视频",
                                owner_name="UP", duration_s=120, cid=0)
    monkeypatch.setattr(runner.client, "hot_comments", lambda aid, limit=30: [])
    monkeypatch.setattr(runner, "_log_usage", lambda *a, **k: None)

    class FakeDigest:
        def load(self):
            return {"text": "你长期关注劳动议题，反感把吃苦当勋章。"}

    monkeypatch.setattr(mod, "TrainingDigestStore", lambda: FakeDigest())
    seen = {}

    class FakeLLM:
        def complete(self, messages, model=None, **kwargs):
            seen["system"] = messages[0]["content"]
            seen["kwargs"] = kwargs
            return {"ok": True, "model": "fake", "usage": {}, "text": json.dumps({
                "memory": "这条视频让我更确定，八小时工作制不是谁好心赏的，是磨出来的。",
                "comment": "那句‘八小时不是恩赐’说得太对了，评论区都在刷。",
            }, ensure_ascii=False)}

    runner.llm = FakeLLM()
    memory, comment, comments = runner._summarize(detail)
    assert "长期判断" in seen["system"]
    assert "劳动议题" in seen["system"]
    assert seen["kwargs"].get("json_mode") is True
    assert "八小时" in memory and "八小时" in comment
    assert comments == []



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
    cfg = LiveConfig(bili_training_like=True, bili_training_comment=True,
                     bili_training_comment_chars=160)
    runner = mod.TrainingRunner(cfg, persona="companion")
    runner.memory = TrainingMemoryStore(tmp_path / "training.json")
    runner.stores = mod.Stores(tmp_path / "cache")

    video = VideoRef(bvid="BV1test", aid=1, title="测试视频",
                     owner_name="UP", duration_s=60)
    detail = VideoDetail(bvid="BV1test", aid=1, title="测试视频",
                         owner_name="UP", duration_s=1, cid=2,
                         stat={"view": 1000, "reply": 20})
    summary = "我看完记住了一个判断：先把问题拆小，再慢慢解决。" * 6
    monkeypatch.setattr(runner.client, "view", lambda bvid: detail)
    monkeypatch.setattr(runner, "_summarize", lambda d: (summary, []))
    monkeypatch.setattr(runner, "_watch_wait", lambda d, **kw: True)
    monkeypatch.setattr(runner, "_live_flags", lambda: (True, True))
    monkeypatch.setattr(runner, "_comment_limit", lambda: 160)
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
    assert rows[0]["comment"]
    assert 80 <= len(rows[0]["comment"]) <= 160
    assert json.loads(json.dumps(rows[0], ensure_ascii=False))["title"] == "测试视频"
    assert runner.stores.seen.is_seen("BV1test", 7)
    assert any(x.get("phase") == "watching" or x.get("phase") == "saved"
               for x in writes)


def test_fav_media_parses_into_video_ref():
    models = _load("models")
    ref = models.VideoRef.from_fav({
        "id": 117513878834212,
        "bvid": "BV1tWx362EVk",
        "title": "一条收藏的视频",
        "intro": "收藏夹里的简介",
        "duration": 1359,
        "upper": {"mid": 1, "name": "UP主"},
        "cnt_info": {"play": 191756, "danmaku": 248, "reply": 3,
                     "collect": 3691},
        "pubtime": 1793179800,
        "ugc": {"first_cid": 43106569686},
    })
    assert ref is not None
    assert ref.bvid == "BV1tWx362EVk"
    assert ref.aid == 117513878834212
    assert ref.desc == "收藏夹里的简介"
    assert ref.duration_s == 1359
    assert ref.owner_name == "UP主"
    assert ref.stat["view"] == 191756
    assert ref.stat["favorite"] == 3691
    assert ref.cid == 43106569686
    assert models.VideoRef.from_fav({"title": "已失效视频", "bvid": ""}) is None


def test_fav_list_parses_page_and_filters_invalid(monkeypatch):
    api = _load("api")
    client = api.BiliClient(cookie="SESSDATA=x")
    calls = []

    def fake_get_json(url, params=None):
        calls.append((url, params))
        if "folder/info" in url:
            return {"id": 308848993, "title": "默认收藏夹", "media_count": 1316}
        return {
            "info": {"id": 308848993, "title": "默认收藏夹", "media_count": 1316},
            "medias": [
                {"id": 117513878834212, "bvid": "BV1tWx362EVk",
                 "title": "视频一", "intro": "简介", "duration": 1359,
                 "upper": {"mid": 1, "name": "UP"},
                 "cnt_info": {"play": 191756}, "pubtime": 1793179800},
                {"id": 2, "title": "已失效视频", "bvid": ""},
            ],
            "has_more": True,
        }

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    folder = client.fav_folder(308848993)
    assert folder["title"] == "默认收藏夹"
    assert folder["count"] == 1316

    page = client.fav_list(308848993, page=2)
    assert page["has_more"] is True
    assert page["info"]["count"] == 1316
    assert len(page["videos"]) == 1
    assert page["videos"][0].bvid == "BV1tWx362EVk"
    assert page["videos"][0].stat["view"] == 191756
    assert calls[-1][1]["pn"] == 2


def _favorites_runner(tmp_path, monkeypatch, *, videos, existing=(),
                      limit=0, writes=None):
    writes = writes if writes is not None else []
    mod = _load("training")
    monkeypatch.setattr(mod.proc, "write_status", lambda **kw: writes.append(kw))
    monkeypatch.setattr(mod.proc, "should_stop", lambda: False)
    runner = mod.TrainingRunner(LiveConfig(), persona="companion",
                                favorites_id=308848993, scan_limit=limit)
    runner.memory = mod.TrainingMemoryStore(tmp_path / "training.json")
    runner.stores = mod.Stores(tmp_path / "cache")
    for bvid in existing:
        runner.memory.add({"bvid": bvid, "title": f"旧收藏 {bvid}",
                           "summary": "以前扫过的一条收藏记忆。" * 8,
                           "source": "favorites", "folder": "默认收藏夹"})
    monkeypatch.setattr(runner.client, "fav_list", lambda media_id, **kw: {
        "videos": videos, "has_more": False, "page": 1,
        "info": {"id": media_id, "title": "默认收藏夹", "count": len(videos)},
    })
    return mod, runner


def test_favorites_scan_writes_memory_without_duration_wait(tmp_path, monkeypatch):
    writes = []
    mod = _load("training")
    models = _load("models")
    VideoRef, VideoDetail = models.VideoRef, models.VideoDetail
    videos = [VideoRef(bvid="BVold", title="旧收藏"),
              VideoRef(bvid="BVnew", title="新收藏")]
    mod, runner = _favorites_runner(tmp_path, monkeypatch, videos=videos,
                                    existing=("BVold",), writes=writes)
    detail = VideoDetail(bvid="BVnew", aid=2, title="新收藏", owner_name="UP",
                         duration_s=3600, cid=9)
    monkeypatch.setattr(runner.client, "view", lambda bvid: detail)
    summaries = []

    def fake_summarize(d, *, source="training", folder=""):
        summaries.append((source, folder, d.bvid))
        return ("这条收藏讲的是先把问题拆小，再慢慢解决。" * 8, [])

    monkeypatch.setattr(runner, "_summarize", fake_summarize)
    monkeypatch.setattr(runner, "_watch_wait",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("快扫不应等待视频时长")))
    monkeypatch.setattr(runner.liker, "like",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("快扫不应点赞")))
    monkeypatch.setattr(runner.commenter, "comment",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("快扫不应评论")))
    monkeypatch.setattr(runner, "_sleep", lambda *a, **k: True)

    assert runner._run_favorites() == 0
    rows = runner.memory.entries()
    assert len(rows) == 2
    new = [row for row in rows if row["bvid"] == "BVnew"][0]
    assert new["source"] == "favorites"
    assert new["folder"] == "默认收藏夹"
    assert new["comment_status"] == "disabled"
    assert new["like_status"] == "skipped"
    assert new["comment"] == ""
    assert summaries == [("favorites", "默认收藏夹", "BVnew")]
    assert runner.stores.seen.is_seen("BVnew", 7)
    assert any(w.get("mode") == "favorites" and w.get("phase") == "done"
               for w in writes)


def test_favorites_scan_respects_batch_limit(tmp_path, monkeypatch):
    writes = []
    mod = _load("training")
    models = _load("models")
    VideoRef, VideoDetail = models.VideoRef, models.VideoDetail
    videos = [VideoRef(bvid=f"BV{i}", title=f"收藏 {i}") for i in range(3)]
    mod, runner = _favorites_runner(tmp_path, monkeypatch, videos=videos,
                                    limit=1, writes=writes)
    detail = VideoDetail(bvid="BV0", aid=1, title="收藏 0", duration_s=60)
    monkeypatch.setattr(runner.client, "view", lambda bvid: detail)
    monkeypatch.setattr(runner, "_summarize",
                        lambda d, **kw: ("收藏夹记忆。" * 10, []))
    monkeypatch.setattr(runner, "_sleep", lambda *a, **k: True)

    assert runner._run_favorites() == 0
    assert [row["bvid"] for row in runner.memory.entries()] == ["BV0"]
    assert any(w.get("phase") == "paused" for w in writes)
    assert not any(w.get("phase") == "done" for w in writes)


def test_favorites_summary_skips_danmaku(tmp_path, monkeypatch):
    mod = _load("training")
    models = _load("models")
    VideoDetail = models.VideoDetail
    runner = mod.TrainingRunner(LiveConfig(), persona="companion")
    detail = VideoDetail(bvid="BVfav", aid=7, cid=8, duration_s=600,
                         title="收藏视频", owner_name="UP")
    danmaku_calls = []
    monkeypatch.setattr(runner.client, "hot_comments", lambda aid, limit=30: [])
    monkeypatch.setattr(runner.client, "danmaku",
                        lambda *a, **k: danmaku_calls.append(a) or None)

    class FakeLLM:
        def complete(self, messages, model=None):
            return {"ok": True, "text": "这条收藏让我记住了一个判断。" * 8,
                    "usage": {}, "model": "fake"}

    runner.llm = FakeLLM()
    monkeypatch.setattr(runner, "_log_usage", lambda *a, **k: None)
    text, comment, comments = runner._summarize(
        detail, source="favorites", folder="默认收藏夹")
    assert text and len(text) >= 30
    assert comment == ""
    assert comments == []
    assert danmaku_calls == []


def test_training_process_start_builds_favorites_command(tmp_path, monkeypatch):
    from dafeiyu.live import bili_training as proc

    monkeypatch.setattr(proc, "ROOT", tmp_path)
    monkeypatch.setattr(proc, "STATUS_FILE", tmp_path / "status.json")
    monkeypatch.setattr(proc, "STOP_FILE", tmp_path / "stop")
    monkeypatch.setattr(proc, "PID_FILE", tmp_path / "pid")
    monkeypatch.setattr(proc, "RUN_LOG", tmp_path / "run.log")
    monkeypatch.setattr(proc, "_alive", lambda pid: True)
    calls = []

    class FakeProc:
        pid = 54321

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeProc()

    monkeypatch.setattr(proc.subprocess, "Popen", fake_popen)
    result = proc.start(favorites_id=308848993, limit=25)
    assert result["ok"] is True
    cmd = calls[0][0]
    assert "--favorites" in cmd and "308848993" in cmd
    assert "--limit" in cmd and "25" in cmd
    assert proc.status()["mode"] == "favorites"
    assert proc.status()["media_id"] == 308848993


def test_training_prompt_marks_favorites(tmp_path):
    store = TrainingMemoryStore(tmp_path / "training.json")
    store.add(_entry(source="favorites", folder="默认收藏夹"))
    text = prompt_text(tmp_path / "training.json")
    assert "（收藏夹）" in text
    assert "默认收藏夹" not in text


def test_training_prompt_marks_shared(tmp_path):
    store = TrainingMemoryStore(tmp_path / "training.json")
    store.add(_entry(source="shared", bvid="BVshared", title="别人发我的视频"))
    text = prompt_text(tmp_path / "training.json")
    assert "（聊天分享）" in text
    assert "别人把视频分享给你" in text
    assert "别人发我的视频" in text


def test_training_store_keeps_like_error(tmp_path):
    store = TrainingMemoryStore(tmp_path / "training.json")
    store.add(_entry(like_status="failed", like_error="Cookie 过期"))
    row = store.entries()[0]
    assert row["like_status"] == "failed"
    assert row["like_error"] == "Cookie 过期"


def test_digest_store_and_prompt_prefers_compress(tmp_path):
    store = TrainingMemoryStore(tmp_path / "training.json")
    row = store.add(_entry(bvid="BV1", title="原始标题"))
    digest = TrainingDigestStore(tmp_path / "training_digest.json")
    saved = digest.save("压缩后的长期画像，保留了主题和立场。" * 8,
                        source_count=1, covered_ids=[row["id"]],
                        persona="companion", model="fake", chunks=1)
    assert saved["text"]
    text = prompt_text(tmp_path / "training.json")
    assert "【B站摸鱼训练记忆（压缩）】" in text
    assert "压缩后的长期画像" in text
    assert "原始标题" not in text
    assert digest.clear() is True
    assert "原始标题" in prompt_text(tmp_path / "training.json")


def test_digest_keeps_new_entries_after_snapshot(tmp_path):
    store = TrainingMemoryStore(tmp_path / "training.json")
    old = store.add(_entry(bvid="BVold", title="已压缩的旧视频"))
    digest = TrainingDigestStore(tmp_path / "training_digest.json")
    digest.save("旧画像：我长期关注社会观察和劳动议题。" * 8,
                source_count=1, covered_ids=[old["id"]])
    store.add(_entry(bvid="BVnew", title="压缩后新扫的视频",
                     summary="这是压缩之后才扫到的新记忆，应该以原文补上。"))
    text = prompt_text(tmp_path / "training.json")
    assert "旧画像" in text
    assert "压缩后新扫的视频" in text
    assert "已压缩的旧视频" not in text
    assert "压缩后新增的 1 条记忆" in text


def test_memory_store_retries_locked_replace(tmp_path, monkeypatch):
    from dafeiyu.persona import training as pt

    real_replace = pt.os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "locked", str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(pt.os, "replace", flaky_replace)
    store = TrainingMemoryStore(tmp_path / "training.json")
    store.add(_entry(title="重试一次"))
    assert calls["n"] == 3
    assert store.entries()[0]["title"] == "重试一次"


def test_compress_runner_writes_digest(tmp_path, monkeypatch):
    mod = _load("training")
    writes = []
    monkeypatch.setattr(mod.proc, "write_compress_status",
                        lambda **kw: writes.append(kw))
    monkeypatch.setattr(mod.proc, "should_compress_stop", lambda: False)
    monkeypatch.setattr(mod.proc, "clear_compress_stop", lambda: None)
    monkeypatch.setattr(mod, "USAGE_FILE", tmp_path / "usage.jsonl")
    monkeypatch.setattr(mod, "_COMPRESS_CHUNK_CHARS", 60)
    runner = mod.CompressionRunner(LiveConfig(), persona="companion")
    memory = TrainingMemoryStore(tmp_path / "training.json")
    for i in range(3):
        memory.add(_entry(bvid=f"BV{i}", title=f"记忆 {i}"))
    runner.memory = memory
    runner.digest = TrainingDigestStore(tmp_path / "training_digest.json")
    calls = []

    class FakeLLM:
        def available(self):
            return True, ""

        def complete(self, messages, model=None):
            calls.append(messages)
            if len(calls) <= 3:
                text = "这是第 %d 段压缩摘要，保留主题和立场。" % len(calls)
            else:
                text = "最终长期画像：我长期关注社会观察、劳动议题和游戏。" * 6
            return {"ok": True, "text": text, "model": "fake",
                    "usage": {"input": 10, "output": 5, "total": 15}}

    runner.llm = FakeLLM()
    assert runner.run() == 0
    saved = runner.digest.load()
    assert saved["source_count"] == 3
    assert len(saved["covered_ids"]) == 3
    assert "最终长期画像" in saved["text"]
    assert len(calls) == 4
    assert any(w.get("phase") == "done" for w in writes)
    assert (tmp_path / "usage.jsonl").is_file()


def test_compress_process_start_builds_command(tmp_path, monkeypatch):
    from dafeiyu.live import bili_training as proc

    monkeypatch.setattr(proc, "ROOT", tmp_path)
    monkeypatch.setattr(proc, "COMPRESS_STATUS_FILE", tmp_path / "compress.json")
    monkeypatch.setattr(proc, "COMPRESS_STOP_FILE", tmp_path / "compress.stop")
    monkeypatch.setattr(proc, "COMPRESS_PID_FILE", tmp_path / "compress.pid")
    monkeypatch.setattr(proc, "COMPRESS_LOG", tmp_path / "compress.log")
    monkeypatch.setattr(proc, "_alive", lambda pid: True)
    calls = []

    class FakeProc:
        pid = 65432

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeProc()

    monkeypatch.setattr(proc.subprocess, "Popen", fake_popen)
    result = proc.start_compress(persona="companion")
    assert result["ok"] is True
    cmd = calls[0][0]
    assert "--compress" in cmd
    assert "--persona" in cmd and "companion" in cmd
    assert proc.compress_status()["running"] is True
    assert proc.compress_status()["phase"] == "starting"
