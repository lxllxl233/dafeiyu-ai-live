# -*- coding: utf-8 -*-
"""单回合战斗模拟器（本地算账，模型只做取舍）。

为什么要有这一层
----------------
v19 的 `advisor._simulate` 只认「伤害/格挡/易伤/虚弱」，于是：
  · 放血/祭品回的能量看不见 → 明明能多打一张，计划却按 3 费封顶；
  · 无情猛攻/踩踏的变费看不见 → 下一张攻击实际 0 费，却被当成 2 费拒掉；
  · 抽牌、消耗、加牌、药水全不在模型里 → 计划只会在「当前手牌」里打转；
  · 击杀连锁（杀一只→另一只吃尸眩晕）只按计划尾部补算，不会反过来影响选牌。

本模块把「一张牌打出去，能量/血/格挡/易伤/抽牌/消耗/变费怎么变」逐步模拟，
供 ctx.py 生成候选计划、act.py 校验模型给的顺序。数值全部来自实时 state，
卡牌文字只用来识别「这张牌会不会改费用/加牌/消耗」，不替代实时数值。

对外接口
--------
    sim = Sim.from_state(state)
    sim.cost_of(card)
    sim.play(card, target_index) -> step dict
    sim.use_potion(slot, target_index) -> step dict
    sim.end_turn() -> 结算 dict
    sql = search(state, max_cards=5) -> [plan dict, ...]
    ok, err, sim, steps = validate(state, seq)
"""
from __future__ import annotations

import re

import sts2

TYPES = {"Attack": "A", "Skill": "S", "Power": "P", "Status": "T", "Curse": "C"}
TYPE_CN = {"A": "攻", "S": "技", "P": "能", "T": "状", "C": "诅", "?": "?"}

# 不打出来也能算进「回合末掉血」的状态牌（skill §3）
STATUS_TAX = {"SLIMED": 3, "WOUND": 3, "INFECTION": 3, "CALL": 6, "BURN": 2, "DAZED": 0, "VOID": 0}


def _num(name: str, text: str, default=0) -> int:
    """从卡牌文字里取「X点<name>」的 X（只用于文字型效果兜底）。"""
    m = re.search(r"(\d+)\s*点\s*" + re.escape(name), text or "")
    return int(m.group(1)) if m else default


def _clean_card_text(card: dict) -> str:
    return sts2._strip_tags(str(card.get("resolved_rules_text") or card.get("rules_text") or ""))


def _db(card: dict) -> dict:
    return sts2.CARD_DB.get(str(card.get("card_id") or "")) or {}


def _dyn(card: dict, *names: str):
    v = sts2._card_live(card, set(names))
    return int(v) if v is not None else None


def card_type(card: dict) -> str:
    cid = str(card.get("card_id") or "")
    t = str(card.get("card_type") or card.get("type") or _db(card).get("type") or "")
    if t in TYPES:
        return TYPES[t]
    if cid.startswith(("STRIKE", "DEFEND")):
        return "A" if cid.startswith("STRIKE") else "S"
    return "?"


def _cost_mod(text: str, cid: str = "") -> dict:
    """识别改费牌。只影响模拟，不改游戏（文字优先，id 兜底）。"""
    cid = cid.upper()
    if cid == "UNRELENTING":
        return {"na": 1}
    if cid == "STOMP":
        return {"per": 1}
    if cid == "CORRUPTION":
        return {"sk0": 1}
    t = (text or "").replace(" ", "")
    if ("下一张攻击牌" in t or "下一张的攻击牌" in t) and ("耗能变为0" in t or "耗能为0" in t or "耗能降为0" in t):
        return {"na": 1}
    if "下一张技能牌" in t and ("耗能变为0" in t or "耗能为0" in t or "耗能降为0" in t):
        return {"ns": 1}
    if "技能牌消耗变为0" in t or ("技能牌" in t and "耗能变为0" in t and "本场" in t):
        return {"sk0": 1}
    if "每打出过一张攻击牌" in t and "耗能减少" in t:
        return {"per": 1}
    if "这张牌的耗能降为0" in t:
        return {"self0": 1}
    if "耗能加1" in t:
        return {"self+": 1}
    if "耗能减少1" in t and "这张牌" in t:
        return {"self-": 1}
    return {}


