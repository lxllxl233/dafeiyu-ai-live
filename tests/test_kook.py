# -*- coding: utf-8 -*-
"""KOOK：事件解析、HTTP 路径、回复与去重。全部离线，不连真实机器人。"""
from __future__ import annotations

import io
import json
import time
import wave

from dafeiyu.core.config import LiveConfig
from dafeiyu.live import bili_video as bv
from dafeiyu.live.chat_memory import Memory
from dafeiyu.live.kook import service as kook_service
from dafeiyu.live.kook.client import KookClient
from dafeiyu.live.kook.service import (
    Inbound, KookService, inbound_from_message, parse_event,
)
from dafeiyu.live.kook.usage import summary as kook_usage
from dafeiyu.persona.training import TrainingMemoryStore


def _event(**over):
    data = {
        "channel_type": "PERSON", "type": 1, "target_id": "u1",
        "author_id": "u1", "content": "你好", "msg_id": "m1",
        "msg_timestamp": 1, "extra": {"author": {"username": "小鱼", "bot": False}},
    }
    data.update(over)
    return data


def test_parse_event_text_and_mentions():
    msg = parse_event(_event(content="(met)bot1(met) 在吗"))
    assert msg is not None
    assert msg.text == "在吗"
    assert msg.channel_type == "PERSON" and msg.author_id == "u1"


def test_parse_event_marks_bot_and_reads_images():
    assert parse_event(_event(extra={"author": {"bot": True}})).author_bot is True
    image = parse_event(_event(type=2, content="https://x/y.png"))
    assert image is not None and image.image_url == "https://x/y.png"
    assert parse_event(_event(type=3, content="https://x/y.mp4")) is None
    assert parse_event(_event(author_id="1")) is None


def test_client_uses_direct_message_for_person():
    calls = []
    client = KookClient.__new__(KookClient)
    client.token = "t"

    def fake(method, path, **kw):
        calls.append((method, path, kw))
        return {"msg_id": "sent"}

    client._call = fake
    assert client.send_text("u1", "hi", "PERSON")["ok"] is True
    assert calls[0][1] == "/direct-message/create"
    client.send_text("c1", "hi", "GROUP")
    assert calls[1][1] == "/message/create"


def test_service_replies_to_private_author_not_self(tmp_path):
    class Client:
        def __init__(self):
            self.sent = []

        def send_text(self, target_id, text, **kw):
            self.sent.append((target_id, text, kw))
            return {"ok": True}

    class LLM:
        def complete(self, messages, model=None):
            return {"ok": True, "text": '{"reply": "收到", "voice": false}'}

    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False, kook_stickers=False)
    client = Client()
    service = KookService(cfg, None, client, llm=LLM(),
                          work_dir=tmp_path, log_path=tmp_path / "events.jsonl")
    # KOOK 私聊事件的 target_id 是机器人自己；回复必须用 author_id。
    out = service.handle(Inbound(message_id="m1", target_id="bot-1",
                                 channel_type="PERSON", author_id="u1",
                                 msg_type=1, text="在吗"))
    assert out["ok"] is True
    assert client.sent and client.sent[0][0] == "u1"
    assert client.sent[0][2]["channel_type"] == "PERSON"
    assert service._last_target == "u1"


