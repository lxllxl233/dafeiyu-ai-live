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
from ..bili_video import extract_video_links, learn_shared_video, shared_video_context
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

    # 主动消息再多也要有个上限，避免一天到晚刷屏。
    _PROACTIVE_DAILY_CAP = 6

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
        self._proactive_day = ""
        self._proactive_sent_today = 0
        self._proactive_retry_at = 0.0
        # WebSocket 和图片轮询可能拿到同一条消息，处理前统一去重。
        self._seen_ids: set[str] = set()
        self._seen_lock = threading.Lock()
        self._media_history: list[tuple[bool, bool]] = []

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
            self.cfg.kook_proactive_minutes = int(fresh.kook_proactive_minutes or 20)
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
        if now < float(self._proactive_retry_at or 0.0):
            return False
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._proactive_day != today:
            self._proactive_day = today
            self._proactive_sent_today = 0
        if self._proactive_sent_today >= self._PROACTIVE_DAILY_CAP:
            return False
        try:
            minutes = int(getattr(self.cfg, "kook_proactive_minutes", 20) or 20)
        except (TypeError, ValueError):
            minutes = 20
        interval = max(10, minutes) * 60
        idle = now - float(self._last_active_at or 0.0)
        if idle < interval:
            return False
        if self._last_proactive_at and now - self._last_proactive_at < interval:
            return False
        roll = random.random() if chance is None else float(chance)
        if roll > 0.5:
            return False
        idle_minutes = max(1, int(idle / 60))
        self._last_proactive_at = now
        self._last_active_at = now
        self._proactive_armed = False
        sent = self._send_proactive(idle_minutes)
        if sent:
            self._proactive_sent_today += 1
        else:
            # 人格选择这次不开口，或发送失败；下次间隔到了再问一次。
            # 给一个 2 小时退避，避免反复调用模型问“要不要说话”。
            self._proactive_armed = True
            self._proactive_retry_at = now + 2 * 3600
        return sent

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
        recent_own = self._recent_own_texts()
        topics = self._repeat_topics(recent_own)
        texts: list[str] = []
        reply: dict = {}
        text = ""
        for attempt in range(2):
            try:
                messages = self._build_messages(
                    "", proactive=True, idle_minutes=idle_minutes)
            except Exception as exc:  # noqa: BLE001
                self._log({"kind": "proactive_error",
                           "error": f"{type(exc).__name__}: {exc}"})
                return False
            if attempt and topics:
                messages.append({
                    "role": "user",
                    "content": (
                        "刚才那条又在讲「" + "、".join(topics) + "」。"
                        "换一个完全不同的主题；绝对不要再提这些词，也不要换个说法"
                        "讲同一件事。如果想不到新的，就问对方一个具体的问题。\n\n"
                        + chat_mod.build_format_reminder(
                            allow_voice=bool(self.cfg.kook_allow_voice))),
                })
            model = chat_mod.pick_model(self.cfg, messages)
            result = self._complete(messages, model)
            if result.get("usage"):
                self._log_usage(result, note="KOOK 主动消息", msg=msg)
            if not result.get("ok"):
                self._log({"kind": "proactive_llm_error",
                           "error": result.get("error", "")})
                return False
            reply = chat_mod.parse_reply(
                result.get("text", ""), allow_voice=bool(self.cfg.kook_allow_voice))
            reply = self._limit_media(reply, "")
            texts = [str(part).strip() for part in (reply.get("texts") or [])
                     if str(part).strip()]
            if not texts:
                single = str(reply.get("text") or "").strip()
                texts = [single] if single else []
            if not texts:
                # 人格可以主动选择这次不开口；不要把它当成错误重试。
                self._log({"kind": "proactive_declined",
                           "idle_minutes": idle_minutes})
                return False
            text = "\n".join(texts)
            repeated = [topic for topic in topics if topic in text]
            if not repeated:
                break
            self._log({"kind": "proactive_repeat", "topics": repeated,
                       "attempt": attempt + 1})
            if attempt == 0:
                continue
            self._log({"kind": "proactive_skipped_repeat", "topics": topics})
            return False
        sent = self._send_texts(msg, texts)

        voice_ok = False
        if reply.get("voice") and self.cfg.kook_allow_voice:
            voice_ok = self._send_voice(msg, str(reply.get("voice_text") or text))

        sticker_ok = False
        sticker_id = str(reply.get("sticker") or "").strip()
        if sticker_id and self.cfg.kook_stickers:
            sticker_ok = self._send_sticker(msg, sticker_id)
        self._note_media(voice_ok, sticker_ok)

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

    def _recent_own_texts(self, limit: int = 8) -> list[str]:
        if not self.memory:
            return []
        try:
            context = self.memory.context(int(self.cfg.kook_context_turns or 12))
        except Exception:  # noqa: BLE001 - 读记忆失败不影响主动消息
            return []
        rows = [str(turn.get("text") or "") for turn in context.get("turns", [])
                if str(turn.get("role") or "") == "assistant"]
        return [text for text in rows if text.strip()][-limit:]

    @staticmethod
    def _repeat_topics(recent: list[str], limit: int = 3) -> list[str]:
        """从最近几条自己说的话里找反复出现的主题字。

        不引入分词依赖：取「在至少 3 条里都出现过的汉字」当主题，
        去掉“我/你/的/了”这类高频字。猫连续出现 4 次时会被抓出来，
        主动消息就不会又拿猫开场。
        """
        common = set(
            "的一是不了在人有我他她它这那为之大来以个中上们到说国和地也子时道"
            "出而要于就下得可你年生自会后能对着事其里所去行过家十用发天如然作"
            "方成者多日都三二无同么经法当起与好看学进种将还分此心前又定见只主"
            "没公从想什怎样现点觉真做给对开很太先再才已经因所但刚今明昨早晚午"
            "秒份号岁次些每各另别外再最更挺蛮特别比较稍微差点儿几乎差不多小大老新"
            "我们你们他们她们它们咱们自己这个那个哪个哪些什么怎么怎样这样那样"
            "一下一个一些一直一起一边一定一样一般一切一点有点有些有些时候"
        )
        counts: dict[str, int] = {}
        for text in recent[-6:]:
            for char in set(str(text or "")):
                if char in common or not ("\u4e00" <= char <= "\u9fff"):
                    continue
                counts[char] = counts.get(char, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return [char for char, count in ranked if count >= 3][:limit]

    # ---------------------------------------------------------------- 主流程
    def handle(self, msg: Inbound) -> dict:
        # 私信图片的 WebSocket 事件有时只带“有图”却不带可用 URL；按 msg_id
        # 从 /direct-message/list 取一次权威内容再走视觉，频道图片仍走原样。
        if msg.channel_type == "PERSON" and msg.msg_type in (2, 10):
            self._refresh_private_image(msg)
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
        self._proactive_retry_at = 0.0

        caption = msg.text
        video_context = ""
        if caption and extract_video_links(caption) and self._video_share_enabled():
            learned = self._learn_shared_video(msg, caption)
            if not learned.get("ok"):
                text = "这个视频我这边没打开，换个链接或稍后再试？"
                sent = self._send_text(msg, text)
                self._log({"kind": "video_error",
                           "error": str(learned.get("error") or "")[:300]})
                self._record(msg, caption, text)
                return {"ok": bool(sent), "action": "text", "reason": "video_error"}
            if not learned.get("skip"):
                video_context = shared_video_context(
                    learned, sharer=msg.author_name)
                video = learned.get("video") or {}
                self._log({
                    "kind": "video_learned",
                    "bvid": str(video.get("bvid") or "")[:20],
                    "cached": bool(learned.get("cached")),
                    "learned": bool(learned.get("learned")),
                    "chars": len(str(learned.get("memory") or "")),
                    "warning": str(learned.get("warning") or "")[:200],
                })
        try:
            messages = self._build_messages(
                caption, msg.image_url, video_context=video_context)
        except KookError as exc:
            self._log({"kind": "image_error", "error": str(exc)})
            text = "这张图我这边没加载出来，再发一次？"
            sent = self._send_text(msg, text)
            return {"ok": bool(sent), "action": "text", "reason": "image_error"}
        model = chat_mod.pick_model(self.cfg, messages)
        started = time.time()
        result = self._complete(messages, model)
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
        reply = self._limit_media(reply, caption)
        sent = self._send_texts(msg, texts)
        if not sent:
            self._log({"kind": "send_error", "reason": "全部切片都失败"})

        sticker_ok = False
        sticker_id = str(reply.get("sticker") or "").strip()
        if sticker_id and self.cfg.kook_stickers:
            sticker_ok = self._send_sticker(msg, sticker_id)

        voice_ok = False
        if reply.get("voice") and self.cfg.kook_allow_voice:
            voice_ok = self._send_voice(msg, str(reply.get("voice_text") or text))
        self._note_media(voice_ok, sticker_ok)

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

    def _refresh_private_image(self, msg: Inbound) -> None:
        """私聊图片事件缺 URL 时，用 direct-message/list 按 msg_id 补全。"""
        if not msg.author_id or not msg.message_id:
            return
        try:
            items = self.client.direct_messages(msg.author_id, 30)
        except Exception as exc:  # noqa: BLE001 - 补全失败仍可尝试原事件
            self._log({"kind": "image_refresh_error",
                       "error": f"{type(exc).__name__}: {exc}"})
            return
        for item in items:
            item_id = str(item.get("id") or item.get("msg_id") or "")
            if item_id != msg.message_id:
                continue
            fresh = inbound_from_message(item, msg.author_id, "PERSON")
            if fresh and fresh.image_url:
                msg.image_url = fresh.image_url
                msg.image_name = fresh.image_name or msg.image_name
                self._log({"kind": "image_refreshed"})
            return

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

    # ---------------------------------------------------------------- 分享视频
    def _video_share_enabled(self) -> bool:
        """设置页改开关后下一条消息生效；读不到新配置就用启动时的值。"""
        try:
            return bool(LiveConfig.load().kook_share_video)
        except Exception:
            return bool(getattr(self.cfg, "kook_share_video", True))

    def _learn_shared_video(self, msg: Inbound, caption: str) -> dict:
        """聊天里认出 B 站视频链接：先发一句「等等」，再读公开文字写记忆。"""
        if not extract_video_links(caption):
            return {"ok": True, "skip": True}
        ack = {"sent": False}

        def on_slow() -> None:
            if ack["sent"]:
                return
            ack["sent"] = True
            self._send_text(msg, "等等，我看看这个视频…")

        def on_usage(result: dict, note: str) -> None:
            self._log_usage(result, note=note, msg=msg)

        try:
            return learn_shared_video(
                self.cfg, caption,
                persona=self.cfg.kook_persona or "companion",
                llm=self.llm, on_slow=on_slow, on_usage=on_usage)
        except Exception as exc:  # noqa: BLE001 - 一条坏链接不能带崩聊天
            return {"ok": False, "kind": "exception",
                    "error": f"{type(exc).__name__}: {exc}"}

    # ---------------------------------------------------------------- 提示词
    def _build_messages(self, caption: str, image_url: str = "", *,
                        proactive: bool = False,
                        idle_minutes: int = 0,
                        video_context: str = "") -> list[dict]:
        from ...persona import stickers

        facts = []
        context = {"summaries": [], "turns": []}
        recent_own: list[str] = []
        query = caption or ("图片" if image_url else ("主动聊天" if proactive else "聊天"))
        if self.cfg.kook_memory and self.memory:
            context = self.memory.context(int(self.cfg.kook_context_turns or 12))
            recent_own = [str(turn.get("text") or "") for turn in context.get("turns", [])
                          if str(turn.get("role") or "") == "assistant"]
            if proactive:
                # 主动消息不要再用“最常被引用”的事实：按主题分簇，并避开
                # 最近已经说过的话，否则每次开口都是同一只猫。
                facts = self.memory.diverse_facts(6, avoid_texts=recent_own[-8:])
            else:
                facts = self.memory.search(query, 6)
        try:
            # 训练记忆的总开关允许在设置页随时改；这里读一次最新配置，
            # 不用重启 KOOK 进程（其它字段仍按服务启动时的配置）。
            apply_training = bool(getattr(self.cfg, "bili_training_apply_kook", True))
            try:
                apply_training = bool(LiveConfig.load().bili_training_apply_kook)
            except Exception:
                pass
            stack = PersonaStack.load(
                self.cfg.kook_persona or "companion", live=False,
                training=apply_training, include_inactive=False)
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
        # 让角色知道自己最近发媒体的频率，避免形成“每条都带语音/表情”的习惯。
        media_hint = self._media_hint()
        if media_hint:
            messages.append({"role": "system", "content": media_hint})
        if video_context:
            # 刚学的视频：公开文字 + 新写下的记忆。放在格式提醒之前，
            # 保证最后一条仍是 JSON 协议提醒。
            messages.append({"role": "system", "content": video_context})
        # 历史里的助手消息是纯文本，模型会照着继续写；在最后一条用户消息
        # 之前再提醒一次 JSON 协议，voice/sticker 才不会被“聊嗨了”吞掉。
        messages.append({
            "role": "system",
            "content": chat_mod.build_format_reminder(
                allow_voice=bool(self.cfg.kook_allow_voice)),
        })
        if image_url:
            data, mime = self.client.download(image_url)
            messages.append(chat_mod.image_message(
                caption or "（对方发来一张图。直接看图片内容回答，不要声称看不到图。）",
                data, mime))
        elif proactive:
            messages.append({"role": "user",
                             "content": chat_mod.build_proactive_prompt(
                                 idle_minutes, recent=recent_own)})
        else:
            messages.append({"role": "user",
                             "content": chat_mod.build_user_prompt(caption, False)})
        return messages

    # ---------------------------------------------------------------- 发送
    def _complete(self, messages: list[dict], model: str) -> dict:
        """优先用 JSON 模式；模型/网关不支持时退回普通补全。"""
        try:
            result = self.llm.complete(messages, model=model, json_mode=True)
        except TypeError:
            return self.llm.complete(messages, model=model)
        if result.get("ok"):
            return result
        # 有些兼容网关开了 response_format 会返回空 content；重试一次普通模式，
        # 别让一条正常消息因为 JSON 模式不可用而丢掉。
        try:
            fallback = self.llm.complete(messages, model=model)
        except TypeError:
            return result
        return fallback if fallback.get("ok") else result

    def _limit_media(self, reply: dict, cue: str) -> dict:
        """一次回复最多带一种附加媒体；对方明确同时要时才允许两个都发。"""
        wants_voice = self._wants_voice(cue)
        wants_sticker = self._wants_sticker(cue)
        voice = bool(reply.get("voice")) and bool(self.cfg.kook_allow_voice)
        sticker = str(reply.get("sticker") or "").strip()
        if sticker and not self.cfg.kook_stickers:
            sticker = ""
        if voice and sticker and not (wants_voice and wants_sticker):
            if wants_sticker and not wants_voice:
                voice = False
            else:
                sticker = ""
        reply["voice"] = voice
        if not voice:
            reply["voice_text"] = ""
        if sticker:
            reply["sticker"] = sticker
        else:
            reply.pop("sticker", None)
        return reply

    def _note_media(self, voice: bool, sticker: bool) -> None:
        self._media_history.append((bool(voice), bool(sticker)))
        del self._media_history[:-8]

    def _media_hint(self) -> str:
        """把最近几条回复的媒体使用情况告诉自己，避免形成每条都带的习惯。"""
        rows = self._media_history[-6:]
        if len(rows) < 3:
            return ""
        voice = sum(1 for used_voice, _ in rows if used_voice)
        sticker = sum(1 for _, used_sticker in rows if used_sticker)
        if voice <= 1 and sticker <= 1:
            return ""
        if voice >= 2 or sticker >= 2:
            return (
                f"你最近 {len(rows)} 条回复里带过 {voice} 次语音、{sticker} 次表情包，"
                "已经有点多了。这次除非对方明确要求，否则不要再带语音或表情包，"
                "用纯文字接话。")
        return (
            f"你最近 {len(rows)} 条回复里带过 {voice} 次语音、{sticker} 次表情包。"
            "这次先判断有没有必要：普通接话就纯文字，别形成每条都带媒体的习惯。")

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
        result = self.llm.complete(chat_mod.extract_facts_prompt(
            dialogue, existing=[row.get("text") or "" for row in self.memory.facts(30)]))
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
        proactive_minutes = max(10, int(cfg.kook_proactive_minutes or 20))
    except (TypeError, ValueError):
        proactive_minutes = 20
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
        proactive_minutes = max(10, int(cfg.kook_proactive_minutes or 20))
    except (TypeError, ValueError):
        proactive_minutes = 20
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