def parse_card(card: dict, idx: int | None = None) -> dict:
    """把手牌压成模拟用的牌模型（字段用短名，ctx 直接复用）。"""
    cid = str(card.get("card_id") or card.get("id") or "?")
    db = _db(card)
    text = _clean_card_text(card)
    out = {
        "i": idx, "id": cid, "n": str(card.get("name") or db.get("name") or cid),
        "c": max(0, sts2._int(card.get("energy_cost", card.get("cost")), sts2._int(db.get("cost"), 0))),
        "t": card_type(card),
        "up": 1 if (card.get("upgraded") or card.get("is_upgraded")) else 0,
        "play": card.get("playable", True) is not False,
        "x": bool(card.get("costs_x") or db.get("is_x_cost")),
    }
    if not out["play"]:
        out["why"] = str(card.get("unplayable_reason") or "")[:24]

    prof = sts2._attack_profile(card)
    if prof:
        per, fixed, cond = prof
        out["dmg"], out["hits"], out["cond"] = int(per), max(1, fixed), max(0, cond)
    else:
        dmg = _dyn(card, "Damage", "CalculatedDamage")
        if dmg is None:
            dmg = sts2._int(db.get("damage"), 0)
        if dmg:
            hits = max(1, sts2._int(db.get("hit_count"), 1))
            out["dmg"], out["hits"] = int(dmg), hits
            fixed, cond = sts2._hits_from_text(card)
            out["hits"], out["cond"] = max(out["hits"], fixed), cond
    blk = _dyn(card, "Block")
    if blk is None:
        blk = sts2._int(db.get("block"), 0)
    if blk:
        out["blk"] = int(blk)
    draw = _dyn(card, "Cards")
    if draw is None:
        draw = sts2._int(db.get("cards_draw"), 0)
    if draw:
        out["draw"] = int(draw)
    en = _dyn(card, "Energy")
    if en is None:
        en = sts2._int(db.get("energy_gain"), 0)
    if en:
        out["en"] = int(en)
    hp = _dyn(card, "HpLoss")
    if hp is None:
        hp = sts2._int(db.get("hp_loss"), 0)
    if hp:
        out["hp"] = int(hp)
    for key, short in (("VulnerablePower", "vuln"), ("WeakPower", "weak"),
                       ("FrailPower", "frail"), ("StrengthPower", "str"),
                       ("DexterityPower", "dex"), ("PlatingPower", "plating")):
        v = _dyn(card, key)
        if v is None:
            for ap in db.get("powers_applied") or []:
                if isinstance(ap, dict) and str(ap.get("power") or "").lower() == key.replace("Power", "").lower():
                    v = sts2._int(ap.get("amount"), 0)
                    break
        if v:
            out[short] = int(v)

    if "所有敌人" in text or str(card.get("target_type") or db.get("target") or "") in ("AllEnemies",):
        out["aoe"] = True
    cm = _cost_mod(text, cid)
    if cm:
        out["cm"] = cm
    if "消耗" in text and ("消耗。" in text or text.endswith("消耗") or "消耗。" in text):
        out["exh"] = 1
    # 加牌：只关心「加入手牌」的即时张数
    if "加入你的手牌" in text:
        n = _num("张", text, 1)
        if "将" in text:
            m = re.search(r"将(\d+)张", text)
            n = int(m.group(1)) if m else n
        out["add"] = max(1, n)
    if out["t"] == "P":
        out["pw"] = cid
    # ---- 跨回合收益（下一轮出牌能不能吃到）：留着不散的才写 ----
    # 敌人身上的易伤/虚弱/脆弱、自己身上的力量/敏捷/覆甲、能力牌、保留、下回合抽牌。
    nxt = {}
    for k in ("vuln", "weak", "frail", "str", "dex", "plating"):
        if out.get(k):
            nxt[k] = out[k]
    if out["t"] == "P":
        nxt["power"] = 1
    if "保留" in text:
        nxt["retain"] = 1
    m = re.search(r"下回合(?:开始时)?抽(?:取)?(\d+)张", text)
    if m:
        nxt["draw"] = int(m.group(1))
    if "覆甲" in text and "plating" not in nxt:
        nxt["plating"] = _num("点", text, 1)
    if "壁垒" in text:
        nxt["block_keep"] = 1
    if nxt:
        out["nxt"] = nxt
    # 少数「按手牌/消耗堆算」的牌：实时数值不够，补语义
    if cid == "BODY_SLAM" and "dmg" not in out:
        out["dmg"], out["hits"] = 0, 1
    if cid == "SECOND_WIND":
        out["exh_hand"] = "nonatk"
    if cid in ("FIEND_FIRE", "STOKE"):
        out["exh_hand"] = "all"
    if cid == "PACT_END":
        out["pact"] = 1
    if cid == "SPITE":
        out["spite"] = 1
    return out


def parse_potion(p: dict) -> dict | None:
    if not isinstance(p, dict) or not p.get("occupied"):
        return None
    text = sts2._strip_tags(str(p.get("description") or ""))
    out = {"p": sts2._int(p.get("index"), 0), "id": str(p.get("potion_id") or ""),
           "n": str(p.get("name") or p.get("potion_id") or ""), "d": text[:80],
           "t": "enemy" if p.get("requires_target") else "self",
           "use": bool(p.get("can_use"))}
    for key, pat in (("dmg", r"造成(\d+)点伤害"), ("blk", r"获得(\d+)点格挡"),
                     ("en", r"获得(\d+)点能量"), ("draw", r"抽(\d+)张"),
                     ("heal", r"回复(\d+)点生命"), ("vuln", r"给予(\d+)层易伤"),
                     ("weak", r"给予(\d+)层虚弱"), ("str", r"获得(\d+)点力量"),
                     ("dex", r"获得(\d+)点敏捷")):
        m = re.search(pat, text)
        if m:
            out[key] = int(m.group(1))
    if "所有敌人" in text:
        out["aoe"] = True
    # 药水也标一下「这一瓶能不能喂下一轮」：力量/敏捷/易伤/虚弱都是跨回合的
    nxt = {k: out[k] for k in ("vuln", "weak", "str", "dex") if out.get(k)}
    if nxt:
        out["nxt"] = nxt
    return out


