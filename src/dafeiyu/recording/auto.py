"""自动玩录像：OBS 没开就尝试拉起，开始/结束一局录制。

这层只做「会话生命周期」，文件命名和 OBS 细节仍复用 ``replay`` / ``obs``。
自动玩默认录像；设 ``DAFEIYU_AUTOPLAY_RECORD=0`` 可关，OBS 路径可用
``STS2_OBS_EXE`` 覆盖。
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from .. import paths
from . import replay

_lock = threading.RLock()
_session: dict | None = None


def _enabled() -> bool:
    return os.environ.get("DAFEIYU_AUTOPLAY_RECORD", "1").lower() not in (
        "0", "false", "no", "off"
    )


def _connect(timeout: float = 1.0):
    from .obs import Obs, load_ws_config

    cfg = load_ws_config()
    return Obs(cfg.get("url", ""), cfg.get("password", ""), timeout=timeout).connect()


def _obs_exe() -> str:
    cands = [
        os.environ.get("STS2_OBS_EXE", ""),
        r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
        r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe",
    ]
    for c in cands:
        if c and Path(c).is_file():
            return c
    return ""


def _launch_obs(exe: str) -> None:
    # OBS 支持 --minimize-to-tray；--disable-shutdown-check 避免上次强退留下的
    # 崩溃恢复弹窗把 WebSocket 卡在后台。
    subprocess.Popen(
        [exe, "--disable-shutdown-check", "--minimize-to-tray"],
        cwd=str(Path(exe).parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def ensure_obs(timeout: float = 20.0) -> tuple[bool, str]:
    """OBS 已在跑就直接用；没跑就尝试拉起并等 WebSocket 就绪。"""
    try:
        _connect(1.0).close()
        return True, ""
    except Exception:
        pass
    if not _enabled():
        return False, "自动录像已关闭（DAFEIYU_AUTOPLAY_RECORD=0）"
    exe = _obs_exe()
    if not exe:
        return False, "找不到 OBS（安装 OBS 或设 STS2_OBS_EXE）"
    try:
        _launch_obs(exe)
    except Exception as exc:  # noqa: BLE001
        return False, f"启动 OBS 失败：{type(exc).__name__}: {exc}"
    end = time.time() + max(3.0, float(timeout))
    while time.time() < end:
        time.sleep(0.8)
        try:
            _connect(1.0).close()
            time.sleep(0.6)  # 让 OBS 把场景/输出加载完
            return True, "已自动启动 OBS"
        except Exception:
            continue
    return False, f"OBS 已启动但 {timeout:.0f}s 内 WebSocket 未就绪"


def _prepare_scene(game: str) -> tuple[bool, str]:
    """确保 OBS 有游戏画面源；用户已有的场景不会被删除。"""
    try:
        from .obs import Obs, load_profile, load_ws_config

        cfg = load_ws_config()
        profile = load_profile(game)
        obs = Obs(cfg.get("url", ""), cfg.get("password", ""), profile=profile).connect()
        try:
            obs.ensure_scene(profile.scene)
            obs.ensure_game_capture(profile.scene)
            obs.ensure_audio(profile.scene)
            obs.free_mode("game")
        finally:
            obs.close()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def start_session(game: str = "slay-the-spire-2", note: str = "") -> dict:
    """开一局自动玩录像；失败返回 ok=False，不阻断自动玩。"""
    global _session
    with _lock:
        if _session and _session.get("active"):
            return {"ok": True, "already": True, "path": _session.get("path", "")}
        if not _enabled():
            return {"ok": False, "error": "自动录像已关闭"}
        ok, why = ensure_obs()
        if not ok:
            return {"ok": False, "error": why}
        scene_ok, scene_why = _prepare_scene(game)
        run_dir = replay.create_run(game, note=note or "自动玩")
        try:
            replay.set_current(run_dir)
            replay.start_recording(run_dir)
        except Exception as exc:  # noqa: BLE001
            replay.write_meta(run_dir, status="record_failed",
                              error=f"{type(exc).__name__}: {exc}")
            try:
                replay.clear_current()
            except Exception:
                pass
            return {"ok": False, "error": f"OBS 开录失败：{type(exc).__name__}: {exc}",
                    "path": str(run_dir)}
        _session = {"active": True, "path": str(run_dir), "started_at": time.time(),
                    "game": game}
        replay.write_meta(run_dir, status="recording", source="autoplay",
                          scene_error="" if scene_ok else scene_why,
                          started_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        return {"ok": True, "path": str(run_dir),
                "warning": "" if scene_ok else f"场景源未完全就绪：{scene_why}"}


def stop_session(result: str = "") -> dict:
    """结束本进程开的录像；不会误停用户手动开的 OBS 录制。"""
    global _session
    with _lock:
        sess = _session
        _session = None
    if not sess or not sess.get("active"):
        return {"ok": True, "active": False}
    run_dir = Path(str(sess.get("path") or ""))
    if not run_dir.is_dir():
        return {"ok": False, "error": f"录像目录不存在：{run_dir}"}
    try:
        dst = replay.stop_recording(run_dir, result=result or "")
    except Exception as exc:  # noqa: BLE001
        # OBS 已自己停掉/连接断开时，已有文件仍可收尾。
        try:
            dst = replay.finalize(run_dir, None, result=result or "", force=True)
        except Exception as exc2:  # noqa: BLE001
            return {"ok": False, "error": f"停录失败：{type(exc).__name__}: {exc}; "
                                          f"收尾失败：{type(exc2).__name__}: {exc2}",
                    "path": str(run_dir)}
    try:
        replay.clear_current()
        replay.write_index()
    except Exception:
        pass
    try:
        size = dst.stat().st_size
    except OSError:
        size = 0
    return {"ok": True, "active": False, "path": str(dst),
            "size": replay.human_size(size)}


def status() -> dict:
    with _lock:
        if not _session:
            return {"active": False}
        return {"active": True, "path": _session.get("path", ""),
                "started_at": _session.get("started_at", 0)}


__all__ = ["ensure_obs", "start_session", "stop_session", "status"]
