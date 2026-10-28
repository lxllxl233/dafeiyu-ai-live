"""录像回放目录：一局一个 run.mp4，按游戏分组。"""
from __future__ import annotations

import json

import pytest

from dafeiyu.recording import auto, replay


@pytest.fixture()
def rec(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "RECORDINGS", tmp_path)
    monkeypatch.setattr(replay, "CURRENT", tmp_path / ".current.json")
    return tmp_path


def test_create_finalize_index(rec):
    d = replay.create_run("sts2", 7, "ironclad", 10, "测试")
    assert d == rec / "slay-the-spire-2" / "run-0007"
    (d / "obs.mp4").write_bytes(b"x" * 16)
    out = replay.finalize(d, d / "obs.mp4", result="win_a10")
    assert out.name == "run.mp4"
    assert out.read_bytes() == b"x" * 16
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["result"] == "win_a10"
    assert meta["size_bytes"] == 16
    assert meta["status"] == "done"

    rows = replay.scan("sts2")
    assert len(rows) == 1 and rows[0]["run"] == 7 and rows[0]["video"] == "run.mp4"
    index = replay.render_index()
    assert "杀戮尖塔 2" in index and "run-0007/run.mp4" in index


def test_finalize_does_not_overwrite(rec):
    d = replay.create_run("sts2", 8)
    (d / "run.mp4").write_bytes(b"old")
    (d / "new.mp4").write_bytes(b"new")
    with pytest.raises(FileExistsError):
        replay.finalize(d, d / "new.mp4")
    assert (d / "run.mp4").read_bytes() == b"old"


def test_migrate_legacy(rec):
    rec.joinpath("sts2_ironclad_a10_run9_win.mp4").write_bytes(b"z" * 8)
    rec.joinpath("preview.mp4").write_bytes(b"p")
    moved = replay.migrate_legacy()
    assert len(moved) == 2
    assert (rec / "slay-the-spire-2" / "run-0009" / "run.mp4").read_bytes() == b"z" * 8
    assert (rec / "slay-the-spire-2" / "_legacy" / "preview.mp4").exists()
    meta = json.loads((rec / "slay-the-spire-2" / "run-0009" / "meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["result"] == "win" and meta["legacy"] is True


def test_timeline_records_offsets_and_chapters(tmp_path, monkeypatch):
    d = tmp_path / "run-0042"
    d.mkdir()
    monkeypatch.setattr(auto, "_session", {
        "active": True, "path": str(d), "record_started_at": 1000.0, "events": 0})
    times = iter([1000.0, 1005.0, 1010.0, 1015.0])
    monkeypatch.setattr(auto.time, "time", lambda: next(times))

    assert auto.record_event("combat_start", "第 1 层 · 战斗开始", floor=1)
    assert auto.record_event("llm_turn", "LLM 回合", tokens=123)
    assert auto.record_event("speech", "冲！", text="冲！")
    assert auto.record_event("stop", "停手")
    out = auto.write_timeline(d)

    assert out == {"events": 4, "chapters": 3}
    events = [json.loads(line) for line in
              (d / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["offset"] for e in events] == [0.0, 5.0, 10.0, 15.0]
    chapters = json.loads((d / "timeline.json").read_text(encoding="utf-8"))["chapters"]
    assert chapters[0]["start"] == 0 and chapters[0]["end"] == 10
    assert chapters[-1]["kind"] == "stop" and chapters[-1]["end"] == 15
    text = (d / "chapters.md").read_text(encoding="utf-8")
    assert "第 1 层 · 战斗开始" in text and "00:00:10" in text
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["event_count"] == 4 and meta["chapters"] == "chapters.md"