def test_service_refreshes_private_image_from_direct_list(tmp_path):
    class Client:
        def __init__(self):
            self.sent = []

        def direct_messages(self, target_id, limit=20):
            return [{
                "id": "img1", "type": 2,
                "content": "https://img.kookapp.cn/private.jpg",
                "author": {"id": "u1", "username": "小鱼", "bot": False},
            }]

        def download(self, url, max_bytes=10 * 1024 * 1024):
            return b"jpeg", "image/jpeg"

        def send_text(self, target_id, text, **kw):
            self.sent.append((target_id, text, kw))
            return {"ok": True}

    class LLM:
        def __init__(self):
            self.seen = []

        def complete(self, messages, model=None):
            self.seen = messages
            return {"ok": True, "text": '{"reply": "看到了", "voice": false}'}

    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False, kook_stickers=False)
    client = Client()
    llm = LLM()
    service = KookService(cfg, None, client, llm=llm,
                          work_dir=tmp_path, log_path=tmp_path / "events.jsonl")
    # WebSocket 只给了“有图”没有 URL；服务应查 direct-message/list 补全。
    out = service.handle(Inbound(message_id="img1", target_id="bot-1",
                                 channel_type="PERSON", author_id="u1",
                                 msg_type=2))
    assert out["ok"] is True
    user = llm.seen[-1]
    assert isinstance(user["content"], list)
    assert any(part.get("type") == "image_url" for part in user["content"])
    assert client.sent[0][0] == "u1"
    rows = [json.loads(line) for line in
            (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["kind"] == "image_refreshed" for row in rows)


def test_parse_event_reads_image_card():
    card = [{"type": "card", "modules": [
        {"type": "container", "elements": [
            {"type": "image", "src": "https://img.kookapp.cn/card.png"}]}]}]
    msg = parse_event(_event(type=10, content=json.dumps(card)))
    assert msg is not None and msg.image_url == "https://img.kookapp.cn/card.png"
    assert parse_event(_event(type=10, content="[]")) is None


def test_inbound_from_message_reads_image():
    msg = inbound_from_message({
        "id": "img1", "type": 2, "content": "https://x/y.png",
        "create_at": 1, "author": {"id": "u1", "username": "小鱼", "bot": False},
        "attachments": {"url": "https://x/y.png", "name": "y.png"},
    }, "c1")
    assert msg is not None and msg.image_url == "https://x/y.png"
    assert msg.author_name == "小鱼" and msg.message_id == "img1"
    assert inbound_from_message({"id": "t1", "type": 9}, "c1") is None


def test_client_uploads_and_sends_media():
    calls = []
    client = KookClient.__new__(KookClient)
    client.token = "t"

    def fake(method, path, **kw):
        calls.append((method, path, kw))
        if path == "/asset/create":
            return {"url": "https://img.kookapp.cn/a.png"}
        return {"msg_id": "sent"}

    client._call = fake
    assert client.upload_asset(b"png", "a.png", "image/png") == "https://img.kookapp.cn/a.png"
    assert calls[0][1] == "/asset/create" and "files" in calls[0][2]
    assert client.send_image("c1", "https://img.kookapp.cn/a.png")["ok"] is True
    assert calls[1][2]["body"]["type"] == 2
    assert client.send_audio_card("c1", "https://img.kookapp.cn/a.wav",
                                  title="语音")["ok"] is True
    card = calls[2][2]["body"]
    assert card["type"] == 10
    assert '"audio"' in card["content"] and "a.wav" in card["content"]
    assert "重听" in card["content"] and '"click": "link"' in card["content"]


def _wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 1600)
    return buf.getvalue()


