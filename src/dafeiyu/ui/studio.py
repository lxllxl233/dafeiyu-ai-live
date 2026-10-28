"""直播姬控制台后端：/api/studio、/api/say、/api/voice。

控制台和 OBS 浏览器源**共用同一个 server 和同一个 overlay 状态**：
- 页面 `/studio` 是给人看的操作台；
- `/` 是给 OBS 录的透明弹幕；
- 两边的台词都落在 `overlay_server.add_comment()` 这条唯一入口上。

这里不引入第二套播放/排队逻辑：控制台只负责「拼句、lint、试听、看状态」。
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable

from .. import __codename__, __product__, __version__, paths
from ..core.config import LiveConfig
from ..games import GameRegistry
from ..persona import Persona
from ..voice import build_router

_GAME_CACHE_TTL = 6.0
_VOICE_CACHE_TTL = 5.0


class StudioRuntime:
    """进程内单例：控制台刷新很频繁，别每次重建人设/语音/注册表。"""

    def __init__(self) -> None:
        self.cfg = LiveConfig.load()
        self.persona = Persona.load(self.cfg.persona)
        self.voice = build_router(self.cfg)
        self.registry = GameRegistry()
        self._lock = threading.Lock()
        self._games_cache: tuple[float, list[dict]] = (0.0, [])
        self._voice_cache: tuple[float, dict] = (0.0, {})
        self._said = 0
        self._pushed = 0
        self._last: dict = {}
        self._voice_state: dict = {"pending": 0, "ok": 0, "fail": 0, "last": {}}
        self._q: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, name="dafeiyu-studio-voice", daemon=True)
        self._worker.start()

    # ---------------------------------------------------------------- 语音
    def _worker_loop(self) -> None:
        while True:
            job = self._q.get()
            try:
                if job is None:
                    return
                text, voice, volume = job
                result = self.voice.speak(text, voice, volume)
                with self._lock:
                    if result.ok:
                        self._voice_state["ok"] += 1
                        self._voice_state["last"] = {
                            "ok": True, "voice": result.voice,
                            "duration": round(result.duration, 3),
                            "cached": result.cached, "text": text[:40],
                        }
                    else:
                        self._voice_state["fail"] += 1
                        self._voice_state["last"] = {
                            "ok": False, "voice": "", "error": result.error, "text": text[:40]}
            finally:
                with self._lock:
                    self._voice_state["pending"] = max(0, self._voice_state["pending"] - 1)

    def audio(self, text: str, voice: str | None = None):
        """同步合成（控制台「试听」用）；返回 VoiceResult。"""
        return self.voice.speak(text, voice)

    def _queue_voice(self, text: str, voice: str | None) -> None:
        with self._lock:
            self._voice_state["pending"] += 1
        self._q.put((text, voice or self.cfg.voice, self.cfg.volume))

    # ---------------------------------------------------------------- 状态
    def _games(self, force: bool = False) -> list[dict]:
        now = time.time()
        ts, cached = self._games_cache
        if not force and now - ts < _GAME_CACHE_TTL:
            return cached
        rows: list[dict] = []
        for pack in self.registry.packs():
            adapter = self.registry.create_adapter(pack.info.id)
            try:
                live = bool(adapter and adapter.available())
            except Exception:
                live = False
            info = pack.info.to_dict()
            info["connected"] = live
            info["manifest"] = pack.manifest
            rows.append(info)
        self._games_cache = (now, rows)
        return rows

    def _voice_status(self) -> dict:
        now = time.time()
        ts, cached = self._voice_cache
        if now - ts < _VOICE_CACHE_TTL:
            return cached
        status = self.voice.status()
        status["runtime"] = dict(self._voice_state)
        self._voice_cache = (now, status)
        return status

    def payload(self, overlay: dict | None = None) -> dict:
        overlay = overlay or {}
        card = self.persona.card
        with self._lock:
            said = self._said
            pushed = self._pushed
            last = dict(self._last)
            voice_state = dict(self._voice_state)
        return {
            "ok": True,
            "product": {"name": __product__, "version": __version__, "codename": __codename__},
            "root": str(paths.ROOT),
            "persona": self.persona.to_dict(),
            "voice": self._voice_status(),
            "games": self._games(),
            "overlay": {
                "clients": overlay.get("clients", 0),
                "seq": overlay.get("seq", 0),
                "thinking": overlay.get("thinking", {}),
                "playback": overlay.get("playback", {}),
                "comments": overlay.get("comments", []),
            },
            "studio": {"said": said, "pushed": pushed, "last": last, "voice": voice_state},
            "samples": list(card.openers[:2]) + list(card.quips[:4]),
            "links": {
                "overlay": "/",
                "studio": "/studio",
                "health": "/health",
                "games": "/api/games",
            },
        }

    # ---------------------------------------------------------------- 说话
    def say(self, body: dict, add_comment: Callable[[dict], Any], *,
            await_voice: bool = False) -> dict:
        thought = str(body.get("thought") or "")
        meme = str(body.get("meme") or "")
        text = str(body.get("text") or "").strip()
        voice = body.get("voice") or None
        report = self.persona.render(thought, meme) if not text else {
            "text": text,
            "lines": [text],
            "lint": self.persona.lint_report(text),
        }
        final = (report.get("text") or "").strip()
        if not final:
            return {"ok": False, "error": "空台词", "lint": report.get("lint", {})}
        if body.get("dry_run"):
            return {"ok": True, "dry_run": True, "text": final,
                    "lines": report.get("lines") or [final], "lint": report.get("lint", {})}
        comment = add_comment({
            "thought": thought,
            "meme": meme,
            "speech": final,
            "tag": "思路",
        })
        with self._lock:
            self._said += 1
            if comment:
                self._pushed += 1
            self._last = {
                "text": final,
                "lint": report.get("lint", {}),
                "at": time.time(),
                "id": (comment or {}).get("id"),
            }
        self._queue_voice(final, voice)
        if await_voice:
            deadline = time.time() + 60
            while time.time() < deadline:
                with self._lock:
                    if self._voice_state["pending"] == 0:
                        break
                time.sleep(0.05)
        with self._lock:
            last = dict(self._last)
        return {
            "ok": True,
            "text": final,
            "lines": report.get("lines") or [final],
            "lint": report.get("lint", {}),
            "comment_id": (comment or {}).get("id"),
            "voice": voice or self.cfg.voice,
            "voice_state": dict(self._voice_state),
            "last": last,
        }


_RUNTIME: StudioRuntime | None = None
_RUNTIME_LOCK = threading.Lock()


def get_runtime() -> StudioRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = StudioRuntime()
        return _RUNTIME


__all__ = ["StudioRuntime", "get_runtime"]
