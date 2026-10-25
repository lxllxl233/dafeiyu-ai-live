# -*- coding: utf-8 -*-
"""Absorb slaythespire2.net ironclad build tier list (v0.107.1)."""

import sqlite3

NEW_RULES = [
    (
        "build_tier_0107",
        "deck",
        1,
        "0.107.1 流派强度：S 消耗 > A 力量/格挡撞击 > B 易伤/自伤",
        "0.107.1 铁甲战士流派评级（成形速度/上限/高进阶稳定度）："
        "S 消耗流（无惧疼痛+黑暗之拥+燃烧契约/坚毅+恶魔之焰/契约终结）；"
        "A 力量流（燃烧/恶魔形态+双重打击/飞剑/旋风斩）≈ A 格挡/全身撞击流（壁垒+全身撞击+势不可当）；"
        "B 易伤流（好易伤源少、凶恶怕人工制品）/ 自伤流（缺回复，战损难避免）。"
        "选轴优先级：先看发牌往哪边靠；消耗流组件齐就锁 S，缺能力牌时力量/格挡撞击更稳。"
        "易伤流虽 B，但主宰可当力量源塞进力量流。",
        "slaythespire2.net 0.107.1 流派评级；与用户主玩消耗/主宰易伤一致。",
        "消耗流 / 力量流 / 格挡撞击 / 易伤流",
    ),
    (
        "strength_build_core",
        "deck",
        1,
        "力量流：燃烧/恶魔形态 + 多段攻击，主宰可当力量源",
        "力量流：前期拿便宜力量（燃烧 1 费 +2/3、预备打击 7 伤+本回合 3 力、与我一战！5×2+3 力、主宰易伤转力量、烙印 0 费 -1 血烧 1 +1 力），"
        "配多段攻击（双重打击 5×2、飞剑回旋镖 3×3、痛殴 4×2 吞攻击加伤、旋风斩 X 费 AOE）；Boss 长线用恶魔形态（3 费，0.107.1 每回合 +2）。"
        "缺点：前期力量多为临时、恶魔形态 3 费；一直跳格挡会脆。",
        "slaythespire2.net 力量流核心/辅助卡表（0.107.1）。",
        "燃烧 / 恶魔形态 / 双重打击 / 飞剑回旋镖 / 旋风斩 / 主宰 / 预备打击",
    ),
    (
        "block_slam_build",
        "deck",
        1,
        "格挡/全身撞击流：壁垒 + 全身撞击 + 势不可当",
        "格挡流：壁垒（格挡不消失）把甲叠起来，全身撞击（伤害=当前格挡，敲完 0 费）转伤害，势不可当（2 费能力：每获得格挡对随机敌人 6 伤）"
        "把叠甲过程也变成输出；辅助岿然不动 30/40、火焰屏障、耸肩、坚毅、重振精神。"
        "A 级原因：成形稳定、长线强；缺点是怕“格挡失效/穿透”类机制，且需要先垫甲再出伤。",
        "slaythespire2.net 格挡/全身撞击流（A 级，0.107.1）。",
        "壁垒 / 全身撞击 / 势不可当 / 岿然不动 / 火焰屏障",
    ),
]

UPDATES = {
    "archetype_exhaust": (
        "消耗流成型：腐化（技能 0 费且打出即消耗）→ 黑暗之拥（每消耗 1 张抽 1）+ 无惧疼痛（每消耗得 3 甲）"
        "→ 收尾恶魔之焰（2 费消耗所有手牌，每张 7 伤）/ 契约终结（消耗堆 ≥3，0 费 AOE）。"
        "燃料：燃烧契约（定向烧+抽 2/3）、坚毅（敲完定向）、重振精神（消耗所有非攻击，每张 5 甲）、添柴（消耗整手随机补）、祭品（0 费 -6 血 +2 能 抽 3/5）。"
        "辅助：被遗忘的仪式（0.107.1 需本回合先消耗过才给 3 能）、灰烬打击（消耗堆每张 +3 伤）、战鼓、彼岸咆哮。"
        "关键遗物：卡戎之灰（每次消耗全体 3 伤）、金纸（每消耗 5 张抽 1）、燃烧木棍、遗忘之魂。"
        "腐化只能先古之民拿，别把整局押它。"
    ),
    "loop_engine_sword_shrug": (
        "塔2 战士“缺抽不缺费”，成型最快的是循环解：**2 张抽牌 + 1 张加费**互相抽打。"
        "抽牌池：剑柄打击（9/10 伤+抽 1/2）、耸肩无视（8/11 甲+抽 1）、劫掠（6 伤+抽到非攻击）、怨恨（5 伤，掉血打 2 次）、"
        "燃烧契约（1 费定向烧+抽 2/3）、凶恶（给易伤抽 1）、杂耍（复制第三张攻击）。加费池：放血（0 费 -3 血 +2/3）、"
        "被遗忘的仪式（1 费 +3 能，**0.107.1 需本回合先消耗过**）。注意：跃跃欲试在 0.107.1 是“手牌每张攻击给能量”，测试版才是格挡牌；"
        "循环怕人工制品/脆弱怪，别为了循环放弃短线。"
    ),
    "archetype_domination_vuln": (
        "主宰易伤流：主宰（1 费：给 1 易伤，敌人每层易伤→1 力量，消耗；0.107.1 为罕见）+ 凶恶（每次给易伤抽 1）+ 残酷（易伤敌人额外受 25%）"
        "+ 欺凌（4 伤+每层易伤 +2）；易伤源：痛击（2 层）、挑衅（1 层+6/7 甲）、上勾拳（1 层+虚弱）、战栗（1 层+7 甲）、易伤药水。"
        "防御：巨像（易伤敌人伤害 -50%）、岿然不动、火焰屏障。打法：先叠 3–5 层易伤 → 主宰转力量 → 欺凌/高力量多段斩杀；人工制品先剥。"
        "注意：易伤流整体 B 级（好易伤源少、凶恶怕人工制品），主宰更适合当力量流的力量源。"
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
