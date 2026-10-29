"""非战斗动作白名单：菜单生成、校验、执行（不启动游戏、不调 API）。"""
from __future__ import annotations

from dafeiyu.games.registry import GameRegistry

actions = GameRegistry().load_game_module("slay-the-spire-2", "decision.actions")
assert actions is not None
act = GameRegistry().load_game_module("slay-the-spire-2", "decision.act")
assert act is not None


def test_potion_written_in_fx_is_moved_into_seq():
    """旧提示词让模型写 fx.p；执行器只认 seq 里的 {"p": 槽位}，没这一步就一滴不喝。"""
    seq, _lines, fx, _auto = act.normalize(
        {"seq": [{"c": "BASH", "t": 0}, {"end": 1}], "fx": {"p": 1, "hp": 40}})
    assert seq[0] == {"p": 1, "t": None}
    assert seq[1]["c"] == "BASH"
    assert "p" not in fx and fx["hp"] == 40


def test_potion_in_seq_is_not_duplicated_by_fx():
    seq, *_ = act.normalize({"seq": [{"p": 0, "t": 0}, {"end": 1}], "fx": {"p": 0}})
    assert seq == [{"p": 0, "t": 0}, {"end": 1}]


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


def test_normalize_keeps_only_one_say_line():
    _seq, lines, _fx, _auto = act.normalize(
        {"seq": [{"c": "BASH", "t": 0}], "say": ["先上易伤。", "这轮安全。"]})
    assert [ln["t"] for ln in lines] == ["先上易伤。"]


CHEST_STATE = {
    "screen": "CHEST",
    "run": {"floor": 4, "current_hp": 50, "max_hp": 80},
    "available_actions": ["choose_treasure_relic", "claim_reward",
                          "collect_rewards_and_proceed", "proceed"],
    "chest": {"has_relic_been_claimed": False,
              "relic_options": [{"name": "红头骨", "description": "+2 力量"}]},
    "reward": {"rewards": [{"name": "30 金币", "claimable": True}]},
}


def test_chest_relic_must_be_taken_before_leaving():
    menu = [(e["a"], e["i"]) for e in actions.available(CHEST_STATE)]
    assert ("choose_treasure_relic", 0) in menu
    entry, err = actions.check({"act": "proceed"}, CHEST_STATE)
    assert entry is None and "遗物" in err
    entry, err = actions.check({"act": "collect_rewards_and_proceed"}, CHEST_STATE)
    assert entry is None and "遗物" in err
    entry, err = actions.check({"act": "choose_treasure_relic", "i": 0}, CHEST_STATE)
    assert entry and not err


def test_chest_allows_leaving_after_relic_claimed():
    taken = {**CHEST_STATE,
             "chest": {"has_relic_been_claimed": True, "relic_options": []},
             "reward": {"rewards": []}}
    entry, err = actions.check({"act": "proceed"}, taken)
    assert entry and not err


def test_chest_blocks_leaving_with_unclaimed_rewards():
    taken = {**CHEST_STATE,
             "chest": {"has_relic_been_claimed": True, "relic_options": []}}
    entry, err = actions.check({"act": "proceed"}, taken)
    assert entry is None and "奖励" in err
    entry, err = actions.check({"act": "claim_reward", "i": 0}, taken)
    assert entry and not err


def test_event_menu_skips_locked_options():
    state = {"screen": "EVENT", "available_actions": ["choose_event_option"],
             "event": {"options": [{"title": "锁着的", "is_locked": True},
                                   {"title": "能点的"}]}}
    assert [(e["a"], e["i"]) for e in actions.available(state)] == [
        ("choose_event_option", 1)]


def test_shop_menu_drops_unaffordable_items_and_offers_proceed():
    # mod v0.8.0 不发 leave_shop：货架关掉后出口是 proceed。
    state = {"screen": "SHOP", "run": {"gold": 60},
             "available_actions": ["buy_card", "buy_relic", "proceed"],
             "shop": {"cards": [{"name": "重击", "price": 50, "available": True},
                                {"name": "恶魔形态", "price": 300, "available": True}],
                      "relics": [{"name": "燃烧之血", "price": 150, "available": True}],
                      "potions": []}}
    assert [(e["a"], e["i"]) for e in actions.available(state)] == [
        ("buy_card", 0), ("proceed", None)]


def test_shop_hides_shelf_when_nothing_is_affordable():
    """一样都买不起时别再给「看货架」：模型会开/关货架来回切（实机 90 金）。"""
    state = {"screen": "SHOP", "run": {"gold": 30},
             "available_actions": ["open_shop_inventory", "proceed"],
             "shop": {"is_open": False,
                      "cards": [{"name": "恶魔形态", "price": 300, "available": True}],
                      "card_removal": {"price": 100, "available": True, "used": False}}}
    assert [(e["a"], e["i"]) for e in actions.available(state)] == [("proceed", None)]


