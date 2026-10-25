# -*- coding: utf-8 -*-
"""Add the rules learned in run 31 (Act 1 boss death, Soul Fysh) to db/sts2.db."""

import sqlite3

RULES = [
    (
        "beckon_discard_first",
        "combat",
        1,
        "「呼唤」类回合末掉血牌：优先花 1 费扔掉，别贪输出",
        "灵魂异鱼等 Boss 会往手牌塞「呼唤」：留在手里回合末扣 6 点（无视格挡）。"
        "每张 1 费换 6 血在任何时候都是赚的；除非这一回合能直接斩杀，否则先把它们扔干净。"
        "判断：`手牌张数 × 6 ≥ 当前血量的一半` → 全部扔掉，输出让位。",
        "第 31 局 Boss 战：为了多打 9 点伤害把一张呼唤留在手里，13 血→7 血，"
        "下一轮被清掉，Boss 当时只剩 9 血。",
        "Soul Fysh / 所有塞呼唤的敌人",
    ),
    (
        "boss_turn_budget",
        "combat",
        1,
        "进 Boss 前也算「清场回合 × 每回合净掉血」，跟精英同一套账",
        "Boss 血量厚，很多人只看 HP 阈值。正确算法：`回合数 ≈ Boss 血 ÷ 实际 DPS`，"
        "`承受 ≈ 回合数 × 每回合净掉血`；这个数 > 当前血量就按「必输局」准备——"
        "药水全部留到 Boss、路线优先多睡一次、进 Boss 前尽量把血补到 80% 以上。",
        "第 31 局灵魂异鱼 221 血、我 DPS 约 22–25 → 9 回合；它每回合 15–25 入伤 → 需要 135+ 血，"
        "进 Boss 只有 70 血，打到它剩 9 血时我先倒下。",
        "所有 Boss（尤其 200+ 血的）",
    ),
]


def main() -> None:
    con = sqlite3.connect("db/sts2.db")
    cur = con.cursor()
    added = 0
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
            added += 1
            print("added", rid)
    con.commit()
    cur.execute("SELECT COUNT(*) FROM strategy_rules")
    print("total rules:", cur.fetchone()[0], "| added:", added)
    con.close()


if __name__ == "__main__":
    main()
