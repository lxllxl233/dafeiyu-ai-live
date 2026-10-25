# -*- coding: utf-8 -*-
"""Verify the natural-speech bubble (no dash, whole sentences, varied phrasing)."""

import base64
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import obsctl

OVERLAY = "http://127.0.0.1:8730"

CASES = [
    (
        "四园丁 120 血，我最大格挡 16-21，清场要 12 回合而血量只够 3 回合",
        "这不叫战斗，这叫一道已经批改完的数学题。",
        "A",
    ),
    (
        "先痛击挂易伤，再让打击吃 1.5 倍",
        "8 + 9 = 17 打 14 血，多出来的 3 点是精神损失费",
        "B",
    ),
    (
        "手里只剩防御牌，对面还要打 14",
        "我不是在战斗，我是在给对面当沙袋，还是自带盾牌那种。",
        "C",
    ),
    (
        "血线 29%，下一个节点只有精英一个出口",
        "地图把我的选择权画得很清楚：一条路，一个坑。",
        "D",
    ),
]


def post(thought, meme):
    payload = json.dumps(
        {"tag": "战斗", "thought": thought, "meme": meme, "floor": 8, "hp": 23, "max_hp": 80, "gold": 20, "screen": "COMBAT"},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(OVERLAY + "/comment", data=payload, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


urllib.request.urlopen(urllib.request.Request(OVERLAY + "/reset", data=b"{}", method="POST"), timeout=5).read()

cfg = obsctl.load_ws_config()
o = obsctl.Obs(cfg["url"], cfg.get("password", ""))
o.connect()
try:
    o.request("SetSceneItemEnabled", {"sceneName": "StS2", "sceneItemId": 4, "sceneItemEnabled": False})
    time.sleep(0.8)
    for thought, meme, name in CASES:
        post(thought, meme)
        time.sleep(1.1)
        resp = o.request(
            "GetSourceScreenshot",
            {"sourceName": "StS2", "imageFormat": "png", "imageWidth": 1920, "imageHeight": 1080},
        )
        with open(f"preview_speech_{name}.png", "wb") as f:
            f.write(base64.b64decode(resp["imageData"].split(",", 1)[-1]))
        print("wrote", f"preview_speech_{name}.png")
finally:
    o.request("SetSceneItemEnabled", {"sceneName": "StS2", "sceneItemId": 4, "sceneItemEnabled": True})
    o.close()
