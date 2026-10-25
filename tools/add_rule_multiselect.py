# -*- coding: utf-8 -*-
"""Add the multi-select tooling rule to db/sts2.db (idempotent)."""

import sqlite3

RULE = (
    "multiselect_progress_state",
    "event",
    1,
    "多选牌界面先读 SELECTION_STATE，一次用 --options 点完",
    "CARD_SELECTION 分三种：卡牌奖励（max_select=0，用 choose_reward_card/skip_reward_cards）、"
    "单选（min_select=1）、多选（min_select≥2）。多选是「先选中、选满才结算」：点完一张列表不变是对的，"
    "看 state 打印的 SELECTION_STATE 已选 k/N，别再点同一张（会取消）。整组用 "
    "`do select_deck_card --options 0,2`（按卡牌身份重定位下标，需要时自动 confirm_selection）。",
    "第 29 局涅奥松动羊毛剪要删 2 张：点第 1 张后状态没变，误以为没生效；又用 wait --player-turn 空等 30 秒。"
    "根因是 compact() 没打印 mod 已提供的 selected_count/min_select/max_select。",
    "松手羊毛剪 / 商店删牌 / 所有 CARD_SELECTION",
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
