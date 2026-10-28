# -*- coding: utf-8 -*-
"""动作回执自测：接口说 completed/pending 都不算数，只看前后状态差分。

背景（run105 T7）：`use_potion` 返回 status=pending，act 当成功记账，实际药水还在槽里；
同局 SPITE 被 sim 多算一段，连「敌人是否掉血」都没人核对。历史账本 19,664 步里
307 步 pending（药水 72、出牌 109…）全被当成功。

本测试守四件事：
  1. status 语义：completed=已结算 / pending=排队中（结果未知）/ 其它=unknown；
  2. 判定只认差分：药水槽、敌人血量、手牌、能量、格挡、buff、界面；
  3. 假成功（completed 但状态没变）必须报 no_effect，pending 无变化报 unverified；
  4. 回执丢失时 cmd_batch 退出码 4，act 返回 ok=False 并给可读原因。

用法：python tools/test_action_receipt.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("STS2_DECISION_LOG", "0")

import sts2  # noqa: E402
import act  # noqa: E402

ok = True


def check(name, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


def potion(pid="FIRE_POTION", idx=0, occupied=True):
    return {"index": idx, "potion_id": pid, "name": pid, "occupied": occupied,
            "can_use": True, "requires_target": True}


def st_combat(hp=44, potions=None, turn=3, energy=3, hand=None, powers=None):
    return {
        "screen": "COMBAT",
        "run": {"current_hp": 60, "max_hp": 80, "potions": potions if potions is not None else [potion()]},
        "combat": {"turn": turn,
                   "player": {"current_hp": 60, "max_hp": 80, "energy": energy, "block": 0,
                              "powers": powers or []},
                   "hand": hand if hand is not None else [{"card_id": "BASH", "energy_cost": 2}],
                   "enemies": [{"index": 0, "enemy_id": "X", "current_hp": hp, "max_hp": 68}]},
    }


print("=== 1) status 语义 ===")
check("completed → ok", sts2.status_kind("completed"), "ok")
check("pending → pending", sts2.status_kind("pending"), "pending")
check("None → unknown", sts2.status_kind(None), "unknown")
check("乱码 → unknown", sts2.status_kind("weird"), "unknown")

print()
print("=== 2) 药水：pending 且没消耗 ≠ 用掉了 ===")
b = st_combat()
rc = sts2.verify_effect("use_potion", {"option_index": 0}, b, st_combat(), "pending")
check("pending + 药水还在 → unverified", rc["effect"], "unverified")
rc = sts2.verify_effect("use_potion", {"option_index": 0}, b, st_combat(potions=[]), "pending")
check("pending + 槽空了 → applied", rc["effect"], "applied")
rc = sts2.verify_effect("use_potion", {"option_index": 0}, b, st_combat(), "completed")
check("completed + 药水还在 → no_effect（假成功）", rc["effect"], "no_effect")
rc = sts2.verify_effect("use_potion", {"option_index": 0}, b, st_combat(hp=24), "pending")
check("pending + 敌人掉血 → applied", rc["effect"], "applied")

print()
print("=== 3) 出牌：敌人是否真的受伤 ===")
rc = sts2.verify_effect("play_card", {"target_index": 0}, st_combat(hp=44), st_combat(hp=36), "completed")
check("敌血 44→36 → applied", rc["effect"], "applied")
rc = sts2.verify_effect("play_card", {"target_index": 0}, st_combat(hp=44), st_combat(hp=44), "completed")
check("completed 但敌血没掉 → no_effect", rc["effect"], "no_effect")
rc = sts2.verify_effect("play_card", {"target_index": 0}, st_combat(hp=44, energy=3),
                        st_combat(hp=44, energy=1), "pending")
check("pending 但能量 3→1 → applied", rc["effect"], "applied")
rc = sts2.verify_effect("play_card", {"target_index": 0}, st_combat(hp=44), st_combat(hp=44), "pending")
check("pending 且无变化 → unverified", rc["effect"], "unverified")
rc = sts2.verify_effect("play_card", {"target_index": 0}, st_combat(hp=44), st_combat(hp=44), None)
check("status 缺失且无变化 → unverified", rc["effect"], "unverified")

print()
print("=== 4) 结束回合 ===")
rc = sts2.verify_effect("end_turn", {}, st_combat(turn=3), st_combat(turn=4), "completed")
check("回合 3→4 → applied", rc["effect"], "applied")
rc = sts2.verify_effect("end_turn", {}, st_combat(turn=3), st_combat(turn=3), "pending")
check("pending 但回合没变 → unverified", rc["effect"], "unverified")

print()
print("=== 5) cmd_batch：药水 pending 没消耗 → 退出码 4 ===")
kept = {"st": st_combat()}
old = (sts2.get_state, sts2.act, sts2.overlay_say, sts2.log, sts2._meme_hint)
try:
    sts2.get_state = lambda: kept["st"]

    def fake_act(action, **params):
        return {"status": "pending", "message": "Action queued but state is still transitioning."}

    sts2.act = fake_act
    sts2.overlay_say = lambda *a, **k: None
    sts2.log = lambda *a, **k: None
    sts2._meme_hint = lambda *a, **k: None
    buf = io.StringIO()
    code = None
    try:
        with contextlib.redirect_stdout(buf):
            sts2.cmd_batch(json.dumps({"actions": [
                {"action": "use_potion", "option_index": 0, "target_index": 0}], "skip_budget": True}))
    except SystemExit as e:
        code = e.code
    out = buf.getvalue()
    check("退出码 4", code, 4)
    check("打印 RECEIPT", any(l.startswith("RECEIPT ") for l in out.splitlines()), True)
    check("打印 RECEIPT_LOST", "RECEIPT_LOST" in out, True)
    check("回执 ok=false", '"ok":false' in out, True)
finally:
    (sts2.get_state, sts2.act, sts2.overlay_say, sts2.log, sts2._meme_hint) = old

print()
print("=== 5b) pending 但动画慢半拍：追读后能看到变化就算 applied ===")
base = st_combat()
seen = {"n": 0}


def slow_state():
    seen["n"] += 1
    return base if seen["n"] <= 3 else st_combat(potions=[])


old = (sts2.get_state, sts2.act, sts2.overlay_say, sts2.log, sts2._meme_hint)
try:
    sts2.get_state = slow_state
    sts2.act = lambda action, **params: {"status": "pending", "message": "queued"}
    sts2.overlay_say = lambda *a, **k: None
    sts2.log = lambda *a, **k: None
    sts2._meme_hint = lambda *a, **k: None
    buf = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf):
            sts2.cmd_batch(json.dumps({"actions": [
                {"action": "use_potion", "option_index": 0, "target_index": 0}], "skip_budget": True}))
    except SystemExit as e:
        code = e.code
    out = buf.getvalue()
    check("慢半拍也放行", code, 0)
    check("回执 ok=true", '"ok":true' in out, True)
    check("确实追读过", seen["n"] >= 4, True)
finally:
    (sts2.get_state, sts2.act, sts2.overlay_say, sts2.log, sts2._meme_hint) = old

print()
print("=== 6) act：回执丢失 → ok=False，不冒充成功 ===")
old_batch = act._run_batch
try:
    lost = ([], "batch exit 4", {"ok": False, "items": [
        {"i": 0, "a": "use_potion", "status": "pending", "effect": "unverified", "why": "状态无变化"}]})
    act._run_batch = lambda acts: lost
    st = st_combat(potions=[potion(idx=0)])
    sts2.get_state = lambda: st
    sts2.get_state_settled = lambda *a, **k: st
    out = act._execute(st, [{"c": "BASH", "t": 0}, {"end": 1}], ["先斩再说。"], {},
                       {"allow_take": True})
    check("ok=False", out.get("ok"), False)
    check("带 receipt", bool(out.get("receipt")), True)
    check("原因可读", "batch exit 4" in (out.get("err") or ""), True)

    good = ([], None, {"ok": True, "items": [
        {"i": 0, "a": "play_card", "status": "completed", "effect": "applied", "why": "敌人血量"}]})
    act._run_batch = lambda acts: good
    out2 = act._execute(st, [{"c": "BASH", "t": 0}, {"end": 1}], ["先斩再说。"], {},
                        {"allow_take": True})
    check("回执正常 → ok=True", out2.get("ok"), True)
    check("回执挂在返回值上", (out2.get("receipt") or {}).get("ok"), True)
finally:
    act._run_batch = old_batch

print()
print("=== 7) _run_batch 解析 RECEIPT 行 ===")
old_cb = act.sts2.cmd_batch
try:
    def fake_batch(spec):
        print('RECEIPT {"ok":true,"items":[{"i":0,"a":"end_turn","effect":"applied"}]}')
    act.sts2.cmd_batch = fake_batch
    log, err, rc = act._run_batch([{"action": "end_turn"}])
    check("无错误", err, None)
    check("解析出回执", (rc or {}).get("ok"), True)
    check("回执行保留在 log", any(x.startswith("RECEIPT ") for x in log), True)
finally:
    act.sts2.cmd_batch = old_cb

print()
print("=== 8) 动作日志带 effect 字段（药水到底用没用可追溯）===")
tmp = os.path.join(tempfile.gettempdir(), "sts2_test_action_receipt.jsonl")
if os.path.exists(tmp):
    os.remove(tmp)
old_sp = sts2._session_path
try:
    sts2._session_path = lambda action: tmp
    sts2.log("use_potion", {"option_index": 0}, st_combat(), st_combat(potions=[]),
             {"status": "pending"}, "", "", effect="applied")
    row = json.loads(open(tmp, encoding="utf-8").read().strip())
    check("journal.effect", row.get("effect"), "applied")
    check("journal.status 原样保留", row.get("status"), "pending")
finally:
    sts2._session_path = old_sp
    try:
        os.remove(tmp)
    except OSError:
        pass

print()
print("ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
