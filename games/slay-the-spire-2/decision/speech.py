"""台词清洗：不把血条念给观众听。

提示词已经明令禁止报 HP，但模型偶尔还是会从 ctx 里把「HP 64/80（80%）」抄进 say。
这里是最后一道过滤：只摘掉血条片段，保留人话；整句都被摘空就丢弃，全是血条就返回空串
（宁可这一句不说，也不念血条）。价格/金币不摘——「删一张 100 金币」是有意义的信息。
"""

from __future__ import annotations

import re

_HP = re.compile(
    r"[（(]?\s*(?:HP|Hp|hp|血量|血条)\s*[:：]?\s*\d+\s*/\s*\d+\s*(?:[（(]\s*\d+\s*%\s*[）)])?"
    r"|[（(]?\s*(?:HP|Hp|hp)\s*[:：]?\s*\d+\s*[）)]?"
    r"|血量\s*\d+"
    r"|我\s*\d+\s*血(?!瓶)",
)
_CJK_PUNCT = "，,、；;：:。.！!？?… "
_END_PUNCT = "。！？!?…"


def tidy_say(text: str) -> str:
    """删掉台词里的血条数字；保留原有句末标点。"""
    raw = str(text or "").strip()
    if not raw or not _HP.search(raw):
        return raw
    cleaned = _HP.sub("", raw)
    # 摘掉血条后留下的空句/悬空标点收拾一下，别让 TTS 读「，，」。
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
