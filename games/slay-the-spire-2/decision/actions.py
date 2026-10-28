"""非战斗决策的动作层：把「LLM 想点哪个」变成一次可校验的点击。

LLM 只输出 ``{"say": ["…"], "act": "choose_event_option", "i": 0}``，
本模块负责三件事：

1. ``available(state)``：当前屏幕真正能点的菜单（下标与 ctx 的 ``choice.*[].i`` 一致，
   已经被禁用的选项不会进菜单）；
2. ``check(plan, state)``：菜单里找不到、下标越界、买不起 → 返回原因，绝不硬点；
3. ``execute(sts2, entry)``：只调 ``sts2.act``，不碰 `play_card` 之类的战斗动作。

白名单之外的 action（`save_and_quit`、`open_timeline`、`use_potion` 等）永远不进菜单，
模型就算写了也会被 ``check`` 拒掉，非战斗决策因此不会越过战斗/停手边界。
"""

from __future__ import annotations


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _acts(state: dict) -> set[str]:
    out = set()
    for a in state.get("available_actions") or []:
        if isinstance(a, str):
            out.add(a)
        elif isinstance(a, dict):
            name = a.get("name") or a.get("action")
            if name:
                out.add(str(name))
    return out


def _short(value, limit: int = 40) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _run(state: dict) -> dict:
    return state.get("run") or {}


# ---------------------------------------------------------------- 菜单
def _map_menu(state: dict) -> list[dict]:
    if "choose_map_node" not in _acts(state):
        return []
    nodes = [n for n in ((state.get("map") or {}).get("nodes") or [])
             if isinstance(n, dict) and n.get("is_available")]
    out = []
    for i, n in enumerate(nodes):
        nxt = ",".join(str((c or {}).get("node_type") or "?")
                       for c in (n.get("children") or []) if isinstance(c, dict))
        out.append({"a": "choose_map_node", "i": i,
                    "n": f"{n.get('node_type') or '?'} ({n.get('row')},{n.get('col')})",
                    "fx": f"后续：{nxt}" if nxt else ""})
    return out


def _event_menu(state: dict) -> list[dict]:
    if "choose_event_option" not in _acts(state):
        return []
    out = []
    for i, o in enumerate((state.get("event") or {}).get("options") or []):
        if not isinstance(o, dict) or o.get("is_locked"):
            continue
        out.append({"a": "choose_event_option", "i": i,
                    "n": _short(o.get("title") or o.get("text") or "?", 30),
                    "fx": _short(o.get("description") or "", 90)})
    return out


def _rest_menu(state: dict) -> list[dict]:
    acts = _acts(state)
    opts = [o for o in ((state.get("rest") or {}).get("options") or []) if isinstance(o, dict)]
    out = []
    for i, o in enumerate(opts):
        if not o.get("is_enabled", o.get("enabled", True)):
            continue
        out.append({"a": "choose_rest_option", "i": i,
                    "n": _short(o.get("title") or o.get("name") or "?", 24),
                    "fx": _short(o.get("description") or "", 60)})
    if not opts and "proceed" in acts:
        out.append({"a": "proceed", "i": None, "n": "继续", "fx": ""})
    return out


def _reward_menu(state: dict) -> list[dict]:
    acts = _acts(state)
    rw = state.get("reward") or {}
    out = []
    for i, r in enumerate([x for x in (rw.get("rewards") or []) if isinstance(x, dict)]):
        out.append({"a": "claim_reward", "i": i,
                    "n": _short(r.get("name") or r.get("reward_type") or "奖励", 30),
                    "fx": _short(r.get("description") or "", 60)})
    cards = [x for x in (rw.get("card_options") or []) if isinstance(x, dict)]
    for i, c in enumerate(cards):
        out.append({"a": "choose_reward_card", "i": i,
                    "n": _short(c.get("name") or c.get("card_id") or "?", 24),
                    "fx": _short(c.get("resolved_rules_text") or c.get("rules_text") or "", 80)})
    if cards and "skip_reward_cards" in acts:
        out.append({"a": "skip_reward_cards", "i": None, "n": "跳过选牌", "fx": ""})
    if "collect_rewards_and_proceed" in acts:
        out.append({"a": "collect_rewards_and_proceed", "i": None,
                    "n": "收下全部并继续", "fx": ""})
    elif "proceed" in acts:
        out.append({"a": "proceed", "i": None, "n": "继续", "fx": ""})
    return out


