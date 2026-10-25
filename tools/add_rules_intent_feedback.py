# -*- coding: utf-8 -*-
"""Add the rules from run 31 feedback (intent prediction / fresh opening / potion execute)."""

import sqlite3

RULES = [
    (
        "intent_pattern_predict",
        "combat",
        1,
        "敌人意图是固定循环：用 INTENT_MEM 预判下一手",
        "同一只敌人的招式按固定序列循环（攻击→Buff→攻击…）。`state` 的 `INTENT_MEM` 行会记录见过的意图序列，"
        "并给出「上次出现后接的是：…」。已知下一手是大攻击（≥当前血量 25%）时，"
        "这一回合就要提前留格挡/上虚弱，而不是等它打出来再补。",
        "第 31 局多场战斗都只按「本回合入伤」决策，没有利用循环信息；"
        "如果能预判下一手 25 点攻击，Boss 战可以少掉十几血。",
        "所有多回合战斗",
    ),
    (
        "potion_for_execute",
        "combat",
        1,
        "斩杀药水（石头 15 伤等）用来在 T1 补足斩杀线，优先拆掉召唤者/触发死亡机制",
        "开场就能用固定伤害药水（药水形状的石头 15 伤、火焰药水等）把某个敌人直接推进斩杀线："
        "多怪战先杀一只经常能触发「击杀收益」（噬尸蛞蝓吃掉尸体被眩晕、召唤者死亡爪牙消失），"
        "等于白赚一个回合。别把这种药水留到残血才想起来用。",
        "第 31 局双尾鼠三只（22/21/19）：T1 双发 14 + 打击 6 = 20，配合石头 15 = 35，"
        "开局就能斩掉一只并赚到眩晕回合；实际拖了 7 回合掉 24 血（用户指出）。",
        "药水形状的石头 / 火焰药水 / 所有固定伤害药水",
    ),
    (
        "opening_fresh",
        "invariant",
        1,
        "开场白当全新一局：不引用上一局/历史",
        "open_character_select 的 thought/meme 只谈这一局的计划（思路、路线意向、纪律），"
        "禁止出现「上局/上次/第几局/之前那局」这类历史引用；复盘和历史放在 logs 与 LESSONS 里，不上直播。",
        "用户指出：开局讲话不要带「上局」，每一轮都当全新一局。",
        "开场白 / 所有对外解说",
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
