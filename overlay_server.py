#!/usr/bin/env python3
"""Tiny local HTTP server for the transparent OBS commentary overlay."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = int(os.environ.get("STS2_OVERLAY_PORT", "8730"))
ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(ROOT, "overlay", "index.html")
ROUTE_PLAN = os.path.join(ROOT, "logs", "route_plan.json")

_lock = threading.Lock()
_comments: deque = deque(maxlen=12)
_seq = 0
_SPEAKER_RE = re.compile(r"(?:大肥鱼|DeepSeek|AI)\s*[:：]\s*", re.I)
_status = {
    "character": "铁甲战士",
    "ascension": 10,
    "floor": 0,
    "hp": 80,
    "max_hp": 80,
    "gold": 99,
    "screen": "MAIN_MENU",
}


def add_comment(payload: dict) -> dict:
    global _seq
    with _lock:
        _seq += 1
        c = {
            "id": _seq,
            "ts": time.time(),
            "tag": str(payload.get("tag") or "思路")[:12],
            "thought": _SPEAKER_RE.sub("", str(payload.get("thought") or "")).strip()[:220],
            "meme": _SPEAKER_RE.sub("", str(payload.get("meme") or "")).strip()[:220],
        }
        _comments.append(c)
        for key in ("character", "ascension", "floor", "hp", "max_hp", "gold", "screen"):
            if payload.get(key) is not None:
                _status[key] = payload[key]
        return c


def route_payload() -> dict:
    plan = None
    try:
        with open(ROUTE_PLAN, encoding="utf-8") as f:
            plan = json.load(f)
    except (OSError, ValueError):
        plan = None
    floor = None
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:8080/state", timeout=0.6) as r:
            st = json.load(r)
        floor = ((st.get("data") or {}).get("run") or {}).get("floor")
    except Exception:
        floor = None
    return {"ok": True, "floor": floor, "plan": plan}


def snapshot(after: int = 0) -> dict:
    with _lock:
        items = [c for c in _comments if c["id"] > after]
        return {"seq": _seq, "comments": items, "status": dict(_status)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                with open(INDEX, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"overlay missing", "text/plain")
        elif path.startswith("/state"):
            q = 0
            if "after=" in self.path:
                try:
                    q = int(self.path.split("after=", 1)[1].split("&", 1)[0])
                except ValueError:
                    q = 0
            self._send(
                200,
                json.dumps(snapshot(q), ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
        elif path.startswith("/assets/"):
            rel = os.path.normpath(path[len("/assets/"):]).replace("\\", "/")
            if rel.startswith("..") or os.path.isabs(rel):
                self._send(403, b"forbidden", "text/plain")
                return
            fp = os.path.join(ROOT, "overlay", "assets", rel)
            if os.path.isfile(fp):
                ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
                with open(fp, "rb") as f:
                    self._send(200, f.read(), ctype)
            else:
                self._send(404, b"asset not found", "text/plain")
        elif path == "/route":
            self._send(
                200,
                json.dumps(route_payload(), ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
        elif path == "/health":
            self._send(200, b'{"ok":true}', "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path == "/comment":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                self._send(400, b'{"ok":false}', "application/json")
                return
            c = add_comment(payload)
            self._send(
                200,
                json.dumps({"ok": True, "comment": c}, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
        elif self.path == "/reset":
            # Keep _seq monotonic: browser sources keep their own lastId and
            # would otherwise ignore new comments after a reset.
            with _lock:
                _comments.clear()
            self._send(200, b'{"ok":true}', "application/json")
        else:
            self._send(404, b"not found", "text/plain")


def main():
    os.makedirs(os.path.join(ROOT, "overlay"), exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"overlay server on http://{HOST}:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
