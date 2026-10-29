# -*- coding: utf-8 -*-
"""5 条硬闸的**唯一来源**：由 ctx 每回合注入，不依赖 skill 是否重新加载。

为什么需要这个文件（2026-10-28 审计）
------------------------------------
这 5 条规则原本只写在 `games/slay-the-spire-2/decision/knowledge/ironclad/OS.md` 里，靠 `skill`
工具一次性加载进模型上下文。审计 run96–run106 的会话文件时发现：

- skill 只在 20:04 / 21:55 / 22:14 加载过 3 次，之后 3.5 小时没有重载；
- 会话被自动压缩 20 次，每次用一段摘要替换前文，SKILL 正文因此掉出上下文；
- 20 份摘要里「≥15 全挡」出现 1 次、「血闸」1 次、「自伤≤40%」0 次、「Boss 不省药」0 次；
- run104 T2（21 点斩击不挡）、run105 f7（30% 血进精英）、run105 T3（6 血交血墙）
  都发生在规则已经不在输入里的回合。

现在改为：`tools/ctx.py` 在**每次构建局面包**时把 `rules` 写进返回值。
模型每回合读 ctx/act 的返回体就能看到，与压缩、与 skill 是否重载无关。

单一来源约定
------------
1. 注入文本的唯一来源就是本文件的 ``RULES``；改阈值只改这里。
2. ``SKILL.md`` 仍然保留人类可读的展开（它太长，不适合每次注入），
   但 ``tools/check_skill.py`` 会校验每条规则的 ``anchor`` 片段还在 SKILL 里，
   改了一边忘了另一边会 FAIL。
3. 改完跑：``python tools/test_rules_in_ctx.py`` + ``python tools/check_skill.py``。
"""
from __future__ import annotations

# 注入格式版本：账本/审计看到 rules_v 变化就知道口径改过。
VERSION = 1

# 每条：id（稳定标识）、t（注入文本，≤60 字）、src（人类可读出处）、
#       anchor（必须同时存在于 SKILL.md 的片段，用于漂移检测）。
RULES = (
    {
        "id": "dmg",
        "t": "小伤害必挡：≤8 挡满；9–14 吃满 calc.blk；≥15 全挡/斩杀/喝药",
        "src": "SKILL.md §1 铁律2",
        "anchor": ("≤8挡满", "9–14吃满calc.blk", "≥15全挡"),
    },
    {
        "id": "hp",
        "t": "血闸：<50%只接营火/事件；<60%不进精英，卡组A/B可放宽；<70%不进Boss",
        "src": "SKILL.md §2 准入闸",
        "anchor": ("血闸：<50%", "卡组A/B可放宽", "<70%不进Boss"),
    },
    {
        "id": "self",
        "t": "自伤闸：HP≤40% 不交放血/祭品/血墙/突破；有别的回能不交",
        "src": "SKILL.md §4 自伤四问",
        "anchor": ("HP≤40%不交",),
    },
    {
        "id": "potion",
        "t": "药水闸：Boss 不省药（剩≥1 瓶=失误）；挡不住且净掉≥10% 最大生命就喝",
        "src": "SKILL.md §4 药水四问",
        "anchor": ("Boss不省药", "剩≥1瓶"),
    },
    {
        "id": "boss",
        "t": "Boss 前：HP≥70% + 2 张 8+ 甲 + 药水先验过；不达标先回血/营火",
        "src": "SKILL.md §8 精英/Boss",
        "anchor": ("Boss前：HP≥70%",),
    },
)

# 注入给模型的最小载荷（不带 src/anchor）。
_MAX_CHARS = 60


def for_ctx() -> list[dict]:
    """返回注入用列表；每次新对象，调用方随便改。"""
    return [{"id": r["id"], "t": r["t"]} for r in RULES]


def ids() -> tuple[str, ...]:
    return tuple(r["id"] for r in RULES)


def all_text() -> str:
    """一行展示，日志/报错用。"""
    return "；".join(f"{r['id']}：{r['t']}" for r in RULES)


def verify_skill(skill_text: str) -> list[str]:
    """SKILL.md 与硬闸是否同步；返回缺失的锚点（空列表=一致）。"""
    miss = []
    for r in RULES:
        for a in r["anchor"]:
            if a not in (skill_text or ""):
                miss.append(f"{r['id']} 缺少锚点 {a!r}")
    return miss


def validate() -> list[str]:
    """自检：条数/长度/id 唯一。测试和 check_skill 都会调。"""
    bad = []
    if len(RULES) != 5:
        bad.append(f"硬闸必须是 5 条，现在是 {len(RULES)}")
    seen = set()
    for r in RULES:
        if r["id"] in seen:
            bad.append(f"重复 id {r['id']}")
        seen.add(r["id"])
        if not r.get("t"):
            bad.append(f"{r['id']} 缺注入文本")
        elif len(r["t"]) > _MAX_CHARS:
            bad.append(f"{r['id']} 注入文本 {len(r['t'])} 字 > {_MAX_CHARS}（每题都注入，别写长）")
        if not r.get("anchor"):
            bad.append(f"{r['id']} 缺 anchor")
    return bad


if __name__ == "__main__":
    import sys
    errs = validate()
    if errs:
        print("FAIL")
        for e in errs:
            print(" -", e)
        sys.exit(1)
    print(f"OK {VERSION} {len(RULES)} 条")
    for r in RULES:
        print(f"  {r['id']}: {r['t']}")
