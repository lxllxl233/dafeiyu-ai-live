# -*- coding: utf-8 -*-
"""手动开关 OBS 录像：桌宠右键「OBS 录像」用的那一层。

和 ``auto.py`` 的分工：自动玩是「按局」录像——建 run 目录、写 meta、收尾时间轴；
这一层只回答一个问题：**现在要不要录**。

- 没在录就开录，正在录就停掉；同一个入口既是开也是关；
- 停的时候，如果这一局是 ``replay`` 开的（``recordings/.current.json`` 还指着它、
  meta 里还是 recording），顺手按回放目录收尾，视频照样落到 ``run-NNNN/run.mp4``；
- 连不上 OBS 就把原因说人话（OBS 没开 / WebSocket 没开 / 密码不对），不往 UI 抛异常。
"""
from __future__ import annotations

from . import replay
from .obs import DEFAULT_WS, Obs, ObsError, load_ws_config

__all__ = ["probe", "toggle"]


def _connect(timeout: float = 2.0) -> Obs:
    cfg = load_ws_config()
    return Obs(str(cfg.get("url") or DEFAULT_WS),
               str(cfg.get("password") or ""), timeout=timeout).connect()


def _why(exc: BaseException) -> str:
    """把 WebSocket / OBS 的异常翻成一句能照着做的话（不暴露密码）。"""
    text = str(exc) or type(exc).__name__
    low = text.lower()
    if isinstance(exc, ObsError):
        return f"OBS 报错：{text}"
    if isinstance(exc, (ConnectionRefusedError, ConnectionError)) or "refused" in low:
        return "连不上 OBS：先把 OBS 打开（工具 → WebSocket 服务器 要开着）"
    if "auth" in low:
        return "OBS 拒绝登录：WebSocket 密码和 obs_config.json 对不上"
    if isinstance(exc, TimeoutError) or "timed out" in low or "timeout" in low:
        return "OBS 没响应：WebSocket 端口被占，或者 OBS 卡住了"
    return f"{type(exc).__name__}: {text}"


def probe(timeout: float = 2.0) -> dict:
    """OBS 现在在不在录；连不上也返回 ok=False，不抛异常。"""
    try:
        obs = _connect(timeout)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "recording": False, "message": _why(exc)}
    try:
        state = obs.status() or {}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "recording": False, "message": _why(exc)}
    finally:
        obs.close()
    active = bool(state.get("outputActive"))
    return {
        "ok": True,
        "recording": active,
        "output_path": str(state.get("outputPath") or ""),
        "message": "OBS 正在录像" if active else "OBS 没在录像",
    }


def toggle(timeout: float = 2.0) -> dict:
    """切一下：在录就停、没录就开。``recording`` 是切换之后的状态。"""
    now = probe(timeout)
    if not now.get("ok"):
        return {"ok": False, "recording": None, "action": "",
                "message": now.get("message") or "连不上 OBS"}
    if now.get("recording"):
        return _stop()
    return _start(timeout)


def _start(timeout: float) -> dict:
    try:
        obs = _connect(timeout)
        try:
            obs.start_record()
        finally:
            obs.close()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "recording": False, "action": "start",
                "message": "开录失败：" + _why(exc)}
    return {"ok": True, "recording": True, "action": "start",
            "message": "OBS 开始录像"}


def _stop() -> dict:
    run_dir = _active_run()
    if run_dir is not None:
        try:
            dst = replay.stop_recording(run_dir, result="manual")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "recording": True, "action": "stop",
                    "message": "停录失败：" + _why(exc)}
        for step in (replay.clear_current, replay.write_index):
            try:
                step()
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "recording": False, "action": "stop",
                "path": str(dst), "message": f"已停止录像：{dst.name}"}

    try:
        out = replay.stop_recording_now()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "recording": True, "action": "stop",
                "message": "停录失败：" + _why(exc)}
    return {"ok": True, "recording": False, "action": "stop", "path": out,
            "message": f"已停止录像：{out}" if out else "已停止录像"}


def _active_run():
    """当前指针是不是指向一条「还在录」的回放目录。"""
    try:
        run_dir = replay.get_current()
        if not run_dir:
            return None
        if str(replay.read_meta(run_dir).get("status") or "") != "recording":
            return None
        return run_dir
    except Exception:  # noqa: BLE001
        return None
