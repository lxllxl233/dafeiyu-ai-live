# -*- coding: utf-8 -*-
"""Hard rule: never end the turn with unused energy (except no playable cards / energy-keeping relics)."""

import sqlite3

RULE = (
    "no_wasted_energy",
    "combat",
    0,
    "回合结束前能量必须打空（例外：全手牌不可打 / 能量保留类遗物）",
    "每次 end_turn 前重读 state 检查 energy：只要手牌里有可打的牌，就必须把能量用完——哪怕多出来的防御是溢出，也比空过强。"
    "唯一允许剩费：① 手牌全部不可打（状态牌/诅咒/费用不足）；② 特殊遗物或机制明确保留能量（冰激凌、能量保留、X 费结算后剩余）。"
    "执行口径：end_turn 前确认 energy == 0，或 playable 列表为空；两者都不满足就是低级失误，直接判负。",
    "用户 2026-10-25 明确批评：还有费不用的低级问题；除非特殊遗物，不要再出现。",
    "end_turn / WASTE_ENERGY / 能量",
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
