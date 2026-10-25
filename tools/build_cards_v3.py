#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate references/cards.md from db/sts2.db (Ironclad only).

Usage: python tools/build_cards_v3.py [--check]
"""
import io
import json
import re
import sqlite3
import sys

OUT = ".dsh/skills/sts2-ironclad-ironclad/references/cards.md"

RE_TPL = re.compile(r"\{[^{}]*\}")
RE_UP = re.compile(r"\{IfUpgraded:show:([^|{}]*)\|([^{}]*)\}")


def clean(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\\n", " ")
    s = RE_UP.sub(lambda m: m.group(2), s)  # 基础版
    s = RE_TPL.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def main() -> int:
    con = sqlite3.connect("db/sts2.db")
    con.row_factory = sqlite3.Row
    rows = list(con.execute("select * from cards where character='Ironclad'"))
    con.close()

    xcost = set()
    try:
        with open("tools/data/eng/cards.json", encoding="utf-8") as f:
            eng = json.load(f)
        for c in (eng if isinstance(eng, list) else eng.values()):
            if c.get("is_x_cost"):
                xcost.add(c.get("id"))
    except (OSError, ValueError):
        pass

    rows.sort(
        key=lambda r: (
            -1 if r["id"] in xcost else (r["cost"] if r["cost"] is not None else 99),
            r["type"],
            r["name_en"],
        )
    )
    total = len(rows)
    if "--check" in sys.argv:
        print(f"{total} Ironclad cards")
        return 0

    out = io.StringIO()
    out.write("# 铁甲战士卡表 v3（db/sts2.db）\n\n")
    out.write("> 用途：奖励/商店/事件时查「这张牌是什么、几费、升级加什么」。\n")
    out.write("> 战斗里的伤害/格挡数值一律以 `state` 实时为准（力量/易伤/遗物都会改）。\n")
    out.write("> 标 ⚠️ 的是本地库文案缺数字或过旧的牌：以游戏内 `resolved_rules_text` 为准。\n\n")

    out.write("## 抓牌三问\n\n")
    out.write("1. 它补的是缺口清单里的**空**位吗（AOE/易伤/防御/运转/收尾）？\n")
    out.write("2. 它比我要删的基础牌强吗？牌组会不会超过 18 张？\n")
    out.write("3. 两幕之内它还会在我的循环里吗？答不上就 skip。\n\n")

    cur = None
    for r in rows:
        cost = "X" if (r["id"] in xcost or (r["cost"] or 0) < 0) else (f"{int(r['cost'])}" if r["cost"] is not None else "?")
        ctype = {"Attack": "攻击", "Skill": "技能", "Power": "能力"}.get(r["type"], r["type"] or "?")
        rarity = {"Basic": "基础", "Common": "普通", "Uncommon": "罕见", "Rare": "稀有", "Ancient": "先古"}.get(
            r["rarity"], r["rarity"] or "?"
        )
        key = f"{cost} 费"
        if key != cur:
            if cur is not None:
                out.write("\n")
            out.write(f"## {key}\n\n")
            cur = key
        base = clean(r["desc_zh"])
        up = clean(r["desc_up_zh"])
        flag = ""
        if re.search(r"(造成|获得|给予|失去)点|获得。|给予层", base + up):
            flag = " ⚠️"
        name = r["name_zh"] or r["name_en"]
        if r["name_en"] and r["name_en"] != name:
            name += f" {r['name_en']}"
        line = f"- {cost} 费 {ctype}/{rarity}｜**{name}**：{base}"
        if up and up != base:
            line += f"｜+：{up}"
        out.write(line + flag + "\n")
    out.write("\n")
    out.write("重新生成：`python tools/build_cards_v3.py`（数据源 `db/sts2.db` 的 cards 表）。\n")

    text = out.getvalue()
    open(OUT, "w", encoding="utf-8", newline="\n").write(text)
    print(f"written {OUT}: {len(text.encode('utf-8'))} bytes, {text.count(chr(10))} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
