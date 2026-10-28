#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档一致性测试：版本串、阈值唯一源、复盘覆盖、禁用结论。

跑法：python tools/test_docs_consistency.py
（tools/test_all.py 会自动带上它。）

为什么要有这个文件：run96–run105 的复盘里出现过「同归于尽」「五连败」这类
和账本矛盾的结论，版本串也漂过（SKILL v21 / NEXT_PROMPT v20 / README v20），
阈值抄了三份（NEXT_PROMPT 的 HP<35% 与 SKILL 的 <60% 冲突）。
这些都是「文字写得比证据多」。本测试把三类漂移钉死：

  1. 版本串只有 v22 一个值（SKILL.md ↔ skill README ↔ NEXT_PROMPT ↔ README.md）；
  2. 血闸数字只认 tools/hard_rules.py（别处出现 35% 这类旧值就 FAIL）；
  3. run96–run106 每局都有复盘文件，且不再出现已被账本推翻的结论。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(".").resolve()
SKILL_DIR = ROOT / ".dsh" / "skills" / "sts2-ironclad-ironclad"

fails = []
passes = 0


def check(name: str, got, want) -> None:
    global passes
    if got == want:
        passes += 1
        print(f"PASS {name}")
    else:
        fails.append(name)
        print(f"FAIL {name}: got={got!r} want={want!r}")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def one(pattern: str, text: str) -> str | None:
    m = re.findall(pattern, text)
    vals = {v for v in m}
    return vals.pop() if len(vals) == 1 else None


