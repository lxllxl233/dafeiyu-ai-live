# -*- coding: utf-8 -*-
"""从主菜单/角色选择直接开一局，并打印涅奥选项。

    python tools/run_start.py                    # 用控制台「开局设置」里的角色/难度
    python tools/run_start.py --dry              # 只看能不能开
    python tools/run_start.py --character DEFECT --ascension 5

默认值来自 ``config.json`` 的 ``start_options["slay-the-spire-2"]``（控制台「开局设置」
保存的那份），没有就退回 game.json 的默认（铁甲 + A10）。

流程：GAME_OVER → 回主菜单 → open_character_select → 切换角色、调进阶 → embark →
打印 ctx（涅奥选项）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import sts2  # noqa: E402

GAME_ID = "slay-the-spire-2"
MANIFEST = Path(ROOT) / "games" / GAME_ID / "game.json"


def _screen(st):
    return (st.get("screen") or "").upper()


def _cs(st):
    return st.get("character_select") or {}


def _acts(st) -> list[str]:
    raw = st.get("available_actions") or []
    return [a if isinstance(a, str) else str(a.get("name") or "") for a in raw]


def _saved_options() -> dict:
    """控制台保存的开局设置；读不到就用 game.json 默认值。"""
    from dafeiyu.games import start_options

    try:
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return start_options.load_values(GAME_ID, start_options.spec_from_manifest(raw))


def _set_ascension(st, target: int) -> dict:
    asc = int(_cs(st).get("ascension") or 0)
    mx = int(_cs(st).get("max_ascension") or 0)
    target = max(0, int(target or 0))
    if mx > 0:
        target = min(target, mx)
    action = "increase_ascension" if asc < target else "decrease_ascension"
    for _ in range(abs(target - asc) + 1):
        if asc == target or action not in _acts(st):
            break
        sts2.act(action)
        st = sts2.get_state()
        new_asc = int(_cs(st).get("ascension") or 0)
        if new_asc == asc:
            print("!! %s 没反应，停在 A%d" % (action, asc))
            break
        asc = new_asc
        action = "increase_ascension" if asc < target else "decrease_ascension"
    print("ascension ->", asc, "(target", target, ")")
    return st


def main():
    saved = _saved_options()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只看能不能开")
    ap.add_argument("--character", default=str(saved.get("character") or "IRONCLAD"))
    ap.add_argument("--ascension", type=int,
                    default=int(saved.get("ascension") or 0))
    args = ap.parse_args()
    want_char = str(args.character or "").strip().upper()
    print("start options:", want_char, "A%d" % args.ascension)

    st = sts2.get_state()
    sc = _screen(st)
    print("start screen:", sc)

    if sc == "GAME_OVER":
        acts = _acts(st)
        print("game_over actions:", acts)
        for name in ("return_to_main_menu", "return_to_menu", "proceed",
                     "continue_after_defeat", "open_main_menu", "main_menu"):
            if name in acts:
                print("-> do", name)
                if not args.dry:
                    sts2.act(name)
                break
        else:
            print("!! 不知道哪个动作能回主菜单，手动看一眼:", acts)
            return 1
        st = sts2.get_state()
        sc = _screen(st)
        print("after:", sc)

    if sc == "MAIN_MENU":
        print("-> open_character_select")
        if not args.dry:
            sts2.act("open_character_select")
            st = sts2.get_state()
        sc = _screen(st)

    if sc != "CHARACTER_SELECT":
        print("!! 当前不是角色选择界面:", sc)
        return 1

    cs = _cs(st)
    cur = str(cs.get("selected_character_id") or "")
    print("selected:", cur, "ascension:", int(cs.get("ascension") or 0),
          "/", int(cs.get("max_ascension") or 0))
    if args.dry:
        print("dry ok")
        return 0

    if cur.upper() != want_char:
        chars = cs.get("characters") or []
        idx = next((c.get("index") for c in chars
                    if str(c.get("character_id") or "").upper() == want_char), None)
        if idx is None:
            print("!! 找不到角色", want_char, "（没解锁？）继续用", cur)
        else:
            print("-> select_character", idx)
            sts2.act("select_character", option_index=int(idx))
            st = sts2.get_state()

    st = _set_ascension(st, args.ascension)
    print("-> embark (%s A%s)" % (_cs(st).get("selected_character_id"),
                                  _cs(st).get("ascension")))
    out = sts2.act("embark")
    print("embark:", json.dumps(out, ensure_ascii=False)[:200])
    st = sts2.get_state()
    print("now:", _screen(st), "run", (st.get("run") or {}).get("run_id"))
    print(sts2.compact(st, brief=True))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
