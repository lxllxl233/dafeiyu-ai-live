# -*- coding: utf-8 -*-
"""Add the route replan rule to db/sts2.db (idempotent)."""

import sqlite3

RULE = (
    "route_replan_on_state",
    "routing",
    1,
    "开局记一条大路线，每步用最新状态对账，触发条件就重规划",
    "开局 `route --commit <opt>` 把大路线记进 logs/route_plan.json（含 HP/金币/药水/牌组快照）；"
    "每到节点先 `plan` 对账，出现这些条件就 REPLAN：血线跌破 70%（<50% 下一格必须营火）、"
    "本段掉血 ≥20% 最大生命、防御牌跨过 6 张、拿到第一张 AOE、药水用光、金币到 100+、"
    "牌组变大 3 张、多了诅咒。改道后重新 route + commit，并在 banner 说明原因。"
    "精英另有三闸（ELITE_GATE）：防御≥6 张 / AOE≥1 / 药水≥1，三项全「是」才把精英当选项。",
    "第 29 局开局计划了商店线，但 23 血（29%）时仍按原计划走进单行道精英——计划是死的，"
    "状态是活的；血量、牌组强度、药水一变，路线权重就该跟着变。",
    "所有路线决策",
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
