"""Minecraft 本地桥客户端（``mc-bridge/v1``）。

游戏侧桥（未来的 Fabric/Forge 模组，或 ``bridge/server.py`` 起的仿真壳）在
loopback 上暴露两个端点：

- ``GET  /state`` -> 世界/玩家/附近方块与生物的快照；
- ``POST /act``   -> 一个动作（goto/mine/craft/smelt/place/attack/eat/equip/wait）
  以及控制动作（start/stop/reset）。

和《吸血鬼幸存者》的桥一样，Python 端**不找窗口、不切前台、不发键盘**：

- ``mode=http``：只走 HTTP，桥没连上就是离线，绝不退化成键盘；
- ``mode=sim``：用内置仿真世界（``bridge/sim.py``）跑通链路；
- ``mode=auto``（默认）：先试 HTTP，连不上才用仿真，状态里会写清 ``source``。

协议细节见 ``games/minecraft/README.md``。
"""

from __future__ import annotations

import os
from typing import Any

import requests

API_ENV = "MINECRAFT_API"
MODE_ENV = "MINECRAFT_BRIDGE"
DEFAULT_API = "http://127.0.0.1:8092"
API_VERSION = 1
MODES = ("auto", "http", "sim")


class McUnavailable(RuntimeError):
    """桥没开 / 连不上。"""


class McError(RuntimeError):
    """桥回话了但拒绝了请求。"""


def api_url() -> str:
    return str(os.environ.get(API_ENV) or DEFAULT_API).rstrip("/")


def bridge_mode() -> str:
    mode = str(os.environ.get(MODE_ENV) or "auto").strip().lower()
    return mode if mode in MODES else "auto"


