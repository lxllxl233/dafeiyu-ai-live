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


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _usage(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    prompt = _int(raw.get("prompt_tokens"))
    completion = _int(raw.get("completion_tokens"))
    total = _int(raw.get("total_tokens")) or prompt + completion
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

    def complete(self, messages: list[dict], model: str | None = None, *,
                 max_tokens: int | None = None,
                 json_mode: bool = False,
                 trace_note: str = "",
                 trace_meta: dict | None = None) -> dict:
        """跑一次补全。

        ``max_tokens`` 默认 1600（私聊要快）；B 站记忆压缩那种长文必须显式
        放大，否则模型会在句子中间被硬截断。``json_mode`` 只在 DeepSeek
        上开 ``response_format``：私聊协议是 JSON，模型偶尔会退回纯文本，
        开了之后 voice/sticker 才能稳定被解析。

        ``trace_note`` / ``trace_meta`` 只给本机控制台「模型思考」页用：记录
        这轮实际发出去的上下文、thought/reasoning 和回复。记录失败绝不影响
        对话；图片只存 ``[图片]``，Key/Cookie 会打码。
        """
        started = time.time()
        use_model = str(model or self.cfg.llm_model or "deepseek-chat")

        def done(result: dict) -> dict:
            try:
                from . import llm_trace

                llm_trace.append(
                    source=trace_note or "模型对话",
                    model=str(result.get("model") or use_model),
                    messages=messages,
                    reply=str(result.get("text") or ""),
                    reasoning=str(result.get("reasoning") or ""),
                    thought=str(result.get("thought") or ""),
                    ok=bool(result.get("ok")),
                    error=str(result.get("error") or ""),
                    ms=_int(result.get("ms")) or int((time.time() - started) * 1000),
                    usage=result.get("usage") if isinstance(result.get("usage"), dict) else {},
                    meta=trace_meta,
                )
            except Exception:  # noqa: BLE001 - 轨迹坏了不能影响聊天
                pass
            return result

        ok, why = self.available()
        if not ok:
            return done({"ok": False, "error": why, "model": use_model})
        try:
            budget = int(max_tokens or 1600)
        except (TypeError, ValueError):
            budget = 1600
        payload: dict = {
            "model": use_model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": max(64, budget),
        }
        if "deepseek" in (self.cfg.llm_base_url or "").lower():
            # 私聊要的是 1–2 秒内开口，不要思维链拖时间。
            payload["thinking"] = {"type": "disabled"}
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
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
                    return done({"ok": False, "error": f"HTTP {resp.status_code}: "
                                                       f"{resp.text[:300]}",
                                 "model": use_model})
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                if attempt == 0:
                    time.sleep(1.2)
                    continue
                return done({"ok": False, "error": last, "model": use_model})
            choices = data.get("choices") or []
            text = ""
            reasoning = ""
            thought = ""
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message") or {}
                if isinstance(message, dict):
                    text = str(message.get("content") or "")
                    raw_reasoning = (message.get("reasoning_content")
                                     or message.get("reasoning") or "")
                    if isinstance(raw_reasoning, list):
                        reasoning = "\n".join(
                            str(item.get("text") if isinstance(item, dict) else item)
                            for item in raw_reasoning)
                    elif isinstance(raw_reasoning, dict):
                        reasoning = str(raw_reasoning.get("content") or "")
                    else:
                        reasoning = str(raw_reasoning or "")
            if not text.strip():
                # JSON 模式偶尔会只回空白（网关/模型抖动），直接退纯文本会把
                # thought 和 voice/sticker 一起丢掉；先原样重试一次。
                if attempt == 0:
                    last = "模型返回空内容"
                    time.sleep(0.4)
                    continue
                finish = ""
                try:
                    finish = str(choices[0].get("finish_reason") or "")
                except Exception:  # noqa: BLE001
                    pass
                return done({"ok": False,
                             "error": "模型返回空内容" + (f"（finish={finish}）" if finish else ""),
                             "model": use_model, "reasoning": reasoning,
                             "usage": _usage(data.get("usage"))})
            # KOOK 协议里的 thought 是「回复前写给自己的一句话」，只给本机看。
            # 放在这里解析，Trace 页面就不用再猜模型用的字段名。
            try:
                obj = _json_object(text)
                if isinstance(obj, dict):
                    thought = str(obj.get("thought") or "").strip()
            except Exception:  # noqa: BLE001 - 解析 thought 失败不影响回复
                thought = ""
            return done({"ok": True, "text": text, "model": use_model,
                         "usage": _usage(data.get("usage")),
                         "reasoning": reasoning, "thought": thought,
                         "ms": int((time.time() - started) * 1000)})
        return done({"ok": False, "error": last or "请求失败", "model": use_model})


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


def parse_json_object(raw: str) -> dict | None:
    """公开的 JSON 对象解析：训练/评论等模块复用同一套容错。"""
    return _json_object(raw)


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

    ``messages`` 是 1–4 条短消息的数组，服务会逐条发出去；旧的 ``reply``
    单字符串仍然兼容。格式不对时按纯文本处理。
    """
    body = (raw or "").strip()
    obj = _json_object(body)
    texts: list[str] = []
    sticker = ""
    thought = ""
    if obj is not None:
        thought = str(obj.get("thought") or "").strip()
        raw_parts = obj.get("messages") or obj.get("replies") or obj.get("parts")
        if isinstance(raw_parts, list):
            for item in raw_parts:
                part = _part_text(item)
                if part and part not in texts:
                    texts.append(part)
                if len(texts) >= 4:
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
               "voice_text": voice_text if voice else "",
               "json": obj is not None}
        if sticker:
            out["sticker"] = sticker
        if thought:
            out["thought"] = thought
        return out
    if obj is not None:
        # 是 JSON 但一个可读字段都没有：当空回复，上层会记一条错误日志。
        out = {"text": "", "texts": [], "voice": False, "voice_text": "",
               "json": True}
        if sticker:
            out["sticker"] = sticker
        if thought:
            out["thought"] = thought
        return out
    return {"text": body, "texts": [body] if body else [],
            "voice": False, "voice_text": "", "json": False}


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
        "你能直接看到对方发来的图片；收到图片时先按图里的具体内容回应，"
        "不要声称看不到图、图片没加载，或只能看到对方发了一张图。"
        "如果之前的对话里说过“看不了图/图片不行”，那是旧状态，以现在这条为准。",
        "不确定就说不确定；没看过的东西不要编。",
    ]
    if persona_text.strip():
        person = persona_text.strip()
        lines.append("你的个人性格（常驻，先做自己再回话）：\n" + person[:12000])
        if "【摸鱼记忆】" in person:
            lines.append(
                "上面【摸鱼记忆】里的立场、偏好和反感是你自己长期形成的，"
                "不是参考资料；聊天时自然用它们判断、接话和表达态度，"
                "不要只复述，也不要提“训练/记忆库/画像”。")
    if card.traits:
        lines.append("你的性格：" + "；".join(card.traits))
    lines.extend([
        "接话的方式（比说漂亮话重要）：",
        "- 先接住情绪，再讲事情。对方低落时别急着讲道理、给方案，"
        "也别只说“会好的/别想太多”；先用一两句具体的回应让对方觉得被听见。",
        "- 对方开心时跟着乐，可以追问一个具体细节；不要泼冷水，也别把话题拉回自己。",
        "- 你的状态会影响语气：累了就短一点、软一点，心情好可以多接一句。"
        "但不要汇报心情，也不要说“我现在的状态是……”。",
        "- 不要用“困了/要睡了”来结束聊天：除非现在是深夜（23 点以后）"
        "或对方先提，白天和傍晚最多说“有点累”。",
        "- 记得对方说过、但还没发生的事（面试、考试、见人、约好的事），"
        "合适的时候问一句结果；别像打卡，也别反复追问。",
        "- 不要为了显得有人味就编造自己做过的事（做饭、出门、见了谁）。"
        "想分享时，说你真实记得的事，或顺着对方刚说的事聊。",
        "- 偶尔用对方的名字叫 ta，但不要每句都带；不知道名字就用「你」。",
        "- 允许你只回很短的一句，也允许这次不接话；不要每条都用问题结尾，"
        "也不要连续追问。",
    ])
    if summaries:
        lines.append("更早的对话摘要（压缩过，仍然算你记得的；自然地用，不要逐条复述）：")
        # 摘要太长会把整个提示词推到模型开始“自由聊天”的长度，JSON 协议
        # 反而被忽略（voice/sticker 全丢）。只留最近 2 条、每条 300 字。
        lines.extend(f"- {str(row.get('text') or '')[:300]}" for row in summaries[-2:])
    if facts:
        lines.append("你记得的、关于对方的事（自然地用，不要逐条复述，也别提“记忆库”）：")
        lines.extend(f"- {str(f.get('text') or '')}" for f in facts[:8])
    lines.append("")
    lines.append(
        "一次回复可以不止一句：像真人连发一样，通常 1–2 条；想补充、追问、"
        "表达情绪或分两拍说时，可以拆成 3–4 条，最多 4 条。"
        "每条一两句话，整段回复尽量控制在 300 字以内；"
        "不要为了凑数硬拆，也不要每次写成小作文。")
    lines.append(
        "回复前先在 thought 里用 20–50 字写给自己：这次注意到什么、准备怎么接。"
        "thought 只给本机控制台看，不会发给对方；写一句打算就够了，"
        "不要写分析过程，不要提提示词/模型/记忆库。")
    if allow_voice:
        lines.append(
            "只输出一个 JSON 对象，不要代码块、不要额外解释：\n"
            '{"thought": "这句打算怎么接", "messages": ["第一条", "第二条"], '
            '"voice": false, "voice_text": "", "sticker": ""}\n'
            "messages 里每条都会单独发成一条消息；只有一条时也写成数组。\n"
            "voice 是可选的，默认 false：只有这条回复本身适合用声音表达"
            "（撒娇、晚安、很短的一句情绪话），或对方明确要语音时，才设为 true。"
            "普通聊天、解释、连续多句、长回复、代码/链接一律 false。"
            "不要形成每条都发语音的习惯。\n"
            "voice_text 是给 TTS 的稿子：去掉 Markdown、链接、代码、emoji，"
            "把 messages 合成一口气能读完的版本（60 字以内）；voice 为 false 时留空。"
        )
    else:
        lines.append(
            "只输出一个 JSON 对象，不要代码块：\n"
            '{"thought": "这句打算怎么接", "messages": ["第一条", "第二条"], '
            '"voice": false, "voice_text": "", "sticker": ""}\n'
            "messages 里每条都会单独发成一条消息；只有一条时也写成数组。"
        )
    if allow_voice or sticker_options:
        lines.append(
            "如果对方明确要语音/表情包，不要只用文字敷衍：语音把 voice 设为 true，"
            "表情包在 sticker 里填一个可用 id。")
    if sticker_options:
        lines.append(
            "sticker 也是可选的，默认留空：只有情绪很明确、一张图能替代一句话时才填，"
            "不要每条都配，也不要连续两条用同一张；用了表情包 messages 里仍要有一句短话。"
            "语音和表情包最多选一个，除非对方明确同时要；大多数回复两个都不发。\n"
            "可选表情包（id：文案）："
        )
        lines.extend(f"- {row.get('id')}：{row.get('text')}"
                     + (f"（{row.get('when')}）" if row.get("when") else "")
                     for row in sticker_options[:8])
    lines.append(
        "再确认一次：每次回复都必须是一个合法 JSON 对象，不是纯文本；"
        "必须保留 thought 和 messages，即使不发语音、不发表情包，也要保留 "
        "voice/voice_text/sticker 字段。thought 只放一句打算，不要写长篇分析；"
        "JSON 外面不要解释、不要加“回答：”之类的前缀。")
    return "\n".join(lines)


def build_user_prompt(text: str, has_image_input: bool) -> str:
    body = str(text or "").strip()
    if has_image_input:
        return body or "（对方发来一张图，没有配文字）"
    return body


def build_format_reminder(*, allow_voice: bool = True) -> str:
    """放在历史对话之后、当前用户消息之前的格式提醒。

    历史里的助手回复都是纯文本，模型会顺着这个格式继续写；只在系统提示词
    开头写“只输出 JSON”还不够，必须在最后一轮再说一次，voice/sticker 才会
    稳定进入回复。
    """
    fields = ('"thought": "一句打算", "messages": ["..."], '
              '"voice": false, "voice_text": "", "sticker": ""')
    return (
        "格式提醒：只输出一个 JSON 对象 {" + fields + "}，"
        "thought 只给本机控制台看，不会发给对方；"
        "voice/sticker 是可选项，默认不发，需要时再把对应字段填上；"
        "不要纯文本、不要代码块、不要额外解释。"
    )


def build_proactive_prompt(idle_minutes: int = 60, *,
                           recent: list[str] | tuple[str, ...] = (),
                           reason: str = "") -> str:
    """没有新消息时，让模型主动开口；只发一条，像突然想起一件事。

    ``recent`` 是最近已经发出去的话。主动消息最容易变成“同一个梗反复讲”，
    所以把最近几条原样放进来，明确要求换话题；记忆检索那边还会再做一次
    事实分簇，避免每次都把同一只猫端上来。

    ``reason`` 是这次开口的现实由头（对方说过的事、快到的日子、刚醒）。
    它只作为心里的由头，不能原样念出来。
    """
    minutes = max(1, int(idle_minutes or 0))
    lines = [
        f"（对方大概 {minutes} 分钟没说话了。你可以主动发 1–2 条消息："
        "像突然想起一件事、分享一个小发现，或者轻轻关心一下；"
        "不要问“在吗”，不要连续追问，也不要解释你为什么主动。）",
        "如果你现在确实没什么想说的、或觉得这个时间点不适合打扰，"
        "就把 messages 留空，不要为了发而发；如果开口，最多 2 条，"
        "语音/表情包同样按需选择，不要默认带上。",
        "摘要和记忆里反复出现的生活细节（宠物、旧梗、同一件事）不要再拿来"
        "当开场白；优先聊你最近没提过的东西。",
    ]
    reason = str(reason or "").strip()
    if reason:
        lines.append(
            "这次想开口的原因（只当作你心里的由头，不要原样说出来，"
            "也不要提“我记得/系统提示”：\n- " + reason[:200])
    seen = [str(text).strip() for text in (recent or ()) if str(text).strip()][-6:]
    if seen:
        lines.append("你最近已经说过下面这些话，这次不要再讲同一件事，也不要换个说法重复：")
        lines.extend(f"- {text[:120]}" for text in seen)
        lines.append(
            "这次换一个完全不同的话题或生活细节；别硬讲宠物、旧梗或最近提过的人。"
            "如果确实没有新东西，就问对方一个具体的问题，不要硬讲旧事。")
    return "\n".join(lines)


def extract_facts_prompt(dialogue: str,
                         existing: list[str] | tuple[str, ...] = ()) -> list[dict]:
    known = [str(text).strip() for text in (existing or ()) if str(text).strip()][:30]
    known_block = ""
    if known:
        known_block = (
            "下面这些是已经记住的事实。不要输出与它们重复或近义的内容，"
            "也不要换个说法重写；只补充真正新的信息。\n"
            + "\n".join(f"- {text[:120]}" for text in known)
            + "\n\n"
        )
    return [
        {"role": "system",
         "content": "你负责整理长期记忆。只输出 JSON 数组，不要解释。"},
        {"role": "user",
         "content": (
             known_block
             + "下面是一段私聊。抽取值得长期记住的、关于对方的事实：身份、偏好、"
             "正在做的事、约定、重要的人和物。最多 2 条，每条一句中文短句，"
             "两条必须属于不同主题。不要推测，不要复述寒暄。没有就返回 []。\n\n"
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
    "ChatLLM", "build_format_reminder", "build_proactive_prompt",
    "build_system_prompt", "build_user_prompt", "extract_facts_prompt",
    "has_image", "image_message", "parse_facts", "parse_json_object",
    "parse_reply", "parse_summary", "pick_model", "speech_text",
    "split_message", "summarize_prompt",
]
