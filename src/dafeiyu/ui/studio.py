"""直播姬控制台后端：/api/studio、/api/say、/api/voice。

控制台和 OBS 浏览器源**共用同一个 server 和同一个 overlay 状态**：
- 页面 `/studio` 是给人看的操作台；
- `/` 是给 OBS 录的透明弹幕；
- 两边的台词都落在 `overlay_server.add_comment()` 这条唯一入口上。

这里不引入第二套播放/排队逻辑：控制台只负责「拼句、lint、试听、看状态」。
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .. import __codename__, __product__, __version__, paths
from ..core.config import LiveConfig
from ..games import GameRegistry
from ..persona import Persona
from ..voice import build_router

_GAME_CACHE_TTL = 6.0
_VOICE_CACHE_TTL = 5.0
_ROOM_MAX = 120
_PERSONA_MAX_BYTES = 200_000

# 控制台「人设/梗」可编辑的文件：只认白名单，页面传名字不传路径，避免任意文件读写。
_PERSONA_TEXT_FILES = (
    ("style.md", "说话风格（三拍/五件武器/梗纪律）"),
)
_CASTER_FILES = (
    ("speech.md", "主播台词规则"),
    ("lines.md", "分节点台词库"),
    ("quips.md", "招牌梗（tools/caster_quips.py 生成）"),
)


def persona_files(persona_id: str, game: str) -> list[dict]:
    """可编辑文件清单（不返回绝对路径）。"""
    pid = str(persona_id or "dafeiyu")
    out: list[dict] = []
    card = paths.PERSONA / pid / "card.json"
    if card.exists():
        out.append({"name": "card.json", "label": "人设卡（名字/性格/招牌/禁语）",
                    "kind": "json", "path": f"persona/{pid}/card.json"})
    for fname, label in _PERSONA_TEXT_FILES:
        p = paths.PERSONA / pid / fname
        if p.exists():
            out.append({"name": fname, "label": label, "kind": "text",
                        "path": f"persona/{pid}/{fname}"})
    for p in sorted((paths.PERSONA / "memes").glob("*.json")):
        out.append({"name": f"memes/{p.name}", "label": f"通用梗 · {p.stem}",
                    "kind": "json", "path": f"persona/memes/{p.name}"})
    game_id = str(game or "")
    if game_id:
        game_dir = paths.ROOT / "games" / game_id / "persona"
        for fname, label in _CASTER_FILES:
            p = game_dir / fname
            if p.exists():
                out.append({"name": f"caster/{fname}", "label": label, "kind": "text",
                            "path": f"games/{game_id}/persona/{fname}"})
    memes = paths.ROOT / "db" / "memes.json"
    if memes.exists():
        out.append({"name": "db/memes.json", "label": "梗列表（同步到游戏梗库）",
                    "kind": "json", "path": "db/memes.json"})
    return out


def persona_file_path(name: str, persona_id: str, game: str) -> Path:
    """把控制台传的文件名解析成白名单里的绝对路径；越界直接拒绝。"""
    for item in persona_files(persona_id, game):
        if item["name"] == name:
            rel = item["path"]
            return paths.ROOT / rel
    raise ValueError(f"不可编辑的文件：{name}")


def validate_card(raw: Any) -> dict:
    """人设卡最小校验：坏 JSON 之外，id/name 和各列表也不能写错类型。"""
    if not isinstance(raw, dict):
        raise ValueError("人设卡必须是 JSON 对象")
    for key in ("id", "name"):
        if not str(raw.get(key) or "").strip():
            raise ValueError(f"人设卡缺少 {key}")
    for key in ("traits", "quips", "openers", "closers", "banned_prefixes",
                "banned_labels", "repeat_words"):
        val = raw.get(key)
        if val is not None and not (isinstance(val, list)
                                    and all(isinstance(x, str) for x in val)):
            raise ValueError(f"人设卡 {key} 必须是字符串数组")
    style = raw.get("style")
    if style is not None and not isinstance(style, dict):
        raise ValueError("人设卡 style 必须是对象")
    return raw


def parse_memes(text: str) -> list[dict]:
    """梗列表：JSON 数组，每条至少 id + text；tags 统一转成字符串数组。"""
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"梗列表不是合法 JSON：{exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise ValueError("梗列表必须是非空 JSON 数组")
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"第 {i + 1} 条不是对象")
        entry = {k: item[k] for k in ("id", "text", "category", "game", "trigger",
                                      "example", "intensity", "cooldown") if k in item}
        if not str(entry.get("id") or "").strip():
            raise ValueError(f"第 {i + 1} 条缺 id")
        if not str(entry.get("text") or "").strip():
            raise ValueError(f"第 {i + 1} 条缺 text")
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [x.strip() for x in tags.split(",") if x.strip()]
        if not isinstance(tags, list):
            raise ValueError(f"第 {i + 1} 条 tags 必须是数组")
        entry["tags"] = [str(x) for x in tags]
        entry.setdefault("intensity", 1)
        entry.setdefault("cooldown", 10)
        out.append(entry)
    return out


def sync_memes_db(items: list[dict], db_path: Path) -> int:
    """把梗列表写回游戏梗库 SQLite 的 memes 表（路径由游戏包提供）。"""
    import sqlite3

    db = Path(db_path)
    con = sqlite3.connect(str(db))
    try:
        with con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS memes (id TEXT PRIMARY KEY, text TEXT, "
                "category TEXT, game TEXT, tags TEXT, trigger TEXT, example TEXT, "
                "intensity INTEGER, cooldown INTEGER)"
            )
            con.execute("DELETE FROM memes")
            con.executemany(
                "INSERT INTO memes VALUES(?,?,?,?,?,?,?,?,?)",
                [(str(m.get("id")), str(m.get("text")), str(m.get("category") or ""),
                  str(m.get("game") or "both"), ",".join(m.get("tags") or []),
                  str(m.get("trigger") or ""), str(m.get("example") or ""),
                  int(m.get("intensity") or 1), int(m.get("cooldown") or 10))
                 for m in items],
            )
    finally:
        con.close()
    return len(items)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)



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
        self._room_cache: tuple[tuple, list[dict]] = ((0, 0), [])
        self._bili_sent: list[dict] = []
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
        from ..agent.play import status as play_status, token_usage

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
            "agent": {**agent_summary(), "tokens": token_usage(40)},
            "play": play_status(),
            "bilibili": self._bilibili_status(),
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

    def _room_events(self) -> list[dict]:
        """最近 120 条 B 站互动；按文件 mtime/size 缓存，3 秒轮询不重复解析。"""
        log_path = paths.LOGS / "bilibili_events.jsonl"
        try:
            st = log_path.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            return []
        if sig == self._room_cache[0]:
            return [dict(x) for x in self._room_cache[1]]
        from ..live.bilibili import RoomLog

        rows = RoomLog(log_path).recent(_ROOM_MAX)
        self._room_cache = (sig, rows)
        return [dict(x) for x in rows]

    def _bilibili_status(self) -> dict:
        """桌宠写的 bilibili.json（连接状态）+ 本地最近 120 条互动。"""
        try:
            raw = json.loads((paths.LOGS / "bilibili.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        room = int(self.cfg.bilibili_room_id or 0)
        cookie = str(self.cfg.bilibili_cookie or "")
        events = self._room_events()
        with self._lock:
            sent = [dict(x) for x in self._bili_sent]
        merged = (events + sent)[-_ROOM_MAX:]
        raw.update({
            "events": merged,
            "seq": merged[-1].get("at") if merged else 0,
            "room_id": room or int(raw.get("room_id") or 0),
            "enabled": bool(self.cfg.bilibili_enabled),
            "cookie_set": bool(cookie),
            "can_send": bool(room and "bili_jct=" in cookie),
            "max": _ROOM_MAX,
        })
        return raw

    def send_to_room(self, text: str) -> dict:
        """控制台发一条弹幕进直播间；需要带 bili_jct 的登录 Cookie。"""
        self._sync_config()
        clean = " ".join(str(text or "").split())
        if not clean:
            raise ValueError("弹幕不能为空")
        room = int(self.cfg.bilibili_room_id or 0)
        if room <= 0:
            raise ValueError("先在「基础配置」填 B 站直播间号")
        from ..live.bilibili import send_danmu

        send_danmu(room, clean, self.cfg.bilibili_cookie)
        event = {"at": time.time(), "kind": "self", "label": "本机发送",
                 "user": "大肥鱼", "uid": 0, "text": clean,
                 "gift": "", "count": 0, "coin": 0, "room_id": room,
                 "ts": int(time.time()), "local": True}
        with self._lock:
            self._bili_sent.append(event)
            self._bili_sent = self._bili_sent[-40:]
        return {"ok": True, "text": clean, "event": event}

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
        if "bilibili_enabled" in body:
            patch["bilibili_enabled"] = bool(body.get("bilibili_enabled"))
        if "bilibili_room_id" in body:
            try:
                room_id = int(body.get("bilibili_room_id") or 0)
            except (TypeError, ValueError):
                raise ValueError("B 站房间号必须是数字")
            if room_id < 0:
                raise ValueError("B 站房间号不能为负")
            patch["bilibili_room_id"] = room_id
        cookie = str(body.get("bilibili_cookie") or "").strip()
        if cookie not in ("", "***"):
            patch["bilibili_cookie"] = cookie
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

    # ---------------------------------------------------------------- 人设 / 梗
    def persona_payload(self) -> dict:
        """控制台「人设/梗」编辑器：白名单文件 + 当前内容。"""
        self._sync_config()
        files = persona_files(self.cfg.persona, self.cfg.game)
        texts: dict[str, str] = {}
        for item in files:
            try:
                p = persona_file_path(item["name"], self.cfg.persona, self.cfg.game)
                texts[item["name"]] = p.read_text(encoding="utf-8")
            except OSError:
                texts[item["name"]] = ""
        return {"ok": True, "persona": self.persona.card.id, "files": files, "texts": texts}

    def _game_memes_db(self) -> Path | None:
        """梗库 DB 由游戏包声明（核心不硬编码具体游戏路径）。"""
        try:
            mod = self.registry.load_game_module(self.cfg.game, "memes")
        except Exception:
            return None
        p = Path(str(getattr(mod, "DB", "") or ""))
        return p if str(p) and p.exists() else None

    def save_persona(self, name: str, text: str) -> dict:
        """写人设/梗文件；JSON 先校验，写坏自动回滚。"""
        self._sync_config()
        body = str(text or "")
        if len(body.encode("utf-8")) > _PERSONA_MAX_BYTES:
            raise ValueError(f"文件超过 {_PERSONA_MAX_BYTES // 1000}KB，控制台不适合存这么大的文本")
        path = persona_file_path(name, self.cfg.persona, self.cfg.game)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        saved_note = ""
        if name == "card.json":
            try:
                validate_card(json.loads(body))
            except ValueError as exc:
                raise ValueError(f"人设卡校验失败：{exc}") from exc
        elif name == "db/memes.json":
            parse_memes(body)   # 先验一遍，别把坏数据写进库
        elif name.startswith("memes/"):
            try:
                raw = json.loads(body)
            except ValueError as exc:
                raise ValueError(f"JSON 语法错误：{exc}") from exc
            items = raw.get("items") if isinstance(raw, dict) else None
            if not isinstance(items, list) or not items:
                raise ValueError("梗文件需要 items 数组（至少一条）")
            if any(not isinstance(x, dict) or not str(x.get("text") or "").strip()
                   for x in items):
                raise ValueError("两条梗里缺 text")
        try:
            _atomic_write(path, body)
            if name == "card.json":
                self.persona = Persona.load(self.cfg.persona)
                saved_note = "人设卡已生效（下一条台词起）"
            elif name == "db/memes.json":
                db = self._game_memes_db()
                if db is None:
                    raise ValueError("当前游戏的梗库 DB 不可用（先确认游戏包里有 memes.py）")
                n = sync_memes_db(parse_memes(body), db)
                saved_note = f"梗列表已保存并重建 memes 表（{n} 条）"
            elif name.startswith("memes/"):
                saved_note = "通用梗已保存"
            else:
                saved_note = "已保存"
        except Exception as exc:
            _atomic_write(path, old)    # 写坏/加载失败：回滚到保存前
            self.persona = Persona.load(self.cfg.persona)
            raise ValueError(f"保存失败，已回滚：{exc}") from exc
        return {"ok": True, "name": name, "bytes": len(body.encode("utf-8")),
                "message": saved_note, "persona": self.persona.to_dict()}

    def reload_persona(self) -> dict:
        """手动重载 card.json（比如用别的编辑器改了文件）。"""
        self._sync_config()
        self.persona = Persona.load(self.cfg.persona)
        return {"ok": True, "message": "人设已重载", "persona": self.persona.to_dict()}

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
