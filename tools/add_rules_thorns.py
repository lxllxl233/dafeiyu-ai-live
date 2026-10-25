# -*- coding: utf-8 -*-
"""Rule: check block before attacking thorns enemies."""

import sqlite3

RULE = (
    "thorns_block_check",
    "combat",
    0,
    "打荆棘前先看甲：反伤会真掉血，不能顺手白嫖",
    "攻击带「荆棘/反伤」的敌人前，先算 段数 × 荆棘层数 的反伤：① 当前格挡 ≥ 反伤总量 → 可以放心砍；"
    "② 不够 → 这一刀会真掉血，只有「斩杀 / 必须换节奏 / 收益明显大于损失」才砍；"
    "③ 不要因为顺手、0 费就砍荆棘，白掉 2-4 血是低级失误，HP 是 Boss 门票。",
    "第 40 局对荆棘蟾蜍连续硬砍，白掉 2+2 血，用户批评「打荆棘不先看有没有甲吗」。",
    "TOADPOLE / 荆棘 / THORNS",
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
