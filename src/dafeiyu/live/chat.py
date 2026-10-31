"""私聊用的 LLM 封装：看图、决定「发文字还是发语音」、抽长期记忆。

仍然是 OpenAI 兼容的 ``/chat/completions``，沿用 ``config.json`` 里的
``llm_base_url / llm_api_key``；只有看图时换 ``llm_vision_model``
（DeepSeek 目前只有 deepseek-flash 收图片，deepseek-chat 是纯文本）。

回复协议：模型只输出一个 JSON 对象——

    {"reply": "...", "voice": true, "voice_text": "..."}

解析失败就当纯文本发，绝不因为格式问题把回复吞掉。
"""

from __future__ import annotations

import json
import re
import time

import requests

from ..core.config import LiveConfig
from ..persona.card import PersonaCard

_RETRY_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]")
_URL = re.compile(r"https?://\S+|www\.\S+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_CODE = re.compile(r"```.*?(?:```|$)", re.S)
_MD_INLINE = re.compile(r"`([^`]*)`")
_MD_MARKS = re.compile(r"[*_~>#]+")


def _usage(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    total = int(raw.get("total_tokens") or 0) or prompt + completion
    return {"input": prompt, "output": completion, "total": total}


class ChatLLM:
    """一次只发一条请求；重试只做一次，避免私聊里卡太久。"""

    def __init__(self, cfg: LiveConfig, timeout: float = 90.0) -> None:
        self.cfg = cfg
        self.timeout = timeout

    # ---------------------------------------------------------------- 请求
    def available(self) -> tuple[bool, str]:
        if not (self.cfg.llm_api_key or "").strip():
            return False, "未配置 LLM API Key（config.json 或控制台「基础配置」）"
        return True, ""

    def _url(self) -> str:
        base = (self.cfg.llm_base_url or "https://api.deepseek.com/v1").rstrip("/")
        return base if base.endswith("/chat/completions") else base + "/chat/completions"

    def complete(self, messages: list[dict], model: str | None = None) -> dict:
        ok, why = self.available()
        if not ok:
            return {"ok": False, "error": why}
        use_model = str(model or self.cfg.llm_model or "deepseek-chat")
        payload: dict = {
            "model": use_model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1600,
        }
        if "deepseek" in (self.cfg.llm_base_url or "").lower():
            # 私聊要的是 1–2 秒内开口，不要思维链拖时间。
            payload["thinking"] = {"type": "disabled"}
        started = time.time()
        last = ""
        for attempt in range(2):
            try:
                resp = requests.post(
                    self._url(),
                    headers={"Authorization": f"Bearer {self.cfg.llm_api_key.strip()}",
                             "Content-Type": "application/json"},
                    json=payload, timeout=(10, self.timeout))
                if resp.status_code in _RETRY_HTTP and attempt == 0:
                    last = f"HTTP {resp.status_code}"
                    time.sleep(1.2)
                    continue
                if resp.status_code >= 400:
                    return {"ok": False, "error": f"HTTP {resp.status_code}: "
                                                  f"{resp.text[:300]}", "model": use_model}
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                if attempt == 0:
                    time.sleep(1.2)
                    continue
                return {"ok": False, "error": last, "model": use_model}
            choices = data.get("choices") or []
            text = ""
            if choices and isinstance(choices[0], dict):
                text = str((choices[0].get("message") or {}).get("content") or "")
            if not text.strip():
                return {"ok": False, "error": "模型返回空内容", "model": use_model,
                        "usage": _usage(data.get("usage"))}
            return {"ok": True, "text": text, "model": use_model,
                    "usage": _usage(data.get("usage")),
                    "ms": int((time.time() - started) * 1000)}
        return {"ok": False, "error": last or "请求失败", "model": use_model}


# ---------------------------------------------------------------- 图片
def image_message(caption: str, image_bytes: bytes, mime: str = "image/jpeg") -> dict:
    import base64

    b64 = base64.b64encode(image_bytes).decode("ascii")
    content = [{"type": "text", "text": caption or "看看这张图，说说你看到了什么。"},
               {"type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "original"}}]
    return {"role": "user", "content": content}


def has_image(messages: list[dict]) -> bool:
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list) and any(
                isinstance(part, dict) and part.get("type") == "image_url" for part in content):
            return True
    return False


def pick_model(cfg: LiveConfig, messages: list[dict]) -> str:
    if has_image(messages):
        return str(cfg.llm_vision_model or cfg.llm_model or "deepseek-flash")
    return str(cfg.llm_model or "deepseek-chat")


# ---------------------------------------------------------------- 回复协议
def _json_object(raw: str) -> dict | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    for candidate in (text,):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start:end + 1])
            return value if isinstance(value, dict) else None
        except ValueError:
            return None
    return None


