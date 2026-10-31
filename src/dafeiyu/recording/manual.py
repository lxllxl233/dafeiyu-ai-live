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

from . import policy, replay
from .obs import DEFAULT_WS, Obs, ObsError, load_ws_config

__all__ = ["probe", "toggle", "toggle_game", "start_game", "stop_game",
           "ensure_game", "status"]


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


# ---------------------------------------------------------------- 按游戏策略
def _auto():
    from . import auto

    return auto


def status(game: str = "", timeout: float = 1.5) -> dict:
    """OBS 当前状态 + 本进程录像会话 + 该游戏策略/脚本，给控制台和右键菜单。"""
    now = probe(timeout)
    session = _auto().status()
    pol = policy.policy_for(game)
    return {
        "ok": bool(now.get("ok")),
        "recording": bool(now.get("recording")),
        "output_path": str(now.get("output_path") or ""),
        "message": str(now.get("message") or ""),
        "master": policy.master_enabled(),
        "session": session,
        "policy": pol,
        "script": policy.script_state(game),
    }


def ensure_game(game: str) -> dict:
    """检查/创建游戏 OBS 场景脚本，并把场景和采集源补齐。"""
    try:
        script = policy.ensure_script(game)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    scene_ok, scene_why = _auto().prepare_scene(game)
    return {
        "ok": True,
        "script": script,
        "scene_ok": scene_ok,
        "scene_error": "" if scene_ok else scene_why,
        "message": ("已创建录制脚本" if script.get("created")
                    else "录制脚本已存在")
                   + ("" if scene_ok else f"；场景源未就绪：{scene_why}"),
    }


def start_game(game: str = "", *, force: bool = True) -> dict:
    """按该游戏策略开录；``force`` 是手动开关，绕过每游戏默认关闭。"""
    gid = replay.game_id(game)
    pol = policy.policy_for(gid)
    try:
        out = _auto().start_session(
            gid, note="手动 OBS 录像", force=force,
            split=str(pol.get("split") or "none"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "recording": False, "action": "start",
                "message": "开录失败：" + _why(exc)}
    if not out.get("ok"):
        return {"ok": False, "recording": False, "action": "start",
                "message": str(out.get("error") or "开录失败"),
                "script": out.get("script")}
    if force:
        try:
            policy.set_master(True)
        except Exception:  # noqa: BLE001
            pass
    return {
        "ok": True, "recording": True, "action": "start",
        "path": str(out.get("path") or ""),
        "kind": str(out.get("kind") or pol.get("split") or "run"),
        "script": out.get("script"),
        "message": str(out.get("message") or "OBS 开始录像"),
    }


def stop_game(result: str = "manual") -> dict:
    """停止当前录像；有本进程会话就按 run/continuous 收尾，否则走旧兜底。"""
    active = _auto().status()
    if active.get("active"):
        try:
            out = _auto().stop_session(result=result)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "recording": True, "action": "stop",
                    "message": "停录失败：" + _why(exc)}
        if out.get("ok"):
            try:
                policy.set_master(False)
            except Exception:  # noqa: BLE001
                pass
        out.update({"recording": False, "action": "stop"})
        out.setdefault("message", "已停止录像")
        return out
    out = _stop()
    if out.get("ok"):
        try:
            policy.set_master(False)
        except Exception:  # noqa: BLE001
            pass
    return out


def toggle_game(game: str = "", timeout: float = 2.0) -> dict:
    """桌宠右键「OBS 录像」：按当前游戏策略开 / 关。"""
    now = probe(timeout)
    # OBS 没开时 probe 会失败；不要在这里直接放弃，交给 start_game 的
    # ensure_obs 拉起 OBS（和自动玩同一条路径）。
    if now.get("ok") and now.get("recording"):
        return stop_game()
    return start_game(game, force=True)
