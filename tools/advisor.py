# -*- coding: utf-8 -*-
"""本地出牌顾问：把「算账」全部放到工具里，模型只做选择和开口。

用法：
  python tools/advisor.py                 # 只算：打印这一回合的最优出牌 + 候选台词
  python tools/advisor.py --run           # 算完直接打（台词用候选里第一条）
  python tools/advisor.py --run --pick 2  # 用第 2 条候选台词打
  python tools/advisor.py --run --thought "…" --meme "…"   # 自己写台词
  python tools/advisor.py --save x.json   # 只导出 spec，不执行

它做的事：遍历「费用够」的出牌顺序（≤5 张）× 两套目标策略（打威胁大的 / 收残血），
模拟伤害、格挡、易伤、虚弱、自伤、斩杀，按
  不死 > 斩杀威胁 > 少掉血 > 多打伤害 > 不浪费费用
挑一条，顺手给 2–3 句带真实数字的台词候选。

模型只需要：看一眼 PLAN（或直接 --run），挑一句台词。
"""
import argparse
import itertools
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import sts2  # noqa: E402


def _live_enemies(c):
    out = []
    for i, e in enumerate(c.get("enemies") or []):
        if not isinstance(e, dict):
            continue
        hp = sts2._int(e.get("current_hp", e.get("hp")), 0)
        if hp > 0:
            out.append((i, e, hp))
    return out


def _card_model(idx, card):
    cid = str(card.get("card_id") or "?")
    cost = sts2._int(card.get("energy_cost", card.get("cost")), 99)
    prof = sts2._attack_profile(card)
    block = sts2._int(sts2._card_live(card, {"Block"}), 0)
    vuln = sts2._int(sts2._card_live(card, {"VulnerablePower"}), 0)
    weak = sts2._int(sts2._card_live(card, {"WeakPower"}), 0)
    selfdmg = sts2._int(sts2._card_live(card, {"HpLoss"}), 0)
    text = str(card.get("resolved_rules_text") or card.get("rules_text") or "")
    aoe = ("所有敌人" in text) or str(card.get("target") or "").lower() == "allenemies"
    if cost is None or cost < 0 or cost > 20:
        return None  # 不可打（诅咒/状态牌）
    if not prof and not block and not vuln and not weak:
        return None  # 对「这一回合」没有数值影响的能力/纯功能牌，先不进搜索
    return {"idx": idx, "id": cid, "cost": cost, "prof": prof, "block": block,
            "vuln": vuln, "weak": weak, "self": selfdmg, "aoe": aoe,
            "name": card.get("name") or cid}


def _pick_target(step_model, live, policy):
    alive = [e for e in live if e["hp"] > 0]
    if not alive:
        return None
    if policy == "threat":
        return max(alive, key=lambda e: (e["atk"], -e["hp"]))
    return min(alive, key=lambda e: (e["hp"], -e["atk"]))


def _simulate(order, enemies, hp, block, energy, policy):
    live = [{"i": i, "hp": h, "block": sts2._int(e.get("block"), 0),
             "vuln": sts2._int(sts2._power_map(e.get("powers")).get("vuln"), 0),
             "atk": sts2._intent_damage(e), "weak": 0} for i, e, h in enemies]
    dmg_total = 0
    kills_atk = 0
    kills = 0
    pblock = block
    php = hp
    spent = 0
    steps = []

    for m in order:
        spent += m["cost"]
        php -= m["self"]
        pblock += m["block"]
        targets = [e for e in live if e["hp"] > 0] if m["aoe"] else []
        target = None if m["aoe"] else _pick_target(m, live, policy)
        picks = targets if m["aoe"] else ([target] if target else [])
        card_dmg = 0
        if m["prof"]:
            per, fixed, cond = m["prof"]
            for t in picks:
                mult = 1.5 if t["vuln"] else 1.0
                hits = max(fixed, cond) if (cond and t["vuln"]) else fixed
                raw = int(per * mult) * hits
                absorbed = min(t["block"], raw)
                t["block"] -= absorbed
                dealt = raw - absorbed
                if dealt >= t["hp"]:
                    dealt = t["hp"]
                    kills += 1
                    kills_atk += t["atk"]
                t["hp"] -= dealt
                card_dmg += dealt
        dmg_total += card_dmg
        for t in picks:
            t["vuln"] += m["vuln"]
            t["weak"] += m["weak"]
        steps.append({"id": m["id"], "t": picks[0]["i"] if picks else 0, "d": card_dmg})

    incoming = 0
    for e in live:
        if e["hp"] <= 0:
            continue
        atk = e["atk"]
        if e["weak"]:
            atk = int(atk * 0.75)
        incoming += atk
    taken = max(0, incoming - pblock)
    return {"dmg": dmg_total, "kills": kills, "kills_atk": kills_atk, "taken": taken,
            "spent": spent, "php": php - taken, "hp_before": php,
            "incoming": incoming, "steps": steps}


