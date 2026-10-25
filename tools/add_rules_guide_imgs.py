# -*- coding: utf-8 -*-
"""Rules absorbed from the Ironclad deck guide images (2026-03-18)."""

import sqlite3

RULES = [
    (
        "offering_engine",
        "deck",
        1,
        "祭品=启动神卡：0 费 -6 血 +2 能 抽 3/5，一张就够",
        "祭品是过牌+回费二合一：0 费、-6 血、+2 能量、抽 3（敲完 5）。"
        "① 看到必抓，一张对血量压力不大，不用怕多抓；② 血线 >10 或本回合有甲/斩杀窗口时用，"
        "血 <10 先垫甲；③ 配绯红披风/放血等自伤联动，长战收益更高；④ 燃烧契约（1 费定向烧+抽 2/3）、"
        "剑柄打击（9/10 伤+抽 1/2）同属优质过牌，优先于随机烧牌。",
        "攻略图（2026-03-18）标注祭品为“神中神”，并强调一张就够；与我们过牌引擎轴一致。",
        "祭品 / 燃烧契约 / 剑柄打击 / Offering",
    ),
    (
        "impervious_and_slam_terminal",
        "deck",
        1,
        "岿然不动/壁垒+全身撞击：防御端终端",
        "岿然不动 2 费 30/40 甲，是战士最缺的单卡大甲，看到必抓（补第二条 15+ 防御红线）；"
        "壁垒让格挡不消失，配全身撞击（敲完 0 费，以 state 实测为准）就是终端：先叠 30-40 甲，"
        "再 0 费把甲转成伤害。武装+ 升级手牌所有牌，敲位优先级高。",
        "攻略图（2026-03-18）：岿然不动=优质防卡；壁垒+全身撞击可当终端；全身撞击敲完 0 费。",
        "岿然不动 / 壁垒 / 全身撞击 / 武装",
    ),
    (
        "exhaust_directed",
        "deck",
        1,
        "消耗要定向：随机烧牌是陷阱，契约终结是消耗轴 AOE 终端",
        "消耗牌优先“自选/定向烧”（坚毅+、燃烧契约），避开随机烧牌——随机烧会烧掉核心，"
        "攻略明确说“除非能 SL 防烧核心，否则别抓”。消耗堆 ≥3 时契约终结变 0 费 AOE 18/24，"
        "是消耗轴收尾；无惧疼痛（消耗得甲）把烧牌变成防御。",
        "攻略图（2026-03-18）：随机烧牌会烧关键牌；契约终结 3 张消耗即可 0 费 AOE。",
        "坚毅 / 燃烧契约 / 契约终结 / 无惧疼痛",
    ),
]


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
    con.commit()
    cur.execute("SELECT COUNT(*) FROM strategy_rules")
    print("total rules:", cur.fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
