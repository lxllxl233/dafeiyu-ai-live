"""桌宠轻点：让当前人设接一句 LLM 回复。

这里只负责「说什么」：用个人性格层拼系统提示，调 ``ChatLLM``，再把模型
返回的 JSON/纯文本洗成一句能进气泡的话。网络请求放在调用方的后台线程里，
桌面主循环和渲染线程都不等它。

不写直播/游戏层：轻点是主人私下戳一下，不是上台，也不是打牌。
"""
from __future__ import annotations

import random
from typing import Any

from ..core.config import LiveConfig
from ..persona.layers import PersonaStack
from ..persona.lint import clip
from .chat import ChatLLM, parse_reply, speech_text

# 同一句「戳了戳你」反复出现会腻；用几种不同的小动作当用户输入。
CLICK_PROMPTS = (
    "（主人用鼠标戳了戳你）",
    "（主人轻轻点了点你的脑袋）",
    "（主人又戳了你一下）",
    "（主人用鼠标碰了碰你的鱼鳍）",
)
# 没配 Key / 模型超时 / 空回复时的保底；不要让点击完全没反应。
FALLBACK_LINES = (
    "唔…你戳我干嘛。",
    "在呢，怎么啦？",
    "别戳啦，痒。",
    "嗯？找我呀。",
)

_RULES = (
    "主人用鼠标戳了你一下，不是在打字聊天。用你的口吻回一句自然反应："
    "可以疑惑、撒娇、吐槽或问怎么了。只回 1–2 句，尽量 30 字以内；"
    "不要解释，不要提 AI/模型/提示词，不要输出 JSON、Markdown 或括号动作。"
    "只输出这一句话。"
)


def build_click_messages(cfg: LiveConfig, prompt: str = "") -> list[dict]:
    """拼一次点击反应的消息；性格层读不到也不能让点击崩掉。"""
    try:
        stack = PersonaStack.load(
            cfg.persona or "dafeiyu", live=False, training=False)
        name = stack.card.name
        persona = stack.prompt()
    except Exception:  # noqa: BLE001 - 人设文件坏了也要能回一句
        name, persona = "大肥鱼", ""
    system = f"你是「{name}」，主人桌面上的桌宠。"
    if persona:
        system += "\n\n" + persona
    system += "\n\n" + _RULES
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt or random.choice(CLICK_PROMPTS)},
    ]


def _clean(raw: str) -> str:
    """兼容 JSON 协议和模型直接说人话；多句用空格合成一个气泡。"""
    parsed = parse_reply(raw, allow_voice=False)
    texts = [str(t).strip() for t in parsed.get("texts") or [] if str(t).strip()]
    text = " ".join(texts) or str(parsed.get("text") or "").strip()
    text = speech_text(text, limit=90).strip().strip('"“”\'')
    return clip(text, 60)


def click_reply(cfg: LiveConfig | None = None, *, llm: Any | None = None,
                prompt: str = "") -> dict:
    """同步跑一次点击回复；调用方负责把它放进后台线程。

    返回 ``{"ok": bool, "text": str, "error": str, "model": str}``。
    """
    cfg = cfg or LiveConfig.load()
    # 点击反馈不该等 90 秒；20 秒还没回就退回本地保底句。
    llm = llm or ChatLLM(cfg, timeout=20.0)
    messages = build_click_messages(cfg, prompt)
    try:
        raw = llm.complete(messages, max_tokens=120,
                           trace_note="桌宠点击",
                           trace_meta={"source": "pet_click"})
    except Exception as exc:  # noqa: BLE001 - 网络/Key/模型任何问题都只算失败
        return {"ok": False, "text": "", "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(raw, dict) or not raw.get("ok"):
        error = (raw or {}).get("error") if isinstance(raw, dict) else "模型返回无效"
        return {"ok": False, "text": "", "error": str(error or "模型请求失败")}
    text = _clean(str(raw.get("text") or ""))
    if not text:
        return {"ok": False, "text": "", "error": "模型返回空回复"}
    return {"ok": True, "text": text, "error": "",
            "model": str(raw.get("model") or ""), "messages": messages}


def fallback_line() -> str:
    """模型不可用时的本地反应；仍然让点击有反馈。"""
    return random.choice(FALLBACK_LINES)


__all__ = ["CLICK_PROMPTS", "FALLBACK_LINES", "build_click_messages",
           "click_reply", "fallback_line"]
