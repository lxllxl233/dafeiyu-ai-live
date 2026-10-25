# -*- coding: utf-8 -*-
"""Add the pre-card combat audit rule to db/sts2.db (idempotent)."""

import sqlite3

RULE = (
    "pre_card_state_check",
    "combat",
    1,
    "出牌前按「我方状态→敌方状态→数量→NxM→EFF→升级版」六项过一遍",
    "每张牌打出前依次读：① 我方状态（PLAYER POWERS 的力量/虚弱/脆弱/易伤，虚弱已让实时 Damage 降 25%，"
    "脆弱让格挡降 25%）；② 敌方状态（每只的 block/powers：荆棘=按次反伤、胆小=每回合首击加盾 6/7、"
    "覆甲/金属化=每回合加甲、力量=它打得更疼、易伤=我打它 ×1.5）；③ 敌人数量（多怪先加总入伤，"
    "再决定单体还是 AOE）；④ 多段意图与多段牌（ANCHOR dmg=8x4=32 多段4次 / EFF 8x2，按较大值挡、按段数算伤）；"
    "⑤ 直接读 EFF 选目标与斩杀（★斩）；⑥ COPIES/升级版（同名不同级打升级版，--card 指错会报 WRONG_COPY）。",
    "前 29 局反复踩：先打普通刀再挂易伤（等于自砍 1/3 DPS）、把 8x4 当 8 挡、"
    "拆卸（易伤时打两次）按 8 伤算成 24 伤、打击 vs 打击+ 打错（run24 因此白掉 16 血）。"
    "把这些检查做成 state 的固定输出后，就不用靠记性。",
    "所有战斗 / Dismantle / Strike vs Strike+",
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
