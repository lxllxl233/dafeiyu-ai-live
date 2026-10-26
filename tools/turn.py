#!/usr/bin/env python3
"""把一回合写成短命令行，省掉 JSON 引号（PowerShell 5.1 会吃掉内层双引号）。

用法：
    python tools/turn.py "BASH@0 STRIKE STRIKE end" --thought "41 血，先挂易伤" --meme "趁它上状态。"
    python tools/turn.py "DEFEND DEFEND end"
    python tools/turn.py "end" --thought "这回合没事干"

记号：
    CARD        打一张牌（不需要目标，比如防御）
    CARD@N      打一张牌，目标敌人下标 N
    POTION:N[@T] 喝药水槽 N（目标可选）
    end         结束回合（必须是最后一步）
    !           后缀表示 thought/meme 挂在这一手（默认第一手）

实际调用的是 sts2.cmd_batch：card_id 每步按当前手牌重解析，超费整手拒。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sts2  # noqa: E402


def parse_spec(spec: str) -> list[dict]:
    items: list[dict] = []
    for tok in spec.split():
        mark = tok.endswith("!")
        if mark:
            tok = tok[:-1]
        low = tok.lower()
        if low == "end":
            item = {"action": "end_turn"}
        elif low.startswith("potion:"):
            body = tok.split(":", 1)[1]
            slot, _, target = body.partition("@")
            item = {"action": "use_potion", "option_index": int(slot)}
            if target:
                item["target_index"] = int(target)
        elif "@" in tok:
            card, _, target = tok.partition("@")
            item = {"action": "play_card", "card_id": card.upper(), "target_index": int(target)}
        else:
            item = {"action": "play_card", "card_id": tok.upper()}
        if mark:
            item["_say"] = True
        items.append(item)
    return items


def resolve_ids(items: list[dict]) -> None:
    """把 STRIKE 这种短名补成手牌里的真 id（STRIKE_IRONCLAD）。"""
    try:
        state = sts2.get_state()
    except Exception:
        return
    hand = (state.get("combat") or {}).get("hand") or []
    hand_ids = [str(c.get("card_id")) for c in hand if isinstance(c, dict)]
    if not hand_ids:
        return
    for it in items:
        cid = it.get("card_id")
        if not cid or cid in hand_ids:
            continue
        matches = sorted({h for h in hand_ids if h.startswith(cid)})
        if len(matches) == 1:
            it["card_id"] = matches[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="一回合一条命令（sts2 batch 的短写法）")
    ap.add_argument("spec", help="BASH@0 STRIKE STRIKE end")
    ap.add_argument("--thought", default="")
    ap.add_argument("--meme", default="")
    ap.add_argument("--dry", action="store_true", help="只打印 JSON，不发")
    a = ap.parse_args()

    items = parse_spec(a.spec)
    resolve_ids(items)
    if not items:
        print("TURN_EMPTY 没有动作")
        return 2

    marked = [it for it in items if it.get("_say")]
    target = marked[0] if marked else next(
        (it for it in items if it.get("action") != "end_turn"), None
    )
    if target is None:
        target = items[-1]
    if a.thought:
        target["thought"] = a.thought
    if a.meme:
        target["meme"] = a.meme
    for it in items:
        it.pop("_say", None)

    spec = json.dumps({"actions": items}, ensure_ascii=False)
    if a.dry:
        print(spec)
        return 0

    sts2.cmd_batch(spec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
