#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate references/enemies.md v3: encounters grouped by act, one line each.

Data: tools/data/eng/encounters.json (act + composition) + db/sts2.db (HP/passives)
      + BRIEF (手写打法，来自 run1–33 复盘 / DB 被动 / 实战校正).

Usage: python tools/build_enemies_v3.py [--check]
       --check 只检查缺 BRIEF 的遭遇，不写文件。
"""
import io
import json
import sqlite3
import sys

OUT = ".dsh/skills/sts2-ironclad-ironclad/references/enemies.md"

NAME_FIX = {
    "TEST_SUBJECT": "实验体",
    "DOOR": "门",
    "DOORMAKER": "门扉缔造者",
    "DECIMILLIPEDE_SEGMENT_FRONT": "千足虫",
    "DECIMILLIPEDE_SEGMENT_MIDDLE": "千足虫",
    "DECIMILLIPEDE_SEGMENT_BACK": "千足虫",
    "TOUGH_EGG": "结实的卵",
    "ARCHITECT": "建筑师",
    "FAKE_MERCHANT_MONSTER": "商人？？？",
}

# 编成显示需要手工修正的（多段体 / 无血量 / 事件战）
COMP_FIX = {
    "DECIMILLIPEDE_ELITE": "3 段（每段 40–46 血）",
    "TEST_SUBJECT_BOSS": "复活型，按当前阶段血量算",
    "MYSTERIOUS_KNIGHT_EVENT_ENCOUNTER": "血量按事件阶段",
    "BATTLEWORN_DUMMY_EVENT_ENCOUNTER": "V1 75 / V2 150 / V3 300，依次上场",
    "DENSE_VEGETATION_EVENT_ENCOUNTER": "4 只，17–21 血/只",
}

# A10 实测血量（只写确认过的；其余按基础值 +5~10% 估）
A10_HP = {
    "CEREMONIAL_BEAST": "262",
    "KIN_PRIEST": "199",
    "WATERFALL_GIANT": "260",
    "TERROR_EEL": "150",
    "SKULKING_COLONY": "80",
    "PHANTASMAL_GARDENER": "27–32",
}

# 遭遇标题（默认从编成自动拼，多怪/翻译不顺手的手工命名）
ENC_FIX = {
    "CEREMONIAL_BEAST_BOSS": "仪式兽",
    "THE_KIN_BOSS": "同族神官 + 信徒",
    "VANTOM_BOSS": "墨影幻灵",
    "BYGONE_EFFIGY_ELITE": "旧日雕像",
    "BYRDONIS_ELITE": "多尼斯异鸟",
    "PHROG_PARASITE_ELITE": "异蛙寄生虫 + 扭动虫",
    "NIBBITS_WEAK": "小啃兽",
    "NIBBITS_NORMAL": "小啃兽 ×2",
    "CUBEX_CONSTRUCT_NORMAL": "立柱构造体",
    "FOGMOG_NORMAL": "雾菇 + 利齿之眼",
    "FUZZY_WURM_CRAWLER_WEAK": "毛绒伏地虫",
    "SLIMES_WEAK": "史莱姆群",
    "INKLETS_NORMAL": "墨宝群",
    "MAWLER_NORMAL": "蛮兽",
    "OVERGROWTH_CRAWLERS": "毛绒虫 + 缩小甲虫",
    "SNAPPING_JAXFRUIT_NORMAL": "飞蝇菌子 + 贾克斯果",
    "RUBY_RAIDERS_NORMAL": "劫掠者小队（5 只）",
    "SHRINKER_BEETLE_WEAK": "缩小甲虫",
    "FLYCONID_NORMAL": "飞蝇菌子 + 双中史莱姆",
    "SLITHERING_STRANGLER_NORMAL": "蛇行扼杀者 + 史莱姆群",
    "SLIMES_NORMAL": "史莱姆群",
    "VINE_SHAMBLER_NORMAL": "藤蔓蹒跚者",
    "PHANTASMAL_GARDENERS_ELITE": "花园幽灵鳗（4 只）",
    "SKULKING_COLONY_ELITE": "鬼祟珊瑚群",
    "TERROR_EEL_ELITE": "骇鳗",
    "TWO_TAILED_RATS_NORMAL": "双尾鼠",
    "CULTISTS_NORMAL": "钙化 + 潮湿邪教徒",
    "LIVING_FOG_NORMAL": "活雾 + 气态炸弹",
    "TOADPOLES_WEAK": "蟾蜍蝌蚪 ×2",
    "TOADPOLES_NORMAL": "邪教徒 + 蟾蜍蝌蚪",
    "CORPSE_SLUGS_NORMAL": "噬尸蛞蝓 ×2–3",
    "CORPSE_SLUGS_WEAK": "噬尸蛞蝓",
    "KAISER_CRAB_BOSS": "碾碎爪 + 火箭",
    "BOWLBUGS_NORMAL": "盛碗虫群",
    "BOWLBUGS_WEAK": "盛碗虫群",
    "TUNNELER_NORMAL": "地道虫 + 碗虫",
    "EXOSKELETONS_NORMAL": "外骨骼虫",
    "EXOSKELETONS_WEAK": "外骨骼虫",
    "SLUMBERING_BEETLE_NORMAL": "熟睡甲虫 + 碗虫",
    "OVICOPTER_NORMAL": "直飞产卵虫 + 卵",
    "DECIMILLIPEDE_ELITE": "千足虫（3 段）",
    "CHOMPERS_NORMAL": "啃咬机",
    "QUEEN_BOSS": "女王 + 火炬头",
    "BATTLEWORN_DUMMY_EVENT_ENCOUNTER": "陪练木桩 V1–V3",
    "TEST_SUBJECT_BOSS": "实验体",
    "DOORMAKER_BOSS": "门扉缔造者（旧版）",
    "KNIGHTS_ELITE": "骑士团",
    "THE_LOST_AND_FORGOTTEN_NORMAL": "遗忘之物 + 失落之物",
    "SCROLLS_OF_BITING_NORMAL": "咬人卷轴",
    "SCROLLS_OF_BITING_WEAK": "咬人卷轴",
    "CONSTRUCT_MENAGERIE_NORMAL": "立柱 + 拳击构装体",
    "TURRET_OPERATOR_WEAK": "活体盾 + 高塔炮手",
}

# 威胁：Boss/精英一律 🔥；普通怪只标真正会带走一局的
THREAT = {
    "FUZZY_WURM_CRAWLER_WEAK": "🔥", "RUBY_RAIDERS_NORMAL": "🔥",
    "TWO_TAILED_RATS_NORMAL": "🔥", "MAWLER_NORMAL": "🔥",
    "SPINY_TOAD_NORMAL": "🔥", "HUNTER_KILLER_NORMAL": "🔥",
    "GLOBE_HEAD_NORMAL": "🔥", "DEVOTED_SCULPTOR_WEAK": "🔥",
    "VINE_SHAMBLER_NORMAL": "⚠️", "SLITHERING_STRANGLER_NORMAL": "⚠️",
    "SEAPUNK_WEAK": "⚠️", "TOADPOLES_WEAK": "⚠️", "TOADPOLES_NORMAL": "⚠️",
    "CORPSE_SLUGS_NORMAL": "⚠️", "CORPSE_SLUGS_WEAK": "⚠️", "CULTISTS_NORMAL": "⚠️",
    "LIVING_FOG_NORMAL": "⚠️", "SNAPPING_JAXFRUIT_NORMAL": "⚠️",
    "SHRINKER_BEETLE_WEAK": "⚠️", "FOSSIL_STALKER_NORMAL": "⚠️",
    "HAUNTED_SHIP_NORMAL": "⚠️", "SEWER_CLAM_NORMAL": "⚠️",
    "SLUDGE_SPINNER_WEAK": "⚠️", "NIBBITS_WEAK": "⚠️", "NIBBITS_NORMAL": "⚠️",
    "INKLETS_NORMAL": "⚠️", "LOUSE_PROGENITOR_NORMAL": "⚠️",
    "EXOSKELETONS_NORMAL": "⚠️", "OVICOPTER_NORMAL": "⚠️",
    "THE_OBSCURA_NORMAL": "⚠️", "TUNNELER_NORMAL": "⚠️", "TUNNELER_WEAK": "⚠️",
    "BOWLBUGS_NORMAL": "⚠️", "BOWLBUGS_WEAK": "⚠️",
    "SLUMBERING_BEETLE_NORMAL": "⚠️", "THIEVING_HOPPER_WEAK": "⚠️",
    "MYTES_NORMAL": "⚠️", "CHOMPERS_NORMAL": "⚠️", "FROG_KNIGHT_NORMAL": "⚠️",
    "OWL_MAGISTRATE_NORMAL": "⚠️", "SLIMED_BERSERKER_NORMAL": "⚠️",
    "TURRET_OPERATOR_WEAK": "⚠️", "AXEBOTS_NORMAL": "⚠️",
    "FABRICATOR_NORMAL": "⚠️", "THE_LOST_AND_FORGOTTEN_NORMAL": "⚠️",
}

# 每个遭遇一句话：最危险的动作 / 先做什么 / 别犯什么错
BRIEF = {
    # ---------- Act 1 · Overgrowth ----------
    "CEREMONIAL_BEAST_BOSS": "跺地→横冲直撞 18/20+2 力→踩踏 15/17→碾碎 17/19+3 力→野兽咆哮（下回合限 1 张）｜首次 ≤150 血眩晕，把爆发留给晕眩窗口；Ringing 回合只打能活/能杀的那一张",
    "THE_KIN_BOSS": "神官 8+debuff→3×3，信徒 +3 力后 5 / 2×2｜有爆发速杀神官；没有就先清一只信徒（爪牙 14+/回合），密林死亡率最高的 Boss",
    "VANTOM_BOSS": "滑溜 9（前 9 次受击只掉 1）+ 塞伤口｜用多段破滑溜，别把大牌留手里",
    "BYGONE_EFFIGY_ELITE": "睡 2 回合→苏醒 +10 力→斩击 15/17；缓慢（你每打一张牌它受伤 +10%）｜先垫防御/能力再上大伤害；HP<50% 别进",
    "BYRDONIS_ELITE": "领地意识（回合末 +1 力）+ 多段｜前 3 回合压死，别拖到 25+",
    "PHROG_PARASITE_ELITE": "甩动 4×4 + 每回合塞感染；死亡召唤 4 只扭动虫｜2–3 回合内打死或备好 AOE；先杀当回合攻击的虫可隔回合零伤",
    "NIBBITS_WEAK": "力量 2/3 每回合成长｜尽快杀，别让它叠起来",
    "NIBBITS_NORMAL": "两只同时成长｜先杀一只、挡满再打，别分散",
    "CUBEX_CONSTRUCT_NORMAL": "人工制品 1｜蓄能后重复轰击；debuff 会被挡一次，直接堆伤害",
    "FOGMOG_NORMAL": "雾菇是召唤者｜先杀本体，爪牙随本体消失；利齿之眼死会复活一次",
    "FUZZY_WURM_CRAWLER_WEAK": "酸液 4×2 →「吸入」+7 力，之后暴涨｜3 回合内必须杀；Buff 回合不是安全回合",
    "SLIMES_WEAK": "会塞黏液｜AOE 清，先杀中号（塞牌那只）；黏液别留手里过回合",
    "INKLETS_NORMAL": "滑溜 1｜多段/小刀破滑溜，别用大单段",
    "MAWLER_NORMAL": "4×2 开场→怒吼挂 3 易伤→14/16 大单段｜怒吼回合后必须挡满或抢杀",
    "OVERGROWTH_CRAWLERS": "缩小让你 3 回合攻击 -30%（重锤 42→22）｜先打输出再按 0.7 折算斩杀；绒毛虫 3 回合内杀",
    "SNAPPING_JAXFRUIT_NORMAL": "贾克斯果每回合 +2 力，飞蝇菌子给易伤/脆弱｜先杀成长的那只",
    "RUBY_RAIDERS_NORMAL": "弩手 16 > 暴徒 8 > 斧手 6｜先杀弩手（血少伤害高），总入伤 30 必须叠甲",
    "SHRINKER_BEETLE_WEAK": "缩小 -30%（3 回合）｜先打输出，之后按 0.7 算斩杀",
    "FLYCONID_NORMAL": "飞蝇菌子先上易伤/脆弱｜先杀菌子或用 AOE 一起清",
    "SLITHERING_STRANGLER_NORMAL": "扼杀者回合末 3→6→9 递增掉血｜5 回合内杀它，先别管史莱姆",
    "SLIMES_NORMAL": "AOE 检验 + 塞黏液｜先杀中号，别把黏液留在手里",
    "VINE_SHAMBLER_NORMAL": "缠结（攻击牌 +1 费）+ 14/16×2｜低血不进，算能量时把 +1 费算进去",
    # ---------- Underdocks ----------
    "LAGAVULIN_MATRIARCH_BOSS": "睡 3 回合白给启动；醒后力量 2 + 敏捷 -2（你的格挡 -2）｜前 3 回合铺场，醒前留够格挡",
    "SOUL_FYSH_BOSS": "无实体 2（本回合伤害降 1）+ 塞「呼唤」（回合末 6 伤/张，无视格挡）｜多段破无实体；呼唤优先打掉，先算 211÷DPS 的回合账",
    "WATERFALL_GIANT_BOSS": "蒸汽喷发 A10 初始 20、每回合 +3，死亡后下回合爆炸｜杀前先上虚弱（×0.75）再补刀，留够格挡吃自爆",
    "PHANTASMAL_GARDENERS_ELITE": "胆小 6/7（每回合首次命中加盾）｜先打意图最高，AOE 或高单体逐个点杀；120 总血=12 回合，血量不够别进",
    "SKULKING_COLONY_ELITE": "硬化外壳 20/回合｜每回合打满 20 就停手叠甲；惯性 9/11+2 力会滚雪球，5–6 回合内解决",
    "TERROR_EEL_ELITE": "撞击 16/18 与撕扯 3×3+6 活力交替；≤50% 首次眩晕（尖叫 70/75），醒后 99 易伤｜控血进晕眩线，醒后灌伤害",
    "CORPSE_SLUGS_WEAK": "饥饿 4/5 + 脆弱｜双只时先杀一只，另一只吃尸眩晕并 +5 力",
    "CULTISTS_NORMAL": "仪式 2 / 5→6｜越快杀越好，先杀潮湿",
    "LIVING_FOG_NORMAL": "Smoggy：每回合只能打 1 张技能｜集火活雾本体，本体死炸弹消失，别清小怪",
    "FOSSIL_STALKER_NORMAL": "吮吸 3（未格挡伤害 +1 力）+ 脆弱｜别白吃伤害",
    "HAUNTED_SHIP_NORMAL": "虚弱 3 + 纠缠｜先解场再打，别在虚弱里硬输出",
    "CORPSE_SLUGS_NORMAL": "饥饿：先杀一只→其余吃尸眩晕，白赚一回合｜之后它 14+，别拖",
    "PUNCH_CONSTRUCT_NORMAL": "人工制品 1 + 虚弱 1｜debuff 会被挡，直接打",
    "SEAPUNK_WEAK": "力量 1/2 滚雪球，回旋踢 2×4 可到 8×4｜血少优先杀或叠够格挡",
    "SEWER_CLAM_NORMAL": "覆甲 8/9（每回合 -1）｜打穿格挡的当回合才算净伤",
    "SLUDGE_SPINNER_WEAK": "虚弱/脆弱，拖久伤害不够｜优先杀",
    "TOADPOLES_WEAK": "荆棘 2：吐刺 3×3（A10 4×3）+ 旋转 7｜别多段硬打，先算反伤；先杀 Buff 的那只",
    "GREMLIN_MERC_NORMAL": "佣兵偷 20 金、死后召 2 只｜金币 >20 优先杀佣兵",
    "TWO_TAILED_RATS_NORMAL": "抓挠 8→疾病啃咬 6→尖声嘶吼（脆弱）→呼唤后援（召唤）｜T1 就集火召唤者，绝不分散",
    "TOADPOLES_NORMAL": "邪教徒仪式 2 成长 + 蟾蜍荆棘 2｜先杀邪教徒，再处理荆棘",
    # ---------- Act 2 · Hive ----------
    "KAISER_CRAB_BOSS": "蟹之怒（盟友死 +5 力 +99 甲）、后方攻击 +50%｜先杀火箭再打本体，别让蟹之怒白送 99 甲",
    "KNOWLEDGE_DEMON_BOSS": "知识诅咒/抽打/知识过载｜铁甲正常打，长战先算回合账",
    "THE_INSATIABLE_BOSS": "力量成长｜长战检验，拖久就是被磨死",
    "ENTOMANCER_ELITE": "人体蜂房（每次命中塞晕眩）+ 蜜蜂 3×7｜少段高伤，憋爆发一波带走",
    "INFESTED_PRISMS_ELITE": "活力火花（每回合首次受击 +1 能量）｜白赚费，算好格挡回合再输出",
    "DECIMILLIPEDE_ELITE": "接续 25（2 回合后以 25 血复活）｜要么全清，要么留 AOE/收尾补最后一段",
    "CHOMPERS_NORMAL": "人工制品 2｜debuff 会被挡，直接打",
    "BOWLBUGS_NORMAL": "石（失衡：完全挡满→眩晕）、蜜（+15/16 力）、丝（虚弱）、卵｜先杀蜜，石可以算准挡满换眩晕",
    "BOWLBUGS_WEAK": "少一只蜜｜同上，先处理加力量的",
    "EXOSKELETONS_WEAK": "难以杀灭 9（每次伤害 ≤9）｜多段小刀反而好打",
    "HUNTER_KILLER_NORMAL": "柔嫩（你每打一张牌 -1 力 -1 敏）｜牌越多越弱，低血别碰，爆发一波",
    "LOUSE_PROGENITOR_NORMAL": "蜷身 14/18（每场一次格挡）+ 力量 5｜要么爆发破蜷身，要么把甲算进斩杀",
    "EXOSKELETONS_NORMAL": "难以杀灭 9｜多段清，别用单发大牌",
    "MYTES_NORMAL": "浓毒/吸吮｜先杀一只减少入伤",
    "OVICOPTER_NORMAL": "召唤者｜先杀本体，卵不用管",
    "SLUMBERING_BEETLE_NORMAL": "熟睡甲虫覆甲 15/18（睡 3 回合/掉 3 次血醒）｜先打碗虫，甲虫醒前算好那 15/18 甲",
    "SPINY_TOAD_NORMAL": "荆棘 5｜多段硬打=自杀；大单发或先叠格挡再打",
    "THE_OBSCURA_NORMAL": "召唤者｜先杀本体",
    "THIEVING_HOPPER_WEAK": "逃脱大师 5（4 回合后跑）+ 振翅 5（攻击伤害 -50%）｜4 回合内打死，否则白打",
    "TUNNELER_WEAK": "埋地（格挡不清）：打空全部格挡→眩晕｜多段磨甲，眩晕回合再爆发",
    "TUNNELER_NORMAL": "地道虫埋地，碗虫骚扰｜先清碗虫，再磨甲换眩晕",
    # ---------- Act 3 · Glory ----------
    "QUEEN_BOSS": "脆弱/易伤/虚弱 99、魂缚锁链 3（每回合前 3 张抽牌变 Bound）｜4 运转 + 2 回费，药水买额外启动；两套独立启动",
    "TEST_SUBJECT_BOSS": "激怒（你打技能它 +2/3 力）+ 适者生存（复活变强）+ 天罚（每 2 回合 +1 无实体）｜9 回合纯成长检验",
    "DOORMAKER_BOSS": "v0.8.0 数据里的旧 Boss；v0.105 起被 Aeonglass 取代｜实战按 MECHANICS 走",
    "KNIGHTS_ELITE": "幽灵骑士存活时你所有牌带虚无（不打即消失），魔法骑士让你牌降级｜先杀幽灵骑士，再魔法骑士",
    "MECHA_KNIGHT_ELITE": "力量 5 + 人工制品 3｜debuff 会被挡 3 次，直接堆伤害",
    "SOUL_NEXUS_ELITE": "易伤 2/虚弱 2｜先清 debuff 来源，长战算账",
    "GLOBE_HEAD_NORMAL": "流电 6（能力牌被侵蚀成流电）+ 脆弱 2 + 力量 2｜少打能力牌，速杀",
    "AXEBOTS_NORMAL": "库存 2（死后重生，最大生命 +10）｜要杀 3 次，留收尾",
    "CONSTRUCT_MENAGERIE_NORMAL": "立柱/拳击都带人工制品 1（拳击还有虚弱 1）｜debuff 不划算，直接打",
    "DEVOTED_SCULPTOR_WEAK": "仪式 9（每回合 +9 力）｜3 回合内必须杀",
    "FABRICATOR_NORMAL": "召唤者｜先杀本体",
    "FROG_KNIGHT_NORMAL": "覆甲 15/19 + 力量 5 + 脆弱 2｜长战，先算每回合净伤",
    "THE_LOST_AND_FORGOTTEN_NORMAL": "遗忘之物瘴气 + 失落之物偷力量（死后返还）｜先杀偷力量的",
    "SCROLLS_OF_BITING_NORMAL": "纸伤难愈 2（未格挡伤害让你 -1 最大生命）｜挡满，别让它碰",
    "SCROLLS_OF_BITING_WEAK": "同上，单只也要挡满",
    "OWL_MAGISTRATE_NORMAL": "易伤 4 + 翱翔 1（落地前 -50%）｜落地回合才爆发",
    "SLIMED_BERSERKER_NORMAL": "力量 3 + 虚弱 3｜长战，先算回合账",
    "TURRET_OPERATOR_WEAK": "盾墙 25（炮手回合开始 +25 甲）｜先杀炮手",
    # ---------- 事件固定战 ----------
    "BATTLEWORN_DUMMY_EVENT_ENCOUNTER": "时间限制 3 回合｜3 回合内打掉当前那只，别省药",
    "DENSE_VEGETATION_EVENT_ENCOUNTER": "4 只小虫｜AOE 优先，先杀攻击单位",
    "FAKE_MERCHANT_EVENT_ENCOUNTER": "事件战，按普通单怪打",
    "MYSTERIOUS_KNIGHT_EVENT_ENCOUNTER": "覆甲 6 + 力量 6｜先算每回合那 6 甲",
    "PUNCH_OFF_EVENT_ENCOUNTER": "人工制品 1，直接打",
    "THE_ARCHITECT_EVENT_ENCOUNTER": "剧情战，按 MECHANICS 走",
}

ACT_ORDER = {
    "Act 1 - Overgrowth": [
        "CEREMONIAL_BEAST_BOSS", "THE_KIN_BOSS", "VANTOM_BOSS",
        "BYGONE_EFFIGY_ELITE", "BYRDONIS_ELITE", "PHROG_PARASITE_ELITE",
        "RUBY_RAIDERS_NORMAL", "FUZZY_WURM_CRAWLER_WEAK", "MAWLER_NORMAL",
        "VINE_SHAMBLER_NORMAL", "SLITHERING_STRANGLER_NORMAL",
        "NIBBITS_NORMAL", "NIBBITS_WEAK", "OVERGROWTH_CRAWLERS", "SNAPPING_JAXFRUIT_NORMAL",
        "SHRINKER_BEETLE_WEAK", "SLIMES_NORMAL", "SLIMES_WEAK", "FLYCONID_NORMAL",
        "INKLETS_NORMAL", "CUBEX_CONSTRUCT_NORMAL", "FOGMOG_NORMAL",
    ],
    "Underdocks": [
        "WATERFALL_GIANT_BOSS", "SOUL_FYSH_BOSS", "LAGAVULIN_MATRIARCH_BOSS",
        "PHANTASMAL_GARDENERS_ELITE", "SKULKING_COLONY_ELITE", "TERROR_EEL_ELITE",
        "TWO_TAILED_RATS_NORMAL", "CULTISTS_NORMAL", "LIVING_FOG_NORMAL",
        "TOADPOLES_WEAK", "TOADPOLES_NORMAL", "CORPSE_SLUGS_NORMAL", "CORPSE_SLUGS_WEAK",
        "SEAPUNK_WEAK", "SLUDGE_SPINNER_WEAK", "SEWER_CLAM_NORMAL", "FOSSIL_STALKER_NORMAL",
        "HAUNTED_SHIP_NORMAL", "GREMLIN_MERC_NORMAL", "PUNCH_CONSTRUCT_NORMAL",
    ],
    "Act 2 - Hive": [
        "KAISER_CRAB_BOSS", "KNOWLEDGE_DEMON_BOSS", "THE_INSATIABLE_BOSS",
        "ENTOMANCER_ELITE", "INFESTED_PRISMS_ELITE", "DECIMILLIPEDE_ELITE",
        "HUNTER_KILLER_NORMAL", "SPINY_TOAD_NORMAL", "BOWLBUGS_NORMAL", "BOWLBUGS_WEAK",
        "TUNNELER_NORMAL", "TUNNELER_WEAK", "EXOSKELETONS_NORMAL", "EXOSKELETONS_WEAK",
        "LOUSE_PROGENITOR_NORMAL", "SLUMBERING_BEETLE_NORMAL", "OVICOPTER_NORMAL",
        "THE_OBSCURA_NORMAL", "MYTES_NORMAL", "CHOMPERS_NORMAL", "THIEVING_HOPPER_WEAK",
    ],
    "Act 3 - Glory": [
        "QUEEN_BOSS", "TEST_SUBJECT_BOSS", "DOORMAKER_BOSS",
        "KNIGHTS_ELITE", "MECHA_KNIGHT_ELITE", "SOUL_NEXUS_ELITE",
        "DEVOTED_SCULPTOR_WEAK", "GLOBE_HEAD_NORMAL", "FROG_KNIGHT_NORMAL",
        "AXEBOTS_NORMAL", "OWL_MAGISTRATE_NORMAL", "THE_LOST_AND_FORGOTTEN_NORMAL",
        "SCROLLS_OF_BITING_NORMAL", "SCROLLS_OF_BITING_WEAK", "TURRET_OPERATOR_WEAK",
        "CONSTRUCT_MENAGERIE_NORMAL", "FABRICATOR_NORMAL", "SLIMED_BERSERKER_NORMAL",
    ],
    "None": [
        "BATTLEWORN_DUMMY_EVENT_ENCOUNTER", "DENSE_VEGETATION_EVENT_ENCOUNTER",
        "FAKE_MERCHANT_EVENT_ENCOUNTER", "MYSTERIOUS_KNIGHT_EVENT_ENCOUNTER",
        "PUNCH_OFF_EVENT_ENCOUNTER", "THE_ARCHITECT_EVENT_ENCOUNTER",
    ],
}

ACT_TITLE = {
    "Act 1 - Overgrowth": "Act 1 · Overgrowth（密林）",
    "Underdocks": "Act 1 · Underdocks（暗港，与密林二选一）",
    "Act 2 - Hive": "Act 2 · Hive（蜂巢）",
    "Act 3 - Glory": "Act 3 · Glory（荣光）",
    "None": "事件固定战（不进地图池）",
}

PASSIVES = [
    ("荆棘 N", "被攻击命中时反伤 N/段", "多段=自杀；先叠格挡再攻击，反伤会被格挡吸收"),
    ("胆小 N", "每回合第一次被命中获得 N 格挡", "先小刀垫掉，再打大牌；多段会反复被吃"),
    ("覆甲 N", "回合结束 +N 甲，每回合 -1 层", "打穿格挡的当回合才算净伤"),
    ("硬化外壳 N", "每回合最多失去 N 点生命（整回合累计）",
     "打满 N 就停手叠甲，别浪费输出；calc.lethal 已按上限算，别看血条自己手算「45 血能一刀斩」"),
    ("难以杀灭 N", "单次伤害封顶 N（连击每段各封顶）", "多段小刀反而好打；calc.dmg 已含封顶"),
    ("滑溜 N", "接下来 N 次失去生命只掉 1", "多段破"),
    ("无实体 N", "本回合受到伤害降为 1", "多段破，或等下回合"),
    ("人工制品 N", "挡掉 N 次负面状态", "先算剥壳成本；不值得就直接堆伤害"),
    ("仪式 N", "回合结束 +N 力量", "优先击杀，越拖越疼"),
    ("领地意识", "回合结束 +1 力量", "同上"),
    ("埋地", "格挡不随回合消失；打空全部格挡→眩晕", "多段磨甲，眩晕回合爆发"),
    ("失衡", "攻击被完全格挡→眩晕", "算准挡满，白赚一回合"),
    ("紧缠 N", "回合末你掉 N 血，层数递增", "5 回合内必须杀"),
    ("饥饿", "有敌人死亡时吃尸、眩晕并 +1 力量", "双只时先杀一只，另一只白给一回合"),
    ("接续 N", "2 回合后以 N 血复活", "留 AOE/收尾补刀"),
    ("适者生存", "死亡时复活并变强", "留爆发给复活后"),
    ("纸伤难愈", "未格挡伤害让你 -1 最大生命", "必须挡满"),
    ("柔嫩", "你每打一张牌 -1 力 -1 敏", "少打牌、憋一波爆发"),
    ("蟹之怒", "盟友死亡时 +5 力量 +99 格挡", "先杀会触发的那只，或先杀本体"),
    ("活力火花", "每回合首次受到攻击伤害时你 +1 能量", "白赚费，算好回合"),
    ("人体蜂房", "每次被命中往你抽牌堆塞晕眩", "少段高伤"),
    ("流电", "能力牌被侵蚀成流电", "少打能力牌"),
    ("魂缚锁链 N", "每回合前 N 张抽牌变 Bound", "多套启动，别只押一套"),
    ("蒸汽喷发 N", "死亡后下回合造成 N 伤害", "杀前上虚弱，留格挡"),
]


def load():
    enc = json.load(open("tools/data/eng/encounters.json", encoding="utf-8"))
    con = sqlite3.connect("db/sts2.db")
    con.row_factory = sqlite3.Row
    mon = {r["id"]: r for r in con.execute("select * from enemies")}
    con.close()
    return enc, mon


def name_of(m, mid):
    r = m.get(mid)
    if r and r["name_zh"] and "#" not in r["name_zh"]:
        return r["name_zh"]
    return NAME_FIX.get(mid) or (r["name_en"] if r else mid)


def hp_of(m, mid):
    r = m.get(mid)
    if not r or not r["hp_min"]:
        return ""
    lo, hi = r["hp_min"], r["hp_max"]
    base = f"{lo}" if lo == hi else f"{lo}–{hi}"
    a10 = A10_HP.get(mid)
    return f"{base} 血" + (f"（A10 {a10}）" if a10 else "")


def comp_line(e, m):
    counts = {}
    for mm in e["monsters"]:
        counts[mm["id"]] = counts.get(mm["id"], 0) + 1
    uniq = sorted(counts, key=lambda mid: -(m[mid]["hp_max"] or 0) if mid in m else 0)
    if len(uniq) == 1:
        mid = uniq[0]
        cnt = counts[mid]
        hp = hp_of(m, mid)
        head = f"×{cnt}，" if cnt > 1 else ""
        return head + hp
    parts = []
    for mid in uniq:
        nm = name_of(m, mid)
        cnt = counts[mid]
        hp = hp_of(m, mid)
        piece = f"{nm} ×{cnt}" if cnt > 1 else nm
        if hp:
            piece += f" {hp}"
        parts.append(piece)
    return " + ".join(parts)


def title_of(e, m):
    if e["id"] in ENC_FIX:
        return ENC_FIX[e["id"]]
    names = []
    for mm in e["monsters"]:
        nm = name_of(m, mm["id"])
        if nm not in names:
            names.append(nm)
    return names[0] if len(names) == 1 else " + ".join(names[:3])


def build():
    enc, m = load()
    by_id = {e["id"]: e for e in enc}
    missing = [e["id"] for e in enc if e["id"] not in BRIEF]
    if missing:
        print("MISSING BRIEF:", ", ".join(missing))
        if "--check" in sys.argv:
            return 1
    if "--check" in sys.argv:
        print(f"{len(enc)} encounters, all briefed")
        return 0

    out = io.StringIO()
    out.write("# 敌人速查 v3 · 按幕/遭遇（不是字母表）\n\n")
    out.write("> 开战前：按幕找到这场遭遇，读「先做什么」。战斗内一律以 `state` 的 `MECHANICS`/`ANCHOR` 为准。\n")
    out.write("> HP 为 db 基础值；标了 A10 的用实测值，其余 A10 约 +5~10%，以实时血条为准。\n")
    out.write("> 记号：🔥 可能直接带走一局｜⚠️ 会滚雪球｜— 按流程打。\n\n")

    out.write("## 七条通用原则\n\n")
    out.write("1. **召唤者**：本体 2 回合内能杀 → 直杀本体；本体厚（>4 回合输出）且爪牙贡献 ≥40% 入伤 → 先清爪牙。\n")
    out.write("2. **荆棘/反伤**：先算每段反伤，多段硬打=自杀；先叠格挡再攻击。\n")
    out.write("3. **Buff 回合不是安全回合**：按「下一击」留血线。\n")
    out.write("4. **多段 N×M**：挡牌按总值；每段独立吃格挡/胆小/荆棘。\n")
    out.write("5. **成长型先杀**：仪式/领地/力量成长，越拖越疼。\n")
    out.write("6. **眩晕窗口**（骇鳗/地道虫/碗虫石/仪式兽）要提前算，不是碰运气。\n")
    out.write("7. **会复活/接续**（实验体/千足虫/外骨骼/巨斧）要留收尾，别把爆发打在第一条命上。\n\n")

    for act in ["Act 1 - Overgrowth", "Underdocks", "Act 2 - Hive", "Act 3 - Glory", "None"]:
        out.write(f"## {ACT_TITLE[act]}\n\n")
        ids = list(ACT_ORDER[act])
        ids += [e["id"] for e in enc if (e.get("act") or "None") == act and e["id"] not in ids]
        for eid in ids:
            e = by_id.get(eid)
            if not e:
                continue
            room = e["room_type"]
            threat = "🔥" if room in ("Boss", "Elite") else THREAT.get(eid, "—")
            tags = []
            if room in ("Boss", "Elite"):
                tags.append(room if room == "Boss" else "精英")
            if e.get("is_weak"):
                tags.append("弱")
            label = f"{threat} **{title_of(e, m)}**"
            if tags:
                label += f"（{'/'.join(tags)}）"
            comp = COMP_FIX.get(eid) or comp_line(e, m)
            out.write(f"- {label}：{comp}｜{BRIEF.get(eid, '')}\n")
        out.write("\n")

    out.write("## 附：被动速查\n\n")
    out.write("| 被动 | 效果 | 铁甲怎么用 |\n|---|---|---|\n")
    for a, b, c in PASSIVES:
        out.write(f"| {a} | {b} | {c} |\n")
    out.write("\n")
    out.write("数据：`tools/data/eng/encounters.json`（幕/编成）+ `db/sts2.db`（HP/被动）；打法来自 run1–33 复盘与实战校正。\n")
    out.write("重新生成：`python tools/build_enemies_v3.py`（改 BRIEF 即可）。\n")

    text = out.getvalue()
    open(OUT, "w", encoding="utf-8", newline="\n").write(text)
    print(f"written {OUT}: {len(text.encode("utf-8"))} bytes, {text.count(chr(10))} lines")
    return 0


if __name__ == "__main__":
    sys.exit(build())

