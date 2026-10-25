# -*- coding: utf-8 -*-
"""Add the elite turn-budget rules learned in run 30 (idempotent)."""

import sqlite3

RULES = [
    (
        "elite_turn_budget",
        "combat",
        1,
        "进精英前算「清场回合 × 每回合净掉血」，别只看三闸",
        "精英三闸（HP≥70% / 防御≥6 / AOE≥1 / 药水≥1）只是必要条件。还要算："
        "① 清场回合数 ≈ 敌人总血 ÷ 我的实际 DPS（含力量/易伤修正后的每回合真实输出）；"
        "② 每回合净掉血 ≈ 敌人入伤 − 我能稳定挡下的部分；"
        "③ 承受伤害 ≈ 回合数 × 每回合净掉血。"
        "这个数超过当前血量的 60%，就不打精英——不管闸门多绿、遗物多香。",
        "第 30 局 94% 血（75/80）进 90 血精英伯多尼斯：单体 DPS 约 22–26，清场要 4 回合，"
        "对方入伤 19→23，4 回合要掉 70–80 血 —— 打完只剩 25 血，等于用一个遗物换掉整局容错。",
        "所有精英（尤其领地意识/仪式/力量成长型）",
    ),
    (
        "elite_potion_timing",
        "combat",
        1,
        "爆发药水留给断档回合，别在 T1 交",
        "精英战第 1 回合手牌最全、伤害最高，通常不是需要药水的时刻。把攻击/能量药水留到"
        "「手牌只剩 1–2 张、牌堆抽空」的断档回合，或者留到必须斩杀的回合；"
        "判断标准是「这一回合不用药水会不会直接决定胜负」，而不是「这回合能不能多打一点」。",
        "第 30 局对伯多尼斯 T1 就用了攻击药水换 18 伤，但真正的危机在 T4（手牌只剩 1 张痛击、硬吃 21），"
        "药水那时候已经不在了。",
        "攻击药水 / 能量药水 / 癫狂之触",
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
