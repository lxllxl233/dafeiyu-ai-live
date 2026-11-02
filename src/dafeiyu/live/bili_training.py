#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 站摸鱼训练进程的启动/停止/状态（设置页和桌宠共用）。

训练是独立进程：``python main.py train``。这样设置页关掉、桌宠重启都不
会影响正在刷的视频；状态文件同时给设置页轮询和桌宠气泡用。压缩记忆另起
``python main.py train --compress``，和扫描互不阻塞。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .. import paths

ROOT = paths.LOGS / "bili_training"
STATUS_FILE = ROOT / "status.json"
STOP_FILE = ROOT / "stop"
PID_FILE = ROOT / "train.pid"
RUN_LOG = ROOT / "run.log"
USAGE_FILE = ROOT / "usage.jsonl"
# 压缩记忆是独立进程：和扫描/训练分开，扫描还在跑时也能压缩当前快照。
COMPRESS_STATUS_FILE = ROOT / "compress_status.json"
COMPRESS_STOP_FILE = ROOT / "compress.stop"
COMPRESS_PID_FILE = ROOT / "compress.pid"
COMPRESS_LOG = ROOT / "compress.log"
_MAX_USAGE_STEPS = 4000


def _atomic_json(path: Path, value: dict) -> bool:
    """原子写状态；Windows 上读进程可能短暂占着目标文件，失败只重试不抛。

    状态文件每秒都在更新，训练学习本身绝不能因为一次状态写失败就退出。
    临时文件名必须唯一，避免设置页/桌宠/训练进程同时写时互相踩 ``.tmp``。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(value, ensure_ascii=False, indent=1) + "\n"
    except (OSError, TypeError, ValueError):
        return False
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                        dir=str(path.parent))
    except OSError:
        return False
    tmp = Path(tmp_name)
    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            return False
        for attempt in range(8):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                # Windows：目标正被另一个读/写进程打开。等它松开。
                time.sleep(0.04 * (attempt + 1))
            except OSError:
                time.sleep(0.02 * (attempt + 1))
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _read_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # 绝不能用 os.kill(pid, 0)：Windows 上它可能走 TerminateProcess 而不是探活。
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED
            if not handle:
                return False
            try:
                code = ctypes.c_ulong(0)
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return int(code.value) == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:  # noqa: BLE001
        return False


def status() -> dict:
    """当前训练状态；进程已经不在时把 running 纠正为 false。"""
    data = _read_json(STATUS_FILE)
    if not data:
        return {"running": False, "phase": "idle", "idle_text": "吃饭ing"}
    pid = int(data.get("pid") or 0)
    if data.get("running") and pid and not _alive(pid):
        data["running"] = False
        data["phase"] = "stopped"
        data["idle_text"] = "吃饭ing"
        data["error"] = data.get("error") or "训练进程已退出"
        _atomic_json(STATUS_FILE, data)
    return data


def write_status(**fields) -> dict:
    data = _read_json(STATUS_FILE)
    data.update({k: v for k, v in fields.items() if v is not None})
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_json(STATUS_FILE, data)   # best-effort；失败不影响训练
    return data


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _epoch(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def token_usage(limit: int = 40, path: str | os.PathLike[str] | None = None) -> dict:
    """读 B 站训练账本，结构对齐自动玩/KOOK，供控制台 Agent 流程合并。"""
    target = Path(path) if path else USAGE_FILE
    empty = {
        "total": 0, "input": 0, "output": 0, "calls": 0, "last": 0,
        "steps": [], "kinds": [], "by_run": [],
        "source": "bili_training", "label": "B站训练",
        "path": str(target), "paths": [str(target)],
    }
    steps: list[dict] = []
    total = total_in = total_out = 0
    try:
        with target.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
                tin = _int(row.get("input")) or _int(usage.get("input"))
                tout = _int(row.get("output")) or _int(usage.get("output"))
                value = _int(row.get("total")) or _int(usage.get("total")) or tin + tout
                if value <= 0:
                    continue
                at = _epoch(row.get("at") or row.get("ts"))
                title = str(row.get("title") or "").strip()
                note = str(row.get("note") or "").strip()
                if not note:
                    note = f"训练《{title[:36]}》" if title else "B站训练"
                total += value
                total_in += tin
                total_out += tout
                steps.append({
                    "at": at,
                    "kind": str(row.get("kind") or "B站训练"),
                    "note": note,
                    "model": str(row.get("model") or ""),
                    "input": tin,
                    "output": tout,
                    "total": value,
                    "ms": _int(row.get("ms")),
                    "bvid": str(row.get("bvid") or ""),
                    "source": "bili_training",
                })
    except OSError:
        return empty
    steps.sort(key=lambda row: row["at"])
    running = total
    for row in reversed(steps):
        row["cumulative"] = running
        running -= row["total"]
    kinds: dict[str, dict] = {}
    for row in steps:
        slot = kinds.setdefault(row["kind"],
                                {"kind": row["kind"], "total": 0, "calls": 0})
        slot["total"] += row["total"]
        slot["calls"] += 1
    empty.update({
        "total": total,
        "input": total_in,
        "output": total_out,
        "calls": len(steps),
        "last": steps[-1]["total"] if steps else 0,
        "steps": steps[-max(1, int(limit or 40)):],
        "kinds": sorted(kinds.values(), key=lambda item: -item["total"]),
        "by_run": ([{
            "run_id": "bili_training",
            "total": total,
            "calls": len(steps),
            "last_at": steps[-1]["at"] if steps else 0.0,
            "note": "B站摸鱼训练",
        }] if steps else []),
    })
    return empty


def request_stop() -> dict:
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        STOP_FILE.write_text("stop\n", encoding="utf-8")
    except OSError:
        pass
    write_status(phase="stopping", note="收到停止请求")
    return status()


def clear_stop() -> None:
    try:
        STOP_FILE.unlink()
    except OSError:
        pass


def should_stop() -> bool:
    return STOP_FILE.exists()


def start(*, persona: str = "", keywords: str = "",
          favorites_id: int = 0, limit: int | None = None) -> dict:
    """起一个独立训练进程；已经在跑就不重复起。

    ``favorites_id`` > 0 时走快速扫收藏夹：不等待视频时长，也不点赞/评论；
    ``limit`` 是本次最多新增多少条（0 = 整个收藏夹，None = 用配置值）。
    """
    current = status()
    if current.get("running"):
        return {"ok": False, "error": "训练已经在跑", "status": current}
    ROOT.mkdir(parents=True, exist_ok=True)
    clear_stop()
    cmd = [sys.executable, str(paths.ROOT / "main.py"), "train"]
    if persona:
        cmd.extend(["--persona", persona])
    if favorites_id:
        cmd.extend(["--favorites", str(int(favorites_id))])
        if limit is not None:
            cmd.extend(["--limit", str(max(0, int(limit)))])
    elif keywords:
        cmd.extend(["--keywords", keywords])
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # 训练不打开任何窗口、不抢焦点；输出只落 run.log 供排查。
    flags = 0
    if os.name == "nt":
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log = open(RUN_LOG, "a", encoding="utf-8")  # noqa: SIM115
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(paths.ROOT), env=env, stdout=log, stderr=log,
            stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.close()
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    log.close()  # 子进程已经拿到自己的句柄，父进程这份可以关掉
    try:
        PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass
    write_status(running=True, pid=proc.pid, phase="starting",
                 idle_text="摸鱼ing", started_at=time.time(),
                 mode=("favorites" if favorites_id else "training"),
                 media_id=int(favorites_id or 0), folder="",
                 total=0, done=0, scanned=0, skipped=0, failed=0, remaining=0,
                 last=("准备扫收藏夹…" if favorites_id else "准备摸鱼刷 B 站…"),
                 error="", bvid="", title="", url="", up="", duration_s=0,
                 position_s=0, remain_s=0, like_status="", like_error="",
                 comment_status="", comment_error="")
    return {"ok": True, "pid": proc.pid, "status": status()}


def stop(*, wait_s: float = 6.0) -> dict:
    current = status()
    if not current.get("running"):
        return {"ok": True, "stopped": True, "status": current}
    request_stop()
    deadline = time.time() + max(0.5, float(wait_s))
    while time.time() < deadline:
        time.sleep(0.25)
        current = status()
        if not current.get("running"):
            return {"ok": True, "stopped": True, "status": current}
    return {"ok": True, "stopping": True, "status": current}


# ---------------------------------------------------------------- 压缩记忆
def compress_status() -> dict:
    """当前压缩进程状态；进程不在时把 running 纠正为 false。"""
    data = _read_json(COMPRESS_STATUS_FILE)
    if not data:
        return {"running": False, "phase": "idle", "entries": 0, "chunks": 0,
                "done_chunks": 0, "chars": 0, "last": "", "error": ""}
    pid = int(data.get("pid") or 0)
    if data.get("running") and pid and not _alive(pid):
        data["running"] = False
        data["phase"] = "stopped"
        data["error"] = data.get("error") or "压缩进程已退出"
        _atomic_json(COMPRESS_STATUS_FILE, data)
    return data


def write_compress_status(**fields) -> dict:
    data = _read_json(COMPRESS_STATUS_FILE)
    data.update({k: v for k, v in fields.items() if v is not None})
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_json(COMPRESS_STATUS_FILE, data)
    return data


def clear_compress_stop() -> bool:
    try:
        COMPRESS_STOP_FILE.unlink()
        return True
    except OSError:
        return False


def request_compress_stop() -> bool:
    try:
        COMPRESS_STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        COMPRESS_STOP_FILE.write_text(str(time.time()), encoding="utf-8")
        return True
    except OSError:
        return False


def should_compress_stop() -> bool:
    return COMPRESS_STOP_FILE.exists()


def start_compress(*, persona: str = "") -> dict:
    """起一个独立的压缩进程；已经在压缩就不重复起。"""
    current = compress_status()
    if current.get("running"):
        return {"ok": False, "error": "压缩已经在跑", "status": current}
    ROOT.mkdir(parents=True, exist_ok=True)
    clear_compress_stop()
    cmd = [sys.executable, str(paths.ROOT / "main.py"), "train", "--compress"]
    if persona:
        cmd.extend(["--persona", persona])
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    flags = 0
    if os.name == "nt":
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log = open(COMPRESS_LOG, "a", encoding="utf-8")  # noqa: SIM115
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(paths.ROOT), env=env, stdout=log, stderr=log,
            stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.close()
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    log.close()
    try:
        COMPRESS_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass
    write_compress_status(
        running=True, pid=proc.pid, phase="starting", started_at=time.time(),
        entries=0, chunks=0, done_chunks=0, chars=0, error="",
        last="准备压缩 B 站记忆…",
    )
    return {"ok": True, "pid": proc.pid, "status": compress_status()}


def stop_compress(*, wait_s: float = 6.0) -> dict:
    current = compress_status()
    if not current.get("running"):
        return {"ok": True, "stopped": True, "status": current}
    request_compress_stop()
    deadline = time.time() + max(0.5, float(wait_s))
    while time.time() < deadline:
        time.sleep(0.25)
        current = compress_status()
        if not current.get("running"):
            return {"ok": True, "stopped": True, "status": current}
    return {"ok": True, "stopping": True, "status": current}


__all__ = ["ROOT", "STATUS_FILE", "STOP_FILE", "USAGE_FILE", "status", "write_status",
           "request_stop", "clear_stop", "should_stop", "start", "stop",
           "token_usage", "COMPRESS_STATUS_FILE", "COMPRESS_STOP_FILE",
           "compress_status", "write_compress_status", "request_compress_stop",
           "clear_compress_stop", "should_compress_stop", "start_compress",
           "stop_compress"]
