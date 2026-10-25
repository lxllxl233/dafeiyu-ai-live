# -*- coding: utf-8 -*-
"""Rule: waterfall giant steam explosion timing."""

import sqlite3

RULE = (
    "waterfall_steam_explosion",
    "combat",
    0,
    "瀑布巨兽蒸汽喷发：击杀后的下一回合结束才炸，别提前交应急按钮",
    "瀑布巨兽（Waterfall Giant）死亡后不会立刻结束战斗：它进入 Stun，并在**你下一回合结束时**触发「蒸汽喷发」，"
    "伤害随场次递增（本局 20→23→26→29→32→35→38→41）。要点："
    "① 爆炸伤害**吃格挡**，但发生在击杀后的下一回合末，所以要把大甲（应急按钮 30 / 岿然 30-40 / 耸肩等）留到**爆炸回合**，"
    "不要在击杀回合就把应急按钮用掉——它会让你接下来 2 回合无法从卡牌获得格挡，直接裸接 41；"
    "② 击杀前算「爆炸值 - 可留格挡」：HP 必须 > 爆炸值 - 爆炸回合能铺的甲；"
    "③ 若爆炸回合无法铺够甲，就先别杀 Boss（保持它低血、继续叠甲/回血）。",
    "第 43 局教训：Boss 20 血时用飞剑收掉，击杀回合误开应急按钮，爆炸回合卡牌格挡被封，33 血裸接 41 身亡。",
    "瀑布巨兽 / 蒸汽喷发 / 应急按钮",
)


def main() -> None:
    con = sqlite3.connect("db/sts2.db")
    cur = con.cursor()
    rid, phase, priority, title, rule, why, cards = RULE
    if cur.execute("select 1 from strategy_rules where id=?", (rid,)).fetchone():
        cur.execute(
            "update strategy_rules set phase=?, priority=?, title=?, rule=?, why=?, cards=? where id=?",
            (phase, priority, title, rule, why, cards, rid),
        )
        print("updated", rid)
    else:
        cur.execute(
            "insert into strategy_rules (id, phase, priority, title, rule, why, cards) values (?,?,?,?,?,?,?)",
            (rid, phase, priority, title, rule, why, cards),
        )
        print("added", rid)
    con.commit()
    cur.execute("select count(*) from strategy_rules")
    print("total rules:", cur.fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
