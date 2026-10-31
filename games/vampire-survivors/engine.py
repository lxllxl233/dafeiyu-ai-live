"""《吸血鬼幸存者》的状态与动作层（本地 API 版）。

游戏侧桥接插件（``compat/mod``）在 ``127.0.0.1:8091`` 暴露一个很小的 HTTP
API；本模块只通过它观察和操作游戏，**不找窗口、不切前台、不发键盘**。
这和《杀戮尖塔 2》的 compat 客户端是同一个思路：游戏可以在后台运行。

状态来自 ``GET /state``::

    {"ok": true, "ready": true, "state": "playing",
     "player": {"x": 1.2, "y": -0.4, "hp": 90, "max_hp": 100, "level": 7,
                "xp": 12, "xp_to_next": 20, "minute": 3, "seconds": 181.2,
                "character": "ANTONIO", "weapons": [...], "passives": [...]},
     "enemies": [{"x": 0.5, "y": 1.0, "hp": 20, "type": "BAT", "boss": false}],
     "gems": [...], "pickups": [...], "screen": {"half_w": 8, "half_h": 4.5},
     "menu": {"kind": "level_up", "options": [{"key": "1", "name": "Whip"}]},
     "run": {"active": true}}

动作走 ``POST /act``（vampire-survivors.action/v1）::

    {"seq": [
        {"move": "right", "seconds": 0.9},
        {"move": "up", "seconds": 0.6},
        {"press": "1"},
        {"wait": 0.4}
    ]}

- ``move``：up / down / left / right（引擎转成 dx/dy 发给桥）；
- ``press``：1–5（升级选择，桥会转成 ``choose``）、enter / space / escape；
- ``wait``：原地等待 0.05–2.0 秒；
- 一次最多 8 步、总时长最多 12 秒。

桥连不上时返回 ``unavailable``，绝不退化成键盘/抢焦点。
"""

from __future__ import annotations

import time
from typing import Any

from dafeiyu.games.contracts import ActionCode, ActionResult, GameStateEnvelope

GAME_ID = "vampire-survivors"
MAX_STEPS = 8
MAX_TOTAL_SECONDS = 12.0
DIRECTIONS = ("up", "down", "left", "right")
PRESS_KEYS = ("1", "2", "3", "4", "5", "space", "enter", "escape", "f", "tab")
VECTORS = {
    "up": (0.0, 1.0),
    "down": (0.0, -1.0),
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
}
SCREEN_MAP = {
    "playing": "PLAYING",
    "level_up": "LEVEL_UP",
    "menu": "MENU",
    "game_over": "GAME_OVER",
    "paused": "PAUSED",
    "loading": "LOADING",
    "dead": "GAME_OVER",
    "unknown": "",
}


