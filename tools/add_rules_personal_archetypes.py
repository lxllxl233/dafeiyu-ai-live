# -*- coding: utf-8 -*-
"""Personal archetypes: exhaust + domination/vuln; Blood Wall is NOT core for this user."""

import sqlite3

NEW_RULES = [
    (
        "archetype_exhaust",
        "deck",
        1,
        "主轴·消耗流：腐化+黑暗之拥+无惧疼痛+契约终结",
        "消耗流成型：腐化（技能 0 费且打出即消耗）→ 黑暗之拥（每消耗 1 张抽 1）+ 无惧疼痛（每消耗得甲）"
        "→ 契约终结（消耗堆 ≥3 时 0 费 AOE 18/24）。燃料：燃烧契约（定向烧+抽 2/3）、坚毅（敲完定向）、祭品（0 费 -6 血 +2 能 抽 3/5）。"
        "核心抓位：腐化 > 黑暗之拥 > 无惧疼痛 > 契约终结；没有腐化时，靠燃烧契约/坚毅手动烧也能转。",
        "用户本人 A10 主玩消耗流；攻略图也把腐化/黑暗之拥/无惧疼痛/契约终结列为消耗轴核心。",
        "腐化 / 黑暗之拥 / 无惧疼痛 / 契约终结 / 燃烧契约 / 坚毅",
    ),
    (
        "archetype_domination_vuln",
        "deck",
        1,
        "主轴·主宰易伤流：易伤叠层转力量，巨像当防御",
        "主宰易伤流：主宰（给予 1 易伤，敌人每层易伤 → 获得力量）打底；痛击（2 易伤）、挑衅（1 易伤+6/7 甲）、"
        "上勾拳（13 伤+虚弱+易伤）负责叠层；巨像（易伤敌人伤害 -50%）当防御；易伤药水=爆发键。"
        "收尾用高力量多段/完美打击/重锤。注意：易伤会被人工制品吃掉，先剥制品再叠。",
        "用户本人 A10 主玩主宰易伤流；痛击/挑衅/上勾拳/巨像都在易伤轴上。",
        "主宰 / 痛击 / 挑衅 / 上勾拳 / 巨像 / 易伤药水",
    ),
    (
        "blood_wall_not_core",
        "deck",
        1,
        "血墙不是必抓（用户不玩）：15+ 甲走岿然/火焰屏障/巨像",
        "本账号不抓血墙，也不为它绕路/花 100 金。需要 15+ 单卡甲时优先：岿然不动（2 费 30/40）、"
        "火焰屏障（2 费 12/16 + 反伤）、巨像+易伤（伤害减半）、绯红披风（长战）。防御密度靠耸肩/坚毅/邪眼/岿然补，"
        "不要因为“攻略说血墙强”就破例。",
        "用户 2026-10-25 明确说：血墙个人从来不抓，主玩消耗流和主宰易伤流。",
        "Blood Wall / 岿然不动 / 火焰屏障 / 巨像 / 绯红披风",
    ),
]

UPDATES = {
    "boss_block_cap_act1": (
        "进 Boss 前硬指标：① 2 张单卡 15+ 防御（岿然不动 30/40、火焰屏障 12/16、巨像+易伤减半、壁垒+ 保留）；"
        "② 单回合最大格挡 ≥ Boss 最大单回合入伤；③ 达不到就继续找防御牌，不要先买中质量攻击。"
    ),
    "defense_density_act1": (
        "进 Act1 Boss 前确保 6 张以上防御，其中至少 2 张 8+（岿然不动/火焰屏障/巨像/耸肩+/坚毅）。"
    ),
    "ringing_turn_single_card": (
        "① 牌组带一张单卡 15+ 的防御（岿然不动/火焰屏障/巨像）；② 或在 Boss 非攻击回合提前铺壁垒/保留格挡；"
        "③ 计算下一手是不是 Ringing，提前把这唯一一张牌留给大甲而不是输出。"
    ),
}


def main() -> None:
    con = sqlite3.connect("db/sts2.db")
    cur = con.cursor()
    for rid, text in UPDATES.items():
        row = cur.execute("select rule from strategy_rules where id=?", (rid,)).fetchone()
        if not row:
            print("missing", rid)
            continue
        # 只替换「防御牌列表」那一段：按已知旧片段替换
        old_frags = [
            "（岿然不动 30/40、血墙+ 20、邪眼+ 烧牌 22、壁垒+ 保留）",
            "（血墙/耸肩+/邪眼/坚毅）",
            "（岿然不动/血墙+/邪眼+）",
        ]
        new = row[0]
        for frag in old_frags:
            if frag in new:
                if "30/40" in frag:
                    new = new.replace(frag, "（岿然不动 30/40、火焰屏障 12/16、巨像+易伤减半、壁垒+ 保留）")
                elif "耸肩+" in frag:
                    new = new.replace(frag, "（岿然不动/火焰屏障/巨像/耸肩+/坚毅）")
                else:
                    new = new.replace(frag, "（岿然不动/火焰屏障/巨像）")
        cur.execute("update strategy_rules set rule=? where id=?", (new, rid))
        print("updated", rid)

    for rid, phase, priority, title, rule, why, cards in NEW_RULES:
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