def _score(res, energy):
    if res["php"] <= 0:
        return -1_000_000 + res["dmg"]
    s = 0.0
    s += 5.0 * res["kills_atk"] + 120 * res["kills"]
    s += res["dmg"]
    s -= 6.0 * res["taken"]
    s -= 4 * (energy - res["spent"])
    if res["taken"] == 0 and res["incoming"] > 0:
        s += 40
    return s


def _best_target_hint(plan):
    return ""


def build_plan(state):
    c = state.get("combat") or {}
    p = c.get("player") or {}
    hand = c.get("hand") or []
    energy = sts2._int(p.get("energy"), 0)
    hp = sts2._int(p.get("current_hp", p.get("hp")), 0)
    block = sts2._int(p.get("block"), 0)
    enemies = _live_enemies(c)
    models = [m for m in (_card_model(i, card) for i, card in enumerate(hand)) if m]
    playable = [m for m in models if m["cost"] <= energy]
    incoming = sts2.combat_incoming(state)

    best = None
    for policy in ("threat", "finish"):
        for k in range(0, min(len(playable), 5) + 1):
            for combo in itertools.permutations(playable, k):
                if sum(m["cost"] for m in combo) > energy:
                    continue
                res = _simulate(combo, enemies, hp, block, energy, policy)
                sc = _score(res, energy)
                if best is None or sc > best[0]:
                    best = (sc, res, combo, policy)

    sc, res, combo, policy = best
    return {
        "energy": energy, "hp": hp, "block": block, "incoming": incoming,
        "enemies": [{"i": i, "hp": h, "atk": sts2._intent_damage(e),
                     "name": str(e.get("enemy_id") or e.get("name") or "?")}
                    for i, e, h in enemies],
        "combo": combo, "steps": res["steps"], "dmg": res["dmg"], "kills": res["kills"],
        "kills_atk": res["kills_atk"], "taken": res["taken"], "spent": res["spent"],
        "hp_after": res["php"], "score": sc, "policy": policy,
        "block_played": sum(m["block"] for m in combo),
    }


def spec_from_plan(plan, thought="", meme=""):
    acts = [{"action": "play_card", "card_id": s["id"], "target_index": s["t"]}
            for s in plan["steps"]]
    acts.append({"action": "end_turn"})
    if thought:
        acts[-1]["thought"] = thought
    if meme:
        acts[-1]["meme"] = meme
    return {"actions": acts}


def candidates(plan):
    out = []
    inc, taken, dmg, kills = plan["incoming"], plan["taken"], plan["dmg"], plan["kills"]
    if inc == 0 and dmg:
        out.append(f"它这回合不动手，先打 {dmg} 点进去。")
    elif taken == 0 and plan["kills"]:
        out.append(f"先把它杀了，{inc} 点就没落下来。")
    elif taken == 0 and inc > 0:
        out.append(f"它 {inc}，我挡满，一点不掉。")
    elif taken > 0 and inc > 0:
        if plan.get("block_played"):
            out.append(f"它 {inc}，我挡完净掉 {taken}，认。")
        else:
            out.append(f"它 {inc}，这手全进攻，硬吃 {taken}。")
    if kills and plan["kills_atk"] >= 10:
        out.append(f"{dmg} 点斩了会打人的那只，这波赚。")
    elif kills:
        out.append("顺手清一只，少挨一拳。")
    if not out:
        out.append(f"这手打 {dmg} 点，能量花 {plan['spent']}。")
    out.append(f"打完剩 {plan['hp_after']} 血，账单还能看。")
    return out[:3]


def render(plan, short=False):
    enemies = " ".join(f"e{e['i']}:{e['hp']}/{e['atk']}" for e in plan["enemies"]) or "-"
    if plan["steps"]:
        seq = " → ".join(f"{s['id']}→e{s['t']}" for s in plan["steps"]) + " → END"
    else:
        seq = "END（没牌可打）"
    stats = (f"伤害{plan['dmg']} 斩{plan['kills']} "
             f"净掉{plan['taken']} 剩血{plan['hp_after']}")
    if short:
        return f"PLAN {seq} | {stats}"
    return "\n".join([
        f"ADVICE {plan['energy']}E HP {plan['hp']} blk{plan['block']} 入伤{plan['incoming']} | {enemies}",
        f"PLAN {seq} | {stats}",
    ])


