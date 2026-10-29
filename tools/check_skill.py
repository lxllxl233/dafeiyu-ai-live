#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for the ironclad decision manual (``decision/knowledge/ironclad/OS.md``).

这个文件以前校验 DSH 技能包里的手册；现在手册直接住在游戏包里，
由 `decision/ctx.py` / `decision/prompts.py` 按需读取。

Checks:
  1. 手册开头的定位行（这是项目内文档，不是 DSH 技能）还在。
  2. 本文件引用的相对路径都存在。
  3. 没有一代旧卡名（煤渣/嗜血/愤怒/灰烬打击…）。
  4. 关键小节齐全，体量没膨胀（手册整本 14KB 内，常驻提示词另有限额）。
  5. references/ 由生成器产出、覆盖对应幕/卡池。
  6. **规则覆盖**：v17.1 的阈值/关键词/反模式编号必须还在——压缩只许改写法，
     不许丢规则。少一条就 FAIL，别用「感觉还在」验收。
  7. **协议覆盖**：一回合一条 batch 的关键标记（card_id / 尾部快照 / STOP 处理）。
  8. **硬闸同步**：games/slay-the-spire-2/decision/hard_rules.py 是 5 条硬闸的唯一来源，手册里的锚点
     必须还在（改阈值一边忘了另一边 → FAIL），且手册要标注来源。
  9. **常驻提示词限额**：`decision/prompts.py` 每回合都在上下文里，超限就 FAIL。

Usage: python tools/check_skill.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(".").resolve()
sys.path.insert(0, str(ROOT / "tools"))
import hard_rules  # noqa: E402
SKILL_DIR = ROOT / "games" / "slay-the-spire-2" / "decision" / "knowledge" / "ironclad"
SKILL = SKILL_DIR / "OS.md"
PROMPT = ROOT / "games" / "slay-the-spire-2" / "decision" / "prompts.py"

LEGACY = ["煤渣", "嗜血", "灰烬打击", "冲击波", "暴力", "愤怒"]
SECTIONS = [
    "传输协议", "记忆分层", "三条铁律", "准入闸", "每回合七步", "数值与斩杀",
    "血预算", "路线", "构筑", "敌人", "精英 / Boss", "工具坑", "反模式", "收尾",
]
MAX_BYTES = 15000
MAX_LINES = 215
MAX_TOKENS = 4600
MAX_PROMPT_BYTES = 7000

# ---- 规则覆盖：压缩可以改措辞，这些数字/机制/编号不能消失 ----
CJK = re.compile(r"[\u3400-\u9fff\uff00-\uffef\u3000-\u303f]")


def est_tokens(text: str) -> int:
    cjk = len(CJK.findall(text))
    return int(cjk + (len(text) - cjk) / 3.6)


THRESHOLDS = [
    "≤8", "9–14", "≥15",            # 铁律2 三档
    "×1.5", "×0.75", "+6/7", "3×3", "5×2",   # 伤害修正/多段
    "2–6", "≤40%",                  # 自伤
    "×0.6",                        # 精英/Boss 总账
    "≤5 血/场", "≤20",              # Act1 道中预算
    "50%", "60%", "70%",            # 血闸三档
    "14–18", "≤22", ">24", "18 张",  # 构筑张数
    "≤3", "≥6", "1–2 张", "4 + 2",   # 打击/防御/AOE/运转
    "199", "15–18", "11–13", "19–33",       # Act1 Boss 账
    "399", "25–35", "12–16", "≥100", "20+", "≤18",  # Act2 Boss 账
    "15–25",                       # 死亡召唤型压血
    "10%", "5–15", "≤3 次",         # 药水/斩杀差/SL
    "60%", "70%", "100+", "+3", "≥20%",     # 闸与 REPLAN
    "飞剑+ 12", "耸肩+ 11", "飞剑 9", "余烬 9", "上勾拳 6.5", "打击 6", "防御 5",
    "3 伤", "6 伤", "79 血", "+4", "+12",    # 状态牌/召唤型精英/血量两件套
]
KEYWORDS = [
    "incoming", "calc.blk", "calc.chain", "calc", "ctx", "plans", "seq", "say", "fx", "auto",
    "hist", "nx", "mech", "potions", "relics", "hand", "choice", "piles",
    "易伤", "虚弱", "脆弱", "胆小", "荆棘", "人工制品", "无实体", "滑溜",
    "召唤", "死亡召唤", "缠结", "懒惰", "蛇行扼杀者", "寄生蛙", "活雾", "雾魔",
    "燃烧之血", "狂宴", "黑暗之血", "重锤", "契约终结", "飞剑", "耸肩", "余烬", "上勾拳",
    "药水四问", "两回合账", "自伤四问", "能量纪律", "AOE 清算",
    "换血闸", "多怪闸", "血闸", "REPLAN", "PLAN_OK",
    "save_and_quit", "continue_run", "choose_reward_card", "use_potion",
    "--full", "--target", "--option",
    "memory --write", "journal", "sts2.py report",
    "obsctl stop", "LESSONS.md", "check_skill.py", "check_caster.py", "speech.md",
    "playbook.md", "cards.md", "enemies.md",
]

