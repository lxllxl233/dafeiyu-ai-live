"""非战斗动作白名单：菜单生成、校验、执行（不启动游戏、不调 API）。"""
from __future__ import annotations

from dafeiyu.games.registry import GameRegistry

actions = GameRegistry().load_game_module("slay-the-spire-2", "decision.actions")
assert actions is not None


class _FakeSts2:
    def __init__(self):
        self.calls = []

    def act(self, action, **params):
        self.calls.append((action, params))
        return {}


REST_STATE = {
    "screen": "REST",
    "run": {"floor": 7, "current_hp": 1, "max_hp": 1},
    "available_actions": ["choose_rest_option", "save_and_quit", "use_potion",
                          "discard_potion"],
    "rest": {"options": [
        {"index": 0, "option_id": "HEAL", "title": "休息", "is_enabled": True},
        {"index": 1, "option_id": "SMITH", "title": "锻造", "is_enabled": False},
    ]},
}


def test_rest_menu_hides_the_disabled_option():
    menu = actions.available(REST_STATE)
    assert [(e["a"], e["i"]) for e in menu] == [("choose_rest_option", 0)]


def test_rest_menu_offers_proceed_when_nothing_is_selectable():
    state = {"screen": "REST", "run": {"current_hp": 1, "max_hp": 1},
             "available_actions": ["proceed"], "rest": {"options": []}}
    assert [(e["a"], e["i"]) for e in actions.available(state)] == [("proceed", None)]


def test_check_accepts_only_menu_entries():
    entry, err = actions.check({"act": "choose_rest_option", "i": 0}, REST_STATE)
    assert entry and not err
    assert actions.check({"act": "choose_rest_option", "i": 1}, REST_STATE)[0] is None
    assert actions.check({"act": "choose_rest_option", "i": 7}, REST_STATE)[0] is None
    assert actions.check({"act": "choose_rest_option", "i": "x"}, REST_STATE)[0] is None
    assert actions.check({}, REST_STATE)[0] is None
    assert actions.check({"act": "proceed"}, REST_STATE)[0] is None


def test_dangerous_actions_never_reach_the_menu():
    unsafe = {"save_and_quit", "use_potion", "discard_potion", "open_timeline",
              "play_card", "end_turn"}
    assert not (unsafe & {e["a"] for e in actions.available(REST_STATE)})
    entry, err = actions.check({"act": "save_and_quit"}, REST_STATE)
    assert entry is None and "save_and_quit" in err


def test_execute_clicks_through_sts2_act():
    fake = _FakeSts2()
    entry, _ = actions.check({"act": "choose_rest_option", "i": 0}, REST_STATE)
    actions.execute(fake, entry)
    assert fake.calls == [("choose_rest_option", {"option_index": 0})]
    actions.execute(fake, {"a": "proceed", "i": None, "n": "继续"})
    assert fake.calls[-1] == ("proceed", {})


def test_event_menu_skips_locked_options():
    state = {"screen": "EVENT", "available_actions": ["choose_event_option"],
             "event": {"options": [{"title": "锁着的", "is_locked": True},
                                   {"title": "能点的"}]}}
    assert [(e["a"], e["i"]) for e in actions.available(state)] == [
        ("choose_event_option", 1)]


def test_shop_menu_drops_unaffordable_items_and_offers_leave():
    state = {"screen": "SHOP", "run": {"gold": 60},
             "available_actions": ["buy_card", "buy_relic", "leave_shop"],
             "shop": {"cards": [{"name": "重击", "price": 50, "available": True},
                                {"name": "恶魔形态", "price": 300, "available": True}],
                      "relics": [{"name": "燃烧之血", "price": 150, "available": True}],
                      "potions": []}}
    assert [(e["a"], e["i"]) for e in actions.available(state)] == [
        ("buy_card", 0), ("leave_shop", None)]


def test_shop_menu_lists_close_shelf_while_open():
    """货架开着只有 close_shop_inventory（run-0111 卡死就是缺这条菜单）。"""
    state = {"screen": "SHOP", "run": {"gold": 200},
             "available_actions": ["close_shop_inventory", "buy_card",
                                   "discard_potion"],
             "shop": {"is_open": True,
                      "cards": [{"name": "余烬", "price": 130, "available": True},
                                {"name": "恶魔形态", "price": 300, "available": True}]}}
    menu = actions.available(state)
    assert [(e["a"], e["i"]) for e in menu] == [
        ("close_shop_inventory", None), ("buy_card", 0)]   # 买不起的不进菜单
    entry, err = actions.check({"act": "close_shop_inventory"}, state)
    assert entry is not None and err == ""
    assert "save_and_quit" not in {e["a"] for e in menu}


def test_card_reward_menu_uses_choose_reward_card():
    """卡牌奖励也是 CARD_SELECTION；旧菜单只认 select_deck_card，llm 模式下永远走本地。"""
    state = {"screen": "CARD_SELECTION", "available_actions": ["choose_reward_card",
                                                               "skip_reward_cards"],
             "selection": {"cards": [{"name": "余烬", "rules_text": "造成18点伤害"},
                                     {"name": "防御", "rules_text": "获得5点格挡"}]}}
    menu = actions.available(state)
    assert [(e["a"], e["i"]) for e in menu] == [
        ("choose_reward_card", 0), ("choose_reward_card", 1),
        ("skip_reward_cards", None)]
    entry, err = actions.check({"act": "choose_reward_card", "i": 1}, state)
    assert entry is not None and err == "" and entry["n"] == "防御"


def test_chest_menu_can_claim_rewards_and_close():
    state = {"screen": "CHEST",
             "available_actions": ["claim_all_rewards", "collect_rewards_and_proceed"],
             "chest": {"relic_options": [{"name": "燃烧之血"}]}}
    menu = actions.available(state)
    assert [(e["a"], e["i"]) for e in menu] == [
        ("claim_all_rewards", None), ("collect_rewards_and_proceed", None)]


def test_map_menu_only_lists_available_nodes():
    state = {"screen": "MAP", "available_actions": ["choose_map_node"],
             "map": {"nodes": [{"row": 0, "col": 0, "node_type": "Monster",
                                "is_available": True, "children": []},
                               {"row": 0, "col": 1, "node_type": "RestSite",
                                "is_available": True, "children": []},
                               {"row": 1, "col": 0, "node_type": "Elite",
                                "is_available": False, "children": []}]}}
    menu = actions.available(state)
    assert [e["i"] for e in menu] == [0, 1]
    assert menu[1]["n"].startswith("RestSite")


def test_unknown_screen_has_no_menu():
    assert actions.available({"screen": "MAIN_MENU",
                              "available_actions": ["open_character_select"]}) == []
    assert actions.available({"screen": "SOMETHING_NEW"}) == []
