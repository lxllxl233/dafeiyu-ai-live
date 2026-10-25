# -*- coding: utf-8 -*-
"""Rules from the v6 skill overhaul: run the skill self-check after edits."""

import sqlite3

RULES = [
    (
        "skill_check_script",
        "tooling",
        1,
        "改完 skill / references 先跑 tools/check_skill.py",
        "SKILL.md 或 references/*.md 改完，先跑 `python tools/check_skill.py`："
        "① frontmatter 的 name/description 还在；② 引用的文件都存在；③ 没有一代旧卡名（煤渣/嗜血/灰烬打击…）；"
        "④ 关键小节齐全；⑤ 体量没超过 16KB / 190 行。全过再开播。",
        "run33 重写 skill 时把 frontmatter 覆盖掉，技能当场从 available_skills 消失；"
        "v6 审计又发现「煤渣/嗜血」两个一代旧名和「上勾拳 13→15」的错误升级数值——这类错会直接误导出牌。",
        "SKILL.md / references/enemies.md / references/cards.md",
    ),
]


def main() -> None:
    con = sqlite3.connect("db/sts2.db")
    cur = con.cursor()
    for rid, phase, priority, title, rule, why, cards in RULES:
        cur.execute("SELECT 1 FROM strategy_rules WHERE id = ?", (rid,))
        if cur.fetchone():
            cur.execute(
                "UPDATE strategy_rules SET phase=?, priority=?, title=?, rule=?, why=?, cards=? WHERE id=?",
                (phase, priority, title, rule, why, cards, rid),
            )
            print("updated", rid)
        else:
            cur.execute(
                "INSERT INTO strategy_rules (id, phase, priority, title, rule, why, cards) VALUES (?,?,?,?,?,?,?)",
                (rid, phase, priority, title, rule, why, cards),
            )
            print("added", rid)
    con.commit()
    cur.execute("SELECT COUNT(*) FROM strategy_rules")
    print("total rules:", cur.fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
