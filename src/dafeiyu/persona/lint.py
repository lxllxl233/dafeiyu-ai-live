"""台词 lint：从 `tools/check_caster.py --say` 抽出来的规则，直播姬里每一步都过一遍。

分两级：
- FAIL：不该发出去（空台词、自称前缀、标签开头、复读、招牌一次两句、超长）
- WARN：能发但建议改（没数字、语气词多、破折号、毒舌词）

`sentences()` / `clip()` 是排版用的工具，和 lint 分开。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .card import Style

_SPEAKER_RE = re.compile(r"(?:大肥鱼|DeepSeek|AI|鱼哥|主播)\s*[:：]\s*", re.I)
_SPACE_RE = re.compile(r"[ \t\u3000]+")
_SENT_RE = re.compile(r"[。！？!?；;…]+")
_FILLER_RE = re.compile(r"[啊吧嘛哈诶]")
_STACK_RE = re.compile(r"[？?]{2,}|[。]{2,}")


@dataclass(frozen=True, slots=True)
class LintIssue:
    level: str  # FAIL | WARN
    message: str
    rule: str = ""

    def to_dict(self) -> dict:
        return {"level": self.level, "message": self.message, "rule": self.rule}


def strip_speaker(text: str) -> str:
    """去掉「大肥鱼：」「DeepSeek:」这类自称前缀（老工程在服务端也剥一次）。"""
    return _SPEAKER_RE.sub("", text or "").strip()


def normalize(text: str) -> str:
    """只做格式归一，不改内容：去自称前缀、去多余空格、去首尾引号。"""
    t = strip_speaker(text)
    t = _SPACE_RE.sub(" ", t).strip()
    return t.strip("“”\"'「」")


def clip(text: str, limit: int) -> str:
    """超长按字符截断，尽量落在标点后；补省略号。"""
    t = (text or "").strip()
    if limit <= 0 or len(t) <= limit:
        return t
    head = t[: max(1, limit - 1)]
    cut = max(head.rfind(ch) for ch in "，。！？；、,.;!?")
    if cut >= limit // 2:
        head = head[: cut + 1]
    return head.rstrip("，,、；;：: ") + "…"


def sentences(text: str, max_lines: int = 3) -> list[str]:
    """把一句话拆成气泡里的几行；最多 max_lines 行。"""
    t = (text or "").strip()
    if not t:
        return []
    parts = [p.strip() for p in _SENT_RE.split(t) if p.strip()]
    if not parts:
        return [t]
    if len(parts) > max_lines:
        head = parts[: max_lines - 1]
        head.append("，".join(parts[max_lines - 1:]))
        parts = head
    out = []
    for part in parts:
        out.append(part if part[-1] in "。！？!?…”）)" else part + "。")
    return out


def _ensure_stop(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    return s if s[-1] in "。！？!?…”）)】」" else s + "。"


def compose(thought: str, meme: str, style: Style | None = None) -> str:
    """和 overlay 页面一致的拼句：思路收句号，吐槽接在后面，不留双句号。"""
    style = style or Style()
    t = normalize(thought).rstrip("。 ")
    m = normalize(meme).strip().lstrip("，,、。；;—–- ")
    if not t:
        return clip(_ensure_stop(m), style.max_meme)
    if not m:
        return clip(_ensure_stop(t), style.max_thought)
    text = _ensure_stop(t) + _ensure_stop(m)
    return clip(text, style.max_total)


def lint(
    text: str,
    style: Style | None = None,
    quips: tuple[str, ...] = (),
    *,
    banned_prefixes: tuple[str, ...] = (),
    banned_labels: tuple[str, ...] = (),
    repeat_words: tuple[str, ...] = (),
) -> list[LintIssue]:
    style = style or Style()
    issues: list[LintIssue] = []
    t = (text or "").strip()
    if not t:
        return [LintIssue("FAIL", "空台词", "empty")]

    n = len(t)
    if n > style.soft_len:
        issues.append(LintIssue("FAIL", f"{n} 字 > {style.soft_len}：砍到一个信息点", "length"))
    elif n > style.max_len:
        issues.append(LintIssue("WARN", f"{n} 字 > {style.max_len}：观众读不完，建议再砍", "length"))

    for prefix in banned_prefixes or ("大肥鱼", "DeepSeek", "AI"):
        if t.startswith(prefix) or prefix in t[:6]:
            issues.append(LintIssue("FAIL", f"不要加自称/前缀「{prefix}」", "prefix"))

    for label in banned_labels or ("战斗：", "路线：", "分析："):
        if label in t:
            issues.append(LintIssue("FAIL", f"不要标签化「{label}」", "label"))

    if "——" in t:
        issues.append(LintIssue("WARN", "不要用破折号硬拼两句", "dash"))

    for word in repeat_words:
        count = t.count(word)
        if count >= 2:
            issues.append(LintIssue("FAIL", f"「{word}」一条里出现 {count} 次 = 复读", "repeat"))
        elif word == "节目效果拉满" and count == 1:
            issues.append(LintIssue("WARN", "自指 meta 一局最多一次", "repeat"))

    if _STACK_RE.search(t):
        issues.append(LintIssue("WARN", "标点堆叠（？？/。。。），删掉一个", "punct"))

    fillers = len(_FILLER_RE.findall(t))
    if fillers > style.max_fillers:
        issues.append(LintIssue("WARN", f"语气词 {fillers} 个：憨不是腻，删几个", "filler"))

    for rude in ("菜", "垃圾", "送人头"):
        if rude in t:
            issues.append(LintIssue("WARN", f"毒舌词「{rude}」：换憨式自嘲", "rude"))

    parts = [p for p in _SENT_RE.split(t) if p.strip()]
    if len(parts) > 4:
        issues.append(LintIssue("WARN", f"{len(parts)} 个短句：一句话最多三拍", "sentences"))

    hits = [q for q in quips if q and q in t]
    if len(hits) >= 2:
        issues.append(LintIssue("FAIL", f"一句里 {len(hits)} 句招牌：最多一句", "quip"))
    if hits and not re.search(r"\d", t):
        issues.append(LintIssue("WARN", "招牌没有数字垫着 = 空喊", "quip"))
    if "这下看懂了" in t and len(t) <= 12:
        issues.append(LintIssue("WARN", "「这下看懂了」后面要跟看懂的是什么", "quip"))

    if not re.search(r"\d", t):
        issues.append(LintIssue("WARN", "没有数字：加一个具体数（血/伤/回合）", "number"))

    return issues


def summarize(issues: list[LintIssue]) -> dict:
    fails = [i for i in issues if i.level == "FAIL"]
    return {
        "ok": not fails,
        "fails": len(fails),
        "warns": len(issues) - len(fails),
        "issues": [i.to_dict() for i in issues],
    }


__all__ = ["LintIssue", "lint", "compose", "sentences", "clip", "normalize", "strip_speaker", "summarize"]
