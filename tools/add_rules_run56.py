# -*- coding: utf-8 -*-
"""Run56 lessons -> strategy_rules."""
import io
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

RULES = [
    ("route", "P0", "金币只有一个用途：删牌",
     "牌组已有诅咒时，任何「交出全部金币换牌面变化」的事件一律拒绝：诅咒大概率再洗成诅咒，"
     "而且金币归零后商店删牌再也不可能。",
     "56 局第 3 层 109 金换 2 变形 -> 受伤变债务，全程没能删牌，两诅咒陪到底"),
    ("route", "P0", "路线枚举要标「唯一出口=精英」",
     "选节点前先看下一层可选集：如果唯一出口是精英，就要按「这条线要求当前血线 >=60%」评估，"
     "血闸挡不住强制精英。",
     "56 局第 12、14 层都是唯一出口精英，53%/20% 血被迫进"),
    ("meta", "P1", "血换 relic 先答「Act1 内回不回本」",
     "成长型 relic（+1 最大生命/场、+1 金币/场）在 Act1 最多回 5-8 点，不值 14 点现血；"
     "血量是唯一硬通货。",
     "56 局第 6 层 -14 血换天选芝士，血线掉到 44% 出门"),
    ("build", "P1", "Act1 第 10 层起牌组 18 张封顶",
     "18 张以后只拿质变牌（AOE/引擎/收尾），普通牌一律跳过；20 张 + 2 诅咒 = 连续空手。",
     "56 局 20 张牌，打寄生蛙时两回合只摸到感染+债务"),
    ("combat", "P1", "Gremlin Horn（地精之角）：击杀回 1 费抽 1 张",
     "拿角之后，多怪战要按「杀一只就多点一下」排序：先杀最便宜的，用回的费用接下一刀，形成连杀链。",
     "56 局寄生蛙召唤后连杀两只扭动虫，靠角把 4 只压到 1 只"),
]

c = sqlite3.connect("db/sts2.db")
inserted = 0
for phase, priority, title, rule, why in RULES:
    exists = c.execute("select 1 from strategy_rules where title=?", (title,)).fetchone()
    if exists:
        continue
    c.execute(
        "insert into strategy_rules(id, phase, priority, title, rule, why, cards) values(?,?,?,?,?,?,'')",
        (f"run56_{inserted}", phase, priority, title, rule, why),
    )
    inserted += 1
c.commit()
print("inserted:", inserted, "total:", c.execute("select count(*) from strategy_rules").fetchone()[0])
