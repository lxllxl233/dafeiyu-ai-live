#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for the ironclad decision manual (``decision/knowledge/ironclad/OS.md``).

手册从 v23 起改成「协议 + 决策流程 + 反模式」结构，旧的 v17 阈值清单不再逐条比对；
仍然守住这些会真出事的点：

  1. 标题与手册定位行。
  2. 核心小节还在（传输协议 / 战斗主流程 / 路线 / 反模式 / 版本维护）。
  3. 引用的相对路径都存在。
  4. 没有一代旧卡名（煤渣/嗜血/愤怒/灰烬打击…）。
  5. `references/` 的卡表/敌人表由生成器产出、覆盖对应幕/卡池。
  6. 协议标记还在（ctx / act / seq / say / fx / card_id）。
  7. 反模式章节仍有足够条目，别被压没。
  8. `hard_rules.py` 自检通过，手册标注它的唯一来源地位。
  9. `prompts.py` 常驻提示词限额 + 关键字段。
 10. 手册长度只做 WARN（控制台允许写长，不阻断保存）。

Usage: python tools/check_skill.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(".").resolve()
sys.path.insert(0, str(ROOT / "tools"))
import hard_rules  # noqa: E402

GAME = ROOT / "games" / "slay-the-spire-2"
SKILL_DIR = GAME / "decision" / "knowledge" / "ironclad"
SKILL = SKILL_DIR / "OS.md"
PROMPT = GAME / "decision" / "prompts.py"

LEGACY = ["煤渣", "嗜血", "灰烬打击", "冲击波", "暴力", "愤怒"]
SECTIONS = ["传输协议", "战斗决策主流程", "路线", "反模式", "版本维护"]
MIN_BYTES = 3000                 # 太短说明被压没了
MAX_BYTES = 15000                # 只作建议提示
MAX_PROMPT_BYTES = 7000
PROTOCOL = ["ctx", "act.py", "seq", "say", "fx", "card_id"]
ANTI_MIN = 15

CJK = re.compile(r"[\u3400-\u9fff\uff00-\uffef\u3000-\u303f]")


def est_tokens(text: str) -> int:
    cjk = len(CJK.findall(text))
    return int(cjk + (len(text) - cjk) / 3.6)


errors = []
warnings = []


def fail(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def _exists(ref: str) -> bool:
    if ref.startswith("references/"):
        return (SKILL_DIR / ref).exists()
    if "/" in ref:
        return (ROOT / ref).exists()
    cands = [SKILL_DIR / "references" / ref, ROOT / ref,
             ROOT / "tools" / ref, GAME / ref,
             GAME / "decision" / ref, GAME / "autoplay" / ref]
    return any(p.exists() for p in cands)


def main() -> int:
    if not SKILL.exists():
        fail(f"缺少 {SKILL}")
        return report()

    text = SKILL.read_text(encoding="utf-8")
    lines = text.splitlines()
    size = len(text.encode("utf-8"))
    # 手册长度只做「建议」提示：控制台允许写长，别让保存被字数卡死。
    if size < MIN_BYTES:
        fail(f"OS.md 只有 {size} 字节，像是被压没了（至少 {MIN_BYTES}）")
    if size > MAX_BYTES:
        warn(f"OS.md {size} 字节 > 建议 {MAX_BYTES}：注意按需读取成本")
    tok = est_tokens(text)
    if tok > 4600:
        warn(f"OS.md 估算 {tok} token > 建议 4600")
    if not lines or not lines[0].startswith("# 铁甲战士"):
        fail("OS.md 首行应是「# 铁甲战士 A10 · 决策 OS」标题")

    for sec in SECTIONS:
        if sec not in text:
            fail(f"缺少小节：{sec}")

    for i, line in enumerate(lines, 1):
        if "旧名" in line:
            continue
        for bad in LEGACY:
            if bad in line:
                fail(f"OS.md:{i} 出现一代旧卡名「{bad}」：{line.strip()[:40]}")

    # 引用的文件路径（`references/x.md`、裸文件名、或仓库相对路径）
    refs = set(re.findall(r"`([\w./-]+\.(?:md|py|db|json|mp4))`", text))
    for ref in sorted(refs):
        if not _exists(ref):
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

    # 协议标记
    missing = [p for p in PROTOCOL if p not in text]
    if missing:
        fail(f"缺协议标记：{', '.join(missing)}")

    # 反模式章节的条目数
    m = re.search(r"##\s*14[^\n]*反模式[^\n]*\n(.*?)(?=\n##\s|\Z)", text, re.S)
    if not m:
        fail("找不到第 14 节「反模式与停止条件」")
    else:
        items = len(re.findall(r"^\s*\d+\.\s+\S", m.group(1), re.M))
        if items < ANTI_MIN:
            fail(f"反模式只有 {items} 条，预期 ≥{ANTI_MIN}")

    # ---- 常驻提示词限额（每回合都在上下文里） ----
    if not PROMPT.exists():
        fail(f"缺少 {PROMPT.relative_to(ROOT)}")
    else:
        pt = PROMPT.read_text(encoding="utf-8")
        psize = len(pt.encode("utf-8"))
        if psize > MAX_PROMPT_BYTES:
            fail(f"prompts.py {psize} 字节 > {MAX_PROMPT_BYTES}：常驻提示词膨胀")
        for key in ("calc.line", "calc.inc", "allow_leftover", "context.acts"):
            if key not in pt:
                fail(f"prompts.py 缺关键字段：{key}")

    # ---- 硬闸单一来源：decision/hard_rules.py ----
    for e in hard_rules.validate():
        fail(f"硬闸自检：{e}")
    if "games/slay-the-spire-2/decision/hard_rules.py" not in text:
        fail("OS.md 未标注硬闸唯一来源 `games/slay-the-spire-2/decision/hard_rules.py`")

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
