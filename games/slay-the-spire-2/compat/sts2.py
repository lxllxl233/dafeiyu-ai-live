#!/usr/bin/env python3
"""StS2 bridge client: compact state, actions, commentary, logging.

The heavy lifting is in the game mod's local HTTP API (STS2-Agent v0.8.0).
This client is deliberately thin: it formats state for a human/agent, posts
one action at a time, keeps a JSONL transcript, and pushes quips to the
transparent OBS overlay.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import threading
import uuid
from collections import Counter
from datetime import datetime

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 布局：games/slay-the-spire-2/compat/sts2.py
# PACK = 游戏包根，ROOT = 仓库根（logs/、tools/ 仍在根上，老命令不变）。
PACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(PACK))
TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import path as brps
import perf

API = os.environ.get("STS2_API", "http://127.0.0.1:8080")
OVERLAY = os.environ.get("STS2_OVERLAY", "http://127.0.0.1:8730")
LOGS = os.path.join(ROOT, "logs")
SESSION_PTR = os.path.join(LOGS, "current_session.json")
PLAN_PATH = os.path.join(LOGS, "route_plan.json")
ROUTE_ACTUAL = os.path.join(LOGS, "route_actual.jsonl")
DATA_DIR = os.path.join(PACK, "corpus", "data", "eng")
os.makedirs(LOGS, exist_ok=True)

# ---- A/V 观测：客户端侧只记录，不参与任何播放决策。
# 用 client_id 把 prewarm/publish 与页面的 bubble/audio mark 串起来。
AV_CLIENT_LOG = os.path.join(LOGS, "av_client.jsonl")
_av_lock = threading.Lock()


def _av_log(ev: str, **fields) -> None:
    row = {"t": time.time(), "ev": ev}
    row.update(fields)
    try:
        with _av_lock:
            with open(AV_CLIENT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_db(name: str) -> dict:
    p = os.path.join(DATA_DIR, name)
    try:
        with open(p, encoding="utf-8") as f:
            items = json.load(f)
        return {str(x.get("id")): x for x in items if isinstance(x, dict) and x.get("id")}
    except Exception:
        return {}


SQLITE_DB = os.path.join(PACK, "corpus", "db", "sts2.db")


def _load_sqlite(table: str, name_col: str = "name_zh", desc_col: str = "desc_zh", extra=()) -> dict:
    """db/sts2.db 是从站点重抓的中文数据，通常比 tools/data/eng/*.json 新。

    返回 {id: {"name": 中文名, "description": 中文效果, ...extra 列}}。
    """
    if not os.path.exists(SQLITE_DB):
        return {}
    try:
        con = sqlite3.connect(SQLITE_DB)
        cols = [name_col, desc_col] + list(extra)
        cur = con.execute(f"SELECT id, {', '.join(cols)} FROM {table}")
        out = {}
        for row in cur.fetchall():
            rid = str(row[0])
            item = {"name": row[1] or "", "description": row[2] or ""}
            for i, col in enumerate(extra, start=3):
                item[col] = row[i]
            out[rid] = item
        con.close()
        return out
    except Exception:
        return {}


def _merge_db(base: dict, newer: dict) -> dict:
    """中文库覆盖同名字段（name/description 等），其余字段保留英文库的（block/damage/hit_count 等）。"""
    out = dict(base)
    for rid, item in (newer or {}).items():
        merged = dict(out.get(rid) or {})
        for k, v in item.items():
            if v not in (None, "", []):
                merged[k] = v
        out[rid] = merged
    return out


CARD_DB = _merge_db(
    _load_db("cards.json"),
    _load_sqlite("cards", extra=("cost", "target", "type", "rarity")),
)
RELIC_DB = _merge_db(_load_db("relics.json"), _load_sqlite("relics"))
POTION_DB = _merge_db(_load_db("potions.json"), _load_sqlite("potions"))
MONSTER_DB = _load_db("monsters.json")
ENCOUNTER_DB = _load_db("encounters.json")


# ---------------------------------------------------------------- plumbing
def get_state() -> dict:
    perf.count("state_read")
    r = requests.get(API + "/state", timeout=15)
    r.raise_for_status()
    d = r.json()
    return d.get("data", d)


def get_actions() -> dict:
    r = requests.get(API + "/actions/available", timeout=15)
    r.raise_for_status()
    return r.json().get("data", {})


def _append_route_actual(step: dict) -> None:
    try:
        with open(ROUTE_ACTUAL, "a", encoding="utf-8") as f:
            f.write(json.dumps({"v": 1, **step, "ts": datetime.now().isoformat(timespec="seconds")},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


def _record_route_step(before=None, params=None, after=None):
    """Append the node we just entered to the actual-path ledger.

    The state after ``choose_map_node`` may no longer carry ``is_current``.
    Therefore the selected node comes from the pre-action AVAILABLE list +
    ``option_index`` first; the post-action map is only a fallback.
    """
    try:
        st = after if isinstance(after, dict) and after.get("run") else get_state()
    except Exception:
        return
    run = st.get("run") or (before or {}).get("run") or {}
    before_run = (before or {}).get("run") or {}
    floor = run.get("floor", before_run.get("floor"))
    selected = None
    if isinstance(params, dict):
        idx = _int(params.get("option_index"), -1)
        avail = [n for n in (((before or {}).get("map") or {}).get("nodes") or [])
                 if n.get("is_available")]
        if 0 <= idx < len(avail):
            selected = avail[idx]
    if selected is None:
        nodes = (st.get("map") or {}).get("nodes") or []
        selected = next((n for n in nodes if n.get("is_current")), None)
    if selected is None or floor is None:
        return
    hp = _int(run.get("current_hp", run.get("hp")), 0)
    mhp = _int(run.get("max_hp"), 0)
    step = {
        "floor": floor,
        "run_id": (st.get("run_id") or run.get("run_id")
                   or (before or {}).get("run_id")
                   or ((before or {}).get("run") or {}).get("run_id")),
        "act_id": run.get("act_id", run.get("act",
                                                before_run.get("act_id", before_run.get("act")))),
        "row": selected.get("row"),
        "col": selected.get("col"),
        "type": selected.get("node_type"),
        "hp": hp,
        "max_hp": mhp,
        "gold": _int(run.get("gold"), 0),
        "potions": len([p for p in (run.get("potions") or [])
                        if isinstance(p, dict) and (p.get("occupied") or p.get("potion_id"))]),
    }
    try:
        with open(PLAN_PATH, encoding="utf-8") as f:
            plan = json.load(f)
    except (OSError, ValueError):
        plan = None
    if not isinstance(plan, dict):
        _append_route_actual(step)
        return
    if not plan.get("identity"):
        _append_route_actual(step)
        print("ROUTE_PLAN_STALE 旧计划没有身份，不写 actual；先 route --commit 重记")
        return
    ident = plan.get("identity") or {}
    cur_ident = {
        "run_id": step["run_id"],
        "act_id": step["act_id"],
        "map_sig": brps.map_signature(
            st.get("map") or (before or {}).get("map") or {}),
    }
    if any(ident.get(k) != v for k, v in cur_ident.items() if k in ident):
        _append_route_actual(step)
        print("ROUTE_PLAN_STALE 当前 run/act/map 与计划不同，不写 actual；先 route --commit 重记")
        return
    for pred in (plan.get("predicted") or {}).get("steps") or []:
        if pred.get("row") == step["row"] and pred.get("col") == step["col"]:
            # The actual HP is read when the map node is entered, i.e. before
            # that node's combat.  Compare it to the prediction's hp_in; the
            # predicted post-node HP is kept for the next step's check.
            step["pred_hp"] = pred.get("hp_in")
            step["pred_hp_out"] = pred.get("hp_out")
            if isinstance(step["pred_hp"], (int, float)):
                step["delta"] = hp - int(step["pred_hp"])
            break
    _append_route_actual(step)
    actual = plan.get("actual") or []
    if actual and actual[-1].get("floor") == floor:
        actual[-1] = step
    else:
        actual.append(step)
    plan["actual"] = actual
    seq = (plan.get("seq") or "").split()
    idx = len(actual) - 1
    sym = _route_sym().get(str(step["type"]), "?")
    if 0 <= idx < len(seq) and seq[idx] != sym:
        print(f"ROUTE_DEVIATED 第{idx + 1}步实际={sym}，原计划={seq[idx]}：建议 route → route --commit 重记")
    try:
        with open(PLAN_PATH, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _enemy_hp(e: dict) -> int:
    """敌人当前血优先；``hp`` 在部分接口里是最大血或旧字段。"""
    cur = e.get("current_hp")
    return _int(cur if cur is not None else e.get("hp"), 0)


def _enemy_alive(e) -> bool:
    return (isinstance(e, dict) and e.get("is_alive", True) is not False
            and _enemy_hp(e) > 0)


def _alive_enemies(st: dict) -> list:
    return [x for x in ((st.get("combat") or {}).get("enemies") or []) if _enemy_alive(x)]


def _enemy_index(st: dict, enemy: dict):
    """目标下标用接口原始 index；没有 index 才退回敌人列表位置。"""
    enemies = (st.get("combat") or {}).get("enemies") or []
    for i, x in enumerate(enemies):
        if x is enemy:
            idx = x.get("index")
            return _int(idx, i) if idx is not None else i
    idx = enemy.get("index") if isinstance(enemy, dict) else None
    return _int(idx, -1) if idx is not None else None


def _auto_target_index(st: dict):
    """Default target: lowest-HP alive enemy (fallback: first), original index."""
    es = _alive_enemies(st)
    if not es:
        return None
    best = min(es, key=_enemy_hp)
    return _enemy_index(st, best)


def act(action: str, **params) -> dict:
    body = {"action": action}
    for k, v in params.items():
        if v is not None:
            body[k] = v
    before = None
    if action == "choose_map_node":
        try:
            before = get_state()
        except Exception:
            before = None
    _t_post = time.time()
    r = requests.post(API + "/action", json=body, timeout=40)
    _dt = (time.time() - _t_post) * 1000
    perf.count("action_post")
    perf.count("action_ms", max(1, int(_dt)))
    if _dt >= 800:
        # 慢动作留证：结算/清场慢到底慢在哪一步，别靠猜。
        _av_log("slow_action", action=action, ms=int(_dt))
        if os.environ.get("STS2_TRACE_SLOW"):
            print(f"SLOW_ACTION {action} {int(_dt)}ms", file=sys.stderr)
    try:
        d = r.json()
    except Exception:
        d = {"ok": False, "error": {"message": r.text[:500]}}
    if not d.get("ok"):
        err = d.get("error", {})
        raise RuntimeError(f"{action} failed: {err.get('code')} {err.get('message')}")
    data = d.get("data", {})
    if action == "choose_map_node":
        _record_route_step(before=before, params=params, after=data)
    return data


_END_PUNCT = "。！？!?…”）)"
_last_say = {"key": "", "at": 0.0}


def _ensure_stop(s: str) -> str:
    if not s:
        return s
    return s if s[-1] in _END_PUNCT else s + "。"


def compose_speech(thought: str, meme: str) -> str:
    """和 overlay/index.html 的 composeSentence 一字不差，预热缓存键才对得上。"""
    t = re.sub(r"[。\s]+$", "", str(thought or "").strip())
    m = re.sub(r"^[，,、。；;\s—–-]+", "", str(meme or "").strip())
    if not t:
        return _ensure_stop(m)
    if not m:
        return _ensure_stop(t)
    return _ensure_stop(t) + _ensure_stop(m)


def _prewarm_tts(sentence: str, cid: str = "") -> None:
    """先合成进 overlay 磁盘缓存：页面拿 /tts 秒回，字幕和配音同时起，不再一前一后。"""
    if not sentence:
        return
    t0 = time.time()
    _av_log("prewarm_begin", cid=cid, chars=len(sentence))
    try:
        r = requests.get(OVERLAY + "/tts", params={"text": sentence}, timeout=6)
        _av_log("prewarm_end", cid=cid, ms=round((time.time() - t0) * 1000, 1),
                status=getattr(r, "status_code", None))
    except Exception as e:
        _av_log("prewarm_end", cid=cid, ms=round((time.time() - t0) * 1000, 1),
                error=str(e)[:120])
    return


def overlay_thinking(on: bool, note: str = "") -> bool:
    """告诉主播窗「Agent 正在算这一步」/「算完了」。

    窗口据此在不出声时显示「思考中……」（状态机在 tools/caster_state.py）。
    只发 on/off 两个**真实事件**，不发倒计时——窗口那边也没有「N 秒后自动消失」的逻辑；
    唯一的兜底是 240s 存活超时（进程没了就不再显示思考中）。

    失败一律静默：直播窗没开不该影响打牌。
    """
    try:
        requests.post(OVERLAY + "/thinking",
                      json={"on": bool(on), "note": str(note or "")[:40]}, timeout=0.8)
        return True
    except Exception:
        return False


def overlay_say(tag: str, thought: str, meme: str, state: dict | None = None,
                force: bool = False):
    thought = _clean_speech(thought)
    meme = _clean_speech(meme)
    if not (thought or meme):
        return
    sentence = compose_speech(thought, meme)
    now = time.time()
    # 同一句话 2.5 秒内只播一次：多动作回合以前会给每个动作都推一条，配音叠着响、越听越不齐。
    if not force and sentence == _last_say["key"] and now - _last_say["at"] < 2.5:
        return
    _last_say["key"] = sentence
    _last_say["at"] = now
    cid = uuid.uuid4().hex[:12]
    _av_log("say_begin", cid=cid, tag=tag, chars=len(sentence))
    payload = {"tag": tag, "thought": thought, "meme": meme, "speech": sentence,
               "client_id": cid}
    if state:
        run = state.get("run") or {}
        payload.update(
            {
                "character": run.get("character_name") or run.get("character_id") or "铁甲战士",
                "ascension": run.get("ascension", 10),
                "floor": run.get("floor", 0),
                "hp": run.get("hp", run.get("current_hp")),
                "max_hp": run.get("max_hp"),
                "gold": run.get("gold"),
                "screen": state.get("screen", ""),
            }
        )
    # 预热必须**后台跑**：实测 `logs/av_client.jsonl` 里 prewarm_end 要 2600–4600ms，
    # 而 POST /comment 只要 3–23ms。同步预热等于把「字幕出现」推迟 3-4 秒──
    # 观众看到的就是「结算/奖励界面卡好久才说话」。页面自己会拉 /tts（同 key 命中缓存则秒回），
    # 所以这里只负责让缓存先开始生成，绝不挡发布。
    threading.Thread(target=_prewarm_tts, args=(sentence, cid), daemon=True).start()
    t_post = time.time()
    try:
        r = requests.post(OVERLAY + "/comment", json=payload, timeout=1.5)
        comment_id = None
        try:
            comment_id = int((r.json() or {}).get("comment", {}).get("id") or 0) or None
        except Exception:
            pass
        _av_log("published", cid=cid, id=comment_id,
                status=getattr(r, "status_code", None),
                post_ms=round((time.time() - t_post) * 1000, 1))
    except Exception as e:
        _av_log("publish_fail", cid=cid, post_ms=round((time.time() - t_post) * 1000, 1),
                error=str(e)[:120])


def tag_for(screen: str) -> str:
    return {
        "COMBAT": "战斗",
        "MAP": "路线",
        "REWARD": "选牌",
        "SHOP": "商店",
        "EVENT": "事件",
        "REST": "营火",
        "CHEST": "宝箱",
        "CARD_SELECTION": "选牌",
        "MODAL": "弹窗",
        "GAME_OVER": "结算",
        "CHARACTER_SELECT": "开局",
    }.get(screen, "思路")


# ---------------------------------------------------------------- formatting
def _first(d: dict, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def _int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def _short(v, n=60):
    s = str(v) if v is not None else ""
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _powers(powers) -> str:
    if not powers:
        return ""
    out = []
    for p in powers:
        if isinstance(p, dict):
            name = p.get("name") or p.get("power_id") or p.get("id") or "?"
            amt = p.get("amount", p.get("stacks", p.get("value")))
            out.append(f"{name}:{amt}" if amt is not None else str(name))
    return ", ".join(out)


def _strip_tags(s: str) -> str:
    s = re.sub(r"\[[^\]]*\]", "", s or "")
    return s.replace("\n", " ").strip()


_SPEAKER_RE = re.compile(r"(?:大肥鱼|DeepSeek|AI)\s*[:：]\s*", re.I)


def _clean_speech(s: str) -> str:
    """The banner shows only the line itself; never a speaker prefix/attribution."""
    return _SPEAKER_RE.sub("", str(s or "")).strip()


def _card_live(c, key_names):
    """First matching dynamic_values entry (current_value) among key_names."""
    for v in c.get("dynamic_values") or []:
        if not isinstance(v, dict):
            continue
        if str(v.get("name") or "") in key_names:
            cv = v.get("current_value", v.get("value"))
            if isinstance(cv, (int, float)):
                return float(cv)
    return None


def _hand_value_rank(hand, enemies) -> str:
    """Per-energy value ranking so cheap low-damage cards don't get defaulted to."""
    if not hand:
        return ""
    n_enemy = max(1, len([e for e in (enemies or []) if _enemy_alive(e)]))
    atk, blk = [], []
    for c in hand:
        if not isinstance(c, dict):
            continue
        if card_is_status_like(c):
            continue  # 状态/诅咒牌不进攻击/格挡价值榜（毒素 5 点是自伤，不是输出）
        cid = str(c.get("card_id") or c.get("id") or "?")
        up = "+" if (c.get("upgraded") or c.get("is_upgraded")) else ""
        cost = _int(c.get("energy_cost", c.get("cost")), 0)
        if cost is None or cost < 0:
            continue
        dmg = _card_live(c, {"Damage", "CalculatedDamage"})
        block = _card_live(c, {"Block"})
        repeat = _card_live(c, {"Repeat"})
        if dmg and repeat and repeat > 1:
            # 多段卡（飞剑 3伤×4段）：动态值里的 Damage 是单段，必须乘段数
            dmg = dmg * repeat
        label = f"{cid}{up}"
        if dmg:
            per = dmg / max(1, cost)
            tag = ""
            tgt = str(c.get("target_type") or "")
            if "All" in tgt and n_enemy > 1:
                tag = f"[{n_enemy}敌合计]"
            elif "Random" in str(tgt) and n_enemy > 1:
                tag = "[随机分摊]"
            seg = f"×{repeat:.0f}段" if (repeat and repeat > 1) else ""
            atk.append((per, f"{label} {dmg:.0f}伤{seg}/{cost or 0}费={per:.1f}{tag}"))
        if block:
            per = block / max(1, cost)
            blk.append((per, f"{label} {block:.0f}甲/{cost or 0}费={per:.1f}"))
    parts = []
    if atk:
        atk.sort(key=lambda x: -x[0])
        parts.append("ATTACK " + " > ".join(s for _, s in atk[:4]))
    if blk:
        blk.sort(key=lambda x: -x[0])
        parts.append("BLOCK " + " > ".join(s for _, s in blk[:3]))
    if not parts:
        return ""
    return ("VALUE_RANK " + " | ".join(parts)
            + "  ← 同费先打高伤/高甲牌；打谁/打多少以 EFF 为准（含易伤/格挡/胆小/多段）")


def _bad_card_kind(c: dict) -> str:
    """'curse' / 'status' / '' for deck-hygiene checks (unremovable ones excluded)."""
    cid = str(c.get("card_id") or c.get("id") or "").upper()
    ctype = str(c.get("card_type") or c.get("type") or "").lower()
    if "ETERNAL" in cid or "ASCENDERS_BANE" in cid:
        return ""  # 永恒：删不掉，别浪费时间
    if "curse" in ctype or cid in {
        "DOUBT", "GUILT", "REGRET", "SHAME", "PAIN", "PARASITE", "CLUMSY", "DECAY",
        "DOUBT_CURSE", "INJURY", "NORMALITY", "WRITHE",
    }:
        return "curse"
    if "status" in ctype or cid in {"SLIMED", "WOUND", "BURN", "DAZED", "VOID"}:
        return "status"
    return ""


def _deck_bad_line(run: dict) -> str:
    deck = run.get("deck") or []
    curses, status = [], []
    for c in deck:
        if not isinstance(c, dict):
            continue
        kind = _bad_card_kind(c)
        cid = str(c.get("card_id") or c.get("id") or "?")
        if kind == "curse":
            curses.append(cid)
        elif kind == "status":
            status.append(cid)
    if not curses and not status:
        return ""
    parts = []
    if curses:
        parts.append("诅咒 " + ", ".join(curses))
    if status:
        parts.append("状态 " + ", ".join(status))
    return "DECK_BAD " + " | ".join(parts) + "  ← 下一个「移除」机会优先删这些（诅咒 > 打击）"


def _fmt_card_desc(desc: str, upgraded: bool) -> str:
    """展开中文库里的 {IfUpgraded:show:X|Y} 之类的模板标记。"""
    if not desc:
        return ""

    def _pick(m):
        return m.group(1) if upgraded else m.group(2)

    desc = re.sub(r"\{IfUpgraded:show:([^|]*)\|([^}]*)\}", _pick, desc)
    desc = re.sub(r"\{[^}]*\}", "", desc)
    return _strip_tags(desc)


def _card_line(i: int, c: dict) -> str:
    cid = c.get("card_id") or c.get("id") or "?"
    meta = CARD_DB.get(str(cid), {})
    name = meta.get("name") or c.get("name") or ""
    cost = c.get("energy_cost", c.get("cost", meta.get("cost", "?")))
    if isinstance(cost, float) and cost.is_integer():
        cost = int(cost)
    playable = c.get("playable", c.get("can_play", c.get("is_playable")))
    target = c.get("target_type") or meta.get("target") or ""
    upgraded_now = bool(c.get("upgraded") or c.get("is_upgraded"))
    # 实时文本优先：本地库可能落后一个版本；都没有时用中文库并展开模板。
    live_desc = _strip_tags(c.get("resolved_rules_text") or c.get("rules_text") or "")
    desc = live_desc or _fmt_card_desc(meta.get("description") or "", upgraded_now)
    if not desc:
        vals = c.get("dynamic_values") or []
        desc = " ".join(
            f"{v.get('name')}={v.get('current_value', v.get('value'))}"
            for v in vals
            if isinstance(v, dict)
        )
    flags = []
    if playable is False:
        flags.append("UNPLAYABLE")
    upgraded = bool(c.get("upgraded") or c.get("is_upgraded"))
    if upgraded:
        flags.append("+")
    # 卡面文字不会随升级变化（"Deal 6 damage"），真实数值在 dynamic_values.current_value。
    live = []
    for v in c.get("dynamic_values") or []:
        if not isinstance(v, dict):
            continue
        cv = v.get("current_value", v.get("value"))
        bv = v.get("base_value")
        if cv is None:
            continue
        nm = v.get("name") or "?"
        live.append(f"{nm}={cv}" if bv in (None, cv) else f"{nm}={cv}(base {bv})")
    tgts = c.get("valid_target_indices")
    ttxt = f" targets={tgts}" if isinstance(tgts, list) and len(tgts) > 1 else ""
    livetxt = f" | {', '.join(live)}" if live else ""
    star = c.get("star_cost") or 0
    starx = c.get("star_costs_x")
    startxt = f" star={'X' if starx else star}" if (star or starx) else ""
    reason = c.get("unplayable_reason")
    unplay = (
        f" UNPLAYABLE({reason})"
        if (playable is False and reason)
        else (" UNPLAYABLE" if playable is False else "")
    )
    return (
        f"[{i}] {cid} ({name}{'+' if upgraded else ''}) "
        f"cost={cost}{startxt} target={target}{ttxt} :: {desc[:110]}{livetxt}{unplay}"
    )


_MECH_CACHE: dict = {}

# Powers whose text is generic status boilerplate and only adds noise to the readout.
_GENERIC_POWERS = {
    "STRENGTH_POWER",
    "DEXTERITY_POWER",
    "VULNERABLE_POWER",
    "WEAK_POWER",
    "FRAIL_POWER",
    "POISON_POWER",
    "THORNS_POWER",
    "METALLICIZE_POWER",
    "PLATED_ARMOR_POWER",
    "INTANGIBLE_POWER",
}


def _enemy_mechanics_map() -> dict:
    """enemy_id -> list of '标题: 说明' strings for its passive powers (from db/sts2.db)."""
    if _MECH_CACHE:
        return _MECH_CACHE
    path = SQLITE_DB
    if not os.path.exists(path):
        return _MECH_CACHE
    try:
        con = sqlite3.connect(path)
        for eid, pj in con.execute("select id, powers_json from enemies"):
            try:
                powers = json.loads(pj or "[]")
            except Exception:
                powers = []
            items = []
            for p in powers if isinstance(powers, list) else []:
                if not isinstance(p, dict):
                    continue
                if str(p.get("key") or "") in _GENERIC_POWERS:
                    continue
                tz = p.get("titleZh") or p.get("titleEn") or ""
                dz = p.get("descriptionZh") or p.get("descriptionEn") or ""
                if tz and dz:
                    items.append(f"{tz}: {dz}")
            if items:
                _MECH_CACHE[str(eid)] = items
        con.close()
    except Exception:
        pass
    return _MECH_CACHE


def enemy_mechanics(enemies) -> list:
    """Mechanic lines for the enemies currently in combat (deduped, truncated)."""
    m = _enemy_mechanics_map()
    out, seen = [], set()
    for e in enemies or []:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("enemy_id") or e.get("monster_id") or e.get("id") or "")
        if not eid or eid in seen:
            continue
        seen.add(eid)
        for line in m.get(eid, []):
            if len(line) > 150:
                line = line[:147] + "..."
            out.append(f"{eid} | {line}")
    return out


def _enemy_line(i: int, e: dict) -> str:
    eid = e.get("enemy_id") or e.get("monster_id") or e.get("id") or e.get("name") or "?"
    meta = MONSTER_DB.get(str(eid), {})
    name = meta.get("name") or e.get("name") or ""
    hp = e.get("current_hp", e.get("hp", "?"))
    mhp = e.get("max_hp", "?")
    block = e.get("block", 0)
    intents = e.get("intents") or e.get("intent") or []
    itxt = []
    if isinstance(intents, list):
        for it in intents:
            if isinstance(it, dict):
                t = it.get("intent_type") or it.get("type") or "?"
                # 多段意图必须用「每段伤害 × 段数 = 总伤」显示，
                # 否则 total_damage=10 + hits=2 会被读成 "10x2"（实际是 5x2）。
                dmg = it.get("damage", it.get("value"))
                hits = _int(it.get("hits", 1), 1)
                td = it.get("total_damage")
                s = f"{t}"
                if dmg is not None:
                    s += f" {dmg}"
                    if hits > 1:
                        s += f"x{hits}={_int(dmg) * hits}"
                        if td is not None and _int(td) != _int(dmg) * hits:
                            s += f"(td={td})"
                elif td is not None:
                    s += f" {td}"
                itxt.append(s)
            else:
                itxt.append(str(it))
    elif intents:
        itxt.append(str(intents))
    powers = _powers(e.get("powers"))
    return (
        f"[{i}] {eid} ({name}) hp={hp}/{mhp} block={block}"
        + (f" intent={';'.join(itxt)}" if itxt else "")
        + (f" powers=({powers})" if powers else "")
    )


_FX_CACHE: dict = {}


def _db_fx(table: str, item_id: str) -> str:
    """'中文名: 中文效果' for a relic/potion id from db/sts2.db (cached)."""
    key = (table, item_id)
    if key in _FX_CACHE:
        return _FX_CACHE[key]
    out = ""
    path = SQLITE_DB
    if os.path.exists(path):
        try:
            con = sqlite3.connect(path)
            row = con.execute(
                f"select name_zh, desc_zh, desc_en from {table} where id = ?", (item_id,)
            ).fetchone()
            con.close()
            if row:
                name = row[0] or ""
                desc = row[1] or row[2] or ""
                desc = desc.replace(
                    "res://images/packed/sprite_fonts/ironclad_energy_icon.png", "1能量"
                )
                desc = re.sub(r"\[[^\]]*\]", "", desc).replace("\n", " ").strip()
                desc = re.sub(r"\s+", " ", desc)
                # source data drops the energy icon; restore the known meaning
                desc = desc.replace("并获得 。", "并获得 1 点能量。")
                desc = desc.replace("获得 。", "获得 1 点能量。")
                out = f"{name}: {desc}"
        except Exception:
            out = ""
    _FX_CACHE[key] = out
    return out


def relic_fx(relic_ids) -> list:
    out = []
    for rid in relic_ids or []:
        fx = _db_fx("relics", str(rid))
        if fx:
            out.append(fx if len(fx) <= 90 else fx[:87] + "...")
    return out


def potion_fx(potion_ids) -> list:
    out = []
    for pid in potion_ids or []:
        fx = _db_fx("potions", str(pid))
        if fx:
            out.append(fx if len(fx) <= 90 else fx[:87] + "...")
    return out


def potion_slots(state: dict) -> list:
    """run.potions 里的槽位（0/1），空槽也会返回，potion_id 为 None 表示空。"""
    run = state.get("run") or {}
    return [p for p in (run.get("potions") or []) if isinstance(p, dict)]


def potion_at(state: dict, index: int):
    for p in potion_slots(state):
        if _int(p.get("index"), -1) == index:
            return p
    return None


def free_potion_slots(state: dict) -> int:
    """空药水位数量（A10 只有 2 格；满了再领奖励只会静默 pending）。"""
    return sum(1 for p in potion_slots(state) if not p.get("potion_id"))


def pending_potion_rewards(state: dict) -> list:
    """奖励界面里还没领的药水奖励的 reward 下标。"""
    rew = state.get("reward") or {}
    out = []
    for i, r in enumerate(rew.get("rewards") or []):
        if not isinstance(r, dict):
            continue
        t = str(r.get("reward_type") or r.get("type") or "").lower()
        if "potion" in t and not (r.get("is_claimed") or r.get("claimed")):
            out.append(i)
    return out


def _hp_gate_line(s):
    """血闸提醒：出口是 Boss/精英但血不够时，直接喊出来（57 局死在 48 血进 Boss）。"""
    run = s.get("run") or {}
    mx = _int(run.get("max_hp"), 0)
    hp = _int(run.get("current_hp", run.get("hp")), 0)
    if not mx:
        return None
    avail = [n for n in ((s.get("map") or {}).get("nodes") or []) if n.get("is_available")]
    kinds = {str(n.get("node_type") or "").lower() for n in avail}
    pct = 100.0 * hp / mx
    asc = _int(run.get("ascension"), 10)
    if "boss" in kinds:
        gate = brps.boss_gate(s, CARD_DB, MONSTER_DB, ENCOUNTER_DB, asc)
        if not gate.get("ok"):
            return brps.gate_line(gate) + f" | HP {hp}/{mx}（{pct:.0f}%）"
        if pct < 70:
            return (f"BOSS_GATE HP {hp}/{mx}（{pct:.0f}%）< 70%：先回血再进 Boss —— "
                    f"57 局就是这么死的（48 血打 183 血）")
    if "elite" in kinds:
        if pct < 60:
            return f"ELITE_GATE HP {hp}/{mx}（{pct:.0f}%）< 60%：这个精英会把你打死，绕或先睡"
        eg = brps.elite_gate(s, CARD_DB, MONSTER_DB, ENCOUNTER_DB, asc)
        if eg.get("loss", 0) >= max(12, int(0.35 * hp)):
            return (f"ELITE_TTK 预计 {eg.get('ttk')} 回合、掉 {eg.get('loss')} 血"
                    f"（HP {hp}/{mx}）：收益不够就绕开")
    return None


def map_ascii(m: dict) -> str:
    sym = {
        "Monster": "M",
        "Elite": "E",
        "RestSite": "R",
        "Shop": "$",
        "Event": "?",
        "Treasure": "T",
        "Boss": "B",
        "Ancient": "A",
        "Unknown": "?",
    }
    if not m:
        return "(no map)"
    grid = {}
    nodes = m.get("nodes") or []
    for n in nodes:
        ch = sym.get(n.get("node_type"), "?")
        if n.get("is_current"):
            ch = "@"
        elif n.get("is_available"):
            ch = "*"
        elif n.get("visited"):
            ch = "."
        grid[(n.get("row"), n.get("col"))] = ch
    rows = m.get("rows", 16)
    cols = m.get("cols", 7)
    out = []
    for r in range(rows + 1):
        out.append(f"{r:2d} " + "".join(grid.get((r, c), " ") for c in range(cols)))
    out.append("   " + "".join(str(c % 10) for c in range(cols)))
    avail = [n for n in nodes if n.get("is_available")]
    out.append("AVAILABLE:")
    for i, n in enumerate(avail):
        ch = [(x.get("row"), x.get("col")) for x in (n.get("children") or [])]
        out.append(
            f"  opt{i}: {n.get('node_type')} row={n.get('row')} col={n.get('col')} -> {ch}"
        )
    if avail:
        kinds = {str(n.get("node_type") or "").lower() for n in avail}
        if "boss" in kinds:
            out.append("BOSS_NEXT 出口是 Boss：HP 不到 70% 就别硬上，先找营火把血睡回来。")
        if "elite" in kinds:
            out.append("ELITE_NEXT 出口有精英：60% 血 + 8 甲×2 + AOE + 药，缺一样就别进。")
    return "\n".join(out)


STATE_CACHE = os.path.join(LOGS, ".state_cache.json")


def _cache_load() -> dict:
    try:
        with open(STATE_CACHE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _cache_save(d: dict) -> None:
    try:
        tmp = STATE_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE_CACHE)
    except Exception:
        pass


_TGT_SHORT = {"AnyEnemy": "敌", "AllEnemies": "全体", "RandomEnemy": "随机",
              "Self": "己", "None": "-", "All": "全体"}


def _short_card_line(i: int, c: dict) -> str:
    """Second+ sighting of a card: keep only what a decision needs (省上下文)."""
    cid = c.get("card_id") or c.get("id") or "?"
    meta = CARD_DB.get(str(cid), {})
    cost = c.get("energy_cost", c.get("cost", meta.get("cost", "?")))
    if isinstance(cost, float) and cost.is_integer():
        cost = int(cost)
    playable = c.get("playable", c.get("can_play", c.get("is_playable")))
    tgt = str(c.get("target_type") or meta.get("target") or "")
    for k, v in _TGT_SHORT.items():
        tgt = tgt.replace(k, v)
    vals = []
    for v in c.get("dynamic_values") or []:
        if isinstance(v, dict) and v.get("current_value") is not None:
            vals.append(f"{v.get('name')}={v.get('current_value')}")
    reason = c.get("unplayable_reason")
    if playable is False:
        unplay = f" UNPLAYABLE({reason})" if reason else " UNPLAYABLE"
    else:
        unplay = ""
    vt = (" | " + ",".join(vals)) if vals else ""
    up = "+" if (c.get("upgraded") or c.get("is_upgraded")) else ""
    return f"[{i}] {cid}{up} c{cost} {tgt}{vt}{unplay}"


def compact(state: dict, full: bool = False, brief: bool = False) -> str:
    screen = state.get("screen", "?")
    run = state.get("run") or {}
    combat = state.get("combat") or {}
    lines = []
    char = run.get("character_id") or run.get("character") or "IRONCLAD"
    asc = run.get("ascension", "?")
    act = run.get("act_id", run.get("act", "?"))
    try:
        act = int(act) + 1
    except Exception:
        pass
    floor = run.get("floor", run.get("total_floor", "?"))
    hp = run.get("current_hp", run.get("hp", "?"))
    mhp = run.get("max_hp", "?")
    gold = run.get("gold", "?")
    deck = run.get("deck") or []
    lines.append(
        f"SCREEN {screen} | {char} A{asc} | act {act} floor {floor} | HP {hp}/{mhp} gold {gold} deck {len(deck)}"
    )
    if brief and screen == "COMBAT":
        # 出牌后的极简回显：只留「还能打什么、打谁、差多少」。
        c = combat
        player = c.get("player") or {}
        energy = player.get("energy", "?")
        block = player.get("block", 0)
        incoming = combat_incoming(state)
        hand = c.get("hand") or []
        ids = []
        for card in hand:
            if not isinstance(card, dict):
                continue
            cid = str(card.get("card_id") or card.get("id") or "?").replace("_IRONCLAD", "")
            cost = card.get("energy_cost", card.get("cost", "?"))
            if isinstance(cost, float) and cost.is_integer():
                cost = int(cost)
            dead = "x" if (card.get("playable") is False or card.get("can_play") is False) else ""
            ids.append(f"{cid}{dead}({cost})")
        lines.append(f"TURN {c.get('turn_number', '?')} | E{energy} blk{block} 入伤{incoming}")
        lines.append(f"HAND {len(hand)}: " + " ".join(ids))
        for i, e in enumerate(c.get("enemies") or []):
            if isinstance(e, dict):
                lines.append(" " + _enemy_line(i, e))
        if incoming > 0:
            pool, parts = hand_block_pool(hand, _int(_power_map(player.get("powers")).get("frail"), 0))
            if pool:
                lines.append(f"BLOCK_POOL {pool}（{' + '.join(parts)}）")
        acts = state.get("available_actions") or []
        lines.append("LEGAL " + ",".join(str(x) for x in acts))
        return "\n".join(lines)

    cache = _cache_load()
    dirty = False

    # ---- 药水 / 遗物 / 诅咒：常驻只留一行 HUD，完整说明只在变化时打 ----
    potions = run.get("potions") or []
    occupied = []
    potion_ids = []
    for i, p in enumerate(potions):
        if isinstance(p, dict) and (p.get("occupied") or p.get("potion_id")):
            pid = str(p.get("potion_id") or p.get("name"))
            potion_ids.append(pid)
            pm = POTION_DB.get(pid, {})
            occupied.append(f"{i}:{pid}" + (f"({pm.get('name')})" if pm.get("name") else ""))

    relics = run.get("relics") or []
    rnames = []
    relic_ids = []
    for r in relics[:12]:
        if isinstance(r, dict):
            rid = str(r.get("relic_id") or r.get("name") or "?")
            relic_ids.append(rid)
            rm = RELIC_DB.get(rid, {})
            rnames.append(rid + (f"({rm.get('name')})" if rm.get("name") else ""))
        else:
            rnames.append(str(r))

    curses = []
    for c in deck:
        if isinstance(c, dict) and _bad_card_kind(c) == "curse":
            curses.append(str(c.get("card_id") or c.get("id") or "?"))

    hud = []
    if occupied:
        hud.append("potions " + " ".join(occupied))
    if rnames:
        hud.append(f"relics {len(rnames)}")
    if curses:
        hud.append("curses " + ",".join(curses))
    if hud:
        lines.append("HUD " + " | ".join(hud))

    if occupied and (full or cache.get("potions") != potion_ids):
        lines.append("POTIONS " + " ".join(occupied))
        for fx in potion_fx(potion_ids):
            lines.append("  POTION FX " + fx)
        cache["potions"] = potion_ids
        dirty = True
    if rnames and (full or cache.get("relics") != relic_ids):
        lines.append("RELICS " + ",".join(rnames))
        for fx in relic_fx(relic_ids):
            lines.append("  RELIC FX " + fx)
        cache["relics"] = relic_ids
        dirty = True
    if curses and (full or cache.get("curses") != curses):
        lines.append("DECK_BAD 诅咒 " + ",".join(curses) + " ← 商店优先删")
        cache["curses"] = curses
        dirty = True
    _deck = run.get("deck") or []
    _cap = deck_cap(run_act(run))
    if len(_deck) > _cap and (full or cache.get("decksize") != len(_deck)):
        lines.append(
            f"DECK_FAT 牌组 {len(_deck)} 张 > 上限 {_cap}：引擎/甲牌/小刀全被稀释，"
            "接下来所有卡牌奖励 skip，商店优先删牌"
        )
        cache["decksize"] = len(_deck)
        dirty = True

    if screen == "COMBAT":
        turn = combat.get("turn_number", combat.get("round", combat.get("turn", "?")))
        player = combat.get("player") or {}
        energy = player.get("energy", "?")
        maxe = run.get("max_energy", "?")
        stars = player.get("stars", 0)
        block = player.get("block", 0)
        lines.append(f"TURN {turn} | energy {energy}/{maxe} stars {stars} block {block}")
        hand = combat.get("hand") or []
        lines.append(f"HAND {len(hand)}")
        seen = cache.get("cards") or {}
        for i, c in enumerate(hand):
            if not isinstance(c, dict):
                continue
            cid = str(c.get("card_id") or c.get("id") or "")
            if not full and seen.get(cid, 0) >= 1:
                lines.append(" " + _short_card_line(i, c))
            else:
                lines.append(" " + _card_line(i, c))
                if cid:
                    seen[cid] = seen.get(cid, 0) + 1
                    cache["cards"] = seen
                    dirty = True
        rank = _hand_value_rank(hand, combat.get("enemies") or [])
        if rank:
            lines.append(rank)
        enemies = combat.get("enemies") or []
        lines.append(f"ENEMIES {len(enemies)}")
        for i, e in enumerate(enemies):
            if isinstance(e, dict):
                lines.append(" " + _enemy_line(i, e))
        for row in _intent_cycle_lines(enemies, combat.get("available_actions") or state.get("available_actions") or []):
            lines.append(row)
        _incoming = combat_incoming(state)
        if _incoming > 0:
            lines.append(block_pool_note(
                hand, _incoming, _int(_power_map(player.get("powers")).get("frail"), 0)))
        held = [p for p in potion_slots(state) if p.get("potion_id")]
        if held and _incoming > 0:
            max_hp = _int(run.get("max_hp"), 0) or 80
            hp_now = _int(run.get("current_hp", run.get("hp")), max_hp)
            if _incoming >= max_hp * 0.25 or hp_now - _incoming <= max_hp * 0.35:
                names = ", ".join((p.get("name") or p.get("potion_id") or "?") for p in held)
                lines.append(
                    f"POTION_NUDGE 入伤 {_incoming} vs HP {hp_now}：槽里有 {len(held)} 瓶（{names}）"
                    "——先过 §4 四问（挡不住/斩杀差/会死就交），别让它吃灰"
                )
        for row in _attack_effect_table(hand, enemies, player):
            lines.append(row)
        copies = _copy_check(hand)
        if copies:
            lines.append(copies)
        pp = _powers(player.get("powers"))
        if pp:
            lines.append("PLAYER POWERS " + pp)
        mech = enemy_mechanics(enemies)
        en_ids = ",".join(sorted(
            str(e.get("enemy_id") or e.get("id") or "")
            for e in enemies if isinstance(e, dict)
        ))
        mech_key = f"{state.get('run_id')}|{act}|{floor}|{en_ids}"
        if mech and (full or cache.get("mech_key") != mech_key):
            lines.append("MECHANICS " + " ／ ".join(mech))
            if enemies:
                lines.append("PREFIGHT 先写一句「机制+意图循环」再出牌（Boss/精英查 references/enemies.md）")
            cache["mech_key"] = mech_key
            dirty = True
        if combat.get("end_turn_will_kill_player"):
            lines.append("WARNING end_turn_will_kill_player")
    elif screen == "MAP":
        lines.append(map_ascii(state.get("map") or {}))
    elif screen == "REWARD":
        rew = state.get("reward") or {}
        for i, r in enumerate(rew.get("rewards") or []):
            if isinstance(r, dict):
                lines.append(
                    f" reward[{i}] {r.get('reward_type', r.get('type', '?'))} "
                    f"{_short(r.get('description') or r.get('name') or '', 70)}"
                )
        for i, c in enumerate(rew.get("card_options") or []):
            if isinstance(c, dict):
                lines.append(" card_opt[" + str(i) + "] " + _card_line(i, c))
    elif screen == "SHOP":
        shop = state.get("shop") or {}
        lines.append(f"SHOP open={shop.get('is_open')} gold={gold}")
        for sec in ("cards", "relics", "potions"):
            for i, it in enumerate(shop.get(sec) or []):
                if isinstance(it, dict):
                    lines.append(
                        f" {sec}[{i}] {it.get('name') or it.get('card_id') or it.get('relic_id') or it.get('potion_id')}"
                        f" price={it.get('price')} available={it.get('available')}"
                    )
        rem = shop.get("card_removal") or {}
        if rem:
            lines.append(f" removal available={rem.get('available')} price={rem.get('price')}")
    elif screen == "EVENT":
        ev = state.get("event") or {}
        lines.append("EVENT " + str(ev.get("event_id") or ev.get("name") or ""))
        for i, o in enumerate(ev.get("options") or []):
            if isinstance(o, dict):
                lines.append(
                    f" opt[{i}] {_short(o.get('title') or o.get('text') or o.get('name') or '', 70)}"
                    f" locked={o.get('is_locked', o.get('locked'))} proceed={o.get('is_proceed')}"
                )
    elif screen == "REST":
        for i, o in enumerate((state.get("rest") or {}).get("options") or []):
            if isinstance(o, dict):
                lines.append(
                    f" rest[{i}] {_short(o.get('title') or o.get('name') or o.get('option_id') or '', 60)}"
                    f" enabled={o.get('is_enabled', o.get('enabled'))}"
                )
    elif screen == "CARD_SELECTION":
        sel = state.get("selection") or {}
        prompt = str(sel.get("prompt") or sel.get("title") or "")
        lines.append("SELECTION " + prompt)
        mn = _int(sel.get("min_select", sel.get("minSelect")), 1)
        mx = _int(sel.get("max_select", sel.get("maxSelect")), 1)
        cnt = sel.get("selected_count", sel.get("selectedCount"))
        cnt = _int(cnt, -1) if cnt is not None else -1
        cards_sel = sel.get("cards") or []
        picked = sum(1 for c in cards_sel if isinstance(c, dict) and (c.get("is_selected") or c.get("selected")))
        known = cnt >= 0 or any(isinstance(c, dict) and ("is_selected" in c or "selected" in c) for c in cards_sel)
        if cnt < 0:
            cnt = picked
        still_need = max(0, mn - cnt)
        legal_now = [str(x) for x in (state.get("available_actions") or [])]
        if mx <= 0 and "choose_reward_card" in legal_now:
            # 卡牌奖励这类「三选一」不是 select_deck_card 多选：min/max 都是 0。
            lines.append(
                " SELECTION_KIND 卡牌奖励（不是多选）："
                "choose_reward_card --option <i> 选一张，skip_reward_cards 跳过"
            )
        elif known:
            state_txt = f"SELECTION_STATE 已选 {cnt}/{mx}（最少 {mn}）"
        else:
            state_txt = f"SELECTION_STATE 需要 {mn}–{mx} 张（界面没暴露已选张数，按提示选满即可）"
        if mx > 0:
            if still_need:
                state_txt += f" ← 还差 {still_need} 张，继续点不同下标（同一张再点会取消）"
            else:
                state_txt += " ← 已满足最少张数"
                if sel.get("requires_confirmation") or sel.get("requiresConfirmation"):
                    state_txt += "，需要 confirm_selection 提交"
                else:
                    state_txt += "，点击后通常会自动提交"
            lines.append(" " + state_txt)
        is_removal = any(k in prompt for k in ("移除", "Remov"))
        for i, c in enumerate(cards_sel):
            if isinstance(c, dict):
                line = _card_line(i, c)
                if c.get("is_selected") or c.get("selected"):
                    line += "  <<已选中>>"
                bad = _bad_card_kind(c)
                if bad:
                    line += ("  <<CURSE·优先删>>" if is_removal else f"  <<{bad.upper()}>>")
                lines.append(" " + line)
    elif screen == "CHEST":
        ch = state.get("chest") or {}
        lines.append(f"CHEST opened={ch.get('is_open')} relics={len(ch.get('relic_options') or [])}")
    elif screen == "MODAL":
        m = state.get("modal") or {}
        lines.append(
            f"MODAL {m.get('type_name')} confirm={m.get('can_confirm')} dismiss={m.get('can_dismiss')}"
        )
    elif screen == "GAME_OVER":
        go = state.get("game_over") or {}
        lines.append(f"GAME_OVER victory={go.get('is_victory')} floor={go.get('floor')}")

    legal = state.get("available_actions") or []
    lines.append("LEGAL " + ",".join(str(x) for x in legal))
    if dirty:
        _cache_save(cache)
    return "\n".join(lines)


# ---------------------------------------------------------------- logging
def _new_session_path() -> str:
    p = os.path.join(LOGS, "run_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".jsonl")
    with open(SESSION_PTR, "w", encoding="utf-8") as f:
        json.dump({"path": p, "started": datetime.now().isoformat()}, f, ensure_ascii=False)
    return p


def _session_path(action: str | None = None) -> str:
    # Opening character select always starts a fresh run log; otherwise the
    # new run's first actions would be appended to the previous run.
    if action == "open_character_select":
        return _new_session_path()
    if os.path.exists(SESSION_PTR):
        try:
            with open(SESSION_PTR, encoding="utf-8") as f:
                p = json.load(f).get("path")
            if p and os.path.exists(p):
                return p
        except Exception:
            pass
    return _new_session_path()


def log(action: str, params: dict, before: dict, after: dict, resp: dict, thought: str, meme: str,
        effect: str | None = None):
    run = after.get("run") or {}
    combat = after.get("combat") or {}
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "run_id": after.get("run_id") or run.get("run_id"),
        "act_id": run.get("act_id"),
        "turn": combat.get("turn"),
        "action": action,
        "params": params,
        "status": resp.get("status"),
        "effect": effect,
        "message": resp.get("message"),
        "screen_before": before.get("screen"),
        "screen_after": after.get("screen"),
        "floor": run.get("floor"),
        "hp": run.get("hp", run.get("current_hp")),
        "max_hp": run.get("max_hp"),
        "gold": run.get("gold"),
        "thought": _clean_speech(thought),
        "meme": _clean_speech(meme),    }
    with open(_session_path(action), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- commands
def _recent_meme_gap(limit: int = 4) -> int:
    """How many of the last `limit` logged actions had no meme (0 = recent meme exists)."""
    gap, _, _ = _meme_stats(limit)
    return gap


# 不设硬上限：梗按“有没有节目效果”决定，只保留密度提示。
# 密度目标 25%（每 4 个动作至少 1 条梗）；低于目标就提示，连续 2 个动作没梗就催。
MEME_PACE_AFTER = 12
MEME_TARGET_RATIO = 0.45


def _meme_stats(limit: int = 3):
    """按「回合」而不是「单张牌」统计说话密度。

    返回 (gap_turns_without_speech, turns_with_speech, turns)：
    一个回合 = 同一秒内连续写入的若干条动作（batch 打出来的 3–5 刀算 1 回合）。
    旧口径按动作行数算，batch 打法下一条台词被 4 刀摊薄成 25%，永远在报警。
    """
    if not os.path.exists(SESSION_PTR):
        return 0, 0, 0
    try:
        with open(SESSION_PTR, encoding="utf-8") as f:
            path = json.load(f).get("path")
    except Exception:
        return 0, 0, 0
    if not path or not os.path.exists(path):
        return 0, 0, 0
    tail = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    tail.append(line)
    except OSError:
        return 0, 0, 0
    if not tail:
        return 0, 0, 0

    turns = []  # [(ts_key, has_speech)]
    for line in tail:
        try:
            e = json.loads(line)
        except Exception:
            continue
        key = str(e.get("ts") or "")[:19]
        spoke = bool(str(e.get("meme") or "").strip() or str(e.get("thought") or "").strip())
        if not turns or turns[-1][0] != key:
            turns.append([key, spoke])
        elif spoke:
            turns[-1][1] = True
    if not turns:
        return 0, 0, 0
    total = sum(1 for _, spoke in turns if spoke)
    gap = 0
    for _, spoke in reversed(turns[-limit:]):
        if spoke:
            break
        gap += 1
    return gap, total, len(turns)


def _meme_hint():
    gap, total, turns = _meme_stats()
    if turns <= 0:
        return
    pct = 100.0 * total / turns
    print(f"MEME_BAR {total}/{turns} 回合 {pct:.0f}% (≥{MEME_TARGET_RATIO:.0%})")
    if gap >= 3:
        print(
            f"MUST_MEME 连着 {gap} 个回合没说话（本局 {total} 个回合有词 / {pct:.0f}%）。"
            f"下一回合必须开口：python tools/memedb.py suggest --situation quips"
        )
        return
    if gap >= 2:
        print(
            f"SUGGEST_MEME 最近 {gap} 个回合没说话（本局 {total} 个回合有词 / {pct:.0f}%）；"
            f"挑一句贴上，或者用 quip 一次埋 3 句。"
        )
        return
    if turns >= MEME_PACE_AFTER and total < turns * MEME_TARGET_RATIO:
        print(
            f"MEME_PACE 回合密度偏低（{pct:.0f}% < 目标 {MEME_TARGET_RATIO:.0%}）："
            f"用 quip 一次埋 3–4 句最省（python tools/memedb.py suggest --situation quips）"
        )


def cmd_state(raw=False, full=False):
    s = get_state()
    if raw:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        print(compact(s, full=full))


def cmd_acts():
    print(json.dumps(get_actions(), ensure_ascii=False, indent=1))


def cmd_map():
    print(map_ascii(get_state().get("map") or {}))


def _route_sym() -> dict:
    return {
        "Monster": "M", "Elite": "E", "RestSite": "R", "Shop": "$",
        "Event": "?", "Treasure": "T", "Boss": "B", "Ancient": "A", "Unknown": "?",
    }


_DEFENSE_IDS = {
    "DEFEND_IRONCLAD", "TRUE_GRIT", "BLOOD_WALL", "SHRUG_IT_OFF", "TAUNT",
    "IMPERVIOUS", "FLAME_BARRIER", "EVIL_EYE", "METALLICIZE", "ARMAMENTS",
}


def _deck_stats(state: dict) -> dict:
    """牌组健康度：张数 / 防御 / AOE / 升级数 / 诅咒。用于判断「这路线还走得动吗」。"""
    run = state.get("run") or {}
    deck = run.get("deck") or []
    atk = skl = pwr = up = defense = aoe = curses = 0
    for c in deck:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("card_id") or c.get("id") or "")
        meta = CARD_DB.get(cid, {})
        ctype = str(c.get("card_type") or meta.get("type") or "").lower()
        if c.get("upgraded") or c.get("is_upgraded"):
            up += 1
        if "attack" in ctype:
            atk += 1
        elif "skill" in ctype:
            skl += 1
        elif "power" in ctype:
            pwr += 1
        if cid in _DEFENSE_IDS or (meta.get("block") or 0) > 0:
            defense += 1
        if "all" in str(meta.get("target") or "").lower():
            aoe += 1
        kind = _bad_card_kind(c)
        if kind == "curse":
            curses += 1
    return {
        "size": len(deck),
        "attack": atk,
        "skill": skl,
        "power": pwr,
        "defense": defense,
        "aoe": aoe,
        "upgraded": up,
        "curses": curses,
    }


def _map_pos(n: dict):
    if not isinstance(n, dict) or n.get("row") is None or n.get("col") is None:
        return None
    try:
        return [int(n["row"]), int(n["col"])]
    except (TypeError, ValueError):
        return None


def _snapshot(state: dict) -> dict:
    run = state.get("run") or {}
    hp = _int(run.get("current_hp", run.get("hp")), 0)
    mhp = _int(run.get("max_hp"), 0)
    potions = run.get("potions") or []
    held = [p for p in potions if isinstance(p, dict) and (p.get("occupied") or p.get("potion_id"))]
    nodes = (state.get("map") or {}).get("nodes") or []
    cur_node = next((n for n in nodes if isinstance(n, dict) and n.get("is_current")), None)
    avail = []
    for n in nodes:
        if not isinstance(n, dict) or not n.get("is_available"):
            continue
        pos = _map_pos(n)
        if pos:
            avail.append(pos + [str(n.get("node_type") or "?")])
    return {
        "run_id": state.get("run_id") or run.get("run_id"),
        "act_id": run.get("act_id", run.get("act")),
        "map_sig": brps.map_signature(state.get("map") or {}),
        "floor": run.get("floor"),
        "act": run.get("act"),
        "hp": hp,
        "max_hp": mhp,
        "gold": _int(run.get("gold"), 0),
        "potions": len(held),
        "deck": _deck_stats(state),
        "node": _map_pos(cur_node),
        "avail": avail,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


def _plan_status(snap: dict, plan: dict) -> str:
    """ok / stale / legacy.  A plan without identity is never PLAN_OK."""
    if not isinstance(plan, dict):
        return "none"
    ident = plan.get("identity")
    if not isinstance(ident, dict) or not ident:
        return "legacy"
    for key in ("run_id", "act_id", "map_sig"):
        if key in ident and ident.get(key) != snap.get(key):
            return "stale"
    return "ok"


def _fmt_snap(s: dict) -> str:
    d = s.get("deck") or {}
    pct = (s["hp"] / s["max_hp"]) if s.get("max_hp") else 0
    return (
        f"第{s.get('floor')}层 HP {s.get('hp')}/{s.get('max_hp')}（{pct:.0%}）"
        f" 金币{s.get('gold')} 药水{s.get('potions')}"
        f" 牌组{d.get('size')}（攻{d.get('attack')}/防{d.get('defense')}/AOE{d.get('aoe')}/升级{d.get('upgraded')}）"
    )


def _load_plan():
    try:
        with open(PLAN_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_plan(data):
    with open(PLAN_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _replan_reasons(cur: dict, plan: dict):
    """当前状态 vs 上次规划：返回需要重规划的理由（空 = 按原计划继续）。"""
    reasons = []
    if not plan or not plan.get("snapshot"):
        return reasons
    status = _plan_status(cur, plan)
    if status == "legacy":
        return ["旧计划没有 run_id/act/map 身份：无法确认它属于这一局，必须重新 route --commit"]
    if status == "stale":
        return [f"计划身份不匹配（计划 {plan.get('identity')} / 当前 run={cur.get('run_id')} act={cur.get('act_id')} map={cur.get('map_sig')}）：换局/换幕/换图后旧计划作废"]
    planned = plan.get("path") or []
    cur_pos = cur.get("node")
    avail = [tuple(a) for a in (cur.get("avail") or [])]
    if planned and avail:
        coords = {a[:2] for a in avail}
        idx = None
        for i, p in enumerate(planned):
            if cur_pos and list(p[:2]) == list(cur_pos):
                idx = i
                break
        if idx is None:
            # 还没走进计划路径：只要计划的起点就在当前可选项里，就算对得上
            if tuple(planned[0][:2]) in coords:
                idx = -1
            else:
                reasons.append(
                    "ROUTE_DRIFT 当前节点不在计划路径上（走岔/换线）：营火商店精英的预测都不作数，必须重规划"
                )
        if idx is not None and idx + 1 < len(planned):
            nxt = planned[idx + 1]
            npos = (int(nxt[0]), int(nxt[1]))
            if all(a[:2] != npos for a in avail):
                act_types = ",".join(sorted({str(a[2]) for a in avail}))
                ntype = nxt[2] if len(nxt) > 2 else "?"
                reasons.append(
                    f"ROUTE_DRIFT 计划下一格({nxt[0]},{nxt[1]})={ntype} 不在当前可选项（实际可选 {act_types}）："
                    "这条线后面的营火/商店/精英都不作数，先 route 重算再走"
                )
    base = plan["snapshot"]
    cur_pct = (cur["hp"] / cur["max_hp"]) if cur.get("max_hp") else 0
    base_pct = (base["hp"] / base["max_hp"]) if base.get("max_hp") else 0
    drop = base["hp"] - cur["hp"]
    if base_pct >= 0.70 and cur_pct < 0.70:
        reasons.append(f"血线跌破 70%（{base_pct:.0%}→{cur_pct:.0%}）：原计划的精英/普通怪序列要降级")
    if drop >= max(10, int(0.20 * max(1, cur.get("max_hp", 1)))):
        reasons.append(f"这一段掉了 {drop} 血：后面的营火要从『锻造』改成『睡觉』")
    if cur_pct < 0.50:
        reasons.append(f"HP {cur_pct:.0%} <50%：下一格必须是营火，绕开任何精英/双怪")
    dd, bd = cur.get("deck") or {}, base.get("deck") or {}
    if dd.get("size", 0) - bd.get("size", 0) >= 3:
        reasons.append(f"牌组 {bd.get('size')}→{dd.get('size')}：抽牌稀释，防御牌占比要看一眼")
    if bd.get("defense", 0) < 6 <= dd.get("defense", 0):
        reasons.append(f"防御牌 {bd.get('defense')}→{dd.get('defense')}：达到 6 张，精英门槛可以放宽")
    if dd.get("aoe", 0) > bd.get("aoe", 0):
        reasons.append(f"拿到 AOE（{bd.get('aoe')}→{dd.get('aoe')}）：多怪精英可以考虑打了")
    if cur.get("potions") == 0 and base.get("potions", 0) > 0:
        reasons.append("药水用光：精英四检里的『救命药』失效，别硬闯")
    if cur.get("gold", 0) >= 100 and base.get("gold", 0) < 100:
        reasons.append(f"金币到 {cur.get('gold')}：开始规划商店（删牌/买防御）")
    if dd.get("curses", 0) > bd.get("curses", 0):
        reasons.append("牌组多了诅咒：下一个移除机会优先删它")
    return reasons


def cmd_route(commit=None, note=""):
    """Plan Act 1 before moving: enumerate paths to the boss and score them."""
    s = get_state()
    m = s.get("map") or {}
    run = s.get("run") or {}
    if not m:
        print("NO MAP (not on map screen)")
        return
    nodes = m.get("nodes") or []
    rows = int(m.get("rows") or 16)
    hp = _int(run.get("current_hp", run.get("hp")), 0)
    mhp = _int(run.get("max_hp"), 0)
    gold = _int(run.get("gold"), 0)
    by_pos = {(int(n.get("row")), int(n.get("col"))): n for n in nodes if n.get("row") is not None}
    by_id = {n.get("id"): n for n in nodes if n.get("id")}

    def children(n):
        out = []
        for ch in n.get("children") or []:
            node = None
            if isinstance(ch, dict):
                node = by_pos.get((int(ch.get("row", -1)), int(ch.get("col", -1))))
            elif isinstance(ch, str):
                node = by_id.get(ch)
            if node is not None:
                out.append(node)
        return out

    def walk(start):
        paths = []

        def dfs(n, path):
            if len(path) > 40 or len(paths) > 2000:
                return
            path = path + [n]
            if n.get("node_type") in ("Boss", "Ancient") or int(n.get("row", -1)) >= rows:
                paths.append(path)
                return
            for ch in children(n):
                if ch in path:
                    continue
                dfs(ch, path)

        dfs(start, [])
        return paths

    def score(path):
        c = Counter(str(n.get("node_type")) for n in path)
        pct = (hp / mhp) if (hp and mhp) else 1.0
        sc = 4.0 * c["RestSite"] + 2.0 * c["Shop"] + 2.0 * c["Treasure"] + 1.0 * c["Event"] + 0.6 * c["Unknown"]
        if c["RestSite"] >= 3:
            sc += 1.5  # 多营火：多敲牌，用户偏好
        elite_pen = 5.0 if pct < 0.55 else 3.5 if pct < 0.70 else 2.0 if pct < 0.85 else 1.0
        # 卡组强度 / 药水会改变「能不能打精英」：防御够、有 AOE、带药水才敢碰
        if deck_stats["defense"] >= 6:
            elite_pen *= 0.78
        if deck_stats["aoe"] >= 1:
            elite_pen *= 0.85
        if potion_count >= 1:
            elite_pen *= 0.85
        if deck_stats["defense"] < 5:
            elite_pen *= 1.25
        sc -= (elite_pen + 2.0) * c["Elite"]  # 非必要不精英
        sc -= (1.0 if pct < 0.45 else 0.3) * c["Monster"]
        if deck_stats["size"] > 16:
            sc += 1.0 * c["Shop"]  # 牌组大了，商店的删牌价值更高
        tail = path[-3:]
        if any(n.get("node_type") == "RestSite" for n in tail):
            sc += 2.0
        pre_boss_shop = any(n.get("node_type") == "Shop" for n in path[-5:])
        if pre_boss_shop:
            sc += 2.5
        if c["Treasure"] >= 2:
            sc += 1.0
        return sc, c, pct, pre_boss_shop

    cur = next((n for n in nodes if n.get("is_current")), None)
    avail = [n for n in nodes if n.get("is_available")]
    if not avail and cur is not None:
        avail = children(cur)
    if not avail:
        print("NO AVAILABLE NODES")
        return

    pct = (hp / mhp) if (hp and mhp) else 1.0
    print(f"ROUTE PLAN | HP {hp}/{mhp} ({pct:.0%}) gold {gold} | floor {run.get('floor', '?')}")
    snap = _snapshot(s)
    deck_stats = snap.get("deck") or {}
    potion_count = _int(snap.get("potions"), 0)
    print("SELF " + _fmt_snap(snap))
    gate = []
    gate.append("防御≥6? " + ("是" if deck_stats.get("defense", 0) >= 6 else f"否（{deck_stats.get('defense', 0)}）"))
    gate.append("AOE≥1? " + ("是" if deck_stats.get("aoe", 0) >= 1 else "否"))
    gate.append("药水≥1? " + ("是" if potion_count >= 1 else "否"))
    print("ELITE_GATE " + " / ".join(gate) + " ← 三项全「是」也只当备选：非必要不精英")
    line = _hp_gate_line(s)
    if line:
        print(line)
    ranked = []
    for i, start in enumerate(avail):
        paths = walk(start)
        if not paths:
            continue
        scored = brps.score_paths(
            s, m, paths,
            CARD_DB, MONSTER_DB, ENCOUNTER_DB,
            ascension=_int(run.get("ascension"), 10),
        )
        if not scored:
            continue
        b = scored[0]
        sc, c, p, pre_shop = score(b["path"])
        # BRPS is the primary key; the old resource score stays as a small
        # tie-breaker so a plan is still readable by the previous playbook.
        rank_sc = b["score"] + 0.25 * sc
        ranked.append((rank_sc, sc, i, start, b["path"], c, pre_shop, b))
    ranked.sort(key=lambda x: x[0], reverse=True)
    for rank_sc, sc, i, start, path, c, pre_shop, b in ranked[:3]:
        seq = b["seq"]
        notes = []
        if c["Elite"]:
            notes.append(f"精英{c['Elite']}")
        if c["RestSite"]:
            notes.append(f"营火{c['RestSite']}")
        if c["Shop"]:
            notes.append(f"商店{c['Shop']}")
        if c["Treasure"]:
            notes.append(f"宝箱{c['Treasure']}")
        if any(n.get("node_type") == "RestSite" for n in path[-3:]):
            notes.append("Boss前有营火")
        if pre_shop:
            notes.append("Boss前有商店")
        if b.get("dead"):
            notes.append(f"预计死在 f{b.get('death_row')}")
        if b.get("forced_elite"):
            notes.append("单线强迫精英")
        if not b.get("boss_gate", {}).get("ok"):
            notes.append("Boss TTK 不够")
        print(
            f"opt{i} {start.get('node_type')} ({start.get('row')},{start.get('col')}) "
            f"score={sc:.1f} brps={b.get('score', 0):.1f} "
            f"HP→{b.get('hp_after')}/{mhp} | {' / '.join(notes) or '常规'}"
        )
        print(f"  {seq}")
        if ranked and b is ranked[0][7]:
            print("  " + brps.gate_line(b.get("boss_gate")))
            print(
                f"  BRPS deck DPS {b.get('deck', {}).get('dps')} / block {b.get('deck', {}).get('block')} "
                f"| 最差 Boss {b.get('boss_gate', {}).get('worst', {}).get('name')}"
            )
    if pct < 0.70:
        print("WARNING HP<70%：火堆权重提高，优先睡觉；非必要不打精英。")
    if pct < 0.50:
        print("WARNING HP<50%：下一格必须营火；没有营火就改道/用药，不接双怪或精英。")
    if gold >= 100 and not any(r[6] for r in ranked[:1]):
        print("WARNING 金币≥100 且首选路线 Boss 前无商店：考虑改道/预留删牌。")

    # 上次规划 vs 现在：状态变了就重规划，别按三层前的计划硬走。
    plan = _load_plan()
    if plan:
        base = plan.get("snapshot") or {}
        print(
            f"PLAN_BASE 上次规划：第{base.get('floor')}层 opt{plan.get('option')} "
            f"{plan.get('seq', '')} | {_fmt_snap(base) if base else '?'}"
        )
        status = _plan_status(snap, plan)
        if status != "ok":
            print("PLAN_STALE 计划身份不匹配，旧路线作废：先 route 重新排序，再 route --commit <opt>")
            print(f"  plan={plan.get('identity')} | now run={snap.get('run_id')} "
                  f"act={snap.get('act_id')} map={snap.get('map_sig')}")
        else:
            reasons = _replan_reasons(snap, plan)
            if reasons:
                print("REPLAN 建议重规划：")
                for r in reasons:
                    print("  - " + r)
            else:
                print("PLAN_OK 状态与上次规划一致，按原计划走（每次仍要看 AVAILABLE）")
    if commit is not None:
        pick = next((r for r in ranked if r[2] == commit), None)
        if pick is None:
            print(f"COMMIT_FAIL opt{commit} 不在候选里（候选：{[r[2] for r in ranked]}）")
        else:
            rank_sc, sc, i, start, path, c, pre_shop, b = pick
            seq = b["seq"]
            _save_plan(
                {
                    "identity": {
                        "run_id": snap.get("run_id"),
                        "act_id": snap.get("act_id"),
                        "map_sig": snap.get("map_sig"),
                    },
                    "snapshot": snap,
                    "option": i,
                    "start": {"row": start.get("row"), "col": start.get("col")},
                    "seq": seq,
                    "path": [
                        [n.get("row"), n.get("col"), str(n.get("node_type") or "?")]
                        for n in path
                    ],
                    "note": note,
                    "predicted": {
                        "score": b.get("score"),
                        "hp_after": b.get("hp_after"),
                        "dead": b.get("dead"),
                        "forced": b.get("forced"),
                        "steps": b.get("steps"),
                        "boss_gate": b.get("boss_gate"),
                    },
                }
            )
            print(
                f"PLAN_COMMIT 已记录 opt{i} ({start.get('row')},{start.get('col')}) {seq} "
                f"| 预测 HP→{b.get('hp_after')}/{mhp} brps={b.get('score')}"
            )


def cmd_plan(clear=False, note=""):
    """看当前路线计划是否还成立（状态一抖就要重规划）。"""
    s = get_state()
    run = s.get("run") or {}
    if clear:
        try:
            os.remove(PLAN_PATH)
        except OSError:
            pass
        print("PLAN_CLEARED 计划已清空（换幕/换路线/弃局后重记）")
        return
    if not run.get("max_hp"):
        print("NO RUN 不在局内（主菜单/结算界面），没有可对账的计划")
        return
    snap = _snapshot(s)
    print("SELF " + _fmt_snap(snap))
    line = _hp_gate_line(s)
    if line:
        print(line)
    if not line or not line.startswith("BOSS_TTK"):
        print(brps.gate_line(brps.boss_gate(
            s, CARD_DB, MONSTER_DB, ENCOUNTER_DB, _int(run.get("ascension"), 10))))
    if clear:
        try:
            os.remove(PLAN_PATH)
        except OSError:
            pass
        print("PLAN_CLEARED 计划已清空（换幕/换路线后重记）")
        return
    plan = _load_plan()
    if not plan:
        print("PLAN_NONE 还没有记录计划：`python sts2.py route --commit <opt>` 先记一条")
        return
    base = plan.get("snapshot") or {}
    print(
        f"PLAN_BASE 第{base.get('floor')}层 opt{plan.get('option')} {plan.get('seq', '')}"
        + (f" | {_fmt_snap(base)}" if base else "")
        + (f" | 备注：{plan['note']}" if plan.get("note") else "")
    )
    status = _plan_status(snap, plan)
    if status != "ok":
        print("PLAN_STALE 计划身份不匹配，旧路线作废：先 route 重新排序，再 route --commit <opt>")
        print(f"  plan={plan.get('identity')} | now run={snap.get('run_id')} "
              f"act={snap.get('act_id')} map={snap.get('map_sig')}")
        return
    reasons = _replan_reasons(snap, plan)
    if reasons:
        print("REPLAN 建议重规划：")
        for r in reasons:
            print("  - " + r)
        print("  → 先 `python sts2.py route` 看新排序，再 `route --commit <opt>` 记下")
    else:
        print("PLAN_OK 仍按原计划：每次选节点前照旧重读 AVAILABLE")


# ---------------------------------------------------------------- combat math
_POWER_ALIASES = {
    "str": ("力量", "Strength", "STRENGTH"),
    "dex": ("敏捷", "Dexterity", "DEXTERITY"),
    "vuln": ("易伤", "Vulnerable", "VULNERABLE"),
    "weak": ("虚弱", "Weak", "WEAK"),
    "frail": ("脆弱", "Frail", "FRAIL"),
    "thorns": ("荆棘", "Thorns", "THORNS"),
    "timid": ("胆小", "Timid", "TIMID"),
    "plating": ("覆甲", "Plating", "PLATING"),
    "metallicize": ("金属化", "Metallicize"),
    "artifact": ("人工制品", "Artifact"),
    "intangible": ("无实体", "Intangible"),
    "barricade": ("壁垒", "Barricade"),
    # run82/83 踩过的四个：柔嫩/缩小/缠结/失衡（mod 里的 power_id 与中文名都认）
    "tender": ("柔嫩", "Tender", "TENDER"),
    "shrink": ("缩小", "Shrink", "SHRINK"),
    "tangled": ("缠结", "Tangled", "TANGLED"),
    "imbalanced": ("失衡", "Imbalanced", "IMBALANCED"),
}


def _power_map(powers) -> dict:
    """{'vuln': 2, 'thorns': 3, ...}；认不出的能力按原名保留。"""
    out = {}
    for p in powers or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or p.get("power_id") or p.get("id") or "")
        amt = p.get("amount", p.get("stacks", p.get("value")))
        if not name:
            continue
        for key, aliases in _POWER_ALIASES.items():
            if any(a in name for a in aliases):
                out[key] = _int(amt, 1) if amt is not None else 1
                break
        else:
            out[name] = amt
    return out


def _sim_hits(per: int, hits: int, block: int, timid: int) -> int:
    """模拟 N 段命中：格挡池 + 胆小首击加盾；返回真实进血量。"""
    pool = max(0, block)
    total = 0
    for i in range(max(1, hits)):
        if i == 0:
            pool += max(0, timid)
        d = per
        if pool > 0:
            absorbed = min(pool, d)
            pool -= absorbed
            d -= absorbed
        total += d
    return total


_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _num_from(tok: str) -> int:
    tok = (tok or "").strip()
    if tok.isdigit():
        return int(tok)
    return _CN_NUM.get(tok, 0)


def _hits_from_text(c: dict):
    """从牌面文字补段数：(固定段数, 仅对易伤目标额外的段数)。

    Repeat 动态值只覆盖固定多段（飞剑 3×3）；像「拆卸：若目标易伤则攻击两次」
    这类条件多段只能从文字里读，否则会把 16 伤算成 8。
    """
    txt = _strip_tags(c.get("resolved_rules_text") or c.get("rules_text") or "").replace(" ", "")
    fixed, cond = 1, 0
    m = re.search(r"造成(\d+)点伤害(\d+)次", txt)
    if m:
        fixed = max(fixed, _num_from(m.group(2)))
    hits_tokens = re.findall(r"(?:攻击|打击|命中)([一两二三四五六七八九十\d]+)次", txt)
    if hits_tokens:
        n = max(_num_from(t) for t in hits_tokens)
        if "易伤" in txt:
            cond = max(cond, n)
        else:
            fixed = max(fixed, n)
    return zero_if_bad(fixed, 1), cond


def zero_if_bad(v: int, default: int) -> int:
    return v if v and v > 0 else default


def card_is_status_like(c: dict) -> bool:
    """状态/诅咒牌（毒素/伤口/灼烧…）。

    这些牌身上也有 Damage 动态值，但那是**自己**掉血的数值，不是攻击伤害：
    毒素 TOXIC 的 Damage=5 是「回合末在手牌里就扣自己 5 血」。run85 里
    VALUE_RANK 把它排成「5伤/1费=5.0」并进了 3 个出牌计划，等于把自伤当输出。
    """
    cid = str(c.get("card_id") or c.get("id") or "")
    t = str(c.get("card_type") or c.get("type") or "").lower()
    if not t:
        db = CARD_DB.get(cid) or {}
        t = str(db.get("type") or "").lower()
    return "status" in t or "curse" in t


def _attack_profile(c: dict):
    """(每段伤害, 固定段数, 易伤额外段数)；Damage 已含自身力量/虚弱。"""
    # 状态/诅咒牌一律不是攻击牌（引擎也会给它们一个 Damage 字段）
    if card_is_status_like(c):
        return None
    per = _card_live(c, {"Damage", "CalculatedDamage"})
    if not per:
        return None
    repeat = _int(_card_live(c, {"Repeat", "Hits"}), 1) or 1
    fixed, cond = _hits_from_text(c)
    return int(per), max(1, repeat, fixed), cond


def _attack_effect_table(hand, enemies, player) -> list:
    """每张攻击牌对每个敌人的真实伤害（易伤 / 格挡 / 胆小 / 段数 / 荆棘反伤）。"""
    live = [
        (i, e)
        for i, e in enumerate(enemies or [])
        if isinstance(e, dict) and _int(e.get("current_hp", e.get("hp")), 1) > 0
    ]
    if not hand or not live:
        return []
    rows = []
    for idx, c in enumerate(hand):
        if not isinstance(c, dict):
            continue
        prof = _attack_profile(c)
        if not prof:
            continue
        per, fixed_hits, cond_hits = prof
        cid = str(c.get("card_id") or "?")
        up = "+" if (c.get("upgraded") or c.get("is_upgraded")) else ""
        parts = []
        lethal = []
        chips = []
        max_hits = fixed_hits
        for ei, e in live:
            pm = _power_map(e.get("powers"))
            mult = 1.5 if pm.get("vuln") else 1.0
            # cond_hits 是「条件满足时的总段数」，不是额外段数
            hits = max(fixed_hits, cond_hits) if pm.get("vuln") else fixed_hits
            max_hits = max(max_hits, hits)
            eff = int(per * mult)
            dmg = _sim_hits(eff, hits, _int(e.get("block"), 0), _int(pm.get("timid"), 0))
            tag = f"e{ei}:{dmg}"
            if pm.get("vuln"):
                tag += "(易伤)"
            if _int(pm.get("timid"), 0):
                tag += f"(胆小{_int(pm.get('timid'), 0)})"
            hp = _int(e.get("current_hp", e.get("hp")), 0)
            if hp and dmg >= hp:
                tag += "★斩"
                lethal.append(f"e{ei}")
            parts.append(tag)
        hit_txt = f"{per}x{max_hits}" if max_hits > 1 else str(per)
        if cond_hits:
            chips.append(f"易伤时{cond_hits}段")
        thorns = max((_int(_power_map(e.get("powers")).get("thorns"), 0) for _i, e in live), default=0)
        if thorns:
            chips.append(f"反伤-{thorns * max_hits}")
        rows.append(
            f"EFF [{idx}] {cid}{up} {hit_txt}伤 → " + " ".join(parts[:4])
            + (f" | ★可斩杀 {'/'.join(lethal)}" if lethal else "")
            + ((" | " + " ".join(chips)) if chips else "")
        )
        if len(rows) >= 6:
            break
    order = _order_hint(hand, live)
    if order:
        rows.append(order)
    return rows


def _order_hint(hand, live) -> str:
    """手里有易伤牌时，提示「先挂易伤」能把其它攻击抬多少。"""
    appliers = []
    for i, c in enumerate(hand or []):
        if not isinstance(c, dict):
            continue
        if _int(_card_live(c, {"VulnerablePower"}), 0) > 0:
            appliers.append((i, c))
    if not appliers:
        return ""
    i0, c0 = appliers[0]
    gains = []
    for i, c in enumerate(hand or []):
        if not isinstance(c, dict) or i == i0:
            continue
        prof = _attack_profile(c)
        if not prof:
            continue
        per, fixed_hits, cond_hits = prof
        before = per * fixed_hits
        after_hits = max(fixed_hits, cond_hits) if cond_hits else fixed_hits
        after = int(per * 1.5) * after_hits
        if after > before:
            gains.append(f"{c.get('card_id')}{'+' if (c.get('upgraded') or c.get('is_upgraded')) else ''} {before}→{after}")
    if not gains:
        return ""
    return (
        f"ORDER 先打 [{i0}] {c0.get('card_id')} 挂易伤 → "
        + "，".join(gains[:4])
        + "  ← 第一回合先挂易伤，别先打普通刀"
    )


def _copy_check(hand) -> str:
    """手里同名但升级状态不同的牌：提醒别打出未升级那张。"""
    groups = {}
    for i, c in enumerate(hand or []):
        if not isinstance(c, dict):
            continue
        cid = str(c.get("card_id") or c.get("id") or "?")
        groups.setdefault(cid, []).append((i, bool(c.get("upgraded") or c.get("is_upgraded"))))
    parts = []
    for cid, items in groups.items():
        if len(items) < 2:
            continue
        ups = [f"[{i}]" for i, u in items if u]
        downs = [f"[{i}]" for i, u in items if not u]
        if ups and downs:
            parts.append(f"{cid}: 升级版 {'/'.join(ups)} vs 未升级 {'/'.join(downs)} → 打升级版")
    if not parts:
        return ""
    return "COPIES " + "；".join(parts[:3]) + "  ← 同名不同级，别把未升级那张打出去"


# ---------------------------------------------------------------- intent memory
INTENT_MEM_PATH = os.path.join(LOGS, "intent_memory.json")


def _intent_sig(enemy: dict) -> str:
    """把当前意图压成一个稳定字符串，用于跨回合比对。"""
    sigs = []
    for it in enemy.get("intents") or []:
        if not isinstance(it, dict):
            continue
        typ = str(it.get("intent_type") or it.get("type") or "?")
        dmg = it.get("damage", it.get("value"))
        hits = _int(it.get("hits", 1), 1)
        if dmg is not None:
            sigs.append(f"{typ} {dmg}" + (f"x{hits}" if hits > 1 else ""))
        else:
            sigs.append(typ)
    return ";".join(sigs) or "?"


def _load_intent_mem() -> dict:
    try:
        with open(INTENT_MEM_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_intent_mem(mem: dict):
    try:
        with open(INTENT_MEM_PATH, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False)
    except OSError:
        pass


def _intent_cycle_lines(enemies, actions) -> list:
    """记录每种敌人见过的意图序列，并推测下一次意图（敌人招式基本是固定循环）。

    run31 复盘：如果能提前知道「下一手是大攻击」，这回合就会多留格挡。
    """
    if not enemies:
        return []
    if not ("play_card" in (actions or []) or "end_turn" in (actions or [])):
        return []  # 只稳定玩家回合才记录，避免把过渡态写进历史
    mem = _load_intent_mem()
    lines = []
    for i, e in enumerate(enemies):
        if not isinstance(e, dict):
            continue
        eid = str(e.get("enemy_id") or e.get("name") or "?")
        cur = _intent_sig(e)
        hist = mem.get(eid) or []
        if not hist or hist[-1] != cur:
            hist = (hist + [cur])[-8:]
            mem[eid] = hist
        if len(hist) < 2:
            continue
        guess = ""
        n = len(hist)
        for j in range(n - 2, -1, -1):
            if hist[j] == cur and j + 1 < n:
                guess = hist[j + 1]
                break
        seq = " → ".join(hist[-4:])
        lines.append(
            f"INTENT_MEM e{i} {eid}: {seq}"
            + (f" | 上次出现后接的是：{guess}" if guess else " | 还没见过重复，先按招式表预判")
        )
    _save_intent_mem(mem)
    return lines


def _intent_damage_parts(enemy: dict) -> tuple[int, list[tuple[int, int]]]:
    """拆敌人本回合攻击意图：``(total_damage 总和, 需要修正的 (每段, 段数))``。

    ``total_damage`` 是 mod 已经算好的本回合最终值（含力量/虚弱/玩家易伤），
    不能再乘段数或再乘一次易伤。只有字段缺失时才回落成
    ``(damage + 敌力量) × hits``，这部分才需要调用方补虚弱/易伤。
    """
    final = 0
    raw: list[tuple[int, int]] = []
    str_amt = _int(_power_map(enemy.get("powers")).get("str"), 0)
    for it in enemy.get("intents") or []:
        if not isinstance(it, dict):
            continue
        typ = str(it.get("intent_type") or it.get("type") or "").upper()
        if "ATTACK" not in typ and "DEATH" not in typ:
            continue
        td = it.get("total_damage")
        if td is not None:
            final += _int(td)
            continue
        per = _int(it.get("damage", it.get("value")), 0) + str_amt
        hits = max(1, _int(it.get("hits", 1), 1))
        raw.append((per, hits))
    return final, raw


def _intent_damage(enemy: dict) -> int:
    final, raw = _intent_damage_parts(enemy)
    weak = _int(_power_map(enemy.get("powers")).get("weak"), 0)
    total = final
    for per, hits in raw:
        total += (int(per * 0.75) if weak else per) * hits
    return total


def combat_incoming(state: dict) -> int:
    if state.get("screen") != "COMBAT":
        return 0
    c = state.get("combat") or {}
    enemies = c.get("enemies") or []
    player_vuln = _int(_power_map((c.get("player") or {}).get("powers")).get("vuln"), 0)
    total = 0
    for e in enemies:
        if not isinstance(e, dict):
            continue
        final, raw = _intent_damage_parts(e)
        total += final
        weak = _int(_power_map(e.get("powers")).get("weak"), 0)
        for per, hits in raw:
            d = per
            if weak:
                d = int(d * 0.75)
            if player_vuln:
                d = int(d * 1.5)
            total += d * hits
    return total


def combat_anchor(state: dict) -> str:
    if state.get("screen") != "COMBAT":
        return ""
    c = state.get("combat") or {}
    enemies = c.get("enemies") or []
    incoming = combat_incoming(state)
    player = c.get("player") or {}
    energy = player.get("energy", "?")
    hand = c.get("hand") or []
    line = f"ANCHOR enemies={len(enemies)} incoming={incoming} energy={energy} hand={len(hand)}"
    pm_self = _power_map(player.get("powers"))
    self_bits = [f"hp={player.get('current_hp', player.get('hp', '?'))}"]
    if player.get("block"):
        self_bits.append(f"blk={player.get('block')}")
    for key, label in (("str", "力"), ("dex", "敏"), ("weak", "虚弱"), ("vuln", "易伤"), ("frail", "脆弱")):
        if pm_self.get(key):
            self_bits.append(f"{label}{pm_self[key]}")
    line += " | self: " + " ".join(self_bits)
    # 多段意图逐条展开：伤害×段数 与 total_damage 都对出来，防止把 8x4 误读成 8。
    detail = []
    for e in enemies:
        if not isinstance(e, dict):
            continue
        name = str(e.get("enemy_id") or e.get("name") or "?")
        for it in e.get("intents") or []:
            if not isinstance(it, dict):
                continue
            typ = str(it.get("intent_type") or it.get("type") or "")
            if "ATTACK" not in typ.upper() and "DEATH" not in typ.upper():
                continue
            dmg = it.get("damage", it.get("value"))
            hits = it.get("hits", 1)
            td = it.get("total_damage")
            calc = ""
            if isinstance(dmg, (int, float)):
                calc = f"dmg={dmg}x{hits}"
                if isinstance(hits, (int, float)) and hits > 1:
                    calc += f"={float(dmg)*hits:.0f} 多段{hits}次"
            if td is not None:
                calc += f" td={td}"
            detail.append(f"{name}[{typ} {calc}]")
    if detail:
        line += " | " + " ".join(detail[:6])
    if incoming == 0:
        line += " | NO_INCOMING: attack/engine first; pure defense only for setup/draw/retain"
    else:
        line += " | " + block_pool_note(hand, incoming, _int(pm_self.get("frail"), 0))
    return line


def hand_block_pool(hand, frail: int = 0) -> tuple:
    """手牌里可直接提供的格挡合计（含覆甲/坚毅这类基础格挡牌）。

    `frail` 只作兼容参数保留、不再参与计算：引擎 `dynamic_values.Block` 已经是
    **砍过脆弱之后**的值（全账本分组：防御 5→3、耸肩 8→6、血墙 16→12、嘲讽 7→5，
    一律 ×0.75 向下取整）。这里再乘一次就是重复打折，会把「手牌够挡」算成不够
    （run86 f12t2 因此把 4 点入伤估成 6 点）。
    """
    total = 0
    parts = []
    for c in hand:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("card_id") or "")
        block = 0
        for v in c.get("dynamic_values") or []:
            if not isinstance(v, dict):
                continue
            if str(v.get("name", "")).strip().lower() == "block":
                block = _int(v.get("current_value", v.get("value")), 0)
        if block <= 0:
            continue
        total += block
        nm = (CARD_DB.get(cid) or {}).get("name") or cid
        parts.append(f"{nm}{block}")
    return total, parts


def block_pool_note(hand, incoming: int, frail: int = 0) -> str:
    """BLOCK_POOL：手牌够不够挡满？不够的话净掉多少？

    run33 复盘（用户指出）：潜行群落那一回合手里两张耸肩、两费富余，
    却没叠防御硬吃伤害。把「能挡多少 / 挡完净掉多少」直接打在屏上。
    """
    total, parts = hand_block_pool(hand, frail)
    if not parts:
        return f"BLOCK_POOL 手牌无格挡牌｜入伤 {incoming} → 净掉 {incoming}"
    txt = f"BLOCK_POOL 手牌可挡 {total}（{' + '.join(parts)}）｜入伤 {incoming} → "
    if total >= incoming:
        return txt + "够挡满（净掉 0）"
    return txt + f"不够，净掉 {incoming - total}"


def _card_strength(c: dict):
    """Rank duplicate copies: upgraded first, then higher live values."""
    up = 1 if (c.get("upgraded") or c.get("is_upgraded")) else 0
    tot = 0.0
    for v in c.get("dynamic_values") or []:
        if not isinstance(v, dict):
            continue
        cv = v.get("current_value", v.get("value"))
        if isinstance(cv, (int, float)):
            tot += float(cv)
    return (up, tot)


def cmd_play(card_id: str, target: int | None = None, thought: str = "", meme: str = "",
             card: int | None = None, full: bool = False):
    """Play a card by card_id. Same-name copies (Strike vs Strike+) resolve to the strongest."""
    s = get_state()
    hand = ((s.get("combat") or {}).get("hand") or [])
    q = str(card_id or "").upper()
    matches = []
    for i, c in enumerate(hand):
        cid = str(c.get("card_id") or c.get("id") or "")
        name = str(c.get("name") or "")
        if (cid and (cid.upper() == q or q in cid.upper())) or (name and q in name.upper()):
            matches.append((i, c))
    if not matches:
        print(f"CARD_NOT_IN_HAND {card_id}")
        print(compact(s))
        sys.exit(2)
    if card is not None:
        idx = card
        # 显式指定下标时，若同名牌还有更强的版本，提醒一下再打（不然 6 伤 vs 9 伤又打错）
        best = max(matches, key=lambda m: _card_strength(m[1]))
        if best[0] != idx:
            best_c = best[1]
            print(
                "WRONG_COPY 指定的 [%d] 不是最强同名副本"
                "（[%d]%s%s 更强：%s）；确认要打这张就继续，否则用 play %s"
                % (
                    idx,
                    best[0],
                    best_c.get("card_id"),
                    "+" if (best_c.get("upgraded") or best_c.get("is_upgraded")) else "",
                    ", ".join(
                        f"{v.get('name')}={v.get('current_value')}"
                        for v in (best_c.get("dynamic_values") or []) if isinstance(v, dict)
                    ),
                    card_id,
                )
            )
    elif len(matches) > 1:
        desc = []
        for i, c in matches:
            vals = ", ".join(
                f"{v.get('name')}={v.get('current_value')}"
                for v in (c.get("dynamic_values") or []) if isinstance(v, dict)
            )
            desc.append(f"[{i}]{c.get('card_id')}{'+' if (c.get('upgraded') or c.get('is_upgraded')) else ''}({vals})")
        best = max(matches, key=lambda m: _card_strength(m[1]))
        idx = best[0]
        print(f"MULTI_COPY {len(matches)} candidates: " + " | ".join(desc) + f" -> chose [{idx}] (upgraded/highest); use --card <index> to override")
    else:
        idx, _ = matches[0]
    pure_def = {
        "DEFEND_IRONCLAD", "BLOOD_WALL", "FLAME_BARRIER", "TRUE_GRIT",
        "SHRUG_IT_OFF", "TAUNT", "IMPERVIOUS",
    }
    if q in pure_def and combat_incoming(s) == 0:
        print("WARNING no incoming attack: pure defense may be wasted; attack/engine first unless setting up Body Slam/Barricade/retain.")
    cmd_do("play_card", card=idx, target=target, thought=thought, meme=meme, full=full)


def _fingerprint(s: dict):
    c = s.get("combat") or {}
    hand = c.get("hand") or []
    ids = tuple((x.get("card_id") or x.get("id"), x.get("energy_cost", x.get("cost"))) for x in hand if isinstance(x, dict))
    p = c.get("player") or {}
    enemies = tuple((e.get("enemy_id") or e.get("name"), e.get("current_hp")) for e in (c.get("enemies") or []) if isinstance(e, dict))
    # 多选界面：进度变化必须进指纹，否则 wait_stable 会把「选中/取消」误判成已稳定。
    sel = s.get("selection") or {}
    sel_cards = sel.get("cards") or []
    sel_fp = (
        tuple((x.get("card_id") or x.get("id"), bool(x.get("is_selected") or x.get("selected"))) for x in sel_cards if isinstance(x, dict)),
        _int(sel.get("selected_count", sel.get("selectedCount")), -1),
        _int(sel.get("max_select", sel.get("maxSelect")), -1),
    )
    return (
        s.get("screen"),
        s.get("turn"),
        p.get("energy"),
        ids,
        enemies,
        len(s.get("available_actions") or []),
        sel_fp,
    )


def _sleep(sec: float, st=None) -> None:
    """统一 sleep：计入 perf（主动等待时间要能和真实执行时间分开看）。"""
    time.sleep(sec)
    perf.count("sleep_ms", int(sec * 1000))
    if st is not None:
        st.slept(sec)


def _await_change(before: dict, timeout: float = 3.0, interval: float = 0.08) -> dict:
    """动作发出后「等状态真的变了」，不要用固定 sleep 猜。

    run92 结算实测：`cmd_rewards` 每步后面挂 0.25–0.4 秒固定 sleep，一次结算 4–6 步
    → 光睡觉就 1–2 秒，而引擎真正刷新只要 30–80ms。固定等待在快的时候全是浪费，
    在慢的时候又不够。改成指纹一变就走，超时才放弃。
    """
    with perf.stage("await_change") as _st:
        end = time.time() + timeout
        fp = _fingerprint(before)
        while time.time() < end:
            s = get_state()
            _st.poll()
            if _fingerprint(s) != fp:
                return s
            _sleep(interval, _st)
        return get_state()


def wait_legal(action: str, timeout: float = 5.0):
    with perf.stage("wait_legal") as _st:
        end = time.time() + timeout
        last = None
        while time.time() < end:
            s = get_state()
            _st.poll()
            if action in (s.get("available_actions") or []):
                return s
            if s.get("screen") != "COMBAT":
                return s
            last = s
            _sleep(0.15, _st)
        return last


def get_state_settled(timeout: float = 6.0) -> dict:
    """读状态，但避开「上一手 end 之后还没抽牌」的过渡快照。

    run95 f1 的账本实证（logs/decisions.jsonl，mode=auto）：auto 在过渡快照上读到
    `hand=[] / energy=0`，sim 只能给出「无牌可打」的线，于是把新回合的 3 费 5 张牌
    整个空过，白吃 16 点（78→62），整局血线从这里开始崩。

    这条坑历史上修过三次（run30 空手、run68 f17 Boss 空过、run85 f11 误判），
    每次都是「某个调用方绕过了守卫」。所以守卫收在这里一处：
    **手牌 ≤1 张就等稳定快照，≥2 张直接返回**（不引入额外的轮询开销）。

    `wait_new_turn_hand` 自带「可用动作 + 指纹稳定」双条件，正常情况下 0.6s 内返回；
    手牌确实是空的时候它也只多等一个稳定性窗口，不会卡满 timeout。
    """
    st = get_state()
    if st.get("screen") != "COMBAT":
        return st
    hand = [x for x in ((st.get("combat") or {}).get("hand") or []) if isinstance(x, dict)]
    if len(hand) <= 1:
        try:
            return wait_new_turn_hand(len(hand), timeout=timeout)
        except Exception:  # noqa: BLE001
            return st
    return st


def _wait_dbg(s: dict) -> str:
    """调试用一行：新回合等在哪里（只看关键字段，不打整份 state）。"""
    cb = s.get("combat") or {}
    pl = cb.get("player") or {}
    return (f"screen={s.get('screen')} acts={','.join(sorted(s.get('available_actions') or []))}"
            f" E={pl.get('energy')}/{pl.get('max_energy', '?')} blk={pl.get('block')}"
            f" hand={len(cb.get('hand') or [])} hp={(s.get('run') or {}).get('current_hp')}"
            f" turn={cb.get('turn')}")


def wait_new_turn_hand(prev_hand_len: int, timeout: float = 4.0) -> dict:
    """end_turn 之后手牌可能是「还没抽牌」的过渡快照：等可操作且状态稳定再交给调用方。

    run30 的坑：结束回合后立刻用返回快照做决策，看到的手牌是空的（或只有 1 张）。
    v20 加两条硬条件：`play_card/end_turn` 已经出现在 available_actions，且整份
    状态指纹连续 2 次不变、至少过了 0.6 秒——抽牌动画/状态推送没落地就不返回。
    """
    with perf.stage("wait_new_turn_hand") as _st:
        start = time.time()
        end = start + timeout
        last_fp = None
        stable = 0
        last = get_state()
        _dbg = bool(os.environ.get("STS2_WAIT_DEBUG"))
        if _dbg:
            print(f"[wait] t=0.00 {_wait_dbg(last)}", file=sys.stderr)
        while time.time() < end:
            s = get_state()
            _st.poll()
            if s.get("screen") != "COMBAT":
                return s
            acts = set(s.get("available_actions") or [])
            fp = _fingerprint(s)
            if fp == last_fp:
                stable += 1
            else:
                stable = 0
                last_fp = fp
            last = s
            if _dbg:
                print(f"[wait] t={time.time()-start:.2f} stable={stable} {_wait_dbg(s)}",
                      file=sys.stderr)
            if ({"play_card", "end_turn"} & acts) and stable >= 2 and time.time() - start >= 0.6:
                return s
            _sleep(0.12, _st)
        return last


def wait_stable_player_state(timeout: float = 6.0):
    with perf.stage("wait_stable_player") as _st:
        end = time.time() + timeout
        last_fp = None
        last_state = None
        stable = 0
        while time.time() < end:
            s = get_state()
            _st.poll()
            fp = _fingerprint(s)
            if fp == last_fp:
                stable += 1
            else:
                stable = 0
                last_fp = fp
            last_state = s
            acts = s.get("available_actions") or []
            if s.get("screen") != "COMBAT":
                if stable >= 1:
                    return s
            elif ("play_card" in acts or "end_turn" in acts) and stable >= 1:
                return s
            _sleep(0.15, _st)
    print("WARNING state did not stabilize into a player turn; not trusting this snapshot.")
    return last_state or get_state()


def _sel_progress(s: dict):
    """(已选, 最少, 最多, 是否停留选牌界面)"""
    if s.get("screen") != "CARD_SELECTION":
        return (None, None, None, False)
    sel = s.get("selection") or {}
    cnt = sel.get("selected_count", sel.get("selectedCount"))
    picked = sum(1 for c in (sel.get("cards") or []) if isinstance(c, dict) and (c.get("is_selected") or c.get("selected")))
    cnt = _int(cnt, -1) if cnt is not None else -1
    if cnt < 0:
        cnt = picked
    return (
        cnt,
        _int(sel.get("min_select", sel.get("minSelect")), 1),
        _int(sel.get("max_select", sel.get("maxSelect")), 1),
        True,
    )


def wait_selection_settle(before: dict, timeout: float = 3.0) -> dict:
    """点完一张牌后等界面稳定：屏幕离开选牌界面，或已选张数/卡面发生变化。"""
    with perf.stage("wait_selection") as _st:
        base_fp = _fingerprint(before)
        end = time.time() + timeout
        last = before
        stable = 0
        while time.time() < end:
            s = get_state()
            _st.poll()
            fp = _fingerprint(s)
            changed = fp != base_fp
            if s.get("screen") != "CARD_SELECTION":
                if stable >= 1:
                    return s
            elif changed and fp == _fingerprint(last):
                if stable >= 1:
                    return s
            stable = stable + 1 if fp == _fingerprint(last) else 0
            last = s
            _sleep(0.12, _st)
        return last


def _resolve_by_descriptor(cards: list, desc: dict):
    """按下标快照里的卡牌描述，在新快照里找回同一张牌。

    返回 (下标, 是否精确命中)。列表若缩短（有些界面点一张就立刻移除），
    精确的第 n 张可能已经不存在，此时回退到同名牌的第一张，避免整步跳过。
    """
    target_id = desc.get("card_id")
    target_up = desc.get("upgraded")
    matches = []
    for i, c in enumerate(cards):
        if not isinstance(c, dict):
            continue
        if (c.get("card_id") or c.get("id")) != target_id:
            continue
        if bool(c.get("upgraded") or c.get("is_upgraded")) != bool(target_up):
            continue
        matches.append(i)
    nth = desc.get("occurrence", 0)
    if nth < len(matches):
        return matches[nth], True
    if matches:
        return matches[0], False
    return None, False


def _pick_cards_once(picks, thought="", meme=""):
    """多选界面：按给定下标依次选牌，自动处理确认，返回最终状态。"""
    s0 = get_state()
    cards0 = ((s0.get("selection") or {}).get("cards") or [])
    if not cards0:
        print("ERROR picks: 当前不在多选界面（selection.cards 为空）")
        sys.exit(2)
    total0 = _sel_progress(s0)
    print(
        f"PICKS 计划选 {len(picks)} 张：{picks}｜界面要求 {total0[1]}–{total0[2]} 张，当前已选 {total0[0]}"
    )
    # 先把下标解析成「卡牌描述」，之后每步都按描述重新定位，避免列表变化导致错位。
    planned = []
    for idx in picks:
        if idx < 0 or idx >= len(cards0):
            print(f"ERROR picks: 下标 {idx} 越界（界面共 {len(cards0)} 张）")
            sys.exit(2)
        c = cards0[idx]
        cid = c.get("card_id") or c.get("id")
        up = bool(c.get("upgraded") or c.get("is_upgraded"))
        nth = sum(
            1
            for j in range(idx)
            if isinstance(cards0[j], dict)
            and (cards0[j].get("card_id") or cards0[j].get("id")) == cid
            and bool(cards0[j].get("upgraded") or cards0[j].get("is_upgraded")) == up
        )
        planned.append({"card_id": cid, "upgraded": up, "occurrence": nth, "label": f"[{idx}] {cid}{'+' if up else ''}"})

    cur = s0
    for k, desc in enumerate(planned, 1):
        if cur.get("screen") != "CARD_SELECTION":
            print(f"PICKS 第 {k} 张跳过：界面已离开选牌（前 {k - 1} 张已生效）")
            return cur
        cards = ((cur.get("selection") or {}).get("cards") or [])
        idx, exact = _resolve_by_descriptor(cards, desc)
        if idx is None:
            print(f"PICKS 第 {k} 张跳过：{desc['label']} 已不在界面里（可能已被上一张移除）")
            continue
        if not exact:
            print(f"PICKS 第 {k} 张按同名牌回退到下标 {idx}（列表已变化）")
        before = cur
        resp = act("select_deck_card", option_index=idx)
        cur = wait_selection_settle(before)
        cnt, mn, mx, in_sel = _sel_progress(cur)
        status = resp.get("status") if isinstance(resp, dict) else "?"
        if in_sel:
            print(
                f"PICKS {k}/{len(planned)} 已点 {desc['label']}（实际下标 {idx}，{status}）→ 已选 {cnt}/{mx}"
                + (f"，还差 {max(0, mn - cnt)} 张" if cnt < mn else "，已满足")
            )
        else:
            print(f"PICKS {k}/{len(planned)} 已点 {desc['label']}（实际下标 {idx}，{status}）→ 界面已离开选牌")

    # 需要手动确认的界面，自动提交。
    if cur.get("screen") == "CARD_SELECTION":
        acts = cur.get("available_actions") or []
        cnt, mn, mx, _ = _sel_progress(cur)
        if cnt >= mn and ("confirm_selection" in acts or (cur.get("selection") or {}).get("requires_confirmation")):
            try:
                act("confirm_selection")
                cur = wait_selection_settle(cur)
                print(f"PICKS 已选 {cnt}/{mx}，自动 confirm_selection 提交")
            except Exception as e:
                print(f"PICKS confirm_selection 失败：{e}（请手动确认）")
        else:
            print(f"PICKS WARNING 仍在选牌界面（已选 {cnt}/{mx}，最少 {mn}）：还需要再选 {max(0, mn - cnt)} 张")
    return cur


def run_act(run: dict) -> int:
    """当前幕数，统一成**1 基**。

    run86 的账本里 `run.act` 是 1 基（"1"/"2"），`run.act_id` 是 0 基（"0"/"1"），
    而 deck_cap 一直读 `act_id` 当 1 基用 —— 第二幕按 16 张算（该 18）、第三幕按 18（该 20），
    提前两张锁死拿牌。这里按字段各自的基准换算，别再混用。
    """
    for key, plus in (("act", 0), ("act_id", 1)):
        raw = (run or {}).get(key)
        if raw is None:
            continue
        try:
            return max(1, int(str(raw).strip()) + plus)
        except (TypeError, ValueError):
            continue
    return 1


def deck_cap(act) -> int:
    """A10 牌组张数上限（`act` 必须是 1 基）：牌组越厚，引擎/甲牌/小刀的抽牌密度越低。"""
    try:
        a = int(str(act).strip())
    except (TypeError, ValueError):
        a = 1
    if a >= 3:
        return 20
    if a == 2:
        return 18
    return 16


def deck_cap_guard(before: dict) -> str | None:
    """抽到奖励牌之前先量牌组：超上限就返回一段拦停文案。"""
    run = before.get("run") or {}
    deck = run.get("deck") or []
    act = run_act(run)
    cap = deck_cap(act)
    names = [str(c.get("name") or c.get("id") or "?") for c in deck if isinstance(c, dict)]
    # 拿牌节奏：上限只是"别超"，但 run83 在 f10 就把 12 张涨到 16 张，
    # 结果整幕都是"半成品牌组"。前 1/3 幕的预算更紧。
    floor = _int(run.get("floor"), 0)
    a = act
    pace_cap = {1: 14, 2: 17}.get(a, cap)
    if len(deck) < cap and a in (1, 2) and floor and floor <= (8 if a == 1 else 12) \
            and len(deck) >= pace_cap:
        return (
            f"DECK_PACE 第{a}幕 f{floor} 牌组已 {len(deck)} 张（这个阶段的预算约 {pace_cap} 张）："
            f"前半幕拿太多牌，等于用「抽到好牌的期望」换「一张现在未必用得上」的牌。\n"
            f"  除非这张是甲牌/引擎/收尾牌，一律 python sts2.py do skip_reward_cards\n"
            f"  当前牌组：{', '.join(names)}"
        )
    if len(deck) < cap:
        return None
    return (
        f"DECK_CAP 牌组已 {len(deck)} 张（上限 {cap}）：再拿牌会把引擎/甲牌/小刀稀释掉，"
        f"前 13 局全是这么输的。\n"
        f"  正确动作：python sts2.py do skip_reward_cards\n"
        f"  真要拿（必须能说出踢掉谁）：python sts2.py do choose_reward_card --option N --force\n"
        f"  当前牌组：{', '.join(names)}"
    )


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "no", "none")
    return bool(v)


def pending_card_reward(state: dict) -> bool:
    """奖励界面是不是**正在等玩家选牌**（选了才会进牌组）。

    只认引擎的权威字段，不再用「rewards 里还有一张没领的 Card」去推断：
    run86 f21/f22 里 skip 掉卡牌奖励之后，`rewards` 里那张 Card 依然是
    `claimable`，旧判据一直返回 True → `collect_rewards_and_proceed` 被
    DECK_CAP 永久拦住，奖励收不掉、也进不了下一层（P10 死锁）。
    引擎在等选牌时给 `reward.pending_card_choice = true`，skip 之后是 false。
    字段缺失（老引擎）时才退回旧判据：有 card_options 或还有没领的 Card 就一律拦 ——
    宁可多拦一手（提示先 skip 再收），也不能静默领牌把牌组撑爆。
    """
    rew = state.get("reward") or {}
    if not isinstance(rew, dict):
        rew = {}
    for blob in (rew, state):
        if isinstance(blob, dict) and "pending_card_choice" in blob:
            return _truthy(blob.get("pending_card_choice"))
    if rew.get("card_options"):
        return True
    for r in rew.get("rewards") or []:
        if not isinstance(r, dict):
            continue
        t = str(r.get("reward_type") or r.get("type") or "").lower()
        if "card" in t and not (r.get("is_claimed") or r.get("claimed")):
            return True
    return False


def unclaimed_card_reward(state: dict) -> int | None:
    """REWARD 界面里「还没被处理掉的卡牌奖励」在 rewards 列表中的下标。

    run89 A1（重大）：卡牌奖励**没打开**（没 claim）时，`pending_card_choice` 是 false，
    于是 `collect_rewards_and_proceed` 绕过所有护栏，**静默把奖励里的第一张牌塞进牌组**。
    本局因此把「耸肩无视」换成了 TREMBLE、把「撕裂」换成了 CINDER —— 两个能救命的引擎
    全变成无用牌，Boss 战再输一次。所以这里改成按 rewards 列表自己找 Card 条目，
    收奖励前显式走 claim → skip。
    """
    rew = state.get("reward") or {}
    if not isinstance(rew, dict):
        return None
    for r in rew.get("rewards") or []:
        if not isinstance(r, dict):
            continue
        t = str(r.get("reward_type") or r.get("type") or "").lower()
        if "card" not in t:
            continue
        if r.get("is_claimed") or r.get("claimed"):
            continue
        return _int(r.get("index"), None)
    return None


def reward_field_hint(state: dict) -> str:
    """奖励对象里到底有没有 `pending_card_choice`？没有就报一次字段名。

    run87 的 P10 修复依赖这个字段（见 `pending_card_reward`）。账本里没有 REWARD 记录，
    所以第一次真到奖励界面时要把真实字段打出来，别让「引擎改了字段名」变成新的死锁。
    """
    rew = state.get("reward") or {}
    if not isinstance(rew, dict):
        return ""
    if "pending_card_choice" in rew or "pending_card_choice" in state:
        return ""
    return ("REWARD_KEYS 奖励对象没有 pending_card_choice，只有："
            + ", ".join(sorted(rew))
            + " —— 这次回落到旧判据（可能误拦）。把这行贴进 docs/RUN87_FIXES.md §10。")


def deck_size(state: dict) -> int:
    run = (state or {}).get("run") or {}
    for key in ("deck",):
        v = run.get(key)
        if isinstance(v, list):
            return len(v)
    return _int(run.get("deck_size"), 0)


def cmd_do(action, card=None, target=None, option=None, thought="", meme="", options=None, full=False, force=False):
    if options:
        if action != "select_deck_card":
            print(f"WARN --options 只用于 select_deck_card（收到 {action}），已忽略")
        else:
            after = _pick_cards_once(options, thought, meme)
            tag = tag_for(after.get("screen", ""))
            overlay_say(tag, thought, meme, after)
            print(compact(after))
            _meme_hint()
            return
    before = get_state()
    if action == "choose_reward_card" and not force:
        msg = deck_cap_guard(before)
        if msg:
            print(msg)
            sys.exit(3)
    # run89 A1：REWARD 界面上「还没打开」的卡牌奖励会让 collect 静默选第一张进牌组
    # （pending_card_choice 这时是 false，所有老护栏都放行）。这里改成显式三步：
    # claim 打开选牌 → skip 跳过 → 再 collect。想拿牌就先 choose_reward_card。
    if action in ("collect_rewards_and_proceed", "resolve_rewards") and not force \
            and before.get("screen") == "CARD_SELECTION":
        # run90 A2：选牌界面还没选完就 collect，引擎会替你选（第 0 张）。
        # choose_reward_card 被 DECK_PACE/DECK_CAP 拦下时，正确动作是加 --force 重发，
        # 不是 collect —— 否则「我要的那张」会变成「列表第一张」。
        print("CARD_SELECTION_OPEN 还在选牌的界面上，collect 会替你选（通常是第 0 张）：\n"
              "  要这张：python sts2.py do choose_reward_card --option N --force\n"
              "  不要牌：python sts2.py do skip_reward_cards\n"
              "  之后再 collect_rewards_and_proceed。")
        sys.exit(3)
    if action in ("collect_rewards_and_proceed", "resolve_rewards") and not force \
            and before.get("screen") == "REWARD":
        idx = unclaimed_card_reward(before)
        if idx is not None and not pending_card_reward(before):
            print(f"REWARD_CARD_UNOPENED 奖励里还有没打开的卡牌（reward[{idx}]）："
                  "直接 collect 会替你自动选第一张进牌组 → 改成 claim→skip→collect。"
                  " 真想拿牌就先 choose_reward_card --option N --force。")
            try:
                act("claim_reward", option_index=idx)
                st_c = _await_change(before)
                if st_c.get("screen") == "CARD_SELECTION" or \
                        "skip_reward_cards" in (st_c.get("available_actions") or []):
                    act("skip_reward_cards")
                    _await_change(st_c)
                    print("REWARD_CARD_SKIPPED 卡牌奖励已跳过（牌组不变）")
                else:
                    print("REWARD_CARD_NOT_OPENED claim 后没进选牌界面，跳过这步")
            except Exception as e:
                print(f"REWARD_CARD_SKIP_FAILED {e}（继续 collect，但可能仍会带牌进组）")
            before = get_state()
    # run85：collect_rewards_and_proceed 会静默把卡牌奖励一起领走，绕过 DECK_CAP
    # （牌组 16→20 张，第二幕就是被这 4 张稀释掉的）。先 skip 再收。
    if action in ("collect_rewards_and_proceed", "resolve_rewards") and not force:
        if before.get("screen") == "REWARD" and pending_card_reward(before):
            msg = deck_cap_guard(before)
            if msg:
                hint = reward_field_hint(before)
                print(msg.replace(
                    "  真要拿（必须能说出踢掉谁）：python sts2.py do choose_reward_card --option N --force",
                    "  要空收奖励（跳过卡牌）：python sts2.py do skip_reward_cards，再 collect_rewards_and_proceed")
                      + (("\n" + hint) if hint else ""))
                sys.exit(3)
    if action in ("play_card", "end_turn") and before.get("screen") == "COMBAT":
        acts = before.get("available_actions") or []
        if acts and action not in acts:
            print(f"WAIT {action} not legal yet; waiting for player turn...")
            s = wait_legal(action, timeout=5.0)
            if not s or action not in (s.get("available_actions") or []):
                print(f"ACTION_SKIPPED {action}: no legal player-turn snapshot; not sending during transition.")
                return
            before = s
    if action == "end_turn" and before.get("screen") == "COMBAT":
        hand = ((before.get("combat") or {}).get("hand") or [])
        zeros = []
        for c in hand:
            if not isinstance(c, dict):
                continue
            cost = c.get("energy_cost", c.get("cost"))
            if _int(cost, 99) != 0:
                continue
            if c.get("playable") is False or c.get("can_play") is False:
                continue
            zeros.append(str(c.get("card_id") or c.get("id") or c.get("name") or "?"))
        if zeros:
            print("WARNING ending with 0-cost playable cards: " + ", ".join(zeros) + " — consider free damage/draw/block first.")
        energy = _int(((before.get("combat") or {}).get("player") or {}).get("energy"), 0)
        affordable = []
        for c in hand:
            if not isinstance(c, dict):
                continue
            if c.get("playable") is False or c.get("can_play") is False:
                continue
            cost = _int(c.get("energy_cost", c.get("cost")), 99)
            if cost is None or cost < 0 or cost > energy:
                continue
            cid = str(c.get("card_id") or c.get("id") or "?")
            affordable.append(f"{cid}({cost}费)")
        if energy and energy > 0 and affordable:
            print(
                f"WASTE_ENERGY 还剩 {energy} 点能量没花，手上有 {len(affordable)} 张能打的牌："
                + ", ".join(affordable[:6])
                + " —— 空过回合 = 白挨打；确实不能打（会送 Buff/吃荆棘/留牌给下回合）就在 thought 里写明原因。"
            )
        # 挡牌留在手上就结束回合 → 白吃伤害（run33 潜行群落那回就是两张耸肩没打）
        incoming = combat_incoming(before)
        player_blk = _int(((before.get("combat") or {}).get("player") or {}).get("block"), 0)
        if incoming > player_blk and energy > 0:
            pm = _power_map(((before.get("combat") or {}).get("player") or {}).get("powers"))
            pool, parts = hand_block_pool(hand, _int(pm.get("frail"), 0))
            if parts and pool > 0:
                print(
                    f"LEFT_BLOCK_ON_TABLE 入伤 {incoming}、当前格挡 {player_blk}，手牌还有格挡 "
                    f"{pool}（{' + '.join(parts)}）能量 {energy} —— 够挡就先把血挡住再谈输出。"
                )
    if action == "use_potion":
        slot = _int(option, 0)
        p = potion_at(before, slot)
        if p is None or not p.get("potion_id"):
            print(f"POTION_EMPTY 槽位 {slot} 是空的：先用 state 看 POTIONS 行再决定（别对着空槽点）")
            for q in potion_slots(before):
                print(f"  slot[{q.get('index')}] {q.get('name') or '-'} occupied={bool(q.get('potion_id'))}")
            sys.exit(2)
        print(
            f"POTION_USE 槽位 {slot} · {p.get('name') or p.get('potion_id')}："
            f"{_short(p.get('description') or '', 70)}"
        )
        if p.get("requires_target") and target is None:
            ti = _auto_target_index(before)
            if ti is not None:
                target = ti
                print(f"POTION_AUTO_TARGET 需要目标 → target_index={ti}（最低血敌人）")
    # run85 C8：药水位只有 2 格，满了再 claim 会静默回 pending（4 次「以为拿到、其实没有」）。
    if action in ("claim_reward", "claim_all_rewards") and not force:
        pend = pending_potion_rewards(before)
        if action == "claim_reward":
            idx = _int(option, 0)
            pend = [x for x in pend if x == idx]
        free = free_potion_slots(before)
        if pend and free < len(pend):
            print(
                f"POTION_SLOTS_FULL 药水只有 {len(potion_slots(before))} 格，空 {free} 格，"
                f"却要领 {len(pend)} 瓶（奖励 {pend}）—— 引擎会返回 pending，等于没领到。\n"
                "  先丢一瓶：python sts2.py do discard_potion --option N\n"
                "  或者跳过这个奖励：python sts2.py do skip_reward_cards"
            )
            for q in potion_slots(before):
                print(f"  slot[{q.get('index')}] {q.get('name') or '-'} "
                      f"occupied={bool(q.get('potion_id'))}")
            sys.exit(2)
    params = {}
    if card is not None:
        params["card_index"] = card
    if target is not None:
        params["target_index"] = target
    if option is not None:
        params["option_index"] = option
    # 台词先出、再落地动作：观众看到/听到这句的同时，操作才发生。
    # 放在动作之后就变成「操作完半天才更新对话」，观感很差。
    if thought or meme:
        overlay_say(tag_for(before.get("screen", "")), thought, meme, before, force=True)
    resp = None
    last_err = None
    for attempt in range(2):
        try:
            resp = act(action, **params)
            last_err = None
            break
        except Exception as e:
            last_err = str(e)
            healed = False
            if action in ("play_card", "use_potion") and "invalid_target" in last_err and target is None:
                st = get_state()
                ti = _auto_target_index(st)
                if ti is not None:
                    print(f"AUTO_TARGET 需要目标 → 自动 target_index={ti}（最低血敌人）重试")
                    params["target_index"] = ti
                    target = ti
                    healed = True
            elif action == "play_card" and "invalid_action" in last_err and attempt == 0:
                st = _await_change(before)
                if "play_card" in (st.get("available_actions") or []):
                    print("AUTO_RETRY play_card 失败 → 重读状态后重试一次")
                    healed = True
            if not healed or attempt == 1:
                break
    if resp is None:
        print(f"ERROR {last_err}")
        if action == "play_card":
            print("HINT 0 费牌没打出去=白丢伤害/格挡；检查 --target / 费用 / 目标是否合法，换一张再试。")
        sys.exit(2)
    if action == "use_potion":
        st1 = _await_change(before)
        p1 = potion_at(st1, _int(option, 0))
        still = bool(p1 and p1.get("potion_id"))
        queued = bool(p1 and p1.get("is_queued"))
        if still and not queued:
            print("POTION_PENDING 药水还在槽里且没排队：重读后再试 1 次（需要目标就自动选最低血）")
            try:
                params2 = dict(params)
                if p1.get("requires_target") and "target_index" not in params2:
                    ti = _auto_target_index(st1)
                    if ti is not None:
                        params2["target_index"] = ti
                resp = act("use_potion", **params2)
                print(f"POTION_RETRY -> {resp.get('status')} {resp.get('message')}")
            except Exception as e:
                print(f"POTION_RETRY_FAILED {e}（按没生效重算，别把药水算进计划）")
        # 接口 ok/回 pending 都不等于药水用掉了：以槽位为准留一句硬证据。
        p2 = potion_at(_await_change(before), _int(option, 0))
        if p2 and p2.get("potion_id") and not p2.get("is_queued"):
            print("POTION_LOST 药水还在槽里（status 说收下了但没消耗）：别把这一瓶算进伤害/格挡，"
                  "先 python tools/ctx.py 看 potions 再决定是否重试")
    after = wait_stable_player_state()
    # P10：牌组已经到/超上限时，收奖励不该再涨牌。真涨了就是「静默领牌」又回来了，
    # 必须喊出来（不吞证据、不改判定；动作已经发生，只能留痕 + 提示怎么补救）。
    if action in ("collect_rewards_and_proceed", "resolve_rewards"):
        n0, n1 = deck_size(before), deck_size(after)
        if n1 > n0:
            run = before.get("run") or {}
            cap = deck_cap(run_act(run))
            print(f"DECK_GREW 牌组 {n0}→{n1}（上限 {cap}）：这个动作本来不该带牌进组。"
                  " 先 python tools/ctx.py 看牌组，多了哪张就在这里说清楚；"
                  " 下次先 skip_reward_cards 再 collect_rewards_and_proceed。")
    # 结束回合后的快照可能还没抽牌；等一下再抓，并把「手牌为空但还能出牌」的过渡态标出来。
    if action == "end_turn" and after.get("screen") == "COMBAT":
        prev_len = len(((before.get("combat") or {}).get("hand") or []))
        ready = wait_new_turn_hand(prev_len)
        if ready is not None:
            after = ready
        hand_now = ((after.get("combat") or {}).get("hand") or [])
        acts_now = after.get("available_actions") or []
        if not hand_now and "play_card" in acts_now:
            print(
                "WARNING HAND_STALE 手牌是空的但 play_card 合法：这是抽牌动画中的过渡快照，"
                "先 `python sts2.py state` 重读再决策（别拿这张快照当回合开始）。"
            )
    if action == "play_card" and before.get("screen") == "COMBAT" and after.get("screen") == "COMBAT":
        bh = len(((before.get("combat") or {}).get("hand") or []))
        ah = len(((after.get("combat") or {}).get("hand") or []))
        be = _int(((before.get("combat") or {}).get("player") or {}).get("energy"), 0)
        ae = _int(((after.get("combat") or {}).get("player") or {}).get("energy"), 0)
        if ah == 0 and bh > 1 and ae >= be:
            print("WARNING suspicious post-card state (hand emptied but energy did not drop); re-read before ending turn.")
    tag = tag_for(after.get("screen", ""))
    log(action, params, before, after, resp or {}, thought, meme)
    if action == "select_deck_card" and after.get("screen") == "CARD_SELECTION":
        cnt, mn, mx, _in = _sel_progress(after)
        if mx > 0 and cnt < mn:
            print(
                f"MULTI_SELECT 已选 {cnt}/{mn}：还要再点 {mn - cnt} 张（同一张再点会取消）。"
                f"下次可一次点完：do select_deck_card --options 0,2"
            )
    in_combat_play = (
        not full
        and action in ("play_card", "use_potion")
        and before.get("screen") == "COMBAT"
        and after.get("screen") == "COMBAT"
    )
    if in_combat_play:
        print(_delta_line(before, after))
        print(compact(after, brief=True))
    else:
        anchor = combat_anchor(after)
        if anchor:
            print(anchor)
        print(compact(after))
    print(f"ACTION {action} {params} -> {resp.get('status')} {resp.get('message')}")
    _meme_hint()


POTION_RANK_HIGH = ("STRENGTH", "ENERGY", "DEXTERITY", "FIRE", "BLOOD", "EXPLOSIVE",
                    "FEAR", "ESSENCE", "AMBROSIA", "FAIRY", "GHOST", "DUPLICATION")
POTION_RANK_MID = ("WEAK", "VULNERABLE", "FRAIL", "POISON", "BLOCK", "SMOKE", "SWIFT",
                   "REGEN", "RECOVERY", "ATTACK", "POWER", "FRUIT", "CUNNING", "LIQUID")


def potion_rank(pid: str) -> int:
    """药水价值粗排：0 越高越该留。只为「满格时该丢哪瓶」服务，不参与战斗判断。"""
    u = str(pid or "").upper()
    if any(k in u for k in POTION_RANK_HIGH):
        return 0
    if any(k in u for k in POTION_RANK_MID):
        return 1
    return 2


def card_pick_score(c: dict) -> float:
    """奖励卡的一张牌值多少分（自动结算用）。

    run91 量过：奖励界面真正贵的不是引擎（/state 10-40ms），而是**多花一个模型回合**
    （logs/run_*.jsonl 里 REWARD 前那一步实测 95–96 秒）。所以卡牌要能自动挑，
    让「打死怪」和「收完奖励」落在同一个 tool call 里。
    评分只做粗筛：伤害/费优先，AOE、0 费、力量引擎加分，诅咒/状态直接 -99。
    """
    cid = str(c.get("card_id") or c.get("id") or "")
    if _bad_card_kind(c):
        return -99.0
    meta = CARD_DB.get(cid, {})
    try:
        cost = float(c.get("energy_cost", c.get("cost", meta.get("cost", 1))) or 1)
    except Exception:
        cost = 1.0
    if cost < 0:
        cost = 3.0                      # X 费按 3 算
    dmg = blk = 0.0
    for v in c.get("dynamic_values") or []:
        if not isinstance(v, dict):
            continue
        nm = str(v.get("name") or "").lower()
        try:
            cv = float(v.get("current_value", v.get("value")))
        except Exception:
            continue
        if nm.startswith("damage"):
            dmg += cv
        elif nm.startswith("block"):
            blk += cv
    desc = _strip_tags(str(c.get("resolved_rules_text") or c.get("rules_text")
                           or meta.get("description") or ""))
    if not dmg:
        m = re.search(r"造成\s*(\d+)\s*点伤害", desc) or re.search(r"Deal\s+(\d+)\s+damage", desc)
        if m:
            dmg = float(m.group(1))
    if not blk:
        m = re.search(r"获得\s*(\d+)\s*点格挡", desc) or re.search(r"Gain\s+(\d+)\s+Block", desc)
        if m:
            blk = float(m.group(1))
    score = dmg * 1.0 + blk * 0.5 - cost * 2.0
    if dmg and ("所有" in desc or "ALL" in desc.upper()):
        score += 3.0                    # AOE
    if cost == 0:
        score += 2.0
    if "power" in str(meta.get("type") or "").lower():
        score += 3.0
    for kw, bonus in (("力量", 4.0), ("易伤", 1.5), ("虚弱", 1.5),
                      ("抽", 1.0), ("消耗", -0.8), ("失去", -0.8)):
        if kw in desc:
            score += bonus
    return round(score, 2)


def pick_best_card(cards: list[dict]) -> tuple[int, float, str]:
    """返回 (下标, 分数, 名字)；空列表返回 (-1, -99, "")。"""
    best_i, best_s, best_n = -1, -99.0, ""
    for i, c in enumerate(cards or []):
        if not isinstance(c, dict):
            continue
        s = card_pick_score(c)
        if s > best_s:
            cid = str(c.get("card_id") or c.get("id") or "")
            best_i, best_s, best_n = i, s, str(CARD_DB.get(cid, {}).get("name") or c.get("name") or cid)
    return best_i, best_s, best_n


def cmd_rewards(take=None, potion="skip", thought="", meme="", full=False,
                pick="auto", quiet=False):
    """一次调用把整个 REWARD 界面收干净（run91 修：结算慢的根因是拆成 5 个回合）。

    根因：原来结算 = claim 金币 → claim 药水 → claim 卡 → choose/skip → collect，
    每一步一次独立进程 + 一次独立回合（模型延迟 1-2 分钟/回合），外加护栏
    POTION_SLOTS_FULL / DECK_PACE / CARD_SELECTION_OPEN 各自逼出一次失败重试，
    于是奖励界面要停 5-10 分钟才「说话」。引擎本身很快（/state 实测 10-40ms），
    慢的全是**回合往返**。所以整个流程放进一个进程里循环，一轮说完再走。

    run92 再进一步：`pick="auto"` 用 `card_pick_score` 自动挑牌，`quiet=True` 给
    `tools/act.py` 在「清场」那一步内部直接调用 → 打死怪+收奖励共用一个 tool call，
    奖励界面不再吃掉一整个模型回合。

    用法：
      python sts2.py rewards                  # 金币/药水照领，卡牌自动挑（分数不够就跳）
      python sts2.py rewards --take 1         # 卡牌奖励拿第 1 张（显式选择，绕 pace 但会报牌组数）
      python sts2.py rewards --pick skip      # 一张都不拿
      python sts2.py rewards --potion swap    # 药水满格时丢最差的一瓶换新
    """
    steps = []
    handled: set[int] = set()
    prof: list[tuple[str, float]] = []
    t_all = time.time()
    with perf.stage("rewards") as _st:
        return _rewards_body(take, potion, thought, meme, full, pick, quiet,
                             steps, handled, prof, t_all, _st)


def _rewards_body(take, potion, thought, meme, full, pick, quiet,
                  steps, handled, prof, t_all, _st):
    after = get_state()
    _st.poll()
    prof.append(("read_state", time.time() - t_all))
    if thought or meme:
        t_say = time.time()
        overlay_say(tag_for(after.get("screen", "")), thought, meme, after, force=True)
        prof.append(("say", time.time() - t_say))
    t_loop = time.time()
    for _round in range(8):
        st = get_state()
        _st.poll()
        screen = st.get("screen")
        if screen == "CARD_SELECTION":
            # 已经站在选牌界面上（上一轮 claim 打开了卡牌奖励）
            cards = (st.get("selection") or {}).get("cards") or []
            want = take
            if want is None and pick == "auto" and cards:
                bi, bs, bn = pick_best_card(cards)
                cap = deck_cap(run_act(st.get("run") or {}))
                n = deck_size(st)
                # 牌组还没满预算 → 分数 >1 就拿；已满 → 只有强牌（≥6 分，通常是有伤害的 AOE/力量）才加
                limit = 1.0 if n < cap else 6.0
                if bi >= 0 and bs >= limit:
                    want = bi
                    steps.append(f"pick_auto[{bi}]{bn}({bs})")
                else:
                    steps.append(f"pick_skip(best={bn} {bs}<{limit})")
            if want is not None:
                try:
                    act("choose_reward_card", option_index=_int(want, 0))
                    steps.append(f"choose_reward_card[{want}]")
                except Exception as e:
                    steps.append(f"choose_failed:{e}")
                    try:
                        act("skip_reward_cards")
                        steps.append("skip_reward_cards")
                    except Exception:
                        pass
            else:
                try:
                    act("skip_reward_cards")
                    steps.append("skip_reward_cards")
                except Exception as e:
                    steps.append(f"skip_failed:{e}")
            st = _await_change(st)
            continue
        if screen != "REWARD":
            break
        rew = st.get("reward") or {}
        rewards = [r for r in (rew.get("rewards") or []) if isinstance(r, dict)]
        # 关键：skip_reward_cards 之后，卡牌奖励条目在 rewards 里**仍然是未领状态**，
        # 只有 pending_card_choice 会变 false（既然这样，就用本地 handled 记账）。
        todo = [r for r in rewards
                if not (r.get("is_claimed") or r.get("claimed"))
                and _int(r.get("index"), 0) not in handled]
        if not todo:
            # 全都处理过 → 收画面
            try:
                act("collect_rewards_and_proceed")
                steps.append("collect")
            except Exception as e:
                steps.append(f"collect_failed:{e}")
            st = _await_change(st)
            continue
        progressed = False
        for r in todo:
            idx = _int(r.get("index"), 0)
            t = str(r.get("reward_type") or r.get("type") or "").lower()
            if "card" in t:
                handled.add(idx)
                try:
                    act("claim_reward", option_index=idx)
                    steps.append(f"claim_card[{idx}]")
                    progressed = True
                except Exception as e:
                    steps.append(f"claim_card_failed:{e}")
                break                      # 打开选牌后必须去 CARD_SELECTION 分支处理
            if "potion" in t:
                pid = str(r.get("potion_id") or r.get("id") or "")
                handled.add(idx)
                if free_potion_slots(st) > 0:
                    try:
                        act("claim_reward", option_index=idx)
                        steps.append(f"claim_potion[{idx}]{pid}")
                        progressed = True
                    except Exception as e:
                        steps.append(f"claim_potion_failed:{e}")
                else:
                    slots = [p for p in potion_slots(st) if p.get("potion_id")]
                    worst = max(slots, key=lambda p: potion_rank(p.get("potion_id")),
                                default=None) if slots else None
                    swap_ok = potion == "swap" and worst is not None \
                        and potion_rank(pid) < potion_rank(worst.get("potion_id"))
                    if swap_ok:
                        try:
                            act("discard_potion", option_index=_int(worst.get("index"), 0))
                            steps.append(f"discard[{worst.get('index')}]{worst.get('potion_id')}")
                            act("claim_reward", option_index=idx)
                            steps.append(f"claim_potion[{idx}]{pid}")
                            progressed = True
                        except Exception as e:
                            steps.append(f"swap_failed:{e}")
                    else:
                        steps.append(f"potion_left[{idx}]{pid}(满格)")
                break                      # 药水/金币一次只动一个，避免下标错位
            # 金币 / 遗物 / 其它：直接领
            handled.add(idx)
            try:
                act("claim_reward", option_index=idx)
                steps.append(f"claim[{idx}]{t or 'gold'}")
                progressed = True
            except Exception as e:
                steps.append(f"claim_failed:{e}")
            break
        if not progressed:
            # 没有任何能推进的动作 → 直接收画面，别死循环
            try:
                act("collect_rewards_and_proceed")
                steps.append("collect")
            except Exception as e:
                steps.append(f"collect_failed:{e}")
                break
        st = _await_change(st)
    prof.append(("loop_acts", time.time() - t_loop))
    t_wait = time.time()
    after = wait_stable()
    prof.append(("wait_stable", time.time() - t_wait))
    n = deck_size(after)
    run = after.get("run") or {}
    cap = deck_cap(run_act(run))
    summary = {"steps": steps, "deck": n, "cap": cap, "screen": after.get("screen"),
               "state": after, "fat": n > cap,
               "ms": int((time.time() - t_all) * 1000),
               "profile": [(k, int(v * 1000)) for k, v in prof]}
    if not quiet:
        print("REWARDS " + (" → ".join(steps) if steps else "(没有可收的奖励)"))
        print(f"REWARDS_RESULT deck={n}（pace {cap}）screen={after.get('screen')}")
        slow = sorted(summary["profile"], key=lambda kv: -kv[1])[:3]
        print("REWARDS_MS total=%dms 最慢 %s" % (
            summary["ms"], " ".join(f"{k}={v}ms" for k, v in slow)))
        if n > cap:
            print(f"REWARDS_DECK_FAT 牌组 {n} 超过这个阶段预算 {cap}：下次别再拿牌，先去商店删一张。")
        anchor = combat_anchor(after)
        if anchor:
            print(anchor)
        print(compact(after, full=full))
        _meme_hint()
    return summary


_BATTLE_OVER = object()   # 战斗已经结束（清场/选牌/奖励）：不会再有「玩家回合」


def _player_turn_state(timeout=8.0):
    """等到「玩家回合」快照再返回；超时返回 None（绝不盲发动作）。

    run92 修的结算卡顿根因：清场那一刀之后，批量里剩下的 `end` 步还会来等
    「玩家回合」，屏幕已经是 REWARD/MAP —— 旧版只会对着非战斗屏幕空转满
    8 秒超时，外层再重试 10 次（8s × 11 ≈ **90 秒**），实测清场这一步
    `run_batch` 92.6 秒，而真正的结算只有 1.1 秒。屏幕一旦不是 COMBAT 就
    立刻返回 `_BATTLE_OVER`，让调用方直接走「跳过 end」的分支。
    """
    with perf.stage("player_turn_state") as _st:
        end = time.time() + timeout
        t0 = time.time()
        while True:
            s = get_state()
            _st.poll()
            acts = s.get("available_actions") or []
            if s.get("screen") == "COMBAT" and ("play_card" in acts or "end_turn" in acts):
                return s
            if (s.get("screen") or "") != "COMBAT":
                return _BATTLE_OVER
            if time.time() >= end:
                return None
            # 出牌动画一般 150–400ms 就完：前 0.6 秒密一点（50ms），之后放松到 150ms。
            # 固定 150ms 起步会每步白等一个周期，一手 4–5 步就是 0.3–0.5 秒。
            _sleep(0.05 if time.time() - t0 < 0.6 else 0.15, _st)


def _delta_line(before: dict, after: dict) -> str:
    """一行战况增量，替代整份 state（batch 中间步只打这个）。"""
    def _c(s):
        return s.get("combat") or {}
    b, a = _c(before), _c(after)
    bp, ap = b.get("player") or {}, a.get("player") or {}
    br, ar = before.get("run") or {}, after.get("run") or {}
    bits = [
        f"E{bp.get('energy', '?')}→{ap.get('energy', '?')}",
        f"blk{ap.get('block', 0)}",
        f"hp{ar.get('current_hp', '?')}",
    ]
    ben = {str(e.get("enemy_id")) + str(i): e for i, e in enumerate(b.get("enemies") or [])}
    aen = {str(e.get("enemy_id")) + str(i): e for i, e in enumerate(a.get("enemies") or [])}
    dead = [k for k in ben if k not in aen]
    if dead:
        bits.append("dead:" + ",".join(str(v.get("enemy_id")) for v in (ben[k] for k in dead)))
    hps = " ".join(
        f"e{i}:{e.get('hp')}" for i, e in enumerate(a.get("enemies") or [])
    )
    if hps:
        bits.append(hps)
    bits.append(f"hand{len(a.get('hand') or [])}")
    return " · ".join(bits)


# ---------------------------------------------------------------- 动作回执
# mod 只回一个 status，**不等于动作真的结算了**（run105 T7 药水就是这么丢的）：
#   completed = 已结算
#   pending   = "Action queued but state is still transitioning."（排队中，结没结算不知道）
#   其它/缺失 = unknown
# 历史账本 19,664 步里 307 步是 pending（use_potion 72、play_card 109、claim_reward 82…），
# 以前这些统统按成功记账。判定一律看**前后状态差分**，接口说什么都不算数。
_STATUS_OK = frozenset(("completed", "ok", "success"))
_STATUS_PENDING = frozenset(("pending", "queued"))


def status_kind(status) -> str:
    s = str(status or "").strip().lower()
    if s in _STATUS_OK:
        return "ok"
    if s in _STATUS_PENDING:
        return "pending"
    return "unknown"


def _pot_slots(st: dict) -> dict:
    out = {}
    run = st.get("run") or {}
    for i, p in enumerate(run.get("potions") or []):
        if not isinstance(p, dict):
            continue
        pid = p.get("potion_id") or p.get("id") or ""
        if not pid or p.get("occupied") is False:
            continue
        out[_int(p.get("index"), i)] = str(pid)
    return out


def _enemy_hps(st: dict) -> dict:
    out = {}
    for i, e in enumerate((st.get("combat") or {}).get("enemies") or []):
        if not isinstance(e, dict):
            continue
        out[_int(e.get("index"), i)] = _int(e.get("current_hp", e.get("hp")), 0)
    return out


def _ui_sig(st: dict):
    """非战斗界面的粗指纹（奖励/选牌/事件/商店的载荷长度），用于判断非战斗动作是否落地。"""
    for k in ("reward", "choice", "selection", "shop", "event", "rest", "map"):
        v = st.get(k)
        if v:
            try:
                return (k, len(json.dumps(v, ensure_ascii=False, sort_keys=True)))
            except Exception:  # noqa: BLE001
                return (k, 0)
    return None


def _snap(st: dict) -> dict:
    """只取「动作一落地就会变」的字段，用来做前后差分（不含动画/计时噪声）。"""
    c = st.get("combat") or {}
    p = c.get("player") or {}
    run = st.get("run") or {}
    return {
        "screen": st.get("screen"),
        "ui": _ui_sig(st),
        "turn": _int(c.get("turn", st.get("turn")), 0),
        "floor": _int(run.get("floor"), 0),
        "hand": len([x for x in (c.get("hand") or []) if isinstance(x, dict)]),
        "e": _int(p.get("energy"), 0),
        "blk": _int(p.get("block"), 0),
        "hp": _int(p.get("current_hp", run.get("current_hp", run.get("hp"))), 0),
        "pot": tuple(sorted(_pot_slots(st).items())),
        "en": tuple(sorted(_enemy_hps(st).items())),
        "pw": tuple(sorted(str(x.get("power_id") or x.get("id")) + str(x.get("amount"))
                           for x in (p.get("powers") or []) if isinstance(x, dict))),
    }


_DIFF_WHY = {"pot": "药水槽", "en": "敌人血量", "hand": "手牌数", "e": "能量", "blk": "格挡",
             "hp": "自己血量", "pw": "buff", "turn": "回合数", "floor": "层数",
             "screen": "界面", "ui": "界面内容"}


def verify_effect(action: str, params: dict, before: dict, after: dict, status=None) -> dict:
    """动作到底有没有落地：只看前后状态差分。

    返回 {"a","status","effect","why"}：
      applied    = 状态里看得到变化（真的结算了）
      no_effect  = 接口说 completed，但状态一点没变（**假成功**）
      unverified = pending/unknown 且看不到变化（不敢说成功，也不敢说失败）
    """
    b, a = _snap(before or {}), _snap(after or {})
    diff = [k for k in b if b[k] != a[k]]
    p = params or {}
    if action == "use_potion":
        slot = _int(p.get("option_index", p.get("p")), -1)
        gone = slot >= 0 and dict(b["pot"]).get(slot) != dict(a["pot"]).get(slot)
        hit = any(k in diff for k in ("en", "hp", "blk", "pw"))
        ok = bool(gone or hit)
    elif action == "play_card":
        ok = bool(diff)   # 出牌必然动 手牌/能量/格挡/敌血/buff 之一
    elif action == "end_turn":
        ok = any(k in diff for k in ("turn", "screen", "hand", "floor", "en"))
    else:
        ok = bool(diff)
    kind = status_kind(status)
    if ok:
        eff = "applied"
    elif kind == "ok":
        eff = "no_effect"
    else:
        eff = "unverified"
    return {"a": action, "status": status, "effect": eff,
            "why": ",".join(_DIFF_WHY.get(k, k) for k in diff) or "状态无变化"}


def _settle(a: str, params: dict, before: dict, resp: dict, after: dict,
            tries: int = 6) -> tuple[dict, dict]:
    """pending/unknown 时多看几眼再看结论：动画/结算经常慢半拍。"""
    rc = verify_effect(a, params, before, after, (resp or {}).get("status"))
    if rc["effect"] == "applied" or rc["effect"] == "no_effect":
        return after, rc
    for _ in range(tries):
        _sleep(0.25)
        after = get_state()
        rc = verify_effect(a, params, before, after, (resp or {}).get("status"))
        if rc["effect"] == "applied":
            rc["recovered"] = True
            return after, rc
    return after, rc


def _emit_receipt(items: list) -> None:
    """打印机器可读回执；有动作没落地就停在这，别让上层当成功。"""
    # 空列表=整批都是「战斗已结束」的跳过步，没有可校验的动作，不算丢失。
    ok = all(x.get("effect") == "applied" for x in items)
    print("RECEIPT " + json.dumps({"ok": ok, "items": items}, ensure_ascii=False,
                                  separators=(",", ":")))
    if not ok:
        bad = next((x for x in items if x.get("effect") != "applied"), items[0])
        print(f"RECEIPT_LOST 第 {bad.get('i')} 步 {bad.get('a')} status={bad.get('status')} "
              f"effect={bad.get('effect')}（{bad.get('why')}）：接口收下了但状态里没落地。"
              "别把这一步当执行过、也别重发后面的动作——先 python tools/ctx.py 看真实局面。")
        print(compact(get_state(), brief=True))
        sys.exit(4)


def _plan_cost(state: dict, items: list) -> tuple[int, int]:
    """本回合计划的费用合计 vs 当前能量（只数当前手牌里能对上的牌）。"""
    combat = state.get("combat") or {}
    energy = _int((combat.get("player") or {}).get("energy"), 0)
    hand = [c for c in (combat.get("hand") or []) if isinstance(c, dict)]
    by_id = {}
    for c in hand:
        cid = str(c.get("card_id") or c.get("id") or "")
        cost = _int(c.get("energy_cost", c.get("cost")), 0)
        by_id.setdefault(cid, max(0, cost))
    total = 0
    for it in items:
        if it.get("action") != "play_card":
            continue
        cid = str(it.get("card_id") or "")
        if cid and cid in by_id:
            total += by_id[cid]
        elif "card_index" in it:
            k = _int(it.get("card_index"), -1)
            if 0 <= k < len(hand):
                total += max(0, _int(hand[k].get("energy_cost", hand[k].get("cost")), 0))
    return energy, total


def cmd_batch(spec: str):
    """一次调用打完整个回合，并直接吐出下一回合的快照。

    为什么这么设计：模型每多一次工具调用，就把整段上下文（实战 30 万 token 级）
    重新读一遍。旧协议「每刀一条 play + 每回合一条 state」= 5–7 次请求/回合；
    这里压成 1 次。安全性不靠模型重复读数，靠工具逐步重解析 card_id：
      · 每步按当前手牌重新找下标（手牌压缩不会打错牌）
      · 缺牌/非法立刻 STOP 并回快照（绝不盲打，也不吞动作）
      · 费用超预算在动手前就打回（不打半截回合）
      · end_turn 之后等新回合手牌抽稳，直接打完整 state（下一回合不用再 state）
    """
    try:
        items = json.loads(spec)
    except json.JSONDecodeError as e:
        print(f"BATCH_BAD_JSON {e}")
        sys.exit(2)
    # act.py 已经逐步模拟过费用（回能/变费也能算），这里可跳过「整手预扣」的粗检。
    skip_budget = bool(items.get("skip_budget")) if isinstance(items, dict) else False
    if isinstance(items, dict):
        items = items.get("actions", [])
    if not items:
        print("BATCH_EMPTY 没有动作；要结束回合就写 {\"action\":\"end_turn\"}")
        sys.exit(2)
    ends = [i for i, it in enumerate(items) if it.get("action") == "end_turn"]
    if len(ends) > 1:
        print("BATCH_FIX end_turn 只能出现一次（重复 end 会把下一回合整回合空过）")
        sys.exit(2)
    if ends and ends[-1] != len(items) - 1:
        print("BATCH_FIX end_turn 必须是最后一步（后面的动作会落到新回合，牌序已变）")
        sys.exit(2)

    start = get_state()
    if start.get("screen") == "COMBAT" and not skip_budget:
        energy, need = _plan_cost(start, items)
        if need > energy:
            print(
                f"PLAN_OVER_BUDGET 这手要 {need} 费、只剩 {energy}：一张都不打，"
                "改计划重发（打半截回合最亏）"
            )
            print(compact(start, brief=True))
            sys.exit(2)

    ended = False
    after = start
    # 一个回合只说一句话，而且要在出牌之前说：先预热 TTS 再推字幕，
    # 话音和第一张牌几乎同时起。放到回合末尾说，就成了「画面走完了才开口」。
    _th = next((str(x.get("thought") or "") for x in items if x.get("thought")), "")
    _mm = next((str(x.get("meme") or "") for x in items if x.get("meme")), "")
    if _th or _mm:
        overlay_say(tag_for(start.get("screen", "")), _th, _mm, start, force=True)
    st = None
    receipts: list = []
    for i, it in enumerate(items):
        perf.count("batch_step")
        a = it.get("action")
        if not a:
            print(f"batch item {i} missing action")
            sys.exit(2)
        if i > 0:
            st = _player_turn_state()
            if st is _BATTLE_OVER:
                # 清场/进选牌：屏幕已经不是战斗，重试只会白等 8 秒 × 10。
                st = None
            elif st is None:
                # 药水/连打后引擎偶尔会短暂没有玩家回合快照（动画/重绘）。
                # 以前这里直接 exit 3，结果「一半动作执行了、一半没执行」，很坑。
                for _ in range(6):
                    _sleep(0.12)
                    st = _player_turn_state(timeout=2.0)
                    if st is _BATTLE_OVER:
                        st = None
                        break
                    if st is not None:
                        break
            if st is None:
                if a == "end_turn":
                    # 上一刀已经打完战斗/进选牌：没有玩家回合可结束，别把 end_turn 发出去。
                    print(f"[{i}] end_turn 跳过：战斗已经结束（没有玩家回合）")
                    ended = True
                    break
                print(f"BATCH STOP at {i} {a}: 没有玩家回合快照（战斗结束/进了选牌/敌人回合）")
                print(f"BATCH PARTIAL {i}/{len(items)}：前 {i} 步已执行，下一步请先 python tools/ctx.py 看真实状态再续，别重发整批")
                print(compact(get_state()))
                sys.exit(3)
        before = st if st is not None else get_state()
        if (
            i == 0
            and before.get("screen") == "COMBAT"
            and a in ("play_card", "end_turn")
            and a not in (before.get("available_actions") or [])
        ):
            # 先点进战斗/敌人回合还没完：等一会儿，别把动作丢进过渡态。
            waited = wait_legal(a, timeout=5.0)
            if waited is not None and a in (waited.get("available_actions") or []):
                before = waited
            else:
                print(f"BATCH STOP at {i} {a}: 当前不是玩家回合（{before.get('screen')}），没有发出动作")
                print(compact(before))
                sys.exit(3)
        if a == "end_turn":
            if before.get("screen") != "COMBAT":
                # 最后一刀已经把战斗打完/进选牌了：end_turn 会 invalid_action，直接收工。
                print(f"[{i}] end_turn 跳过：已经是 {before.get('screen')}（战斗结束，别补刀）")
                ended = True
                break
            try:
                resp = act("end_turn")
            except Exception as e:
                print(f"BATCH STOP at end_turn: {e}")
                print(compact(get_state()))
                sys.exit(2)
            prev = len(((before.get("combat") or {}).get("hand") or []))
            after = wait_new_turn_hand(prev) or get_state()
            after, rc = _settle(a, {}, before, resp or {}, after)
            log(a, {}, before, after, resp or {}, it.get("thought", ""), it.get("meme", ""),
                effect=rc["effect"])
            receipts.append({"i": i, "a": a, "status": rc["status"], "effect": rc["effect"],
                             "why": rc["why"]})
            print(f"[{i}] end_turn -> {resp.get('status')} | {_delta_line(before, after)}")
            ended = True
            break
        params = {k: it[k] for k in ("card_index", "target_index", "option_index") if k in it}
        # 允许按 card_id 指定，客户端每步按当前手牌重新解析下标（手牌会压缩）。
        cid = it.get("card_id")
        if cid and "card_index" not in params:
            hand = (before.get("combat") or {}).get("hand") or []
            cands = [k for k, c in enumerate(hand)
                     if isinstance(c, dict) and str(c.get("card_id")) == str(cid)
                     and c.get("playable", True) is not False]
            # 同名多张（被变费/升级过）优先打「当下最便宜」的那张，避免 0 费牌没打掉。
            idx = min(cands, key=lambda k: (_int(hand[k].get("energy_cost", hand[k].get("cost")), 99),
                                            -_int(hand[k].get("upgraded"), 0))) if cands else None
            if idx is None:
                print(f"BATCH STOP at {i}: 手里没有可打的 {cid}（牌序已变）")
                print(compact(before))
                sys.exit(3)
            params["card_index"] = idx
        # 目标已死 → 自动改打最低血活敌人，别把整刀浪费在尸体上。
        # target_index 是接口的原始敌人下标（enemies[i].index），不是 alive 列表位置。
        if a == "play_card" and "target_index" in params:
            alive = _alive_enemies(before)
            ti = params.get("target_index")
            if ti is not None:
                if not alive:
                    print(f"BATCH STOP at {i} {a}: 目标 {ti} 不在场且没有活怪，拒绝空打")
                    print(compact(before))
                    sys.exit(3)
                targets = {_enemy_index(before, e) for e in alive}
                if _int(ti, -1) not in targets:
                    hi = min(alive, key=_enemy_hp)
                    new_ti = _enemy_index(before, hi)
                    params["target_index"] = new_ti
                    print(f"TARGET_FIX 目标 {ti} 已不在场 → 改打最低血敌人 {new_ti}")
        thought = it.get("thought", "")
        meme = it.get("meme", "")
        try:
            resp = act(a, **params)
        except Exception as e:
            print(f"BATCH STOP at {i} {a}: {e}")
            sys.exit(2)
        after = resp.get("state") or get_state()
        if a == "play_card":
            # 过渡快照守卫：动作后状态没变，说明撞上了出牌动画；等一手再读，
            # 否则下一步 card_index 会解析到旧手牌（run55 的 ACTION_SKIPPED 就是这么来的）。
            for _ in range(3):
                if _fingerprint(after) != _fingerprint(before):
                    break
                _sleep(0.12)
                after = get_state()
        after, rc = _settle(a, params, before, resp or {}, after)
        log(a, params, before, after, resp or {}, thought, meme, effect=rc["effect"])
        receipts.append({"i": i, "a": a, "status": rc["status"], "effect": rc["effect"],
                         "why": rc["why"]})
        print(f"[{i}] {a} {params.get('card_index', params)} -> {resp.get('status')} | {_delta_line(before, after)}")
    _emit_receipt(receipts)
    if ended:
        # 完整快照 = 下一回合的 state：省掉「回合开始再 state 一次」的那次请求。
        print(compact(after))
    else:
        print(compact(get_state(), brief=True))
    _meme_hint()


def wait_stable(need_player=False, timeout=25):
    """等一个「真能操作」的快照再打印：过滤动画过渡帧（LEGAL 只有 save_and_quit）。

    batch --wait player 用它省掉「打完再看一眼」的第二次工具调用；
    连读两次都稳定才算稳，避免把过渡帧当回合开始。
    """
    with perf.stage("wait_stable") as _st:
        end = time.time() + timeout
        last = None
        while time.time() < end:
            s = get_state()
            _st.poll()
            acts = set(s.get("available_actions") or [])
            screen = s.get("screen") or ""
            busy = not acts or acts <= {"save_and_quit", "discard_potion"}
            if busy:
                last = None
                _sleep(0.2, _st)
                continue
            if need_player and screen == "COMBAT" and not ({"play_card", "end_turn"} & acts):
                last = None
                _sleep(0.2, _st)
                continue
            key = (screen, tuple(sorted(acts)), ((s.get("combat") or {}).get("hand") or []) and
                   len(((s.get("combat") or {}).get("hand") or [])), (s.get("run") or {}).get("current_hp"))
            if key == last:
                print(compact(s))
                return s
            last = key
            _sleep(0.15, _st)
        print("WAIT_TIMEOUT")
        print(compact(get_state(), brief=True))
        return None


def cmd_wait(screen=None, player_turn=False, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        s = get_state()
        if screen and s.get("screen") != screen:
            _sleep(0.25)
            continue
        if player_turn:
            acts = s.get("available_actions") or []
            if "play_card" not in acts and "end_turn" not in acts:
                # 选牌/事件/商店不是「战斗回合」，等下去只会白等 30 秒。
                if s.get("screen") != "COMBAT":
                    print(
                        f"WAIT ABORT --player-turn 不适用：当前 screen={s.get('screen')}"
                        f"（不是战斗回合；多选界面看 SELECTION_STATE，或直接 do <action>）"
                    )
                    print(compact(s))
                    return
                _sleep(0.25)
                continue
        print(compact(s))
        return
    print("WAIT TIMEOUT")
    print(compact(get_state()))
    sys.exit(3)


def cmd_say(thought, meme):
    s = get_state()
    overlay_say(tag_for(s.get("screen", "")), thought, meme, s)
    print("ok")
    _meme_hint()


def cmd_quip(lines):
    """一次推多句台词（省 token）：先并行预热 TTS，再按顺序推给页面排队播。"""
    s = get_state()
    tag = tag_for(s.get("screen", ""))
    cleaned = []
    for ln in lines:
        t = str(ln or "").strip()
        if t:
            cleaned.append(t)
    if not cleaned:
        return
    threads = []
    for ln in cleaned:
        th = threading.Thread(target=_prewarm_tts, args=(_ensure_stop(ln),), daemon=True)
        th.start()
        threads.append(th)
    for th in threads:
        th.join(timeout=25)
    for ln in cleaned:
        overlay_say(tag, ln, "", s, force=True)
    print(f"ok {len(cleaned)} 句")


# ---------------------------------------------------------------- memory
def _session_entries(limit: int | None = None) -> list[dict]:
    """纯读本局 JSONL（不新建会话文件）。"""
    if not os.path.exists(SESSION_PTR):
        return []
    try:
        with open(SESSION_PTR, encoding="utf-8") as f:
            path = json.load(f).get("path")
    except Exception:
        return []
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if isinstance(e, dict):
                    out.append(e)
    except Exception:
        return []
    return out[-limit:] if limit else out


def _key_cards(deck: list, cap: int = 10) -> list[str]:
    """牌组里的「非基础牌」清单（同名去重、带升级标记），用来一眼认出这套牌靠什么赢。"""
    basic = {"STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "ASCENDERS_BANE"}
    seen = {}
    order = []
    for c in deck:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("card_id") or c.get("id") or "?")
        if cid in basic or _bad_card_kind(c) == "curse":
            continue
        up = "+" if (c.get("upgraded") or c.get("is_upgraded")) else ""
        key = cid + up
        if key not in seen:
            seen[key] = 0
            order.append(key)
        seen[key] += 1
    out = [f"{k}x{seen[k]}" if seen[k] > 1 else k for k in order]
    return out[:cap]


def _hp_curve(entries: list[dict], cap: int = 8) -> list[tuple]:
    curve = []
    for e in entries:
        f, hp = e.get("floor"), e.get("hp")
        if f is None or hp is None:
            continue
        if curve and curve[-1][0] == f:
            curve[-1] = (f, hp)
        else:
            curve.append((f, hp))
    if len(curve) > cap:
        step = len(curve) / float(cap - 1)
        curve = [curve[int(round(i * step))] for i in range(cap - 1)] + [curve[-1]]
    return curve


def memory_digest(state: dict) -> list[str]:
    """分层记忆的 L1：一屏结构化摘要（换幕/新 session 时读它，不读旧对话）。"""
    run = state.get("run") or {}
    deck = run.get("deck") or []
    stats = _deck_stats(state)
    entries = _session_entries()
    hp = run.get("current_hp", run.get("hp", "?"))
    mhp = run.get("max_hp", "?") or 1
    try:
        pct = 100.0 * float(hp) / float(mhp)
    except Exception:
        pct = 0.0
    act = run.get("act_id", run.get("act", "?"))
    try:
        act = int(act) + 1
    except Exception:
        pass
    floor = run.get("floor", "?")
    sl = sum(1 for e in entries if e.get("action") == "save_and_quit")
    lines = [
        f"MEMORY {state.get('run_id', '?')} | {run.get('character_id', 'IRONCLAD')} "
        f"A{run.get('ascension', '?')} | act{act} f{floor} | "
        f"HP {hp}/{mhp} ({pct:.0f}%) | gold {run.get('gold', '?')} | deck {stats['size']} | SL {sl}/3"
    ]
    lines.append(
        f"DECK {stats['size']}: atk{stats['attack']} def{stats['defense']} "
        f"skill{stats['skill']} pwr{stats['power']} curse{stats['curses']} up{stats['upgraded']} "
        f"| AOE {stats['aoe']}"
    )
    keys = _key_cards(deck)
    if keys:
        lines.append("KEY " + ",".join(keys))
    relics = [str((r or {}).get("relic_id") or r) for r in (run.get("relics") or []) if isinstance(r, dict)]
    if relics:
        lines.append("RELICS " + ",".join(relics))
    curses = [str(c.get("card_id")) for c in deck if isinstance(c, dict) and _bad_card_kind(c) == "curse"]
    if curses:
        lines.append("CURSES " + ",".join(sorted(set(curses))))
    curve = _hp_curve(entries)
    if curve:
        lo = min(c[1] for c in curve)
        lines.append("HP_CURVE " + " ".join(f"f{f}:{h}" for f, h in curve) + f" | min {lo}")
    plan = {}
    if os.path.exists(PLAN_PATH):
        try:
            with open(PLAN_PATH, encoding="utf-8") as f:
                plan = json.load(f) or {}
        except Exception:
            plan = {}
    if plan:
        note = plan.get("note") or ""
        unit = plan.get("opt", plan.get("unit"))
        if note or unit is not None:
            head = f"PLAN opt{unit}" if unit is not None else "PLAN"
            lines.append(f"{head} {note}".strip())
    tail = entries[-5:]
    if tail:
        lines.append(
            "LAST " + " | ".join(
                f"f{e.get('floor', '?')} {str(e.get('action', '?'))[:18]} hp{e.get('hp', '?')}" for e in tail
            )
        )
    try:
        import decision_log
        rid = state.get("run_id") or (state.get("run") or {}).get("run_id")
        n = sum(1 for r in decision_log.read_decisions()
                if rid and str(r.get("run_id")) == str(rid))
        if n:
            lines.append(f"DECISIONS {n} (logs/decisions.jsonl)")
    except Exception:
        pass
    risk = []
    if pct < 50:
        risk.append("血闸<50%")
    elif pct < 60:
        risk.append("精英闸<60%")
    elif pct < 70:
        risk.append("Boss闸<70%")
    if stats["size"] > 18:
        risk.append(f"牌组{stats['size']}>18")
    if stats["curses"]:
        risk.append(f"诅咒{stats['curses']}")
    if stats["aoe"] == 0:
        risk.append("AOE=0")
    if risk:
        lines.append("RISK " + " | ".join(risk))
    return lines


def cmd_memory(write: bool = False):
    lines = memory_digest(get_state())
    text = "\n".join(lines)
    print(text)
    if write:
        path = os.path.join(LOGS, "run_memory.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 本局结构化记忆（L1，读这个就够）\n\n")
            f.write(f"- 更新时间：{datetime.now().isoformat(timespec='seconds')}\n")
            for line in lines:
                f.write(f"- {line}\n")
            f.write("\n> L2 当前状态：`python sts2.py state`；L3 原始流水：`python sts2.py journal --floor <n>`\n")
        print(f"MEMORY_WRITTEN {path}")


def cmd_journal(floor: int | None = None, last: int = 20):
    entries = _session_entries()
    if floor is not None:
        entries = [e for e in entries if e.get("floor") == floor]
    entries = entries[-max(1, last):]
    if not entries:
        print("JOURNAL_EMPTY 没有匹配的记录")
        return
    for e in entries:
        th = str(e.get("thought") or "")[:40]
        print(f"f{e.get('floor', '?')} hp{e.get('hp', '?')} g{e.get('gold', '?')} "
              f"{e.get('action')} {e.get('params')} {('| ' + th) if th else ''}")


def _latest_run_summary() -> dict:
    """读 P0-1 账本写的最近一局 summary；没有就空。"""
    try:
        import decision_log
        p = os.path.join(decision_log.log_dir(), "run_summary.json")
    except Exception:
        p = os.path.join(LOGS, "run_summary.json")
    try:
        if not os.path.exists(p):
            return {}
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def cmd_report():
    path = _session_path()
    out = os.path.splitext(path)[0] + ".md"
    entries = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    lines = ["# 杀戮尖塔 2 · AI 实况战报", ""]
    lines.append(f"- 录像会话：`{path}`")
    lines.append(f"- 动作数：{len(entries)}")
    summary = _latest_run_summary()
    if summary:
        sl = summary.get("sl")
        lines += [
            "",
            "## 最近一局结算（决策账本）",
            f"- run `{summary.get('run_id')}` · {summary.get('result')} · "
            f"f{summary.get('floor')} · HP {summary.get('hp')}/{summary.get('max_hp')} · "
            f"gold {summary.get('gold')} · deck {summary.get('deck_size')}",
            f"- 决策回合 {summary.get('decisions', '?')} · 被拒 {summary.get('rejected', '?')} · "
            f"掉血 {summary.get('hp_lost', '?')} · 药水 {summary.get('potions_used', '?')} · "
            f"SL {sl if sl is not None else '?'}/3",
        ]
    lines.append("")
    for e in entries:
        lines.append(
            f"## {e.get('ts')} · 第{e.get('floor') if e.get('floor') is not None else '?'}层 · {e.get('screen_before')} → {e.get('screen_after')}"
        )
        lines.append(f"- 动作：`{e.get('action')}` {e.get('params')} → {e.get('status')}")
        if e.get("thought"):
            lines.append(f"- 思路：{_clean_speech(e.get('thought'))}")
        if e.get("meme"):
            lines.append(f"- 吐槽：{_clean_speech(e.get('meme'))}")
        lines.append("")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(out)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("state")
    p.add_argument("--raw", action="store_true")
    p.add_argument("--full", action="store_true", help="忽略差异缓存，打印完整药水/遗物/卡牌说明")
    sub.add_parser("acts")
    sub.add_parser("map")
    p = sub.add_parser("route")
    p.add_argument("--commit", type=int, help="把 opt<i> 记为当前路线计划（之后 plan 会拿它和状态对比）")
    p.add_argument("--note", default="", help="计划备注，例如「走商店线，Boss 前留营火」")
    p = sub.add_parser("plan")
    p.add_argument("--clear", action="store_true", help="清空已记录的计划（换幕/改线时用）")
    p.add_argument("--note", default="")
    p = sub.add_parser("play")
    p.add_argument("card_id")
    p.add_argument("--target", type=int)
    p.add_argument("--card", type=int, help="force hand index when several copies exist")
    p.add_argument("--thought", default="")
    p.add_argument("--meme", default="")
    p.add_argument("--full", action="store_true", help="出牌后仍打完整 state（默认只打一行增量 + 手牌/敌人）")
    p = sub.add_parser("do")
    p.add_argument("action")
    p.add_argument("--card", type=int)
    p.add_argument("--target", type=int)
    p.add_argument("--option", type=int)
    p.add_argument("--options", help="多选界面一次点完：逗号分隔下标，如 --options 0,3,5（自动确认）")
    p.add_argument("--thought", default="")
    p.add_argument("--meme", default="")
    p.add_argument("--full", action="store_true", help="动作后打完整 state")
    p.add_argument("--force", action="store_true",
                   help="牌组已到上限时仍要拿这张奖励牌（必须能说出踢掉谁）")
    p = sub.add_parser("batch", help="一次调用打完整个回合（推荐）：actions 列表 + 可选 end_turn")
    p.add_argument("spec", nargs="?", help='JSON：{"actions":[{"action":"play_card","card_id":"BASH","target_index":0},...,{"action":"end_turn"}]}')
    p.add_argument("--file", help="从 UTF-8 文件读 spec（省掉 PowerShell 引号转义，中文更稳）")
    p.add_argument("--wait", choices=["player", "any"], help="打完直接等下一个可操作快照：player=等自己回合，any=等任意稳定界面（省一次工具调用）")
    p.add_argument("--timeout", type=int, default=25)
    p = sub.add_parser("wait")
    p.add_argument("--screen")
    p.add_argument("--player-turn", action="store_true")
    p.add_argument("--timeout", type=int, default=30)
    p = sub.add_parser("say")
    p.add_argument("--thought", default="")
    p.add_argument("--meme", default="")
    p = sub.add_parser("rewards", help="一次收完整个奖励界面（金币/药水/卡牌/collect 全在一个进程里循环）")
    p.add_argument("--take", type=int, default=None,
                   help="卡牌奖励拿第 N 张（0 起）；不给就走 --pick")
    p.add_argument("--pick", choices=["auto", "skip"], default="auto",
                   help="auto=用 card_pick_score 自动挑（默认）；skip=一张都不拿")
    p.add_argument("--potion", choices=["skip", "swap"], default="skip",
                   help="药水满格时：skip 留空不领 / swap 丢最差一瓶换新")
    p.add_argument("--thought", default="")
    p.add_argument("--meme", default="")
    p.add_argument("--full", action="store_true")
    p = sub.add_parser("quip", help="一次推多句台词（省 token）：quip \"句1\" \"句2\" \"句3\"")
    p.add_argument("lines", nargs="+")
    p = sub.add_parser("memory")
    p.add_argument("--write", action="store_true", help="同时写 logs/run_memory.md（新 session 恢复用）")
    p = sub.add_parser("journal")
    p.add_argument("--floor", type=int, help="只看某一层")
    p.add_argument("--last", type=int, default=20, help="最多打印几条")
    sub.add_parser("report")
    args = ap.parse_args()
    if args.cmd == "state":
        cmd_state(args.raw, args.full)
    elif args.cmd == "acts":
        cmd_acts()
    elif args.cmd == "map":
        cmd_map()
    elif args.cmd == "route":
        cmd_route(args.commit, args.note)
    elif args.cmd == "plan":
        cmd_plan(args.clear, args.note)
    elif args.cmd == "play":
        cmd_play(args.card_id, args.target, args.thought, args.meme, args.card, args.full)
    elif args.cmd == "do":
        options = None
        if getattr(args, "options", None):
            options = [int(x) for x in str(args.options).replace(" ", "").split(",") if x != ""]
        cmd_do(args.action, args.card, args.target, args.option, args.thought, args.meme, options,
               getattr(args, "full", False), getattr(args, "force", False))
    elif args.cmd == "rewards":
        cmd_rewards(args.take, args.potion, args.thought, args.meme, getattr(args, "full", False),
                    pick=getattr(args, "pick", "auto"))
    elif args.cmd == "batch":
        spec = args.spec
        if args.file:
            with open(args.file, encoding="utf-8-sig") as f:
                spec = f.read().strip()
        if not spec:
            print("BATCH_EMPTY 没有 spec：传 JSON，或用 --file 指一个 UTF-8 文件")
            sys.exit(2)
        cmd_batch(spec)
        if args.wait:
            wait_stable(need_player=(args.wait == "player"), timeout=args.timeout)
    elif args.cmd == "wait":
        cmd_wait(args.screen, args.player_turn, args.timeout)
    elif args.cmd == "say":
        cmd_say(args.thought, args.meme)
    elif args.cmd == "quip":
        cmd_quip(args.lines)
    elif args.cmd == "memory":
        cmd_memory(args.write)
    elif args.cmd == "journal":
        cmd_journal(args.floor, args.last)
    elif args.cmd == "report":
        cmd_report()


if __name__ == "__main__":
    main()
