"""KOOK 私聊/频道聊天常驻服务：WebSocket 收消息 → 记忆 + 模型 → 文字/图片/语音。

    python main.py kook            # 常驻
    python main.py kook --check    # 只自检配置/网关，不进入消息循环

只用纯出站 WebSocket，不需要公网 IP。KOOK 的频道消息和私信都支持。
频道消息的 ``target_id`` 是频道 id；私聊事件的 ``target_id`` 是机器人自己，
回复时必须用发送者 ``author_id`` 走 ``direct-message/create``，否则平台会报
“不能是自己”。
"""

from __future__ import annotations

import json
import queue
import random
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ... import paths
from ...core.config import LiveConfig
from ...persona.card import PersonaCard
from ...persona.layers import PersonaStack
from .. import chat as chat_mod
from ..chat_memory import Memory
from .client import KookClient, KookError
from .usage import append_usage

_MET = re.compile(r"\(met\)[^)]*\(met\)|\(rol\)[^)]*\(rol\)")
_WS = re.compile(r"\s+")


def _card_image_urls(content) -> list[str]:
    """从 KOOK 卡片消息里抠图片地址；粘贴/拖拽图片在网页端会走卡片。"""
    raw = content
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    out: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if str(node.get("type") or "") == "image":
                src = str(node.get("src") or "").strip()
                if src.startswith(("https://", "http://")):
                    out.append(src)
            for key in ("modules", "elements", "accessory", "fields"):
                walk(node.get(key))
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(raw)
    return out


def _clean_content(text: str) -> str:
    body = _MET.sub(" ", str(text or ""))
    body = body.replace("@全体成员", " ").replace("@在线成员", " ")
    return _WS.sub(" ", body).strip()


@dataclass
class Inbound:
    message_id: str = ""
    target_id: str = ""
    channel_type: str = ""
    author_id: str = ""
    author_name: str = ""
    author_bot: bool = False
    msg_type: int = 0
    text: str = ""
    image_url: str = ""
    image_name: str = ""
    create_time: int = 0


def parse_event(data: dict) -> Inbound | None:
    """把 KOOK s=0 的事件 data 拆成纯数据；非聊天消息返回 None。"""
    if not isinstance(data, dict):
        return None
    try:
        msg_type = int(data.get("type") or 0)
    except (TypeError, ValueError):
        msg_type = 0
    if msg_type not in (1, 2, 9, 10):  # 文本 / 图片 / KMarkdown / 卡片
        return None
    extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
    author = extra.get("author") if isinstance(extra.get("author"), dict) else {}
    author_id = str(data.get("author_id") or "")
    if author_id == "1":
        return None
    content = data.get("content") or ""
    image_url = ""
    image_name = ""
    if msg_type == 2:
        att = extra.get("attachments") if isinstance(extra.get("attachments"), dict) else {}
        image_url = str(att.get("url") or content or "").strip()
        image_name = str(att.get("name") or "").strip()
        text = ""
    elif msg_type == 10:
        images = _card_image_urls(content)
        if not images:
            return None
        image_url = images[0]
        text = ""
    else:
        text = _clean_content(content)
    return Inbound(
        message_id=str(data.get("msg_id") or ""),
        target_id=str(data.get("target_id") or ""),
        channel_type=str(data.get("channel_type") or ""),
        author_id=author_id,
        author_name=str(author.get("username") or author.get("nickname") or ""),
        author_bot=bool(author.get("bot")),
        msg_type=msg_type,
        text=text,
        image_url=image_url,
        image_name=image_name,
        create_time=int(data.get("msg_timestamp") or 0),
    )


def inbound_from_message(item: dict, target_id: str,
                         channel_type: str = "GROUP") -> Inbound | None:
    """把 ``/message/list`` 的一条消息转成 Inbound（图片轮询兜底用）。"""
    if not isinstance(item, dict):
        return None
    try:
        msg_type = int(item.get("type") or 0)
    except (TypeError, ValueError):
        msg_type = 0
    if msg_type not in (2, 10):
        return None
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    att = item.get("attachments") if isinstance(item.get("attachments"), dict) else {}
    content = item.get("content") or ""
    if msg_type == 2:
        image_url = str(att.get("url") or content or "").strip()
    else:
        images = _card_image_urls(content)
        image_url = images[0] if images else ""
    if not image_url:
        return None
    return Inbound(
        message_id=str(item.get("id") or item.get("msg_id") or ""),
        target_id=str(target_id or item.get("target_id") or ""),
        channel_type=channel_type,
        author_id=str(item.get("author_id") or author.get("id") or ""),
        author_name=str(author.get("username") or author.get("nickname") or ""),
        author_bot=bool(author.get("bot")),
        msg_type=msg_type,
        text="",
        image_url=image_url,
        image_name=str(att.get("name") or ""),
        create_time=int(item.get("create_at") or item.get("msg_timestamp") or 0),
    )


