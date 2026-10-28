"""会话编排：把「游戏事件 / 一句台词」串成 性格 → 语音 → UI 的完整链路。

职责边界很清楚：
- 性格层决定「说什么」（Persona.compose / lint）
- 语音层决定「怎么发声」（VoiceRouter.speak，后台线程，绝不阻塞 UI）
- UI 层只负责显示（OverlayPublisher 推 /comment；页面/弹幕窗自己拉 /state）

任何一层挂了都不能让直播停：推送失败只记一笔，语音失败自动回退。
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..persona import Persona
from ..voice import build_router
from .bus import EventBus
from .config import LiveConfig
from .events import Topics


@dataclass
class SayResult:
    text: str = ""
    lines: list[str] = field(default_factory=list)
    ok: bool = False
    pushed: dict = field(default_factory=dict)
    lint: dict = field(default_factory=dict)
    voice: str = ""
    duration: float = 0.0
    voice_error: str = ""
    client_id: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "text": self.text, "lines": list(self.lines),
            "lint": self.lint, "pushed": self.pushed, "voice": self.voice,
            "duration": round(self.duration, 3), "voice_error": self.voice_error,
            "client_id": self.client_id,
        }


class OverlayPublisher:
    """把台词推给 overlay 服务（OBS 浏览器源和桌面弹幕窗共用同一份状态）。"""

    def __init__(self, url: str = "http://127.0.0.1:8730", timeout: float = 2.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> dict:
        import json
        import urllib.request

        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=self.timeout) as r:
                body = r.read()
            data = json.loads(body.decode("utf-8")) if body else {}
            return {"ok": True, **({"id": data.get("comment", {}).get("id")} if isinstance(data, dict) else {})}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def push(self, payload: dict) -> dict:
        return self._post("/comment", payload)

    def ping(self) -> dict:
        import json
        import urllib.request

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(self.url + "/health", timeout=0.8) as r:
                data = json.loads(r.read().decode("utf-8"))
            return {"ok": True, "clients": data.get("clients", 0),
                    "seq": data.get("seq", 0), "tts": data.get("tts", {})}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


class LiveSession:
    def __init__(
        self,
        config: LiveConfig | None = None,
        *,
        bus: EventBus | None = None,
        persona: Persona | None = None,
        voice=None,
        publisher: OverlayPublisher | None = None,
    ) -> None:
        self.config = config or LiveConfig.load()
        self.bus = bus or EventBus()
        self.persona = persona or Persona.load(self.config.persona)
        self.voice = voice if voice is not None else build_router(self.config)
        self.publisher = publisher or OverlayPublisher(self.config.overlay_url)
        self.game = None
        self.state: dict[str, Any] = {}
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._started_at = 0.0
        self._said = 0
        self._pushed = 0
        self._voice_ok = 0
        self._voice_fail = 0
        self._last_voice: dict = {}
        self._cond = threading.Condition()
        self._pending = 0

    # ---------------------------------------------------------------- 生命周期
    def start(self, game=None) -> "LiveSession":
        if self._worker and self._worker.is_alive():
            return self
        self._stop.clear()
        self._started_at = time.time()
        self._worker = threading.Thread(target=self._worker_loop, name="dafeiyu-voice", daemon=True)
        self._worker.start()
        self.bus.publish(Topics.SESSION_STARTED, {
            "persona": self.persona.card.id,
            "voice": self.config.voice,
            "game": game.game_id if game else self.config.game,
        }, source="session")
        if game is not None:
            self.attach(game)
        return self

    def attach(self, adapter) -> None:
        """接上游戏适配器（只读状态；出牌仍由兼容层 CLI/Agent 负责）。"""
        self.game = adapter
        try:
            if adapter.available():
                self.state = adapter.snapshot().to_dict()
                self.bus.publish(Topics.GAME_CONNECTED, {
                    "game_id": adapter.game_id, "name": adapter.display_name,
                }, source=adapter.game_id)
                return
        except Exception:
            pass
        self.bus.publish(Topics.GAME_ERROR, {"game_id": getattr(adapter, "game_id", ""),
                                             "error": "游戏/兼容层不在线"}, source="session")

    def stop(self, drain: bool = True) -> None:
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._queue.put(None)
            if drain:
                self._worker.join(timeout=30)
        self.bus.publish(Topics.SESSION_STOPPED, {"uptime": round(self.uptime(), 1)},
                         source="session")

    def uptime(self) -> float:
        return max(0.0, time.time() - self._started_at) if self._started_at else 0.0

    # ---------------------------------------------------------------- 状态
    def update_state(self, data: dict) -> None:
        for key in ("screen", "floor", "hp", "max_hp", "gold", "character",
                    "ascension", "in_combat", "turn"):
            if key in data and data[key] is not None:
                self.state[key] = data[key]
        self.bus.publish(Topics.GAME_STATE, dict(self.state), source="session")

    def _status_fields(self) -> dict:
        keys = ("character", "ascension", "floor", "hp", "max_hp", "gold", "screen")
        return {k: self.state[k] for k in keys if self.state.get(k) is not None}

    # ---------------------------------------------------------------- 说话
    def say(
        self,
        thought: str = "",
        meme: str = "",
        *,
        text: str = "",
        source: str = "agent",
        push: bool | None = None,
        speak: bool = True,
        voice: str | None = None,
        wait_voice: float = 0.0,
    ) -> SayResult:
        """一句台词走完整链路：拼句 → lint → 推 UI → 后台配音。"""
        if not self._worker:
            self.start()
        report = self.persona.render(thought, meme) if not text else None
        final = (text or (report or {}).get("text") or "").strip()
        if not final:
            return SayResult(text="", ok=False, lint={"ok": False, "issues": [
                {"level": "FAIL", "message": "空台词", "rule": "empty"}]})
        issues = report["lint"]["issues"] if report else [
            i.to_dict() for i in self.persona.lint(final)]
        lint_report = report["lint"] if report else self.persona.lint_report(final)

        self._said += 1
        cid = uuid.uuid4().hex[:12]
        payload = {
            "thought": thought, "meme": meme, "speech": final,
            "client_id": cid, **self._status_fields(),
        }
        self.bus.publish(Topics.COMMENTARY_SAY, {
            "text": final, "lines": (report or {}).get("lines") or [final],
            "thought": thought, "meme": meme, "client_id": cid,
            "lint": lint_report, "source": source,
        }, source="session")

        do_push = self.config.push_overlay if push is None else push
        pushed: dict = {"ok": None, "skipped": True}
        if do_push:
            pushed = self.publisher.push(payload)
            if pushed.get("ok"):
                self._pushed += 1
            self.bus.publish(Topics.COMMENTARY_PUSHED, {"ok": bool(pushed.get("ok")),
                                                        "client_id": cid, "error": pushed.get("error", "")},
                             source="session")

        result = SayResult(text=final, lines=(report or {}).get("lines") or [final],
                           ok=bool(lint_report.get("ok")), pushed=pushed,
                           lint=lint_report, client_id=cid)
        if speak and self.config.voice not in ("off", "none", "silent"):
            self._enqueue_voice(final, voice or self.config.voice, cid)
            if wait_voice > 0:
                self.wait_idle(wait_voice)
        self.persona.note_sent(final)
        return result

    # ---------------------------------------------------------------- 配音线程
    def _enqueue_voice(self, text: str, voice: str, cid: str) -> None:
        with self._cond:
            self._pending += 1
        self._queue.put((text, voice, cid))

    def _worker_loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                text, voice, cid = job
                self.bus.publish(Topics.VOICE_STARTED, {"text": text, "voice": voice,
                                                        "client_id": cid}, source="voice")
                try:
                    result = self.voice.speak(text, voice)
                except Exception as exc:  # 任何实现崩了都只算一次失败
                    from ..voice.base import VoiceResult
                    result = VoiceResult(voice="", error=f"{type(exc).__name__}: {exc}")
                if result.ok:
                    self._voice_ok += 1
                    self._last_voice = {"voice": result.voice, "duration": round(result.duration, 3),
                                        "cached": result.cached, "client_id": cid}
                    self.bus.publish(Topics.VOICE_READY, {
                        "voice": result.voice, "duration": round(result.duration, 3),
                        "cached": result.cached, "bytes": len(result.wav or b""),
                        "client_id": cid,
                    }, source="voice")
                else:
                    self._voice_fail += 1
                    self._last_voice = {"voice": "", "error": result.error, "client_id": cid}
                    self.bus.publish(Topics.VOICE_FAILED, {"error": result.error,
                                                           "client_id": cid}, source="voice")
            finally:
                with self._cond:
                    self._pending = max(0, self._pending - 1)
                    self._cond.notify_all()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """等配音队列排空（CLI/演示用；直播主链路不等）。"""
        deadline = time.time() + timeout
        with self._cond:
            while self._pending > 0:
                left = deadline - time.time()
                if left <= 0:
                    return False
                self._cond.wait(left)
        return True

    # ---------------------------------------------------------------- 观测
    def status(self) -> dict:
        publisher = self.publisher.ping() if self.publisher else {"ok": False}
        game_status = {}
        if self.game is not None:
            try:
                game_status = self.game.status()
            except Exception as exc:
                game_status = {"error": str(exc)}
        return {
            "session": {
                "started": bool(self._started_at),
                "uptime": round(self.uptime(), 1),
                "said": self._said,
                "pushed": self._pushed,
                "voice_ok": self._voice_ok,
                "voice_fail": self._voice_fail,
                "pending_voice": self._pending,
                "last_voice": dict(self._last_voice),
            },
            "config": self.config.to_dict(),
            "persona": self.persona.to_dict(),
            "voice": self.voice.status(),
            "publisher": publisher,
            "game": {
                "adapter": getattr(self.game, "game_id", "") if self.game else "",
                "state": dict(self.state),
                "status": game_status,
            },
            "bus": self.bus.stats(),
        }


__all__ = ["LiveSession", "OverlayPublisher", "SayResult"]
