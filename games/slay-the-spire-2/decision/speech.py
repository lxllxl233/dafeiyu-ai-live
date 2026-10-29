"""台词清洗：不把血条、卡组等级念给观众听。

提示词已经明令禁止报 HP / 卡组等级，但模型偶尔还是会从 ctx 里把「HP 64/80（80%）」
「卡组还是 C 级」抄进 say。这里是最后一道过滤：只摘掉这类播报片段，保留人话；
整句都被摘空就丢弃，宁可这一句不说，也不念状态。价格/金币不摘——「删一张 100
金币」是有意义的信息。
"""

from __future__ import annotations

import re

_HP = re.compile(
    r"[（(]?\s*(?:HP|Hp|hp|血量|血条)\s*[:：]?\s*\d+\s*/\s*\d+\s*(?:[（(]\s*\d+\s*%\s*[）)])?"
    r"|[（(]?\s*(?:HP|Hp|hp)\s*[:：]?\s*\d+\s*[）)]?"
    r"|血量\s*\d+"
    r"|我\s*\d+\s*血(?!瓶)",
)
# 一整个小句都是卡组播报：「卡组还是 C 级」「卡组状态不错」「卡组强度 B」「卡组评分 48」。
_DECK = re.compile(
    r"(?:这|我的|现在)?卡组[^，,；;。！？!?…\n]{0,14}?"
    r"(?:[A-DＡ-Ｄa-d]\s*(?:级|档|分)|状态|强度|评分|分数|等级)"
    r"[^，,；;。！？!?…\n]{0,6}"
)
_CJK_PUNCT = "，,、；;：:。.！!？?… "
_END_PUNCT = "。！？!?…"
_CLAUSE = re.compile(r"(?<=[，,；;。！？!?…])")


def _strip_deck_status(text: str) -> str:
    """删掉「卡组还是 X 级」这类状态播报小句，保留其余内容。"""
    out: list[str] = []
    for chunk in _CLAUSE.split(text):
        core = chunk.strip(" \u3000")
        body = core.rstrip("，,；;：:。！？!?… ")
        if body and _DECK.fullmatch(body):
            tail = core[len(body):]
            end = next((ch for ch in tail if ch in _END_PUNCT), "")
            if end and out and out[-1].strip(" \u3000，,；;：:"):
                out[-1] = out[-1].rstrip("，,、；;：: ") + end
            continue
        out.append(chunk)
    return "".join(out)


def tidy_say(text: str) -> str:
    """删掉台词里的血条 / 卡组等级播报；保留原有句末标点。"""
    raw = str(text or "").strip()
    if not raw or not (_HP.search(raw) or _DECK.search(raw)):
        return raw
    cleaned = _strip_deck_status(_HP.sub("", raw))
    # 摘掉播报后留下的空句/悬空标点收拾一下，别让 TTS 读「，，」。
    parts = []
    for chunk in re.split(r"(?<=[。！？!?])", cleaned):
        seg = chunk.strip(" " + _CJK_PUNCT)
        if not seg:
            continue
        parts.append(chunk.strip().lstrip("，,、；;：: "))
    out = "".join(parts).strip()
    out = re.sub(r"[，,、；;：:]{2,}", "，", out)
    out = re.sub(r"\s{2,}", " ", out).strip(_CJK_PUNCT + " ")
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", out):
        return ""
    return out + raw[-1] if raw[-1] in _END_PUNCT and out[-1] not in _END_PUNCT else out


__all__ = ["tidy_say"]
