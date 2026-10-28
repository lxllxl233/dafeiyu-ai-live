"""启动本地曼波引擎（MamboTTS / GPT-SoVITS）。

直播时引擎可能没起。语音菜单 / 控制台点「启动」时调用这里：只负责把
`run_engine.py` 拉起来，不阻塞 UI；真正的就绪状态仍由 MamboTTS 的
`/control` 与合成结果判断。

引擎目录优先级：调用方传入 > `DAFEIYU_MAMBO_DIR` > `STS2_MAMBO_DIR` >
默认的 `D:\\MamboTTS\\app\\MamboTTS-v1.2.1-full`。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from .. import paths

DEFAULT_ENGINE_DIR = r"D:\MamboTTS\app\MamboTTS-v1.2.1-full"
_start_lock = threading.Lock()
_started: dict = {"at": 0.0, "pid": 0, "dir": "", "message": ""}


def engine_dir(configured: str = "") -> Path:
    """找 MamboTTS 根目录；不存在也返回候选路径，交给 start() 报错。"""
    for value in (configured, os.environ.get("DAFEIYU_MAMBO_DIR"),
                  os.environ.get("STS2_MAMBO_DIR"), DEFAULT_ENGINE_DIR):
        if value and str(value).strip():
            return Path(str(value).strip()).expanduser()
    return Path(DEFAULT_ENGINE_DIR)


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def engine_online(url: str = "http://127.0.0.1:9880", timeout: float = 1.5) -> bool:
    """只探 /control；合成是否假活由合成路径自己判断。"""
    try:
        with _opener().open(str(url).rstrip("/") + "/control", timeout=timeout) as r:
            r.read(64)
        return True
    except Exception:
        return False


def start_mambo(configured_dir: str = "",
                url: str = "http://127.0.0.1:9880") -> dict:
    """把曼波引擎拉到后台；10 秒内重复点击复用一个启动结果。"""
    if engine_online(url):
        return {"ok": True, "online": True, "starting": False,
                "message": "曼波引擎已在运行"}

    with _start_lock:
        root = engine_dir(configured_dir)
        # 双击菜单/连点按钮时不重复拉起进程；换目录则重新判断。
        if (time.time() - float(_started.get("at") or 0.0) < 10.0
                and _started.get("dir") == str(root)):
            return {"ok": True, "online": False, "starting": True,
                    "pid": _started.get("pid") or 0,
                    "message": _started.get("message") or "曼波引擎正在启动…"}

        script = root / "run_engine.py"
        if not script.is_file():
            msg = f"找不到曼波引擎启动脚本：{script}"
            _started.update(at=0.0, pid=0, dir=str(root), message=msg)
            return {"ok": False, "online": False, "starting": False, "message": msg}

        try:
            paths.LOGS.mkdir(parents=True, exist_ok=True)
            out = open(paths.LOGS / "mambo_engine.out.log", "ab")
            err = open(paths.LOGS / "mambo_engine.err.log", "ab")
            proc = subprocess.Popen(
                [sys.executable, "run_engine.py"],
                cwd=str(root), stdout=out, stderr=err,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            msg = f"曼波引擎启动失败：{type(exc).__name__}: {exc}"
            _started.update(at=0.0, pid=0, dir=str(root), message=msg)
            return {"ok": False, "online": False, "starting": False, "message": msg}

        msg = f"已启动曼波引擎（pid {proc.pid}），等它就绪…"
        _started.update(at=time.time(), pid=proc.pid, dir=str(root), message=msg)
        return {"ok": True, "online": False, "starting": True,
                "pid": proc.pid, "dir": str(root), "message": msg}


def start_from_config(cfg=None) -> dict:
    """按 LiveConfig 启动；给桌宠/控制台共用。"""
    configured = getattr(cfg, "mambo_dir", "") if cfg is not None else ""
    url = getattr(cfg, "tts_url", None) if cfg is not None else None
    return start_mambo(str(configured or ""), str(url or "http://127.0.0.1:9880"))


__all__ = ["DEFAULT_ENGINE_DIR", "engine_dir", "engine_online", "start_mambo",
           "start_from_config"]
