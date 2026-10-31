"""KOOK 聊天常驻进程的看护：桌宠启动时按配置拉起，退出时收掉。

KOOK 的 WebSocket 是一个独立进程（``python main.py kook``）；这里用
``logs/kook/service.lock`` 做互斥，lock 只记 pid/时间，不记 token。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ... import paths
from ...ui import instance_lock

SERVICE_DIR = paths.LOGS / "kook"
SERVICE_LOCK = SERVICE_DIR / "service.lock"
SERVICE_LOG = SERVICE_DIR / "service.log"


def _read_lock(path: Path) -> dict | None:
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def service_owner(path: str | Path = SERVICE_LOCK, alive=None) -> dict | None:
    """返回仍活着的服务进程锁；没有/已死返回 None。"""
    path = Path(path)
    info = _read_lock(path)
    if not info:
        return None
    try:
        pid = int(info.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    alive = alive or instance_lock.pid_alive
    if pid > 0 and alive(pid):
        return info
    return None


def acquire_service_lock(path: str | Path = SERVICE_LOCK, pid: int | None = None,
                         alive=None) -> bool:
    """抢占服务锁；已有活着的进程返回 False，陈旧锁会被替换。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid() if pid is None else int(pid)
    alive = alive or instance_lock.pid_alive
    for _ in range(2):
        info = _read_lock(path)
        if info:
            try:
                old = int(info.get("pid") or 0)
            except (TypeError, ValueError):
                old = 0
            if old and old != pid and alive(old):
                return False
            try:
                path.unlink()
            except OSError:
                pass
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        except OSError:
            return False
        try:
            os.write(fd, json.dumps({"pid": pid, "at": int(time.time())}).encode("utf-8"))
        finally:
            os.close(fd)
        return True
    return False


def release_service_lock(path: str | Path = SERVICE_LOCK, pid: int | None = None) -> None:
    """退出时只清自己的锁，不碰别的进程。"""
    path = Path(path)
    info = _read_lock(path)
    pid = os.getpid() if pid is None else int(pid)
    if info and int(info.get("pid") or 0) == pid:
        try:
            path.unlink()
        except OSError:
            pass


class KookSupervisor:
    """桌宠持有的 KOOK 服务看护器（Windows 下无窗口子进程）。"""

    def __init__(self, root: str | Path | None = None, python: str | None = None,
                 spawn=None, clock=time.monotonic,
                 lock_path: str | Path = SERVICE_LOCK,
                 log_path: str | Path = SERVICE_LOG, alive=None) -> None:
        self.root = Path(root or paths.ROOT)
        self.python = python or sys.executable
        self._spawn = spawn or subprocess.Popen
        self._clock = clock
        self._lock_path = Path(lock_path)
        self._log_path = Path(log_path)
        self._alive = alive
        self._proc = None
        self._last_start = 0.0
        self._retry_after = 8.0
        self.last_error = ""

    @property
    def pid(self) -> int:
        try:
            return int(self._proc.pid) if self._proc is not None else 0
        except (AttributeError, TypeError):
            return 0

    def running(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        return service_owner(self._lock_path, alive=self._alive) is not None

    def tick(self, cfg=None) -> None:
        """按当前配置决定拉起/停止；桌宠每 1.2s 调一次。"""
        try:
            if cfg is None:
                from ...core.config import LiveConfig

                cfg = LiveConfig.load()
            want = bool(getattr(cfg, "kook_enabled", False)
                        and getattr(cfg, "kook_bot_token", ""))
        except Exception as exc:  # noqa: BLE001 - 配置读失败不能拖垮桌宠
            self.last_error = f"{type(exc).__name__}: {exc}"
            return
        if not want:
            self.stop()
            return
        if self._proc is not None and self._proc.poll() is None:
            return
        self._proc = None
        if service_owner(self._lock_path, alive=self._alive) is not None:
            return
        if self._clock() - self._last_start < self._retry_after:
            return
        self.start()

    def start(self) -> bool:
        if service_owner(self._lock_path, alive=self._alive) is not None:
            return False
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        log = None
        try:
            log = open(self._log_path, "ab", buffering=0)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            self._proc = self._spawn(
                [self.python, str(self.root / "main.py"), "kook"],
                cwd=str(self.root), stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=flags, close_fds=(os.name != "nt"),
            )
            self._last_start = self._clock()
            self.last_error = ""
            print(f"KOOK 监控：已拉起（pid={self._proc.pid}）", flush=True)
            return True
        except Exception as exc:  # noqa: BLE001
            self._proc = None
            self._last_start = self._clock()
            self.last_error = f"{type(exc).__name__}: {exc}"
            print(f"KOOK 监控启动失败：{self.last_error}", flush=True)
            return False
        finally:
            if log is not None:
                try:
                    log.close()
                except OSError:
                    pass

    def stop(self) -> None:
        """只收自己拉起的子进程；手动开的服务留着。"""
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:
                pass
        print("KOOK 监控：已停止", flush=True)


__all__ = [
    "KookSupervisor",
    "acquire_service_lock",
    "release_service_lock",
    "service_owner",
]