def _power_ids(powers) -> dict:
    """power_id/中文名 → 层数。"""
    out = {}
    for p in powers or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("power_id") or p.get("id") or "").upper()
        if pid:
            out[pid] = sts2._int(p.get("amount"), 1)
    return out


def enemy_state(e: dict, idx: int | None = None) -> dict:
    pid = _power_ids(e.get("powers"))
    pm = sts2._power_map(e.get("powers"))
    atk = sts2._intent_damage(e)
    types = [str(x.get("intent_type") or x.get("type") or "?")
             for x in (e.get("intents") or []) if isinstance(x, dict)]
    ravenous = "RAVENOUS_POWER" in pid or any("当有敌人死亡" in str(x.get("description") or "") for x in (e.get("powers") or []) if isinstance(x, dict))
    return {
        "i": sts2._int(e.get("index", idx if idx is not None else 0), 0),
        "id": str(e.get("enemy_id") or "?"), "n": str(e.get("name") or "?"),
        "hp": sts2._int(e.get("current_hp", e.get("hp")), 0),
        "max": sts2._int(e.get("max_hp"), 0),
        "blk": sts2._int(e.get("block"), 0),
        "atk": atk, "types": types,
        "vuln": sts2._int(pm.get("vuln"), 0), "weak": sts2._int(pm.get("weak"), 0),
        "timid": sts2._int(pm.get("timid"), 0), "thorns": sts2._int(pm.get("thorns"), 0),
        "slippery": sts2._int(pid.get("SLIPPERY_POWER"), 0),
        "artifact": sts2._int(pm.get("artifact"), 0),
        "intangible": sts2._int(pm.get("intangible"), 0),
        "ravenous": ravenous,
        "minion": "MINION_POWER" in pid,
        "dead": not (e.get("is_alive", True) and sts2._int(e.get("current_hp", e.get("hp")), 1) > 0),
        "stunned": False, "str": 0,
    }


