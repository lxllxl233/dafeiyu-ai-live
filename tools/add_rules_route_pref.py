# -*- coding: utf-8 -*-
"""Rule: route planning prefers campfires (smithing) and avoids unnecessary elites."""

import sqlite3

RULE = (
    "route_pref_campfire_no_elite",
    "route",
    1,
    "路线：多营火敲牌，非必要不精英",
    "规划路线时：① 同分优先营火多的线（≥3 个营火额外加分），营火默认**锻造**——先敲血墙/全身撞击/武装/"
    "岿然不动这类每回合生效的牌；只有 HP<50% 或 Boss 前血线 <70% 才睡。"
    "② 精英非必要不打：三闸全过也只是备选，只有『顺路 + 血 ≥70% + 遗物确实质变 + 有药』才进；"
    "血 <60% 或要绕路/战损换精英一律跳过。③ 低血优先级：营火 > 问号 > 商店 > 普通怪 > 精英。",
    "用户 2026-10-25 明确要求：规划路线多篝火敲牌，非必要不精英。",
    "route / RestSite / Elite",
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
