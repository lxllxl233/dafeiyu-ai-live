# -*- coding: utf-8 -*-
"""Reload the OBS browser source (cache-busted) so overlay/index.html changes show.

Usage:
  python tools/reload_overlay.py                # reload (animalese on, default volume)
  python tools/reload_overlay.py --sfx 0        # reload with animalese off
  python tools/reload_overlay.py --vol 0.8      # reload with animalese volume 0.8
  python tools/reload_overlay.py --post         # reload and post one test comment
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import obsctl

args = sys.argv[1:]


def opt(name):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return None


query = "?v=%d" % int(time.time())
sfx = opt("--sfx")
vol = opt("--vol")
if sfx is not None:
    query += "&sfx=" + sfx
if vol is not None:
    query += "&vol=" + vol
url = "http://127.0.0.1:8730/" + query

cfg = obsctl.load_ws_config()
o = obsctl.Obs(cfg["url"], cfg.get("password", ""))
o.connect()
o.request(
    "SetInputSettings",
    {"inputName": "AI Commentary", "inputSettings": {"url": url}, "overlay": True},
)
o.request("SetInputAudioTracks", {"inputName": "AI Commentary", "inputAudioTracks": {"1": True}})
o.close()
print("browser source reloaded:", url)

if "--post" in args:
    payload = json.dumps(
        {
            "tag": "战斗",
            "thought": "入场 14：耸肩 8 + 防御 5 = 13，差 1 点用痛击补易伤，下回合再收。",
            "meme": "它 14 血，我先活下去，再谈数学。",
            "floor": 8,
            "hp": 23,
            "max_hp": 80,
            "gold": 20,
            "screen": "COMBAT",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8730/comment", data=payload, headers={"Content-Type": "application/json"}
    )
    print(urllib.request.urlopen(req, timeout=5).read().decode("utf-8"))