def _shop_menu(state: dict) -> list[dict]:
    acts = _acts(state)
    sh = state.get("shop") or {}
    gold = _int(_run(state).get("gold"), 0)
    out = []
    if "open_shop_inventory" in acts:
        out.append({"a": "open_shop_inventory", "i": None, "n": "看货架", "fx": ""})
    # 货架打开时游戏只给 close_shop_inventory，不给 leave_shop；不列这一项，
    # 模型和本地兜底都没有出口，商店就卡死了（实机 run-0111）。
    if "close_shop_inventory" in acts:
        out.append({"a": "close_shop_inventory", "i": None, "n": "关闭货架", "fx": ""})
    for sec, act in (("cards", "buy_card"), ("relics", "buy_relic"),
                     ("potions", "buy_potion")):
        if act not in acts:
            continue
        for i, it in enumerate([x for x in (sh.get(sec) or []) if isinstance(x, dict)]):
            if not it.get("available", True):
                continue
            price = _int(it.get("price"), 0)
            out.append({"a": act, "i": i,
                        "n": f"{_short(it.get('name') or '?', 24)} {price}金",
                        "fx": _short(it.get("description") or "", 70),
                        "ok": price <= gold})
    if "leave_shop" in acts:
        out.append({"a": "leave_shop", "i": None, "n": "离开商店", "fx": ""})
    elif "proceed" in acts:
        out.append({"a": "proceed", "i": None, "n": "继续", "fx": ""})
    return out


def _chest_menu(state: dict) -> list[dict]:
    acts = _acts(state)
    ch = state.get("chest") or {}
    out = []
    if "open_chest" in acts:
        out.append({"a": "open_chest", "i": None, "n": "打开宝箱", "fx": ""})
    if "choose_treasure_relic" in acts:
        for i, r in enumerate([x for x in (ch.get("relic_options") or [])
                               if isinstance(x, dict)]):
            out.append({"a": "choose_treasure_relic", "i": i,
                        "n": _short(r.get("name") or "?", 24),
                        "fx": _short(r.get("description") or "", 70)})
    if "proceed" in acts:
        out.append({"a": "proceed", "i": None, "n": "继续", "fx": ""})
    return out


def _select_menu(state: dict) -> list[dict]:
    acts = _acts(state)
    sel = state.get("selection") or {}
    out = []
    if "select_deck_card" in acts:
        for i, c in enumerate([x for x in (sel.get("cards") or []) if isinstance(x, dict)]):
            if c.get("is_selected") or c.get("selected"):
                continue
            out.append({"a": "select_deck_card", "i": i,
                        "n": _short(c.get("name") or c.get("card_id") or "?", 24),
                        "fx": _short(c.get("resolved_rules_text") or c.get("rules_text") or "", 70)})
    if "confirm_selection" in acts:
        out.append({"a": "confirm_selection", "i": None, "n": "确认", "fx": ""})
    return out


_BUILDERS = {
    "MAP": _map_menu,
    "EVENT": _event_menu,
    "REST": _rest_menu,
    "REWARD": _reward_menu,
    "SHOP": _shop_menu,
    "CHEST": _chest_menu,
    "CARD_SELECTION": _select_menu,
}


def available(state: dict) -> list[dict]:
    """当前屏幕可点的动作菜单；LOCAL_SCREENS 之外返回空。"""
    screen = str(state.get("screen") or "").upper()
    builder = _BUILDERS.get(screen)
    if builder is None:
        return []
    return [e for e in builder(state) if e.get("ok", True)]


# ---------------------------------------------------------------- 校验/执行
def _menu_names(menu: list[dict], limit: int = 8) -> str:
    names = []
    for e in menu[:limit]:
        names.append(str(e.get("n") or e["a"]))
    return "、".join(names) or "（空）"


def check(plan: dict, state: dict) -> tuple[dict | None, str]:
    """把模型的 plan 对到菜单项；返回 (entry, err)。err 非空表示不可执行。"""
    if not isinstance(plan, dict):
        return None, "plan 不是 JSON 对象"
    act = str(plan.get("act") or "").strip()
    if not act:
        return None, "缺 act 字段"
    menu = available(state)
    if not menu:
        return None, "当前屏幕没有可执行动作"
    raw = plan.get("i", plan.get("index"))
    if raw is None:
        for entry in menu:
            if entry["a"] == act and entry["i"] is None:
                return entry, ""
        return None, f"{act} 不能这么点；可选：{_menu_names(menu)}"
    try:
        idx = int(raw)
    except (TypeError, ValueError):
        return None, "i 必须是整数下标"
    for entry in menu:
        if entry["a"] == act and entry["i"] == idx:
            return entry, ""
    return None, f"{act}[{idx}] 不在菜单里；可选：{_menu_names(menu)}"


def execute(sts2, entry: dict) -> dict:
    """执行菜单项；只走 sts2.act，动作名来自白名单。"""
    if entry.get("i") is None:
        return sts2.act(entry["a"])
    return sts2.act(entry["a"], option_index=int(entry["i"]))


def label(entry: dict) -> str:
    """写日志/status 用的短标签。"""
    idx = "" if entry.get("i") is None else f" {entry['i']}"
    return f"{entry['a']}{idx}"


__all__ = ["available", "check", "execute", "label"]
