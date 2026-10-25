# -*- coding: utf-8 -*-
"""Rule from run 37: double ritual cultists are a hidden act-1 boss."""

import sqlite3

RULE = (
    "cultist_pair_ritual",
    "combat",
    1,
    "双仪式祭司：T1 必 Buff，T2 起入伤 14→22→30",
    "双仪式祭司（Calcified +2/回合、Damp +6/回合）T1 固定 Buff，T2 起单回合入伤 14 → 22 → 30，每回合涨 8。"
    "应对：① HP<60% 且起手没有 16+ 大甲（血墙/岿然不动）时不要接这场；"
    "② 优先 T3 前杀 +6 力量那只（Damp），否则 T4 27+ 直接压死；"
    "③ 每回合至少 20 甲或斩杀，5-8 甲的小防御磨不过仪式成长。",
    "第 37 局 39 血进双祭司，T1/T2 手里无甲，三次 SL 都死在 T4 的 30 入伤。",
    "CALCIFIED_CULTIST / DAMP_CULTIST / 双祭坛",
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
