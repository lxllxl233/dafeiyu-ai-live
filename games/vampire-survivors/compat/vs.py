"""Vampire Survivors local API client.

The game-side bridge (``compat/mod``) exposes a tiny HTTP API on loopback:

- ``GET  /state``  -> the current run/menu snapshot
- ``POST /act``    -> movement, menu picks, automation on/off

Like the Slay the Spire 2 compat client, this module is deliberately thin: it
does no keyboard/window work.  Everything the pet does goes through this API,
so the game can stay in the background and never steals focus.
"""

from __future__ import annotations

import os
from typing import Any

import requests

API_ENV = "VAMPIRE_SURVIVORS_API"
DEFAULT_API = "http://127.0.0.1:8091"
API_VERSION = 1


class VsUnavailable(RuntimeError):
    """The VS bridge is not running / not reachable."""


class VsError(RuntimeError):
    """The bridge answered but rejected the request."""


def api_url() -> str:
    raw = os.environ.get(API_ENV) or DEFAULT_API
    return str(raw).rstrip("/")


class VsClient:
    """Thin HTTP client for the in-game bridge."""

    def __init__(self, base_url: str | None = None, timeout: float = 2.0) -> None:
        self.base_url = str(base_url or api_url()).rstrip("/")
        self.timeout = max(0.2, float(timeout))

    # ---------------------------------------------------------------- plumbing
    def _get(self, path: str) -> dict[str, Any]:
        try:
            resp = requests.get(self.base_url + path, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise VsUnavailable(f"VS API {self.base_url}{path}: {exc}") from exc
        except ValueError as exc:
            raise VsError(f"VS API 返回的不是 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise VsError("VS API 返回的不是对象")
        if not data.get("ok", False):
            raise VsError(str(data.get("error") or "VS API 拒绝了请求"))
        return data

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = requests.post(self.base_url + "/act", json=payload,
                                 timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise VsUnavailable(f"VS API POST: {exc}") from exc
        except ValueError as exc:
            raise VsError(f"VS API 返回的不是 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise VsError("VS API 返回的不是对象")
        if not data.get("ok", False):
            raise VsError(str(data.get("error") or "VS API 拒绝了动作"))
        return data

    # ---------------------------------------------------------------- public
    def state(self) -> dict[str, Any]:
        """Return the full bridge snapshot; raises ``VsUnavailable`` when down."""
        return self._get("/state")

    def available(self) -> bool:
        try:
            data = self.state()
        except (VsUnavailable, VsError):
            return False
        return bool(data.get("ready", True))

    def act(self, kind: str, **fields: Any) -> dict[str, Any]:
        body = {"type": str(kind)}
        body.update({k: v for k, v in fields.items() if v is not None})
        return self._post(body)

    def move(self, dx: float, dy: float, seconds: float = 0.5,
             choice: str = "") -> dict[str, Any]:
        return self.act("move", dx=round(float(dx), 3), dy=round(float(dy), 3),
                        seconds=round(max(0.05, min(3.0, float(seconds))), 2),
                        choice=str(choice or "")[:40])

    def choose(self, index: int) -> dict[str, Any]:
        return self.act("choose", index=max(0, int(index)))

    def control(self, automation: bool) -> dict[str, Any]:
        return self.act("control", automation=bool(automation))

    def start(self) -> dict[str, Any]:
        return self.act("start")

    def stop(self) -> dict[str, Any]:
        return self.act("stop")


__all__ = [
    "API_ENV", "API_VERSION", "DEFAULT_API", "VsClient", "VsError",
    "VsUnavailable", "api_url",
]
