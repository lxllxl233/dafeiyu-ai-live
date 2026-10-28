from __future__ import annotations

import time

from dafeiyu.persona import Pacing, Persona
from dafeiyu.persona.lint import lint


def test_card_loads():
    p = Persona.load("dafeiyu")
    assert p.card.name == "大肥鱼"
    assert p.card.style.max_len == 60
    assert "刚好够" in p.card.quips


def test_compose_strips_prefix_and_joins():
    p = Persona.load()
    text = p.compose("大肥鱼：它 21，我最多挡 15", "，差 6，嗨呀。")
    assert text == "它 21，我最多挡 15。差 6，嗨呀。"
    # 不能出现双句号
    assert "。。" not in text


def test_compose_handles_single_side():
    p = Persona.load()
    assert p.compose("", "差 6") == "差 6。"
    assert p.compose("它 21", "") == "它 21。"


def test_lint_rejects_empty_prefix_and_labels():
    assert any(i.level == "FAIL" for i in lint(""))
    assert any(i.level == "FAIL" for i in lint("大肥鱼：它 21 我 15"))
    assert any(i.level == "FAIL" for i in lint("分析：敌人攻击力 21"))
    assert any(i.level == "FAIL" for i in lint("它 21，我 15。" * 12))


def test_lint_flags_double_quip():
    issues = p_lint("刚好够，这下看懂了。")
    assert any(i.level == "FAIL" and i.rule == "quip" for i in issues)


def p_lint(text: str):
    return Persona.load().lint(text)


def test_lint_passes_clean_line():
    issues = Persona.load().lint("9 点，它 9 血。刚好够。")
    assert not [i for i in issues if i.level == "FAIL"]


def test_lint_warns_without_number():
    issues = Persona.load().lint("这波稳了，上车。")
    assert any(i.rule == "number" and i.level == "WARN" for i in issues)


def test_render_shape():
    r = Persona.load().render("它 21，我最多挡 15", "差 6，嗨呀。")
    assert r["text"]
    assert isinstance(r["lines"], list) and r["lines"]
    assert r["lint"]["ok"] is True
    assert "pacing" in r


def test_pacing_gap_repeat_and_quips():
    pace = Pacing()
    now = time.time()
    assert pace.gap_remaining(now) == 0
    pace.note_sent("它 21，我 15。刚好够。", ("刚好够",), now=now)
    assert pace.gap_remaining(now + 0.1) > 0
    assert pace.advice("它 21，我 15。刚好够。", ("刚好够",), now + 0.2)["repeat"] is True
    assert pace.quip_allowed("刚好够", now + 1) is False
    assert pace.quip_allowed("刚好够", now + 601) is True
    assert pace.stats(now + 1)["sent"] == 1

def test_generic_memes_are_game_agnostic():
    p = Persona.load()
    bank = p.memes()
    assert bank["id"] == "general"
    assert len(bank["items"]) >= 10
    item = p.pick_meme(tag="flag")
    assert item is not None and item["text"]
    assert p.pick_meme(tag="no-such-tag") is None