class Sim:
    """一份可复制的回合内状态。"""

    def __init__(self):
        self.hp = 0
        self.maxhp = 0
        self.block = 0
        self.energy = 0
        self.e_max = 0
        self.hand: list[dict] = []
        self.enemies: list[dict] = []
        self.potions: list[dict] = []
        self.attacks = 0
        self.skills = 0
        self.played = 0
        self.exhausts = 0
        self.draws = 0
        self.selfhp = 0
        self.lost_hp_turn = 0
        self.maxhp_gain = 0
        self.str_bonus = 0
        self.dex_bonus = 0
        self.next_attack_zero = False
        self.next_skill_zero = False
        self.skills_zero = False
        self.next_attack_twice = False
        self.barricade = False
        self.metallicize = 0
        self.plating = 0
        self.fnp = 0
        self.dark_embrace = 0
        self.rupture = 0
        self.player_vuln = 0
        self.player_weak = 0
        self.status_tax = 0
        self.steps: list[dict] = []
        self.exhaust_pile = 0

    @classmethod
    def from_state(cls, state: dict) -> "Sim":
        s = cls()
        c = state.get("combat") or {}
        p = c.get("player") or {}
        run = state.get("run") or {}
        s.hp = sts2._int(p.get("current_hp", run.get("current_hp")), 0)
        s.maxhp = sts2._int(p.get("max_hp", run.get("max_hp")), 1)
        s.block = sts2._int(p.get("block"), 0)
        s.energy = sts2._int(p.get("energy"), 0)
        s.e_max = sts2._int(p.get("max_energy", run.get("max_energy")), s.energy)
        s.hand = [parse_card(x, i) for i, x in enumerate(c.get("hand") or []) if isinstance(x, dict)]
        s.enemies = [enemy_state(x, i) for i, x in enumerate(c.get("enemies") or []) if isinstance(x, dict)]
        s.potions = [p for p in (parse_potion(x) for x in (run.get("potions") or [])) if p]
        pid = _power_ids(p.get("powers"))
        pm = sts2._power_map(p.get("powers"))
        s.player_vuln = sts2._int(pm.get("vuln"), 0)
        s.metallicize = sts2._int(pm.get("metallicize"), 0)
        s.barricade = bool(pm.get("barricade"))
        s.plating = sts2._int(pm.get("plating"), 0)
        s.fnp = sts2._int(pid.get("FEEL_NO_PAIN_POWER"), 0)
        s.dark_embrace = sts2._int(pid.get("DARK_EMBRACE_POWER"), 0)
        s.rupture = sts2._int(pid.get("RUPTURE_POWER"), 0)
        s.skills_zero = "CORRUPTION_POWER" in pid
        av = (state.get("agent_view") or {}).get("combat") or {}
        s.exhaust_pile = len([x for x in (av.get("exhaust_cards") or []) if isinstance(x, dict)])
        return s

    def clone(self) -> "Sim":
        n = Sim.__new__(Sim)
        n.__dict__ = {k: (list(v) if isinstance(v, list) else v) for k, v in self.__dict__.items()}
        n.enemies = [dict(e) for e in self.enemies]
        n.potions = [dict(p) for p in self.potions]
        n.hand = [dict(c) for c in self.hand]
        return n

    # ---------------------------------------------------------------- 查询
    def alive(self) -> list[dict]:
        return [e for e in self.enemies if not e["dead"] and e["hp"] > 0]

    def by_index(self, i: int) -> dict | None:
        for e in self.enemies:
            if e["i"] == i and not e["dead"] and e["hp"] > 0:
                return e
        return None

    def threat(self, e: dict) -> int:
        return sts2._int(e.get("atk"), 0)

    def cost_of(self, card: dict) -> int:
        if not card.get("play", True):
            return 999
        if card.get("x"):
            return self.energy
        c = max(0, sts2._int(card.get("c"), 0))
        if card.get("t") == "A" and self.next_attack_zero:
            c = 0
        if card.get("t") == "S" and (self.next_skill_zero or self.skills_zero):
            c = 0
        cm = card.get("cm") or {}
        if cm.get("per"):
            c = max(0, c - self.attacks * cm["per"])
        if cm.get("self+"):
            c = max(0, c + cm["self+"])
        if cm.get("self-"):
            c = max(0, c - cm["self-"])
        return c

    def incoming(self) -> int:
        total = 0
        for e in self.alive():
            if e["stunned"]:
                continue
            atk = e["atk"]
            if e["weak"]:
                atk = int(atk * 0.75)
            total += atk
        if self.player_vuln:
            total = int(total * 1.5)
        return total

    # ---------------------------------------------------------------- 结算
    def lose_hp(self, n: int) -> int:
        """失去生命（无视格挡）：放血/祭品/血墙/状态牌。"""
        n = max(0, int(n))
        self.hp -= n
        self.lost_hp_turn += n
        return n

    def hurt_player(self, n: int) -> int:
        n = max(0, int(n))
        if not n:
            return 0
        absorbed = min(self.block, n)
        self.block -= absorbed
        d = n - absorbed
        self.hp -= d
        self.lost_hp_turn += d
        return d

    def hurt_enemy(self, e: dict, per: int, hits: int = 1) -> int:
        if e["dead"] or e["hp"] <= 0:
            return 0
        per = max(0, int(per + self.str_bonus))
        if e["timid"]:
            e["blk"] += e["timid"]
            e["timid"] = 0
        dealt = 0
        for _ in range(max(1, hits)):
            if e["blk"] > 0:
                absorb = min(e["blk"], per)
                e["blk"] -= absorb
                d = per - absorb
            else:
                d = per
            if d <= 0:
                continue
            if e["intangible"]:
                d = 1
            elif e["slippery"] > 0:
                d = 1
                e["slippery"] -= 1
            e["hp"] -= d
            dealt += d
            if e["thorns"]:
                self.hurt_player(e["thorns"])
            if e["hp"] <= 0:
                e["hp"] = 0
                e["dead"] = True
                self._on_death(e)
                break
        return dealt

    def _on_death(self, dead: dict):
        # 吃尸眩晕：一只死，带饥饿的其它怪本回合被击晕并 +1 力量
        for o in self.enemies:
            if o is dead or o["dead"] or o["hp"] <= 0:
                continue
            if o["ravenous"]:
                o["stunned"] = True
                o["str"] += 1
        # 召唤者死，爪牙撤
        if any(not o["dead"] and not o["minion"] for o in self.enemies):
            return
        for o in self.enemies:
            if o["minion"]:
                o["dead"] = True
                o["hp"] = 0

    def apply_debuff(self, e: dict, key: str, n: int):
        if n <= 0 or e is None or e["dead"]:
            return
        if e["artifact"] > 0:
            e["artifact"] -= 1
            return
        e[key] += n

    def end_turn(self) -> dict:
        """只结算、不改状态（搜索会在同一节点反复调用）。"""
        inc = self.incoming()
        block = self.block + self.metallicize + self.plating
        taken = max(0, inc - block)
        # 状态牌留在手里：回合末掉血（呼唤无视格挡）
        tax = 0
        for c in self.hand:
            if c.get("_played"):
                continue
            if not c.get("play", True) and c["id"] in STATUS_TAX:
                tax += STATUS_TAX[c["id"]]
        if tax:
            taken += tax
        hp_after = self.hp - taken
        return {"incoming": inc, "taken": taken, "hp_after": hp_after,
                "block": block, "tax": tax}

    # ---------------------------------------------------------------- 出牌
    def play(self, card: dict, target: int | None = None) -> dict | None:
        if not card.get("play", True) or card.get("_played"):
            return None
        cost = self.cost_of(card)
        if cost > self.energy:
            return None
        cm = card.get("cm") or {}
        used_na = self.next_attack_zero and card.get("t") == "A" and not cm.get("na")
        used_ns = self.next_skill_zero and card.get("t") == "S" and not cm.get("ns")
        self.energy -= cost
        self.played += 1
        card["_played"] = True
        if card.get("t") == "A":
            self.attacks += 1
        if card.get("t") == "S":
            self.skills += 1
        step = {"k": "c", "id": card["id"], "t": target, "e": cost, "d": 0, "blk": 0}

        # 自伤 / 回能 / 抽牌 / 加牌
        if card.get("hp"):
            d = self.lose_hp(card["hp"])
            self.selfhp += d
            step["hp"] = -d
            if self.rupture and d:
                self.str_bonus += self.rupture
        if card.get("en"):
            self.energy = min(self.e_max + 10, self.energy + card["en"])
            step["en"] = card["en"]
        if card.get("draw"):
            self.draws += card["draw"]
            step["draw"] = card["draw"]
        if card.get("str"):
            self.str_bonus += card["str"]
        if card.get("dex"):
            self.dex_bonus += card["dex"]
        if card.get("blk"):
            if card.get("exh_hand") == "nonatk":
                n = len([x for x in self.hand if x is not card and not x.get("_played")
                         and x.get("t") != "A"])
                gain = max(0, card["blk"] * n)
            else:
                gain = max(0, card["blk"] + self.dex_bonus)
            self.block += gain
            step["blk"] = gain
        if card.get("add"):
            step["add"] = card["add"]
        if card.get("pact") and self.exhaust_pile + self.exhausts >= 3 and not card.get("dmg"):
            card["dmg"] = 18

        # 伤害
        e = self.by_index(target) if target is not None else None
        if card.get("dmg") is not None:
            per = int(card["dmg"])
            if card["id"] == "BODY_SLAM":
                per = self.block
            hits = int(card.get("hits", 1))
            if card.get("spite") and self.lost_hp_turn:
                hits = max(hits, 2)
            if card.get("x"):
                hits = max(1, cost)
            if card["id"] == "FIEND_FIRE":
                hits = max(1, len([x for x in self.hand if x is not card and not x.get("_played")]))
            if card["id"] == "BULLY" and e:
                per += 2 * e["vuln"]
            if self.next_attack_twice and card.get("t") == "A":
                hits = max(1, hits) * 2
                self.next_attack_twice = False
            targets = self.alive() if card.get("aoe") else ([e] if e else [])
            dealt = 0
            for t in targets:
                if not t or t["dead"]:
                    continue
                h = hits
                if t["vuln"] and card.get("cond"):
                    h = max(h, int(card["cond"]))
                mul = 1.5 if t["vuln"] else 1.0
                dealt += self.hurt_enemy(t, int(per * mul), h)
            step["d"] = dealt
            if card["id"] == "REAPER":
                self.hp = min(self.maxhp, self.hp + dealt)
            if card["id"] == "FEED" and any(x["dead"] for x in targets):
                self.maxhp += 3
                self.maxhp_gain += 3

        # debuff
        if card.get("vuln"):
            if card.get("aoe"):
                for t in self.alive():
                    self.apply_debuff(t, "vuln", card["vuln"])
            elif e:
                self.apply_debuff(e, "vuln", card["vuln"])
                if card["id"] == "MOLTEN_FIST" and e:
                    e["vuln"] *= 2
                if card["id"] == "DOMINATE" and e:
                    self.str_bonus += e["vuln"]
        if card.get("weak"):
            if card.get("aoe"):
                for t in self.alive():
                    self.apply_debuff(t, "weak", card["weak"])
            elif e:
                self.apply_debuff(e, "weak", card["weak"])
        if card.get("frail") and e:
            self.apply_debuff(e, "frail", card["frail"])

        # 变费 / 能力
        if cm.get("na"):
            self.next_attack_zero = True
        if cm.get("ns"):
            self.next_skill_zero = True
        if cm.get("sk0"):
            self.skills_zero = True
        if used_na:
            self.next_attack_zero = False
        if used_ns:
            self.next_skill_zero = False
        if card["id"] == "ONE_TWO_PUNCH":
            self.next_attack_twice = True
        if card.get("pw"):
            self._power(card)
        if card.get("exh") or cm.get("self0") or (card.get("t") == "S" and self.skills_zero
                                                  and card["id"] != "CORRUPTION"):
            self.exhaust(card)
        if card.get("exh_hand"):
            for x in self.hand:
                if x is card or x.get("_played"):
                    continue
                if card["exh_hand"] == "nonatk" and x.get("t") == "A":
                    continue
                x["_played"] = True
                self.exhaust(x)
        self.steps.append(step)
        return step

    def exhaust(self, card: dict | None = None):
        self.exhausts += 1
        if self.fnp:
            self.block += self.fnp
        if self.dark_embrace:
            self.draws += 1

    def _power(self, card: dict):
        cid = card["id"]
        if cid == "FEEL_NO_PAIN":
            self.fnp += max(1, card.get("blk", 3) or 3)
        elif cid == "DARK_EMBRACE":
            pass
        elif cid == "CORRUPTION":
            self.skills_zero = True
        elif cid == "BARRICADE":
            self.barricade = True
        elif cid == "METALLICIZE":
            self.metallicize += card.get("blk", 3) or 3
        elif cid == "STONE_ARMOR":
            self.plating += card.get("plating", 4) or 4
        elif cid == "RUPTURE":
            self.rupture += max(1, card.get("str", 1) or 1)

    # ---------------------------------------------------------------- 药水
    def potion(self, p: dict, target: int | None = None) -> dict | None:
        if not p or not p.get("use") or p.get("_used"):
            return None
        p["_used"] = True
        step = {"k": "p", "p": p["p"], "id": p["id"], "t": target, "d": 0}
        if p.get("heal"):
            self.hp = min(self.maxhp, self.hp + p["heal"])
            step["heal"] = p["heal"]
        if p.get("en"):
            self.energy += p["en"]
        if p.get("draw"):
            self.draws += p["draw"]
        if p.get("str"):
            self.str_bonus += p["str"]
        if p.get("dex"):
            self.dex_bonus += p["dex"]
        if p.get("blk"):
            self.block += p["blk"] + self.dex_bonus
            step["blk"] = p["blk"]
        if p.get("dmg"):
            targets = self.alive() if p.get("aoe") else ([self.by_index(target)] if target is not None else [])
            dealt = 0
            for t in targets:
                if t:
                    dealt += self.hurt_enemy(t, p["dmg"], 1)
            step["d"] = dealt
        e = self.by_index(target) if target is not None else None
        if p.get("vuln"):
            if p.get("aoe"):
                for t in self.alive():
                    self.apply_debuff(t, "vuln", p["vuln"])
            elif e:
                self.apply_debuff(e, "vuln", p["vuln"])
        if p.get("weak"):
            if p.get("aoe"):
                for t in self.alive():
                    self.apply_debuff(t, "weak", p["weak"])
            elif e:
                self.apply_debuff(e, "weak", p["weak"])
        self.steps.append(step)
        return step


