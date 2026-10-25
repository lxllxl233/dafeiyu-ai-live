# -*- coding: utf-8 -*-
"""用「模拟的 mod 选牌状态机」验证 _pick_cards_once 的完整流程。

真实字段来自 mod 的 /state（2026-10-25 实测）：
  selection.kind / prompt / min_select / max_select / selected_count /
  requires_confirmation / can_confirm / cards[].index, card_id, upgraded
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sts2


class FakeGame:
    """极简 mod 模拟器：只实现 CARD_SELECTION 的选中/确认语义。"""

    def __init__(self, cards, min_select, max_select, requires_confirmation=False, remove_immediately=False, shrink_each=False):
        self.cards = [dict(c) for c in cards]
        self.min_select = min_select
        self.max_select = max_select
        self.requires_confirmation = requires_confirmation
        self.remove_immediately = remove_immediately
        self.shrink_each = shrink_each
        self.removed = 0
        self.picked = []
        self.done = False
        self.confirmed = 0
        self.actions = []

    def state(self):
        if self.done:
            return {"screen": "EVENT", "available_actions": ["proceed"], "event": {"event_id": "X"}}
        sel_cards = []
        for i, c in enumerate(self.cards):
            d = dict(c)
            d["index"] = i
            d["is_selected"] = i in self.picked
            sel_cards.append(d)
        can_confirm = len(self.picked) >= self.min_select
        return {
            "screen": "CARD_SELECTION",
            "available_actions": ["select_deck_card"] + (["confirm_selection"] if can_confirm else []),
            "selection": {
                "kind": "deck_card_select",
                "prompt": f"选择{self.min_select}张牌来移除。",
                "min_select": self.min_select,
                "max_select": self.max_select,
                "selected_count": len(self.picked),
                "requires_confirmation": self.requires_confirmation,
                "can_confirm": can_confirm,
                "cards": sel_cards,
            },
            "run": {"floor": 1, "hp": 40, "max_hp": 80, "gold": 100},
        }

    def act(self, action, **params):
        self.actions.append((action, dict(params)))
        if action == "select_deck_card":
            idx = params["option_index"]
            if idx in self.picked:
                self.picked.remove(idx)          # 再点同一张 = 取消
            else:
                self.picked.append(idx)
            if self.shrink_each:
                # 每点一张就立刻移除一张（列表边点边缩）
                self.cards.pop(idx)
                self.removed += 1
                self.picked = []
                if self.removed >= self.min_select:
                    self.done = True
                return {"status": "completed", "message": ""}
            if self.remove_immediately and len(self.picked) >= self.min_select:
                # 有些界面点一张就立刻移除并关界面
                self._apply()
            elif not self.requires_confirmation and len(self.picked) >= self.min_select:
                if len(self.picked) >= self.max_select:
                    self._apply()
            return {"status": "completed", "message": ""}
        if action == "confirm_selection":
            self.confirmed += 1
            self._apply()
            return {"status": "completed", "message": ""}
        raise AssertionError(f"unexpected action {action}")

    def _apply(self):
        for i in sorted(self.picked, reverse=True):
            if i < len(self.cards):
                self.cards.pop(i)
        self.picked = []
        self.done = True


def run_case(name, game, picks):
    print(f"=== {name} ===")
    sts2.get_state = game.state
    sts2.act = game.act
    sts2.time.sleep = lambda *_a, **_k: None   # 跳过轮询等待
    out = sts2._pick_cards_once(picks, "t", "m")
    print("最终 screen:", out.get("screen"), "| 剩余卡:", [c["card_id"] for c in game.cards])
    print("动作序列:", game.actions)
    print()


cards = [
    {"card_id": "STRIKE_IRONCLAD", "upgraded": False, "energy_cost": 1},
    {"card_id": "STRIKE_IRONCLAD", "upgraded": False, "energy_cost": 1},
    {"card_id": "DEFEND_IRONCLAD", "upgraded": False, "energy_cost": 1},
    {"card_id": "BASH", "upgraded": False, "energy_cost": 2},
]

# 1) 选 2 张：点 0 和 3（两张打击 + 痛击）→ 界面在选满后自动关闭
run_case("多选 2 张（自动提交）", FakeGame(cards, 2, 2), [0, 3])

# 2) 选 1 张（商店删牌，点一张即生效）
run_case("单选 1 张（点一张即移除）", FakeGame(cards, 1, 1), [2])

# 3) 需要手动确认的界面 → 自动 confirm_selection
run_case("多选 2 张（需要确认）", FakeGame(cards, 2, 2, requires_confirmation=True), [1, 2])

# 4) 点一张就立刻移除的界面（列表会缩短）→ 第二次点击要按卡牌身份重新定位
run_case("多选 2 张（列表边点边缩）", FakeGame(cards, 2, 2, shrink_each=True), [0, 1])

print("ALL CASES DONE")
sys.exit(0)
