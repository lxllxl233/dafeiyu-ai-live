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
· 抽牌后手牌未知：只发抽牌牌本身（不写 end），读返回的新 ctx 再排下一手；
  若在同一 seq 里写“抽牌牌 + 不在当前手里的牌”，整手拒绝并提示 split_at_draw
· say 1–3 句：第一句随本回合播，其余排队；写法看 sts2-caster
· fx 可选：只支持 e/hp/take（通常硬校验；抽牌+end 的 hp/take 只作近似）、draw/exh/self（软提醒）

输出（stdout 最后一行）
----------------------
{"ok": true, "fx": {...}, "ctx": {...下一回合局面包...}}
不写 end：另带 {"mid":1,"next":"read_ctx","mid_reason":"..."}；抽牌后必须重读 ctx。
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
import decision_log  # noqa: E402
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


def _next_ctx(mem: dict | None = None, st: dict | None = None) -> dict:
    return ctxmod.build_with_mem(st if st is not None else sts2.get_state(),
                                 mem if mem is not None else ctxmod._load_mem())


def _candidate_plans(st: dict) -> list:
    """只给账本看：不写 mem、不改状态；失败不冒充有候选。"""
    if not isinstance(st, dict) or st.get("screen") != "COMBAT":
        return []
    try:
        return sim.search(st, max_cards=8, max_plans=5, time_limit=0.8,
                          max_nodes=120_000)
    except Exception:  # noqa: BLE001
        return []


def _log_decision(st: dict, seq: list, lines: list, fx: dict, *, outcome: str,
                  err=None, steps=None, forecast=None, plans=None, batch=None,
                  after=None, retargets=None, mode: str = "model") -> None:
    """账本失败绝不能影响出牌。"""
    try:
        decision_log.record_decision(
            st, seq, lines, fx, outcome=outcome, err=err, steps=steps,
            forecast=forecast, plans=plans, batch=batch, after=after,
            retargets=retargets, mode=mode,
        )
    except Exception:  # noqa: BLE001
        pass


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
    if ended and sm.draws:
        # 抽到的牌不在本地 sim 手里；end_turn 的状态税/掉血只能按旧手牌估。
        fx["draw_unknown"] = sm.draws
    if take is not None:
        fx["take"] = take
    if not sm.alive():
        # 清场这一手：战斗结束时燃烧之血回 6、遗物/死亡结算都会改血量，
        # 本地 sim 只算回合内，血/掉血在这一手不可信（账本 15 条 fx.hp 误报都来自这里）。
        fx["clear"] = 1
    if sm.selfhp:
        fx["self"] = sm.selfhp
    if kills:
        fx["kill"] = kills
    if stun:
        fx["stun"] = stun
    return fx


def fx_error(got: dict, want: dict) -> str | None:
    """模型自报的硬资源（费/血/掉血）必须和本地账本一致；不写 fx 就不查。

    含抽牌的回合：抽到的牌不在本地 sim 手里，end_turn 的血量/状态税只能按旧
    手牌估，不能当作硬账本；此时只验能量，hp/take 由下一回合 ctx 复核。
    """
    keys = [("e", "回合末能量")]
    if not want.get("draw_unknown"):
        keys += [("hp", "回合末血量"), ("take", "本回合净掉血")]
    if want.get("clear"):
        # 清场：战后回血（燃烧之血 6 点）与死亡结算不在本地 sim 里，血量不可硬校验。
        keys = [k for k in keys if k[0] == "e"]
    for k, label in keys:
        if k in got and k in want:
            if sts2._int(got[k], 0) != sts2._int(want[k], 0):
                return (f"fx.{k} 不对：你写 {got[k]}，实际 {want[k]}（{label}）"
                        f" → 改成 \"{k}\": {want[k]} 重发，或把 fx.{k} 删掉让模型自己算")
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


