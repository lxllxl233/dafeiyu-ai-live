# -*- coding: utf-8 -*-
"""分层记忆（memory/journal）回归：结构化摘要能复述「我是谁、牌组靠什么、血怎么掉的」。

用法：python tools/test_memory.py
"""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import sts2  # noqa: E402

STATE = {
    "screen": "MAP", "run_id": "run_test",
    "run": {
        "character_id": "IRONCLAD", "ascension": 10, "act_id": 0, "floor": 12,
        "current_hp": 17, "max_hp": 85, "gold": 59,
        "deck": [
            {"card_id": "STRIKE_IRONCLAD", "card_type": "Attack"},
            {"card_id": "STRIKE_IRONCLAD", "card_type": "Attack"},
            {"card_id": "DEFEND_IRONCLAD", "card_type": "Skill"},
            {"card_id": "BASH", "card_type": "Attack"},
            {"card_id": "DISMANTLE", "card_type": "Attack"},
            {"card_id": "DISMANTLE", "card_type": "Attack"},
            {"card_id": "THUNDERCLAP", "card_type": "Attack"},
            {"card_id": "IMPERVIOUS", "card_type": "Skill"},
            {"card_id": "DEBT", "card_type": "Curse"},
        ],
        "relics": [{"relic_id": "BURNING_BLOOD"}, {"relic_id": "GREMLIN_HORN"}],
    },
}

ENTRIES = [
    {"ts": "t1", "action": "choose_map_node", "params": {}, "floor": 1, "hp": 64, "gold": 99},
    {"ts": "t2", "action": "play_card", "params": {"card_index": 0}, "floor": 1, "hp": 64, "gold": 99},
    {"ts": "t3", "action": "save_and_quit", "params": {}, "floor": 12, "hp": 17, "gold": 59},
    {"ts": "t4", "action": "continue_run", "params": {}, "floor": 12, "hp": 17, "gold": 59},
    {"ts": "t5", "action": "choose_map_node", "params": {}, "floor": 12, "hp": 17, "gold": 59,
     "thought": "17 血不能接确定的小怪"},
]


def main():
    tmp = tempfile.mkdtemp()
    jsonl = os.path.join(tmp, "run_test.jsonl")
    with open(jsonl, "w", encoding="utf-8") as f:
        for e in ENTRIES:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    ptr = os.path.join(tmp, "current_session.json")
    with open(ptr, "w", encoding="utf-8") as f:
        json.dump({"path": jsonl, "started": "t0"}, f)

    old_ptr, old_state = sts2.SESSION_PTR, sts2.get_state
    sts2.SESSION_PTR = ptr
    sts2.get_state = lambda: STATE
    try:
        lines = sts2.memory_digest(STATE)
        text = "\n".join(lines)
        for ln in lines:
            print(ln)
        print("---")
        sts2.cmd_journal(floor=12, last=3)
    finally:
        sts2.SESSION_PTR, sts2.get_state = old_ptr, old_state

    checks = [
        ("头部含难度与血量", "A10" in text and "HP 17/85" in text),
        ("牌组统计", "atk4" in text.replace("atk4", "atk4") or "DECK 9:" in text),
        ("关键牌去重带数量", "DISMANTLEx2" in text),
        ("诅咒单列", "CURSES DEBT" in text),
        ("血线曲线", "HP_CURVE" in text and "f12:17" in text),
        ("SL 计数", "SL 1/3" in text),
        ("低血风险提示", "RISK" in text and "血闸" in text),
        ("摘要够短", len(text.encode("utf-8")) < 1200),
    ]
    ok = True
    for name, passed in checks:
        print(("PASS " if passed else "FAIL ") + name)
        ok = ok and passed
    print(f"bytes: {len(text.encode('utf-8'))} / lines: {len(lines)}")
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
