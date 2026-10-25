# -*- coding: utf-8 -*-
"""Isolate which source paints the stale/duplicated overlay: toggle a scene item and shoot."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import obsctl

cfg = obsctl.load_ws_config()
o = obsctl.Obs(cfg["url"], cfg.get("password", ""))
o.connect()


def shot(path):
    resp = o.request(
        "GetSourceScreenshot",
        {"sourceName": "StS2", "imageFormat": "png", "imageWidth": 1920, "imageHeight": 1080},
    )
    import base64

    data = resp["imageData"].split(",", 1)[-1]
    with open(path, "wb") as f:
        f.write(base64.b64decode(data))
    print("wrote", path)


def set_item(item_id, enabled):
    o.request("SetSceneItemEnabled", {"sceneName": "StS2", "sceneItemId": item_id, "sceneItemEnabled": enabled})
    print("item", item_id, "enabled=", enabled)


# 1) 关掉显示器采集，只留浏览器源
set_item(4, False)
time.sleep(1.0)
shot("preview_overlay_only.png")

# 2) 恢复
set_item(4, True)
time.sleep(0.5)

# 3) 看一下各源的输入类型，判断谁在抓屏
for name in ("StS2 Monitor", "StS2 Game", "StS2 Window", "AI Commentary"):
    try:
        r = o.request("GetInputSettings", {"inputName": name})
        print(name, "->", r.get("inputKind"), str(r.get("inputSettings"))[:160])
    except Exception as e:
        print(name, "err", e)

o.close()