def digest(s):
    """两行摘要代替整份快照：省 token 的关键。"""
    if s.get("screen") != "COMBAT":
        run = s.get("run") or {}
        return (f"SCREEN {s.get('screen')} | HP {run.get('current_hp')}/{run.get('max_hp')} "
                f"gold {run.get('gold')} floor {run.get('floor')} deck {len(run.get('deck') or [])} "
                f"| LEGAL {','.join((s.get('available_actions') or [])[:4])}")
    c = s.get("combat") or {}
    p = c.get("player") or {}
    hand = c.get("hand") or []
    cards = []
    for card in hand[:8]:
        cid = str(card.get("card_id") or "?")[:14]
        cost = sts2._int(card.get("energy_cost", card.get("cost")), 99)
        prof = sts2._attack_profile(card)
        dmg = ""
        if prof:
            per, hits, _cond = prof
            dmg = f"/{per}x{hits}" if hits > 1 else f"/{per}"
        block = sts2._int(sts2._card_live(card, {"Block"}), 0)
        cards.append(f"{cid}({cost}{dmg}{'/' + str(block) if block else ''})")
    enemies = " ".join(
        f"e{i}:{sts2._int(e.get('current_hp', e.get('hp')), 0)}→{sts2._intent_damage(e)}"
        for i, e in enumerate(c.get("enemies") or []) if isinstance(e, dict)
        and sts2._int(e.get("current_hp", e.get("hp")), 0) > 0)
    incoming = sts2.combat_incoming(s)
    return (f"NEXT {sts2._int(p.get('energy'), 0)}E HP {sts2._int(p.get('current_hp', p.get('hp')), 0)}"
            f" blk{sts2._int(p.get('block'), 0)} 入伤{incoming} | {enemies} | hand {' '.join(cards)}")


def _wait_quiet(need_player=True, timeout=25):
    """等可操作的一帧，不打印（省 token），返回 state。"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        s = sts2.get_state()
        acts = set(s.get("available_actions") or [])
        busy = not acts or acts <= {"save_and_quit", "discard_potion"}
        if busy:
            last = None
            time.sleep(0.2)
            continue
        if need_player and s.get("screen") == "COMBAT" and not ({"play_card", "end_turn"} & acts):
            last = None
            time.sleep(0.2)
            continue
        key = (s.get("screen"), tuple(sorted(acts)))
        if key == last:
            return s
        last = key
        time.sleep(0.15)
    return sts2.get_state()


def main():
    ap = argparse.ArgumentParser(description="本地出牌顾问（算账在本地，模型只决策/开口）")
    ap.add_argument("--run", action="store_true", help="算完直接执行（默认只打印）")
    ap.add_argument("--turns", type=int, default=1, help="连续自动打几个回合（默认 1）")
    ap.add_argument("--stop-hp", type=int, default=0, help="血量降到这个值以下就停手（默认不停）")
    ap.add_argument("--pick", type=int, default=1, help="用第几条候选台词（从 1 开始）")
    ap.add_argument("--thought", default="")
    ap.add_argument("--meme", default="")
    ap.add_argument("--save", help="把 spec 写到文件")
    ap.add_argument("--wait", default="player", choices=["player", "any", "no"])
    args = ap.parse_args()

    s = sts2.get_state()
    if s.get("screen") != "COMBAT":
        print(f"NOT_COMBAT screen={s.get('screen')}")
        return 2

    spoke = 0
    for turn in range(max(1, args.turns)):
        acts = s.get("available_actions") or []
        if s.get("screen") != "COMBAT":
            break
        if "play_card" not in acts and "end_turn" not in acts:
            s = _wait_quiet(need_player=True, timeout=20)
            acts = s.get("available_actions") or []
            if "play_card" not in acts and "end_turn" not in acts:
                print("NOT_PLAYER_TURN（等不到自己的回合），先停")
                break

        plan = build_plan(s)
        single = max(1, args.turns) == 1
        print(render(plan) if single else f"T{turn + 1} " + render(plan, short=True))
        lines = candidates(plan)
        if single:
            for i, ln in enumerate(lines, 1):
                print(f"LINE{i} {ln}")

        thought, meme = args.thought, args.meme
        if not (thought or meme) and lines and spoke < 2:
            # 只在前两回合配台词，后面交给 quip 埋的句子，避免刷屏
            meme = lines[max(1, min(args.pick, len(lines))) - 1]
            thought = (f"它 {plan['incoming']}，这手能打 {plan['dmg']}" if plan["incoming"]
                       else f"这手打 {plan['dmg']} 点")
            spoke += 1

        spec = spec_from_plan(plan, thought, meme)
        path = args.save or os.path.join(ROOT, "logs", ".turn_spec.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        if single:
            print(f"SPEC {path}")

        if not args.run:
            return 0
        sts2.cmd_batch(json.dumps(spec, ensure_ascii=False))
        s = _wait_quiet(need_player=(args.wait == "player"), timeout=25)
        hp = sts2._int(((s.get("run") or {}).get("current_hp")), 0)
        if args.stop_hp and hp and hp <= args.stop_hp:
            print(f"STOP hp {hp} <= {args.stop_hp}")
            break

    if args.run:
        print(digest(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
