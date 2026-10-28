#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regenerate sts2-caster/references/quips.md from the meme DB.

Source of truth: db/sts2.db -> memes table, category = 社区口头禅.
Usage: python tools/caster_quips.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = Path("db/sts2.db")
OUT = Path("games/slay-the-spire-2/persona/quips.md")

HEADER = """# 招牌稿 · 大肥鱼口头禅

> 自动生成：`python tools/caster_quips.py`（源：`db/sts2.db` 的 `memes` 表「社区口头禅」）。
> **用法**：一句台词最多带一句招牌，放句尾，且必须接在**本回合真实数字**后面。
> 每条冷却 8–12 分钟（≈ 一局 1–2 次）；同一句一局不超过 2 次，不解释、改编就换说法。
> 招牌是调味，不是正文：没有数字垫着，招牌就是空喊。

| 招牌 | 什么时候用 | 例句 |
|---|---|---|
"""

FOOTER = """
## 使用提醒

- 冷启动（引擎/连招就位）用「我已启动」，**先说清启动了啥**（薪火？烧牌？易伤链？），再喊。
- 局面揭晓（Boss 套路、发牌员暗示、死因）用「这下看懂了」，**后面必须跟一句“看懂的是什么”**。
- 拿到强牌用「好牌多拿」，但要接「为什么好」（数字/曲线），不然像无脑拿牌。
- SL 相关（时间回溯 / 读档也是尖塔的一部分 / Ctrl+Z）只在真的 SL 后用，一局最多一次。
- 嘴硬（问题不大）只用在**还有救**的时候；真死了就认，别硬撑。
- 精确斩杀用「刚好够」；差 1–2 点没收掉用「差一口气」，不要混用。
"""


def main() -> int:
    if not DB.exists():
        print(f"缺少 {DB}")
        return 1
    con = sqlite3.connect(DB)
    rows = con.execute(
        "select text, trigger, example, cooldown from memes "
        "where category = '社区口头禅' order by id"
    ).fetchall()
    if len(rows) < 15:
        print(f"口头禅只有 {len(rows)} 条，预期 ≥15")
        return 1
    lines = [HEADER]
    for text, trigger, example, cooldown in rows:
        t = (text or "").replace("|", "\\|")
        g = (trigger or "").replace("|", "\\|")
        e = (example or "").replace("|", "\\|")
        cd = f"{cooldown} 分钟" if cooldown else "8–12 分钟"
        lines.append(f"| {t} | {g}（冷却 {cd}） | {e} |\n")
    lines.append(FOOTER)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(lines), encoding="utf-8")
    print(f"OK  {OUT}  {len(rows)} 条口头禅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
