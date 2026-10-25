# -*- coding: utf-8 -*-
"""Rule: Bloodletting as an engine, with guardrails."""

import sqlite3

RULE = (
    "bloodletting_as_engine",
    "combat",
    1,
    "放血当引擎：先有甲/斩杀窗口，再拿血换费",
    "放血（-3 血 +2/3 费）是启动核心，但只在三种情况用："
    "① 手里已有 16+ 甲或本回合能铺满 Boss 单击；② 有明确斩杀窗口需要补费；"
    "③ 血线 > Boss 最大单回合攻击 + 3。无甲、无斩杀窗口、血线不够时，放血是自杀；"
    "Boss 战节奏：先血墙/耸肩铺甲 → 放血 → 高费大锤/斩杀，不要反过来。",
    "攻略吸收（Spire of Chaos 构筑文）+ 第 35/37 局教训：A10 放血必须有甲兜底。",
    "BLOODLETTING / 放血 / 血量换费",
)


def main() -> None:
    con = sqlite3.connect("db/sts2.db")
    cur = con.cursor()
    rid, phase, priority, title, rule, why, cards = RULE
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
