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
        self._cfg_mtime = self._config_mtime()
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
        from ..agent.trace import agent_summary
        from ..agent.play import status as play_status

        return {
            "ok": True,
            "product": {"name": __product__, "version": __version__, "codename": __codename__},
            "root": str(paths.ROOT),
            "config": self.config_payload(),
            "persona": self.persona.to_dict(),
            "voice": self._voice_status(),
            "games": self._games(),
            "overlay": {
                "clients": overlay.get("clients", 0),
                "seq": overlay.get("seq", 0),
                "thinking": overlay.get("thinking", {}),
                "playback": overlay.get("playback", {}),
                "comments": overlay.get("comments", []),
                "decisions": overlay.get("decisions", []),
                "logs": overlay.get("logs", []),
                "usage": overlay.get("usage", {}),
            },
            "studio": {"said": said, "pushed": pushed, "last": last, "voice": voice_state},
            "agent": agent_summary(),
            "play": play_status(),
            "samples": list(card.openers[:2]) + list(card.quips[:4]),
            "links": {
                "overlay": "/",
                "studio": "/studio",
                "health": "/health",
                "games": "/api/games",
            },
        }

    # ---------------------------------------------------------------- 配置
    @staticmethod
    def _config_mtime() -> int:
        try:
            return paths.CONFIG_FILE.stat().st_mtime_ns
        except OSError:
            return 0

    def _sync_config(self) -> None:
        """桌宠右键菜单也会写 config.json；控制台下一次轮询要跟上。"""
        mtime = self._config_mtime()
        if mtime == self._cfg_mtime:
            return
        old = (self.cfg.voice, self.cfg.tts_url, self.cfg.mambo_dir,
               self.cfg.tts_ref, self.cfg.tts_prompt, self.cfg.tts_speed,
               self.cfg.tts_timeout)
        fresh = LiveConfig.load()
        self.cfg = fresh
        self._cfg_mtime = mtime
        new = (fresh.voice, fresh.tts_url, fresh.mambo_dir,
               fresh.tts_ref, fresh.tts_prompt, fresh.tts_speed, fresh.tts_timeout)
        if old != new:
            self.voice = build_router(self.cfg)
            self._voice_cache = (0.0, {})

    def config_payload(self) -> dict:
        """控制台配置视图：API Key 只显示是否已配置。"""
        self._sync_config()
        out = self.cfg.to_dict()
        out["voice_options"] = ["mambo", "animal", "off"]
        out["llm_api_key_set"] = bool(self.cfg.llm_api_key)
        out["game_options"] = [
            {"id": g["id"], "name": g["name"], "platform": g.get("platform", ""),
             "status": g.get("status", "")}
            for g in self.registry.list()
        ]
        return out

    def start_voice(self, voice: str) -> dict:
        """启动指定音色引擎；目前只有曼波需要启动。"""
        self._sync_config()
        voice = str(voice or "mambo").strip().lower()
        if voice in ("animal", "animalese"):
            return {"ok": True, "voice": "animal", "online": True, "starting": False,
                    "message": "动物语是内置合成，不需要启动"}
        if voice != "mambo":
            return {"ok": False, "voice": voice, "message": "未知音色"}
        from ..voice.startup import start_from_config

        return {"voice": "mambo", **start_from_config(self.cfg)}

    def launch_game(self, game_id: str | None = None) -> dict:
        """启动当前 / 指定游戏；桌面菜单和控制台按钮共用。"""
        self._sync_config()
        from ..games.launch import launch_game

        return launch_game(game_id or self.cfg.game, self.registry)

    def update_config(self, body: dict) -> dict:
        """从控制台/桌宠菜单保存语音、模型、API 配置。"""
        patch: dict = {}
        for key in ("voice", "game", "mambo_dir", "llm_provider", "llm_base_url", "llm_model",
                    "autoplay_mode", "autoplay_pace", "autoplay_llm_model"):
            if body.get(key) is not None:
                patch[key] = str(body[key]).strip()
        if patch.get("voice") not in (None, "", "mambo", "animal", "off"):
            raise ValueError("voice 只能是 mambo / animal / off")
        if patch.get("autoplay_mode") not in (None, "", "local", "llm"):
            raise ValueError("autoplay_mode 只能是 local / llm")
        if patch.get("autoplay_pace") not in (None, "", "voice", "normal"):
            raise ValueError("autoplay_pace 只能是 voice / normal")
        if "game" in patch and patch["game"]:
            if self.registry.get(patch["game"]) is None:
                raise ValueError("未知游戏")
        if str(body.get("llm_api_key") or "").strip() not in ("", "***"):
            patch["llm_api_key"] = str(body["llm_api_key"]).strip()
        if body.get("clear_api_key"):
            patch["llm_api_key"] = ""
        if "llm_models" in body and isinstance(body["llm_models"], list):
            patch["llm_models"] = [str(x).strip() for x in body["llm_models"] if str(x).strip()][:12]
        if patch.get("llm_model"):
            models = [str(x) for x in (patch.get("llm_models") or self.cfg.llm_models or []) if str(x).strip()]
            if patch["llm_model"] not in models:
                models.insert(0, patch["llm_model"])
            patch["llm_models"] = models[:12]
        if not patch:
            return {"ok": True, "config": self.config_payload()}
        self.cfg.save_patch(patch)
        self._cfg_mtime = self._config_mtime()
        if "voice" in patch:
            self.voice = build_router(self.cfg)
            self._voice_cache = (0.0, {})
        voice_start = None
        if patch.get("voice") == "mambo":
            voice_start = self.start_voice("mambo")
        return {"ok": True, "config": self.config_payload(), "voice_start": voice_start}

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
