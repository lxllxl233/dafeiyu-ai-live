# -*- coding: utf-8 -*-
"""Absorb Zhihu/17173 high-winrate guide: loop engine, deletion by draft, short-fight plan."""

import sqlite3

NEW_RULES = [
    (
        "loop_engine_sword_shrug",
        "deck",
        1,
        "循环解（剑耸）：2 抽牌 + 1 加费，抽牌优先",
        "塔2 战士“缺抽不缺费”，成型最快的是循环解：**2 张抽牌 + 1 张加费**互相抽打。"
        "抽牌池：剑柄打击（9/10 伤+抽 1/2）、耸肩无视（8/11 甲+抽 1）、劫掠（6 伤+抽到非攻击）、怨恨（5 伤，掉血打 2 次）、"
        "燃烧契约（1 费定向烧+抽 2/3）、凶恶（给易伤抽 1）、杂耍（复制第三张攻击）。加费池：放血（0 费 -3 血 +2/3）、被遗忘的仪式（1 费 +3 能）。"
        "注意：跃跃欲试是 3 费 15 甲+力量加成的格挡牌，不是加费牌。循环怕人工制品/脆弱怪，别为了循环放弃短线。",
        "知乎/17173 高胜率测评：战士现在几乎只能走循环解，两抽牌+一加费；缺抽不缺费。",
        "剑柄打击 / 耸肩无视 / 劫掠 / 怨恨 / 燃烧契约 / 放血 / 被遗忘的仪式",
    ),
    (
        "short_fight_aggro_defense",
        "combat",
        1,
        "短线只选攻杀或防杀，不要半攻半防",
        "战士短线弱、长线强：道中短线要么攻杀速杀，要么防杀磨过，不要半攻半防。"
        "攻杀卡：愤怒、突破、完美打击、旋风斩、拆卸（对易伤打两次）、无情猛攻、重锤、狱火、杂耍。"
        "防杀卡：狂怒（0 费，本回合每打一张攻击获得格挡，输出转防御）、武装/耸肩/坚毅、挑衅/邪眼/火焰屏障/重振、无惧/巨像/绯红披风。"
        "对人工制品先剥制品再易伤；被上脆弱时优先留甲。",
        "知乎/17173 高胜率测评：战士短线弱、长线强，攻杀/防杀二选一；狂怒是输出转防御的端口。",
        "狂怒 / 拆卸 / 无情猛攻 / 旋风斩 / 火焰屏障 / 巨像",
    ),
]

UPDATES = {
    "removal_curve": (
        "删牌不要无脑删打/删防：先买卡/遗物，钱没处花再删；看发牌——重振/地狱狂徒先来就删 Strike，"
        "狂怒/劫掠先来就删 Defend；防御轴保留优质防御。"
    ),
    "archetype_domination_vuln": (
        "主宰易伤流：主宰（敌人每层易伤→1 力量，消耗）+ 凶恶（每次给易伤抽 1）+ 残酷（易伤敌人额外受 25%）"
        "+ 欺凌（4 伤+每层易伤 +2）；易伤源：痛击（2 层）、挑衅（1 层+6/7 甲）、上勾拳（1 层+虚弱）、战栗（1 层+7 甲）、易伤药水。"
        "防御：巨像（易伤敌人伤害 -50%）、岿然不动、火焰屏障。打法：先叠 3–5 层易伤 → 主宰转力量 → 欺凌/高力量多段斩杀；人工制品先剥。"
    ),
}


def main() -> None:
    con = sqlite3.connect("db/sts2.db")
    cur = con.cursor()
    for rid, text in UPDATES.items():
        if cur.execute("select 1 from strategy_rules where id=?", (rid,)).fetchone():
            cur.execute("update strategy_rules set rule=? where id=?", (text, rid))
            print("updated", rid)
        else:
            print("missing", rid)
    for rid, phase, priority, title, rule, why, cards in NEW_RULES:
        if cur.execute("select 1 from strategy_rules where id=?", (rid,)).fetchone():
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
