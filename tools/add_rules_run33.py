# -*- coding: utf-8 -*-
"""Rules from run 33: inferno self-damage at low HP, hard deck cap."""

import sqlite3

RULES = [
    (
        "inferno_low_hp",
        "combat",
        1,
        "狱火/回合开始自伤：HP ≤ 3 时必须先算会不会直接死",
        "狱火每回合开始失去 1 点生命。HP 只有 1–3 时，这一掉可能直接结束游戏（而且优先于任何格挡结算）。"
        "低血时要么先把它当不存在（不再靠它输出），要么明确接受「这回合就是最后一回合」，"
        "先把血补回来或斩杀，再谈续航。",
        "第 33 局层 15：靠血墙+坚毅挡到只剩 1 血，回合开始狱火 -1 直接判负。",
        "INFERNO / 任何回合开始自伤的牌或遗物",
    ),
    (
        "deck_cap_hard",
        "deck",
        1,
        "牌组 >18 张后：非质变牌一律跳过",
        "Act1 目标 12–16、最终 16–18。超过 18 张后，卡牌奖励只拿「质变」：引擎（放血/燃烧）、"
        "大甲（血墙/耸肩）、AOE、核心收尾；普通伤害牌、第二张三档牌直接 skip_reward_cards。"
        "牌越厚，抽到关键防御的概率越低——低血时这一抽就是生死。",
        "第 33 局从 13 张一路加到 21 张（芝士 2 张 + 商店 + 精英奖励），后期手里经常只有 1–2 张能用的牌。",
        "所有卡牌奖励",
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
