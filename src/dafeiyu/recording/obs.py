#!/usr/bin/env python3
"""Minimal OBS WebSocket v5 controller for recording / scene setup.

Usage examples (from repo root):
  python main.py obs status
  python main.py obs setup          # create scene + game capture + audio + ticker
  python main.py obs shot preview.png
  python main.py obs start
  python main.py obs stop
  python main.py obs ticker "text"
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass

from websockets.sync.client import connect

from .. import paths

# 布局：src/dafeiyu/recording/obs.py
BASE = os.fspath(paths.ROOT)
CONFIG_PATH = os.path.join(BASE, "obs_config.json")
DEFAULT_WS = "ws://127.0.0.1:4455"
OBS_WS_CONFIG = os.path.join(
    os.environ.get("APPDATA", ""), "obs-studio", "plugin_config", "obs-websocket", "config.json"
)


def load_ws_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
            if cfg.get("password"):
                return cfg
    # Fall back to OBS's own config (password is stored in plain text there).
    if os.path.exists(OBS_WS_CONFIG):
        with open(OBS_WS_CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        return {
            "url": f"ws://127.0.0.1:{cfg.get('server_port', 4455)}",
            "password": cfg.get("server_password", ""),
        }
    return {"url": DEFAULT_WS, "password": ""}


class ObsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ObsProfile:
    """OBS 场景/采集源命名。游戏专属值放 ``games/<id>/game-skill/obs.json``。"""

    scene: str = "Game"
    game_source: str = "Game Capture"
    window_source: str = "Game Window"
    monitor_source: str = "Game Monitor"
    audio_source: str = "Game Audio"
    overlay_source: str = "AI Commentary"
    ticker_source: str = "AI Ticker"
    ticker_text: str = "AI 实况"
    window_match: tuple[str, ...] = ()
    window_fallback: str = ""
    # 采集方式：game（游戏 hook）/ monitor（整块副屏）/ window（单个窗口）。
    # B 站云观影默认用 monitor，因为 Chrome 视频页不是一个 game_capture 目标。
    capture_mode: str = "game"
    monitor_index: int = -1          # -1 = 优先 1920x1080；0/1 = 第几个显示器
    # 非空时 obs setup 会创建/更新这个浏览器源；场景页自己负责画面排版。
    overlay_url: str = ""
    overlay_width: int = 1920
    overlay_height: int = 1080
    # B 站切视频会换 Chrome 窗口，按窗口抓音频会跟丢；B 场景用桌面音频。
    audio_mode: str = "process"       # process | desktop

    @classmethod
    def from_dict(cls, raw: dict) -> "ObsProfile":
        values = {}
        int_fields = {"monitor_index", "overlay_width", "overlay_height"}
        for key in cls.__dataclass_fields__:
            if key not in raw:
                continue
            if key == "window_match":
                values[key] = tuple(raw[key])
            elif key in int_fields:
                try:
                    values[key] = int(raw[key])
                except (TypeError, ValueError):
                    pass
            else:
                values[key] = str(raw[key])
        return cls(**values)


def load_profile(game: str | None = None) -> ObsProfile:
    """读当前游戏的 ``game-skill/obs.json``；没有就返回通用命名。"""
    if not game:
        from ..core.config import LiveConfig

        game = LiveConfig.load().game
    path = paths.GAMES / str(game) / "game-skill" / "obs.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ObsProfile()
    return ObsProfile.from_dict(raw) if isinstance(raw, dict) else ObsProfile()


class Obs:
    def __init__(self, url: str, password: str, timeout: float = 10.0,
                 profile: ObsProfile | None = None):
        self.url = url
        self.password = password or ""
        self.timeout = timeout
        self.ws = None
        self._rid = 0
        self.p = profile or load_profile()

    def connect(self):
        self.ws = connect(
            self.url,
            open_timeout=self.timeout,
            close_timeout=2,
            max_size=32 * 1024 * 1024,
        )
        hello = json.loads(self.ws.recv(timeout=self.timeout))
        if hello.get("op") != 0:
            raise ObsError(f"unexpected hello: {hello}")
        auth = None
        auth_info = hello.get("d", {}).get("authentication")
        if auth_info:
            challenge = auth_info["challenge"]
            salt = auth_info["salt"]
            secret = base64.b64encode(
                hashlib.sha256((self.password + salt).encode("utf-8")).digest()
            ).decode("ascii")
            auth = base64.b64encode(
                hashlib.sha256((secret + challenge).encode("utf-8")).digest()
            ).decode("ascii")
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        if auth:
            ident["authentication"] = auth
        self.ws.send(json.dumps({"op": 1, "d": ident}))
        resp = json.loads(self.ws.recv(timeout=self.timeout))
        if resp.get("op") != 2:
            raise ObsError(f"identify failed: {resp}")
        return self

    def close(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None

    def request(self, request_type: str, data: dict | None = None, timeout: float | None = None):
        if not self.ws:
            self.connect()
        self._rid += 1
        rid = f"req-{self._rid}-{uuid.uuid4().hex[:6]}"
        msg = {"op": 6, "d": {"requestType": request_type, "requestId": rid}}
        if data is not None:
            msg["d"]["requestData"] = data
        self.ws.send(json.dumps(msg, ensure_ascii=False))
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            raw = self.ws.recv(timeout=max(0.1, deadline - time.time()))
            m = json.loads(raw)
            if m.get("op") != 7:
                continue
            d = m.get("d", {})
            if d.get("requestId") != rid:
                continue
            status = d.get("requestStatus", {})
            if not status.get("result"):
                raise ObsError(
                    f"{request_type} failed: code={status.get('code')} comment={status.get('comment')}"
                )
            return d.get("responseData", {})
        raise ObsError(f"{request_type} timed out")

    # ---- helpers ----
    def list_inputs(self):
        return self.request("GetInputList").get("inputs", [])

    def ensure_scene(self, name: str, *, activate: bool = True):
        try:
            self.request("CreateScene", {"sceneName": name})
        except ObsError as e:
            if "already exists" not in str(e).lower() and "1001" not in str(e):
                # Many OBS versions return 1001 resource exists; ignore that.
                pass
        if activate:
            self.request("SetCurrentProgramScene", {"sceneName": name})

    def get_scene_item_id(self, scene: str, source: str):
        return self.request(
            "GetSceneItemId", {"sceneName": scene, "sourceName": source}
        )["sceneItemId"]

    def ensure_game_capture(self, scene: str) -> dict:
        """Try game_capture; fall back to monitor/window capture. Returns chosen source info."""
        mode = str(self.p.capture_mode or "game").lower()
        if mode == "monitor":
            return self.ensure_monitor_capture(scene)
        if mode == "window":
            return self.ensure_window_capture(scene)
        source = self.p.game_source
        existing = {i["inputName"] for i in self.list_inputs()}
        if source not in existing:
            try:
                self.request(
                    "CreateInput",
                    {
                        "sceneName": scene,
                        "inputName": source,
                        "inputKind": "game_capture",
                        "inputSettings": {"capture_mode": "any_fullscreen"},
                        "sceneItemEnabled": True,
                    },
                )
            except ObsError as e:
                print("game_capture create failed:", e, file=sys.stderr)

        if source in {i["inputName"] for i in self.list_inputs()}:
            win = self.window_item(source)
            settings = {"capture_mode": "window"}
            settings["window"] = win["itemValue"] if win else self.p.window_fallback
            try:
                self.request(
                    "SetInputSettings",
                    {"inputName": source, "inputSettings": settings, "overlay": True},
                )
            except ObsError:
                pass
            try:
                item_id = self.get_scene_item_id(scene, source)
                self.request(
                    "SetSceneItemTransform",
                    {
                        "sceneName": scene,
                        "sceneItemId": item_id,
                        "sceneItemTransform": {
                            "positionX": 0,
                            "positionY": 0,
                            "scaleX": 1,
                            "scaleY": 1,
                        },
                    },
                )
            except ObsError:
                pass
            return {"kind": "game_capture", "source": source}

        # Fallback: monitor capture (prefer the 1920x1080 secondary display)
        return self.ensure_monitor_capture(scene)

    def _monitor_property(self) -> str:
        """OBS 29/30 的显示器列表属性叫 monitor_id，老版本叫 monitor。"""
        for name in ("monitor_id", "monitor"):
            try:
                self.request(
                    "GetInputPropertiesListPropertyItems",
                    {"inputName": self.p.monitor_source,
                     "propertyName": name},
                )
                return name
            except ObsError:
                continue
        return "monitor_id"

    def _monitor_item(self):
        prop = self._monitor_property()
        props = self.request(
            "GetInputPropertiesListPropertyItems",
            {"inputName": self.p.monitor_source, "propertyName": prop},
        ).get("propertyItems", [])
        # 第一项通常是「请选择要采集的显示器」的 DUMMY，不能选。
        valid = [x for x in props
                 if x.get("itemEnabled", True) and str(x.get("itemValue") or "") not in ("", "DUMMY")]
        if not valid:
            return None, prop
        idx = int(self.p.monitor_index)
        if 0 <= idx < len(valid):
            return valid[idx], prop
        for item in valid:
            name = str(item.get("itemName", ""))
            if "1920" in name and "1080" in name:
                return item, prop
        return valid[-1], prop

    def ensure_monitor_capture(self, scene: str) -> dict:
        """录一整块显示器；B 站云观影默认把 Chrome 放副屏，所以不走 game_capture。"""
        source = self.p.monitor_source
        existing = {i["inputName"] for i in self.list_inputs()}
        if source not in existing:
            self.request(
                "CreateInput",
                {
                    "sceneName": scene,
                    "inputName": source,
                    "inputKind": "monitor_capture",
                    "inputSettings": {},
                    "sceneItemEnabled": True,
                },
            )
        try:
            self.set_item_enabled(scene, source, True)
        except ObsError:
            pass
        chosen, prop = self._monitor_item()
        if chosen is not None:
            self.request(
                "SetInputSettings",
                {"inputName": source,
                 "inputSettings": {prop: chosen["itemValue"]}},
            )
        try:
            self.fit_item(scene, source, self.p.overlay_width,
                          self.p.overlay_height)
        except ObsError:
            pass
        return {"kind": "monitor_capture", "source": source,
                "monitor": chosen, "property": prop}

    def ensure_window_capture(self, scene: str) -> dict:
        """录单个窗口（例如 Chrome）；window_match 匹配不到时保留 fallback。"""
        source = self.p.window_source
        existing = {i["inputName"] for i in self.list_inputs()}
        if source not in existing:
            self.request(
                "CreateInput",
                {
                    "sceneName": scene,
                    "inputName": source,
                    "inputKind": "window_capture",
                    "inputSettings": {},
                    "sceneItemEnabled": True,
                },
            )
        try:
            self.set_item_enabled(scene, source, True)
        except ObsError:
            pass
        win = self.window_item(source)
        selected = win["itemValue"] if win else self.p.window_fallback
        if selected:
            self.request(
                "SetInputSettings",
                {"inputName": source,
                 "inputSettings": {"window": selected}},
            )
        try:
            self.fit_item(scene, source, self.p.overlay_width,
                          self.p.overlay_height)
        except ObsError:
            pass
        return {"kind": "window_capture", "source": source,
                "window": win, "fallback": bool(not win)}

    def ensure_browser_source(self, scene: str) -> dict:
        """创建/更新场景页浏览器源；仅当 profile 配了 overlay_url 时生效。"""
        url = str(self.p.overlay_url or "").strip()
        if not url:
            return {"kind": "missing"}
        name = self.p.overlay_source
        existing = {i["inputName"] for i in self.list_inputs()}
        if name not in existing:
            self.request(
                "CreateInput",
                {
                    "sceneName": scene,
                    "inputName": name,
                    "inputKind": "browser_source",
                    "inputSettings": {
                        "url": url,
                        "width": int(self.p.overlay_width),
                        "height": int(self.p.overlay_height),
                        "reroute_audio": True,
                        # 场景切走时关掉页面，避免它在后台继续播放/抢占 live 音频。
                        "shutdown": True,
                        # 切到 Bili Watch 时强制重新载入，免得浏览器源缓存了旧页/404。
                        "restart_when_active": True,
                    },
                    "sceneItemEnabled": True,
                },
            )
        else:
            self.request(
                "SetInputSettings",
                {"inputName": name,
                 "inputSettings": {"url": url, "reroute_audio": True,
                                   "restart_when_active": True,
                                   "shutdown": True},
                 "overlay": True},
            )
        try:
            self.set_item_enabled(scene, name, True)
        except ObsError:
            pass
        try:
            item_id = self.get_scene_item_id(scene, name)
            # 场景页必须在采集画面之上，否则视频会把侧栏盖住。
            # OBS 的 sceneItemIndex 是 0=最底层；这里必须放到最顶层。
            count = len(self.scene_items(scene))
            self.request("SetSceneItemIndex", {
                "sceneName": scene, "sceneItemId": item_id,
                "sceneItemIndex": max(0, count - 1),
            })
        except ObsError:
            pass
        try:
            self.fit_item(scene, name, self.p.overlay_width,
                          self.p.overlay_height)
        except ObsError:
            pass
        return {"kind": "browser_source", "source": name, "url": url}

    def _remove_scene_source(self, scene: str, source: str) -> None:
        # OBS 对「还挂在场景里的 source」RemoveInput 会静默不生效，
        # 先摘掉这个场景里所有同名 scene item，再尝试移除 source。
        try:
            for item in self.scene_items(scene):
                if item.get("sourceName") == source:
                    self.request("RemoveSceneItem", {
                        "sceneName": scene,
                        "sceneItemId": item.get("sceneItemId")})
        except ObsError:
            pass
        try:
            if source in {i["inputName"] for i in self.list_inputs()}:
                self.request("RemoveInput", {"inputName": source})
        except ObsError:
            pass

    def ensure_desktop_audio(self, scene: str) -> dict:
        """B 站切视频会关旧 Chrome 窗口，桌面音频不会跟着某个窗口一起断。

        优先复用集合里已有的桌面音频，避免 Bili Audio 和 OBS 自带桌面音频
        同时抓一遍造成双倍音量/回声。
        """
        source = self.p.audio_source
        inputs = self.list_inputs()
        desktops = [i["inputName"] for i in inputs
                    if i.get("inputKind") == "wasapi_output_capture"]
        others = [n for n in desktops if n != source]
        if others:
            # 已经有现成的桌面音频；把之前建过的同名桌面源清掉。
            if source in desktops:
                self._remove_scene_source(scene, source)
            target = others[0]
            reused = True
        elif source in desktops:
            target = source
            reused = False
        else:
            if any(i["inputName"] == source for i in inputs):
                self._remove_scene_source(scene, source)
            self.request(
                "CreateInput",
                {
                    "sceneName": scene,
                    "inputName": source,
                    "inputKind": "wasapi_output_capture",
                    "inputSettings": {},
                    "sceneItemEnabled": True,
                },
            )
            target = source
            reused = False
        # 集合自带的桌面音频可能不在新场景的 scene item 列表里，补一个引用；
        # 但 CreateSceneItem 对同一个 source 可以重复加，先清掉重复项。
        try:
            items = [x for x in self.scene_items(scene)
                     if x.get("sourceName") == target]
            for extra in items[1:]:
                self.request("RemoveSceneItem", {
                    "sceneName": scene,
                    "sceneItemId": extra.get("sceneItemId")})
            if not items:
                self.request("CreateSceneItem", {
                    "sceneName": scene, "sourceName": target,
                    "sceneItemEnabled": True})
        except ObsError:
            pass
        try:
            self.set_item_enabled(scene, target, True)
        except ObsError:
            pass
        try:
            self.request("SetInputMute", {"inputName": target,
                                          "inputMuted": False})
            self.request("SetInputVolume", {"inputName": target,
                                            "inputVolumeMul": 1.0})
            self.request("SetInputAudioTracks", {
                "inputName": target, "inputAudioTracks": {"1": True}})
        except ObsError:
            pass
        return {"source": target, "kind": "desktop", "reused": reused}

    def ensure_audio(self, scene: str) -> dict:
        # B 站等会换窗口的模式用桌面音频；游戏默认按进程抓。
        if str(self.p.audio_mode or "process").lower() == "desktop":
            return self.ensure_desktop_audio(scene)
        source = self.p.audio_source
        existing = {i["inputName"] for i in self.list_inputs()}
        if source not in existing:
            try:
                self.request(
                    "CreateInput",
                    {
                        "sceneName": scene,
                        "inputName": source,
                        "inputKind": "wasapi_process_output_capture",
                        "inputSettings": {},
                        "sceneItemEnabled": True,
                    },
                )
            except ObsError as e:
                print("app audio create failed:", e, file=sys.stderr)
        if source in {i["inputName"] for i in self.list_inputs()}:
            try:
                props = self.request(
                    "GetInputPropertiesListPropertyItems",
                    {"inputName": source, "propertyName": "window"},
                ).get("propertyItems", [])
                chosen = None
                for item in props:
                    name = str(item.get("itemName", ""))
                    if any(key in name for key in self.p.window_match):
                        chosen = item
                        break
                if chosen:
                    self.request(
                        "SetInputSettings",
                        {"inputName": source, "inputSettings": {"window": chosen["itemValue"]}},
                    )
                self.request("SetInputMute", {"inputName": source, "inputMuted": False})
                self.request("SetInputVolume", {"inputName": source, "inputVolumeMul": 1.0})
                self.request("SetInputAudioTracks", {"inputName": source, "inputAudioTracks": {"1": True}})
                return {"source": source, "kind": "process"}
            except ObsError as e:
                print("app audio config failed:", e, file=sys.stderr)
        # Fallback: ensure the collection desktop audio is audible.
        try:
            for inp in self.list_inputs():
                if inp.get("inputKind") == "wasapi_output_capture":
                    name = inp["inputName"]
                    self.request("SetInputMute", {"inputName": name, "inputMuted": False})
                    self.request("SetInputVolume", {"inputName": name, "inputVolumeMul": 1.0})
                    self.request("SetInputAudioTracks", {"inputName": name, "inputAudioTracks": {"1": True}})
                    return {"source": name, "kind": "desktop"}
        except ObsError:
            pass
        return {"kind": "none"}

    def ensure_overlay_audio(self) -> dict:
        """让 OBS 接管浏览器源（AI Commentary）的音频，这样动物语音效会进录像。"""
        name = self.p.overlay_source
        if name not in {i["inputName"] for i in self.list_inputs()}:
            return {"kind": "missing"}
        try:
            self.request(
                "SetInputSettings",
                {"inputName": name, "inputSettings": {"reroute_audio": True}, "overlay": True},
            )
            self.request("SetInputAudioTracks", {"inputName": name, "inputAudioTracks": {"1": True}})
            self.request("SetInputMute", {"inputName": name, "inputMuted": False})
            self.request("SetInputVolume", {"inputName": name, "inputVolumeMul": 1.0})
            return {"source": name, "reroute_audio": True, "volume": 1.0}
        except ObsError as e:
            return {"error": str(e)}

    def ensure_ticker(self, scene: str):
        name = self.p.ticker_source
        if not name:
            return {"kind": "none"}
        existing = {i["inputName"] for i in self.list_inputs()}
        if name not in existing:
            self.request(
                "CreateInput",
                {
                    "sceneName": scene,
                    "inputName": name,
                    "inputKind": "text_gdiplus_v3",
                    "inputSettings": {
                        "text": self.p.ticker_text,
                        "font": {"face": "Microsoft YaHei", "size": 28, "flags": 0},
                        "color1": 0xFFFFFFFF,
                        "outline": True,
                        "outline_color": 0xFF000000,
                        "outline_size": 2,
                    },
                    "sceneItemEnabled": True,
                },
            )
        item_id = self.get_scene_item_id(scene, name)
        self.request(
            "SetSceneItemTransform",
            {
                "sceneName": scene,
                "sceneItemId": item_id,
                "sceneItemTransform": {"positionX": 24, "positionY": 24, "scaleX": 1, "scaleY": 1},
            },
        )
        return {"source": name, "item_id": item_id}

    def set_ticker(self, text: str):
        self.request("SetInputSettings", {"inputName": self.p.ticker_source, "inputSettings": {"text": text}})

    # ---- capture mode helpers (录游戏窗口，而不是录整个屏幕) ----
    def scene_items(self, scene: str):
        return self.request("GetSceneItemList", {"sceneName": scene}).get("sceneItems", [])

    def set_item_enabled(self, scene: str, source: str, enabled: bool):
        try:
            item_id = self.get_scene_item_id(scene, source)
        except ObsError:
            # free_mode 会顺手关掉“另一种采集源”；该游戏只建了一种源时，
            # 这里找不到是正常的，不要让整个场景准备失败。
            return False
        self.request(
            "SetSceneItemEnabled",
            {"sceneName": scene, "sceneItemId": item_id, "sceneItemEnabled": enabled},
        )
        return True

    def fit_item(self, scene: str, source: str, width: int = 1920, height: int = 1080):
        """Scale/center the source inside the canvas (top-left alignment, like the monitor source)."""
        item_id = self.get_scene_item_id(scene, source)
        tr = self.request(
            "GetSceneItemTransform", {"sceneName": scene, "sceneItemId": item_id}
        )["sceneItemTransform"]
        sw = float(tr.get("sourceWidth") or 0)
        sh = float(tr.get("sourceHeight") or 0)
        if sw <= 0 or sh <= 0:
            return None
        scale = min(width / sw, height / sh)
        self.request(
            "SetSceneItemTransform",
            {
                "sceneName": scene,
                "sceneItemId": item_id,
                "sceneItemTransform": {
                    "alignment": 5,
                    "positionX": (width - sw * scale) / 2,
                    "positionY": (height - sh * scale) / 2,
                    "scaleX": scale,
                    "scaleY": scale,
                },
            },
        )
        return {"sourceWidth": sw, "sourceHeight": sh, "scale": round(scale, 4)}

    def window_item(self, source: str):
        """当前打开的游戏窗口（title:class:exe）——按 profile 的 window_match 匹配。"""
        try:
            props = self.request(
                "GetInputPropertiesListPropertyItems",
                {"inputName": source, "propertyName": "window"},
            ).get("propertyItems", [])
        except ObsError:
            return None
        for item in props:
            name = str(item.get("itemName", ""))
            if any(key in name for key in self.p.window_match):
                return item
        return None

    def free_mode(self, mode: str = "game") -> dict:
        """切到「窗口/游戏采集」：别的窗口盖住、或游戏失焦，都继续录游戏画面。

        mode: game = game_capture（hook，无标题栏）｜window = window_capture(WGC)｜monitor = 旧的显示器采集
        """
        scene = self.p.scene
        self.ensure_scene(scene)
        existing = {i["inputName"] for i in self.list_inputs()}
        out = {"mode": mode, "scene": scene}

        if mode == "monitor":
            target = self.p.monitor_source
            for name in (self.p.game_source, self.p.window_source):
                if name in existing:
                    self.set_item_enabled(scene, name, False)
            self.set_item_enabled(scene, target, True)
            out["source"] = target
        else:
            target = self.p.game_source if mode == "game" else self.p.window_source
            other = self.p.window_source if mode == "game" else self.p.game_source
            if target not in existing:
                self.request(
                    "CreateInput",
                    {
                        "sceneName": scene,
                        "inputName": target,
                        "inputKind": "game_capture" if mode == "game" else "window_capture",
                        "inputSettings": {},
                        "sceneItemEnabled": True,
                    },
                )
            win = self.window_item(target)
            settings = {"capture_cursor": False}
            if mode == "game":
                settings["capture_mode"] = "window"
            else:
                settings["method"] = 2  # Windows 10/11 WGC，被遮挡也能抓
            settings["window"] = win["itemValue"] if win else self.p.window_fallback
            self.request(
                "SetInputSettings",
                {"inputName": target, "inputSettings": settings, "overlay": True},
            )
            self.set_item_enabled(scene, other, False)
            if self.p.monitor_source in existing:
                self.set_item_enabled(scene, self.p.monitor_source, False)
            self.set_item_enabled(scene, target, True)
            out["fit"] = self.fit_item(scene, target)
            out["source"] = target
            out["window"] = (win or {}).get("itemName") or settings.get("window")

        out["audio"] = self.ensure_audio(scene)
        out["overlay_audio"] = self.ensure_overlay_audio()
        try:
            out["active"] = self.request("GetSourceActive", {"sourceName": out["source"]})
        except ObsError as e:
            out["active"] = {"error": str(e)}
        try:
            out["frame"] = self.frame_stats(out["source"])
        except (ObsError, OSError, ValueError) as e:
            out["frame"] = {"error": str(e)}
        return out

    def frame_stats(self, source: str, width: int = 160) -> dict:
        """截一帧算亮度/方差：判断采集源是真的有画面，还是黑屏（game_capture 的 active 会骗人）。"""
        d = self.request(
            "GetSourceScreenshot",
            {"sourceName": source, "imageFormat": "png", "imageWidth": width,
             "imageCompressionQuality": 50},
            timeout=15,
        )
        data = d.get("imageData", "")
        if data.startswith("data:image"):
            data = data.split(",", 1)[1]
        raw = base64.b64decode(data)
        try:
            from PIL import Image, ImageStat

            im = Image.open(io.BytesIO(raw)).convert("L")
            st = ImageStat.Stat(im)
            return {"mean": round(st.mean[0], 1), "stddev": round(st.stddev[0], 1), "bytes": len(raw)}
        except ImportError:
            return {"bytes": len(raw)}

    def health(self) -> dict:
        """录制健康检查：画面源是否真的在出帧、音频是否静音、有没有掉过窗口。"""
        out = {}
        try:
            out["record"] = self.request("GetRecordStatus")
        except ObsError as e:
            out["record"] = {"error": str(e)}
        try:
            out["scene"] = self.request("GetCurrentProgramScene").get("currentProgramSceneName")
        except ObsError as e:
            out["scene"] = f"error: {e}"
        sources = []
        try:
            kinds = {i["inputName"]: i.get("inputKind") for i in self.list_inputs()}
            for item in self.scene_items(out["scene"]):
                name = item.get("sourceName")
                try:
                    active = self.request("GetSourceActive", {"sourceName": name})
                except ObsError:
                    active = {}
                stats = {}
                if item.get("sceneItemEnabled") and (item.get("inputKind") or kinds.get(name)) in (
                    "monitor_capture", "window_capture", "game_capture"
                ):
                    try:
                        stats = self.frame_stats(name)
                    except (ObsError, OSError, ValueError):
                        stats = {}
                sources.append(
                    {
                        "name": name,
                        "kind": item.get("inputKind") or kinds.get(name),
                        "enabled": item.get("sceneItemEnabled"),
                        "videoActive": active.get("videoActive"),
                        "frame": stats,
                    }
                )
        except ObsError:
            pass
        out["sources"] = sources
        out["window"] = game_window_state(self.p)
        out["mute_in_background"] = game_mute_in_background()
        try:
            ov = self.request("GetInputSettings", {"inputName": self.p.overlay_source}).get("inputSettings", {})
            out["overlay_audio"] = {
                "reroute_audio": bool(ov.get("reroute_audio")),
                "muted": self.request("GetInputMute", {"inputName": self.p.overlay_source}).get("inputMuted"),
                "volume": self.request("GetInputVolume", {"inputName": self.p.overlay_source}).get("inputVolumeMul"),
            }
        except ObsError as e:
            out["overlay_audio"] = {"error": str(e)}
        try:
            out["audio"] = {
                "muted": self.request("GetInputMute", {"inputName": self.p.audio_source}).get("inputMuted"),
                "volume": self.request("GetInputVolume", {"inputName": self.p.audio_source}).get("inputVolumeMul"),
                "settings": self.request("GetInputSettings", {"inputName": self.p.audio_source}).get("inputSettings"),
            }
        except ObsError as e:
            out["audio"] = {"error": str(e)}

        warns = []
        win = out.get("window") or {}
        if win.get("minimized"):
            warns.append(
                "游戏窗口被最小化了：OBS 抓不到最小化窗口，画面会停/黑、音频也会断 → 恢复窗口（别最小化）"
            )
        if out.get("mute_in_background"):
            warns.append(
                "游戏自带的「后台静音」还开着（prefs.save: mute_in_background=true）：失焦约 20 秒后音轨会静音 → "
                "改成 false（或游戏设置里关掉）"
            )
        ov = out.get("overlay_audio") or {}
        if "error" not in ov:
            if not ov.get("reroute_audio"):
                warns.append("评论音效没被 OBS 接管（reroute_audio=false），动物语不会进录像 → 跑 `python main.py obs free`")
            elif ov.get("muted"):
                warns.append("评论音效被静音了，动物语音效听不到 → `python main.py obs mute` 反了？先看 OBS 混音器")
        active_video = [s for s in sources if s["enabled"] and s["kind"] in
                        ("monitor_capture", "window_capture", "game_capture", "browser_source")]
        for s in active_video:
            if s["kind"] == "monitor_capture":
                warns.append(
                    "画面源是「显示器采集」：你切到 B 站或别的游戏，录到的就是那个画面 → 跑 "
                    "`python main.py obs free` 换成窗口采集"
                )
            elif s["kind"] in ("window_capture", "game_capture"):
                frame = s.get("frame") or {}
                if not frame:
                    if not win:
                        warns.append(f"「{s['name']}」没有画面：游戏没开（开游戏后跑 `python main.py obs free`）")
                elif frame.get("mean") is not None and frame["mean"] < 3 and frame.get("stddev", 0) < 3:
                    warns.append(f"「{s['name']}」画面全黑：采集没命中窗口")
        if out["audio"].get("muted"):
            warns.append(f"{self.p.audio_source} 被静音了 → `python main.py obs mute 0`")
        rec = out["record"] or {}
        if not rec.get("outputActive"):
            warns.append("当前没有在录制 → `python main.py obs start`")
        out["warnings"] = warns
        out["log"] = obs_log_tail()
        return out

    def start_record(self):
        return self.request("StartRecord")

    def stop_record(self):
        return self.request("StopRecord")

    def status(self):
        return self.request("GetRecordStatus")

    def screenshot(self, path: str, source: str | None = None, width: int = 1920):
        source = source or self.p.game_source
        d = self.request(
            "GetSourceScreenshot",
            {
                "sourceName": source,
                "imageFormat": "png",
                "imageWidth": width,
                "imageCompressionQuality": 80,
            },
            timeout=20,
        )
        data = d.get("imageData", "")
        if data.startswith("data:image"):
            data = data.split(",", 1)[1]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path


def obs_log_tail(limit: int = 400) -> dict:
    """最近一份 OBS 日志里的窗口/音频异常（用来判断应用音频有没有断过）。"""
    logdir = os.path.join(os.environ.get("APPDATA", ""), "obs-studio", "logs")
    try:
        logs = sorted(
            (os.path.join(logdir, f) for f in os.listdir(logdir) if f.endswith(".txt")),
            key=os.path.getmtime,
        )
    except OSError:
        return {}
    if not logs:
        return {}
    path = logs[-1]
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return {}
    disappearing = [
        ln.strip() for ln in lines
        if "window disappeared" in ln or "invalidated" in ln or "failed to start" in ln
    ]
    return {
        "file": os.path.basename(path),
        "events": disappearing[-5:],
    }


def game_mute_in_background() -> list:
    """扫描 prefs.save：游戏自带的「后台静音」有没有还开着（开了就会失焦静音）。"""
    root = os.path.join(os.environ.get("APPDATA", ""), "SlayTheSpire2")
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        if "prefs.save" not in files:
            continue
        path = os.path.join(dirpath, "prefs.save")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        if '"mute_in_background": true' in text:
            hits.append(path)
    return hits


def fix_mute_in_background() -> list:
    """把 prefs.save 的 mute_in_background 改成 false（本地 + Steam 云存档），并留备份。

    游戏只在启动时读这个值，所以改完要重启游戏；云存档不改的话，下次启动会被云端的 true 覆盖。
    """
    import shutil

    appdata = os.environ.get("APPDATA", "")
    local_root = os.path.join(appdata, "SlayTheSpire2")
    steam_root = ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam_root = winreg.QueryValueEx(k, "SteamPath")[0]
    except Exception:
        for cand in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
            if os.path.isdir(cand):
                steam_root = cand
                break

    backup_dir = os.path.join(BASE, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    changed = []

    def patch(path: str, root: str, tag: str):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return
        if '"mute_in_background": true' not in text:
            return
        rel = os.path.relpath(path, root).replace("\\", "_").replace(":", "")
        name = f"{tag}.{stamp}.{hashlib.md5(rel.encode('utf-8')).hexdigest()[:6]}.bak"
        shutil.copy2(path, os.path.join(backup_dir, name))
        text = text.replace('"mute_in_background": true', '"mute_in_background": false')
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        changed.append(path)

    for root in (local_root,):
        for dirpath, _dirs, files in os.walk(root):
            if "prefs.save" in files:
                patch(os.path.join(dirpath, "prefs.save"), root, "prefs")

    if steam_root:
        remote_root = os.path.join(steam_root, "userdata")
        if os.path.isdir(remote_root):
            for uid in os.listdir(remote_root):
                base = os.path.join(remote_root, uid, "2868840", "remote")
                if not os.path.isdir(base):
                    continue
                for dirpath, _dirs, files in os.walk(base):
                    if "prefs.save" in files:
                        patch(os.path.join(dirpath, "prefs.save"), base, "cloud_prefs")
    return changed


def game_window_state(profile: ObsProfile | None = None) -> dict:
    """找游戏窗口，报告标题/是否最小化（最小化会让采集和音频一起断）。"""
    p = profile or load_profile()
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return {}
    user32 = ctypes.windll.user32
    found = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
            if any(key in title for key in p.window_match):
                found.update(
                    title=title,
                    minimized=bool(user32.IsIconic(hwnd)),
                    visible=bool(user32.IsWindowVisible(hwnd)),
                )
                return False
        return True

    user32.EnumWindows(cb, 0)
    return found


def print_health(h: dict) -> None:
    rec = h.get("record", {})
    if rec.get("outputActive"):
        print(f"录制：进行中（{rec.get('outputTimecode', '')}，{rec.get('outputBytes', 0) / 1e9:.2f} GB）")
    else:
        print("录制：未开始")
    print(f"场景：{h.get('scene')}")
    for s in h.get("sources", []):
        if s.get("kind") not in ("monitor_capture", "window_capture", "game_capture", "browser_source"):
            continue
        mark = "[ON] " if s.get("enabled") else "[off]"
        live = "出帧" if s.get("videoActive") else ("无画面" if s.get("enabled") else "关闭")
        fr = s.get("frame") or {}
        if fr.get("mean") is not None:
            live += f"（亮度 {fr['mean']} / 方差 {fr['stddev']}）"
        print(f"  {mark} {s.get('name')} [{s.get('kind')}] {live}")
    a = h.get("audio", {})
    if "error" in a:
        print(f"音频：读取失败（{a['error']}）")
    else:
        win = (a.get("settings") or {}).get("window", "")
        print(f"音频：{'静音' if a.get('muted') else '开'}｜音量 {a.get('volume')}｜目标 {win}")
    gw = h.get("window") or {}
    if gw:
        print(f"游戏窗口：{gw.get('title')}｜{'已最小化' if gw.get('minimized') else '正常'}")
    else:
        print("游戏窗口：没找到（游戏没开）")
    mb = h.get("mute_in_background") or []
    print(f"后台静音：{'仍开着（%d 个 profile）' % len(mb) if mb else '已关'}")
    ov = h.get("overlay_audio") or {}
    if "error" not in ov:
        vol = round(float(ov.get("volume") or 0), 2)
        print(f"评论音效：{'OBS 接管' if ov.get('reroute_audio') else '未接管'}"
              f"｜音量 {vol}｜{'静音' if ov.get('muted') else '开'}")
    warns = h.get("warnings") or []
    for w in warns:
        print(f"WARN {w}")
    log = h.get("log") or {}
    if log.get("events"):
        print(f"最近 OBS 日志（{log.get('file')}）：")
        for e in log["events"]:
            print(f"  {e}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "cmd",
        choices=["status", "setup", "start", "stop", "shot", "ticker", "mute", "free", "health", "nomute"],
    )
    ap.add_argument("arg", nargs="?")
    args = ap.parse_args(argv)
    cfg = load_ws_config()
    profile = load_profile()
    obs = Obs(cfg.get("url", DEFAULT_WS), cfg.get("password", ""), profile=profile)
    try:
        obs.connect()
        if args.cmd == "status":
            print(json.dumps(obs.status(), ensure_ascii=False, indent=2))
        elif args.cmd == "setup":
            # setup noswitch：只建/更新场景，不打断正在直播的 Program Scene。
            activate = str(args.arg or "").strip().lower() not in (
                "noswitch", "no-switch", "false")
            obs.ensure_scene(profile.scene, activate=activate)
            cap = obs.ensure_game_capture(profile.scene)
            audio = obs.ensure_audio(profile.scene)
            overlay = (obs.ensure_browser_source(profile.scene)
                       if profile.overlay_url else {"kind": "existing"})
            overlay_audio = obs.ensure_overlay_audio()
            ticker = obs.ensure_ticker(profile.scene)
            free = (obs.free_mode(profile.capture_mode or "game")
                    if activate else {"kind": "skipped", "mode": profile.capture_mode})
            print(
                json.dumps(
                    {"scene": profile.scene, "activated": activate,
                     "capture": cap, "audio": audio, "overlay": overlay,
                     "overlay_audio": overlay_audio, "ticker": ticker,
                     "free": free},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.cmd == "free":
            mode = args.arg or "game"
            if mode not in ("game", "window", "monitor"):
                raise SystemExit("free 模式只能是 game / window / monitor")
            print(json.dumps(obs.free_mode(mode), ensure_ascii=False, indent=2))
        elif args.cmd == "health":
            h = obs.health()
            if args.arg == "json":
                print(json.dumps(h, ensure_ascii=False, indent=2))
            else:
                print_health(h)
        elif args.cmd == "nomute":
            changed = fix_mute_in_background()
            if changed:
                print("已改 prefs.save（重启游戏后生效）：")
                for p in changed:
                    print(" ", p)
            else:
                print("没有需要改的：mute_in_background 已经是 false（或没找到 prefs.save）")
        elif args.cmd == "start":
            print(json.dumps(obs.start_record(), ensure_ascii=False))
        elif args.cmd == "stop":
            print(json.dumps(obs.stop_record(), ensure_ascii=False))
        elif args.cmd == "shot":
            p = args.arg or "preview.png"
            print(obs.screenshot(p))
        elif args.cmd == "ticker":
            obs.set_ticker(args.arg or "")
            print("ok")
        elif args.cmd == "mute":
            name = profile.audio_source
            obs.request("SetInputMute", {"inputName": name, "inputMuted": args.arg == "1"})
            print("ok")
    finally:
        obs.close()


if __name__ == "__main__":
    main()
