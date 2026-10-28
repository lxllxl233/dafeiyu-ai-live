"""玩具决斗：一个完全离线的第二游戏插件，用来验收非 STS2 链路。

它不是直播内容，而是可重复的控制面验收对象：

- ``observe`` 读 ``logs/toy_duel_state.json``；
- ``validate`` 按手牌和能量检查计划；
- ``execute`` 真正结算一回合，并回写状态文件；
- ``runner`` 用同一套 ``GameTools`` 自动玩，状态交给核心 ``AutoplayHost``。

动作协议（``toy.action/v1``）::

    {"seq": [{"play": "STRIKE"}, {"play": "GUARD"}, {"end": true}]}

状态文件只用于演示/回归；正式游戏应把状态放在自己的兼容层里。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from dafeiyu import paths
from dafeiyu.games.contracts import (
    ActionCode,
    ActionResult,
    GameStateEnvelope,
)

GAME_ID = "toy-duel"
STATE_ENV = "TOY_DUEL_STATE"
MAX_STEPS = 12

CARDS: dict[str, dict[str, Any]] = {
    "STRIKE": {"id": "STRIKE", "name": "打击", "cost": 1, "damage": 6, "block": 0},
    "GUARD": {"id": "GUARD", "name": "防御", "cost": 1, "damage": 0, "block": 5},
}
HAND_IDS = ("STRIKE", "STRIKE", "GUARD")

_LOCK = threading.RLock()


def state_path() -> Path:
    """演示状态路径；测试可用 ``TOY_DUEL_STATE`` 指到临时目录。"""
    override = os.environ.get(STATE_ENV)
    if override:
        return Path(override).expanduser()
    return paths.LOGS / "toy_duel_state.json"


def _hand() -> list[dict[str, Any]]:
    return [dict(CARDS[card_id]) for card_id in HAND_IDS]


def new_state(*, run: int = 1) -> dict[str, Any]:
    """开一局新演示；写入状态文件，供适配器和工具跨模块读取。"""
    state = {
        "game_id": GAME_ID,
        "run": int(run),
        "screen": "COMBAT",
        "turn": 1,
        "energy": 3,
        "max_energy": 3,
        "hero": {"hp": 30, "max_hp": 30, "block": 0},
        "enemy": {"id": "训练木桩", "hp": 18, "max_hp": 18, "attack": 6},
        "hand": _hand(),
        "last": "演示开始：先打两下，再挡一下。",
    }
    _save(state)
    return state


def _save(state: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=1)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _read() -> dict[str, Any] | None:
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def peek_state() -> dict[str, Any]:
    """只读当前状态；没有文件时返回空 dict，不创建文件。"""
    return _read() or {}


def load_state() -> dict[str, Any]:
    """工具/runner 用：没有状态就开一局，保证演示总能启动。"""
    raw = _read()
    if raw is None:
        return new_state()
    state = {
        "game_id": GAME_ID,
        "run": int(raw.get("run") or 1),
        "screen": str(raw.get("screen") or "COMBAT"),
        "turn": int(raw.get("turn") or 1),
        "energy": int(raw.get("energy") or 0),
        "max_energy": int(raw.get("max_energy") or 3),
        "hero": dict(raw.get("hero") or {}),
        "enemy": dict(raw.get("enemy") or {}),
        "hand": list(raw.get("hand") or _hand()),
        "last": str(raw.get("last") or ""),
    }
    state["hero"].setdefault("hp", 30)
    state["hero"].setdefault("max_hp", 30)
    state["hero"].setdefault("block", 0)
    state["enemy"].setdefault("id", "训练木桩")
    state["enemy"].setdefault("hp", 18)
    state["enemy"].setdefault("max_hp", 18)
    state["enemy"].setdefault("attack", 6)
    if not state["hand"]:
        state["hand"] = _hand()
    return state


def context(state: dict[str, Any]) -> dict[str, Any]:
    hero = state["hero"]
    enemy = state["enemy"]
    return {
        "screen": state["screen"],
        "turn": int(state.get("turn") or 0),
        "run": int(state.get("run") or 0),
        "hero": {
            "hp": int(hero.get("hp") or 0),
            "max_hp": int(hero.get("max_hp") or 0),
            "block": int(hero.get("block") or 0),
        },
        "enemy": {
            "id": str(enemy.get("id") or ""),
            "hp": int(enemy.get("hp") or 0),
            "max_hp": int(enemy.get("max_hp") or 0),
            "attack": int(enemy.get("attack") or 0),
        },
        "energy": int(state.get("energy") or 0),
        "max_energy": int(state.get("max_energy") or 0),
        "hand": [dict(card) for card in state["hand"]],
        "last": str(state.get("last") or ""),
    }


def envelope(state: dict[str, Any]) -> GameStateEnvelope:
    ctx = context(state)
    return GameStateEnvelope(
        game_id=GAME_ID,
        revision=f"turn-{ctx['turn']}",
        summary={
            "screen": ctx["screen"],
            "floor": ctx["turn"],
            "hp": ctx["hero"]["hp"],
            "max_hp": ctx["hero"]["max_hp"],
            "in_combat": ctx["screen"] == "COMBAT",
            "turn": ctx["turn"],
        },
        data=ctx,
    )


def _resolve(plan: dict[str, Any], state: dict[str, Any]) -> tuple[list[dict], str, int]:
    """检查计划，返回 ``(规范动作, 错误, 剩余能量)``；不修改 state。"""
    if not isinstance(plan, dict):
        return [], "plan 必须是对象", int(state.get("energy") or 0)
    raw_seq = plan.get("seq")
    if raw_seq is None:
        if any(key in plan for key in ("play", "card", "end")):
            raw_seq = [plan]
        else:
            return [], "plan 缺少 seq", int(state.get("energy") or 0)
    if not isinstance(raw_seq, list) or not raw_seq:
        return [], "seq 必须是非空数组", int(state.get("energy") or 0)

    energy = int(state.get("energy") or 0)
    hand = [dict(card) for card in state.get("hand") or []]
    resolved: list[dict] = []
    for index, item in enumerate(raw_seq, 1):
        if not isinstance(item, dict):
            return [], f"seq[{index}] 不是对象", energy
        if item.get("end"):
            resolved.append({"end": True})
            continue
        card_id = str(item.get("play") or item.get("card") or "").strip().upper()
        if not card_id:
            return [], f"seq[{index}] 缺少 play/card", energy
        if card_id not in CARDS:
            return [], f"未知卡牌：{card_id}", energy
        slot = next((i for i, card in enumerate(hand)
                     if str(card.get("id") or "").upper() == card_id), None)
        if slot is None:
            return [], f"手牌没有 {card_id}", energy
        cost = int(CARDS[card_id]["cost"])
        if cost > energy:
            return [], f"能量不足：{card_id} 需要 {cost}，只剩 {energy}", energy
        energy -= cost
        hand.pop(slot)
        resolved.append({"play": card_id})
    return resolved, "", energy


def _apply(state: dict[str, Any], steps: list[dict]) -> str:
    hero = state["hero"]
    enemy = state["enemy"]
    messages: list[str] = []
    for step in steps:
        if state["screen"] != "COMBAT":
            break
        if step.get("end"):
            _end_turn(state)
            messages.append("结束回合")
            continue
        card_id = str(step.get("play") or "")
        card = CARDS.get(card_id)
        if card is None:
            continue
        hero["block"] = int(hero.get("block") or 0) + int(card["block"])
        enemy["hp"] = max(0, int(enemy.get("hp") or 0) - int(card["damage"]))
        for index, item in enumerate(state["hand"]):
            if str(item.get("id") or "").upper() == card_id:
                state["hand"].pop(index)
                break
        state["energy"] = max(0, int(state.get("energy") or 0) - int(card["cost"]))
        messages.append(f"{card['name']}：伤害 {card['damage']} / 格挡 {card['block']}")
        if int(enemy["hp"]) <= 0:
            state["screen"] = "REWARD"
            state["last"] = "训练木桩倒了，演示进入奖励界面。"
            messages.append("敌人倒下")
            break
    if state["screen"] == "COMBAT":
        state["last"] = "；".join(messages) or "空动作"
    return state["last"]


def _end_turn(state: dict[str, Any]) -> None:
    if state["screen"] != "COMBAT":
        return
    hero = state["hero"]
    enemy = state["enemy"]
    attack = int(enemy.get("attack") or 0)
    block = int(hero.get("block") or 0)
    absorbed = min(block, attack)
    hero["block"] = block - absorbed
    hero["hp"] = max(0, int(hero.get("hp") or 0) - (attack - absorbed))
    if int(hero["hp"]) <= 0:
        state["screen"] = "GAME_OVER"
        state["last"] = "演示角色倒下，准备重开。"
        return
    state["turn"] = int(state.get("turn") or 1) + 1
    state["energy"] = int(state.get("max_energy") or 3)
    hero["block"] = 0
    state["hand"] = _hand()
    state["last"] = f"木桩反击 {attack} 点，挡下 {absorbed}。"


def plan_for(ctx: dict[str, Any]) -> dict[str, Any]:
    """演示策略：能杀就全打，否则先打一下再挡；最后结束回合。"""
    hand = [str(card.get("id") or "").upper() for card in ctx.get("hand") or []]
    strikes = hand.count("STRIKE")
    guards = hand.count("GUARD")
    enemy_hp = int((ctx.get("enemy") or {}).get("hp") or 0)
    seq: list[dict] = []
    if strikes and enemy_hp <= strikes * int(CARDS["STRIKE"]["damage"]):
        seq.extend({"play": "STRIKE"} for _ in range(strikes))
    else:
        if strikes:
            seq.append({"play": "STRIKE"})
        if guards:
            seq.append({"play": "GUARD"})
        if strikes > 1:
            seq.append({"play": "STRIKE"})
    seq.append({"end": True})
    return {"seq": seq}


class ToyTools:
    """实现 ``GameTools``；返回值统一 ``ActionResult``。"""

    def __init__(self, game_id: str = GAME_ID) -> None:
        self.game_id = game_id

    def observe(self) -> ActionResult:
        with _LOCK:
            state = load_state()
            ctx = context(state)
            return ActionResult.success(
                "observe",
                message=f"{ctx['screen']} · 第 {ctx['turn']} 回合",
                state=envelope(state),
                data={"context": ctx},
            )

    def validate(self, plan: dict) -> ActionResult:
        with _LOCK:
            state = load_state()
            steps, error, energy = _resolve(plan, state)
            if error:
                return ActionResult.failure(
                    "validate",
                    code=ActionCode.INVALID_PLAN,
                    message=error,
                    data={"context": context(state)},
                )
            return ActionResult.success(
                "validate",
                message="计划合法",
                data={
                    "seq": steps,
                    "energy_after": energy,
                    "context": context(state),
                },
            )

    def execute(self, plan: dict, *, dry_run: bool = True) -> ActionResult:
        with _LOCK:
            state = load_state()
            steps, error, _energy = _resolve(plan, state)
            if error:
                return ActionResult.failure(
                    "execute",
                    code=ActionCode.INVALID_PLAN,
                    message=error,
                    data={"dry_run": bool(dry_run), "context": context(state)},
                )
            if dry_run:
                return ActionResult.success(
                    "execute",
                    message="预演通过（未落盘）",
                    data={
                        "dry_run": True,
                        "seq": steps,
                        "context": context(state),
                    },
                )
            message = _apply(state, steps)
            _save(state)
            return ActionResult.success(
                "execute",
                message=message,
                state=envelope(state),
                data={
                    "dry_run": False,
                    "seq": steps,
                    "receipt": f"toy-{state['run']}-{state['turn']}",
                    "context": context(state),
                },
            )


__all__ = [
    "CARDS",
    "GAME_ID",
    "MAX_STEPS",
    "STATE_ENV",
    "ToyTools",
    "context",
    "envelope",
    "load_state",
    "new_state",
    "peek_state",
    "plan_for",
    "state_path",
]