def _write_memo(turn, mid: bool, ended_turn=None) -> None:
    try:
        os.makedirs(os.path.dirname(TURN_MEMO), exist_ok=True)
        data = {"turn": turn, "mid": bool(mid)}
        if ended_turn is not None:
            data["ended_turn"] = ended_turn
        with open(TURN_MEMO, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _end_count(seq: list) -> int:
    n = 0
    for x in seq or []:
        if isinstance(x, str):
            if x.strip().lower() == "end":
                n += 1
        elif isinstance(x, dict) and (x.get("end") or str(x.get("a") or "").lower() == "end"):
            n += 1
    return n


def _end_only(seq: list) -> bool:
    return len(seq) == 1 and _end_count(seq) == 1


def _has_playable(st: dict) -> bool:
    """保守判断：新回合手上有能打出的牌时，禁止用纯 end 白白空过。"""
    try:
        sm = sim.Sim.from_state(st)
        return any(sm.cost_of(c) <= sm.energy for c in sm.hand)
    except Exception:  # noqa: BLE001
        c = st.get("combat") or {}
        energy = sts2._int((c.get("player") or {}).get("energy"), 0)
        for card in c.get("hand") or []:
            if not isinstance(card, dict) or card.get("playable") is False:
                continue
            cost = sts2._int(card.get("energy_cost", card.get("cost")), 99)
            if cost <= energy:
                return True
        return False


def _pre_draw(st: dict) -> bool:
    """刚 end 完的过渡快照：手牌还是空的、牌堆里却有牌（新回合的 5 张还没抽上来）。

    run68 f17：Boss 准备回合，ctx 读到 hand=[]，我照着手牌空发了纯 end，
    结果把整个第 8 回合（3 费 + 5 张牌，Boss 不还手）空过。
    """
    c = st.get("combat") or {}
    if [x for x in (c.get("hand") or []) if isinstance(x, dict)]:
        return False
    try:
        draw, dis, _exh = ctxmod._piles_cards(st)
    except Exception:  # noqa: BLE001
        return False
    return bool(draw or dis)


def _stale_end(st: dict, seq: list) -> str | None:
    """上一次停在 mid、回合却已经换了：再发 end 会把新回合整个空过（run60 白吃 16 血）。"""
    seq = seq or []
    if st.get("screen") != "COMBAT":
        return None
    ends = _end_count(seq)
    if ends > 1:
        return f"seq 里出现 {ends} 次 end；end_turn 只能发一次且必须是最后一步，拒绝执行整手"
    if not _end_only(seq):
        return None
    if _pre_draw(st):
        return ("手牌是空的、抽牌堆/弃牌堆却还有牌：这是上一手 end 之后的过渡快照（新回合还没抽牌），"
                "现在发 end 会把整回合空过；先重读 ctx 确认 turn 变了再出牌")
    cur, memo = _turn_of(st), _read_memo()
    if not isinstance(memo, dict):
        memo = {}
    if cur is not None and memo.get("mid") and memo.get("turn") != cur:
        return f"回合已切换（turn {memo.get('turn')}→{cur}）：上一手停在 mid，这里再 end 会空过整回合；先读 ctx 出牌"
    ended_turn = memo.get("ended_turn")
    if cur is not None and ended_turn is not None:
        if ended_turn == cur:
            return f"turn {cur} 已经 end 过一次：状态还没推进，再发 end 会重复结束；先读 ctx"
        if ended_turn != cur and _has_playable(st):
            return (f"上一手已经 end_turn（turn {ended_turn}→{cur}），新回合手里还有能打的牌；"
                    "先读 ctx 出牌，不要只发 end")
    return None


# ---------------------------------------------------------------- 硬护栏
# run69 的病根：手牌读到的是「过渡快照 / 合并后的残缺列表」，我据此排的牌序
# 会漏掉还在手上的防御牌，于是「有费不叠甲、掉血」；斩杀线算不出来，于是
# 「能斩不斩、多吃一回合」。下面三条在任何一手执行前强制检查，不靠模型自觉。
def _affordable(sm) -> list:
    """(牌, 实费) —— 当前能量下真正打得出来的牌（sim 里打过的牌只是标记 _played，还在手里）。"""
    out = []
    for c in sm.hand:
        if not c.get("play", True) or c.get("_played"):
            continue
        cost = sm.cost_of(c)
        if cost <= sm.energy:
            out.append((c, cost))
    return out


def _is_block_card(c: dict) -> bool:
    if sts2._int(c.get("blk"), 0) > 0:
        return True
    return str(c.get("id") or "") in {
        "BLOOD_WALL", "DEFEND_IRONCLAD", "EXPECT_A_FIGHT", "ARMAMENTS", "SHRUG_IT_OFF",
        "TRUE_GRIT", "SECOND_WIND", "IMPERVIOUS", "FLAME_BARRIER", "ENTRENCH",
        "BODY_SLAM",  # 靠甲打人：先起甲再撞
    }


def _guard_leftover(sm, allow: dict) -> tuple[str | None, dict]:
    """结束回合还剩能量、手上还有牌没打 —— 等于把费扔了（含「有费不叠甲」）。"""
    if allow.get("allow_leftover") or sm.energy <= 0:
        return None, {}
    left = _affordable(sm)
    if not left:
        return None, {}
    blocks = [(c, cost) for c, cost in left if _is_block_card(c)]
    names = "、".join(f"{c['id']}({cost}费)" for c, cost in left[:5])
    if blocks:
        bn = "、".join(f"{c['id']}({cost}费)" for c, cost in blocks[:4])
        return (f"护栏·漏费：结束回合还剩 {sm.energy} 费，手上还有能打的牌 {names}，"
                f"其中 {bn} 可以叠甲。先把费花掉（优先起甲/斩杀）再 end；"
                f"确实要留费就写 {{\"allow_leftover\":1}} 并说明原因"), {"left_n": len(left)}
    return (f"护栏·漏费：结束回合还剩 {sm.energy} 费，手上还能打 {names}。"
            f"先把费花掉再 end；确实要留费就写 {{\"allow_leftover\":1}}"), {"left_n": len(left)}


def _death_damage_risk(st: dict) -> list:
    """敌人身上「死亡时会结算伤害」的遗言（蒸汽喷发/爆炸/亡语…）。

    斩杀不等于安全：瀑布巨兽死时会按蒸汽层数炸一发（run72 实测 44 点），
    没甲没缓冲的斩杀回合可能直接被炸死。
    """
    tags = ("蒸汽喷发", "爆炸", "亡语", "自爆", "爆裂", "死亡时", "死后")
    out = []
    for e in ((st.get("combat") or {}).get("enemies") or []):
        if e.get("dead"):
            continue
        hit = []
        for p in e.get("powers") or []:
            name = p.get("name") if isinstance(p, dict) else str(p)
            if name and any(k in name for k in tags):
                hit.append(name)
        if hit:
            out.append(f"{e.get('id')}({'/'.join(hit)})")
    return out


def _guard_missed_lethal(st: dict, sm, allow: dict) -> str | None:
    """这回合能全斩却不斩：白送一回合挨打（run69 多次「能斩不斩」）。

    只信「能重新校验通过、并且真的把活怪清空」的斩杀线：本地搜索偶尔会给出
    牌序上打不出来的线，拿那种线来拦会误伤，这里逐条复核。
    """
    if allow.get("allow_no_lethal"):
        return None
    alive = sm.alive()
    if not alive:
        return None
    risk = _death_damage_risk(st)
    try:
        plans = sim.search(st, max_cards=8, max_plans=3, time_limit=0.8, max_nodes=80_000)
    except Exception:  # noqa: BLE001
        return None
    for p in plans or []:
        seq = []
        for step in p.get("seq") or []:
            cid = str(step[0])
            tgt = step[1] if len(step) > 1 else None
            if cid.startswith("@"):
                seq.append({"p": sts2._int(cid.lstrip("@"), 0), "t": tgt})
            else:
                seq.append({"c": cid, "t": tgt})
        okv, _e, sm2, _s = sim.validate(st, seq)
        if not okv or sm2.alive():
            continue
        line = " → ".join(str(x[0]) for x in p.get("seq") or [])
        if risk and sts2._int(getattr(sm2, "block", 0), 0) <= 0:
            return (f"护栏·漏斩：这回合有全斩线（{p.get('dmg')} 伤害）：{line}；"
                    f"但 {', '.join(risk)} 死后还要结算一发——这条线 0 甲，可能被遗言炸死。"
                    f"换一条带甲的斩杀线，或确认要裸斩就写 {{\"allow_no_lethal\":1}} 并在 say 里说明")
        return (f"护栏·漏斩：这回合有全斩线（{p.get('dmg')} 伤害）：{line}。"
                f"多拖一回合就多挨一刀；确认要拖就写 {{\"allow_no_lethal\":1}}")
    return None


def _taken_if_end(sm) -> int:
    """这一手 end 之后本回合会净掉多少血（不改真状态）。"""
    try:
        return sts2._int(sm.clone().end_turn().get("taken"), 0)
    except Exception:  # noqa: BLE001
        return 0


def _free_block_cards(base, require_value: bool = True) -> list:
    """起手就能打的真·防御牌（血墙这种扣血换甲的要算过账）。"""
    out = []
    for c in base.hand:
        if not c.get("play", True) or c.get("_played") or not _is_block_card(c):
            continue
        cost = base.cost_of(c)
        if cost > base.energy:
            continue
        hp_cost = sts2._int(c.get("hp"), 0)
        blk = sts2._int(c.get("blk"), 0)
        if require_value and hp_cost and blk and blk <= hp_cost * 2:
            continue
        out.append((c, cost))
    return out


def _played_counts(st: dict, seq: list) -> dict:
    """这一手已经打出的牌（id → 张数）。

    漏甲护栏必须扣掉「已经打过的防御」：run73 实测，出完两张防御再 end 仍被拦，
    因为护栏看的是**回合起手**的手牌，等于罚已经做了正确动作的人。
    """
    out: dict = {}
    for step in seq or []:
        if not isinstance(step, dict) or "c" not in step:
            continue
        cid = str(step.get("c") or "")
        if cid:
            out[cid] = out.get(cid, 0) + 1
    return out


def _guard_take(st: dict, sm, allow: dict, played: dict | None = None):
    """换血线提示（原「护栏·漏甲」）。

    为什么不再硬拦：漏费护栏挡在前面，能走到这一步的牌序**必然已经花光能量**。
    账本 1104 条决策里 40 条历史「漏甲」全部是 e=0 —— 那不是漏甲，是「用一张攻击换一张
    防御」的换血线，拦下来只会逼着写 allow_take，把护栏变成噪音（run84 复盘结论）。
    所以这里只算账、给提示：净挨多少、手上哪张甲能顶、要挤掉一张什么。
    """
    if allow.get("allow_take") or allow.get("allow_leftover"):
        return None, None
    if sm.incoming() <= 0:
        return None, None
    taken = _taken_if_end(sm)
    if taken <= 0:
        return None, None
    base = sim.Sim.from_state(st)
    blocks = _free_block_cards(base)
    # 扣掉这一手已经打出去的甲牌（含同名多张）
    left = dict(played or {})
    rest = []
    for c, cost in blocks:
        cid = str(c.get("id") or "")
        if left.get(cid, 0) > 0:
            left[cid] -= 1
            continue
        rest.append((c, cost))
    blocks = rest
    if not blocks:
        return None, None
    bn = "、".join(f"{c['id']}({cost}费)" for c, cost in blocks[:4])
    if taken < 6:
        return None, None
    best = max(sts2._int(c.get("blk"), 0) for c, _k in blocks)
    if best < 4:
        return None, None
    left_e = sts2._int(getattr(sm, "energy", 0), 0)
    saved = min(taken, best)
    return None, (f"换血线：这一手净挨 {taken} 点，剩 {left_e} 费；手上 {bn} 能顶掉约 {saved} 点，"
                  f"代价是少打一张输出 —— 要换就换，硬吃就写 {{\"allow_take\":1}}")


def _guards(st: dict, sm, ended: bool, allow: dict,
            played: dict | None = None) -> tuple[str | None, dict]:
    """返回 (拒绝原因, 附加信息)。只在「这一手会结束回合」时查漏斩/漏费/漏甲。

    顺序按「亏多少」排：漏斩最贵（白送一回合挨打），其次漏费，最后漏甲。
    """
    if not ended:
        return None, {}
    if not sm.alive():
        # 已经清场：剩下的费/甲都没意义，但「死亡遗言」（蒸汽喷发之类）仍会结算
        risk = _death_damage_risk(st)
        if risk and sts2._int(getattr(sm, "block", 0), 0) <= 0:
            return None, {"warn": [f"遗言警告：{', '.join(risk)} 死亡后还要结算一发，"
                                   f"这条斩杀线 0 甲——下一回合记得留甲/缓冲药水"]}
        return None, {}
    extra: dict = {}
    err = _guard_missed_lethal(st, sm, allow)
    if err:
        return err, extra
    err, meta = _guard_leftover(sm, allow)
    extra.update(meta)
    if err:
        return err, extra
    err, warn = _guard_take(st, sm, allow, played)
    if warn:
        extra["warn"] = [warn]
    return err, extra


# ---------------------------------------------------------------- 执行
def _execute(st: dict, seq: list, lines: list, fx: dict,
             allow: dict | None = None) -> dict:
    lines = [{"t": x, "m": ""} if isinstance(x, str) else x for x in (lines or []) if x]
    plans = _candidate_plans(st) if decision_log.enabled() else []
    ok, err, sm, steps, ended = validate_plan(st, seq)
    if not ok:
        out = {"ok": False, "err": err, "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}
        if err.startswith("抽牌边界"):
            out["next"] = "split_at_draw"
            out["hint"] = "抽牌后手牌未知：先只打抽牌牌（不写 end），先读 ctx 再出下一手"
        _log_decision(st, seq, lines, fx, outcome="rejected_validate", err=err,
                      steps=steps, plans=plans, retargets=getattr(sm, "retargets", None))
        return out

    want = forecast(st, sm, steps, ended)
    bad = fx_error(fx or {}, want)
    if bad:
        out = {"ok": False, "err": bad, "fx": want,
               "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}
        _log_decision(st, seq, lines, fx, outcome="rejected_fx", err=bad,
                      steps=steps, forecast=want, plans=plans,
                      retargets=getattr(sm, "retargets", None))
        return out

    guard_err, guard_meta = _guards(st, sm, ended, allow or {},
                                    played=_played_counts(st, seq))
    if guard_err:
        out = {"ok": False, "err": guard_err, "fx": want,
               "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}
        if guard_meta:
            out.update(guard_meta)
        _log_decision(st, seq, lines, fx, outcome="rejected_guard", err=guard_err,
                      steps=steps, forecast=want, plans=plans,
                      retargets=getattr(sm, "retargets", None))
        return out

    actions = _actions(steps)
    if not actions:
        _log_decision(st, seq, lines, fx, outcome="no_actions", err="seq 里没有动作",
                      steps=steps, forecast=want, plans=plans,
                      retargets=getattr(sm, "retargets", None))
        return {"ok": False, "err": "seq 里没有动作（要结束回合就写 {\"end\":1}）",
                "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}
    _attach_say(actions, lines)
    log, run_err = _run_batch(actions)

    out = {"ok": run_err is None, "fx": want}
    if guard_meta.get("warn"):
        out["warn"] = guard_meta["warn"]
    if run_err:
        out["err"] = run_err
        tail = [x for x in log if ("STOP" in x or "BUDGET" in x or "TARGET" in x or "FIX" in x)]
        if tail:
            out["log"] = tail[-2:]
        # 头槌/秘密武器这类牌会在 batch 中途弹 CARD_SELECTION（run82 撞了三次）。
        # 顺手把选项带回来，省一次「再读 ctx」的往返。
        try:
            cur = sts2.get_state()
        except Exception:  # noqa: BLE001
            cur = {}
        if cur.get("screen") == "CARD_SELECTION":
            brief = _selection_brief(cur)
            if brief:
                out["selection"] = brief
                out["next"] = "select_deck_card --option N"
    elif sm.retargets:
        out["retarget"] = sm.retargets
    if not ended:
        out["mid"] = 1
        out["next"] = "read_ctx"
        if sm.draws:
            out["mid_reason"] = (f"已抽 {sm.draws} 张，手牌已变；先读 ctx 再出下一手，"
                                 "不要按抽牌前的手牌继续排")
        else:
            out["mid_reason"] = "没写 end：本手只打到一半；先读 ctx 确认手牌/能量，再决定是否结束回合"
    elif run_err is None:
        # 用于下一次 act 识别「刚 end 完又补 end」：记录结束的是哪一回合。
        out["ended_turn"] = _turn_of(st)
    if ended:
        # run69 的病根之二：end_turn 之后立刻读状态，拿到的是「新回合还没抽牌」
        # 的过渡快照（手牌 1 张 / 空）。照它排下一手就是漏费漏甲。这里等抽完牌、
        # 状态指纹连续两次不变再交出去；读不到就明确标 stale，别让模型照着排。
        try:
            next_st = sts2.wait_new_turn_hand(
                len(st.get("combat", {}).get("hand") or []), timeout=6.0)
        except Exception:  # noqa: BLE001
            next_st = sts2.get_state()
    else:
        next_st = sts2.get_state()
    out["ctx"] = _next_ctx(st=next_st)
    if ended and next_st.get("screen") == "COMBAT" and _pre_draw(next_st):
        out["stale_ctx"] = ("新回合还没抽完牌（手牌空/只有 1 张）：这份 ctx 不能用来排牌序，"
                            "先跑 python tools/ctx.py 再出牌")

    batch_info = {"ok": run_err is None, "err": run_err, "log": log[-8:]}
    _log_decision(st, seq, lines, fx,
                  outcome="executed" if run_err is None else "batch_error",
                  err=run_err, steps=steps, forecast=want, plans=plans,
                  batch=batch_info, after=next_st, retargets=sm.retargets)

    # 第一句已随动作播；其余排队（不阻塞本回合）
    for ln in lines[1:]:
        txt = "".join([ln.get("t") or "", ln.get("m") or ""])
        if txt:
            try:
                sts2.overlay_say("combat", txt, "", st)
            except Exception:
                pass
    return out


def _selection_brief(state: dict) -> dict:
    """batch 半路弹出来的选牌界面：只回传「选什么」的最小集。"""
    try:
        ch = ctxmod.build_with_mem(state, ctxmod._load_mem()).get("choice") or {}
    except Exception:  # noqa: BLE001
        return {}
    if ch.get("kind") != "select":
        return {}
    return {"prompt": ch.get("prompt"), "min": ch.get("min"), "max": ch.get("max"),
            "cards": [{"i": c.get("i"), "n": c.get("n"), "c": c.get("c"),
                       "dmg": c.get("dmg"), "blk": c.get("blk")}
                      for c in (ch.get("cards") or [])]}


def _dry(st: dict, seq: list, fx: dict, allow: dict | None = None) -> dict:
    """只验算不执行。run82 里三次「第四张牌没费了」都是自己心算错的，
    这一手下去就浪费一回合；先 --dry 把能量/伤害/净掉/斩杀核对一遍。"""
    ok, err, sm, steps, ended = validate_plan(st, seq)
    if not ok:
        return {"ok": False, "dry": 1, "err": err,
                "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}
    want = forecast(st, sm, steps, ended)
    bad = fx_error(fx or {}, want)
    guard_err, guard_meta = _guards(st, sm, ended, allow or {},
                                    played=_played_counts(st, seq))
    out = {"ok": True, "dry": 1, "fx": want, "ended": bool(ended)}
    if bad:
        out["fx_err"] = bad
    if guard_err:
        out["guard"] = guard_err
    if guard_meta.get("warn"):
        out["warn"] = guard_meta["warn"]
    # 每步一眼：第几张牌、耗几费、打多少、起多少甲
    per = []
    for s in sm.steps:
        if s.get("k") == "c":
            per.append({"c": s.get("id"), "e": s.get("e"), "d": s.get("d"), "blk": s.get("blk")})
        elif s.get("k") == "p":
            per.append({"p": s.get("id"), "d": s.get("d"), "blk": s.get("blk")})
    out["steps"] = per
    try:
        plans = sim.search(st, max_cards=8, max_plans=3, time_limit=0.6, max_nodes=80_000)
        out["best"] = [{"s": p["seq"], "d": p["dmg"], "take": p["taken"], "hp": p["hp"]}
                       for p in plans]
    except Exception:  # noqa: BLE001
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
        speak_now = bool(lines and not spoke)
        if speak_now:
            _attach_say(actions, lines)
            spoke = True
        _lg, run_err = _run_batch(actions)
        _log_decision(st, seq, lines if speak_now else [], {},
                      outcome="executed" if run_err is None else "batch_error",
                      err=run_err, steps=steps, forecast=forecast(st, sm, steps, ended),
                      plans=plans, retargets=sm.retargets, mode="auto")
        log.append(f"AUTO T{n + 1}: 伤害{plans[0]['dmg']} 斩{plans[0].get('kill', [])} 净掉{plans[0]['taken']}")
        if run_err:
            log.append(f"AUTO T{n + 1}: {run_err}")
            failed = True
            break
    out = {"ok": not failed, "log": log}
    next_st = sts2.get_state()
    out["ctx"] = _next_ctx(st=next_st)
    try:
        decision_log.record_run_summary(next_st)
    except Exception:  # noqa: BLE001
        pass
    return out


def main():
    ap = argparse.ArgumentParser(description="执行结构化计划（seq + say）")
    ap.add_argument("--file", help="计划 JSON 文件（不给则读 stdin）")
    ap.add_argument("--dry", action="store_true",
                    help="只验算不执行：返回 fx/护栏/建议线，用来在花掉这一手之前核对算术")
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
    allow = {str(k): bool(v) for k, v in spec.items() if str(k).startswith("allow")}
    st = sts2.get_state()
    if st.get("screen") != "COMBAT":
        err = f"当前不是战斗（{st.get('screen')}）"
        _log_decision(st, seq, lines, fx, outcome="not_combat", err=err)
        print(json.dumps({"ok": False, "err": err,
                          "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}, ensure_ascii=False))
        return
    stale = _stale_end(st, seq)
    if stale:
        _log_decision(st, seq, lines, fx, outcome="stale_end", err=stale)
        print(json.dumps({"ok": False, "err": stale,
                          "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}, ensure_ascii=False))
        return
    if auto:
        out = _auto(sts2._int(auto.get("turns"), 1), sts2._int(auto.get("stop_hp"), 0), lines)
    elif not seq:
        err = "缺 seq（要结束回合就写 [{\"end\":1}]）"
        _log_decision(st, seq, lines, fx, outcome="missing_seq", err=err)
        out = {"ok": False, "err": err,
               "ctx": ctxmod.build_with_mem(st, ctxmod._load_mem())}
    elif args.dry:
        out = _dry(st, seq, fx, allow)
    else:
        out = _execute(st, seq, lines, fx, allow)
    # 只有真的执行成功才更新回合记忆；被拒/批处理报错不能把上一手的 mid 清掉。
    if out.get("ok"):
        _write_memo(_turn_of(sts2.get_state()), bool(out.get("mid")), out.get("ended_turn"))
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
