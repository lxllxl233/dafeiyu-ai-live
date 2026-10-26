# -*- coding: utf-8 -*-
"""从主菜单/角色选择直接开一局铁甲战士 A10，并打印涅奥选项。

    python tools/run_start.py            # 自动开跑
    python tools/run_start.py --dry      # 只看能不能开

流程：GAME_OVER → 回主菜单 → open_character_select → 确认 IRONCLAD + A10 →
embark → 打印 ctx（涅奥选项）。
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import sts2  # noqa: E402


def _screen(st):
    return (st.get("screen") or "").upper()


def _cs(st):
    return st.get("character_select") or {}


def main():
    dry = "--dry" in sys.argv
    st = sts2.get_state()
    sc = _screen(st)
    print("start screen:", sc)

    if sc == "GAME_OVER":
        acts = [a.get("name") for a in (st.get("available_actions") or [])]
        print("game_over actions:", acts)
        for name in ("return_to_menu", "proceed", "continue_after_defeat", "open_main_menu"):
            if name in acts:
                print("-> do", name)
                if not dry:
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
        if not dry:
            sts2.act("open_character_select")
            st = sts2.get_state()
        sc = _screen(st)

    if sc != "CHARACTER_SELECT":
        print("!! 当前不是角色选择界面:", sc)
        return 1

    cs = _cs(st)
    cur = str(cs.get("selected_character_id") or "")
    asc = int(cs.get("ascension") or 0)
    mx = int(cs.get("max_ascension") or 0)
    print("selected:", cur, "ascension:", asc, "/", mx)
    if dry:
        print("dry ok")
        return 0

    if asc < mx:
        for _ in range(asc, mx):
            sts2.act("increase_ascension")
            st = sts2.get_state()
        asc = int(_cs(st).get("ascension") or 0)
        print("ascension ->", asc)

    cur = str(_cs(st).get("selected_character_id") or "")
    if cur != "IRONCLAD":
        chars = _cs(st).get("characters") or []
        idx = next((c["index"] for c in chars if c.get("character_id") == "IRONCLAD"), None)
        if idx is None:
            print("!! 找不到 IRONCLAD")
            return 1
        print("-> select_character", idx)
        sts2.act("select_character", option_index=idx)
        st = sts2.get_state()

    print("-> embark (ascension %s)" % _cs(st).get("ascension"))
    out = sts2.act("embark")
    print("embark:", json.dumps(out, ensure_ascii=False)[:200])
    st = sts2.get_state()
    print("now:", _screen(st), "run", (st.get("run") or {}).get("run_id"))
    print(sts2.compact(st, brief=True))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
