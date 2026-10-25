# -*- coding: utf-8 -*-
"""Add skill-maintenance + catchphrase rules (user feedback)."""

import sqlite3

RULES = [
    (
        "skill_frontmatter_keep",
        "invariant",
        1,
        "改 SKILL.md 必须保留 YAML frontmatter，否则技能从目录消失",
        "SKILL.md 顶部的 `--- name: ... description: ... ---` 是技能注册信息：改写/精简正文时必须原样保留（或同步更新 description）。"
        "失去 frontmatter → 会话技能目录里直接看不到这个技能，下一局就加载不到。改完用 `Get-Content -TotalCount 5` 自查。",
        "2026-10-25 重写 sts2 技能为 v5 时把 frontmatter 覆盖掉了，技能当场从 available_skills 里消失，补回后才恢复。",
        "SKILL.md / 任何技能文件",
    ),
    (
        "skill_lessons_inline",
        "invariant",
        1,
        "每局收尾把可复用教训压成 1–3 行写回 SKILL.md",
        "复盘后不只写 docs/LESSONS.md：把**下次开局就要用**的教训（新公式、新触发条件、新工具坑）压成 1–3 行，写进 SKILL.md 对应小节；"
        "细节、数字、原话留在 LESSONS。SKILL.md 是每局都要加载的常驻上下文，超过 ~150 行就删旧细节、合并重复。",
        "用户要求：每局打完把经验教训总结进 skill，下次思考不要带其他信息（省 token）。",
        "SKILL.md 维护",
    ),
    (
        "catchphrase_cadence",
        "invariant",
        1,
        "每 3–4 个动作一句口头禅，贴场面、不连刷、不解释",
        "直播要有「人味」：已启动 / 这下看懂了 / 这波不亏 / 收工 / 寄 / 坐牢 / 数学爬塔 / 发牌员针对我 等。"
        "规则：① 每 3–4 个动作至少一句（MEME_BAR ≥25%）；② 同一条不连刷，轮换着用；③ 不解释梗；④ 只贴当前场面，不硬凹。"
        "长篇分析照旧写进 --thought，口头禅放 --meme。",
        "用户看录像指出：基本没听到「我已启动」「这下看懂了」这类俏皮话，解说太像战术报告。",
        "所有对外解说",
    ),
]


def main() -> None:
    con = sqlite3.connect("db/sts2.db")
    cur = con.cursor()
    added = 0
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
            added += 1
            print("added", rid)
    con.commit()
    cur.execute("SELECT COUNT(*) FROM strategy_rules")
    print("total rules:", cur.fetchone()[0], "| added:", added)
    con.close()


if __name__ == "__main__":
    main()
