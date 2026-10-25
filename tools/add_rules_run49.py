# -*- coding: utf-8 -*-
"""Rules from run 49 (user correction): small hits must be blocked; potions are a resource.

Two changes:
  1. NEW  small_hit_always_block — 入伤 ≤10% 最大生命必须挡满（打 7 也挡）。
  2. NEW  potion_use_or_lose   — 药水必须规划，不许整局吃灰。
  3. FIX  r48_potion_no_hp_budget — 旧结论「药水无效、按 0 药水算」被用户否决，改写。
"""

import sqlite3

RULES = [
    (
        "small_hit_always_block",
        "combat",
        0,
        "小伤害必挡：入伤 ≤10% 最大生命一律挡满",
        "入伤 ≤10% 最大生命（80 血 ≤8）时先挡满再输出，宁可少打一张刀；要吃伤必须写清算式："
        "（提前一回合结束省下的入伤）>（这回合吃的血）。入伤 9–14 也先吃满 BLOCK_POOL，差额才是换血额度。",
        "第 49 局泥浆纺锤意图 7，手里 2 防御 3 能量，却 Inflame + 打击×2 硬吃 7；"
        "正解「打击 + 打击 + 防御」= 12 伤 + 挡 5、净掉 2，只少 6 点伤害。用户原话："
        "「他打 7 的时候你可以先俩张打再起一次防，但是你硬吃伤害。」",
        "DEFEND_IRONCLAD / BLOCK_POOL",
    ),
    (
        "potion_use_or_lose",
        "combat",
        1,
        "药水是资源：四问决定交还是留，不许吃灰",
        "每次进战斗先看 POTIONS 行，四问：解什么｜槽满还留不留｜这回合会不会被打穿｜留给下一场更值吗。"
        "交：挡不住且净掉 ≥10% 最大生命｜斩杀差 5–15｜Boss 大攻击回合｜精英 T1 血线 <60%。"
        "留：T1 满血、入伤 ≤10%、下一格营火、Boss 前一层。槽满再捡药 = 先喝或先丢。",
        "第 49 局两个槽从 1 层空到 9 层，一次 use_potion 都没发——不是 API 失效，是根本没把药水当资源；"
        "用户要求「不会用药水要修」。旧规则 r48_potion_no_hp_budget 把「pending」误读成「药水无用」。",
        "use_potion / discard_potion",
    ),
]

OLD_POTION = (
    "r48_potion_no_hp_budget",
    "药水计入血预算（v10 改写）",
    "药水是挡伤/斩杀的第三条路：入伤 ≥ HP 时按「全挡 / 斩杀 / 喝药」三选一；"
    "pending 只重试 1 次并重读 is_queued，别连点，也别再把药水当 0。",
    "第 48 局把 use_potion 全程 pending 误判成「药水无效」→ 第 49 局整局 0 药水。"
    "用户 2026-10-25 指正后改为「必须规划」，细则见 references/potions.md。",
)


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

    rid, title, rule, why = OLD_POTION
    cur.execute("SELECT 1 FROM strategy_rules WHERE id = ?", (rid,))
    if cur.fetchone():
        cur.execute(
            "UPDATE strategy_rules SET title=?, rule=?, why=? WHERE id=?",
            (title, rule, why, rid),
        )
        print("rewrote", rid)
    else:
        print("missing", rid)

    con.commit()
    cur.execute("SELECT COUNT(*) FROM strategy_rules")
    print("total rules:", cur.fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
