#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check the sts2-caster skill pack + lint a banner line before sending.

Usage:
  python tools/check_caster.py                                  # 验证技能文件
  python tools/check_caster.py --say "它 21，我最多挡 15。这回合交 6 点税。"
  python tools/check_caster.py --thought "它 21，我最多挡 15" --meme "交 6 点税"

Exit code 0 = pass (warnings allowed), 1 = at least one FAIL.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(".").resolve()
SKILL_DIR = ROOT / ".dsh" / "skills" / "sts2-caster"
SKILL = SKILL_DIR / "SKILL.md"
LINES = SKILL_DIR / "references" / "lines.md"

MAX_BYTES = 9000
SECTIONS = ["三条铁律", "两个通道", "三拍", "五件武器", "招牌稿", "反效果", "节奏", "出口前三问", "台词 lint"]

BANNED_PREFIX = ["大肥鱼", "DeepSeek", "AI:", "AI：", "鱼哥："]
LABELS = ["战斗：", "路线：", "分析：", "结论：", "思路：", "决策："]
REPEAT_WORDS = ["典", "寄", "就这", "笑死", "绷不住", "节目效果拉满"]

# 招牌稿（与 db/sts2.db 的 memes.社区口头禅 对齐，见 tools/caster_quips.py）
QUIPS = [
    "进 Boss 先算 DPS", "Ctrl+Z", "这抓位还用想", "好牌多拿", "我已启动",
    "我就说嘛", "刚好够", "学到了", "半挡半打", "不用想，直接打",
    "问题不大", "先算账再摸牌", "等一下，我算错了", "我说这卡特别强有没有懂得",
    "时间回溯", "这下看懂了", "读档也是尖塔的一部分", "这也能联动", "易伤道歉",
]

MAX_LEN = 45
SOFT_LEN = 55


def lint(text: str) -> list[tuple[str, str]]:
    """Return [(level, message)] where level in {"FAIL","WARN"}."""
    out: list[tuple[str, str]] = []
    t = (text or "").strip()
    if not t:
        return [("FAIL", "空台词")]

    n = len(t)
    if n > SOFT_LEN:
        out.append(("FAIL", f"{n} 字 > {SOFT_LEN}：砍到一个信息点"))
    elif n > MAX_LEN:
        out.append(("WARN", f"{n} 字 > {MAX_LEN}：观众读不完，建议再砍"))

    for p in BANNED_PREFIX:
        if t.startswith(p) or f"{p}" in t[:6]:
            out.append(("FAIL", f"不要加自称/前缀「{p}」"))
    for lab in LABELS:
        if lab in t:
            out.append(("FAIL", f"不要标签化开头「{lab}」"))

    if "——" in t:
        out.append(("WARN", "不要用破折号硬拼两句"))

    hits = {w: t.count(w) for w in REPEAT_WORDS if w in t}
    for w, c in hits.items():
        if c >= 2:
            out.append(("FAIL", f"「{w}」一条里出现 {c} 次 = 复读"))
        elif w == "节目效果拉满":
            out.append(("WARN", "自指 meta 一局最多一次"))

    if re.search(r"[？?]{2,}", t) or re.search(r"[。]{2,}", t):
        out.append(("WARN", "标点堆叠（？？/。。。），删掉一个"))

    fillers = len(re.findall(r"[啊吧嘛哈诶]", t))
    if fillers > 3:
        out.append(("WARN", f"语气词 {fillers} 个：憨不是腻，删几个"))

    for rude in ("菜", "垃圾", "送人头"):
        if rude in t:
            out.append(("WARN", f"毒舌词「{rude}」：换憨式自嘲"))

    sents = [s for s in re.split(r"[。！？!?]", t) if s.strip()]
    if len(sents) > 3:
        out.append(("WARN", f"{len(sents)} 个短句：一句话最多两拍"))

    hits_q = [q for q in QUIPS if q in t]
    if len(hits_q) >= 2:
        out.append(("FAIL", f"一句里 {len(hits_q)} 句招牌：最多一句"))
    if hits_q and not re.search(r"\d", t):
        out.append(("WARN", "招牌没有数字垫着 = 空喊"))
    if "这下看懂了" in t and len(t) <= 12:
        out.append(("WARN", "「这下看懂了」后面要跟看懂的是什么"))

    if not re.search(r"\d", t):
        out.append(("WARN", "没有数字：加一个具体数（血/伤/回合）"))

    return out


def check_skill() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    if not SKILL.exists():
        print(f"FAIL  缺少 {SKILL}")
        return 1
    text = SKILL.read_text(encoding="utf-8")
    size = len(text.encode("utf-8"))
    if size > MAX_BYTES:
        errors.append(f"SKILL.md {size} 字节 > {MAX_BYTES}：常驻上下文膨胀")
    if not text.startswith("---\n"):
        errors.append("缺少 frontmatter（技能会从目录消失）")
    else:
        fm = text.split("---", 2)[1]
        if "name:" not in fm or "description:" not in fm:
            errors.append("frontmatter 缺 name/description")
        elif "sts2-caster" not in fm:
            errors.append("frontmatter name 与目录名不一致")
        if "lines.md" not in fm:
            warnings.append("description 没提 references/lines.md")
    for sec in SECTIONS:
        if sec not in text:
            errors.append(f"缺少小节：{sec}")
    if not LINES.exists():
        errors.append("缺少 references/lines.md")
    else:
        lt = LINES.read_text(encoding="utf-8")
        count = len([l for l in lt.splitlines() if l.startswith("- ")])
        if count < 50:
            errors.append(f"lines.md 只有 {count} 条台词，预期 ≥50")
        for node in ("开局", "战斗开场", "掉大血", "斩杀", "认错", "Boss", "死亡", "连载梗", "憨式三招"):
            if node not in lt:
                errors.append(f"lines.md 缺节点：{node}")

    quips = SKILL_DIR / "references" / "quips.md"
    if not quips.exists():
        errors.append("缺少 references/quips.md（跑 python tools/caster_quips.py）")
    else:
        qt = quips.read_text(encoding="utf-8")
        n = len([l for l in qt.splitlines() if l.startswith("| ") and "招牌" not in l and "---" not in l])
        if n < 15:
            errors.append(f"quips.md 只有 {n} 条招牌，预期 ≥15")
        for q in ("我已启动", "这下看懂了", "好牌多拿"):
            if q not in qt:
                errors.append(f"quips.md 缺招牌：{q}")

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"FAIL  {e}")
    if errors:
        print(f"\n{len(errors)} error(s)")
        return 1
    print(f"OK  {size} 字节 / {len(text.splitlines())} 行，{len(warnings)} warning(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--say", help="整句台词（banner 全文）")
    ap.add_argument("--thought", help="overlay --thought 部分")
    ap.add_argument("--meme", help="overlay --meme 部分")
    args = ap.parse_args()

    if args.say or args.thought or args.meme:
        text = args.say or "。".join(x for x in (args.thought, args.meme) if x)
        results = lint(text)
        for level, msg in results:
            print(f"{level}  {msg}")
        fails = [r for r in results if r[0] == "FAIL"]
        if fails:
            print(f"\nFAIL  「{text}」")
            return 1
        print(f"PASS  {len(text)} 字：「{text}」")
        return 0

    return check_skill()


if __name__ == "__main__":
    sys.exit(main())