class McClient:
    """很薄的 HTTP 客户端；只认 ``mc-bridge/v1``。"""

    def __init__(self, base_url: str | None = None, timeout: float = 1.8,
                 act_timeout: float = 40.0) -> None:
        self.base_url = str(base_url or api_url()).rstrip("/")
        self.timeout = max(0.2, float(timeout))
        # 真机动作（走过去+挖/打）要好几秒，读状态仍要快，所以分开超时。
        self.act_timeout = max(self.timeout, float(act_timeout))

    # ---------------------------------------------------------------- plumbing
    def _get(self, path: str) -> dict[str, Any]:
        try:
            resp = requests.get(self.base_url + path, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise McUnavailable(f"MC 桥 {self.base_url}{path}: {exc}") from exc
        except ValueError as exc:
            raise McError(f"MC 桥返回的不是 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise McError("MC 桥返回的不是对象")
        if not data.get("ok", False):
            raise McError(str(data.get("error") or "MC 桥拒绝了请求"))
        return data

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = requests.post(self.base_url + path, json=payload,
                                 timeout=self.act_timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise McUnavailable(f"MC 桥 {self.base_url}{path}: {exc}") from exc
        except ValueError as exc:
            raise McError(f"MC 桥返回的不是 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise McError("MC 桥返回的不是对象")
        if not data.get("ok", False):
            raise McError(str(data.get("error") or "MC 桥拒绝了动作"))
        return data

    # ---------------------------------------------------------------- public
    def state(self) -> dict[str, Any]:
        return self._get("/state")

    def act(self, kind: str, **fields: Any) -> dict[str, Any]:
        body = {"type": str(kind)}
        body.update({key: value for key, value in fields.items()
                     if value is not None})
        return self._post("/act", body)

    def available(self) -> bool:
        try:
            return bool(self.state().get("ready", True))
        except (McUnavailable, McError):
            return False

    def start(self, difficulty: str = "normal") -> dict[str, Any]:
        return self.act("start", difficulty=difficulty)

    def stop(self) -> dict[str, Any]:
        return self.act("stop")


class Bridge:
    """协议层：自动在「真机 HTTP」和「内置仿真」之间选一个，并标明来源。"""

    def __init__(self, mode: str | None = None, *, base_url: str | None = None,
                 timeout: float = 1.8, act_timeout: float = 40.0,
                 sim_path: Any = None, difficulty: str = "normal") -> None:
        self.mode = str(mode or bridge_mode()).strip().lower()
        if self.mode not in MODES:
            self.mode = "auto"
        self.base_url = str(base_url or api_url()).rstrip("/")
        self.timeout = max(0.2, float(timeout))
        self.act_timeout = max(self.timeout, float(act_timeout))
        self.sim_path = sim_path
        self.difficulty = str(difficulty or "normal")
        self._client_obj: McClient | None = None
        self._sim_obj = None
        self._source = "offline" if self.mode == "http" else (
            "sim" if self.mode == "sim" else "")
        self.error = ""

    # ---------------------------------------------------------------- 内部
    def _client(self) -> McClient:
        if self._client_obj is None:
            self._client_obj = McClient(self.base_url, timeout=self.timeout,
                                        act_timeout=self.act_timeout)
        return self._client_obj

    def _sim(self):
        if self._sim_obj is None:
            from ..bridge.sim import SimBridge

            self._sim_obj = SimBridge(self.sim_path, difficulty=self.difficulty)
        return self._sim_obj

    def _http_state(self) -> dict[str, Any]:
        data = self._client().state()
        if not isinstance(data, dict):
            raise McError("MC 桥返回的不是对象")
        return {**data, "source": "http"}

    # ---------------------------------------------------------------- 公开
    @property
    def source(self) -> str:
        if self.mode == "sim":
            return "sim"
        if self.mode == "http":
            return "http" if not self.error else "offline"
        return self._source or "offline"

    def state(self) -> dict[str, Any]:
        if self.mode == "sim":
            return self._sim().state()

        if self._source == "sim":
            return self._sim().state()

        try:
            data = self._http_state()
        except McUnavailable as exc:
            self.error = str(exc)[:200]
            if self.mode != "auto":
                self._source = "offline"
                raise
            self._source = "sim"
            data = self._sim().state()
            data["fallback_reason"] = self.error
            return data
        self._source = "http"
        self.error = ""
        return data

    def act(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        payload = payload if isinstance(payload, dict) else {}
        if self.mode == "sim" or self._source == "sim":
            result = self._sim().act(payload)
            if not result.get("ok", False):
                raise McError(str(result.get("error") or "仿真桥拒绝了动作"))
            return result
        if self._source == "offline":
            raise McUnavailable(self.error or "MC 桥未连接")
        try:
            return self._client().act(str(payload.get("type") or ""),
                                      **{key: value for key, value in payload.items()
                                         if key != "type"})
        except McUnavailable:
            # 跑到一半桥断了：不要在另一个世界里继续，交给 runner 收手。
            self.error = "MC 桥断线"
            self._source = "offline" if self.mode == "auto" else self._source
            raise

    def act_step(self, step: dict[str, Any]) -> dict[str, Any]:
        """执行一步计划动作（payload 就是动作本身）。"""
        return self.act(step)

    def available(self) -> bool:
        try:
            data = self.state()
        except (McUnavailable, McError):
            return False
        return bool(data.get("ready", True))

    def start(self, difficulty: str = "normal") -> dict[str, Any]:
        return self.act({"type": "start", "difficulty": difficulty})

    def stop(self) -> dict[str, Any]:
        return self.act({"type": "stop"})

    def reset(self, difficulty: str = "normal") -> dict[str, Any]:
        return self.act({"type": "reset", "difficulty": difficulty})

    def close(self) -> None:
        if self._sim_obj is not None:
            self._sim_obj.close()


__all__ = [
    "API_ENV", "API_VERSION", "Bridge", "DEFAULT_API", "McClient", "McError",
    "McUnavailable", "MODE_ENV", "MODES", "api_url", "bridge_mode",
]
