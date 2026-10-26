# -*- coding: utf-8 -*-
"""执行模型的结构化计划（seq + say），把下一回合的局面包一起回给你。

模型输出（写 tmp_plan.json，UTF-8）
----------------------------------
{
  "say": ["它 16，痛击加一刀刚好带走。", "另一只吃尸体，这回合没人打我。"],
  "seq": [
    {"c": "BASH", "t": 0},
    {"c": "STRIKE_IRONCLAD", "t": 0},
    {"p": 1, "t": 0},              // 药水槽 1，打 0 号
    {"end": 1}
  ],
  "fx": {"e": 0, "hp": 62},        // 可选：自报回合末能量/血量，错了解释器会打回
  "auto": {"turns": 3, "stop_hp": 40}   // 可选：杂怪本地连打
}

· c = card_id（可写 `STRIKE_IRONCLAD` / `STRIKE+` 指升级版；不用写下标）
· t = 敌人下标（可省：默认打威胁最大的活怪；AOE 不写）
· p = 药水槽位（t 可选，targeted 药水必须给）
· end 必须是最后一步；不写 end = 只打到一半就停（等抽牌/Buff 落地时用）
· say 1–3 句：第一句随本回合播，其余排队；写法看 sts2-caster
· fx 可选：只支持 e/hp/take（硬校验）、draw/exh/self（软提醒）

输出（stdout 最后一行）
----------------------
{"ok": true, "fx": {...}, "ctx": {...下一回合局面包...}}
出错时 ok=false，err 说明，ctx 仍是当前局面；**一张都不会打**（除非 batch 中途 STOP）。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import sts2  # noqa: E402
import ctx as ctxmod  # noqa: E402
import sim  # noqa: E402


# ---------------------------------------------------------------- 输入规整
def normalize(spec: dict) -> tuple[list, list, dict, dict]:
    """兼容 v19 的 {plan:[{a:play,id,t}]} 和 v20 的 {seq:[{c:...}]}。"""
    seq = spec.get("seq")
    if seq is None:
        seq = []
        for s in spec.get("plan") or spec.get("actions") or []:
            if not isinstance(s, dict):
                continue
            a = str(s.get("a") or s.get("action") or "")
            if a == "end":
                seq.append({"end": 1})
            elif a == "potion":
                seq.append({"p": sts2._int(s.get("slot", s.get("option_index")), 0),
                            "t": s.get("t", s.get("target_index"))})
            elif a in ("play", "play_card"):
                seq.append({"c": s.get("id") or s.get("card_id"),
                            "t": s.get("t", s.get("target_index"))})
    say = spec.get("say") or []
    if isinstance(say, dict):
        say = [say]
    lines = []
    for x in say:
        if isinstance(x, str):
            lines.append({"t": x, "m": ""})
        elif isinstance(x, dict):
            lines.append({"t": str(x.get("t") or x.get("thought") or ""),
                          "m": str(x.get("m") or x.get("meme") or "")})
    fx = spec.get("fx") or {}
    auto = spec.get("auto") or {}
    return seq, lines, fx, auto


def _actions(steps: list) -> list:
    out = []
    for s in steps:
        if s["k"] == "end":
            out.append({"action": "end_turn"})
        elif s["k"] == "p":
            a = {"action": "use_potion", "option_index": sts2._int(s["p"], 0)}
            if s.get("t") is not None:
                a["target_index"] = sts2._int(s["t"], 0)
            out.append(a)
        else:
            a = {"action": "play_card", "card_id": s["id"]}
            if s.get("t") is not None:
                a["target_index"] = sts2._int(s["t"], 0)
            out.append(a)
    return out


def _run_batch(actions: list) -> tuple[list, str | None]:
    spec = {"actions": actions, "skip_budget": True}
    err = None
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            sts2.cmd_batch(json.dumps(spec, ensure_ascii=False))
    except SystemExit as exc:
        err = f"batch exit {exc.code}"
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    log = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    return log, err


def _attach_say(actions: list, lines: list) -> None:
    if not actions or not lines:
        return
    first = lines[0]
    if isinstance(first, str):
        first = {"t": first, "m": ""}
    actions[0]["thought"] = first.get("t", "")
    actions[0]["meme"] = first.get("m", "")


def _next_ctx(mem: dict | None = None) -> dict:
    return ctxmod.build_with_mem(sts2.get_state(), mem if mem is not None else ctxmod._load_mem())


# ---------------------------------------------------------------- 校验
def validate_plan(st: dict, seq: list) -> tuple[bool, str, sim.Sim, list, bool]:
    ok, err, sm, steps = sim.validate(st, seq)
    if not ok:
        return False, err, sm, steps, False
    ended = bool(steps and steps[-1].get("k") == "end")
    return True, "", sm, steps, ended


def forecast(st: dict, sm: sim.Sim, steps: list, ended: bool) -> dict:
    """本地账本：模型可以用它对账，也用来校验自报 fx。"""
    if ended:
        res = sm.end_turn()
        hp, take = res["hp_after"], res["taken"]
    else:
        hp, take = sm.hp, None
    kills = [e["i"] for e in sm.enemies if e["dead"]]
    stun = [e["i"] for e in sm.enemies if e["stunned"] and not e["dead"]]
    fx = {"e": sm.energy, "hp": hp, "draw": sm.draws, "exh": sm.exhausts}
    if take is not None:
        fx["take"] = take
    if sm.selfhp:
        fx["self"] = sm.selfhp
    if kills:
        fx["kill"] = kills
    if stun:
        fx["stun"] = stun
    return fx


def fx_error(got: dict, want: dict) -> str | None:
    """模型自报的硬资源（费/血/掉血）必须和本地账本一致；不写 fx 就不查。"""
    for k, label in (("e", "回合末能量"), ("hp", "回合末血量"), ("take", "本回合净掉血")):
        if k in got and k in want:
            if sts2._int(got[k], 0) != sts2._int(want[k], 0):
                return f"fx.{k} 不对：你写 {got[k]}，实际 {want[k]}（{label}）"
    return None


# ---------------------------------------------------------------- 回合记忆
TURN_MEMO = os.path.join(ROOT, "logs", ".act_turn.json")


def _turn_of(st: dict):
    t = (st.get("combat") or {}).get("turn")
    return sts2._int(t, 0) if t is not None else None


def _read_memo() -> dict:
    try:
        with open(TURN_MEMO, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_memo(turn, mid: bool) -> None:
    try:
        os.makedirs(os.path.dirname(TURN_MEMO), exist_ok=True)
        with open(TURN_MEMO, "w", encoding="utf-8") as f:
            json.dump({"turn": turn, "mid": bool(mid)}, f)
    except Exception:
        pass


def _end_only(seq: list) -> bool:
    return len(seq) == 1 and isinstance(seq[0], dict) and bool(seq[0].get("end"))


def _stale_end(st: dict, seq: list) -> str | None:
    """上一次停在 mid、回合却已经换了：再发 end 会把新回合整个空过（run60 白吃 16 血）。"""
    if st.get("screen") != "COMBAT" or not _end_only(seq):
        return None
    cur, memo = _turn_of(st), _read_memo()
    if cur is not None and memo.get("mid") and memo.get("turn") != cur:
        return f"回合已切换（turn {memo.get('turn')}→{cur}）：上一手停在 mid，这里再 end 会空过整回合；先读 ctx 出牌"
    return None


# ---------------------------------------------------------------- 执行
def _execute(st: dict, seq: list, lines: list, fx: dict) -> dict:
    lines = [{"t": x, "m": ""} if isinstance(x, str) else x for x in (lines or []) if x]
    ok, err, sm, steps, ended = validate_plan(st, seq)
    if not ok:
        out = {"ok": False, "err": err, "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}
        return out

    want = forecast(st, sm, steps, ended)
    bad = fx_error(fx or {}, want)
    if bad:
        out = {"ok": False, "err": bad, "fx": want,
               "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}
        return out

    actions = _actions(steps)
    if not actions:
        return {"ok": False, "err": "seq 里没有动作（要结束回合就写 {\"end\":1}）",
                "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}
    _attach_say(actions, lines)
    log, run_err = _run_batch(actions)

    out = {"ok": run_err is None, "fx": want}
    if run_err:
        out["err"] = run_err
        tail = [x for x in log if ("STOP" in x or "BUDGET" in x or "TARGET" in x or "FIX" in x)]
        if tail:
            out["log"] = tail[-2:]
    elif sm.retargets:
        out["retarget"] = sm.retargets
    if not ended:
        out["mid"] = 1
    out["ctx"] = _next_ctx()

    # 第一句已随动作播；其余排队（不阻塞本回合）
    for ln in lines[1:]:
        txt = "".join([ln.get("t") or "", ln.get("m") or ""])
        if txt:
            try:
                sts2.overlay_say("combat", txt, "", st)
            except Exception:
                pass
    return out


def _auto(turns: int, stop_hp: int, lines: list) -> dict:
    """杂怪本地连打：每回合用 sim 搜索的最优线，不交药水。"""
    log = []
    spoke = False
    failed = False
    for n in range(max(1, turns)):
        st = sts2.get_state()
        if st.get("screen") != "COMBAT":
            log.append(f"AUTO T{n + 1}: 不是战斗（{st.get('screen')}），停")
            break
        hp = sts2._int(((st.get("combat") or {}).get("player") or {}).get("current_hp"), 0)
        if stop_hp and hp <= stop_hp:
            log.append(f"AUTO T{n + 1}: HP {hp} ≤ {stop_hp}，停")
            break
        plans = sim.search(st, max_plans=1)
        if not plans:
            log.append(f"AUTO T{n + 1}: 没算到可行线，改手动")
            break
        seq = []
        for step in plans[0]["seq"]:
            if str(step[0]).startswith("@"):
                continue  # auto 不替模型交药
            seq.append({"c": step[0], "t": step[1] if len(step) > 1 else None})
        seq.append({"end": 1})
        ok, err, sm, steps, ended = validate_plan(st, seq)
        if not ok:
            log.append(f"AUTO T{n + 1}: 计划失效 {err}，停")
            failed = True
            break
        actions = _actions(steps)
        if lines and not spoke:
            _attach_say(actions, lines)
            spoke = True
        _lg, run_err = _run_batch(actions)
        log.append(f"AUTO T{n + 1}: 伤害{plans[0]['dmg']} 斩{plans[0].get('kill', [])} 净掉{plans[0]['taken']}")
        if run_err:
            log.append(f"AUTO T{n + 1}: {run_err}")
            failed = True
            break
    out = {"ok": not failed, "log": log}
    out["ctx"] = _next_ctx()
    return out


def main():
    ap = argparse.ArgumentParser(description="执行结构化计划（seq + say）")
    ap.add_argument("--file", help="计划 JSON 文件（不给则读 stdin）")
    args = ap.parse_args()
    raw = open(args.file, encoding="utf-8-sig").read() if args.file else sys.stdin.read()
    try:
        spec = json.loads(raw)
    except ValueError as exc:
        print(json.dumps({"ok": False, "err": f"plan 不是合法 JSON: {exc}"}, ensure_ascii=False))
        sys.exit(2)
    if not isinstance(spec, dict):
        print(json.dumps({"ok": False, "err": "顶层必须是对象：{say,seq,fx,auto}"}, ensure_ascii=False))
        sys.exit(2)

    seq, lines, fx, auto = normalize(spec)
    st = sts2.get_state()
    if st.get("screen") != "COMBAT":
        print(json.dumps({"ok": False, "err": f"当前不是战斗（{st.get('screen')}）",
                          "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}, ensure_ascii=False))
        return
    stale = _stale_end(st, seq)
    if stale:
        print(json.dumps({"ok": False, "err": stale,
                          "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}, ensure_ascii=False))
        return
    if auto:
        out = _auto(sts2._int(auto.get("turns"), 1), sts2._int(auto.get("stop_hp"), 0), lines)
    elif not seq:
        out = {"ok": False, "err": "缺 seq（要结束回合就写 [{\"end\":1}]）",
               "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}
    else:
        out = _execute(st, seq, lines, fx)
    _write_memo(_turn_of(sts2.get_state()), bool(out.get("mid")))
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
