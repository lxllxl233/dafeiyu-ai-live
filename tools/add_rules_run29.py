# -*- coding: utf-8 -*-
"""Add the three rules learned in run 29 to db/sts2.db (idempotent)."""

import sqlite3

NEW_RULES = [
    (
        "event_option_text_check",
        "event",
        1,
        "事件/奖励选项：下标必须与 state 打印的 opt[i] 文本逐字核对",
        "选任何选项前，先读 state 里 opt[i] 的中文标题，再确认 --option 下标与 thought 里的意图一致。"
        "典型陷阱：光/暗之门（opt[0]=光之门 随机升级 2 张，opt[1]=暗之门 删 1 张）——删牌优先级远高于随机升级。"
        "同理适用于营火、商店、奖励界面：文本优先于记忆里的顺序。",
        "第 29 局「光与暗的门扉」thought 写「暗之门删牌」，实际传 --option 0 点了光之门，"
        "白丢一次删牌（只换来 2 张打击+）。",
        "Doors of Light and Dark / 所有多选项事件",
    ),
    (
        "elite_block_cap_check",
        "combat",
        1,
        "进精英前算「单回合最大格挡 vs 每回合入伤」，4 怪精英还要 AOE",
        "除 HP≥70%、DPS≥20、有药水外，再加一条硬指标：单回合可产出的最大格挡 ≥ 精英每回合入伤，"
        "否则就是结构性必输局（血会按回合数线性归零）。4 只以上精英再叠加 AOE 检查：无 AOE 时"
        "单目标首刀会被『胆小/覆甲』吃掉，实际 DPS 远低于纸面。",
        "第 29 局 23 血进四幻影园丁（120 血、每回合 24–30 入伤），最大格挡只有 16–21，"
        "需要 12 回合清场而血量只够 3 回合，数学上不可能赢。",
        "四幻影园丁 / 所有精英",
    ),
    (
        "barricade_bank_block",
        "combat",
        1,
        "壁垒/覆甲在场时：格挡溢出是储值，先把盾堆过入伤再转输出",
        "拿到壁垒（格挡不清空）或稳定覆甲后，打法从「挡到 5 + 剩余费输出」切换为"
        "「每回合把格挡堆到 ≥ 本回合入伤，溢出部分留到下回合」，盾滚起来之后再全力输出。"
        "此时多打的防御牌不是浪费，是存款；但也要同步算「敌人总血 ÷ 我的实际 DPS」是否在血量耗尽前能清场。",
        "第 29 局精英战药水开出 0 费壁垒，前两回合 0 掉血并滚起格挡；但因为没有 AOE，"
        "输出侧跟不上，壁垒只买到了 2 个回合。",
        "Barricade / 覆甲类遗物与药水",
    ),
]


def main() -> None:
    con = sqlite3.connect("db/sts2.db")
    cur = con.cursor()
    added = 0
    for rid, phase, priority, title, rule, why, cards in NEW_RULES:
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