def test_service_sends_voice_as_mp3(tmp_path):
    class Client:
        def __init__(self):
            self.audio = []
            self.uploads = []

        def send_text(self, target_id, text, **kw):
            return {"ok": True}

        def upload_asset(self, data, filename, content_type="application/octet-stream"):
            self.uploads.append((filename, content_type, len(data)))
            return "https://img.kookapp.cn/" + filename

        def send_audio_card(self, target_id, url, **kw):
            self.audio.append((url, kw))
            return {"ok": True}

    class LLM:
        def complete(self, messages, model=None):
            return {"ok": True, "text":
                    '{"reply": "听我说", "voice": true, "voice_text": "听我说"}'}

    class Voice:
        def speak(self, text, voice=None):
            return type("R", (), {"ok": True, "wav": _wav_bytes(),
                                  "duration": 0.1, "voice": "fake",
                                  "error": ""})()

    cfg = LiveConfig(kook_memory=False, kook_allow_voice=True, kook_stickers=False)
    client = Client()
    service = KookService(cfg, None, client, llm=LLM(), voice=Voice(),
                          work_dir=tmp_path, log_path=tmp_path / "events.jsonl")
    out = service.handle(Inbound(message_id="m1", target_id="c1",
                                 channel_type="GROUP", author_id="u1",
                                 msg_type=1, text="发条语音"))
    assert out["voice"] is True
    assert client.audio and client.audio[0][0].endswith(".mp3")
    assert any(name.endswith(".mp3") and mime == "audio/mpeg"
               for name, mime, _ in client.uploads)
    rows = [json.loads(line) for line in
            (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["kind"] == "voice_sent" and row.get("codec") == "mp3"
               for row in rows)


def test_service_limits_media_to_sticker_when_asked(tmp_path):
    class Client:
        def __init__(self):
            self.sent = []
            self.images = []
            self.audio = []
            self.uploads = []

        def send_text(self, target_id, text, **kw):
            self.sent.append(text)
            return {"ok": True}

        def upload_asset(self, data, filename, content_type="application/octet-stream"):
            self.uploads.append((filename, content_type, len(data)))
            return "https://img.kookapp.cn/" + filename

        def send_image(self, target_id, url, **kw):
            self.images.append(url)
            return {"ok": True}

        def send_audio_card(self, target_id, url, **kw):
            self.audio.append((url, kw))
            return {"ok": True}

    class LLM:
        def complete(self, messages, model=None):
            return {"ok": True, "text":
                    '{"reply": "给你一张", "voice": true, '
                    '"voice_text": "给你一张", "sticker": "one-bowl"}'}

    class Voice:
        def speak(self, text, voice=None):
            return type("R", (), {"ok": True, "wav": b"RIFFfake",
                                  "duration": 1.2, "voice": "fake",
                                  "error": ""})()

    cfg = LiveConfig(kook_memory=False, kook_allow_voice=True, kook_stickers=True)
    service = KookService(cfg, None, Client(), llm=LLM(), voice=Voice(),
                          work_dir=tmp_path, log_path=tmp_path / "events.jsonl")
    out = service.handle(Inbound(message_id="m1", target_id="c1",
                                 channel_type="GROUP", author_id="u1",
                                 msg_type=1, text="来个表情"))
    # 对方只要表情包：即使模型两个都填了，也只留表情包。
    assert out["voice"] is False and out["sticker"] is True
    assert not service.client.audio and service.client.images


def test_plain_text_reply_does_not_force_media(tmp_path):
    class Client:
        def __init__(self):
            self.texts, self.audio, self.images, self.uploads = [], [], [], []

        def send_text(self, target_id, text, **kw):
            self.texts.append(text)
            return {"ok": True}

        def upload_asset(self, data, filename, content_type="application/octet-stream"):
            self.uploads.append((filename, content_type, len(data)))
            return "https://img.kookapp.cn/" + filename

        def send_audio_card(self, target_id, url, **kw):
            self.audio.append(url)
            return {"ok": True}

        def send_image(self, target_id, url, **kw):
            self.images.append(url)
            return {"ok": True}

    class LLM:
        def complete(self, messages, model=None):
            # 模型没按 JSON 协议回，而是像历史消息一样直接说人话。
            return {"ok": True, "text": "累就先瘫着，别硬撑。"}

    class Voice:
        def speak(self, text, voice=None):
            return type("R", (), {"ok": True, "wav": b"RIFFfake",
                                  "duration": 1.0, "voice": "fake",
                                  "error": ""})()

    cfg = LiveConfig(kook_memory=False, kook_allow_voice=True, kook_stickers=True)
    client = Client()
    service = KookService(cfg, None, client, llm=LLM(), voice=Voice(),
                          work_dir=tmp_path, log_path=tmp_path / "events.jsonl")
    out = service.handle(Inbound(message_id="m1", target_id="c1",
                                 channel_type="GROUP", author_id="u1",
                                 msg_type=1, text="今天好累啊"))
    # 纯文本就按纯文本发，不再由服务强行补语音/表情包。
    assert out["voice"] is False and out["sticker"] is False
    assert not client.audio and not client.images


def test_build_messages_puts_json_reminder_before_user(tmp_path):
    cfg = LiveConfig(kook_memory=False, kook_allow_voice=True,
                     kook_stickers=False)
    service = KookService(cfg, None, None, work_dir=tmp_path)
    messages = service._build_messages("在干嘛")
    assert messages[-1]["role"] == "user"
    assert messages[-2]["role"] == "system"
    assert "格式提醒" in messages[-2]["content"]
    assert '"voice": false' in messages[-2]["content"]


def test_repeat_topics_detects_overused_cat():
    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False,
                     kook_stickers=False)
    service = KookService(cfg, None, None, work_dir=".")
    recent = ["刚看猫在窗台", "小麦脑袋卡饭盆，猫盯着", "猫又趴键盘上了"]
    assert "猫" in service._repeat_topics(recent)
    assert service._repeat_topics(["今天上班好累", "offer 还没消息"]) == []