def main() -> int:
    skill_md = read(SKILL_DIR / "SKILL.md")
    skill_readme = read(SKILL_DIR / "README.md")
    next_prompt = read(ROOT / "NEXT_PROMPT.txt")
    root_readme = read(ROOT / "README.md")
    lessons = read(ROOT / "docs" / "LESSONS.md")

    print("=== 1) 版本串：四处必须是同一个 vN ===")
    skill_v = one(r"决策OS v(\d+)", skill_md)
    skill_readme_v = one(r"版本：\*\*v(\d+)\*\*", skill_readme)
    next_v = one(r"决策 OS v(\d+)", next_prompt)
    root_v = one(r"sts2-ironclad-ironclad`，v(\d+)", root_readme)
    check("SKILL.md 版本唯一", skill_v is not None, True)
    check("skill README 版本唯一", skill_readme_v is not None, True)
    check("NEXT_PROMPT 版本唯一", next_v is not None, True)
    check("README.md 版本唯一", root_v is not None, True)
    versions = {skill_v, skill_readme_v, next_v, root_v}
    check("四处版本一致", len(versions), 1)

    print()
    print("=== 2) 血闸数字只认 tools/hard_rules.py ===")
    sys.path.insert(0, str(ROOT / "tools"))
    import hard_rules  # noqa: E402

    hp_rule = next(r["t"] for r in hard_rules.RULES if r["id"] == "hp")
    allowed = set(re.findall(r"<(\d+)%", hp_rule))
    check("hard_rules 血闸解析出三档", sorted(allowed), ["50", "60", "70"])

    # 别处出现的 HP<NN% 必须落在 allowed 里（run 里 35% 就是这么漂出来的）
    stale = []
    for name, text in (("NEXT_PROMPT", next_prompt), ("SKILL.md", skill_md),
                       ("README.md", root_readme)):
        for num in re.findall(r"HP\s*<\s*(\d+)%", text):
            if num not in allowed:
                stale.append(f"{name}:HP<{num}%")
    check("没有旧血闸数字", stale, [])

    print()
    print("=== 3) 阈值不复制：SKILL/NEXT_PROMPT 必须指向 hard_rules ===")
    check("NEXT_PROMPT 指向 hard_rules", "tools/hard_rules.py" in next_prompt, True)
    check("SKILL.md 指向 hard_rules", "tools/hard_rules.py" in skill_md, True)
    check("skill README 提到硬闸单一源", "hard_rules" in skill_readme, True)
    check("NEXT_PROMPT 声明不另抄数字", "不要在这份 prompt" in next_prompt, True)

    print()
    print("=== 4) 复盘覆盖：run96–run106 每局都要有文件 ===")
    missing = [f"run{n}" for n in range(96, 107)
               if not (ROOT / "logs" / f"run{n}_report.md").exists()
               and not (ROOT / "logs" / f"run{n}_review.md").exists()]
    check("run96–run106 复盘齐全", missing, [])

    idx = ROOT / "logs" / "SEASON_INDEX.md"
    idx_text = read(idx) if idx.exists() else ""
    check("赛季索引存在", idx.exists(), True)
    check("索引覆盖 run96–run106",
          [f"run{n}" for n in range(96, 107) if f"run{n}" not in idx_text], [])
    # 索引里的两局「中止」必须标成中止，不能算败（否则赛季结论又错）
    check("索引把 run96 标成中止",
          "| run96 |" in idx_text and "中止" in idx_text, True)
    check("索引写明 run105 剩 16（150）", "16（150）" in idx_text, True)

    print()
    print("=== 5) 禁用结论：账本已推翻的说法不许再当结论用 ===")
    # 允许「旧版写的是 X，账本说不对」这种更正记录：同一行里必须有更正标记。
    OK_MARK = ("旧版", "撤回", "不是", "~~", "错", "纠正", "改：", "改后")
    hits = []
    for p in sorted((ROOT / "logs").glob("run*_report.md")) + \
            sorted((ROOT / "logs").glob("run*_review.md")):
        for i, line in enumerate(read(p).splitlines(), 1):
            if "同归于尽" in line and not any(m in line for m in OK_MARK):
                hits.append(f"{p.name}:{i}:同归于尽")
    # LESSONS 里的历史章节（run91 的「五连败」）是另一回事，只钉 run102–106 这条具体结论
    for i, line in enumerate(lessons.splitlines(), 1):
        if "run102–run106 五连败" in line and not any(m in line for m in OK_MARK):
            hits.append(f"LESSONS.md:{i}:run102–run106 五连败")
    check("没有已推翻的结论", hits, [])

    # run105 的两条硬事实：敌人剩 16/150、不是斩杀
    r105 = read(ROOT / "logs" / "run105_report.md")
    check("run105 写明敌人剩 16/150", "16/150" in r105, True)

    print()
    print("=== 6) 经验卡与文档同源 ===")
    lp = ROOT / "logs" / "lessons.jsonl"
    check("lessons.jsonl 存在", lp.exists(), True)
    if lp.exists():
        sys.path.insert(0, str(ROOT / "tools"))
        import lesson_index  # noqa: E402

        cards = lesson_index.load()
        check("经验卡自检", lesson_index.validate(cards), [])
        check("卡片出处都在本季 run96–run106",
              sorted({c["src"]["run"] for c in cards
                      if not str(c["src"]["run"]) in {f"run{n}" for n in range(96, 107)}}), [])
        check("卡片里没有已推翻的结论",
              [c["id"] for c in cards if "同归于尽" in c["t"] or "五连败" in c["t"]], [])
        check("注入上限 ≤3 条", lesson_index.MAX_N <= 3, True)

    print()
    print("=== 7) hard_rules 仍是 5 条、能跑 ===")
    check("硬闸条数", len(hard_rules.RULES), 5)
    check("硬闸自检", hard_rules.validate(), [])

    print()
    print("=== 8) 回放审计：文档、工具、产物同源 ===")
    ra_doc = ROOT / "docs" / "REPLAY_AUDIT.md"
    check("docs/REPLAY_AUDIT.md 存在", ra_doc.exists(), True)
    check("docs/REPLAY_AUDIT.md 被 NEXT_PROMPT 引用",
          "docs/REPLAY_AUDIT.md" in next_prompt, True)
    check("replay_audit 自测存在", (ROOT / "tools" / "test_replay_audit.py").exists(), True)
    for f in ("replay_audit_season.json", "replay_audit_all.json"):
        p = ROOT / "logs" / f
        check(f"{f} 存在", p.exists(), True)
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            check(f"{f} 四个指标齐全",
                  {k for k in d["total"]
                   if k in ("行数", "可对账行", "规则注入条数", "经验注入条数")},
                  {"行数", "可对账行", "规则注入条数", "经验注入条数"})
            check(f"{f} 每行 5 条规则",
                  round(d["total"]["规则注入条数"] / max(1, d["total"]["行数"]), 2), 5.0)
            check(f"{f} 没有历史执行被误拒", d["total"].get("历史执行今天被拒", 0), 0)
            check(f"{f} 没有历史被拒被放行", d["total"].get("历史被拒今天全放行", 0), 0)

    print()
    if fails:
        print(f"FAILED {len(fails)}: " + ", ".join(fails))
        return 1
    print(f"ALL PASS（{passes} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