# -------------------------------------------------------------------- 选线
def _policies(sim: Sim) -> list[tuple[str, int | None]]:
    out: list[tuple[str, int | None]] = [("threat", None), ("finish", None)]
    for e in sim.alive():
        out.append((f"focus:{e['i']}", e["i"]))
    return out


def _pick_target(sim: Sim, policy: str, focus: int | None, card: dict) -> int | None:
    alive = sim.alive()
    if not alive:
        return None
    if policy.startswith("focus") and focus is not None and sim.by_index(focus):
        return focus
    if policy == "finish":
        return min(alive, key=lambda e: (e["hp"], -e["atk"]))["i"]
    return max(alive, key=lambda e: (e["atk"], -e["hp"]))["i"]


def _score(sim: Sim, res: dict, energy0: int, hp0: int, incoming0: int, played: list[dict]) -> float:
    if res["hp_after"] <= 0:
        return -1_000_000 + sum(s.get("d", 0) for s in sim.steps)
    score = 0.0
    score += 3.0 * sum(s.get("d", 0) for s in sim.steps)
    score -= 7.0 * res["taken"]
    score -= 3.0 * sim.energy
    score -= 2.5 * sim.selfhp
    score += 2.0 * sim.draws
    score += 4.0 * sim.exhausts
    score += 6.0 * sum(1 for c in sim.hand if c.get("_played") and c.get("pw"))
    score -= 120.0 * sum(1 for p in sim.potions if p.get("_used"))
    for e in sim.enemies:
        if e["dead"]:
            score += 150 + 9 * e["atk"]
        elif e["stunned"]:
            score += 6 * e["atk"]
    if res["taken"] == 0 and incoming0 > 0:
        score += 45
    if res["hp_after"] == hp0 and res["taken"] == 0:
        score += 15
    # 终局多杀/清场
    if not sim.alive():
        score += 300
    return score