def _part_text(value) -> str:
    """从 messages 数组的一项里取文字；兼容模型写成 {"text": "..."}。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "content", "reply", "message"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def parse_reply(raw: str, allow_voice: bool = True) -> dict:
    """返回 ``{"text", "texts", "voice", "voice_text", "sticker"?}``。

    ``messages`` 是 1–3 条短消息的数组，服务会逐条发出去；旧的 ``reply``
    单字符串仍然兼容。格式不对时按纯文本处理。
    """
    body = (raw or "").strip()
    obj = _json_object(body)
    texts: list[str] = []
    sticker = ""
    if obj is not None:
        raw_parts = obj.get("messages") or obj.get("replies") or obj.get("parts")
        if isinstance(raw_parts, list):
            for item in raw_parts:
                part = _part_text(item)
                if part and part not in texts:
                    texts.append(part)
                if len(texts) >= 3:
                    break
        if not texts:
            for key in ("reply", "text", "content", "message", "answer"):
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    texts = [value.strip()]
                    break
        if not texts:
            # 模型偶尔换个字段名，挑最长的字符串也比把 JSON 甩给对方强。
            strings = [v for v in obj.values() if isinstance(v, str) and v.strip()]
            if strings:
                texts = [max(strings, key=len).strip()]
        sticker = str(obj.get("sticker") or "").strip()
    elif body:
        texts = [body]
    text = "\n".join(texts)
    if text:
        voice = bool(obj.get("voice")) and allow_voice if obj else False
        voice_text = str((obj or {}).get("voice_text") or "").strip()
        if voice and not voice_text:
            voice_text = speech_text(" ".join(texts))
        out = {"text": text, "texts": texts, "voice": voice,
               "voice_text": voice_text if voice else ""}
        if sticker:
            out["sticker"] = sticker
        return out
    if obj is not None:
        # 是 JSON 但一个可读字段都没有：当空回复，上层会记一条错误日志。
        out = {"text": "", "texts": [], "voice": False, "voice_text": ""}
        if sticker:
            out["sticker"] = sticker
        return out
    return {"text": body, "texts": [body] if body else [],
            "voice": False, "voice_text": ""}


def speech_text(text: str, limit: int = 200) -> str:
    """把回复洗成能朗读的文本：去代码、链接、Markdown、emoji。"""
    s = str(text or "")
    s = _MD_CODE.sub(" ", s)
    s = _MD_LINK.sub(r"\1", s)
    s = _URL.sub(" ", s)
    s = _MD_INLINE.sub(r"\1", s)
    s = _MD_MARKS.sub(" ", s)
    s = _EMOJI.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    for mark in ("。", "！", "？", "…", "；", "."):
        pos = cut.rfind(mark)
        if pos >= limit // 2:
            return cut[:pos + 1]
    return cut


def split_message(text: str, limit: int = 1800) -> list[str]:
    body = str(text or "").strip()
    if len(body) <= limit:
        return [body] if body else []
    chunks, buf = [], ""
    for line in body.splitlines(keepends=True):
        if len(buf) + len(line) > limit and buf:
            chunks.append(buf.rstrip())
            buf = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        buf += line
    if buf.strip():
        chunks.append(buf.rstrip())
    return [c for c in chunks if c]


# ---------------------------------------------------------------- 提示词
def build_system_prompt(card: PersonaCard, facts: list[dict],
                        allow_voice: bool = True, *, persona_text: str = "",
                        summaries: list[dict] | None = None,
                        sticker_options: list[dict] | None = None) -> str:
    lines = [
        f"你是「{card.name}」，现在在 KOOK 里和对方聊天（可能是频道，也可能是一对一私聊）——不是在直播。",
        "说话像熟人：短句、口语、先说人话；不喊口号、不背台词、不制造节目效果。",
        "不确定就说不确定；没看过的东西不要编。",
    ]
    if persona_text.strip():
        lines.append("你的个人性格（常驻，先做自己再回话）：\n" + persona_text.strip()[:3000])
    if card.traits:
        lines.append("你的性格：" + "；".join(card.traits))
    if summaries:
        lines.append("更早的对话摘要（压缩过，仍然算你记得的；自然地用，不要逐条复述）：")
        lines.extend(f"- {str(row.get('text') or '')}" for row in summaries[:4])
    if facts:
        lines.append("你记得的、关于对方的事（自然地用，不要逐条复述，也别提“记忆库”）：")
        lines.extend(f"- {str(f.get('text') or '')}" for f in facts[:8])
    lines.append("")
    lines.append(
        "一次回复不一定只有一句：像真人连发一样，可以是 1–3 条短消息。"
        "大多数时候 1 条；想补充、追问、表达情绪或分两拍说时，拆成 2–3 条，"
        "不要为了凑数硬拆。")
    if allow_voice:
        lines.append(
            "只输出一个 JSON 对象，不要代码块、不要额外解释：\n"
            '{"messages": ["第一条", "第二条"], "voice": true, "voice_text": "朗读版", "sticker": ""}\n'
            "messages 里每条都会单独发成一条消息；只有一条时也写成数组。\n"
            "voice 决定这条回复是否额外配语音卡：日常寒暄、情绪、调侃、晚安、"
            "一两句话能说完的，用 true；代码、链接、清单、表格、长解释、需要对方"
            "复制的内容，用 false。\n"
            "voice_text 是给 TTS 的稿子：去掉 Markdown、链接、代码、emoji，"
            "把 messages 合成一口气能读完的版本（60 字以内）；voice 为 false 时留空。"
        )
    else:
        lines.append(
            "只输出一个 JSON 对象，不要代码块：\n"
            '{"messages": ["第一条", "第二条"], "voice": false, "voice_text": "", "sticker": ""}\n'
            "messages 里每条都会单独发成一条消息；只有一条时也写成数组。"
        )
    if allow_voice or sticker_options:
        lines.append(
            "如果对方明确要语音/表情包，不要只用文字敷衍：语音把 voice 设为 true，"
            "表情包在 sticker 里填一个可用 id。")
    if sticker_options:
        lines.append(
            "sticker 是可选的大肥鱼表情包：可以主动配一张来表达情绪，不用等对方要；"
            "但不要每条都发，同一张别连续用；用了表情包 messages 里仍要有一句短话。\n"
            "可选表情包（id：文案）："
        )
        lines.extend(f"- {row.get('id')}：{row.get('text')}"
                     + (f"（{row.get('when')}）" if row.get("when") else "")
                     for row in sticker_options[:12])
    lines.append("不要输出内心戏、不要解释 JSON、不要加“回答：”之类的前缀。")
    return "\n".join(lines)


def build_user_prompt(text: str, has_image_input: bool) -> str:
    body = str(text or "").strip()
    if has_image_input:
        return body or "（对方发来一张图，没有配文字）"
    return body


def build_proactive_prompt(idle_minutes: int = 60) -> str:
    """没有新消息时，让模型主动开口；只发一条，像突然想起一件事。"""
    minutes = max(1, int(idle_minutes or 0))
    return (
        f"（对方大概 {minutes} 分钟没说话了。你可以主动发一条消息："
        "像突然想起一件事、分享一个小发现，或者轻轻关心一下；"
        "只发 1 条，不要问“在吗”，不要连续追问，也不要解释你为什么主动。）"
    )


def extract_facts_prompt(dialogue: str) -> list[dict]:
    return [
        {"role": "system",
         "content": "你负责整理长期记忆。只输出 JSON 数组，不要解释。"},
        {"role": "user",
         "content": (
             "下面是一段私聊。抽取值得长期记住的、关于对方的事实：身份、偏好、"
             "正在做的事、约定、重要的人和物。最多 3 条，每条一句中文短句，"
             "不要推测，不要复述寒暄。没有就返回 []。\n\n"
             f"{dialogue}\n\n"
             '只输出 JSON 数组，例如 ["对方养了一只叫豆豆的猫"]'
         )},
    ]


def parse_facts(raw: str, limit: int = 3) -> list[str]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        body = str(item or "").strip()
        if 2 <= len(body) <= 120 and body not in out:
            out.append(body)
        if len(out) >= limit:
            break
    return out


def summarize_prompt(turns: list[dict]) -> list[dict]:
    """把一批旧对话压成长期摘要；保留事实/偏好/约定/情绪，不保留寒暄。"""
    lines = []
    for turn in turns:
        who = "用户" if str(turn.get("role")) == "user" else "你"
        text = " ".join(str(turn.get("text") or "").split())
        if text:
            lines.append(f"{who}：{text}")
    return [
        {"role": "system",
         "content": "你负责压缩聊天记忆。只输出摘要正文，不要解释、不要 Markdown。"},
        {"role": "user",
         "content": (
             "把下面这段私聊压缩成一段长期记忆，供以后继续聊天时参考。保留："
             "对方的事实/身份/偏好/正在做的事、双方的约定、情绪变化、重要的人和物；"
             "删掉寒暄和重复。用第三人称写（「对方……」），不超过 400 字；"
             "不要编造，不要提「摘要」「上下文」。\n\n"
             + "\n".join(lines)
         )},
    ]


def parse_summary(raw: str, limit: int = 800) -> str:
    text = " ".join(str(raw or "").split()).strip()
    if not text:
        return ""
    for prefix in ("摘要：", "摘要:", "总结：", "总结:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    if len(text) < 4 or len(text) > limit:
        return ""
    return text


__all__ = [
    "ChatLLM", "build_proactive_prompt", "build_system_prompt", "build_user_prompt",
    "extract_facts_prompt", "has_image", "image_message", "parse_facts",
    "parse_reply", "parse_summary", "pick_model", "speech_text",
    "split_message", "summarize_prompt",
]
