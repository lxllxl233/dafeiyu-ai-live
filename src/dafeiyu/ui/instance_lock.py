"""单实例锁：同一台机器只允许一个主播窗进程。

为什么需要它：主播窗是分层置顶窗（WS_EX_LAYERED|TOPMOST），多开一次屏幕上就会
叠出**两个弹窗**，而且两个都会连 HTML 轮询同一份 overlay 状态 —— 看起来像重影，
关掉一个另一个还在。历史上还出现过 `pythonw.exe` 起的旧进程一直活着、后来用
`python.exe` 再开一个，于是「上面显示两个弹窗」。

做法：`logs/streamer.lock` 里记 `{pid, ctime, argv, at}`。
- `ctime` 是进程创建时间，用来区分「pid 被系统复用」的情况（只有 pid 相同不算同一个）。
- 新进程启动时先 `owner()`：锁里的进程还活着 → 默认把它杀掉（替换），保证屏幕上只有一个；
  `--multi` 时不杀，直接并存（调试渲染用）。
- 纯函数 + 可注入的 `alive/ctime`，所以能在 `tools/test_instance_lock.py` 里不用真开窗就测。

这个模块只负责「谁在跑/杀谁」，不碰窗口本身，Windows API 也只用到 kernel32。
"""

from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes as w

from .. import paths

_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001

_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None

# 默认锁文件：跟其它运行期产物一起放 logs/（.gitignore 已忽略 logs/*.json）
LOCK_PATH = os.path.join(os.fspath(paths.LOGS), "streamer.lock")


# ---------------------------------------------------------------- Windows 探针

def pid_alive(pid: int) -> bool:
    """进程还活着吗（拿到句柄 + 退出码 == STILL_ACTIVE）。"""
    if not _k32 or not pid or pid <= 0:
        return False
    h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False
    try:
        code = w.DWORD(0)
        if not _k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return int(code.value) == _STILL_ACTIVE
    finally:
        _k32.CloseHandle(h)


def proc_ctime(pid: int) -> int | None:
    """进程创建时间（FILETIME 拼成的 64 位整数）；拿不到返回 None。"""
    if not _k32 or not pid or pid <= 0:
        return None
    h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return None
    try:
        c, e, k, u = (w.FILETIME() for _ in range(4))
        if not _k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                    ctypes.byref(k), ctypes.byref(u)):
            return None
        return (int(c.dwHighDateTime) << 32) | int(c.dwLowDateTime)
    finally:
        _k32.CloseHandle(h)


def kill_pid(pid: int) -> bool:
    """强杀一个 pid（只用于我们自己 lock 里记的那个主播窗）。"""
    if not _k32 or not pid or pid <= 0:
        return False
    h = _k32.OpenProcess(_PROCESS_TERMINATE, False, int(pid))
    if not h:
        return False
    try:
        return bool(_k32.TerminateProcess(h, 1))
    finally:
        _k32.CloseHandle(h)


# ---------------------------------------------------------------- 锁文件读写

def read_lock(path: str = LOCK_PATH) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def owner(path: str = LOCK_PATH, alive=None, ctime=None) -> dict | None:
    """锁的主人：只有「进程活着 + 创建时间对得上」才算数，否则当没有。"""
    alive = pid_alive if alive is None else alive
    ctime = proc_ctime if ctime is None else ctime
    info = read_lock(path)
    if not info:
        return None
    try:
        pid = int(info.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if not alive(pid):
        return None
    want = info.get("ctime")
    got = ctime(pid)
    # pid 会被系统复用：创建时间不一致就说明是别人，不能杀
    if want and got and int(want) != int(got):
        return None
    return info


def claim(path: str = LOCK_PATH, argv=None, pid: int | None = None,
          ctime: int | None = None) -> dict:
    pid = os.getpid() if pid is None else int(pid)
    ctime = proc_ctime(pid) if ctime is None else ctime
    info = {"pid": pid, "ctime": ctime, "argv": list(argv or []), "at": int(time.time())}
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False)
    return info


def release(path: str = LOCK_PATH, pid: int | None = None) -> None:
    """退出时清锁；只清自己的（别人的锁不动）。"""
    pid = os.getpid() if pid is None else int(pid)
    info = read_lock(path)
    if info and int(info.get("pid") or 0) == pid:
        try:
            os.remove(path)
        except OSError:
            pass


def take_over(path: str = LOCK_PATH, argv=None, multi: bool = False,
              alive=None, ctime=None, kill=None, note=print) -> dict:
    """启动时调用：替换旧实例（默认）或并存（multi=True），然后把自己写进锁。"""
    kill = kill_pid if kill is None else kill
    old = owner(path, alive=alive, ctime=ctime)
    if old and not multi:
        note(f"发现旧的主播窗在跑（pid={old.get('pid')}），先关掉它，避免屏幕上出现两个弹窗")
        kill(int(old.get("pid") or 0))
        # 给它一点时间消失（分层窗是随进程走的）
        time.sleep(0.4)
    elif old:
        note(f"旧的主播窗仍在跑（pid={old.get('pid')}），--multi 下两个都留着")
    return claim(path, argv=argv)
