# -*- coding: utf-8 -*-
"""Rules from run33 feedback: pre-lethal setup turn + don't leave block in hand."""

import sqlite3

RULES = [
    (
        "pre_lethal_setup_turn",
        "combat",
        1,
        "下回合大概率斩杀时：本回合只做铺垫，剩下全叠防御",
        "如果算出来「下回合能斩杀」（敌血 ≤ 下回合可用输出 + 已挂易伤/力量的加成），"
        "本回合就不要为了多点伤害去裸奔：优先叠防御、把易伤/力量/引擎铺好，"
        "让斩杀回合以满血或接近满血的状态开局。少掉的血就是斩杀回合的保险。",
        "用户指出：有时候下回合有大概率斩杀，本回合稍微压一下血线、剩下叠防御就好，可以少掉不少血。",
        "所有战斗（尤其 Boss/精英）",
    ),
    (
        "left_block_on_table",
        "combat",
        1,
        "手上有格挡牌又有能量时，先挡满再谈输出",
        "结束回合前检查：`BLOCK_POOL` 显示手牌能挡多少、入伤多少。只要「可挡 ≥ 入伤」且能量够，"
        "就必须先把伤害挡住；把耸肩/防御/血墙留在手里结束回合 = 白吃一整轮伤害。"
        "工具会在 end_turn 前打 `LEFT_BLOCK_ON_TABLE` 告警。",
        "第 33 局潜行群落：手里两张耸肩（共 16 格挡）还有 2 点能量，却直接结束回合硬吃伤害（用户指出）。",
        "耸肩无视 / 防御 / 血墙 / 坚毅",
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
