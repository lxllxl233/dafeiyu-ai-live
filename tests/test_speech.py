"""说话回归：不念血条、坏局面有情绪、语气影响曼波语速。"""
from __future__ import annotations

import re

from dafeiyu.voice import MamboVoice
from dafeiyu.games.registry import GameRegistry

play = GameRegistry().load_game_module("slay-the-spire-2", "autoplay.runner")
prompts = GameRegistry().load_game_module("slay-the-spire-2", "decision.prompts")

_CN_NUM = re.compile(r"[0-9]")


def _plan(**kw):
    plan = {"steps": [{"id": "STRIKE_IRONCLAD", "t": 0}], "dmg": 6, "kills": 0,
            "incoming": 0, "taken": 0, "hp_after": 60, "kind": "best"}
    plan.update(kw)
    return plan


def _state(hp=60, max_hp=80, turn=3):
    return {"screen": "COMBAT", "turn": turn,
            "run": {"current_hp": hp, "max_hp": max_hp},
            "combat": {"player": {"current_hp": hp, "max_hp": max_hp, "energy": 3,
                                  "block": 0, "powers": []},
                       "enemies": [{"index": 0, "enemy_id": "JAW_WORM", "name": "虫",
                                    "current_hp": 40, "max_hp": 44, "block": 0,
                                    "powers": [], "intents": [
                                        {"intent_type": "Attack", "total_damage": 11}]}]}}


def test_combat_bark_never_recites_hp():
    for hp in (70, 20, 3):
        for turn in (0, 3, 7):
            for plan in (_plan(), _plan(incoming=11), _plan(kills=1), _plan(incoming=40)):
                thought, meme = play._combat_bark(plan, _state(hp=hp, turn=turn))
                text = f"{thought}{meme}"
                assert "HP" not in text, text
                # 血量只允许用「血不多了」这种感受句；A10 这种非血量数字可以有。
                assert not re.search(r"\d+\s*/\s*\d+|我\s*\d+\s*血", text), text


def test_combat_bark_has_emotion_when_dying_and_is_short_when_killing():
    thought, meme = play._combat_bark(_plan(incoming=30), _state(hp=12))
    assert "唉" in thought, thought
    thought, meme = play._combat_bark(_plan(kills=1), _state(hp=60))
    assert "收工" in thought
    assert len(thought) <= 20


def test_combat_bark_varies_by_turn():
    lines = {play._combat_bark(_plan(), _state(turn=t))[0] for t in range(5)}
    assert len(lines) >= 4


def test_goals_do_not_bait_hp_recitation():
    combat = prompts.combat_goal({"floor": 7, "current_hp": 32, "max_hp": 80})
    assert "HP" not in combat and "32/80" not in combat
    screen = prompts.noncombat_goal("SHOP", {"run": {"floor": 7, "current_hp": 32,
                                                    "max_hp": 80, "gold": 120,
                                                    "deck": []}})
    assert "HP" not in screen and "120" not in screen


def test_system_prompt_bans_hp_recital_and_allows_sighs():
    assert "别念血条" in prompts.SYSTEM_PROMPT
    assert "唉" in prompts.SYSTEM_PROMPT
    assert "1–2 句" in prompts.SYSTEM_PROMPT
    assert "别念 HP/金币流水账" in prompts.NONCOMBAT_PROMPT


def test_lint_flags_hp_recital_and_accepts_emotional_line():
    from dafeiyu.persona.lint import lint

    hp_issue = [i for i in lint("HP 32/80，这波稳了。") if i.rule == "hp"]
    assert hp_issue and hp_issue[0].level == "WARN"
    sigh = lint("唉……这波稳了。")
    assert not [i for i in sigh if i.rule in ("number", "hp")]
    assert [i for i in lint("唉……唔……") if i.rule == "sigh"]


def test_mambo_reads_sigh_lines_softer(tmp_path):
    voice = MamboVoice(url="http://127.0.0.1:1", timeout=0.3, cache_dir=tmp_path)
    assert voice._delivery("唉……这波稳了。") == (0.94, "soft")
    assert voice._delivery("它 21，挡满。") == (1.0, "")
    voice.cfg["sigh_speed"] = 0.9
    assert voice._delivery("唔")[0] == 0.9