def test_shop_keeps_shelf_when_removal_is_affordable():
    state = {"screen": "SHOP", "run": {"gold": 120},
             "available_actions": ["open_shop_inventory", "proceed"],
             "shop": {"is_open": False,
                      "cards": [{"name": "恶魔形态", "price": 300, "available": True}],
                      "card_removal": {"price": 100, "available": True, "used": False}}}
    assert [e["a"] for e in actions.available(state)] == ["open_shop_inventory", "proceed"]


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


def test_confirm_only_after_rest_site_card_is_selected():
    """run-0049 f7 营火升级：选够 1 张后菜单只剩「确认」。
    mod 的 select_deck_card 是切换，再点同一张会取消选择 → 界面原地不动。"""
    state = {"screen": "CARD_SELECTION",
             "available_actions": ["save_and_quit", "select_deck_card",
                                   "confirm_selection", "discard_potion"],
             "selection": {"min_select": 1, "max_select": 1,
                           "requires_confirmation": True, "can_confirm": True,
                           "selected_count": 1,
                           "cards": [{"name": "打击", "card_id": "STRIKE_IRONCLAD"},
                                     {"name": "防御", "card_id": "DEFEND_IRONCLAD"}]}}
    menu = actions.available(state)
    assert [(e["a"], e["i"]) for e in menu] == [("confirm_selection", None)]
    assert "已选 1/1" in menu[0]["n"]

    # 还没选够：列牌，不列确认（此时确认也没有意义）
    state["selection"]["selected_count"] = 0
    state["selection"]["can_confirm"] = False
    assert [e["a"] for e in actions.available(state)] == ["select_deck_card",
                                                          "select_deck_card"]


def test_chest_menu_can_claim_rewards_and_continue():
    # mod 的发奖动作用 claim_reward（claim_all_rewards / close_chest 它根本不发）。
    state = {"screen": "CHEST",
             "available_actions": ["claim_reward", "collect_rewards_and_proceed"],
             "chest": {"relic_options": [{"name": "燃烧之血"}]},
             "reward": {"rewards": [{"name": "金币", "reward_type": "Gold",
                                     "claimable": True}]}}
    menu = actions.available(state)
    assert [(e["a"], e["i"]) for e in menu] == [
        ("claim_reward", 0), ("collect_rewards_and_proceed", None)]


def test_chest_menu_hides_relic_after_it_is_gone():
    state = {"screen": "CHEST",
             "available_actions": ["choose_treasure_relic", "proceed"],
             "chest": {"has_relic_been_claimed": True, "relic_options": []}}
    assert [(e["a"], e["i"]) for e in actions.available(state)] == [("proceed", None)]


def test_chest_menu_keeps_relic_when_claimed_flag_lies():
    # 实机 run-0048 f10：mod 给 claimed=True，但遗物还在 relic_options 里没点。
    state = {"screen": "CHEST",
             "available_actions": ["choose_treasure_relic", "proceed"],
             "chest": {"has_relic_been_claimed": True,
                       "relic_options": [{"name": "冰淇淋"}]}}
    assert [(e["a"], e["i"]) for e in actions.available(state)] == [
        ("choose_treasure_relic", 0), ("proceed", None)]
    entry, err = actions.check({"act": "proceed"}, state)
    assert entry is None and "遗物" in err


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


# ---------------------------------------------------------------- 奖励屏：别静默拿第一张
REWARD_WITH_CARD = {
    "screen": "REWARD",
    "available_actions": ["resolve_rewards", "collect_rewards_and_proceed",
                          "claim_reward"],
    "reward": {"pending_card_choice": False,
               "rewards": [{"index": 0, "reward_type": "Gold", "description": "7金币",
                            "claimable": True},
                           {"index": 1, "reward_type": "Card",
                            "description": "将一张牌添加到你的牌组。", "claimable": True}]},
}


def test_reward_menu_uses_reward_description_as_the_label():
    """mod 的奖励条目没有 name；旧菜单只剩 "Card"/"Gold"，模型看不出这是什么。"""
    menu = actions.available(REWARD_WITH_CARD)
    assert [(e["a"], e["i"], e["n"]) for e in menu] == [
        ("claim_reward", 0, "7金币"),
        ("claim_reward", 1, "将一张牌添加到你的牌组。"),
        ("collect_rewards_and_proceed", None, "收下全部并继续"),
    ]


def test_reward_collect_blocked_while_card_reward_unopened():
    """collect 会让 mod 静默把第一张牌塞进牌组，台词却说「不拿」——必须先 claim。"""
    entry, err = actions.check({"act": "collect_rewards_and_proceed"}, REWARD_WITH_CARD)
    assert entry is None and "卡牌" in err and "claim_reward" in err
    entry, err = actions.check({"act": "claim_reward", "i": 1}, REWARD_WITH_CARD)
    assert entry is not None and err == ""


