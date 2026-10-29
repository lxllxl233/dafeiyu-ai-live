"""右键「OBS 录像」：一个入口开 / 停，连不上要说人话、别把异常抛到 UI。"""
from __future__ import annotations

from dafeiyu.recording import manual, replay


class _FakeObs:
    def __init__(self, playing: bool):
        self.playing = playing
        self.calls: list[str] = []
        self.closed = False

    def status(self):
        return {"outputActive": self.playing, "outputPath": "/tmp/obs.mp4"}

    def start_record(self):
        self.calls.append("start")
        self.playing = True

    def stop_record(self):
        self.calls.append("stop")
        self.playing = False
        return {"outputPath": "/tmp/obs.mp4"}

    def close(self):
        self.closed = True


def _patch_connect(monkeypatch, playing: bool) -> _FakeObs:
    obs = _FakeObs(playing)
    monkeypatch.setattr(manual, "_connect", lambda timeout=2.0: obs)
    return obs


def test_toggle_starts_when_obs_is_idle(monkeypatch):
    obs = _patch_connect(monkeypatch, playing=False)
    out = manual.toggle()
    assert out["ok"] is True
    assert out["recording"] is True
    assert out["action"] == "start"
    assert obs.calls == ["start"]
    assert obs.closed is True


def test_toggle_stops_plain_obs_recording(monkeypatch, tmp_path):
    _patch_connect(monkeypatch, playing=True)
    monkeypatch.setattr(replay, "get_current", lambda: None)
    stopped: list[int] = []
    monkeypatch.setattr(replay, "stop_recording_now",
                        lambda: stopped.append(1) or str(tmp_path / "clip.mp4"))
    out = manual.toggle()
    assert out["ok"] is True
    assert out["recording"] is False
    assert out["action"] == "stop"
    assert stopped == [1]
    assert out["path"].endswith("clip.mp4")


def test_toggle_stop_files_the_run_into_recordings(monkeypatch, tmp_path):
    """这一局是 replay 开的：停录顺手收尾，视频落到 run-NNNN/run.mp4。"""
    _patch_connect(monkeypatch, playing=True)
    run_dir = tmp_path / "run-0001"
    run_dir.mkdir()
    dst = run_dir / "run.mp4"
    monkeypatch.setattr(replay, "get_current", lambda: run_dir)
    monkeypatch.setattr(replay, "read_meta", lambda _d: {"status": "recording"})
    monkeypatch.setattr(replay, "stop_recording", lambda _d, result="": dst)
    cleared: list[int] = []
    indexed: list[int] = []
    monkeypatch.setattr(replay, "clear_current", lambda: cleared.append(1))
    monkeypatch.setattr(replay, "write_index", lambda: indexed.append(1))
    out = manual.toggle()
    assert out["ok"] is True
    assert out["recording"] is False
    assert out["path"] == str(dst)
    assert cleared == [1]
    assert indexed == [1]


def test_toggle_leaves_finished_runs_alone(monkeypatch, tmp_path):
    """上一局 meta 已经是 done：别再往那个 run.mp4 里塞东西。"""
    _patch_connect(monkeypatch, playing=True)
    run_dir = tmp_path / "run-0002"
    run_dir.mkdir()
    monkeypatch.setattr(replay, "get_current", lambda: run_dir)
    monkeypatch.setattr(replay, "read_meta", lambda _d: {"status": "done"})
    calls: list[str] = []
    monkeypatch.setattr(replay, "stop_recording",
                        lambda *a, **k: calls.append("finalize"))
    monkeypatch.setattr(replay, "stop_recording_now",
                        lambda: calls.append("raw") or "")
    out = manual.toggle()
    assert out["ok"] is True
    assert calls == ["raw"]


def test_toggle_reports_obs_offline(monkeypatch):
    def boom(timeout=2.0):
        raise ConnectionRefusedError(10061, "Connection refused")

    monkeypatch.setattr(manual, "_connect", boom)
    out = manual.toggle()
    assert out["ok"] is False
    assert out["recording"] is None
    assert "OBS" in out["message"]
    assert "密码" not in out["message"]


def test_start_failure_is_reported_not_raised(monkeypatch):
    """第一次连上（问出没在录）之后 OBS 掉线：报错而不是抛异常。"""
    obs = _FakeObs(playing=False)
    calls: list[int] = []

    def connect(timeout=2.0):
        calls.append(1)
        if len(calls) == 1:
            return obs
        raise TimeoutError()

    monkeypatch.setattr(manual, "_connect", connect)
    out = manual.toggle()
    assert out["ok"] is False
    assert out["recording"] is False
    assert "开录失败" in out["message"]


def test_probe_reports_status(monkeypatch):
    _patch_connect(monkeypatch, playing=True)
    out = manual.probe()
    assert out["ok"] is True
    assert out["recording"] is True
    assert out["message"] == "OBS 正在录像"
