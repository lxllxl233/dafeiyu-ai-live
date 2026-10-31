# -*- coding: utf-8 -*-
"""overlay server 的断线与 TTS 故障路径。

页面（OBS 浏览器源 / 直播姬）在等 /tts 时随时可能被切走，连接直接 RST；
默认的 socketserver 会为这种「响应没人要了」打一整页 traceback。这里锁住：
连接层的错一律安静吞掉、服务继续活着，同时合成失败要留下可排查的原因。
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
import urllib.error
import urllib.request

from dafeiyu.ui import overlay_server


def _serve():
    srv = overlay_server._make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _get(port: int, path: str):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(f"http://127.0.0.1:{port}{path}", timeout=5)


def test_make_server_port_zero_opens_ephemeral_port():
    """0 = 让系统给空端口；不能悄悄绑回 8730（Windows SO_REUSEADDR 会成功，
    请求却被正在跑的桌宠进程接走，测试等于测了个寂寞）。"""
    srv = overlay_server._make_server(0)
    try:
        port = srv.server_address[1]
        assert port > 0 and port != overlay_server.PORT
    finally:
        srv.server_close()


def test_send_survives_dead_socket():
    """_send 碰上已断开的连接不抛异常，只把连接标记成该关。"""
    handler = overlay_server.Handler.__new__(overlay_server.Handler)
    handler.close_connection = False
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET /tts?text=hi HTTP/1.1"
    handler._headers_buffer = []

    class DeadPipe:
        def write(self, data):
            raise ConnectionAbortedError(10053, "你的主机中的软件中止了一个已建立的连接。")

        def flush(self):
            raise ConnectionAbortedError(10053, "aborted")

    handler.wfile = DeadPipe()
    handler._send(503, b"tts offline", "text/plain")
    assert handler.close_connection is True


def test_handler_swallows_abort_raised_in_do_get(capsys):
    """响应写到一半发现对端走了：handle() 吞掉，服务照常接下一个请求。"""
    from http.server import ThreadingHTTPServer

    class Aborting(overlay_server.Handler):
        def do_GET(self):
            if self.path.startswith("/tts"):
                raise ConnectionAbortedError(10053, "你的主机中的软件中止了一个已建立的连接。")
            return super().do_GET()

    srv = ThreadingHTTPServer((overlay_server.HOST, 0), Aborting)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        sock = socket.create_connection(("127.0.0.1", port))
        sock.sendall(b"GET /tts?text=hi HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        assert sock.recv(64) == b""      # 服务端直接关连接：没有 traceback 的那条路
        sock.close()

        with _get(port, "/health") as r:  # 同一台服务还能干活
            assert r.status == 200
        assert "Traceback" not in capsys.readouterr().err
    finally:
        srv.shutdown()
        srv.server_close()


def test_client_hangup_mid_tts_is_silent(capsys, monkeypatch):
    """页面在合成中途被切走：服务端不打印 traceback，下一次请求照常。"""
    def slow_tts(text):
        time.sleep(0.3)
        return None

    monkeypatch.setattr(overlay_server, "tts_wav", slow_tts)
    srv, port = _serve()
    try:
        sock = socket.create_connection(("127.0.0.1", port))
        sock.sendall(b"GET /tts?text=hi HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        time.sleep(0.05)
        # SO_LINGER 0 → close 直接发 RST，模拟切场景掐断还在等的 fetch
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()
        time.sleep(0.8)

        with _get(port, "/health") as r:
            assert r.status == 200
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "ConnectionAbortedError" not in err
    finally:
        srv.shutdown()
        srv.server_close()


def test_tts_endpoint_offline_returns_503(monkeypatch):
    """/tts 合成不出来时回 503，服务不塌。"""
    monkeypatch.setattr(overlay_server, "tts_wav", lambda text: None)
    srv, port = _serve()
    try:
        try:
            with _get(port, "/tts?text=hi") as r:
                assert r.status == 200  # 不会走到这里
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            assert exc.read() == b"tts offline"
    finally:
        srv.shutdown()
        srv.server_close()


def test_game_start_endpoint_get_and_post(monkeypatch):
    """开局设置：GET 出声明 + 当前值，POST 清洗后保存。"""
    from dafeiyu.ui import studio

    seen = {}

    class FakeRuntime:
        def game_start(self):
            return {"ok": True, "game": "slay-the-spire-2",
                    "spec": {"options": [{"key": "character", "type": "choice",
                                          "choices": [{"value": "IRONCLAD"}]}]},
                    "values": {"character": "IRONCLAD"}}

        def save_game_start(self, body):
            seen.update(body)
            return {"ok": True, "game": "slay-the-spire-2",
                    "spec": {"options": []}, "values": body.get("values") or {}}

    monkeypatch.setattr(studio, "get_runtime", lambda: FakeRuntime())
    srv, port = _serve()
    try:
        with _get(port, "/api/game/start") as r:
            got = json.loads(r.read())
        assert got["ok"] is True and got["game"] == "slay-the-spire-2"
        assert got["values"] == {"character": "IRONCLAD"}
        assert got["spec"]["options"][0]["key"] == "character"

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/game/start",
            data=json.dumps({"values": {"character": "DEFECT", "ascension": 3}}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=5) as r:
            saved = json.loads(r.read())
        assert saved["ok"] is True
        assert seen["values"] == {"character": "DEFECT", "ascension": 3}
        assert saved["values"] == {"character": "DEFECT", "ascension": 3}
    finally:
        srv.shutdown()
        srv.server_close()


def test_bilibili_comment_endpoint_includes_posted_log(monkeypatch):
    """/api/bilibili/comment = 设置 + 已发评论账本，控制台一个接口就能出列表。"""
    from dafeiyu.ui import studio

    class FakeRuntime:
        def bilibili_comment_settings(self):
            return {"ok": True, "can_post": True, "enabled": True}

        def bilibili_video_comments(self):
            return {"ok": True, "count": 1, "items": [{
                "at": 1.0, "bvid": "BV1POST",
                "url": "https://www.bilibili.com/video/BV1POST",
                "title": "大肥鱼：今天也被捞了", "text": "同类看同类"}]}

    monkeypatch.setattr(studio, "get_runtime", lambda: FakeRuntime())
    srv, port = _serve()
    try:
        with _get(port, "/api/bilibili/comment") as r:
            payload = json.loads(r.read())
        assert payload["ok"] is True
        assert payload["posted"]["items"][0]["bvid"] == "BV1POST"
        assert payload["posted"]["items"][0]["text"] == "同类看同类"
    finally:
        srv.shutdown()
        srv.server_close()


def test_synth_failure_records_reason(monkeypatch):
    """引擎没起/超时要留下原因，控制台不该只有「合成失败」。"""
    class Boom:
        def open(self, req, timeout=0):
            raise ConnectionRefusedError(10061, "由于目标计算机积极拒绝，无法连接。")

    monkeypatch.setattr(overlay_server.urllib.request, "build_opener",
                        lambda *a, **k: Boom())
    monkeypatch.setattr(overlay_server, "_tts_stat", dict(overlay_server._tts_stat))
    assert overlay_server._synth("测试一句") is None
    assert "ConnectionRefusedError" in overlay_server._tts_stat["last_error"]


def test_tts_failure_counts_without_writing_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(overlay_server, "_tts_stat", dict(overlay_server._tts_stat))
    monkeypatch.setattr(overlay_server, "TTS_CACHE", str(tmp_path))
    monkeypatch.setattr(overlay_server, "_synth", lambda text: None)
    monkeypatch.setattr(overlay_server, "_kick_guard", lambda: None)
    base_fail = overlay_server._tts_stat["fail"]
    base_consec = overlay_server._tts_stat["consec_fail"]

    assert overlay_server.tts_wav("金币先揣兜里") is None
    assert overlay_server._tts_stat["fail"] == base_fail + 1
    assert overlay_server._tts_stat["consec_fail"] == base_consec + 1
    assert list(tmp_path.iterdir()) == []

def test_bound_port_reports_the_started_server_port():
    old = overlay_server._SERVER
    srv = overlay_server._make_server(0)
    try:
        overlay_server._SERVER = None
        assert overlay_server.bound_port() == 0
        overlay_server._SERVER = srv
        assert overlay_server.bound_port() == srv.server_address[1]
    finally:
        overlay_server._SERVER = old
        srv.server_close()
