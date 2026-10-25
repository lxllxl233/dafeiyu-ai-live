#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for the sts2-ironclad-ironclad skill pack.

Checks:
  1. YAML frontmatter (name/description) — 丢了技能会从目录里消失（run33 踩过）。
  2. 本文件引用的相对路径都存在。
  3. 没有一代旧卡名（煤渣/嗜血/愤怒/灰烬打击…）。
  4. 关键小节齐全，体量没膨胀（常驻上下文必须精简）。
  5. references/ 由生成器产出、覆盖对应幕/卡池。

Usage: python tools/check_skill.py
"""
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(".").resolve()
SKILL_DIR = ROOT / ".dsh" / "skills" / "sts2-ironclad-ironclad"
SKILL = SKILL_DIR / "SKILL.md"

LEGACY = ["煤渣", "嗜血", "灰烬打击", "冲击波", "暴力", "愤怒"]
SECTIONS = ["三条铁律", "每回合 7 步", "构筑", "敌人", "精英 / Boss", "工具坑", "反模式", "收尾", "口头禅"]
MAX_BYTES = 16000
MAX_LINES = 190

errors = []
warnings = []


def fail(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def main() -> int:
    if not SKILL.exists():
        fail(f"缺少 {SKILL}")
        return report()

    text = SKILL.read_text(encoding="utf-8")
    lines = text.splitlines()
    size = len(text.encode("utf-8"))
    if size > MAX_BYTES:
        fail(f"SKILL.md {size} 字节 > {MAX_BYTES}：常驻上下文膨胀，先压细节")
    if len(lines) > MAX_LINES:
        fail(f"SKILL.md {len(lines)} 行 > {MAX_LINES} 行")
    if not text.startswith("---\n"):
        fail("SKILL.md 缺少 frontmatter（技能会从 available_skills 消失）")
    else:
        fm = text.split("---", 2)[1]
        if "name:" not in fm or "description:" not in fm:
            fail("frontmatter 缺 name/description")
        elif "sts2-ironclad-ironclad" not in fm:
            fail("frontmatter name 与目录名不一致")

    for sec in SECTIONS:
        if sec not in text:
            fail(f"缺少小节：{sec}")

    # 旧卡名（允许出现在「别用一代旧名」这一行）
    for i, line in enumerate(lines, 1):
        if "旧名" in line:
            continue
        for bad in LEGACY:
            if bad in line:
                fail(f"SKILL.md:{i} 出现一代旧卡名「{bad}」：{line.strip()[:40]}")

    # 引用的文件路径
    refs = set(re.findall(r"`([\w./-]+\.(?:md|py|db|json))`", text))
    for ref in sorted(refs):
        p = SKILL_DIR / ref if ref.startswith("references/") else ROOT / ref
        if not p.exists():
            fail(f"引用的文件不存在：{ref}")

    # 子技能文件
    enemies = SKILL_DIR / "references" / "enemies.md"
    cards = SKILL_DIR / "references" / "cards.md"
    for p in (enemies, cards):
        if not p.exists():
            fail(f"缺少子技能文件：{p.relative_to(ROOT)}")
    if enemies.exists():
        et = enemies.read_text(encoding="utf-8")
        for act in ("Act 1 · Overgrowth", "Underdocks", "Act 2 · Hive", "Act 3 · Glory"):
            if act not in et:
                fail(f"enemies.md 缺少分幕：{act}")
        n = len([l for l in et.splitlines() if l.startswith("- ")])
        if n < 80:
            fail(f"enemies.md 只有 {n} 条遭遇，预期 ≥80")
        if "v3" not in et:
            warn("enemies.md 标题里没有 v3")
    if cards.exists():
        ct = cards.read_text(encoding="utf-8")
        n = len([l for l in ct.splitlines() if l.startswith("- ")])
        if n < 80:
            fail(f"cards.md 只有 {n} 张牌，预期 ≥80（铁甲 87 张）")

    # 描述里应该提到两个子文件
    fm = text.split("---", 2)[1] if text.startswith("---") else ""
    if "enemies.md" not in fm:
        warn("description 没提 enemies.md，模型可能不知道要查")

    return report()


def report() -> int:
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"FAIL  {e}")
    if errors:
        print(f"\n{len(errors)} error(s)")
        return 1
    print(f"OK  {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