def _seq_json(sim: Sim) -> list:
    out = []
    for s in sim.steps:
        if s["k"] == "p":
            out.append([f"@{s['p']}", s["t"]] if s.get("t") is not None else [f"@{s['p']}"])
        else:
            out.append([s["id"], s["t"]] if s.get("t") is not None else [s["id"]])
    return out


def _plan(sim: Sim, res: dict, score: float, kind: str) -> dict:
    kills = [e["i"] for e in sim.enemies if e["dead"]]
    stun = [e["i"] for e in sim.enemies if e["stunned"] and not e["dead"]]
    return {"k": kind, "seq": _seq_json(sim), "dmg": sum(s.get("d", 0) for s in sim.steps),
            "kill": kills, "stun": stun, "taken": res["taken"],
            "hp": res["hp_after"], "e": sim.energy, "score": round(score, 1)}


def search(state: dict, max_cards: int = 5, max_plans: int = 3) -> list[dict]:
    """穷举「费用内所有牌序 × 目标策略」，返回 1–3 条候选（best/guard/kill）。"""
    base = Sim.from_state(state)
    if not base.alive():
        return []
    energy0, hp0, incoming0 = base.energy, base.hp, base.incoming()
    playable = [c for c in base.hand if c.get("play", True)]
    if len(playable) > 7:
        playable = playable[:7]
    seen_keys: list[tuple] = []
    for c in playable:
        key = (c["id"], c.get("up", 0), c.get("c", 0))
        if key not in seen_keys:
            seen_keys.append(key)

    best: dict[str, tuple[float, Sim, dict]] = {}

    def record(sim: Sim, res: dict, score: float, kind: str):
        cur = best.get(kind)
        if cur is None or score > cur[0]:
            best[kind] = (score, sim.clone(), res)

    def dfs(sim: Sim, used: set[int], depth: int, policy: str, focus: int | None,
            potion_line: bool = False):
        res = sim.end_turn()
        score = _score(sim, res, energy0, hp0, incoming0, [])
        dmg_total = sum(s.get("d", 0) for s in sim.steps)
        if potion_line:
            record(sim, res, score, "potion")
        else:
            record(sim, res, score, "best")
            # 纯伤害上限：不管防御，只看这回合最多能打多少（ctx.calc.dmg / ttk 用）
            record(sim, res, dmg_total, "maxdmg")
            if res["taken"] == 0:
                record(sim, res, score, "guard")
            if any(e["dead"] for e in sim.enemies):
                record(sim, res, score, "kill")
        if depth <= 0:
            return
        for c in playable:
            if c["i"] in used:
                continue
            # 同 id 同费只按第一个下标展开，砍掉等价排列
            twin = next((x for x in playable if x is not c and x["i"] not in used
                         and (x["id"], x.get("up", 0), x.get("c", 0)) == (c["id"], c.get("up", 0), c.get("c", 0))), None)
            if twin is not None and twin["i"] < c["i"]:
                continue
            cost = sim.cost_of(c)
            if cost > sim.energy:
                continue
            nxt = sim.clone()
            target = _pick_target(nxt, policy, focus, c)
            if c.get("dmg") is not None and target is None and not c.get("aoe"):
                continue
            card = next((x for x in nxt.hand if x["i"] == c["i"]), None)
            if not card:
                continue
            nxt.play(card, target if target is not None else (nxt.alive()[0]["i"] if nxt.alive() else None))
            if nxt.played == sim.played:
                continue
            dfs(nxt, used | {c["i"]}, depth - 1, policy, focus, potion_line)

    for policy, focus in _policies(base):
        dfs(base.clone(), set(), max_cards, policy, focus)

    # 药水线：只试「药水先手」，让回能/斩杀药水能进入候选（用一次扣分，不轻易交）
    useful = [p for p in base.potions if p.get("use") and any(
        p.get(k) for k in ("dmg", "blk", "en", "heal", "vuln", "weak", "str", "dex"))]
    for p in useful[:2]:
        for policy, focus in _policies(base):
            nxt = base.clone()
            pp = next((x for x in nxt.potions if x["p"] == p["p"]), None)
            if not pp:
                continue
            tgt = _pick_target(nxt, policy, focus, {}) if pp["t"] == "enemy" else None
            nxt.potion(pp, tgt)
            dfs(nxt, set(), max_cards, policy, focus, potion_line=True)

    out = []
    for kind in ("best", "maxdmg", "kill", "guard", "potion"):
        if kind in best:
            score, sim, res = best[kind]
            plan = _plan(sim, res, score, kind)
            if all(plan["seq"] != o["seq"] for o in out):
                out.append(plan)
    return out[:max_plans]


