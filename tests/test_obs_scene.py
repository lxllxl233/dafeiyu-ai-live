"""OBS 场景配置：B 站云观影的采集/场景页参数能被正确读入。"""
from __future__ import annotations

from dafeiyu.recording.obs import Obs, ObsProfile, load_profile


def test_bili_obs_profile_uses_monitor_and_scene_browser_source():
    profile = load_profile("bilibili-hot-commentary")
    assert profile.scene == "Bili Watch"
    assert profile.capture_mode == "monitor"
    assert profile.audio_mode == "desktop"
    assert profile.monitor_index == 1
    assert profile.overlay_url == "http://127.0.0.1:8730/bili"
    assert profile.overlay_source == "Bili Scene"
    assert profile.ticker_source == ""


def test_obs_profile_from_dict_parses_numeric_fields():
    profile = ObsProfile.from_dict({
        "capture_mode": "window",
        "monitor_index": "2",
        "overlay_width": "1280",
        "overlay_height": 720,
        "window_match": ["Chrome"],
    })
    assert profile.capture_mode == "window"
    assert profile.monitor_index == 2
    assert profile.overlay_width == 1280
    assert profile.overlay_height == 720
    assert profile.window_match == ("Chrome",)


def _fake_obs(inputs):
    obs = Obs.__new__(Obs)
    obs.p = ObsProfile(audio_source="Bili Audio", audio_mode="desktop")
    obs.list_inputs = lambda: inputs
    calls = []
    obs.request = lambda kind, payload=None: (calls.append((kind, payload)),
                                              {})[1]
    obs.get_scene_item_id = lambda scene, name: 7
    obs.set_item_enabled = lambda scene, name, enabled: None
    return obs, calls


def test_bili_desktop_audio_reuses_existing_without_duplicate_source():
    obs, calls = _fake_obs([
        {"inputName": "桌面音频", "inputKind": "wasapi_output_capture"},
    ])
    result = obs.ensure_desktop_audio("Bili Watch")
    assert result["source"] == "桌面音频"
    assert result["reused"] is True
    assert not [c for c in calls if c[0] == "CreateInput"]


def test_bili_desktop_audio_drops_old_duplicate_source():
    obs, calls = _fake_obs([
        {"inputName": "桌面音频", "inputKind": "wasapi_output_capture"},
        {"inputName": "Bili Audio", "inputKind": "wasapi_output_capture"},
    ])
    obs.scene_items = lambda scene: [
        {"sourceName": "Bili Audio", "sceneItemId": 7}]
    result = obs.ensure_desktop_audio("Bili Watch")
    assert result["source"] == "桌面音频"
    assert [c for c in calls if c[0] == "RemoveSceneItem"]
    assert not [c for c in calls if c[0] == "CreateInput"]


def test_bili_scene_browser_source_is_placed_on_top():
    obs, calls = _fake_obs([])
    obs.p = ObsProfile(
        overlay_url="http://127.0.0.1:8730/bili",
        overlay_source="Bili Scene", overlay_width=1920, overlay_height=1080)
    obs.scene_items = lambda scene: [
        {"sourceName": "Bili Scene", "sceneItemId": 7},
        {"sourceName": "Bili Monitor", "sceneItemId": 1},
        {"sourceName": "桌面音频", "sceneItemId": 5},
    ]
    obs.fit_item = lambda *args, **kwargs: None
    obs.ensure_browser_source("Bili Watch")
    created = [c for c in calls if c[0] == "CreateInput"]
    assert created and created[0][1]["inputSettings"]["shutdown"] is True
    placed = [c for c in calls if c[0] == "SetSceneItemIndex"]
    assert placed and placed[-1][1]["sceneItemIndex"] == 2
