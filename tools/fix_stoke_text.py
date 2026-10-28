# -*- coding: utf-8 -*-
"""Fix Stoke wording + add 'live card text first' rule (local db was stale)."""

import re
import sqlite3

SKILL = r"games/slay-the-spire-2/decision/knowledge/ironclad/OS.md"

with open(SKILL, encoding="utf-8") as f:
    s = f.read()

s = s.replace(
    "11.5 垃圾手先打添柴（Stoke）：诅咒/状态 ≥2 张就烧掉换牌，别硬用垃圾手打回合",
    "11.5 垃圾手先打添柴（Stoke）：1 费消耗整手牌，每消耗 1 张随机加入 1 张牌（添柴+ 加升级牌）——"
    "手里有 ≥2 张诅咒/状态就烧掉换牌",
)
s = s.replace(
    "**添柴/消耗手牌 = 过滤**：手里有添柴（1 费，消耗整手牌、每张抽 1）且手上有 ≥2 张诅咒/状态/废牌时，先打它——烧垃圾换新牌是最便宜的深度过滤，别因为「怕消耗」把它留到最后。",
    "**添柴（Stoke）= 垃圾手过滤器**：1 费消耗整手牌，每消耗 1 张**随机加入 1 张牌**（添柴+ 加的是升级牌）。"
    "手里有 ≥2 张诅咒/状态/废牌时先打它：烧垃圾换随机新牌，是最便宜的手牌重置；别因为「怕消耗」把它留到最后。",
)
s = s.replace(
    "| 打完牌不重读 | 挂易伤/力量后立刻重读 EFF（斩杀线会变） |",
    "| 打完牌不重读 | 挂易伤/力量后立刻重读 EFF（斩杀线会变） |\n"
    "| 牌面文字和本地库不一致 | **以游戏实时 `resolved_rules_text` 为准**；本地 cards.json 可能落后一个版本（例：添柴 改成「消耗整手牌→每张随机加 1 张牌」） |",
)

with open(SKILL, "w", encoding="utf-8") as f:
    f.write(s)

RULES = [
    (
        "stoke_burn_junk",
        "combat",
        1,
        "添柴（Stoke）= 垃圾手过滤器：烧掉整手，每张随机补一张",
        "添柴 1 费：**消耗整手牌，每消耗 1 张随机加入 1 张牌**（添柴+ 加入的是升级牌）。"
        "手里有 ≥2 张诅咒/状态/废牌时先打它：等于把垃圾换成随机新牌，是处理「手牌被塞满」最便宜的手段。"
        "不要因为「怕消耗」把它留到最后——核心牌本来就该先打出去，它烧不到核心。",
        "第 32 局两只史莱姆战：连续 3 个回合手里有添柴 + 2~3 张 Slimed/Guilty，我一次没打，"
        "战斗拖到 7 回合、白挨两轮攻击（用户当场叫停并纠正了牌面效果）。",
        "Stoke / 坚毅 / 所有消耗手牌的牌",
    ),
    (
        "live_text_first",
        "invariant",
        1,
        "牌面以游戏实时文本为准，本地库只做兜底",
        "`state` 里每张牌会优先显示游戏返回的 `resolved_rules_text`；本地 `tools/data/eng/cards.json` 与 "
        "`db/sts2.db` 可能是旧版本（改过效果的牌会写错）。打牌前若觉得数值/效果对不上，先信实时文本，"
        "并回头更新本地库。",
        "添柴 STOKE 的效果在新版本改成「消耗整手牌、每张随机加入 1 张牌」，本地英文库还是旧文案「抽 1 张」，"
        "导致第 32 局误判（用户指出）。",
        "所有卡牌",
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
