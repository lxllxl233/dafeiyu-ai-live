# -*- coding: utf-8 -*-
"""Rules from run 36: ringing can lock all cards; Hellraiser strike engine."""

import sqlite3

RULES = [
    (
        "ringing_locks_cards",
        "combat",
        1,
        "昏眩可能整回合锁牌：进昏眩前 HP 必须 > Boss 最大单回合攻击",
        "昏眩/Ringing 的说明是“本回合只能打出 1 张牌”，但实测有些回合全部手牌被标 UNPLAYABLE、`play_card` 非法，"
        "等于整回合空过（第 36 局 T11/T13 各空过一轮，白吃 21+25）。因此："
        "① 不要把“还能出 1 张”当保底；② 进昏眩回合前 HP 必须 > Boss 最大单回合攻击（Act1 25+），"
        "否则一张 5-8 甲的牌也救不了；③ 昏眩回合若牌全锁，只能 end_turn，提前把血线留够。",
        "第 36 局：1 血进昏眩，全手牌 UNPLAYABLE，被 25 攻收掉；Boss 剩 27 血。",
        "Boss 战；看到“昏眩/Ringing”状态",
    ),
    (
        "hellraiser_strike_engine",
        "deck",
        1,
        "地狱狂徒 + 打击系牌 = Act1 Boss 免费 DPS 引擎",
        "地狱狂徒（2 费能力）：每当你抽到名字含“打击”的牌，对随机敌人免费打出。牌组里打击/双重打击/完美打击越多，"
        "每次抽牌（战斗专注、耸肩、剑柄）都在白嫖伤害；5 张打击系时 Boss 战自动刀可打 100+。"
        "抓牌优先级：先拿 1-2 张抽牌，再补打击系攻击，不要急着删光打击。",
        "第 36 局靠地狱狂徒把 262 血 Boss 打到 27；第 35 局没有这张引擎，伤害明显不够。",
        "Act1 抓牌；Boss 前构筑",
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