def _client():
    try:
        from .compat.vs import VsClient

        return VsClient()
    except ImportError:
        from games.vampire_survivors.compat.vs import VsClient

        return VsClient()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _choices(menu: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    options = (menu or {}).get("options") if isinstance(menu, dict) else None
    for index, item in enumerate(options if isinstance(options, list) else [], 1):
        if isinstance(item, dict):
            out.append({
                "key": str(item.get("key") or index),
                "name": str(item.get("name") or "")[:80],
            })
        elif item:
            out.append({"key": str(index), "name": str(item)[:80]})
    return out[:5]


def _equip(raw: Any) -> list[str]:
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            name = str(item.get("id") or item.get("name") or "").strip()
            if name:
                level = item.get("level")
                out.append(f"{name} L{level}" if level is not None else name)
        elif item:
            out.append(str(item))
    return [line[:40] for line in out][:12]


def context(state: dict[str, Any] | None = None,
            api: dict[str, Any] | None = None) -> dict[str, Any]:
    """把桥返回的原始状态合成一份给模型/适配器看的上下文。"""
    state = state if isinstance(state, dict) else {}
    api = api if isinstance(api, dict) else {}
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    run = state.get("run") if isinstance(state.get("run"), dict) else {}
    menu = state.get("menu") if isinstance(state.get("menu"), dict) else {}
    screen = SCREEN_MAP.get(str(state.get("state") or "").lower(),
                            str(state.get("state") or "").upper())
    online = bool(state.get("ready", False)) and bool(api.get("ok", False))
    source = "api" if online else "offline"
    choices = _choices(menu)
    return {
        "game": GAME_ID,
        "screen": screen,
        "source": source,
        "time_s": round(max(0.0, _float(player.get("seconds"),
                                        _float(state.get("seconds")))), 1),
        "level": _int(player.get("level")),
        "kills": _int(player.get("kills"), _int(state.get("kills"))),
        "gold": _int(player.get("gold"), _int(state.get("gold"))),
        "hp": _int(player.get("hp")) if online else None,
        "max_hp": _int(player.get("max_hp")) if online else None,
        "xp": _int(player.get("xp")),
        "xp_to_next": _int(player.get("xp_to_next")),
        "minute": _int(player.get("minute")),
        "stage": str(player.get("stage") or state.get("stage") or "")[:60],
        "character": str(player.get("character") or "")[:40],
        "weapons": _equip(player.get("weapons")),
        "passives": _equip(player.get("passives")),
        "choices": choices,
        "menu": {
            "kind": str(menu.get("kind") or "")[:40],
            "title": str(menu.get("title") or "")[:80],
            "options": choices,
        } if menu else None,
        "enemies": [
            {
                "x": round(_float(item.get("x")), 2),
                "y": round(_float(item.get("y")), 2),
                "hp": _int(item.get("hp")),
                "type": str(item.get("type") or "")[:30],
                "boss": bool(item.get("boss")),
            }
            for item in (state.get("enemies") or [])[:20]
            if isinstance(item, dict)
        ],
        "gems": [
            {
                "x": round(_float(item.get("x")), 2),
                "y": round(_float(item.get("y")), 2),
                "value": _int(item.get("value"), 1),
            }
            for item in (state.get("gems") or [])[:16]
            if isinstance(item, dict)
        ],
        "pickups": [
            {"x": round(_float(item.get("x")), 2),
             "y": round(_float(item.get("y")), 2),
             "type": str(item.get("type") or "")[:30]}
            for item in (state.get("pickups") or [])[:8]
            if isinstance(item, dict)
        ],
        "position": {
            "x": round(_float(player.get("x")), 2),
            "y": round(_float(player.get("y")), 2),
        },
        "run": {
            "active": bool(run.get("active")),
            "started": bool(run.get("started")),
            "automation": bool(state.get("automation") or api.get("automation")),
        },
        "api": {
            "ok": online,
            "url": str(api.get("url") or "")[:80],
            "ready": bool(state.get("ready", False)),
            "version": _int(api.get("version")),
        },
        "last": str(state.get("last") or "")[:120],
    }


def envelope(ctx: dict[str, Any]) -> GameStateEnvelope:
    return GameStateEnvelope(
        game_id=GAME_ID,
        revision=f"{ctx.get('screen') or 'off'}-{ctx.get('level') or 0}-"
                 f"{ctx.get('kills') or 0}",
        summary={
            "screen": ctx.get("screen") or "",
            "floor": int(ctx.get("level") or 0),
            "hp": int(ctx.get("hp") or 0),
            "max_hp": int(ctx.get("max_hp") or 0),
            "gold": int(ctx.get("gold") or 0),
            "character": ctx.get("character") or "",
            "in_combat": bool(ctx.get("screen") == "PLAYING"),
            "turn": int(ctx.get("level") or 0),
        },
        data=ctx,
    )


def _resolve(plan: Any) -> tuple[list[dict[str, Any]], str]:
    """校验计划，返回 ``(规范动作, 错误)``；不发请求、不改状态。"""
    if not isinstance(plan, dict):
        return [], "plan 必须是对象"
    raw_seq = plan.get("seq")
    if raw_seq is None:
        if any(key in plan for key in ("move", "press", "wait", "action")):
            raw_seq = [plan]
        else:
            return [], "plan 缺少 seq"
    if not isinstance(raw_seq, list) or not raw_seq:
        return [], "seq 必须是非空数组"
    if len(raw_seq) > MAX_STEPS:
        return [], f"seq 最多 {MAX_STEPS} 步"

    steps: list[dict[str, Any]] = []
    total = 0.0
    for index, item in enumerate(raw_seq, 1):
        if not isinstance(item, dict):
            return [], f"seq[{index}] 不是对象"
        if "move" in item:
            direction = str(item.get("move") or "").strip().lower()
            if direction not in DIRECTIONS:
                return [], f"seq[{index}] 未知方向：{direction!r}"
            seconds = _float(item.get("seconds"), 0.8)
            if not (0.05 <= seconds <= 3.0):
                return [], f"seq[{index}] seconds 超出 0.05–3.0"
            total += seconds
            steps.append({"move": direction, "seconds": round(seconds, 2)})
            continue
        if "press" in item:
            key = str(item.get("press") or "").strip().lower()
            if key not in PRESS_KEYS:
                return [], f"seq[{index}] 不支持的按键：{key!r}"
            steps.append({"press": key})
            continue
        if "wait" in item:
            seconds = _float(item.get("wait"), 0.2)
            if not (0.05 <= seconds <= 2.0):
                return [], f"seq[{index}] wait 超出 0.05–2.0"
            total += seconds
            steps.append({"wait": round(seconds, 2)})
            continue
        # 也接受 {"action": "move", "dir": ...} 这种口语写法，但只做最小映射。
        action = str(item.get("action") or "").strip().lower()
        direction = str(item.get("dir") or item.get("direction") or "").strip().lower()
        if action in ("move", "walk") and direction in DIRECTIONS:
            seconds = _float(item.get("seconds"), 0.8)
            if not (0.05 <= seconds <= 3.0):
                return [], f"seq[{index}] seconds 超出 0.05–3.0"
            total += seconds
            steps.append({"move": direction, "seconds": round(seconds, 2)})
            continue
        return [], f"seq[{index}] 缺少 move / press / wait"
    if total > MAX_TOTAL_SECONDS:
        return [], f"总时长超过 {MAX_TOTAL_SECONDS:.0f} 秒"
    return steps, ""


def _press(client, key: str) -> None:
    if key in ("1", "2", "3", "4", "5"):
        client.choose(int(key) - 1)
        return
    client.act("press", key=key)


def _apply(steps: list[dict[str, Any]], client, guard=None) -> list[str]:
    from .compat.vs import VsError, VsUnavailable

    sent: list[str] = []
    for step in steps:
        # 一个计划可能连续走好几秒；每步都重新确认桥还在，
        # 游戏退出/插件停掉就立刻停手。
        if guard is not None and not guard():
            raise VsUnavailable("VS 本地 API 已断开")
        if "move" in step:
            dx, dy = VECTORS[step["move"]]
            client.move(dx, dy, seconds=float(step["seconds"]),
                        choice=step["move"])
            sent.append(f"move:{step['move']}:{step['seconds']}")
            remaining = float(step["seconds"])
            while remaining > 0:
                chunk = min(0.1, remaining)
                time.sleep(chunk)
                remaining -= chunk
        elif "press" in step:
            _press(client, step["press"])
            sent.append(f"press:{step['press']}")
        else:
            seconds = float(step.get("wait") or 0.2)
            time.sleep(min(2.0, max(0.05, seconds)))
            sent.append(f"wait:{seconds:.2f}")
    return sent


class VampireSurvivorsTools:
    """实现 ``GameTools``；观察和动作都只走本地 API。"""

    def __init__(self, game_id: str = GAME_ID, client=None,
                 stop_check=None, backend=None) -> None:
        self.game_id = game_id
        self._client = client or _client()
        self.stop_check = stop_check
        # ``backend`` 只为旧调用方保留，API 版不再使用键盘/窗口。
        self._legacy_backend = backend

    # ---------------------------------------------------------------- 观察
    def snapshot(self) -> dict[str, Any]:
        from .compat.vs import VsError, VsUnavailable

        try:
            raw = self._client.state()
            api = {"ok": True, "url": self._client.base_url,
                   "version": _int(raw.get("api")),
                   "automation": bool(raw.get("automation"))}
        except (VsUnavailable, VsError) as exc:
            raw = {}
            api = {"ok": False, "url": getattr(self._client, "base_url", ""),
                   "error": str(exc)[:160]}
        except Exception as exc:  # noqa: BLE001
            raw = {}
            api = {"ok": False, "url": getattr(self._client, "base_url", ""),
                   "error": f"{type(exc).__name__}: {exc}"[:160]}
        return context(raw, api)

    def available(self) -> bool:
        try:
            return bool(self._client.available())
        except Exception:
            return False

    def start_run(self) -> dict[str, Any]:
        """Ask the bridge to enable automation and start a run from the menu."""
        return self._client.start()

    def stop_control(self) -> dict[str, Any]:
        """Disable automation and zero movement; best-effort."""
        try:
            return self._client.stop()
        except Exception:
            return {"ok": False}

    def observe(self) -> ActionResult:
        try:
            ctx = self.snapshot()
        except Exception as exc:  # noqa: BLE001
            return ActionResult.failure(
                "observe", code=ActionCode.INTERNAL,
                message=f"{type(exc).__name__}: {exc}")
        if ctx["source"] != "api":
            return ActionResult.failure(
                "observe", code=ActionCode.UNAVAILABLE,
                message="VS 本地 API 未连接（先启动游戏并安装桥插件）",
                data={"context": ctx})
        return ActionResult.success(
            "observe",
            message=(f"{ctx['screen'] or '在线'} · Lv {ctx['level']} · "
                     f"击杀 {ctx['kills']}"),
            state=envelope(ctx),
            data={"context": ctx},
        )

    # ---------------------------------------------------------------- 验算
    def validate(self, plan: dict) -> ActionResult:
        steps, error = _resolve(plan)
        ctx = self.snapshot()
        if error:
            return ActionResult.failure(
                "validate", code=ActionCode.INVALID_PLAN, message=error,
                data={"context": ctx})
        return ActionResult.success(
            "validate", message="计划合法",
            data={"seq": steps, "context": ctx},
        )

    # ---------------------------------------------------------------- 执行
    def execute(self, plan: dict, *, dry_run: bool = True) -> ActionResult:
        from .compat.vs import VsError, VsUnavailable

        steps, error = _resolve(plan)
        ctx = self.snapshot()
        if error:
            return ActionResult.failure(
                "execute", code=ActionCode.INVALID_PLAN, message=error,
                data={"dry_run": bool(dry_run), "context": ctx},
            )
        if dry_run:
            return ActionResult.success(
                "execute", message="预演通过（未发送动作）",
                data={"dry_run": True, "seq": steps, "sent": [],
                      "context": ctx},
            )
        if self.stop_check is not None and self.stop_check():
            return ActionResult.failure(
                "execute", code=ActionCode.UNAVAILABLE, message="已停手",
                data={"context": ctx},
            )
        if ctx["source"] != "api":
            return ActionResult.failure(
                "execute", code=ActionCode.UNAVAILABLE,
                message="VS 本地 API 未连接（先启动游戏并安装桥插件）",
                data={"context": ctx},
            )
        sent: list[str] = []
        try:
            sent = _apply(steps, self._client, guard=self.available)
        except VsUnavailable as exc:
            return ActionResult.failure(
                "execute", code=ActionCode.UNAVAILABLE,
                message=f"VS 本地 API 断开：{exc}"[:200],
                data={"context": self.snapshot(), "sent": sent},
            )
        except VsError as exc:
            return ActionResult.failure(
                "execute", code=ActionCode.UNAVAILABLE,
                message=f"桥拒绝了动作：{exc}"[:200],
                data={"context": self.snapshot(), "sent": sent},
            )
        after = self.snapshot()
        return ActionResult.success(
            "execute",
            message=f"已发送 {len(sent)} 步",
            state=envelope(after),
            data={"dry_run": False, "seq": steps, "sent": sent,
                  "receipt": f"vs-{after.get('level') or 0}-"
                             f"{after.get('kills') or 0}",
                  "context": after},
        )


__all__ = [
    "DIRECTIONS", "GAME_ID", "MAX_STEPS", "PRESS_KEYS",
    "VampireSurvivorsTools", "context", "envelope",
]
