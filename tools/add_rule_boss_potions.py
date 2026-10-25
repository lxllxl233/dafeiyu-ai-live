# -*- coding: utf-8 -*-
"""用户口径（2026-10-26）：Boss 战不省药水。"""
import io
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

RULES = [
    ("boss", "P0", "Boss 战不省药水",
     "进 Boss 那一刻起药水就是消耗品：每回合问「现在开这瓶，能不能把这回合变成 0 伤 / 多打 20+ 血 / 直接斩杀」，"
     "能就开，不问「留给下一幕」。Boss 打完还剩 >=1 瓶 = 决策失误。省药只在道中。",
     "用户口径 2026-10-26；Boss 战把药水带进坟墓是最常见的白给"),
]

c = sqlite3.connect("db/sts2.db")
inserted = 0
for phase, priority, title, rule, why in RULES:
    if c.execute("select 1 from strategy_rules where title=?", (title,)).fetchone():
        continue
    c.execute(
        "insert into strategy_rules(id, phase, priority, title, rule, why, cards) values(?,?,?,?,?,?,'')",
        (f"boss_potion_{inserted}", phase, priority, title, rule, why),
    )
    inserted += 1
c.commit()
print("inserted:", inserted, "total:", c.execute("select count(*) from strategy_rules").fetchone()[0])
