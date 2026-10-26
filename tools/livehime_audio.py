# -*- coding: utf-8 -*-
"""给直播姬加「游戏进程音频」+「曼波语音（浏览器源，隐藏但出声）」两路音频，
并把桌面音频、麦克风全部静音：直播里只留游戏声和曼波配音。

用法：直播姬关闭时执行 python tools/livehime_audio.py
"""
import io
import json
import os
import shutil
import sys
import time
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SCENE = r"C:\Users\22766\AppData\Local\bililive\User Data\396079693\Scene Collection\support_group_collection.json"
CUR_SCENE = "Scene 4_场景7"
GAME_WINDOW = "Slay the Spire 2:Engine:SlayTheSpire2.exe"
OVERLAY_URL = "http://127.0.0.1:8730/?live=1"
GAME_SRC = "StS2 游戏音频"
VOICE_SRC = "曼波配音"


def new_uuid():
    return str(uuid.uuid4())


def audio_source(name, sid, versioned_id, settings):
    return {
        "prev_ver": 536870916,
        "name": name,
        "uuid": new_uuid(),
        "id": sid,
        "versioned_id": versioned_id,
        "settings": settings,
        "mixers": 255,
        "sync": 0,
        "flags": 0,
        "volume": 1.0,
        "balance": 0.5,
        "enabled": True,
        "muted": False,
        "push-to-mute": False,
        "push-to-mute-delay": 0,
        "push-to-talk": False,
        "push-to-talk-delay": 0,
        "hotkeys": {
            "libobs.mute": [],
            "libobs.unmute": [],
            "libobs.push-to-mute": [],
            "libobs.push-to-talk": [],
        },
        "deinterlace_mode": 0,
        "deinterlace_field_order": 0,
        "monitoring_type": 0,
        "private_settings": {},
        "filters": [],
    }


def scene_item(name, source_uuid, ident, visible, cx=1920.0, cy=1080.0):
    return {
        "name": name,
        "source_uuid": source_uuid,
        "visible": visible,
        "locked": False,
        "rot": 0.0,
        "scale_ref": {"x": cx, "y": cy},
        "align": 5,
        "bounds_type": 0,
        "bounds_align": 0,
        "bounds_crop": False,
        "crop_left": 0,
        "crop_top": 0,
        "crop_right": 0,
        "crop_bottom": 0,
        "id": ident,
        "group_item_backup": False,
        "pos": {"x": 0.0, "y": 0.0},
        "scale": {"x": 1.0, "y": 1.0},
        "bounds": {"x": 0.0, "y": 0.0},
        "scale_filter": "disable",
        "blend_method": "default",
        "blend_type": "normal",
        "show_transition": {"duration": 0},
        "hide_transition": {"duration": 0},
        "private_settings": {},
    }


def main():
    if not os.path.exists(SCENE):
        raise SystemExit(f"scene file not found: {SCENE}")
    bak = SCENE + time.strftime(".bak_%Y%m%d_%H%M%S")
    shutil.copy2(SCENE, bak)
    print("backup:", bak)

    with io.open(SCENE, encoding="utf-8-sig") as f:
        d = json.load(f)

    # 1) 全局桌面音频 / 麦克风静音
    for key in ("Desktop Audio", "Mic/Aux"):
        if key in d:
            d[key]["muted"] = True
            print(f"mute {key}")

    sources = d["sources"]
    by_name = {s["name"]: s for s in sources}

    # 2) 场景里那个「额外音频输入设备」（麦克风）直接静音
    if "额外音频输入设备" in by_name:
        by_name["额外音频输入设备"]["muted"] = True
        print("mute 额外音频输入设备")

    # 3) 删掉旧的同名源，避免重复
    for nm in (GAME_SRC, VOICE_SRC):
        if nm in by_name:
            sources.remove(by_name[nm])
            print("replace existing source:", nm)
    by_name = {s["name"]: s for s in sources}

    # 4) 游戏进程音频（wasapi_process_output_capture，按进程抓，不混系统声）
    game = audio_source(
        GAME_SRC,
        "wasapi_process_output_capture",
        "wasapi_process_output_capture",
        {"window": GAME_WINDOW, "priority": 2},
    )
    # 5) 曼波语音：浏览器源加载 overlay 页面，页面自己调 /tts 播 WebAudio
    voice = audio_source(
        VOICE_SRC,
        "browser_source",
        "browser_source",
        {
            "url": OVERLAY_URL,
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "fps_custom": False,
            "shutdown": False,
            "restart_when_active": False,
            "css": "body{background:transparent;}",
            "reroute_audio": False,
        },
    )
    sources.append(game)
    sources.append(voice)

    # 6) 挂进当前场景：游戏音频出声、曼波源隐藏（不叠画面，但浏览器的声音照常输出）
    cur = by_name.get(CUR_SCENE)
    if not cur:
        raise SystemExit(f"current scene not found: {CUR_SCENE}")
    st = cur["settings"]
    items = st.setdefault("items", [])
    existing = {i["name"] for i in items}
    ident = max([i.get("id", 0) for i in items] + [0]) + 1
    if GAME_SRC not in existing:
        items.append(scene_item(GAME_SRC, game["uuid"], ident, True))
        ident += 1
    if VOICE_SRC not in existing:
        # 留在场景里但挪到画布外：浏览器源照常渲染出声，画面不会叠进直播
        vi = scene_item(VOICE_SRC, voice["uuid"], ident, True)
        vi["pos"] = {"x": 4000.0, "y": 4000.0}
        items.append(vi)
    st["id_counter"] = max(st.get("id_counter", 0), ident)

    with io.open(SCENE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))

    # 校验
    with io.open(SCENE, encoding="utf-8-sig") as f:
        chk = json.load(f)
    print("Desktop muted:", chk["Desktop Audio"]["muted"])
    print("Mic aux muted:", chk["Mic/Aux"]["muted"])
    for s in chk["sources"]:
        if s["name"] in (GAME_SRC, VOICE_SRC):
            print("source:", s["name"], "|", s["id"], "| muted", s["muted"], "| settings", json.dumps(s["settings"], ensure_ascii=False))
    print("scene items:", [(i["name"], i["visible"]) for i in chk["sources"][[s["name"] for s in chk["sources"]].index(CUR_SCENE)]["settings"]["items"]])


if __name__ == "__main__":
    main()
