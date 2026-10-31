# -*- coding: utf-8 -*-
"""设置页后端：和桌宠/控制台共用一份 config.json，而不是另存一份状态。

这里锁住三件事：
- 读接口脱敏（Key / Cookie / Secret 只报「已配置」）；
- 写接口校验端口/枚举/模型，并把「需要重启」的字段点名；
- 重启/退出按钮只落到注册过的桌宠窗口上（没有桌宠时明确报错）。
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import urllib.error
import urllib.request

import pytest

from dafeiyu import paths
from dafeiyu.ui import settings, stream_overlay


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "persona": "dafeiyu",
        "voice": "mambo",
        "game": "slay-the-spire-2",
        "port": 8730,
        "tts_url": "http://127.0.0.1:9880",
        "llm_model": "deepseek-flash",
        "llm_models": ["deepseek-flash", "deepseek-chat"],
        "llm_vision_model": "deepseek-flash",
        "bilibili_enabled": False,
        "bilibili_room_id": 0,
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(paths, "CONFIG_FILE", path)
    monkeypatch.setattr(settings, "voice_state", lambda: {"mambo_online": False})
    monkeypatch.setattr(stream_overlay, "STATE_FILE", tmp_path / "overlay.json")
    return path


def _get(port: int, path: str):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(f"http://127.0.0.1:{port}{path}", timeout=5)


def test_payload_masks_secrets_and_reports_ports(cfg_file, monkeypatch):
    cfg_file.write_text(json.dumps({
        "port": 8730, "tts_url": "http://127.0.0.1:9880",
        "llm_api_key": "sk-local-secret",
        "bilibili_cookie": "SESSDATA=secret",
        "kook_bot_token": "kook-secret",
    }, ensure_ascii=False), encoding="utf-8")

    payload = settings.payload()
    cfg = payload["config"]

    assert payload["ok"] is True
    assert cfg["port"] == 8730 and cfg["tts_port"] == 9880
    assert cfg["llm_api_key"] == "" and cfg["llm_api_key_set"] is True
    assert cfg["bilibili_cookie"] == "" and cfg["bilibili_cookie_set"] is True
    assert cfg["kook_bot_token"] == "" and cfg["kook_bot_token_set"] is True
    assert cfg["config_path"].endswith("config.json")
    assert cfg["server"]["pid"]
    assert "sk-local-secret" not in json.dumps(payload)
    assert "kook-secret" not in json.dumps(payload)


def test_payload_includes_persona_stickers_memory_autostart(cfg_file):
    cfg = settings.payload()["config"]
    assert cfg["persona_layers"]["layers"][0]["scope"] == "personal"
    assert isinstance(cfg["stickers"], list) and cfg["stickers"]
    assert all(row["url"].startswith("/stickers/") for row in cfg["stickers"])
    assert "memory" in cfg and "autostart" in cfg


def test_save_new_chat_and_desktop_fields(cfg_file):
    res = settings.save({"kook_context_turns": 16, "kook_summary_trigger": 32,
                         "kook_pet_say": True, "kook_allow_voice": False,
                         "kook_stickers": False, "kook_proactive": False,
                         "kook_proactive_minutes": 30,
                         "persona_live": False,
                         "pet_status_bar": True})
    cfg = res["config"]
    assert cfg["kook_context_turns"] == 16
    assert cfg["kook_summary_trigger"] == 32
    assert cfg["kook_pet_say"] is True
    assert cfg["kook_allow_voice"] is False
    assert cfg["kook_stickers"] is False
    assert cfg["kook_proactive"] is False
    assert cfg["kook_proactive_minutes"] == 30
    assert cfg["persona_live"] is False
    assert cfg["pet_status_bar"] is True


def test_save_rejects_bad_memory_window(cfg_file):
    with pytest.raises(ValueError):
        settings.save({"kook_context_turns": 2})
    with pytest.raises(ValueError):
        settings.save({"kook_summary_trigger": 200})
    with pytest.raises(ValueError):
        settings.save({"kook_proactive_minutes": 2})


def test_save_writes_config_and_points_at_restart_fields(cfg_file):
    new_port = _free_port()
    res = settings.save({"port": new_port, "pet_topmost": False, "volume": 0.9})

    assert res["ok"] is True
    assert res["config"]["port"] == new_port
    assert res["config"]["overlay_url"].endswith(f":{new_port}")
    assert res["config"]["pet_topmost"] is False
    assert res["config"]["volume"] == 0.9
    assert res["restart_required"] == ["port"]

    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["port"] == new_port and saved["pet_topmost"] is False


@pytest.mark.parametrize("patch, message", [
    ({"port": 0}, "端口"),
    ({"port": 70000}, "端口"),
    ({"port": 9880}, "不能是同一个"),
    ({"voice": "robot"}, "voice"),
    ({"autoplay_mode": "magic"}, "autoplay_mode"),
    ({"volume": 9}, "音量"),
    ({"tts_speed": 0.1}, "语速"),
    ({"llm_base_url": "ftp://example"}, "接口地址"),
    ({"llm_model": "  "}, "模型名"),
    ({"game": "not-a-game"}, "未知游戏"),
])
def test_save_rejects_bad_values(cfg_file, patch, message):
    with pytest.raises(ValueError) as exc:
        settings.save(patch)
    assert message in str(exc.value)


def test_secrets_roundtrip_never_leak(cfg_file):
    res = settings.save({"llm_api_key": "sk-new-key"})
    assert res["config"]["llm_api_key_set"] is True
    assert "sk-new-key" not in json.dumps(res)
    assert "sk-new-key" in cfg_file.read_text(encoding="utf-8")

    # *** 是页面回显占位符，不当成新 Key
    res = settings.save({"llm_api_key": "***"})
    assert res["config"]["llm_api_key_set"] is True

    res = settings.save({"clear_api_key": True})
    assert res["config"]["llm_api_key_set"] is False


def test_model_name_is_added_to_the_model_list(cfg_file):
    res = settings.save({"llm_model": "deepseek-reasoner"})
    assert res["config"]["llm_models"][0] == "deepseek-reasoner"


def test_overlay_template_roundtrip(cfg_file):
    res = settings.save({"overlay_template": "cinema"})
    assert res["config"]["overlay"]["template"] == "cinema"
    assert stream_overlay.load_template() == "cinema"

    res = settings.save({"overlay_template": "off"})
    assert res["config"]["overlay"]["template"] == "off"


def test_restart_requires_a_registered_pet(cfg_file):
    settings.clear_app_hooks()
    out = settings.request_restart()
    assert out["ok"] is False
    assert "python main.py" in out["error"]


def test_restart_fires_the_registered_hook(cfg_file, monkeypatch):
    fired = []

    class ImmediateTimer:
        def __init__(self, delay, fn, args=()):
            self.fn, self.args = fn, args

        def start(self):
            self.fn(*self.args)

    monkeypatch.setattr(settings.threading, "Timer", ImmediateTimer)
    settings.set_app_hook("restart", lambda: fired.append("restart"))
    try:
        out = settings.request_restart()
    finally:
        settings.clear_app_hooks()
    assert out["ok"] is True and out["restart"] is True
    assert fired == ["restart"]


def test_restart_app_spawns_a_new_process_and_closes(monkeypatch, tmp_path):
    from dafeiyu.ui import desktop

    calls = {}

    class FakeWin:
        restart_app = desktop.StreamerWindow.restart_app

        def close(self):
            calls["closed"] = True

    def fake_popen(args, **kwargs):
        calls["args"] = list(args)
        calls["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(desktop.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(desktop.paths, "LOGS", tmp_path)

    FakeWin().restart_app()

    assert calls["args"][0] == sys.executable
    assert calls["args"][1].endswith("main.py")
    assert calls["closed"] is True
    assert calls["kwargs"]["cwd"] == desktop.ROOT
    assert (tmp_path / "pet_restart.log").exists()


def test_settings_page_and_api_routes(cfg_file, monkeypatch):
    from dafeiyu.ui import overlay_server

    srv = overlay_server._make_server(0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with _get(port, "/settings") as resp:
            page = resp.read().decode("utf-8")
        assert "设置" in page and "/api/settings" in page

        with _get(port, "/api/settings") as resp:
            payload = json.loads(resp.read())
        assert payload["ok"] is True
        assert payload["config"]["port"] == 8730

        with _get(port, "/stickers/sticker_one-bowl.png") as resp:
            assert resp.headers.get_content_type() == "image/png"
            assert resp.read()[:8] == b"\x89PNG\r\n\x1a\n"

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/settings",
            data=json.dumps({"pet_muted": True}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=10) as resp:
            saved = json.loads(resp.read())
        assert saved["config"]["pet_muted"] is True

        from dafeiyu.voice import startup

        monkeypatch.setattr(startup, "stop_from_config",
                            lambda cfg=None: {"ok": True, "stopped": True,
                                              "message": "已关闭"})
        stop_req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/voice/stop",
            data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with opener.open(stop_req, timeout=10) as resp:
            assert json.loads(resp.read())["stopped"] is True

        bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/settings",
            data=json.dumps({"port": 70000}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            opener.open(bad, timeout=10)
        assert exc.value.code == 400

        cross = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/settings",
            data=json.dumps({"pet_muted": False}).encode(),
            headers={"Content-Type": "application/json", "Origin": "http://evil.example"},
            method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            opener.open(cross, timeout=10)
        assert exc.value.code == 403

        same = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/settings",
            data=json.dumps({"pet_muted": False}).encode(),
            headers={"Content-Type": "application/json",
                     "Origin": f"http://127.0.0.1:{port}"},
            method="POST")
        with opener.open(same, timeout=10) as resp:
            assert json.loads(resp.read())["config"]["pet_muted"] is False
    finally:
        srv.shutdown()
        srv.server_close()
