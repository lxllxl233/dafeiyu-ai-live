# -*- coding: utf-8 -*-
"""P0-1 决策账本回归测试：离线、不连游戏、不读真实 logs。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import contextlib
import io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import act  # noqa: E402
import decision_log  # noqa: E402
import replay  # noqa: E402
import sts2  # noqa: E402

ok = True


def check(name, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def card(cid, cost, dmg=None, target="AnyEnemy", text=""):
    dv = []
    if dmg is not None:
        dv.append({"name": "Damage", "current_value": dmg})
    return {
        "card_id": cid, "name": cid, "energy_cost": cost, "upgraded": False,
        "playable": True, "costs_x": False,
        "requires_target": dmg is not None and target != "AllEnemies",
        "target_type": target, "dynamic_values": dv, "resolved_rules_text": text,
    }


def enemy(eid="A", hp=30, atk=10, idx=0):
    return {
        "index": idx, "enemy_id": eid, "name": eid, "current_hp": hp, "max_hp": hp,
        "block": 0, "is_alive": True, "powers": [],
        "intents": [{"intent_type": "ATTACK", "damage": atk, "hits": 1,
                     "total_damage": atk}],
    }


def combat_state(hand=None, hp=60, energy=3, turn=1, screen="COMBAT"):
    return {
        "screen": screen,
        "run_id": "run_test",
        "available_actions": ["play_card", "end_turn"],
        "run": {
            "run_id": "run_test", "current_hp": hp, "max_hp": 80, "max_energy": 3,
            "act_id": 0, "floor": 7, "ascension": 10, "gold": 99,
            "deck": [{"card_id": "STRIKE_IRONCLAD"}, {"card_id": "DEFEND_IRONCLAD"}],
            "potions": [{"index": 0, "potion_id": "FIRE_POTION", "occupied": True}],
            "relics": [{"relic_id": "BURNING_BLOOD"}],
        },
        "combat": {
            "turn": turn,
            "player": {"current_hp": hp, "max_hp": 80, "energy": energy,
                       "block": 0, "powers": []},
            "hand": hand if hand is not None else [card("STRIKE_IRONCLAD", 1, dmg=6)],
            "enemies": [enemy()],
        },
    }


def game_over_state(win=True):
    s = combat_state(hp=0, energy=0, screen="GAME_OVER")
    s["game_over"] = {"is_victory": win, "floor": 17}
    s["run"]["current_hp"] = 0
    s["run"]["floor"] = 17
    s["run"]["gold"] = 211
    s["run"]["deck"] = [{"card_id": "STRIKE_IRONCLAD"}, {"card_id": "BASH"}]
    return s


def main():
    global ok
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["STS2_DECISION_LOG"] = "1"
        os.environ["STS2_DECISION_LOG_DIR"] = tmp
        os.environ["STS2_DECISION_STATE"] = "1"

        calls = []
        after = combat_state(hp=60, turn=2)

        def batch_ok(actions):
            calls.append(actions)
            return ["[0] play_card STRIKE_IRONCLAD"], None

        def batch_err(actions):
            return ["STOP 缺牌"], "STOP 缺牌"

        old = (act._run_batch, act.sts2.get_state, act.ctxmod.build_with_mem,
               act.ctxmod._load_mem)
        act._run_batch = batch_ok
        act.sts2.get_state = lambda: after
        act.ctxmod.build_with_mem = lambda st, mem: {"v": 3, "screen": st.get("screen")}
        act.ctxmod._load_mem = lambda: {}
        try:
            before = combat_state()
            seq = [{"c": "STRIKE_IRONCLAD", "t": 0}]
            out = act._execute(before, seq, ["第一句"], {"hp": 60})
            check("执行成功", out.get("ok"), True)
            check("batch 收到动作", len(calls), 1)
            recs = decision_log.read_decisions()
            check("写了一条决策", len(recs), 1)
            r = recs[0]
            check("run_id", r.get("run_id"), "run_test")
            check("act", r.get("act"), 1)
            check("floor", r.get("floor"), 7)
            check("outcome", r.get("outcome"), "executed")
            check("chosen seq", r.get("input", {}).get("seq"), seq)
            check("steps 有牌", (r.get("steps") or [{}])[0].get("id"), "STRIKE_IRONCLAD")
            check("未写 end 标 mid", r.get("mid"), True)
            check("候选计划非空", bool(r.get("plans")), True)
            check("选中计划可对上", bool(r.get("chosen_plan")), True)
            rr = replay.verify_record(r)
            check("离线重放通过", rr.get("ok"), True)
            rs = replay.verify_record(r, search=True)
            check("重算 score 一致", rs.get("ok"), True)
            check("重放能看到选中计划", rr.get("chosen_plan"), True)
            check("缺 state 的重放拒绝",
                  replay.verify_record({"outcome": "executed", "input": {"seq": []}}).get("ok"),
                  False)
            check("forecast 有 hp", (r.get("forecast") or {}).get("hp"), 60)
            check("before 手牌", (r.get("before", {}).get("hand") or [{}])[0].get("id"),
                  "STRIKE_IRONCLAD")
            check("after 回合", (r.get("after") or {}).get("turn"), 2)
            check("state 快照存在", bool(r.get("state", {}).get("combat")), True)
            check("batch 日志保留", bool((r.get("batch") or {}).get("log")), True)

            # 1b) auto 连打也落账（mode=auto），避免杂怪决策在账本里消失
            calls.clear()
            act.sts2.get_state = lambda: combat_state(hp=60, turn=2)
            auto_out = act._auto(1, 0, [])
            check("auto 执行", auto_out.get("ok"), True)
            ra = decision_log.read_decisions()[-1]
            check("auto 落账", ra.get("mode"), "auto")
            check("auto outcome", ra.get("outcome"), "executed")
            act.sts2.get_state = lambda: after

            # 2) 非法牌：账本必须记 rejected_validate，不写 executed
            calls.clear()
            bad = act._execute(before, [{"c": "NOPE"}], [], {})
            check("非法牌被拒", bad.get("ok"), False)
            r2 = decision_log.read_decisions()[-1]
            check("拒绝原因落账", r2.get("outcome"), "rejected_validate")
            check("拒绝错误非空", bool(r2.get("err")), True)
            check("拒绝不执行 batch", len(calls), 0)

            # 3) fx 不符：账本必须记 rejected_fx + want
            calls.clear()
            badfx = act._execute(before, seq, [], {"hp": 999})
            check("fx 不符被拒", badfx.get("ok"), False)
            r3 = decision_log.read_decisions()[-1]
            check("fx 拒绝落账", r3.get("outcome"), "rejected_fx")
            check("fx 保留预测", (r3.get("forecast") or {}).get("hp"), 60)

            # 4) batch 报错：账本记 batch_error，不伪装成 executed
            act._run_batch = batch_err
            badbatch = act._execute(before, seq, [], {})
            check("batch 失败返回 false", badbatch.get("ok"), False)
            r4 = decision_log.read_decisions()[-1]
            check("batch 失败落账", r4.get("outcome"), "batch_error")
            check("batch 错误保留", "STOP" in (r4.get("err") or ""), True)

            # 5) GAME_OVER：写 run summary，胜/负都写
            act._run_batch = batch_ok
            act.sts2.get_state = lambda: game_over_state(win=True)
            win = act._execute(before, [{"end": 1}], [], {})
            check("结束回合返回成功", win.get("ok"), True)
            summary = json.load(open(os.path.join(tmp, "run_summary_run_test.json"),
                                     encoding="utf-8"))
            check("summary 结果", summary.get("result"), "victory")
            check("summary 楼层", summary.get("floor"), 17)
            check("summary 金币", summary.get("gold"), 211)
            check("summary 牌组张数", summary.get("deck_size"), 2)
            check("summary 决策回合", summary.get("decisions", 0) >= 1, True)
            check("summary 掉血字段", "hp_lost" in summary, True)
            check("summary 药水字段", "potions_used" in summary, True)
            check("summary SL 字段", "sl" in summary, True)
            check("latest summary 存在",
                  os.path.exists(os.path.join(tmp, "run_summary.json")), True)

            # 6) 关闭账本：不再新增记录
            os.environ["STS2_DECISION_LOG"] = "0"
            n = len(decision_log.read_decisions())
            act._execute(before, seq, [], {})
            check("关闭后不新增", len(decision_log.read_decisions()), n)

            # 6b) 账本自身炸了也不能影响出牌
            os.environ["STS2_DECISION_LOG"] = "1"
            old_record = decision_log.record_decision

            def boom(*a, **k):
                raise RuntimeError("ledger down")

            decision_log.record_decision = boom
            try:
                out_boom = act._execute(before, seq, [], {})
                check("账本异常仍出牌", out_boom.get("ok"), True)
            finally:
                decision_log.record_decision = old_record

            # 6c) 坏行只跳过，不拖垮整个账本
            with open(decision_log.decisions_path(), "a", encoding="utf-8") as f:
                f.write("{not json}\n")
            check("坏行被跳过", len(decision_log.read_decisions()) >= 1, True)

            # 7) state 关闭：记录变小，但核心字段仍在
            os.environ["STS2_DECISION_STATE"] = "0"
            act._execute(before, seq, [], {})
            r7 = decision_log.read_decisions()[-1]
            check("state 可关闭", "state" in r7, False)
            check("关闭 state 仍有 before", bool(r7.get("before")), True)

            # 8) 败局 summary 也写，且 result=defeat
            lose = game_over_state(win=False)
            lose["run_id"] = "run_lose"
            lose["run"]["run_id"] = "run_lose"
            summary_lose = decision_log.record_run_summary(lose)
            check("败局 summary 存在", bool(summary_lose), True)
            check("败局 result", (summary_lose or {}).get("result"), "defeat")
            check("败局 summary 文件",
                  os.path.exists(os.path.join(tmp, "run_summary_run_lose.json")), True)

            # 9) report：坏 JSONL 不崩，且带最近一局 summary
            sess = os.path.join(tmp, "run_fake.jsonl")
            with open(sess, "w", encoding="utf-8") as f:
                f.write(json.dumps({"ts": "t", "floor": 17, "action": "end_turn",
                                    "screen_before": "COMBAT", "screen_after": "GAME_OVER",
                                    "status": "completed"}, ensure_ascii=False) + "\n")
                f.write("{broken json}\n")
            old_ptr = sts2.SESSION_PTR
            sts2.SESSION_PTR = os.path.join(tmp, "current_session.json")
            with open(sts2.SESSION_PTR, "w", encoding="utf-8") as f:
                json.dump({"path": sess}, f)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    sts2.cmd_report()
                rep = open(os.path.splitext(sess)[0] + ".md", encoding="utf-8").read()
                check("report 不因坏行崩溃", "动作数：1" in rep, True)
                check("report 带结算段", "最近一局结算" in rep, True)
                check("report 带败局", "defeat" in rep, True)
            finally:
                sts2.SESSION_PTR = old_ptr

            mem_lines = sts2.memory_digest(combat_state())
            check("memory 带决策计数", any(x.startswith("DECISIONS ") for x in mem_lines), True)
        finally:
            (act._run_batch, act.sts2.get_state, act.ctxmod.build_with_mem,
             act.ctxmod._load_mem) = old
            for k in ("STS2_DECISION_LOG", "STS2_DECISION_LOG_DIR", "STS2_DECISION_STATE"):
                os.environ.pop(k, None)

    print("ALL OK" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
