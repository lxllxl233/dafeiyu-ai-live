# -*- coding: utf-8 -*-
"""Add the post-debuff re-read rule (run 30 feedback) to db/sts2.db."""

import sqlite3

RULE = (
    "debuff_then_recheck",
    "combat",
    1,
    "挂上易伤/力量后立刻重读 EFF 再决定收手",
    "打出痛击/嘲讽/战栗/燃烧这类改变伤害系数的牌之后，必须重新读一次 `state` 的 `EFF`："
    "易伤让后续攻击 ×1.5（6→9、8→12），力量让每张攻击 +N，斩杀线会当场变。"
    "同样的，`end_turn` 返回的快照可能是「还没抽牌」的过渡态（手牌 0–1 张），不能拿它当回合开始；"
    "决策前一律 `python tools/sts2.py state` 重读。",
    "第 30 局对伯多尼斯：痛击把它打到 8 血并挂上 2 易伤，我直接结束回合；实际上手牌里还有能打的牌，"
    "8 血 + 易伤正好是可以直接斩杀的回合（用户当场指出）。"
    "根因是拿 end_turn 的过渡快照（手牌显示 1 张）当成了完整手牌。",
    "Bash / Taunt / Tremble / Inflame",
)


def main() -> None:
    con = sqlite3.connect("db/sts2.db")
    cur = con.cursor()
    cur.execute("SELECT 1 FROM strategy_rules WHERE id = ?", (RULE[0],))
    if cur.fetchone():
        cur.execute(
            "UPDATE strategy_rules SET phase=?, priority=?, title=?, rule=?, why=?, cards=? WHERE id=?",
            RULE[1:] + (RULE[0],),
        )
        print("updated", RULE[0])
    else:
        cur.execute(
            "INSERT INTO strategy_rules (id, phase, priority, title, rule, why, cards) VALUES (?,?,?,?,?,?,?)",
            RULE,
        )
        print("added", RULE[0])
    con.commit()
    cur.execute("SELECT COUNT(*) FROM strategy_rules")
    print("total rules:", cur.fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
