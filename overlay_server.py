#!/usr/bin/env python3
"""Tiny local HTTP server for the transparent OBS commentary overlay."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = int(os.environ.get("STS2_OVERLAY_PORT", "8730"))
ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(ROOT, "overlay", "index.html")
ROUTE_PLAN = os.path.join(ROOT, "logs", "route_plan.json")

# ---------------------------------------------------------------- 曼波配音（MamboTTS / GPT-SoVITS）
# 浏览器源拿到 /tts 的 WAV 自己播 → OBS 录制里就带配音。
TTS_URL = os.environ.get("STS2_TTS_URL", "http://127.0.0.1:9880")
TTS_REF = os.environ.get("STS2_TTS_REF", "")
TTS_PROMPT = os.environ.get(
    "STS2_TTS_PROMPT", "大家好，欢迎来到我的频道，今天给大家分享一个有趣的内容"
)
TTS_CUT = "，。？！；：、…,.;?!"
TTS_CACHE = os.path.join(ROOT, "logs", "tts_cache")
TTS_TIMEOUT = float(os.environ.get("STS2_TTS_TIMEOUT", "60"))

# 一回合可能好几路同时要同一句（预热 / OBS 页面 / 直播姬页面）：
# 串行进引擎，后到的等锁，醒来先查磁盘缓存。绝不并发压 MamboTTS。
_tts_lock = threading.Lock()
_tts_stat = {"ok": 0, "fail": 0, "last_ok": 0.0, "last_fail": 0.0,
             "last_ms": 0, "consec_fail": 0}
_guard = {"t": 0.0, "runs": 0}


def _kick_guard() -> None:
    """连续失败才拉起看护进程；冷却 3 分钟，避免反复杀引擎。"""
    if _tts_stat["consec_fail"] < 2:
        return
    now = time.time()
    if now - _guard["t"] < 180:
        return
    _guard["t"] = now
    _guard["runs"] += 1
    try:
        subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "tools", "tts_guard.py"), "--auto"],
            cwd=ROOT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        print("[tts] 连续合成失败，已拉起 tts_guard 重启引擎", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[tts] tts_guard 拉起失败: {e}", file=sys.stderr, flush=True)


def _synth(text: str) -> bytes | None:
    payload = {
        "text": text,
        "text_language": "zh",
        "speed": float(os.environ.get("STS2_TTS_SPEED", "1.0")),
        "cut_punc": TTS_CUT,
    }
    if TTS_REF:
        payload["refer_wav_path"] = TTS_REF
        payload["prompt_text"] = TTS_PROMPT
        payload["prompt_language"] = "zh"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        TTS_URL.rstrip("/") + "/",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(req, timeout=TTS_TIMEOUT) as r:
            data = r.read()
    except Exception:
        return None
    if len(data) < 44 or data[:4] != b"RIFF":
        return None
    return data


def tts_wav(text: str) -> bytes | None:
    """调本地 MamboTTS 合成一句，带磁盘缓存；并发时串行，后到的直接吃缓存。"""
    text = (text or "").strip()[:200]
    if not text:
        return None
    key = hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]
    path = os.path.join(TTS_CACHE, key + ".wav")
    with _tts_lock:
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            pass
        t0 = time.time()
        data = _synth(text)
        if data:
            try:
                os.makedirs(TTS_CACHE, exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
            except OSError:
                pass
            _tts_stat.update(ok=_tts_stat["ok"] + 1, last_ok=time.time(),
                             last_ms=int((time.time() - t0) * 1000), consec_fail=0)
            return data
        _tts_stat.update(fail=_tts_stat["fail"] + 1, last_fail=time.time(),
                         consec_fail=_tts_stat["consec_fail"] + 1)
        print(f"[tts] 合成失败 {time.time() - t0:.1f}s: {text[:24]}", file=sys.stderr, flush=True)
        _kick_guard()
        return None

_lock = threading.Lock()
_comments: deque = deque(maxlen=12)
_seq = 0
SEQ_PATH = os.path.join(ROOT, "logs", "overlay_seq.json")


def _load_seq() -> int:
    """手牌 id 必须跨重启保持递增：页面按 id > lastId 过滤，服务器一旦从 0 重来，
    已连着的页面（直播姬里的那个不会自动刷新）就把后面所有台词当成旧的，彻底哑掉。"""
    try:
        with open(SEQ_PATH, encoding="utf-8") as f:
            return int(json.load(f).get("seq") or 0)
    except Exception:
        return 0


def _save_seq() -> None:
    try:
        os.makedirs(os.path.dirname(SEQ_PATH), exist_ok=True)
        tmp = SEQ_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"seq": _seq, "ts": time.time()}, f)
        os.replace(tmp, SEQ_PATH)
    except Exception:
        pass


_seq = _load_seq()
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
            "speech": str(payload.get("speech") or "")[:300],
        }
        _comments.append(c)
        _save_seq()
        for key in ("character", "ascension", "floor", "hp", "max_hp", "gold", "screen"):
            if payload.get(key) is not None:
                _status[key] = payload[key]
        snap = {"seq": _seq, "comments": [c], "status": dict(_status)}
    publish(snap)
    return c


# ---- 推送：出话即时到达页面，不再等下一次轮询（轮询最慢会晚 1 秒，字幕就落在出牌之后）
# 每个订阅记角色：record=OBS 录制页（只进录像）、live=直播姬页（本地能听到）。
# 本地只允许**一个** live 页面出声（audio=true），其余 live 页只挂字幕——
# 多个页面同时播同一句，就是观众听到的「回音 / 同样台词说两次」。
_subscribers: dict[queue.Queue, str] = {}
_sub_lock = threading.Lock()
_live_owner = {"q": None}
_audio_ts = {"record": 0.0, "live": 0.0, "window": 0.0}   # 上一次「真的出声」的时刻（各端自报）


def _pick_live_owner_locked() -> None:
    cur = _live_owner.get("q")
    if cur in _subscribers and _subscribers[cur] == "live":
        return
    _live_owner["q"] = next((q for q, r in _subscribers.items() if r == "live"), None)


def publish(payload: dict) -> None:
    with _sub_lock:
        live = list(_subscribers.items())
        owner = _live_owner.get("q")
    n_audio = 0
    for q, role in live:
        body = payload
        if role == "live":
            body = dict(payload)
            body["audio"] = (q is owner)
            n_audio += 1 if body["audio"] else 0
        try:
            q.put_nowait(body)
        except Exception:
            pass
    if payload.get("comments"):
        n_live = sum(1 for _q, r in live if r == "live")
        print(f"[push] clients={len(live)} live={n_live} live_audio={n_audio}", flush=True)


def _role_counts() -> dict:
    with _sub_lock:
        roles = list(_subscribers.values())
    out = {}
    for r in roles:
        out[r] = out.get(r, 0) + 1
    return out


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
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _serve_events(self):
        """SSE：新台词一产生就推下去，页面不用轮询。"""
        qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
        role = ((qs.get("role") or [""])[0] or "").strip().lower()
        if role not in ("record", "live"):
            # 旧页面（直播姬里那个 browser_source）不会带 role：从 Referer 认出来
            ref = str(self.headers.get("Referer") or "")
            role = "live" if "live=1" in ref else "record"
        q: queue.Queue = queue.Queue(maxsize=64)
        with _sub_lock:
            _subscribers[q] = role
            _pick_live_owner_locked()
            is_owner = (role == "live" and _live_owner.get("q") is q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            init = snapshot(0)
            init["comments"] = init["comments"][-1:]
            init["init"] = True
            if role == "live":
                init["audio"] = is_owner
            self.wfile.write(("data: " + json.dumps(init, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(
                    ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")
                )
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass
        finally:
            with _sub_lock:
                _subscribers.pop(q, None)
                _pick_live_owner_locked()

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
            # 页面游标跑在服务端前面（旧页面熬过了重启）时把 seq 顶上去：
            # 否则它永远等 id > lastId，所有新台词都被当成旧的，直播里直接哑掉。
            global _seq
            if q > _seq:
                _seq = q
                _save_seq()
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
        elif path == "/tts":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            text = (qs.get("text") or [""])[0]
            # 页面要合成 = 它马上要播。直播姬那页（旧 JS 不会自报 audio）靠这个认出来，
            # 主播窗据此让位，避免同一句窗口和页面各播一遍（回声）。
            if "live=1" in str(self.headers.get("Referer") or ""):
                _audio_ts["live"] = time.time()
            print(f"[tts] 请求 {len(text)} 字: {text[:20]}", flush=True)
            wav = tts_wav(text)
            if wav:
                print(f"[tts] 返回 {len(wav) // 1024} KB", flush=True)
                self._send(200, wav, "audio/wav")
            else:
                print("[tts] 503 offline", flush=True)
                self._send(503, b"tts offline", "text/plain")
        elif path == "/events":
            self._serve_events()
        elif path == "/health":
            roles = _role_counts()
            body = {"ok": True, "clients": sum(roles.values()), "roles": roles,
                    "tts": dict(_tts_stat), "guard": dict(_guard),
                    "seq": _seq, "audio_ts": dict(_audio_ts)}
            self._send(200, json.dumps(body, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif path == "/mark":
            # 页面自报「弹窗渲染/音频起播」的时间戳，用来对齐/核对谁在响（只写日志）
            try:
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                ev = (qs.get("ev") or ["?"])[0]
                role = (qs.get("role") or [""])[0] or "-"
                if ev == "audio" and role in _audio_ts:
                    _audio_ts[role] = time.time()
                with open(os.path.join(ROOT, "logs", "av_marks.log"), "a", encoding="utf-8") as f:
                    f.write(f"{time.time():.4f}\t{ev}\t{role}\n")
            except Exception:
                pass
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