class KookService:
    """一条消息 → 一次回复；可注入假 client/llm 做离线测试。"""

    def __init__(self, cfg: LiveConfig, memory: Memory | None, client,
                 llm: chat_mod.ChatLLM | None = None,
                 card: PersonaCard | None = None,
                 voice=None,
                 work_dir: str | Path | None = None,
                 log_path: str | Path | None = None) -> None:
        self.cfg = cfg
        self.memory = memory
        self.client = client
        self.llm = llm or chat_mod.ChatLLM(cfg)
        self.card = card or PersonaCard.load(cfg.kook_persona or "companion")
        self._voice = voice
        self.work_dir = Path(work_dir or (paths.LOGS / "kook"))
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = Path(log_path or (self.work_dir / "events.jsonl"))
        self._turns_since_facts = 0
        self._queue: queue.Queue = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._work, name="kook-chat", daemon=True)
        # KOOK 的 WebSocket 目前不推送 type=2 图片消息；用频道消息轮询兜底。
        self._poll_channels: set[str] = set()
        self._poll_seen: set[str] = set()
        self._poll_primed: set[str] = set()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="kook-image-poll", daemon=True)
        # 安静一阵后偶尔主动找最近聊过的人/频道；只记在内存里，重启后等新消息。
        self._proactive_thread = threading.Thread(
            target=self._proactive_loop, name="kook-proactive", daemon=True)
        self._last_target = ""
        self._last_channel_type = ""
        self._last_active_at = time.time()
        self._last_proactive_at = 0.0
        self._proactive_armed = False
        # WebSocket 和图片轮询可能拿到同一条消息，处理前统一去重。
        self._seen_ids: set[str] = set()
        self._seen_lock = threading.Lock()

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> "KookService":
        self._worker.start()
        self._poll_thread.start()
        self._proactive_thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._worker.is_alive() and self._worker is not threading.current_thread():
            self._worker.join(timeout=5)
        if self._poll_thread.is_alive() and self._poll_thread is not threading.current_thread():
            self._poll_thread.join(timeout=5)
        if (self._proactive_thread.is_alive()
                and self._proactive_thread is not threading.current_thread()):
            self._proactive_thread.join(timeout=5)

    # ---------------------------------------------------------------- 事件
    def on_event(self, data: dict) -> None:
        try:
            msg = parse_event(data)
        except Exception as exc:  # noqa: BLE001
            self._log({"kind": "event_error", "error": f"{type(exc).__name__}: {exc}"})
            return
        if msg is None:
            return
        if msg.channel_type == "GROUP" and msg.target_id:
            self._poll_channels.add(msg.target_id)
        if msg.message_id:
            self._poll_seen.add(msg.message_id)
        self._log({"kind": "event_received", "msg_type": msg.msg_type,
                   "channel_type": msg.channel_type})
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            self._log({"kind": "drop", "reason": "队列已满"})

    # ---------------------------------------------------------------- 图片轮询
    def _discover_channels(self) -> None:
        try:
            for guild in self.client.guilds():
                gid = str(guild.get("id") or "")
                if not gid:
                    continue
                for ch in self.client.channels(gid):
                    if int(ch.get("type") or 0) == 1:
                        cid = str(ch.get("id") or "")
                        if cid:
                            self._poll_channels.add(cid)
        except Exception as exc:  # noqa: BLE001
            self._log({"kind": "poll_discovery_error",
                       "error": f"{type(exc).__name__}: {exc}"})

    def _poll_loop(self) -> None:
        if self._stop.wait(2.0):
            return
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:  # noqa: BLE001 - 轮询失败不能带崩服务
                self._log({"kind": "poll_error",
                           "error": f"{type(exc).__name__}: {exc}"})
            if self._stop.wait(6.0):
                break

    def _poll_once(self) -> None:
        """只补图片消息；文字仍走 WebSocket，避免重复回复。"""
        if not self._poll_channels:
            self._discover_channels()
        for target in list(self._poll_channels):
            try:
                items = self.client.messages(target, 20)
            except Exception as exc:  # noqa: BLE001
                self._log({"kind": "poll_read_error", "target_id": target,
                           "error": f"{type(exc).__name__}: {exc}"})
                continue
            first = target not in self._poll_primed
            if first:
                self._poll_primed.add(target)
            fresh: list[Inbound] = []
            for item in reversed(items):
                msg = inbound_from_message(item, target)
                if msg is None or not msg.message_id:
                    continue
                if msg.message_id in self._poll_seen:
                    continue
                self._poll_seen.add(msg.message_id)
                if not first and not msg.author_bot:
                    fresh.append(msg)
            for msg in fresh:
                self._log({"kind": "poll_image", "target_id": target})
                self.handle(msg)

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                msg = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if msg is None:
                break
            try:
                self.handle(msg)
            except Exception as exc:  # noqa: BLE001 - 一条消息不能带崩常驻
                self._log({"kind": "error", "error": f"{type(exc).__name__}: {exc}"})
            finally:
                self._queue.task_done()

    # ---------------------------------------------------------------- 主动消息
    def _proactive_loop(self) -> None:
        if self._stop.wait(30.0):
            return
        while not self._stop.is_set():
            self._refresh_proactive_settings()
            try:
                self._maybe_proactive()
            except Exception as exc:  # noqa: BLE001 - 主动消息失败不能带崩常驻
                self._log({"kind": "proactive_error",
                           "error": f"{type(exc).__name__}: {exc}"})
            if self._stop.wait(60.0):
                break

    def _refresh_proactive_settings(self) -> None:
        """设置页改了「主动消息」不用重启 KOOK 服务，最多一分钟内生效。"""
        try:
            fresh = LiveConfig.load()
            self.cfg.kook_proactive = bool(fresh.kook_proactive)
            self.cfg.kook_proactive_minutes = int(fresh.kook_proactive_minutes or 60)
        except Exception:
            pass

    def _maybe_proactive(self, now: float | None = None,
                         chance: float | None = None) -> bool:
        """安静够久后按概率主动开口；只找最近聊过的地方，不连发。"""
        if not bool(getattr(self.cfg, "kook_proactive", False)):
            return False
        target = str(self._last_target or "")
        if not target or not self._proactive_armed:
            return False
        now = time.time() if now is None else float(now)
        try:
            minutes = int(getattr(self.cfg, "kook_proactive_minutes", 60) or 60)
        except (TypeError, ValueError):
            minutes = 60
        interval = max(10, minutes) * 60
        idle = now - float(self._last_active_at or 0.0)
        if idle < interval:
            return False
        if self._last_proactive_at and now - self._last_proactive_at < interval:
            return False
        roll = random.random() if chance is None else float(chance)
        if roll > 0.2:
            return False
        idle_minutes = max(1, int(idle / 60))
        self._last_proactive_at = now
        self._last_active_at = now
        self._proactive_armed = False
        return self._send_proactive(idle_minutes)

    def _send_proactive(self, idle_minutes: int) -> bool:
        target = str(self._last_target or "")
        if not target:
            return False
        channel_type = self._last_channel_type or "GROUP"
        # 私聊的 Inbound.target_id 只作展示/兜底；真正发给谁由 _reply_target 决定。
        # 主动消息没有入站作者，私聊时把 target 同时放进 author_id。
        msg = Inbound(target_id=target,
                      channel_type=channel_type,
                      author_id=target if str(channel_type).upper() == "PERSON" else "")
        started = time.time()
        try:
            messages = self._build_messages(
                "", proactive=True, idle_minutes=idle_minutes)
        except Exception as exc:  # noqa: BLE001
            self._log({"kind": "proactive_error",
                       "error": f"{type(exc).__name__}: {exc}"})
            return False
        model = chat_mod.pick_model(self.cfg, messages)
        result = self.llm.complete(messages, model=model)
        if result.get("usage"):
            self._log_usage(result, note="KOOK 主动消息", msg=msg)
        if not result.get("ok"):
            self._log({"kind": "proactive_llm_error",
                       "error": result.get("error", "")})
            return False

        reply = chat_mod.parse_reply(
            result.get("text", ""), allow_voice=bool(self.cfg.kook_allow_voice))
        texts = [str(part).strip() for part in (reply.get("texts") or [])
                 if str(part).strip()]
        if not texts:
            single = str(reply.get("text") or "").strip()
            texts = [single] if single else []
        if not texts:
            self._log({"kind": "proactive_empty"})
            return False
        text = "\n".join(texts)
        sent = self._send_texts(msg, texts)

        voice_ok = False
        if reply.get("voice") and self.cfg.kook_allow_voice:
            voice_ok = self._send_voice(msg, str(reply.get("voice_text") or text))

        sticker_ok = False
        sticker_id = str(reply.get("sticker") or "").strip()
        if sticker_id and self.cfg.kook_stickers:
            sticker_ok = self._send_sticker(msg, sticker_id)

        if sent and self.memory:
            for part in texts:
                self.memory.add_turn("assistant", part, "", "")
            self._turns_since_facts += 1
        self._mirror_to_pet(" ".join(texts))
        actions = ["text"] + (["voice"] if voice_ok else []) + (
            ["sticker"] if sticker_ok else [])
        self._log({"kind": "proactive", "action": "+".join(actions),
                   "chars": len(text), "parts": len(texts),
                   "target_id": target,
                   "ms": int((time.time() - started) * 1000)})
        return bool(sent)

    # ---------------------------------------------------------------- 主流程
    def handle(self, msg: Inbound) -> dict:
        skip = self._filter(msg)
        if skip:
            self._log({"kind": "skip", "reason": skip, "author_id": msg.author_id})
            return {"ok": False, "action": "skip", "reason": skip}

        if msg.message_id:
            with self._seen_lock:
                if msg.message_id in self._seen_ids:
                    return {"ok": False, "action": "skip", "reason": "重复事件"}
                self._seen_ids.add(msg.message_id)
                if len(self._seen_ids) > 2000:
                    self._seen_ids = set(list(self._seen_ids)[-1000:])
        if self.memory and not self.memory.first_seen(msg.message_id):
            return {"ok": False, "action": "skip", "reason": "重复事件"}

        if self.cfg.kook_memory and msg.text:
            self._handle_memory_command(msg.text)

        # 记住最近聊过的地方，给「偶尔主动发消息」用。
        self._last_target = self._reply_target(msg)
        self._last_channel_type = msg.channel_type
        self._last_active_at = time.time()
        self._proactive_armed = True

        caption = msg.text
        try:
            messages = self._build_messages(caption, msg.image_url)
        except KookError as exc:
            self._log({"kind": "image_error", "error": str(exc)})
            text = "这张图我这边没加载出来，再发一次？"
            sent = self._send_text(msg, text)
            return {"ok": bool(sent), "action": "text", "reason": "image_error"}
        model = chat_mod.pick_model(self.cfg, messages)
        started = time.time()
        result = self.llm.complete(messages, model=model)
        if result.get("usage"):
            self._log_usage(result, note="KOOK 聊天", msg=msg)
        if not result.get("ok"):
            text = "我这边模型没连上，等下再试。"
            self._send_text(msg, text)
            self._log({"kind": "llm_error", "error": result.get("error", "")})
            return {"ok": False, "action": "text", "reason": "llm_error"}

        reply = chat_mod.parse_reply(
            result.get("text", ""), allow_voice=bool(self.cfg.kook_allow_voice))
        texts = [str(part).strip() for part in (reply.get("texts") or [])
                 if str(part).strip()]
        if not texts:
            single = str(reply.get("text") or "").strip()
            texts = [single] if single else []
        if not texts:
            texts = ["我卡了一下，你再说一遍？"]
        text = "\n".join(texts)
        # 模型偶尔会漏掉明确要求：对方点名要语音/表情包时兜底。
        if self.cfg.kook_allow_voice and self._wants_voice(caption) and not reply.get("voice"):
            reply["voice"] = True
            reply["voice_text"] = chat_mod.speech_text(text, 120)
        if self.cfg.kook_stickers and self._wants_sticker(caption) and not reply.get("sticker"):
            picked = self._pick_sticker(text)
            if picked:
                reply["sticker"] = picked
        sent = self._send_texts(msg, texts)
        if not sent:
            self._log({"kind": "send_error", "reason": "全部切片都失败"})

        voice_ok = False
        if reply.get("voice") and self.cfg.kook_allow_voice:
            voice_ok = self._send_voice(msg, str(reply.get("voice_text") or text))

        sticker_ok = False
        sticker_id = str(reply.get("sticker") or "").strip()
        if sticker_id and self.cfg.kook_stickers:
            sticker_ok = self._send_sticker(msg, sticker_id)

        user_text = caption or ("[图片]" if msg.image_url else "")
        self._record(msg, user_text, texts)
        self._maybe_extract_facts()
        self._maybe_summarize()
        self._mirror_to_pet(" ".join(texts))
        actions = ["text"] + (["voice"] if voice_ok else []) + (
            ["sticker"] if sticker_ok else [])
        self._log({"kind": "reply", "action": "+".join(actions), "chars": len(text),
                   "parts": len(texts), "msg_id": msg.message_id,
                   "ms": int((time.time() - started) * 1000)})
        return {"ok": bool(sent), "action": "+".join(actions), "reply": text,
                "texts": texts, "voice": voice_ok, "sticker": sticker_ok}

    @staticmethod
    def _reply_target(msg: Inbound) -> str:
        """频道回频道 id；私聊只回发送者，不能回 ``target_id``（那是机器人自己）。"""
        if str(msg.channel_type or "").upper() == "PERSON":
            return str(msg.author_id or msg.target_id or "")
        return str(msg.target_id or msg.author_id or "")

    def _filter(self, msg: Inbound) -> str:
        if msg.author_bot:
            return "机器人自己发的"
        if msg.channel_type not in ("GROUP", "PERSON"):
            return "只处理频道/私聊"
        if not self._reply_target(msg):
            return "没有 target_id"
        if not msg.text and not msg.image_url:
            return "只接文字/图片消息"
        allowed = [str(x) for x in (self.cfg.kook_allowed_users or [])]
        if allowed and msg.author_id not in allowed:
            return "不在白名单"
        return ""

    # ---------------------------------------------------------------- 提示词
    def _build_messages(self, caption: str, image_url: str = "", *,
                        proactive: bool = False,
                        idle_minutes: int = 0) -> list[dict]:
        from ...persona import stickers

        facts = []
        context = {"summaries": [], "turns": []}
        query = caption or ("图片" if image_url else ("主动聊天" if proactive else "聊天"))
        if self.cfg.kook_memory and self.memory:
            facts = self.memory.search(query, 6)
            context = self.memory.context(int(self.cfg.kook_context_turns or 12))
        try:
            stack = PersonaStack.load(self.cfg.kook_persona or "companion",
                                      live=False, include_inactive=False)
            persona_text = stack.prompt()
        except Exception:
            persona_text = ""
        options = stickers.options() if self.cfg.kook_stickers else []
        messages: list[dict] = [{
            "role": "system",
            "content": chat_mod.build_system_prompt(
                self.card, facts, bool(self.cfg.kook_allow_voice),
                persona_text=persona_text,
                summaries=context.get("summaries", []), sticker_options=options),
        }]
        for turn in context.get("turns", []):
            role = "user" if turn["role"] == "user" else "assistant"
            messages.append({"role": role, "content": turn["text"]})
        if image_url:
            data, mime = self.client.download(image_url)
            messages.append(chat_mod.image_message(
                caption or "看看这张图，说说你看到了什么。", data, mime))
        elif proactive:
            messages.append({"role": "user",
                             "content": chat_mod.build_proactive_prompt(idle_minutes)})
        else:
            messages.append({"role": "user",
                             "content": chat_mod.build_user_prompt(caption, False)})
        return messages

    # ---------------------------------------------------------------- 发送
    def _send_texts(self, msg: Inbound, texts: list[str]) -> bool:
        """逐条发送模型给的短消息；每条单独一个气泡，超长才切片。"""
        parts = [str(text).strip() for text in texts if str(text).strip()]
        if not parts:
            return False
        ok = True
        first = True
        target = self._reply_target(msg)
        for body in parts:
            for chunk in chat_mod.split_message(body):
                quote = msg.message_id if first else ""
                try:
                    self.client.send_text(
                        target, chunk, channel_type=msg.channel_type,
                        quote=quote, reply_msg_id=quote)
                except Exception as exc:  # noqa: BLE001
                    ok = False
                    self._log({"kind": "send_error",
                               "error": f"{type(exc).__name__}: {exc}"})
                finally:
                    first = False
        return ok

    def _send_text(self, msg: Inbound, text: str) -> bool:
        return self._send_texts(msg, [text])

    @staticmethod
    def _wants_voice(text: str) -> bool:
        body = str(text or "")
        return any(word in body for word in (
            "语音", "说句话", "说话", "念给", "读给", "听你说", "发条语音"))

    @staticmethod
    def _wants_sticker(text: str) -> bool:
        body = str(text or "")
        return any(word in body for word in (
            "表情包", "表情", "发张图", "发个图", "贴图", "斗图"))

    @staticmethod
    def _pick_sticker(text: str) -> str:
        from ...persona import stickers

        rows = [row for row in stickers.items() if row.get("exists")]
        if not rows:
            return ""
        body = str(text or "")
        for row in rows:
            if row["id"] == "one-bowl" and any(w in body for w in ("饭", "饿", "碗")):
                return row["id"]
        return rows[0]["id"]

    def _send_voice(self, msg: Inbound, voice_text: str) -> bool:
        """TTS → 优先 MP3 → 上传 → 卡片 audio。KOOK 不允许机器人直发 type=8。

        手机端播完 WAV 再点经常没声音；MP3 的通用解码路径更稳，编码失败才回退 WAV。
        """
        body = chat_mod.speech_text(voice_text, 120)
        if not body:
            return False
        try:
            if self._voice is None:
                from ...voice import build_router

                self._voice = build_router(self.cfg)
            result = self._voice.speak(body)
            if not result.ok or not result.wav:
                self._log({"kind": "voice_error",
                           "error": result.error or "合成没有返回音频"})
                return False
            from ...voice.encode import wav_to_mp3

            mp3 = wav_to_mp3(result.wav)
            if mp3:
                data, filename, mime, codec = mp3, "dafeiyu_voice.mp3", "audio/mpeg", "mp3"
            else:
                data, filename, mime, codec = (result.wav, "dafeiyu_voice.wav",
                                               "audio/wav", "wav")
            url = self.client.upload_asset(data, filename, mime)
            title = "大肥鱼语音"
            if result.duration:
                title += f" · {result.duration:.1f}s"
            self.client.send_audio_card(
                self._reply_target(msg), url, channel_type=msg.channel_type,
                title=title)
            self._log({"kind": "voice_sent", "voice": result.voice,
                       "codec": codec, "bytes": len(data),
                       "duration": round(result.duration, 2)})
            return True
        except Exception as exc:  # noqa: BLE001 - 语音失败不能影响文字回复
            self._log({"kind": "voice_error",
                       "error": f"{type(exc).__name__}: {exc}"})
            return False

    def _send_sticker(self, msg: Inbound, sticker_id: str) -> bool:
        from ...persona import stickers

        path = stickers.path_of(sticker_id)
        if path is None:
            self._log({"kind": "sticker_error", "reason": "未知或缺失的表情包",
                       "sticker": sticker_id})
            return False
        try:
            data = path.read_bytes()
            url = self.client.upload_asset(data, path.name, "image/png")
            self.client.send_image(self._reply_target(msg), url,
                                   channel_type=msg.channel_type)
            self._log({"kind": "sticker_sent", "sticker": sticker_id})
            return True
        except Exception as exc:  # noqa: BLE001
            self._log({"kind": "sticker_error",
                       "error": f"{type(exc).__name__}: {exc}"})
            return False

    # ---------------------------------------------------------------- 记忆
    def _record(self, msg: Inbound, user_text: str,
                reply_text: str | list[str]) -> None:
        if not self.memory:
            return
        self.memory.add_turn("user", user_text, msg.message_id, msg.image_url)
        parts = reply_text if isinstance(reply_text, (list, tuple)) else [reply_text]
        for part in parts:
            body = str(part or "").strip()
            if body:
                self.memory.add_turn("assistant", body, "", "")
        self._turns_since_facts += 1

    def _handle_memory_command(self, text: str) -> None:
        if not self.memory:
            return
        body = text.strip()
        for prefix in ("记住", "记一下", "帮我记住"):
            if body.startswith(prefix):
                fact = body[len(prefix):].lstrip("：:，, ").strip()
                if 2 <= len(fact) <= 120:
                    self.memory.remember(fact)
                return
        for prefix in ("忘掉", "忘记", "删掉记忆"):
            if body.startswith(prefix):
                word = body[len(prefix):].lstrip("：:，, ").strip()
                if word:
                    self.memory.forget(word)
                return

    def _maybe_extract_facts(self) -> None:
        if not (self.cfg.kook_memory and self.memory):
            return
        if self._turns_since_facts < 4:
            return
        self._turns_since_facts = 0
        dialogue = self.memory.dialogue(16)
        if not dialogue.strip():
            return
        result = self.llm.complete(chat_mod.extract_facts_prompt(dialogue))
        if result.get("usage"):
            self._log_usage(result, note="KOOK 记忆抽取")
        if not result.get("ok"):
            return
        for fact in chat_mod.parse_facts(result.get("text", "")):
            self.memory.remember(fact)

    def _maybe_summarize(self) -> None:
        if not (self.cfg.kook_memory and self.memory):
            return
        trigger = max(4, int(self.cfg.kook_summary_trigger or 24))
        batch = self.memory.compression_batch(trigger=trigger, keep=8)
        if not batch:
            return
        result = self.llm.complete(chat_mod.summarize_prompt(batch["turns"]))
        if result.get("usage"):
            self._log_usage(result, note="KOOK 记忆压缩")
        if not result.get("ok"):
            self._log({"kind": "summary_error", "error": result.get("error", "")})
            return
        summary = chat_mod.parse_summary(result.get("text", ""))
        if not summary:
            self._log({"kind": "summary_empty"})
            return
        self.memory.add_summary(summary, batch["first_id"], batch["last_id"],
                                batch["count"])
        self._log({"kind": "memory_summary", "turns": batch["count"],
                   "chars": len(summary)})

    # ---------------------------------------------------------------- 桌宠
    def _mirror_to_pet(self, text: str) -> None:
        if not self.cfg.kook_pet_say or not text:
            return
        try:
            import requests

            requests.post(f"{self.cfg.overlay_url.rstrip('/')}/api/say",
                          json={"text": text}, timeout=2)
        except Exception:
            pass

    # ---------------------------------------------------------------- 日志
    def _log(self, payload: dict) -> None:
        row = {"ts": time.time(), **payload}
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _log_usage(self, result: dict, note: str, msg: Inbound | None = None) -> None:
        append_usage(result, note, path=self.work_dir / "usage.jsonl",
                     msg_id=msg.message_id if msg else "",
                     target_id=self._reply_target(msg) if msg else "")


