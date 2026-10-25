#!/usr/bin/env python3
"""Build a local SQLite reference DB from the public wiki pages + bundled English data."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "db" / "raw"
ENG = ROOT / "tools" / "data" / "eng"
PAGES = ROOT / "tools" / "pages"
DB = ROOT / "db" / "sts2.db"


def load(name: str):
    p = ENG / name
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def strip_tags(s: str) -> str:
    s = re.sub(r"\[[^\]]*\]", "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def parse_list_page(slug: str, prefix: str):
    p = PAGES / f"{slug}_zh.html"
    if not p.exists():
        return []
    soup = BeautifulSoup(p.read_text(encoding="utf-8"), "lxml")
    out = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if f"/zh/{prefix}/" not in href:
            continue
        item_slug = href.rstrip("/").split("/")[-1]
        if item_slug in seen:
            continue
        seen.add(item_slug)
        name_el = a.select_one("p.font-medium")
        name = name_el.get_text(" ", strip=True) if name_el else ""
        badges = [
            d.get_text(" ", strip=True)
            for d in a.select("div.flex.flex-wrap.gap-1 > div")
            if d.get_text(strip=True)
        ]
        desc_el = a.select_one("div.text-xs.text-muted-foreground")
        desc = desc_el.get_text(" ", strip=True) if desc_el else ""
        out.append({"slug": item_slug, "name": name, "badges": badges, "desc": desc})
    return out


def main():
    eng_cards = {c.get("id"): c for c in load("cards.json") if c.get("id")}
    eng_relics = {r.get("id"): r for r in load("relics.json") if r.get("id")}
    eng_potions = {p.get("id"): p for p in load("potions.json") if p.get("id")}
    eng_monsters = {m.get("id"): m for m in load("monsters.json") if m.get("id")}
    eng_events = {e.get("id"): e for e in load("events.json") if e.get("id")}
    eng_powers = {p.get("id"): p for p in load("powers.json") if p.get("id")}
    eng_keywords = {k.get("id"): k for k in load("keywords.json") if k.get("id")}

    site_cards = json.loads((RAW / "cards.json").read_text(encoding="utf-8"))
    site_enemies = json.loads((RAW / "enemies.json").read_text(encoding="utf-8"))
    site_events = json.loads((RAW / "events.json").read_text(encoding="utf-8"))
    site_ench = json.loads((RAW / "enchantments.json").read_text(encoding="utf-8"))
    site_relics = parse_list_page("relics", "relics")
    site_potions = parse_list_page("potions", "potions")

    (RAW / "relics.json").write_text(json.dumps(site_relics, ensure_ascii=False, indent=1), encoding="utf-8")
    (RAW / "potions.json").write_text(json.dumps(site_potions, ensure_ascii=False, indent=1), encoding="utf-8")

    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE cards(
          id TEXT PRIMARY KEY, site_id TEXT, name_en TEXT, name_zh TEXT,
          character TEXT, type TEXT, rarity TEXT, cost REAL, target TEXT,
          desc_en TEXT, desc_zh TEXT, desc_up_zh TEXT, raw TEXT
        );
        CREATE TABLE enemies(
          id TEXT PRIMARY KEY, name_en TEXT, name_zh TEXT, type TEXT,
          hp_min INTEGER, hp_max INTEGER, powers_json TEXT, moves_json TEXT,
          notes TEXT, raw TEXT
        );
        CREATE TABLE relics(
          id TEXT PRIMARY KEY, slug TEXT, name_en TEXT, name_zh TEXT,
          rarity TEXT, pool TEXT, desc_en TEXT, desc_zh TEXT, raw TEXT
        );
        CREATE TABLE potions(
          id TEXT PRIMARY KEY, slug TEXT, name_en TEXT, name_zh TEXT,
          rarity TEXT, pool TEXT, desc_en TEXT, desc_zh TEXT, raw TEXT
        );
        CREATE TABLE events(
          id TEXT PRIMARY KEY, name_en TEXT, name_zh TEXT, act TEXT,
          desc_en TEXT, desc_zh TEXT, options_json TEXT, raw TEXT
        );
        CREATE TABLE powers(
          id TEXT PRIMARY KEY, name_en TEXT, name_zh TEXT, desc_en TEXT, desc_zh TEXT, raw TEXT
        );
        CREATE TABLE keywords(id TEXT PRIMARY KEY, name TEXT, description TEXT);
        CREATE TABLE mechanics(key TEXT PRIMARY KEY, title TEXT, note TEXT);
        """
    )

    # cards
    card_count = 0
    for c in site_cards:
        cid = c.get("localizationKey") or c.get("id")
        e = eng_cards.get(cid, {})
        cur.execute(
            "INSERT OR REPLACE INTO cards VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                cid,
                c.get("id"),
                e.get("name") or c.get("name"),
                c.get("name"),
                c.get("character"),
                c.get("cardType") or e.get("type"),
                c.get("rarity") or e.get("rarity"),
                c.get("energy", e.get("cost")),
                e.get("target"),
                strip_tags(e.get("description", "")),
                strip_tags(c.get("description", "")),
                strip_tags(c.get("descriptionUpgraded", "")),
                json.dumps(c, ensure_ascii=False),
            ),
        )
        card_count += 1
    # add English-only cards
    for cid, e in eng_cards.items():
        cur.execute("SELECT 1 FROM cards WHERE id=?", (cid,))
        if not cur.fetchone():
            cur.execute(
                "INSERT OR REPLACE INTO cards VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, None, e.get("name"), "", e.get("color"), e.get("type"), e.get("rarity"),
                 e.get("cost"), e.get("target"), strip_tags(e.get("description", "")), "", "", json.dumps(e, ensure_ascii=False)),
            )
            card_count += 1

    # enemies
    enemy_count = 0
    for m in site_enemies:
        mid = m.get("id")
        e = eng_monsters.get(mid, {})
        powers = m.get("powers") or e.get("powers") or []
        moves = m.get("moves") or e.get("moves") or []
        cur.execute(
            "INSERT OR REPLACE INTO enemies VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                mid,
                e.get("name") or m.get("nameEn"),
                m.get("nameZh"),
                e.get("type"),
                m.get("hpMin", e.get("min_hp")),
                m.get("hpMax", e.get("max_hp")),
                json.dumps(powers, ensure_ascii=False),
                json.dumps(moves, ensure_ascii=False),
                "",
                json.dumps(m, ensure_ascii=False),
            ),
        )
        enemy_count += 1

    # relics
    relic_count = 0
    for r in site_relics:
        rid = next((k for k, v in eng_relics.items() if slugify(v.get("name", "")) == r["slug"]), None)
        e = eng_relics.get(rid, {})
        cur.execute(
            "INSERT OR REPLACE INTO relics VALUES(?,?,?,?,?,?,?,?,?)",
            (rid or r["slug"], r["slug"], e.get("name") or r["name"], r["name"],
             e.get("rarity") or (r["badges"][0] if r["badges"] else ""),
             e.get("pool") or (r["badges"][1] if len(r["badges"]) > 1 else ""),
             strip_tags(e.get("description", "")), r["desc"], json.dumps(r, ensure_ascii=False)),
        )
        relic_count += 1

    # potions
    potion_count = 0
    for p in site_potions:
        pid = next((k for k, v in eng_potions.items() if slugify(v.get("name", "")) == p["slug"]), None)
        e = eng_potions.get(pid, {})
        cur.execute(
            "INSERT OR REPLACE INTO potions VALUES(?,?,?,?,?,?,?,?,?)",
            (pid or p["slug"], p["slug"], e.get("name") or p["name"], p["name"],
             e.get("rarity") or (p["badges"][0] if p["badges"] else ""),
             e.get("pool") or (p["badges"][1] if len(p["badges"]) > 1 else ""),
             strip_tags(e.get("description", "")), p["desc"], json.dumps(p, ensure_ascii=False)),
        )
        potion_count += 1

    # events
    event_count = 0
    for ev in site_events:
        eid = ev.get("id")
        e = eng_events.get(eid, {})
        cur.execute(
            "INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?)",
            (eid, e.get("name") or ev.get("title"), ev.get("title"),
             e.get("act"), e.get("description"), ev.get("descriptionPlain") or ev.get("description"),
             json.dumps(ev.get("options") or [], ensure_ascii=False), json.dumps(ev, ensure_ascii=False)),
        )
        event_count += 1

    # powers
    for pid, p in eng_powers.items():
        cur.execute("INSERT OR REPLACE INTO powers VALUES(?,?,?,?,?,?)",
                    (pid, p.get("name"), "", strip_tags(p.get("description", "")), "", json.dumps(p, ensure_ascii=False)))
    # add site power descriptions (Chinese) from enemies
    for m in site_enemies:
        for p in m.get("powers") or []:
            key = p.get("key")
            if not key:
                continue
            cur.execute("SELECT 1 FROM powers WHERE id=?", (key,))
            if not cur.fetchone():
                cur.execute("INSERT OR REPLACE INTO powers VALUES(?,?,?,?,?,?)",
                            (key, p.get("titleEn"), p.get("titleZh"), p.get("descriptionEn"), p.get("descriptionZh"), json.dumps(p, ensure_ascii=False)))

    for kid, k in eng_keywords.items():
        cur.execute("INSERT OR REPLACE INTO keywords VALUES(?,?,?)", (kid, k.get("name"), strip_tags(k.get("description", ""))))

    enemy_notes = {
        "LIVING_FOG": "召唤者：80 HP，Smoggy（每回合只能打1张技能牌），Bloat 召唤 Gas Bomb。正确打法是集火活雾本体；活雾死后 Gas Bomb 自动消失。不要先清小怪。",
        "GAS_BOMB": "活雾的爪牙：7-8 HP，Explode/DeathBlow 约9。爪牙会在领导者死亡时放弃战斗；通常无视它，直接杀活雾。",
        "CALCIFIED_CULTIST": "仪式2：回合结束获得2力量，越拖越疼。必须优先击杀。",
        "DAMP_CULTIST": "仪式：同样需要优先击杀。",
        "SEAPUNK": "会叠力量/格挡；A10 可能打出 8x4。血少时优先击杀或叠够格挡。",
        "CEREMONIAL_BEAST": "第一章 Boss，252 HP；需要至少 50+ HP 和稳定格挡进 Boss。",
        "BYGONE_EFFIGY": "第一章精英，127 HP；低血量不要碰。",
        "BYRDONIS": "第一章精英，81-84 HP；多段攻击，需要格挡。",
        "PHROG_PARASITE": "第一章精英，61-64 HP；会寄生/召唤，优先本体。",
        "VINE_SHAMBLER": "61 HP，A10 可打 14x2；第一局第15层就死在这里。低血不要进。",
        "MAWLER": "72 HP，10x2 后接 Buff；会滚雪球，尽快击杀。",
        "CORPSE_SLUG": "25-27 HP，饥饿；双只一起出现时总输出很高，低血不要进双怪。",
        "SLUDGE_SPINNER": "37-39 HP，会施加虚弱/脆弱；拖久了伤害不够，优先击杀。",
        "OVICOPTER": "124-130 HP，会召唤；优先本体。",
        "FOGMOG": "74 HP，会召唤；优先本体，小怪会在召唤者死后消失。",
        "THE_OBSCURA": "123 HP，会召唤；优先本体。",
        "FABRICATOR": "150 HP，会召唤；优先本体。",
    }
    for mid, note in enemy_notes.items():
        cur.execute("UPDATE enemies SET notes=? WHERE id=?", (note, mid))

    mechanics = [
        ("minion_despawn", "召唤物随召唤者死亡消失",
         "被召唤的爪牙（Gas Bomb 等）在召唤者死亡后自动消失。面对活雾（Living Fog）这类召唤者时，优先集火本体，不要先清小怪；本体一死，小怪直接消失。"),
        ("smoggy", "烟雾弥漫（Smoggy）",
         "每回合只能打出 1 张技能牌。活雾的 Advanced Gas 会施加此效果；因此每回合只能在防御/过牌/功能技能中选一张最关键的。"),
        ("ritual", "仪式（Ritual）",
         "回合结束时获得力量，越拖越强。Calcified Cultist 等带仪式的怪必须优先击杀，不能和平发育。"),
        ("artifact", "人工制品（Artifact）",
         "抵挡 1 次负面状态（易伤/虚弱/中毒等）。上易伤前先用低价值 debuff 破掉人工制品，或直接用伤害。"),
        ("plating", "覆甲（Plating）",
         "回合结束获得等量格挡，随后层数减少。岩石铠甲（Stone Armor）提供 4 层，是长期防御引擎。"),
        ("target_priority", "目标优先级",
         "召唤者/仪式成长怪 > 高输出攻击者 > 普通小怪。能斩杀召唤者时，忽略小怪；小怪会在召唤者死后消失。"),
        ("a10", "Ascension 10 关键",
         "双 Boss；精英与 Boss 更强；普通怪也容易在低血量时翻车。A10 的牌组要薄、防御要厚、每回合先算格挡。"),
        ("shop_priority", "商店优先级",
         "优先删 1 张打击/防御（100 金），再买高质量防御/过牌；不要为了稀有攻击牌放弃删牌。"),
        ("defense_cards", "铁甲战士防御牌优先级",
         "血墙（Blood Wall，2 费 16 甲）、岩石铠甲（Stone Armor，4 覆甲）、武装（Armaments，5 甲+升级）、Shrug It Off（格挡+抽牌）、Flame Barrier（格挡+反伤）、Taunt（格挡+易伤）。"),
        ("attack_cards", "铁甲战士攻击牌优先级",
         "重锤（Bludgeon，3 费 32/42）、完美打击（Perfected Strike，按打击数量加伤）、熔火之拳（Molten Fist，10 伤+易伤翻倍）、Bash（易伤）、Anger（0 费 6 伤+复制）。"),
        ("living_fog", "活雾（Living Fog）正确打法",
         "80 HP；Smoggy 每回合只能打1张技能；Bloat 召唤 Gas Bomb（7-8 HP，DeathBlow 9）。关键：活雾是召唤者，杀死活雾后 Gas Bomb 会自动消失。所以应该用重锤/攻击全力打活雾，不要浪费输出清炸弹；每回合只选1张最关键技能（血墙/防御），其余费用打攻击。"),
        ("smoggy_math", "Smoggy 回合规划",
         "活雾在场时每回合只能打出1张技能牌。优先顺序：能斩杀的 Attack > 血墙/防御（1张） > 其他技能。不要把两张防御/血墙排在同一回合。"),
        ("summoner_rule", "召唤者规则",
         "带 Minion（爪牙）的怪物会在其领导者死亡时放弃战斗。面对召唤者，目标优先级是“召唤者本体”，不是小怪。"),
        ("act1_threats", "第一章威胁表",
         "普通：Calcified Cultist（仪式2）、Seapunk（力量/8x4）、Living Fog（召唤+Smoggy）、Corpse Slug（双只高输出）、Mawler（10x2+Buff）、Vine Shambler（14x2）、Sludge Spinner（虚弱/脆弱）。精英：Bygone Effigy 127、Byrdonis 81-84、Phrog Parasite 61-64。Boss：Ceremonial Beast 252。"),
        ("block_math", "格挡数学",
         "A10 常见爆发：8x4=32、14x2=28、10x2=20、9x2=18。进入战斗前先看 intent 总伤害；不能斩杀时，格挡要 ≥ 总伤害，否则先杀攻击者/召唤者。"),
        ("deck_size", "牌组厚度",
         "A10 建议 16 张以内；优先删打击/防御。每多一张基础牌，关键防御/重锤的抽取概率就低一分。"),
    ]
    for key, title, note in mechanics:
        cur.execute("INSERT OR REPLACE INTO mechanics VALUES(?,?,?)", (key, title, note))

    con.commit()
    con.close()
    print(json.dumps({
        "db": str(DB),
        "cards": card_count,
        "enemies": enemy_count,
        "relics": relic_count,
        "potions": potion_count,
        "events": event_count,
        "powers": len(eng_powers),
        "enchantments": len(site_ench),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
