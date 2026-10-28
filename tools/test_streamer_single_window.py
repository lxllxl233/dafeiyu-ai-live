"""端到端回归：连开两次主播窗，屏幕上必须只剩一个。

为什么要有这个测试：用户看到的症状是「页面上显示两个弹窗」。根因是单实例缺失 ——
老的 `pythonw.exe streamer.py` 没关，又用 `python.exe` 开了一个，两个分层置顶窗叠加。
所以这里真的起进程、真的数窗口，而不是只测锁的逻辑（那是 test_instance_lock.py 的事）。

跑：python -X utf8 tools/test_streamer_single_window.py
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes as w

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)
import instance_lock as il  # noqa: E402

u32 = ctypes.windll.user32
u32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]

PASS = 0
FAIL = 0


def ok(cond, what):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {what}")
    else:
        FAIL += 1
        print(f"  FAIL {what}")


def windows():
    """[(pid, rect)]：所有可见的 DafeiyuStreamer* 窗口。"""
    found = []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def cb(h, lp):
        if not u32.IsWindowVisible(h):
            return True
        b = ctypes.create_unicode_buffer(128)
        u32.GetClassNameW(h, b, 128)
        if b.value.startswith("DafeiyuStreamer"):
            pid = w.DWORD()
            u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            r = w.RECT()
            u32.GetWindowRect(h, ctypes.byref(r))
            found.append((int(pid.value), (r.left, r.top, r.right, r.bottom)))
        return True

    u32.EnumWindows(cb, 0)
    return found


def wait_windows(n, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        got = windows()
        if len(got) >= n:
            return got
        time.sleep(0.25)
    return windows()


def spawn(tag, out_dir=os.path.join(ROOT, "logs")):
    out = open(os.path.join(out_dir, f"single_{tag}.out"), "w", encoding="utf-8")
    err = open(os.path.join(out_dir, f"single_{tag}.err"), "w", encoding="utf-8")
    env = dict(os.environ, STS2_OVERLAY_PORT=os.environ.get("STS2_OVERLAY_PORT", "8730"),
               PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, "-X", "utf8", "main.py", "--click-through"],
                         cwd=ROOT, stdout=out, stderr=err, env=env)
    p._tag_files = (out, err)  # type: ignore[attr-defined]
    return p


def stop(p):
    try:
        p.terminate()
        p.wait(timeout=6)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
    for fh in getattr(p, "_tag_files", ()):  # 子进程退出后再关文件
        try:
            fh.close()
        except Exception:
            pass


def cleanup_stale():
    """测试开始前把可能残留的主播窗清掉，保证基线干净。"""
    old = il.owner(il.LOCK_PATH)
    if old:
        il.kill_pid(int(old["pid"]))
        time.sleep(0.5)
    for pid, _ in windows():
        il.kill_pid(pid)
    time.sleep(0.5)
    try:
        os.remove(il.LOCK_PATH)
    except OSError:
        pass


def main() -> int:
    print("[0] 基线：先把残留实例清掉")
    cleanup_stale()
    ok(len(windows()) == 0, "起始时没有残留窗口")

    print("[1] 起第一个窗口")
    a = spawn("e2e_a")
    got = wait_windows(1)
    ok(len(got) == 1, f"第一个进程起了一个窗口（实际 {len(got)}）")
    ok(got and got[0][0] == a.pid, "窗口属于第一个进程")
    info = il.read_lock()
    ok(bool(info) and int(info["pid"]) == a.pid, "锁文件记的是第一个进程")

    print("[2] 再起第二个（模拟用户又点了一次）")
    b = spawn("e2e_b")
    time.sleep(4.0)
    got = windows()
    ok(len(got) == 1, f"屏幕上仍然只有一个弹窗（实际 {len(got)}）")
    ok(got and got[0][0] == b.pid, "留下的是新进程的窗口（旧的被替换）")
    ok(a.poll() is not None, "旧进程已经退出")
    info = il.read_lock()
    ok(bool(info) and int(info["pid"]) == b.pid, "锁换成了新进程")

    print("[3] --multi 允许并存（调试用）")
    env = dict(os.environ, STS2_OVERLAY_PORT=os.environ.get("STS2_OVERLAY_PORT", "8730"),
               PYTHONIOENCODING="utf-8")
    c = subprocess.Popen([sys.executable, "-X", "utf8", "main.py", "--click-through", "--multi"],
                         cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    got = wait_windows(2, timeout=15)
    ok(len(got) == 2, f"--multi 时确实两个窗口（实际 {len(got)}）")

    print("[4] 收尾")
    stop(b)
    stop(c)
    time.sleep(0.6)
    left = windows()
    ok(len(left) == 0, f"进程杀掉后窗口都没了（实际 {len(left)}）")
    try:
        os.remove(il.LOCK_PATH)
    except OSError:
        pass

    print(f"\n{'ALL OK' if not FAIL else 'FAILED'}  pass={PASS} fail={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        cleanup_stale()