# ---------------------------------------------------------------- 运行
def check(cfg: LiveConfig) -> int:
    """自检：token、LLM、记忆库、网关。不进入消息循环。"""
    token_set = bool(str(cfg.kook_bot_token or "").strip())
    print(f"KOOK Bot Token：{'已配置' if token_set else '未配置'}", flush=True)
    print(f"LLM Key：{'已配置' if cfg.llm_api_key else '未配置'}", flush=True)
    print(f"人设：{cfg.kook_persona or 'companion'}", flush=True)
    print(f"长期记忆：{'开' if cfg.kook_memory else '关'}", flush=True)
    try:
        proactive_minutes = max(10, int(cfg.kook_proactive_minutes or 60))
    except (TypeError, ValueError):
        proactive_minutes = 60
    print(f"主动消息：{'开' if cfg.kook_proactive else '关'}"
          f"（{proactive_minutes} 分钟）", flush=True)
    if cfg.kook_memory:
        memory = Memory()
        print(f"记忆库：{memory.path}（{memory.stats()}）", flush=True)
    if not token_set:
        return 2
    try:
        url = KookClient(cfg.kook_bot_token).gateway()
    except KookError as exc:
        print(f"网关校验失败：{exc}", flush=True)
        return 3
    print(f"网关：{url.split('?')[0]}（OK）", flush=True)
    return 0


