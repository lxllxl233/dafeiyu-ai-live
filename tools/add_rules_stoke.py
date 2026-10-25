# -*- coding: utf-8 -*-
"""Add the Stoke / curse-as-fuel rules from run 32 feedback."""

import sqlite3

RULES = [
    (
        "stoke_burn_junk",
        "combat",
        1,
        "手里有添柴时，垃圾手先打添柴：烧掉换等量新牌",
        "「添柴 Stoke」= 消耗整手牌、每消耗 1 张抽 1 张，1 费。只要手上有 ≥2 张诅咒/状态/低价值牌，"
        "就先打它：等于一次深度过滤 + 免费过牌，是处理「手牌被塞满」最便宜的手段。"
        "不要因为它「消耗手牌」就留到最后——它烧的是垃圾，不是核心牌（核心牌会先打出去）。",
        "第 32 局两只史莱姆战：连续 3 个回合手里有添柴 + 2~3 张 Slimed/Guilty，我一次没打，"
        "战斗拖到 7 回合、白挨两轮攻击，用户当场叫停。",
        "Stoke / 坚毅 / 所有消耗手牌的牌",
    ),
    (
        "curse_is_fuel",
        "combat",
        1,
        "诅咒在战斗内可以当燃料：消耗掉就是净赚",
        "诅咒/状态牌在战斗里被消耗（添柴、坚毅、其他消耗效果）虽然不会从牌组永久移除，"
        "但本场战斗等于清掉一张死牌 + 少一次抽牌污染。别把诅咒「留着占位」等商店删——"
        "能用消耗烧掉就烧。",
        "第 32 局 Guilty 诅咒在手里反复卡位，添柴本来能直接烧掉。",
        "Guilty / Slimed / 所有诅咒与状态牌",
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
