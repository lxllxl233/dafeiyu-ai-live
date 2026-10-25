# -*- coding: utf-8 -*-
"""Write run33 block/setup lessons into SKILL.md."""

SKILL = r".dsh/skills/sts2-ironclad-ironclad/SKILL.md"

with open(SKILL, encoding="utf-8") as f:
    s = f.read()

s = s.replace(
    "10 非斩杀回合 → incoming 压到 ≤5；长战（T≈敌HP÷我输出 ≥6）→ 每回合挡到 0",
    "10 非斩杀回合 → incoming 压到 ≤5；长战（T≈敌HP÷我输出 ≥6）→ 每回合挡到 0\n"
    "10.5 手上有格挡牌又有能量 → 先看 BLOCK_POOL：够挡满就先挡满，别把耸肩/防御留在手里结束回合\n"
    "10.6 **预斩杀回合**：算到下回合大概率能斩，本回合只铺易伤/力量/引擎，剩下全叠防御——少掉的血就是斩杀回合的保险",
)

with open(SKILL, "w", encoding="utf-8") as f:
    f.write(s)

print("skill updated")
