# -*- coding: utf-8 -*-
"""Verify the overlay shows exactly one comment: post several, screenshot after each."""

import base64
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import obsctl

OVERLAY = "http://127.0.0.1:8730"


def post(thought, meme, hp=23, gold=20, floor=8):
    payload = json.dumps(
        {"tag": "战斗", "thought": thought, "meme": meme, "floor": floor, "hp": hp, "max_hp": 80, "gold": gold, "screen": "COMBAT"},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(OVERLAY + "/comment", data=payload, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def reset():
    req = urllib.request.Request(OVERLAY + "/reset", data=b"{}", method="POST")
    urllib.request.urlopen(req, timeout=5).read()


cfg = obsctl.load_ws_config()
o = obsctl.Obs(cfg["url"], cfg.get("password", ""))
o.connect()


def shot(path):
    resp = o.request(
        "GetSourceScreenshot",
        {"sourceName": "StS2", "imageFormat": "png", "imageWidth": 1920, "imageHeight": 1080},
    )
    with open(path, "wb") as f:
        f.write(base64.b64decode(resp["imageData"].split(",", 1)[-1]))
    print("wrote", path)


try:
    reset()
    # 只留浏览器源，避免显示器采集里那个「暂停的视频」干扰判断
    o.request("SetSceneItemEnabled", {"sceneName": "StS2", "sceneItemId": 4, "sceneItemEnabled": False})
    time.sleep(0.8)

    post("A 短文本：先挡满再输出。", "A 的吐槽：挡 10 漏 4。")
    time.sleep(1.2)
    shot("preview_single_A.png")

    post(
        "B 长文本：四园丁 120 血、每只首次命中 +7 格挡，我最大格挡 16-21，每回合入伤 24-30，需要 12 回合清场而血量只够 3 回合。",
        "B 的吐槽：这不是战斗，这是一道已经批改完的数学题。",
    )
    time.sleep(1.2)
    shot("preview_single_B.png")

    post("C 中等文本：进精英前算清场回合数。", "C 的吐槽：数学不会骗人，我会。")
    time.sleep(1.2)
    shot("preview_single_C.png")
finally:
    o.request("SetSceneItemEnabled", {"sceneName": "StS2", "sceneItemId": 4, "sceneItemEnabled": True})
    o.close()
    print("monitor capture re-enabled")
