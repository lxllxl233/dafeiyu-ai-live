"""内置仿真世界：没有 Minecraft 也能跑通 observe / validate / execute。

它把 :mod:`rules` 的纯规则包成一个「假桥」：

- 状态持久化到 ``logs/minecraft_state.json``（可用 ``MINECRAFT_STATE`` 指到别处），
  所以适配器、工具、自动玩、测试看到的是同一份世界；
- 接口和真机桥一致：``state()`` 读快照，``act(payload)`` 走一个动作；
- 只用于离线验收和本地演示，状态里会带 ``source: "sim"``，不会伪装成真游戏。

真机请装游戏侧桥并把 ``MINECRAFT_BRIDGE=http``；协议见 ``games/minecraft/README.md``。
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from dafeiyu import paths

from .. import rules
from ..rules import RuleError

STATE_ENV = "MINECRAFT_STATE"
STATE_VERSION = 1


def state_path() -> Path:
    """仿真世界的落盘路径；测试用 ``MINECRAFT_STATE`` 指到临时目录。"""
    override = os.environ.get(STATE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return paths.LOGS / "minecraft_state.json"


def public_state(state: dict[str, Any]) -> dict[str, Any]:
    """给引擎/适配器看的快照：加协议头，不改内部字段。"""
    payload = copy.deepcopy(state)
    payload.update({
        "ok": True,
        "ready": True,
        "api": rules.BRIDGE_VERSION,
        "bridge": "mc-bridge/v1",
        "source": "sim",
    })
    return payload


class SimBridge:
    """一个进程内、可持久化的 Minecraft 生存仿真。"""

    def __init__(self, path: Path | str | None = None,
                 difficulty: str = "normal") -> None:
        self.path = Path(path) if path else state_path()
        self.difficulty = str(difficulty or "normal").strip().lower()
        self._lock = threading.RLock()
        self._state: dict[str, Any] | None = None

    # ---------------------------------------------------------------- 存取
    def _load(self) -> dict[str, Any]:
        if self._state is not None:
            return self._state
        raw: dict[str, Any] | None = None
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("version") == STATE_VERSION:
                raw = loaded
        except (OSError, ValueError):
            raw = None
        self._state = raw or rules.new_world(self.difficulty)
        return self._state

    def _save(self) -> None:
        state = self._load()
        state["version"] = STATE_VERSION
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    # ---------------------------------------------------------------- 接口
    def state(self) -> dict[str, Any]:
        with self._lock:
            return public_state(self._load())

    def act(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        """执行一个控制动作或一步计划；返回 ``{ok, message|error}``。"""
        payload = payload if isinstance(payload, dict) else {}
        with self._lock:
            state = self._load()
            kind = str(payload.get("type") or "").strip().lower()
            try:
                if kind in ("start", "respawn", "new"):
                    message = self._start(payload)
                elif kind == "stop":
                    state["screen"] = "paused"
                    state["last"] = "已暂停"
                    message = "已暂停"
                elif kind == "reset":
                    self._state = rules.new_world(
                        str(payload.get("difficulty") or self.difficulty))
                    state = self._state
                    state["last"] = "重新开了一个世界"
                    message = "新世界已生成"
                elif kind in rules.ACTIONS:
                    result = rules.apply_step(state, payload)
                    message = str(result.get("message") or "")
                else:
                    return {"ok": False, "error": f"不认识的控制动作：{kind or '空'}"}
            except RuleError as exc:
                state["last"] = str(exc)
                return {"ok": False, "error": str(exc)}
            state["updated_at"] = time.time()
            self._save()
            return {"ok": True, "message": message,
                    "state": public_state(state)}

    def _start(self, payload: dict[str, Any]) -> str:
        state = self._load()
        difficulty = str(payload.get("difficulty") or self.difficulty).strip().lower()
        if state.get("screen") == "paused":
            state["screen"] = "playing"
            state["last"] = "继续玩"
            return "继续玩"
        if state.get("screen") == "dead":
            self._state = rules.new_world(difficulty)
            self._state["last"] = "重生了：这次先搭个躲的地方。"
            return "重生了"
        if state.get("screen") not in ("playing", ""):
            self._state = rules.new_world(difficulty)
            return "新世界已生成"
        if difficulty in rules.DIFFICULTIES:
            state["world"]["difficulty"] = difficulty
        return "已经在玩了"

    def reset(self, difficulty: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._state = rules.new_world(str(difficulty or self.difficulty))
            self._save()
            return public_state(self._state)

    def close(self) -> None:
        return None


__all__ = ["STATE_ENV", "STATE_VERSION", "SimBridge", "public_state",
           "state_path"]