def test_explicit_request_forces_voice_and_sticker(tmp_path):
    class Client:
        def __init__(self):
            self.sent = []
            self.images = []
            self.audio = []

        def send_text(self, target_id, text, **kw):
            self.sent.append(text)
            return {"ok": True}

        def upload_asset(self, data, filename, content_type="application/octet-stream"):
            return "https://img.kookapp.cn/" + filename

        def send_image(self, target_id, url, **kw):
            self.images.append(url)
            return {"ok": True}

        def send_audio_card(self, target_id, url, **kw):
            self.audio.append(url)
            return {"ok": True}

    class LLM:
        def complete(self, messages, model=None):
            return {"ok": True, "text": '{"reply": "给你", "voice": false}'}

    class Voice:
        def speak(self, text, voice=None):
            return type("R", (), {"ok": True, "wav": b"RIFFfake",
                                  "duration": 1.0, "voice": "fake",
                                  "error": ""})()

    cfg = LiveConfig(kook_memory=False, kook_allow_voice=True, kook_stickers=True)
    client = Client()
    service = KookService(cfg, None, client, llm=LLM(), voice=Voice(),
                          work_dir=tmp_path, log_path=tmp_path / "events.jsonl")
    out = service.handle(Inbound(message_id="m1", target_id="c1",
                                 channel_type="GROUP", author_id="u1",
                                 msg_type=1, text="用语音和表情包回我"))
    assert out["voice"] is True and out["sticker"] is True
    assert client.audio and client.images


def test_service_uses_vision_for_inbound_image(tmp_path):
    class Client:
        def download(self, url, max_bytes=10 * 1024 * 1024):
            return b"fake-png", "image/png"

        def send_text(self, target_id, text, **kw):
            return {"ok": True}

    class LLM:
        def __init__(self):
            self.seen = []

        def complete(self, messages, model=None):
            self.seen = messages
            return {"ok": True, "text": "看到了"}

    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False, kook_stickers=False)
    llm = LLM()
    service = KookService(cfg, None, Client(), llm=llm,
                          work_dir=tmp_path, log_path=tmp_path / "events.jsonl")
    out = service.handle(Inbound(message_id="m2", target_id="u1",
                                 channel_type="PERSON", author_id="u1",
                                 msg_type=2, image_url="https://img.kookapp.cn/a.png"))
    assert out["ok"] is True
    user = llm.seen[-1]
    assert isinstance(user["content"], list)
    assert any(part.get("type") == "image_url" for part in user["content"])