# -------------------------------------------------------------------- 速算
def max_block(state: dict) -> int:
    """当前手牌在费用内最多能叠多少甲（含覆甲/金属化），用于 full_block 判断。"""
    s = Sim.from_state(state)
    cards = [c for c in s.hand if c.get("play", True) and (c.get("blk") or c.get("en"))]
    # 回能/0 费先打，再按每费格挡排序
    cards.sort(key=lambda c: (0 if c.get("en") else 1, -(c.get("blk", 0) / max(1, s.cost_of(c)))))
    for c in cards:
        if s.cost_of(c) <= s.energy:
            s.play(c, None)
    return s.block + s.metallicize + s.plating


def lethal_targets(state: dict) -> list[dict]:
    """每个敌人：把易伤/攻击都灌给它，最多能打多少；够斩杀就列出来。"""
    base = Sim.from_state(state)
    out = []
    for target in base.alive():
        s = base.clone()
        e = s.by_index(target["i"])
        vuln_cards = sorted([c for c in s.hand if c.get("play", True) and c.get("vuln")],
                            key=lambda c: s.cost_of(c))
        for c in vuln_cards:
            if s.cost_of(c) <= s.energy:
                s.play(c, target["i"])
        atk = sorted([c for c in s.hand if c.get("play", True) and c.get("dmg") is not None
                      and not c.get("aoe")],
                     key=lambda c: -(c.get("dmg", 0) * max(1, c.get("hits", 1))
                                     * (1.5 if e and e["vuln"] else 1.0) / max(1, s.cost_of(c))))
        dealt = sum(x.get("d", 0) for x in s.steps)
        for c in atk:
            if s.cost_of(c) <= s.energy:
                s.play(c, target["i"])
        dealt = sum(x.get("d", 0) for x in s.steps)
        if dealt >= target["hp"] + target["blk"]:
            out.append({"i": target["i"], "d": dealt, "hp": target["hp"]})
    return out


