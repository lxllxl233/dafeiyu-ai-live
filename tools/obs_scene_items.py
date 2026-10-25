# -*- coding: utf-8 -*-
"""List scene items of the OBS scenes so we can spot duplicated overlay sources."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import obsctl

cfg = obsctl.load_ws_config()
o = obsctl.Obs(cfg["url"], cfg.get("password", ""))
o.connect()

scenes = o.request("GetSceneList")
print("current:", scenes.get("currentProgramSceneName"))
for sc in scenes.get("scenes", []):
    name = sc.get("sceneName")
    print("== scene:", name)
    try:
        items = o.request("GetSceneItemList", {"sceneName": name})
    except Exception as e:
        print("   err", e)
        continue
    for it in items.get("sceneItems", []):
        print(
            "   id=%s src=%s enabled=%s"
            % (it.get("sceneItemId"), it.get("sourceName"), it.get("sceneItemEnabled"))
        )
o.close()
