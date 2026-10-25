# -*- coding: utf-8 -*-
"""Rule: Regent (star-cost) cards from Kaleidoscope are dead for Ironclad."""

import sqlite3

RULE = (
    "kaleidoscope_star_cost",
    "deck",
    1,
    "万花筒外角色牌：储君（Regent）牌要星，铁甲打不出",
    "万花筒/外角色卡奖励混合所有角色。储君（The Regent）的牌用“星”支付：star_cost > 0（0 能量的 AOE/大招多半是星牌），"
    "铁甲没有星资源 → 抽到也 UNPLAYABLE 或根本打不出。选牌前看 `sts2.py state` 的 `star=N`，星牌一律不选；"
    "可选 Defect/Silent/Necrobinder 用能量或生命支付的牌。注意 Defect 的 Turbo 能用，但会把一张虚空塞进弃牌堆。",
    "第 39 局万花筒选了储君的 Astral Pulse（0 费 6×2 AOE），实战 UNPLAYABLE，白废一张牌。",
    "ASTRAL_PULSE / Kaleidoscope / star_cost",
)


def main() -> None:
    con = sqlite3.connect("db/sts2.db")
    cur = con.cursor()
    rid, phase, priority, title, rule, why, cards = RULE
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