def run(cfg: LiveConfig) -> int:
    """常驻入口；断线自动重连，收到 Ctrl+C 正常退出。

    先用 ``logs/kook/service.lock`` 占位，避免桌宠拉起一份、手动又开一份，
    同一条消息被回复两次。锁只记 pid/时间，不记 token。
    """
    from .supervisor import acquire_service_lock, release_service_lock

    if not str(cfg.kook_bot_token or "").strip():
        print("缺少 kook_bot_token；去 KOOK 开发者中心 → 机器人 → WebSocket 拿 Token，"
              "填到设置页或 config.json。", flush=True)
        return 2
    if not acquire_service_lock():
        print("[kook] 已有 KOOK 监控在跑，本进程退出（锁：logs/kook/service.lock）。",
              flush=True)
        return 0
    try:
        return _run_service(cfg)
    finally:
        release_service_lock()


def _run_service(cfg: LiveConfig) -> int:
    if not str(cfg.kook_bot_token or "").strip():
        print("缺少 kook_bot_token；去 KOOK 开发者中心 → 机器人 → WebSocket 拿 Token，"
              "填到设置页或 config.json。", flush=True)
        return 2
    if not cfg.llm_api_key:
        print("缺少 llm_api_key；先配置模型 Key，否则只能回固定提示。", flush=True)

    memory = Memory() if cfg.kook_memory else None
    client = KookClient(cfg.kook_bot_token)
    service = KookService(cfg, memory, client)
    event_log = service.log_path
    try:
        proactive_minutes = max(10, int(cfg.kook_proactive_minutes or 60))
    except (TypeError, ValueError):
        proactive_minutes = 60
    service._log({"kind": "started", "persona": cfg.kook_persona,
                  "memory": bool(cfg.kook_memory),
                  "proactive": bool(cfg.kook_proactive),
                  "proactive_minutes": proactive_minutes})
    service.start()
    print(f"[kook] 聊天已启动：{cfg.kook_persona} · 记忆="
          f"{'开' if cfg.kook_memory else '关'} · 主动消息="
          f"{'开' if cfg.kook_proactive else '关'}", flush=True)
    print(f"[kook] 事件日志：{event_log}", flush=True)
    print("[kook] Ctrl+C 退出。", flush=True)

    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    session_id = ""
    last_sn = 0
    failures = 0
    try:
        while True:
            try:
                gateway = client.gateway()
                url = gateway
                if session_id:
                    sep = "&" if "?" in url else "?"
                    url += (f"{sep}resume=1&sn={last_sn}"
                            f"&session_id={session_id}")
                with connect(url, open_timeout=12, close_timeout=5,
                             ping_interval=None, max_size=4 * 1024 * 1024) as ws:
                    failures = 0
                    hello = json.loads(ws.recv(timeout=8))
                    if int(hello.get("s") or 0) != 1:
                        raise KookError(f"握手失败：{hello}")
                    hd = hello.get("d") if isinstance(hello.get("d"), dict) else {}
                    if int(hd.get("code") or 0) != 0:
                        raise KookError(f"握手失败 code={hd.get('code')}")
                    session_id = str(hd.get("session_id") or session_id)
                    service._log({"kind": "connected", "session": bool(session_id)})
                    print("[kook] WebSocket 已连接", flush=True)
                    next_ping = time.monotonic() + 30
                    pong_deadline = 0.0
                    while True:
                        now = time.monotonic()
                        if pong_deadline and now > pong_deadline:
                            raise TimeoutError("心跳超时")
                        if now >= next_ping:
                            ws.send(json.dumps({"s": 2, "sn": last_sn}))
                            pong_deadline = now + 6
                            next_ping = now + 30
                        try:
                            raw = ws.recv(timeout=1)
                        except TimeoutError:
                            continue
                        if isinstance(raw, (bytes, bytearray)):
                            raw = raw.decode("utf-8", "replace")
                        packet = json.loads(raw)
                        sig = int(packet.get("s") or 0)
                        if sig == 0:
                            sn = packet.get("sn")
                            if isinstance(sn, int):
                                last_sn = sn
                            data = packet.get("d")
                            if isinstance(data, dict):
                                service.on_event(data)
                        elif sig == 3:
                            pong_deadline = 0.0
                        elif sig == 5:
                            raise ConnectionError(f"服务端要求重连：{packet.get('d')}")
                        elif sig == 6:
                            service._log({"kind": "resume_ok"})
            except KeyboardInterrupt:
                break
            except (ConnectionClosed, OSError, TimeoutError, ConnectionError) as exc:
                failures += 1
                service._log({"kind": "disconnect",
                              "error": f"{type(exc).__name__}: {exc}",
                              "failures": failures})
                print(f"[kook] 连接中断：{type(exc).__name__}: {exc}；"
                      f"{min(30, 2 ** min(failures, 5))} 秒后重连", flush=True)
                time.sleep(min(30, 2 ** min(failures, 5)))
            except (KookError, ValueError, json.JSONDecodeError) as exc:
                service._log({"kind": "fatal", "error": f"{type(exc).__name__}: {exc}"})
                print(f"[kook] 无法继续：{exc}", flush=True)
                break
    finally:
        service.stop()
        if memory:
            memory.close()
        service._log({"kind": "stopped"})
        print("\n[kook] 已停止。", flush=True)
    return 0


__all__ = ["Inbound", "KookService", "parse_event", "inbound_from_message",
           "check", "run"]