ANTI_PATTERNS = "①②③④⑤⑥⑦⑧⑩⑪⑫⑬⑭⑯⑱㉓㉔㉕㉖㉙㉜㉝㉞㉟㊱㊲㊳"
PROTOCOL = ["ctx.py", "act.py", "seq", "say", "fx", "auto", "card_id"]

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
    # 手册长度只做「建议」提示：控制台允许写长，别让保存被字数卡死。
    if size > MAX_BYTES:
        warn(f"OS.md {size} 字节 > 建议 {MAX_BYTES}：已不阻止保存，注意按需读取成本")
    if len(lines) > MAX_LINES:
        warn(f"OS.md {len(lines)} 行 > 建议 {MAX_LINES} 行")
    tok = est_tokens(text)
    if tok > MAX_TOKENS:
        warn(f"OS.md 估算 {tok} token > 建议 {MAX_TOKENS}")
    if not lines or not lines[0].startswith("# 铁甲战士"):
        fail("OS.md 首行应是「# 铁甲战士 A10 · 决策 OS」标题")

    for sec in SECTIONS:
        if sec not in text:
            fail(f"缺少小节：{sec}")

    # 旧卡名（允许出现在「别用一代旧名」这一行）
    for i, line in enumerate(lines, 1):
        if "旧名" in line:
            continue
        for bad in LEGACY:
            if bad in line:
                fail(f"OS.md:{i} 出现一代旧卡名「{bad}」：{line.strip()[:40]}")

    # 引用的文件路径（`references/x.md`、裸文件名、或仓库相对路径）
    refs = set(re.findall(r"`([\w./-]+\.(?:md|py|db|json|mp4))`", text))
    for ref in sorted(refs):
        if ref.startswith("references/"):
            cands = [SKILL_DIR / ref]
        elif "/" not in ref:
            cands = [SKILL_DIR / "references" / ref, ROOT / ref,
                     ROOT / "tools" / ref]
        else:
            cands = [ROOT / ref]
        if not any(p.exists() for p in cands):
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
    head = "\n".join(lines[:16])
    if "enemies.md" not in text:
        warn("正文没提 enemies.md，模型可能不知道要查")
    if "ctx.py" not in head:
        warn("开头没提 ctx.py：模型可能又回去读自然语言快照")

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

    # ---- 规则覆盖（v17.1 → v18 不丢规则） ----
    missing = [t for t in THRESHOLDS if t not in text]
    if missing:
        fail(f"丢了阈值：{', '.join(missing)}")
    missing = [k for k in KEYWORDS if k not in text]
    if missing:
        fail(f"丢了关键词：{', '.join(missing)}")
    missing = [c for c in ANTI_PATTERNS if c not in text]
    if missing:
        fail(f"丢了反模式编号：{''.join(missing)}")
    missing = [p for p in PROTOCOL if p not in text]
    if missing:
        fail(f"缺协议标记：{', '.join(missing)}")

    # 每个反模式编号后面要有内容（别只剩下编号）
    for c in ANTI_PATTERNS:
        m = re.search(re.escape(c) + r"([^①-⑳㉕]{2,})", text)
        if not m:
            warn(f"反模式 {c} 后面像没有说明文字")

    # ---- 硬闸单一来源：decision/hard_rules.py ↔ OS.md ----
    for e in hard_rules.validate():
        fail(f"硬闸自检：{e}")
    for m in hard_rules.verify_skill(text):
        fail(f"硬闸与 OS.md 不同步：{m}（阈值只改 hard_rules，或两边一起改）")
    if "games/slay-the-spire-2/decision/hard_rules.py" not in text:
        fail("OS.md 未标注硬闸唯一来源 `games/slay-the-spire-2/decision/hard_rules.py`")
    if "rules" not in text:
        warn("OS.md 没提 ctx 的 rules 字段：模型可能不知道每回合都有硬闸")

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