# -------------------------------------------------------------------- 校验
def resolve_card(sim: Sim, cid: str, used: set[int]) -> dict | None:
    key = str(cid or "").strip().upper()
    want_up = key.endswith("+")
    if want_up:
        key = key[:-1]
    pool = [c for c in sim.hand if c["i"] not in used
            and (not want_up or c.get("up"))]
    exact = [c for c in pool if c["id"].upper() == key]
    if not exact:
        pref = [c for c in pool if c["id"].upper().startswith(key)]
        if len({c["id"] for c in pref}) == 1:
            exact = pref
    if not exact:
        return None
    # 同名多张：先挑当下最便宜，再挑升级/伤害高的（变费后别打错那张）
    exact.sort(key=lambda c: (sim.cost_of(c), -(c.get("up") or 0), -(c.get("dmg") or 0)))
    return exact[0]


def validate(state: dict, seq: list) -> tuple[bool, str, Sim, list]:
    """逐步校验模型给的顺序：牌/药水是否存在、每一步当下费用够不够。

    目标死了自动改打威胁最大的活怪（和游戏里的 batch 一致），记在 sim.retargets。
    返回 (ok, err, sim, steps)。ok=False 时调用方一张都不许打。
    """
    sim = Sim.from_state(state)
    sim.retargets = []
    used: set[int] = set()
    steps = []
    for n, raw in enumerate(seq or []):
        if isinstance(raw, str):
            raw = {"end": 1} if raw.lower() == "end" else {"c": raw}
        if not isinstance(raw, dict):
            return False, f"第{n + 1}步不是对象", sim, steps
        if raw.get("end") or raw.get("a") == "end":
            steps.append({"k": "end"})
            return True, "", sim, steps
        if "p" in raw or raw.get("a") == "potion" or "slot" in raw:
            slot = sts2._int(raw.get("p", raw.get("slot")), 0)
            p = next((x for x in (parse_potion(y) for y in (state.get("run") or {}).get("potions") or [])
                      if x and x["p"] == slot), None)
            if not p:
                return False, f"第{n + 1}步：药水槽 {slot} 是空的", sim, steps
            if not p["use"]:
                return False, f"第{n + 1}步：{p['n']} 现在不能用（{p['d']}）", sim, steps
            tgt = raw.get("t", raw.get("target"))
            tgt = sts2._int(tgt, -1) if tgt is not None else None
            if p["t"] == "enemy" and tgt is None:
                alive = sim.alive()
                if not alive:
                    return False, f"第{n + 1}步：{p['n']} 需要目标但没有活怪", sim, steps
                tgt = max(alive, key=lambda e: e["atk"])["i"]
            if tgt is not None and p["t"] == "enemy" and not sim.by_index(tgt):
                alive = sim.alive()
                if not alive:
                    return False, f"第{n + 1}步：目标 {tgt} 不是活怪", sim, steps
                tgt = max(alive, key=lambda e: e["atk"])["i"]
                sim.retargets.append({"at": n + 1, "to": tgt})
            p2 = sim.potion(p, tgt)
            if not p2:
                return False, f"第{n + 1}步：{p['n']} 用不了", sim, steps
            steps.append(p2)
            continue
        cid = raw.get("c", raw.get("id", raw.get("card_id")))
        card = resolve_card(sim, str(cid or ""), used)
        if not card:
            return False, f"第{n + 1}步：手里没有可打的 {cid}", sim, steps
        cost = sim.cost_of(card)
        if cost > sim.energy:
            return False, (f"第{n + 1}步：{card['id']} 现在要 {cost} 费，只剩 {sim.energy}"
                           "（回能/变费牌要先打）"), sim, steps
        tgt = raw.get("t", raw.get("target"))
        tgt = sts2._int(tgt, -1) if tgt is not None else None
        alive = sim.alive()
        if tgt is not None and not sim.by_index(tgt):
            if not alive:
                return False, f"第{n + 1}步：目标 {tgt} 不是活怪", sim, steps
            tgt = max(alive, key=lambda e: e["atk"])["i"]
            sim.retargets.append({"at": n + 1, "to": tgt})
        if card.get("dmg") is not None and not card.get("aoe") and tgt is None:
            if not alive:
                return False, f"第{n + 1}步：{card['id']} 需要目标但没有活怪", sim, steps
            tgt = max(alive, key=lambda e: e["atk"])["i"]
        step = sim.play(card, tgt)
        if not step:
            return False, f"第{n + 1}步：{card['id']} 打不出去", sim, steps
        used.add(card["i"])
        steps.append(step)
    return True, "", sim, steps