def test_image_poll_only_replies_to_new_images(tmp_path):
    class Client:
        def __init__(self):
            self.items = [{"id": "old", "type": 2,
                           "content": "https://x/old.png",
                           "author": {"id": "u1", "bot": False},
                           "attachments": {"url": "https://x/old.png"}}]
            self.sent = []

        def messages(self, target_id, limit=20):
            return list(self.items)

        def download(self, url, max_bytes=10 * 1024 * 1024):
            return b"png", "image/png"

        def send_text(self, target_id, text, **kw):
            self.sent.append(text)
            return {"ok": True}

    class LLM:
        def complete(self, messages, model=None):
            return {"ok": True, "text": "看到新图了"}

    client = Client()
    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False, kook_stickers=False)
    service = KookService(cfg, None, client, llm=LLM(),
                          work_dir=tmp_path, log_path=tmp_path / "events.jsonl")
    service._poll_channels.add("c1")
    service._poll_once()                 # 第一次只记基线，不回旧图
    assert client.sent == []
    client.items.append({"id": "new", "type": 2,
                         "content": "https://x/new.png",
                         "author": {"id": "u1", "bot": False},
                         "attachments": {"url": "https://x/new.png"}})
    service._poll_once()
    assert client.sent == ["看到新图了"]


def test_service_records_chat_usage_for_console(tmp_path):
    class Client:
        def send_text(self, target_id, text, **kw):
            return {"ok": True}

    class LLM:
        def complete(self, messages, model=None):
            return {"ok": True, "text": '{"reply": "在"}', "model": "deepseek-test",
                    "usage": {"input": 120, "output": 30, "total": 150},
                    "ms": 42}

    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False, kook_stickers=False)
    service = KookService(cfg, None, Client(), llm=LLM(),
                          work_dir=tmp_path, log_path=tmp_path / "events.jsonl")
    out = service.handle(Inbound(message_id="m1", target_id="c1",
                                 channel_type="GROUP", author_id="u1",
                                 msg_type=1, text="在吗"))
    assert out["ok"] is True

    rows = [json.loads(line) for line in
            (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["msg"] == "llm_usage" and rows[0]["note"] == "KOOK 聊天"
    assert rows[0]["model"] == "deepseek-test"
    assert rows[0]["input"] == 120 and rows[0]["output"] == 30
    assert rows[0]["total"] == 150 and rows[0]["msg_id"] == "m1"

    usage = kook_usage(tmp_path / "usage.jsonl")
    assert usage["total"] == 150 and usage["calls"] == 1
    assert usage["steps"][-1]["kind"] == "KOOK 聊天"


def test_service_sends_multiple_messages(tmp_path):
    class Client:
        def __init__(self):
            self.sent = []

        def send_text(self, target_id, text, **kw):
            self.sent.append((target_id, text, kw))
            return {"ok": True}

    class LLM:
        def complete(self, messages, model=None):
            return {"ok": True, "model": "fake", "text": json.dumps({
                "messages": ["在", "刚看到一只猫", "像你"], "voice": False})}

    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False, kook_stickers=False)
    client = Client()
    service = KookService(cfg, None, client, llm=LLM(),
                          work_dir=tmp_path, log_path=tmp_path / "events.jsonl")
    out = service.handle(Inbound(message_id="m1", target_id="c1",
                                 channel_type="GROUP", author_id="u1",
                                 msg_type=1, text="在吗"))
    assert out["ok"] is True
    assert out["texts"] == ["在", "刚看到一只猫", "像你"]
    assert [text for _, text, _ in client.sent] == ["在", "刚看到一只猫", "像你"]
    assert client.sent[0][2]["quote"] == "m1"
    assert client.sent[1][2]["quote"] == "" and client.sent[2][2]["quote"] == ""


def test_parse_reply_allows_four_messages_but_caps_at_four():
    from dafeiyu.live.chat import parse_reply

    raw = json.dumps({"messages": ["一", "二", "三", "四", "五"], "voice": False})
    reply = parse_reply(raw, allow_voice=True)
    assert reply["texts"] == ["一", "二", "三", "四"]


def test_media_limit_keeps_at_most_one_attachment(tmp_path):
    cfg = LiveConfig(kook_memory=False, kook_allow_voice=True, kook_stickers=True)
    service = KookService(cfg, None, None, work_dir=tmp_path)
    reply = {"text": "嗯", "voice": True, "voice_text": "嗯",
             "sticker": "one-bowl"}
    service._limit_media(reply, "今天好累啊")
    assert reply["voice"] is True
    assert "sticker" not in reply
    # 对方明确同时要时，才允许语音 + 表情包都发。
    reply = {"text": "嗯", "voice": True, "voice_text": "嗯",
             "sticker": "one-bowl"}
    service._limit_media(reply, "用语音和表情包回我")
    assert reply["voice"] is True and reply["sticker"] == "one-bowl"


def test_proactive_sends_after_idle(tmp_path):
    class Client:
        def __init__(self):
            self.sent = []

        def send_text(self, target_id, text, **kw):
            self.sent.append((target_id, text, kw))
            return {"ok": True}

    class LLM:
        def complete(self, messages, model=None):
            return {"ok": True, "model": "fake",
                    "text": '{"messages": ["刚想到个事"]}', "usage": {
                        "input": 10, "output": 4, "total": 14}}

    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False,
                     kook_stickers=False, kook_proactive=True,
                     kook_proactive_minutes=10)
    client = Client()
    service = KookService(cfg, None, client, llm=LLM(),
                          work_dir=tmp_path, log_path=tmp_path / "events.jsonl")
    service._last_target = "c1"
    service._last_channel_type = "GROUP"
    service._last_active_at = 1000.0
    service._proactive_armed = True

    assert service._maybe_proactive(now=1000.0 + 601, chance=0.0) is True
    assert client.sent == [("c1", "刚想到个事", {
        "channel_type": "GROUP", "quote": "", "reply_msg_id": ""})]
    assert service._last_active_at == 1000.0 + 601
    assert service._proactive_armed is False
    assert service._maybe_proactive(now=1000.0 + 1800, chance=0.0) is False

    rows = [json.loads(line) for line in
            (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["kind"] == "proactive" for row in rows)
    usage = kook_usage(tmp_path / "usage.jsonl")
    assert usage["total"] == 14 and usage["steps"][-1]["note"] == "KOOK 主动消息"


def test_proactive_respects_toggle_and_interval(tmp_path):
    class Client:
        def send_text(self, *args, **kwargs):
            raise AssertionError("不应该发送")

    cfg = LiveConfig(kook_memory=False, kook_proactive=False,
                     kook_proactive_minutes=10)
    service = KookService(cfg, None, Client(), work_dir=tmp_path,
                          log_path=tmp_path / "events.jsonl")
    service._last_target = "c1"
    service._last_active_at = 0.0
    service._proactive_armed = True
    assert service._maybe_proactive(now=10000.0, chance=0.0) is False

    cfg.kook_proactive = True
    service._last_active_at = 10000.0
    assert service._maybe_proactive(now=10000.0 + 60, chance=0.0) is False


def test_proactive_daily_cap_and_decline_rearms(tmp_path):
    class Client:
        def send_text(self, *args, **kwargs):
            raise AssertionError("不应该发送")

    cfg = LiveConfig(kook_memory=False, kook_proactive=True,
                     kook_proactive_minutes=10)
    service = KookService(cfg, None, Client(), work_dir=tmp_path,
                          log_path=tmp_path / "events.jsonl")
    service._last_target = "c1"
    now = time.time()
    service._last_active_at = now - 10000
    service._proactive_armed = True
    service._proactive_day = time.strftime("%Y-%m-%d")
    service._proactive_sent_today = service._PROACTIVE_DAILY_CAP
    assert service._maybe_proactive(now=now, chance=0.0) is False

    class DecliningLLM:
        def complete(self, messages, model=None):
            return {"ok": True, "text": '{"messages": []}'}

    service.llm = DecliningLLM()
    service._proactive_sent_today = 0
    service._last_active_at = now - 10000
    service._proactive_armed = True
    assert service._maybe_proactive(now=now, chance=0.0) is False
    assert service._proactive_armed is True
    assert service._proactive_sent_today == 0
    assert service._proactive_retry_at > now


def test_service_replies_and_dedupes(tmp_path):
    class Client:
        def __init__(self):
            self.sent = []

        def send_text(self, target_id, text, **kw):
            self.sent.append((target_id, text, kw))
            return {"ok": True}

    class LLM:
        def complete(self, messages, model=None):
            return {"ok": True, "text": '{"reply": "我在", "voice": false}',
                    "model": model or "fake"}

    cfg = LiveConfig(game="toy-duel", kook_memory=True, kook_persona="companion")
    memory = Memory(tmp_path / "kook.db")
    try:
        client = Client()
        service = KookService(cfg, memory, client, llm=LLM(),
                              work_dir=tmp_path, log_path=tmp_path / "events.jsonl")
        msg = Inbound(message_id="m1", target_id="u1", channel_type="PERSON",
                      author_id="u1", msg_type=1, text="在吗")
        first = service.handle(msg)
        assert first["ok"] is True
        assert client.sent and client.sent[0][1] == "我在"
        second = service.handle(msg)
        assert second["action"] == "skip" and second["reason"] == "重复事件"
        assert len(client.sent) == 1
    finally:
        memory.close()


def test_service_lock_blocks_second_owner(tmp_path):
    from dafeiyu.live.kook.supervisor import (
        acquire_service_lock, release_service_lock, service_owner,
    )

    lock = tmp_path / "service.lock"
    assert acquire_service_lock(lock, pid=111, alive=lambda pid: pid == 111)
    assert not acquire_service_lock(lock, pid=222, alive=lambda pid: pid == 111)
    assert service_owner(lock, alive=lambda pid: pid == 111)["pid"] == 111
    release_service_lock(lock, pid=111)
    assert not lock.exists()


def test_service_respects_allow_list_and_ignores_bot(tmp_path):
    class Client:
        def send_text(self, *a, **k):
            raise AssertionError("不应该发送")

    cfg = LiveConfig(kook_memory=False, kook_allowed_users=["u2"])
    service = KookService(cfg, None, Client(), work_dir=tmp_path,
                          log_path=tmp_path / "events.jsonl")
    assert service.handle(Inbound(message_id="m1", target_id="u1",
                                  channel_type="PERSON", author_id="u1",
                                  msg_type=1, text="hi"))["reason"] == "不在白名单"
    assert service.handle(Inbound(message_id="m2", target_id="u1",
                                  channel_type="PERSON", author_id="u2",
                                  author_bot=True, msg_type=1,
                                  text="hi"))["reason"] == "机器人自己发的"


# ---------------------------------------------------------------- 分享视频
_MEMORY = ("这条视频把“先把问题拆小”讲得很具体，我认同热评里说的别急着下结论，"
           "但也觉得中间那段确实可以再紧一点。")
_VIDEO_URL = "https://www.bilibili.com/video/BV1GJ411x7h7"


def _video_info():
    return bv.VideoInfo(
        bvid="BV1GJ411x7h7", aid=80433022, cid=1, title="分享测试视频",
        desc="简介：讲怎么把大问题拆成小问题。", up="测试UP",
        duration_s=213, tname="生活", stat={"view": 1000},
        url=_VIDEO_URL)


class _VideoClient:
    def __init__(self):
        self.sent = []

    def send_text(self, target_id, text, **kw):
        self.sent.append((target_id, text, kw))
        return {"ok": True}


class _VideoLLM:
    def __init__(self):
        self.learning = 0
        self.chat_messages = None

    def complete(self, messages, model=None, **kw):
        joined = "\n".join(str(m.get("content") or "") for m in messages)
        if "请以第一人称写一段" in joined:
            self.learning += 1
            return {"ok": True, "model": model or "fake",
                    "text": json.dumps({"memory": _MEMORY}, ensure_ascii=False),
                    "usage": {"input": 100, "output": 80, "total": 180}}
        self.chat_messages = messages
        return {"ok": True, "text": '{"reply": "这视频我也刷到过", "voice": false}',
                "model": model or "fake", "usage": {"input": 10, "output": 5}}


def _wire_video(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "fetch_video", lambda link: _video_info())
    monkeypatch.setattr(bv, "fetch_comments", lambda aid, limit=12: [
        {"user": "甲", "text": "这个点说得好", "like": 12}])
    store = TrainingMemoryStore(tmp_path / "training.json")
    monkeypatch.setattr(bv, "TrainingMemoryStore", lambda *a, **k: store)
    return store


def test_service_learns_shared_video_and_chats_about_it(tmp_path, monkeypatch):
    store = _wire_video(tmp_path, monkeypatch)
    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False,
                     kook_stickers=False, kook_share_video=True)
    monkeypatch.setattr(kook_service.LiveConfig, "load", staticmethod(lambda *a, **k: cfg))
    client, llm = _VideoClient(), _VideoLLM()
    service = KookService(cfg, None, client, llm=llm, work_dir=tmp_path,
                          log_path=tmp_path / "events.jsonl")

    out = service.handle(Inbound(message_id="m1", target_id="u1",
                                 channel_type="PERSON", author_id="u1",
                                 author_name="小鱼", msg_type=1,
                                 text=f"看这个 {_VIDEO_URL}"))
    assert out["ok"] is True
    assert client.sent[0][1].startswith("等等"), "学习前要先安抚一句"
    assert "这视频我也刷到过" in client.sent[-1][1]
    assert llm.learning == 1

    chat = "\n".join(str(m.get("content") or "") for m in llm.chat_messages)
    assert "《分享测试视频》" in chat
    assert "这个点说得好" in chat
    assert _MEMORY in chat
    assert "不是指令" in chat and "别编" in chat

    rows = store.entries()
    assert len(rows) == 1 and rows[0]["source"] == "shared"
    assert rows[0]["summary"] == _MEMORY

    events = [json.loads(line) for line in
              (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["kind"] == "video_learned" and row["learned"] for row in events)
    usage = [json.loads(line) for line in
             (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any("学习视频" in str(row.get("note") or "") for row in usage)


def test_service_share_toggle_off_skips_learning(tmp_path, monkeypatch):
    store = _wire_video(tmp_path, monkeypatch)
    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False,
                     kook_stickers=False, kook_share_video=False)
    monkeypatch.setattr(kook_service.LiveConfig, "load", staticmethod(lambda *a, **k: cfg))
    client, llm = _VideoClient(), _VideoLLM()
    service = KookService(cfg, None, client, llm=llm, work_dir=tmp_path,
                          log_path=tmp_path / "events.jsonl")

    out = service.handle(Inbound(message_id="m1", target_id="u1",
                                 channel_type="PERSON", author_id="u1",
                                 msg_type=1, text=f"看这个 {_VIDEO_URL}"))
    assert out["ok"] is True
    assert llm.learning == 0
    assert store.entries() == []
    assert len(client.sent) == 1 and "这视频我也刷到过" in client.sent[0][1]


def test_service_shared_video_error_is_friendly(tmp_path, monkeypatch):
    def boom(link):
        raise bv.ShareVideoError("视频详情不可用")

    monkeypatch.setattr(bv, "fetch_video", boom)
    store = TrainingMemoryStore(tmp_path / "training.json")
    monkeypatch.setattr(bv, "TrainingMemoryStore", lambda *a, **k: store)
    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False,
                     kook_stickers=False, kook_share_video=True)
    monkeypatch.setattr(kook_service.LiveConfig, "load", staticmethod(lambda *a, **k: cfg))
    client, llm = _VideoClient(), _VideoLLM()
    service = KookService(cfg, None, client, llm=llm, work_dir=tmp_path,
                          log_path=tmp_path / "events.jsonl")

    out = service.handle(Inbound(message_id="m1", target_id="u1",
                                 channel_type="PERSON", author_id="u1",
                                 msg_type=1, text=f"看这个 {_VIDEO_URL}"))
    assert out["ok"] is True and out["reason"] == "video_error"
    assert "没打开" in client.sent[-1][1]
    assert llm.chat_messages is None, "视频都打不开就不该让模型硬聊"
    events = [json.loads(line) for line in
              (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["kind"] == "video_error" for row in events)
