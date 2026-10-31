"""启动本地曼波引擎（MamboTTS / GPT-SoVITS）。

直播时引擎可能没起。语音菜单 / 控制台点「启动」时调用这里：只负责把
`run_engine.py` 拉起来，不阻塞 UI；真正的就绪状态仍由 MamboTTS 的
`/control` 与合成结果判断。

引擎目录优先级：调用方传入 > `DAFEIYU_MAMBO_DIR`（或游戏包声明的旧前缀）>
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
from ..games.catalog import env_value

DEFAULT_ENGINE_DIR = r"D:\MamboTTS\app\MamboTTS-v1.2.1-full"
PID_FILE = paths.LOGS / "mambo_engine.pid"
_start_lock = threading.Lock()
_started: dict = {"at": 0.0, "pid": 0, "dir": "", "message": ""}


def engine_dir(configured: str = "") -> Path:
    """找 MamboTTS 根目录；不存在也返回候选路径，交给 start() 报错。"""
    for value in (configured, env_value("MAMBO_DIR", ""), DEFAULT_ENGINE_DIR):
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
        try:
            PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            PID_FILE.write_text(str(proc.pid), encoding="utf-8")
        except OSError:
            pass
        return {"ok": True, "online": False, "starting": True,
                "pid": proc.pid, "dir": str(root), "message": msg}


def start_from_config(cfg=None) -> dict:
    """按 LiveConfig 启动；给桌宠/控制台共用。"""
    configured = getattr(cfg, "mambo_dir", "") if cfg is not None else ""
    url = getattr(cfg, "tts_url", None) if cfg is not None else None
    return start_mambo(str(configured or ""), str(url or "http://127.0.0.1:9880"))


# ---------------------------------------------------------------- 关闭
def _process_name(pid: int) -> str:
    """Windows 下查 pid 的映像名；查不到返回空串。"""
    if os.name != "nt" or int(pid or 0) <= 0:
        return ""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=4, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    first = out.strip().splitlines()[0] if out.strip() else ""
    name = first.split(",")[0].strip().strip('"') if first else ""
    return name if name and name.lower() != "info:" else ""


def _pid_alive(pid: int) -> bool:
    return bool(_process_name(pid))


def _port_owner(port: int) -> int:
    """找监听该端口的 pid（Windows netstat）；找不到返回 0。"""
    if os.name != "nt":
        return 0
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if "LISTENING" not in line.upper():
            continue
        local, pid = parts[1], parts[-1]
        if local.rsplit(":", 1)[-1] == str(int(port)):
            try:
                return int(pid)
            except ValueError:
                return 0
    return 0


def _kill_tree(pid: int) -> bool:
    if not _pid_alive(pid):
        return False
    try:
        subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def stop_mambo(url: str = "http://127.0.0.1:9880", timeout: float = 8.0) -> dict:
    """关掉本机曼波引擎，释放内存；只杀 python 进程，避免 pid 复用误伤。"""
    if not engine_online(url):
        _clear_pid()
        return {"ok": True, "stopped": False, "online": False,
                "message": "曼波引擎本来就没在运行"}
    if os.name != "nt":
        return {"ok": False, "stopped": False, "online": True,
                "error": "只有 Windows 支持从桌宠关闭引擎"}

    candidates: list[int] = []
    try:
        raw = PID_FILE.read_text(encoding="utf-8").strip()
        if raw.isdigit():
            candidates.append(int(raw))
    except OSError:
        pass
    if _started.get("pid"):
        candidates.append(int(_started["pid"]))
    try:
        port = int(url.rsplit(":", 1)[-1].split("/")[0])
    except ValueError:
        port = 9880
    port_pid = _port_owner(port)
    if port_pid:
        candidates.append(port_pid)

    killed: list[int] = []
    for pid in dict.fromkeys(candidates):
        name = _process_name(pid).lower()
        if not name or "python" not in name:
            continue        # 不是 python：可能是别的服务占了这个端口，不动
        if _kill_tree(pid):
            killed.append(int(pid))

    if not killed:
        return {"ok": False, "stopped": False, "online": True,
                "error": "找不到曼波引擎进程（可能不是本机拉起的）"}

    deadline = time.time() + max(1.0, float(timeout or 8.0))
    while time.time() < deadline:
        if not engine_online(url, timeout=0.4):
            break
        time.sleep(0.25)
    online = engine_online(url, timeout=0.5)
    _clear_pid()
    _started.update(at=0.0, pid=0, dir="", message="")
    if online:
        return {"ok": False, "stopped": bool(killed), "online": True,
                "pid": killed[-1], "error": "进程已结束，但端口还在响应，可能还有别的引擎实例"}
    return {"ok": True, "stopped": True, "online": False,
            "pid": killed[-1], "message": "曼波引擎已关闭，内存已释放"}


def _clear_pid() -> None:
    try:
        PID_FILE.unlink()
    except OSError:
        pass


def stop_from_config(cfg=None) -> dict:
    url = getattr(cfg, "tts_url", None) if cfg is not None else None
    return stop_mambo(str(url or "http://127.0.0.1:9880"))


__all__ = ["DEFAULT_ENGINE_DIR", "engine_dir", "engine_online", "start_mambo",
           "start_from_config", "stop_from_config", "stop_mambo"]
