#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""牌组 DPS 体检：把「牌太多/输出不够」这件事算成数字。

用法：
  python tools/dps.py            # 读当前这局的牌组+遗物
  python tools/dps.py --deck 19  # 只看张数闸门

只做一件事：估算「每回合净输出」，再对比各 Boss 的有效血 / 回血 / 硬截止，
输出「几回合能打死」和「按当前牌组是必死还是够」。

模型（故意保守）：
  每回合抽 5 张；攻击牌占比 = 攻击牌数/牌组；
  期望攻击牌 = 5 × 占比（上限 3.2，实际手牌经济没那么好）；
  期望每张伤害 = 攻击牌平均伤害（含升级、力量按 0 算）；
  能量闸：3 能量 ÷ 攻击牌平均费用（0 费牌按 0.5 折算，防止 0 费刷分）；
  净 DPS = min(手牌闸, 能量闸) × 期望每张伤害。
"""

from __future__ import annotations

import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PACK = os.path.dirname(_HERE)
ROOT = os.path.dirname(os.path.dirname(_PACK))
sys.path.insert(0, ROOT)
sys.path.insert(0, _HERE)

import gates  # noqa: E402
import sim  # noqa: E402
import sts2  # noqa: E402

# 第二幕 Boss 口径（docs/boss_act2_dossier.md / boss_act3_dossier.md）
BOSSES = [
    ("知识恶魔", 399, 459, 10, "每 4 回合回 30、+3 力量"),
    ("帝皇蟹", 300, 300, 8, "双目标，先杀火箭"),
    ("无厌沙虫", 400, 400, 9, "吞牌，手感牌被吞"),
]
ACT1_WARN = "第一幕 Boss 有效血 ~300，硬截止 10 回合"


def deck_of(state: dict) -> list[dict]:
    run = state.get("run") or {}
    cards = run.get("deck") or []
    return [sim.parse_card(c, None) for c in cards]


def audit(state: dict) -> dict:
    deck = [c for c in deck_of(state) if c]
    n = len(deck)
    atk = [c for c in deck if c.get("t") == "A" and c.get("dmg") is not None]
    blk = [c for c in deck if c.get("blk")]
    basics = [c for c in deck if str(c.get("id", "")).startswith(("STRIKE", "DEFEND"))]
    dmg = [c["dmg"] * max(1, c.get("hits", 1)) for c in atk]
    avg_dmg = sum(dmg) / len(dmg) if dmg else 0.0
    costs = [max(0.5, float(c.get("c", 1) or 0)) for c in atk]
    avg_cost = sum(costs) / len(costs) if costs else 1.0
    ratio = len(atk) / n if n else 0.0
    hand_limit = min(3.2, 5 * ratio)
    en_limit = 3.0 / avg_cost if avg_cost else 0.0
    # 易伤折算：破击/痛击挂上之后，我方几乎所有伤害 ×1.5。只按「有易伤牌」给保守折扣。
    vuln_layers = max([int(c.get("vuln") or 0) for c in deck] or [0])
    vuln_mult = 1.0
    if vuln_layers >= 5:
        vuln_mult = 1.35
    elif vuln_layers >= 2:
        vuln_mult = 1.25
    dps = min(hand_limit, en_limit) * avg_dmg * vuln_mult
    return {"n": n, "atk": len(atk), "blk": len(blk), "basics": len(basics),
            "ratio": ratio, "avg_dmg": avg_dmg, "avg_cost": avg_cost,
            "hand_limit": hand_limit, "en_limit": en_limit,
            "vuln": vuln_layers, "vuln_mult": vuln_mult, "dps": dps}


def main(argv: list[str]) -> int:
    force_n = None
    if "--deck" in argv:
        force_n = int(argv[argv.index("--deck") + 1])
    st = sts2.get_state()
    a = audit(st)
    n = force_n or a["n"]
    print(f"牌组 {n} 张：攻击 {a['atk']} / 格挡 {a['blk']} / 打击防御 {a['basics']}")
    print(f"攻击占比 {a['ratio']*100:.0f}%  平均伤害 {a['avg_dmg']:.1f}  平均费用 {a['avg_cost']:.2f}")
    print(f"手牌闸 {a['hand_limit']:.1f} 张/回合 · 能量闸 {a['en_limit']:.1f} 张/回合"
          f" · 易伤 ×{a['vuln_mult']:.2f}（{a['vuln']} 层）")
    print(f"--> 估计净输出 {a['dps']:.1f} / 回合")
    frees = [c for c in deck_of(st) if c and gates.is_free_output(c)]
    print(f"0费/小刀输出 {len(frees)} 张（{'、'.join(c['n'] for c in frees) or '无'}）："
          f"上面这个净输出**不含**它们的免费伤害"
          f"{'　⚠️ 第二幕起要 ≥2 张（doctrine §3.2）' if len(frees) < 2 else ''}")
    print()
    bar = 50.0
    print(f"{'Boss':<10}{'有效血':>7}{'回合':>6}   判定")
    for name, hp, eff, limit, note in BOSSES:
        t = eff / a["dps"] if a["dps"] else 99
        ok = "✅ 够" if t <= limit - 1 else ("⚠️ 擦边" if t <= limit + 1 else "❌ 必死")
        print(f"{name:<10}{eff:>7}{t:>6.1f}   {ok}（硬截止 {limit}）{note}")
    print()
    if a["dps"] < bar:
        print(f"结论：净输出 {a['dps']:.1f} < {bar:.0f} —— 第二幕 Boss 打不下来。")
        print("该做的三件事（按性价比）：1) 删打击/防御（提高攻击占比）2) 升级主力攻击牌 3) 吃 1 张纯输出牌")
    else:
        print(f"结论：净输出 {a['dps']:.1f} ≥ {bar:.0f}，第二幕可以打，注意格挡闸。")
    cap = {1: 16, 2: 18}.get(int((st.get("run") or {}).get("act_id", 0)) + 1, 20)
    if n > cap:
        print(f"⚠️ 牌组 {n} 张 > 本幕上限 {cap}：抽牌稀释，先删牌再加牌。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
