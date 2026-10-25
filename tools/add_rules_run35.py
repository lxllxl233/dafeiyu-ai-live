# -*- coding: utf-8 -*-
"""Rules from run 35: boss block cap, ringing single-card, all-enemy potion pending."""

import sqlite3

RULES = [
    (
        "boss_block_cap_act1",
        "combat",
        1,
        "Act1 Boss 单回合 20–26 伤：进 Boss 前至少 2 张单卡 15+ 防御",
        "Ceremonial Beast / Kin Priest / Vantom 的单回合入伤在 20–26。若牌组里只有 5–8 甲的小防御，"
        "单回合最大格挡 < 入伤，每回合净掉 10+，几回合就把血量打光。进 Boss 前硬指标："
        "① 2 张单卡 15+ 防御（岿然不动 30/40、血墙+ 20、邪眼+ 烧牌 22、壁垒+ 保留）；"
        "② 单回合最大格挡 ≥ Boss 最大单回合入伤；③ 达不到就继续找防御牌，不要先买中质量攻击。",
        "第 35 局：6 张防御只有邪眼一张 8+，Boss 20→26，单回合最多挡 12–16，"
        "T2–T4 硬吃 34，最终 Boss 剩 37 血时自己先死。",
        "Boss 前构筑检查；商店买牌",
    ),
    (
        "ringing_turn_single_card",
        "combat",
        1,
        "昏眩/Ringing 回合只 1 张牌：必须留一张 15+ 甲的牌",
        "Boss/敌人给你「本回合只能打 1 张牌」时，7–8 甲的防御牌等于白给。应对："
        "① 牌组带一张单卡 15+ 的防御（岿然不动/血墙+/邪眼+）；② 或在 Boss 非攻击回合提前铺壁垒/保留格挡；"
        "③ 计算下一手是不是 Ringing，提前把这唯一一张牌留给大甲而不是输出。",
        "第 35 局 T7/T10 两次昏眩：只能打坚毅 7 / 防御+ 8，分别白吃 10 和 13，18 血掉到 5。",
        "Boss 战；看到「昏眩/Ringing」状态",
    ),
    (
        "ampoule_pending",
        "tooling",
        1,
        "全敌药水（爆炸安瓿）pending 不结算：别算进计划",
        "战斗内对全敌药水用 `use_potion --option <i>` 会返回 pending，之后状态不变、药水仍在槽里（本局试了 2 次）。"
        "不要把它算进斩杀/防御计划，也不要反复重试；优先用自身/单体药水（力量药水、能力药水都正常结算）。",
        "run35 打缩小甲虫和 Boss 前各试一次爆炸安瓿，都返回 pending 且 0 伤害。",
        "爆炸安瓿 / 任何全敌药水",
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