def test_reward_collect_allowed_after_card_claimed():
    taken = {**REWARD_WITH_CARD,
             "reward": {"pending_card_choice": False,
                        "rewards": [{"index": 0, "reward_type": "Gold",
                                     "claimable": True}]}}
    entry, err = actions.check({"act": "collect_rewards_and_proceed"}, taken)
    assert entry is not None and err == ""
    # 已经 claim 过的 Card 也不能再拦
    claimed = {**REWARD_WITH_CARD,
               "reward": {"pending_card_choice": False,
                          "rewards": [{"index": 0, "reward_type": "Card",
                                       "claimable": False, "claimed": True}]}}
    assert actions.check({"act": "collect_rewards_and_proceed"}, claimed)[0] is not None


def test_card_reward_screen_does_not_offer_deck_select_twice():
    """卡牌奖励屏同时暴露 select_deck_card；只列 choose_reward_card，避免点错那套。"""
    state = {"screen": "CARD_SELECTION",
             "available_actions": ["choose_reward_card", "select_deck_card",
                                   "skip_reward_cards"],
             "selection": {"cards": [{"name": "旋风斩"}, {"name": "愤怒"}]},
             "reward": {"pending_card_choice": True}}
    assert [(e["a"], e["i"]) for e in actions.available(state)] == [
        ("choose_reward_card", 0), ("choose_reward_card", 1),
        ("skip_reward_cards", None)]


# A10 第一幕零精英：路线计划把结论写进 policy，菜单/校验都据此拦人。
MAP_PLAN_STATE = {
    "screen": "MAP",
    "available_actions": ["choose_map_node"],
    "map": {
        "nodes": [
            {"row": 0, "col": 0, "node_type": "Monster", "is_current": True,
             "children": [{"row": 1, "col": 0, "node_type": "Elite"},
                          {"row": 1, "col": 1, "node_type": "Monster"}]},
            {"row": 1, "col": 0, "node_type": "Elite", "is_available": True},
            {"row": 1, "col": 1, "node_type": "Monster", "is_available": True},
        ],
        "available_nodes": [
            {"index": 0, "row": 1, "col": 0, "node_type": "Elite"},
            {"index": 1, "row": 1, "col": 1, "node_type": "Monster"},
        ],
    },
    "route_plan": {
        "seq": "M M B",
        "path": [[0, 0, "Monster"], [1, 1, "Monster"], [2, 2, "Boss"]],
        "policy": {"elite_ok": False, "why": "Act1 不碰精英（A10 开局硬规则）"},
    },
}


def test_map_menu_marks_planned_node_and_bans_act1_elite():
    menu = actions.available(MAP_PLAN_STATE)
    assert menu[0]["ban"] is True and "Act1 不碰精英" in menu[0]["why"]
    assert menu[1].get("plan") is True
    # 精英项仍列出（否则整屏无出口时模型会乱点），但校验会拦。
    entry, err = actions.check({"act": "choose_map_node", "i": 0}, MAP_PLAN_STATE)
    assert entry is None and "Act1 不碰精英" in err
    entry, err = actions.check({"act": "choose_map_node", "i": 1}, MAP_PLAN_STATE)
    assert entry is not None and err == ""


def test_map_menu_allows_explicit_off_route_override():
    entry, err = actions.check(
        {"act": "choose_map_node", "i": 0, "allow_off_route": 1}, MAP_PLAN_STATE)
    assert entry is not None and err == ""


def test_map_menu_unbans_when_every_choice_is_elite():
    """单线地图可选点全是精英：不能把整屏都封死，否则模型无路可走。"""
    st = {"screen": "MAP", "available_actions": ["choose_map_node"],
          "map": {"nodes": [{"row": 1, "col": 0, "node_type": "Elite",
                             "is_available": True}],
                  "available_nodes": [{"index": 0, "row": 1, "col": 0,
                                       "node_type": "Elite"}]},
          "route_plan": {"path": [[0, 0, "Monster"], [1, 0, "Elite"]],
                         "policy": {"elite_ok": False, "forced": True,
                                    "why": "Act1 不碰精英"}}}
    menu = actions.available(st)
    assert menu and not menu[0].get("ban") and menu[0].get("forced") is True
    entry, err = actions.check({"act": "choose_map_node", "i": 0}, st)
    assert entry is not None and err == ""


def test_map_menu_blocks_off_plan_node_unless_override():
    st = {"screen": "MAP", "available_actions": ["choose_map_node"],
          "map": {"nodes": [
              {"row": 1, "col": 0, "node_type": "Monster", "is_available": True},
              {"row": 1, "col": 1, "node_type": "Monster", "is_available": True}],
              "available_nodes": [
                  {"index": 0, "row": 1, "col": 0, "node_type": "Monster"},
                  {"index": 1, "row": 1, "col": 1, "node_type": "Monster"}]},
          "route_plan": {"path": [[1, 1, "Monster"], [2, 2, "Boss"]],
                         "policy": {"elite_ok": False, "why": "Act1"}}}
    entry, err = actions.check({"act": "choose_map_node", "i": 0}, st)
    assert entry is None and "allow_off_route" in err
    entry, err = actions.check(
        {"act": "choose_map_node", "i": 0, "allow_off_route": 1}, st)
    assert entry is not None and err == ""
