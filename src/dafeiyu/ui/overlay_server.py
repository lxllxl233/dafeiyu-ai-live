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

# 布局：src/dafeiyu/ui/overlay_server.py
_HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(os.path.dirname(_HERE))       # src/
ROOT = os.path.dirname(SRC_DIR)                          # 仓库根
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from dafeiyu.games.catalog import env_value  # noqa: E402

HOST = "127.0.0.1"
PORT = int(env_value("OVERLAY_PORT", "8730"))
UI_DIR = os.path.join(ROOT, "ui")
INDEX = os.path.join(UI_DIR, "overlay", "index.html")
STUDIO = os.path.join(UI_DIR, "studio", "index.html")
ROUTE_PLAN = os.path.join(ROOT, "logs", "route_plan.json")

# ---------------------------------------------------------------- 曼波配音（MamboTTS / GPT-SoVITS）
# 浏览器源拿到 /tts 的 WAV 自己播 → OBS 录制里就带配音。
TTS_URL = env_value("TTS_URL", "http://127.0.0.1:9880")
TTS_REF = env_value("TTS_REF", "")
TTS_PROMPT = env_value(
    "TTS_PROMPT", "大家好，欢迎来到我的频道，今天给大家分享一个有趣的内容"
)
TTS_CUT = "，。？！；：、…,.;?!"
TTS_CACHE = os.path.join(ROOT, "logs", "tts_cache")
TTS_TIMEOUT = float(env_value("TTS_TIMEOUT", "60"))

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
        "speed": float(env_value("TTS_SPEED", "1.0")),
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
_log_lock = threading.Lock()
_comments: deque = deque(maxlen=12)
_decisions: deque = deque(maxlen=30)
_logs: deque = deque(maxlen=80)
_usage = {"input": 0, "output": 0, "total": 0, "calls": 0, "updated": 0.0}
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def estimate_tokens(text: str) -> int:
    """本地 token 估算：中文按字、英文/数字按词；只用于控制台观察，不是账单。"""
    return len(_TOKEN_RE.findall(str(text or "")))


def log_event(level: str, message: str) -> None:
    """控制台日志环：只留最近 80 条，不落盘、不挡主流程。"""
    try:
        with _log_lock:
            _logs.append({"ts": time.time(), "level": str(level)[:8],
                          "msg": str(message)[:240]})
    except Exception:
        pass


def _record_usage(thought: str, meme: str, speech: str) -> dict:
    """调用方必须已持有 `_lock`。"""
    delta = {"input": estimate_tokens(f"{thought}\n{meme}"),
             "output": estimate_tokens(speech), "calls": 1}
    delta["total"] = delta["input"] + delta["output"]
    for key in ("input", "output", "total", "calls"):
        _usage[key] += int(delta[key])
    _usage["updated"] = time.time()
    return delta
_seq = 0
# 日志/序号目录可重定向：测试用临时目录，别把真直播的 seq 和观测日志搅乱。
LOG_DIR = env_value("OVERLAY_LOG_DIR", "") or os.path.join(ROOT, "logs")
SEQ_PATH = os.path.join(LOG_DIR, "overlay_seq.json")


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
    "character": "演示角色",
    "ascension": 10,
    "floor": 0,
    "hp": 80,
    "max_hp": 80,
    "gold": 99,
    "screen": "MAIN_MENU",
}

# ---- A/V 观测：只落盘，不改变任何显示/播放时序。
# av_marks.log 保持旧的 3 列前缀（time/event/role），第 4 列起才是 id；
# av_marks.jsonl 给新脚本用，避免再靠猜解析。
AV_MARKS = os.path.join(LOG_DIR, "av_marks.log")
AV_MARKS_JSONL = os.path.join(LOG_DIR, "av_marks.jsonl")
_av_lock = threading.Lock()


