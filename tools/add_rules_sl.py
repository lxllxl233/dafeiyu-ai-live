# -*- coding: utf-8 -*-
"""Rule: at most 3 save-loads per recording, with a playful line."""

import sqlite3

RULE = (
    "sl_max_three_per_recording",
    "meta",
    1,
    "每次录像最多 SL 3 次，且用俏皮话带过",
    "每次录像（一局）最多 SL 3 次；只在必死（全挡/斩杀/喝药三条都不成立）时用，"
    "重打必须换决策。SL 时配一句俏皮话，别唉声叹气、别解释机制，避免破坏观感。"
    "示例：“刚才那条是预告片，正片重来”“倒带一下，刚才卡带了”“这个 Boss 先按下不表，重开一镜”。"
    "三次用完就认，不再 SL。",
    "用户 2026-10-25 明确要求：每次录像最多 3 次 SL，操作时说俏皮话不影响观感。",
    "save_and_quit / continue_run / SL",
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
