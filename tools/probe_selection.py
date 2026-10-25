# -*- coding: utf-8 -*-
"""打印当前 /state 里 selection 的原始字段（用于校准多选渲染）。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sts2

s = sts2.get_state()
print("screen:", s.get("screen"))
print("legal:", s.get("available_actions"))
sel = s.get("selection")
print("selection keys:", list(sel.keys()) if isinstance(sel, dict) else sel)
if isinstance(sel, dict):
    for k, v in sel.items():
        if k == "cards":
            print("  cards: n=", len(v or []))
            for i, c in enumerate((v or [])[:3]):
                print("   ", i, json.dumps(c, ensure_ascii=False)[:400])
        else:
            print(" ", k, "=", json.dumps(v, ensure_ascii=False)[:300])
sys.exit(0)
