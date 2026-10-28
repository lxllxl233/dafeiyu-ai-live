# -*- coding: utf-8 -*-
"""A10 门槛体检：牌组厚度 / 甲牌占比 / 抽牌数 / 引擎。

run71/72/73 三次死在同一个地方，所以把门槛做成可执行检查，别靠自觉：

    python tools/gates.py            # 只看当前这一局
    python tools/gates.py --act 2    # 按第二幕门槛算

门槛（docs/RUN_DOCTRINE.md §0.5）：
    第一幕 Boss：牌组 ≤17、甲牌 ≥45%
    第二幕 Boss：牌组 ≤16、甲牌 ≥45%、抽牌 ≥3
    第三幕     ：外加 1 个防御引擎（壁垒/金属化/岿然不动）
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PACK = os.path.dirname(_HERE)
ROOT = os.path.dirname(os.path.dirname(_PACK))
sys.path.insert(0, ROOT)
sys.path.insert(0, _HERE)

import sts2  # noqa: E402

BLOCK_KEYS = ("格挡", "Block")
DRAW_KEYS = ("抽", "Draw")
ENGINE_IDS = {
    "BARRICADE": "壁垒",
    "METALLICIZE": "金属化",
    "IMPERVIOUS": "岿然不动",
}
ENGINE_TXT = ("格挡", "覆甲")  # 引擎的兜底判断：能力牌 + 格挡/覆甲


def card_text(c: dict) -> str:
    return " ".join(str(c.get(k) or "") for k in
                    ("rules_text", "resolved_rules_text", "name"))


def is_block(c: dict) -> bool:
    txt = card_text(c)
    if any(k in txt for k in BLOCK_KEYS):
        return True
    for dv in c.get("dynamic_values") or []:
        if str(dv.get("name")) in ("Block", "BlockPower"):
            return True
    return False


def is_draw(c: dict) -> bool:
    txt = card_text(c)
    if any(k in txt for k in DRAW_KEYS):
        return True
    return any(str(dv.get("name")) in ("Cards", "Draw") for dv in c.get("dynamic_values") or [])


def is_engine(c: dict) -> bool:
    cid = str(c.get("card_id") or "")
    if cid in ENGINE_IDS:
        return True
    if str(c.get("card_type")) != "Power":
        return False
    txt = card_text(c)
    return any(k in txt for k in ENGINE_TXT)


def card_dmg(c: dict) -> int:
    for dv in c.get("dynamic_values") or []:
        if str(dv.get("name")) == "Damage":
            try:
                return int(dv.get("current_value") or dv.get("base_value") or 0)
            except Exception:  # noqa: BLE001
                return 0
    return 0


def is_free_output(c: dict) -> bool:
    """0 费输出 = 第二幕 DPS 的唯一免费杠杆（doctrine §3.2，用户亲口提示「多打小刀」）。

    两类都算：①真·0 费攻击牌（小刀/亮剑）；②生成小刀的能力/技能牌（刀刃之舞/无限刀刃）。
    """
    if str(c.get("card_type")) == "Curse":
        return False
    if "小刀" in card_text(c):
        return True
    cost = c.get("cost", c.get("energy_cost"))
    try:
        cost = int(cost)
    except (TypeError, ValueError):
        return False
    return cost == 0 and card_dmg(c) > 0


def is_real_attack(c: dict) -> bool:
    """非基础的高伤攻击：run74 就是「只拿甲不拿刀」被 act1 的怪磨死的。"""
    if str(c.get("card_type")) != "Attack":
        return False
    if "打击" in str(c.get("name") or "") and card_dmg(c) <= 7:
        return False
    return card_dmg(c) >= 8 or "Strike" in str(c.get("card_id") or "")


def analyse(deck: list) -> dict:
    playable = [c for c in deck if str(c.get("card_type")) != "Curse"]
    curses = [c for c in deck if str(c.get("card_type")) == "Curse"]
    blocks = [c for c in playable if is_block(c)]
    draws = [c for c in playable if is_draw(c)]
    engines = [c for c in playable if is_engine(c)]
    atks = [c for c in playable if is_real_attack(c)]
    frees = [c for c in playable if is_free_output(c)]
    return {
        "deck": len(deck),
        "playable": len(playable),
        "curses": [c.get("name") for c in curses],
        "block": len(blocks),
        "block_ratio": round(len(blocks) / len(playable), 3) if playable else 0.0,
        "draw": len(draws),
        "engines": [ENGINE_IDS.get(str(c.get("card_id")), c.get("name")) for c in engines],
        "real_atk": len(atks),
        "real_atk_cards": [f"{c.get('name')}({card_dmg(c)})" for c in atks],
        "free_output": len(frees),
        "free_cards": [c.get("name") for c in frees],
        "block_cards": [c.get("name") for c in blocks],
        "draw_cards": [c.get("name") for c in draws],
    }


def gate(act: int, info: dict) -> list:
    """返回 [(名字, 实际, 门槛, 是否过)]。"""
    if act <= 1:
        rows = [("牌组张数", info["deck"], "<=17"), ("甲牌占比", info["block_ratio"], ">=0.45"),
                ("非基础攻击", info["real_atk"], ">=2")]
    elif act == 2:
        rows = [("牌组张数", info["deck"], "<=16"), ("甲牌占比", info["block_ratio"], ">=0.45"),
                ("抽牌张数", info["draw"], ">=3"), ("非基础攻击", info["real_atk"], ">=2"),
                ("0费输出", info["free_output"], ">=2")]
    else:
        rows = [("牌组张数", info["deck"], "<=16"), ("甲牌占比", info["block_ratio"], ">=0.45"),
                ("抽牌张数", info["draw"], ">=3"), ("非基础攻击", info["real_atk"], ">=2"),
                ("0费输出", info["free_output"], ">=2"),
                ("防御引擎", len(info["engines"]), ">=1")]
    out = []
    for name, got, want in rows:
        if want.startswith("<="):
            ok = got <= int(want[2:])
        elif want.startswith(">="):
            ok = got >= float(want[2:])
        else:
            ok = False
        out.append((name, got, want, ok))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--act", type=int, default=0, help="按哪一幕的门槛算（默认取当前幕）")
    ap.add_argument("--deck", default="", help="直接给牌组 JSON 文件（离线算）")
    args = ap.parse_args()

    if args.deck:
        import json
        with open(args.deck, encoding="utf-8-sig") as f:
            deck = json.load(f)
        act = args.act or 1
    else:
        st = sts2.get_state()
        run = st.get("run") or {}
        deck = run.get("deck") or []
        act = args.act or sts2._int(run.get("act"), 1) or 1
        print(f"SCREEN {st.get('screen')} | act {act} floor {run.get('floor')} | "
              f"HP {run.get('current_hp')}/{run.get('max_hp')} | gold {run.get('gold')}")
    if not deck:
        print("没有牌组数据（可能还没进局）")
        return 2

    info = analyse(deck)
    print(f"DECK {info['deck']} 张 | 可打 {info['playable']} | 诅咒 {info['curses'] or '无'}")
    print(f"甲牌 {info['block']}（{info['block_ratio']:.0%}）：{'、'.join(info['block_cards']) or '无'}")
    print(f"抽牌 {info['draw']}：{'、'.join(info['draw_cards']) or '无'}")
    print(f"引擎 {info['engines'] or '无'}")
    print(f"0费输出 {info['free_output']}：{'、'.join(info['free_cards']) or '无'}"
          f"{'（第二幕起要 ≥2，这是唯一的免费伤害杠杆）' if act >= 2 else ''}")
    print()
    rows = gate(act, info)
    bad = 0
    for name, got, want, ok in rows:
        if isinstance(got, float):
            print(f"{'PASS' if ok else 'FAIL'} 第{act}幕 {name} = {got:.0%} （要求 {want}）")
        else:
            print(f"{'PASS' if ok else 'FAIL'} 第{act}幕 {name} = {got} （要求 {want}）")
        bad += 0 if ok else 1
    print()
    print("GATES OK" if bad == 0 else f"GATES FAIL {bad} 条 —— 按 docs/RUN_DOCTRINE.md 补")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