def _av_num(v):
    """把 query 里的数字尽量变成 number，方便直接算差值。"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)[:80]


def _av_append(row: dict, tsv: str | None = None) -> None:
    try:
        os.makedirs(os.path.dirname(AV_MARKS_JSONL), exist_ok=True)
        with _av_lock:
            if tsv:
                with open(AV_MARKS, "a", encoding="utf-8") as f:
                    f.write(tsv)
            with open(AV_MARKS_JSONL, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---- 思考状态：producer 明确上报「Agent 正在算这一步」，不许自己编一个计时器
_thinking = {"on": False, "since": 0.0, "beat": 0.0, "seq": 0, "note": ""}


def thinking_state() -> dict:
    with _lock:
        return dict(_thinking)


def set_thinking(on: bool, note: str = "") -> dict:
    """上报思考状态（on/off）。`seq` 由服务端发，客户端按序号丢弃迟到事件。

    重复上报同一个状态**不涨 seq**、只续 beat：窗口/页面据此知道「上报方还活着」，
    但状态本身不变 —— 所以不会出现「没在算却显示思考中」的假状态。
    """
    with _lock:
        now = time.time()
        changed = bool(on) != _thinking["on"]
        _thinking["beat"] = now
        if changed:
            _thinking["on"] = bool(on)
            _thinking["since"] = now
            _thinking["seq"] = int(_thinking["seq"]) + 1
            _thinking["note"] = str(note or "")[:80]
        elif note:
            _thinking["note"] = str(note or "")[:80]
        snap = {"seq": _seq, "comments": [], "status": dict(_status),
                "thinking": dict(_thinking), "playback": audio_state()}
        state = dict(_thinking)
    if changed:
        log_event("think", f"{'开始' if on else '结束'}思考 · {str(note or '')[:60]}")
        publish(snap)
    return state


# ---------------------------------------------------------------- 音频生命周期
# 「主播正在说话吗」必须由**真实播放**决定，不能由字数估时长决定：
# 实测（logs/av_client.jsonl + logs/av_marks.jsonl）从发布到真的出声要
# 2–6 秒（曼波合成 0.57+0.057*字数 秒，队里还有上一句时更久），而字数估计
# 从发布时刻起算——所以旧实现 40/40 句都提前切了「思考中」，中位早 4.2s。
#
# 这里记录页面（OBS/直播姬 browser source）自报的 /mark 事件：
#   ev=audio     → 这一句真的起播（带 dur），从「排队」移到「正在播」
#   ev=audio_end → 这一句播完
#   ev=skip      → 页面判定过期没播（要让队列长度回落，否则永远显示在说话）
#   ev=silent    → 只挂字幕不出声
# pending = 已发布但还没起播的台词（合成中/排队中）。它是**真实队列**，
# 主播窗据此在合成空档里维持说话状态，而不是靠定时器硬撑。
AUDIO_PENDING_TTL = float(env_value("AUDIO_PENDING_TTL", "15"))
_audio_lock = threading.Lock()
_audio = {"seq": 0, "cid": "", "role": "", "since": 0.0, "dur": 0.0, "pending": {}}


def _audio_tick() -> None:
    """内部：涨 seq（只在真实状态变化时调用）。"""
    _audio["seq"] = int(_audio["seq"]) + 1


def audio_state() -> dict:
    """给窗口看的音频快照：是否在播、还剩多久、队列还有几条。"""
    now = time.time()
    with _audio_lock:
        pend = _audio["pending"]
        for cid, at in list(pend.items()):
            if now - at > AUDIO_PENDING_TTL:
                pend.pop(cid, None)
        playing = bool(_audio["cid"]) and now < _audio["since"] + _audio["dur"]
        left = max(0.0, _audio["since"] + _audio["dur"] - now) if playing else 0.0
        if not playing and _audio["cid"] and now >= _audio["since"] + _audio["dur"]:
            _audio["cid"] = ""
        return {
            "seq": int(_audio["seq"]),
            "playing": playing,
            "remaining": round(left, 3),
            "dur": round(float(_audio["dur"]), 3),
            "cid": _audio["cid"],
            "role": _audio["role"],
            "pending": len(pend),
            "speaking": bool(playing or pend),
        }


def audio_mark(ev: str, cid: str = "", role: str = "", dur: float = 0.0) -> dict:
    """页面/OBS 自报的播放事件落到状态机。

    只改状态、涨 seq，不主动 publish：主播窗是按 /state 轮询的（0.35s），
    推一帧完整快照反而会把历史台词重复塞给页面。
    """
    now = time.time()
    with _audio_lock:
        pend = _audio["pending"]
        if ev == "audio":
            pend.pop(cid, None)
            if dur > 0:
                _audio.update(cid=cid, role=role, since=now, dur=float(dur))
                _audio_tick()
        elif ev in ("audio_end", "skip", "silent"):
            had = cid in pend
            pend.pop(cid, None)
            if ev == "audio_end" and (not cid or cid == _audio["cid"]):
                _audio.update(cid="", role="", since=0.0, dur=0.0)
                had = True
            if had:
                _audio_tick()
    return audio_state()


def audio_enqueue(cid: str) -> None:
    """新台词发布：记进待播队列（合成 + 排队都算在途）。"""
    if not cid:
        return
    with _audio_lock:
        _audio["pending"][cid] = time.time()
        _audio_tick()


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
            # 客户端关联 id：把游戏的 prewarm/publish 和页面 bubble/audio 串起来。
            "client_id": str(payload.get("client_id") or "")[:64],
        }
        _comments.append(c)
        usage = _record_usage(c["thought"], c["meme"], c["speech"])
        c["usage"] = usage
        decision = {
            "id": c["id"], "ts": c["ts"], "thought": c["thought"], "meme": c["meme"],
            "speech": c["speech"], "tag": c["tag"], "usage": usage,
            "thinking": bool(_thinking.get("on")), "note": str(_thinking.get("note") or ""),
        }
        _decisions.append(decision)
        _save_seq()
        for key in ("character", "ascension", "floor", "hp", "max_hp", "gold", "screen"):
            if payload.get(key) is not None:
                _status[key] = payload[key]
        snap = {"seq": _seq, "comments": [c], "status": dict(_status)}
    log_event("say", f"#{c['id']} {c['speech'][:80]} · tokens={usage['total']}")
    _av_append(
        {
            "t": c["ts"],
            "ev": "comment",
            "id": c["id"],
            "cid": c["client_id"],
            "tag": c["tag"],
            "speech": c["speech"],
        }
    )
    if c["speech"]:
        # 有台词 = 一定会有一次播放（页面出声，或窗口补位）→ 记进待播队列。
        audio_enqueue(c["client_id"])
        snap["playback"] = audio_state()
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
        return {"seq": _seq, "comments": items, "status": dict(_status),
                "thinking": dict(_thinking), "playback": audio_state(),
                "ui_rev": _ui_rev()}


def _ui_rev() -> str:
    """前端版本号（index.html 的 mtime+大小）：页面据此自己刷新，避免 OBS 缓存旧 JS。"""
    try:
        st = os.stat(INDEX)
        return f"{int(st.st_mtime)}-{st.st_size}"
    except OSError:
        return ""


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
        global _seq
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                with open(INDEX, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"overlay missing", "text/plain")
        elif path in ("/studio", "/studio/", "/studio/index.html"):
            try:
                with open(STUDIO, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"studio missing", "text/plain")
        elif path == "/api/studio":
            try:
                from dafeiyu.ui.studio import get_runtime

                roles = _role_counts()
                with _lock:
                    telemetry = {
                        "decisions": list(_decisions),
                        "logs": list(_logs),
                        "usage": dict(_usage),
                    }
                payload = get_runtime().payload({
                    "clients": sum(roles.values()), "roles": roles,
                    "seq": _seq, "thinking": dict(_thinking), "playback": audio_state(),
                    "comments": snapshot(0).get("comments", [])[-5:],
                    **telemetry,
                })
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                                           ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
        elif path == "/api/config":
            try:
                from dafeiyu.ui.studio import get_runtime

                body = json.dumps({"ok": True, "config": get_runtime().config_payload()},
                                  ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                                           ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
        elif path == "/api/games":
            try:
                from dafeiyu.ui.studio import get_runtime

                games = get_runtime()._games(force=True)  # noqa: SLF001 控制台手动刷新用
                self._send(200, json.dumps({"ok": True, "games": games}, ensure_ascii=False)
                           .encode("utf-8"), "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
                           .encode("utf-8"), "application/json; charset=utf-8")
        elif path.startswith("/state"):
            q = 0
            if "after=" in self.path:
                try:
                    q = int(self.path.split("after=", 1)[1].split("&", 1)[0])
                except ValueError:
                    q = 0
            # 页面游标跑在服务端前面（旧页面熬过了重启）时把 seq 顶上去：
            # 否则它永远等 id > lastId，所有新台词都被当成旧的，直播里直接哑掉。
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
            fp = os.path.join(UI_DIR, "overlay", "assets", rel)
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
                    "seq": _seq, "audio_ts": dict(_audio_ts),
                    "thinking": dict(_thinking), "playback": audio_state()}
            self._send(200, json.dumps(body, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif path == "/mark":
            # 页面自报「弹窗渲染/音频起播」的时间戳，用来对齐/核对谁在响（只写日志）
            try:
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                ev = (qs.get("ev") or ["?"])[0][:32]
                role = (qs.get("role") or [""])[0] or "-"
                mid = (qs.get("id") or [""])[0][:32]
                cid = (qs.get("cid") or [""])[0][:64]
                t = time.time()
                if ev == "audio" and role in _audio_ts:
                    _audio_ts[role] = t
                row = {"t": t, "ev": ev, "role": role,
                       "id": _av_num(mid), "cid": cid}
                for key in ("page_t", "ctx", "base", "out", "dur", "start_t", "ver", "why", "age"):
                    val = (qs.get(key) or [""])[0]
                    if val:
                        row[key] = _av_num(val)
                _av_append(row, tsv=f"{t:.4f}\t{ev}\t{role}\t{mid or '-'}\n")
                # 真实播放生命周期：窗口的「说话中/思考中」靠它，不靠字数估。
                if ev in ("audio", "audio_end", "skip", "silent"):
                    audio_mark(ev, cid=cid, role=role,
                               dur=float(row.get("dur") or 0.0))
            except Exception:
                pass
            self._send(200, b'{"ok":true}', "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path == "/api/config":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n) or b"{}")
                from dafeiyu.ui.studio import get_runtime

                result = get_runtime().update_config(payload if isinstance(payload, dict) else {})
                body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            except Exception as exc:
                self._send(400, json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                                           ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            return
        if self.path == "/api/say":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                self._send(400, b'{"ok":false}', "application/json")
                return
            try:
                from dafeiyu.ui.studio import get_runtime

                result = get_runtime().say(payload, add_comment)
                body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                                           ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            return
        if self.path == "/api/bili/send":
            try:
                n = int(self.headers.get("Content-Length", "0") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:
                self._send(400, b'{"ok":false,"error":"bad json"}', "application/json")
                return
            try:
                from dafeiyu.ui.studio import get_runtime

                text = str((payload or {}).get("text") or "")
                result = get_runtime().send_to_room(text)
                self._send(200, json.dumps(result, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            except ValueError as exc:
                self._send(400, json.dumps({"ok": False, "error": str(exc)},
                                           ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"ok": False, "error": str(exc)},
                                           ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            return
        if self.path == "/api/play/start":
            try:
                from dafeiyu.agent.play import start_autoplay

                n = int(self.headers.get("Content-Length", "0") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                body = body if isinstance(body, dict) else {}
                result = start_autoplay(mode=body.get("mode"), pace=body.get("pace"))
                code = 200 if result.get("ok") else 503
                self._send(code, json.dumps(result, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"ok": False, "error": str(exc)},
                                           ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            return
        if self.path == "/api/play/stop":
            try:
                from dafeiyu.agent.play import stop_autoplay

                self._send(200, json.dumps(stop_autoplay(), ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"ok": False, "error": str(exc)},
                                           ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            return
        if self.path == "/api/agent/run":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n) or b"{}")
                from dafeiyu.agent.graph import run_turn
                from dafeiyu.agent.tools import DemoPlanner, DemoTools

                result = run_turn(
                    str(payload.get("goal") or "控制台演示：观察 → 规划 → 验算 → 执行"),
                    tools=DemoTools(), planner=DemoPlanner(), mode="studio",
                    trace_path=None,
                )
                body = {k: v for k, v in result.items() if k not in ("context", "plan")}
                self._send(200, json.dumps(body, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"ok": False, "error": str(exc)},
                                           ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            return
        if self.path == "/api/game/launch":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n) or b"{}")
                from dafeiyu.ui.studio import get_runtime

                result = get_runtime().launch_game(
                    str(payload.get("game") or "").strip() or None)
                code = 200 if result.get("ok") else 503
                self._send(code, json.dumps(result, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
                           .encode("utf-8"), "application/json; charset=utf-8")
            return
        if self.path == "/api/voice/start":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n) or b"{}")
                from dafeiyu.ui.studio import get_runtime

                result = get_runtime().start_voice(str(payload.get("voice") or "mambo"))
                code = 200 if result.get("ok") else 503
                self._send(code, json.dumps(result, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
                           .encode("utf-8"), "application/json; charset=utf-8")
            return
        if self.path == "/api/voice":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                self._send(400, b'{"ok":false}', "application/json")
                return
            text = str(payload.get("text") or "").strip()[:200]
            voice = payload.get("voice") or None
            if not text:
                self._send(400, b'{"ok":false,"error":"empty"}', "application/json")
                return
            try:
                from dafeiyu.ui.studio import get_runtime

                runtime = get_runtime()
                # 曼波未就绪时，点「试听」等于点「启动」：先把引擎拉起来，
                # 本次返回 202 让前端提示稍后重试，不让用户以为没反应。
                if voice == "mambo":
                    mambo = runtime.voice.voices.get("mambo")
                    if mambo is not None and not mambo.available():
                        started = runtime.start_voice("mambo")
                        if not started.get("online"):
                            code = 202 if started.get("starting") else 503
                            self._send(code, json.dumps(
                                {"ok": False, **started,
                                 "error": started.get("message") or "曼波引擎未就绪"},
                                ensure_ascii=False).encode("utf-8"),
                                "application/json; charset=utf-8")
                            return
                result = runtime.audio(text, voice)
                if result.ok:
                    self._send(200, result.wav, "audio/wav")
                else:
                    self._send(503, json.dumps({"ok": False, "error": result.error},
                                               ensure_ascii=False).encode("utf-8"),
                               "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
                           .encode("utf-8"), "application/json; charset=utf-8")
            return
        if self.path == "/comment":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                self._send(400, b'{"ok":false}', "application/json")
                return
            c = add_comment(payload)
            # 发布成功后**由服务端预热配音**（后台线程，常驻进程活到最后）：
            # 客户端 CLI 是短命进程，它自己预热要么阻塞发布 2-4 秒（观众看到「卡好久才说话」），
            # 要么在退出时被掐死。放在这里两头都占：字幕秒出，缓存照样填好，
            # 页面 / 主播窗随后取 /tts 命中缓存即可。
            speech = str((c or {}).get("speech") or "").strip()
            if speech:
                threading.Thread(target=tts_wav, args=(speech,), daemon=True).start()
            self._send(
                200,
                json.dumps({"ok": True, "comment": c}, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
        elif self.path == "/thinking":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                self._send(400, b'{"ok":false}', "application/json")
                return
            st = set_thinking(bool(payload.get("on")), str(payload.get("note") or ""))
            self._send(200, json.dumps({"ok": True, "thinking": st}, ensure_ascii=False)
                       .encode("utf-8"), "application/json; charset=utf-8")
        elif self.path == "/reset":
            # Keep _seq monotonic: browser sources keep their own lastId and
            # would otherwise ignore new comments after a reset.
            with _lock:
                _comments.clear()
            self._send(200, b'{"ok":true}', "application/json")
        else:
            self._send(404, b"not found", "text/plain")


def _make_server(port: int | None = None) -> ThreadingHTTPServer:
    os.makedirs(os.path.join(UI_DIR, "overlay"), exist_ok=True)
    return ThreadingHTTPServer((HOST, port or PORT), Handler)


def start_background(port: int | None = None) -> ThreadingHTTPServer | None:
    """给 main.py 用：端口空就起后台服务；被占用返回 None（不抢正在跑的 studio）。"""
    try:
        srv = _make_server(port)
    except OSError as exc:
        print(f"overlay server 未启动（{exc}）", flush=True)
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"overlay server on http://{HOST}:{port or PORT}", flush=True)
    return srv


def main(port: int | None = None):
    srv = _make_server(port)
    print(f"overlay server on http://{HOST}:{port or PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
