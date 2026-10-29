"""用本机 Chrome 打开 B 站视频页；只负责「打开」，不控制播放。

优先找 Google Chrome，找不到才退回系统默认浏览器。不会读取页面、不会
注入脚本，也不会把浏览器内容当成指令。Chrome 进程独立于 autoplay 线程，
启动失败只记日志，不影响虚拟时钟和语音。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

DEFAULT_START_URL = "https://www.bilibili.com/v/popular/all"


def work_areas() -> list[tuple[bool, tuple[int, int, int, int]]]:
    """返回 Windows 显示器工作区 ``(is_primary, (left, top, right, bottom))``。

    非 Windows / 取不到时返回空列表；调用方退回 Chrome 默认位置。
    """
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        monitor_proc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, wintypes.HDC,
            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
        )
        found: list[tuple[bool, tuple[int, int, int, int]]] = []

        def _collect(hmonitor, _hdc, _rect, _data):
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                work = info.rcWork
                found.append((
                    bool(info.dwFlags & 1),
                    (int(work.left), int(work.top), int(work.right), int(work.bottom)),
                ))
            return True

        user32.EnumDisplayMonitors(0, 0, monitor_proc(_collect), 0)
        return found
    except Exception:  # noqa: BLE001 - 定位失败不能影响打开视频
        return []


def choose_work_area(
    monitor: str = "secondary",
    areas: list[tuple[bool, tuple[int, int, int, int]]] | None = None,
) -> tuple[int, int, int, int] | None:
    """按配置选显示器；默认副屏，没有副屏就用主屏。"""
    items = work_areas() if areas is None else list(areas)
    if not items:
        return None
    key = str(monitor or "secondary").strip().lower()
    if key.isdigit():
        index = int(key)
        if 0 <= index < len(items):
            return items[index][1]
        return None
    if key == "primary":
        for is_primary, area in items:
            if is_primary:
                return area
        return items[0][1]
    # secondary / auto：优先非主屏
    for is_primary, area in items:
        if not is_primary:
            return area
    return items[0][1]


def _chrome_windows() -> dict[int, str]:
    """当前所有可见 Chrome 顶层窗口 {hwnd: title}；失败返回空。"""
    if os.name != "nt":
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, wintypes.LPARAM)
        found: dict[int, str] = {}

        def _collect(hwnd, _data):
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value != "Chrome_WidgetWin_1":
                return True
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            found[int(hwnd)] = title.value
            return True

        user32.EnumWindows(proc(_collect), 0)
        return found
    except Exception:  # noqa: BLE001
        return {}


def find_new_window(before: set[int], *, timeout: float = 4.0) -> int:
    """等启动后新出现的 Chrome 顶层窗口，返回 hwnd；找不到返回 0。"""
    if os.name != "nt":
        return 0
    deadline = time.monotonic() + max(0.2, float(timeout))
    while time.monotonic() < deadline:
        fresh = [h for h in _chrome_windows() if h not in before]
        if fresh:
            return int(fresh[0])
        time.sleep(0.12)
    return 0


def move_window(hwnd: int, area: tuple[int, int, int, int] | None) -> bool:
    """把一个已有 Chrome 窗口移动到目标显示器；不负责找窗口。"""
    if os.name != "nt" or not hwnd or not area:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        left, top, right, bottom = area
        width, height = max(320, int(right - left)), max(240, int(bottom - top))
        # Chrome 可能把新窗口直接最大化在旧屏幕上；先还原，移动后再最大化，
        # 否则 SetWindowPos 只改还原尺寸，窗口仍留在主屏。
        user32.ShowWindow(int(hwnd), 9)  # SW_RESTORE
        time.sleep(0.08)
        # SWP_NOZORDER | SWP_SHOWWINDOW
        user32.SetWindowPos(int(hwnd), 0, int(left), int(top), width, height,
                            0x0004 | 0x0040)
        time.sleep(0.08)
        user32.ShowWindow(int(hwnd), 3)  # SW_MAXIMIZE
        return True
    except Exception:  # noqa: BLE001
        return False


def move_new_window(before: set[int], area: tuple[int, int, int, int] | None,
                    *, timeout: float = 4.0) -> bool:
    """把启动后新出现的 Chrome 窗口移到目标显示器。

    Chrome 已在运行时 ``--window-position``/``--window-size`` 可能被忽略，
    所以启动后再用 Win32 移动一次；找不到新窗口就安静放弃。
    """
    if os.name != "nt" or not area:
        return False
    hwnd = find_new_window(before, timeout=timeout)
    return move_window(hwnd, area) if hwnd else False


def is_window(hwnd: int) -> bool:
    if os.name != "nt" or not hwnd:
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.IsWindow(int(hwnd)))
    except Exception:  # noqa: BLE001
        return False


def window_title(hwnd: int) -> str:
    if os.name != "nt" or not hwnd:
        return ""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(int(hwnd), buf, 256)
        return str(buf.value or "")
    except Exception:  # noqa: BLE001
        return ""


def close_window(hwnd: int) -> bool:
    """给 Chrome 窗口发 WM_CLOSE；只关本进程记录的那个窗口。"""
    if not is_window(hwnd):
        return False
    try:
        import ctypes

        ctypes.windll.user32.PostMessageW(int(hwnd), 0x0010, 0, 0)  # WM_CLOSE
        return True
    except Exception:  # noqa: BLE001
        return False


def refresh_window(hwnd: int) -> bool:
    """把窗口提到前台并按 F5；用于“刷新当前标签页”。"""
    if not is_window(hwnd):
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.ShowWindow(int(hwnd), 9)  # SW_RESTORE
        user32.SetForegroundWindow(int(hwnd))
        time.sleep(0.12)
        user32.keybd_event(0x74, 0, 0, 0)  # VK_F5
        user32.keybd_event(0x74, 0, 0x0002, 0)  # KEYEVENTF_KEYUP
        return True
    except Exception:  # noqa: BLE001
        return False


def _env_path(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def find_chrome() -> str:
    """返回 Chrome 可执行文件；找不到返回空字符串。"""
    candidates = [
        _env_path("CHROME_PATH"), _env_path("DAFEIYU_CHROME"),
    ]
    for name in ("chrome", "chrome.exe", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    if os.name == "nt":
        roots = [
            _env_path("PROGRAMFILES"),
            _env_path("PROGRAMFILES(X86)"),
            _env_path("LOCALAPPDATA"),
        ]
        for root in roots:
            if root:
                candidates.append(str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    else:
        candidates.extend([
            "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ])
    for path in candidates:
        if path and Path(path).exists():
            return path
    return ""


class ChromeBrowser:
    def __init__(self, *, exe: str = "", logger=None, launcher=None,
                 windowed: bool = True, monitor: str = "secondary",
                 area_provider=None, positioner=None, finder=None,
                 closer=None, refresher=None) -> None:
        self.exe = str(exe or "").strip() or find_chrome()
        self.log = logger or (lambda text: None)
        self._launch = launcher or self._popen
        self.windowed = bool(windowed)
        self.monitor = str(monitor or "secondary").strip().lower()
        self._area_provider = area_provider or choose_work_area
        # None = 默认 Win32 定位；测试可注入 positioner。
        self._positioner = positioner
        self._finder = finder
        self._closer = closer
        self._refresher = refresher
        self._hwnd = 0
        self._opened = False
        self._browser = ""
        self._last_url = ""
        self._title = ""
        self._open_gen = 0

    def available(self) -> bool:
        return bool(self.exe)

    def _window_args(self) -> list[str]:
        if not self.windowed:
            return ["--new-tab"]
        args = ["--new-window"]
        try:
            area = self._area_provider(self.monitor)
        except Exception:  # noqa: BLE001
            area = None
        if area:
            left, top, right, bottom = area
            width, height = max(320, right - left), max(240, bottom - top)
            # Chrome 新窗口启动参数；已在运行的 Chrome 会按自己的窗口策略接收新窗口，
            # 位置参数是尽力而为，失败仍然会正常打开视频。
            args.extend([
                f"--window-position={int(left)},{int(top)}",
                f"--window-size={int(width)},{int(height)}",
            ])
        return args

    def _remember(self, hwnd: int) -> None:
        self._hwnd = int(hwnd or 0)
        if self._hwnd:
            self._title = window_title(self._hwnd)

    def _position_later(self, before: set[int],
                        area: tuple[int, int, int, int] | None,
                        gen: int = 0) -> None:
        """启动后找新窗口，记住 hwnd 并移动到目标屏。

        ``open()`` 不阻塞等待 Chrome；控制台稍后点关闭/刷新时通常已经找到。
        ``gen`` 防止旧启动线程在用户换视频后把 hwnd 写回旧窗口。
        """
        try:
            hwnd = 0
            if self._finder is not None:
                hwnd = int(self._finder(before) or 0)
            elif self._positioner is None:
                hwnd = find_new_window(before, timeout=8.0)
            if gen and gen != self._open_gen:
                return
            if hwnd:
                self._remember(hwnd)
            moved = False
            if area:
                if self._positioner is not None:
                    moved = bool(self._positioner(before, area))
                elif hwnd:
                    moved = move_window(hwnd, area)
                if not moved:
                    self.log("[bili] Chrome 已打开，但没有找到可移动的新窗口")
        except Exception as exc:  # noqa: BLE001
            self.log(f"[bili] Chrome 窗口定位失败：{type(exc).__name__}: {exc}")

    def _popen(self, command: list[str]) -> object:
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )

    def close(self) -> dict:
        """关闭当前记住的 Chrome 窗口（没有记录就安静返回失败）。"""
        self._open_gen += 1  # 作废还在找窗口的旧启动线程
        try:
            if self._closer is not None:
                ok = bool(self._closer(self._hwnd))
            else:
                ok = close_window(self._hwnd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if ok:
            self._hwnd = 0
            self._opened = False
            self._title = ""
            time.sleep(0.18)  # 给 Chrome 一点时间收窗口，紧接着 open 不会叠窗
            return {"ok": True, "url": self._last_url}
        return {"ok": False, "error": "没有可关闭的 B 站标签页", "url": self._last_url}

    def refresh(self) -> dict:
        """刷新当前标签页；浏览器没记录/已关时返回失败。"""
        try:
            if self._refresher is not None:
                ok = bool(self._refresher(self._hwnd))
            else:
                ok = refresh_window(self._hwnd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": ok, "url": self._last_url,
                "error": "" if ok else "没有可刷新的 B 站标签页"}

    def snapshot(self) -> dict:
        opened = bool(self._browser == "chrome" and self._opened
                      and is_window(self._hwnd))
        if self._browser == "chrome" and self._opened and not opened:
            self._hwnd = 0
        if self._hwnd:
            self._title = window_title(self._hwnd) or self._title
        return {
            "opened": opened,
            "browser": self._browser,
            "hwnd": self._hwnd,
            "url": self._last_url,
            "title": self._title[:120],
            "windowed": self.windowed,
            "monitor": self.monitor,
        }

    def open(self, url: str, *, close_previous: bool = True) -> dict:
        url = str(url or "").strip()
        if not url:
            return {"ok": False, "error": "空 URL"}
        if close_previous:
            # 云观影只留一个 B 站窗口：新视频先关旧窗口，避免标签页越积越多。
            self.close()
        if self.exe:
            command = [
                self.exe,
                *self._window_args(),
                "--autoplay-policy=no-user-gesture-required",
                url,
            ]
            try:
                area = None
                before: set[int] = set()
                if self.windowed:
                    try:
                        area = self._area_provider(self.monitor)
                        before = set(_chrome_windows())
                    except Exception:  # noqa: BLE001
                        area = None
                self._launch(command)
                self._open_gen += 1
                gen = self._open_gen
                self._browser = "chrome"
                self._opened = True
                self._last_url = url
                self._title = ""
                if self.windowed:
                    threading.Thread(
                        target=self._position_later, args=(before, area, gen),
                        name="bili-chrome-place", daemon=True,
                    ).start()
                return {"ok": True, "browser": "chrome", "exe": self.exe, "url": url,
                        "windowed": self.windowed, "monitor": self.monitor}
            except Exception as exc:  # noqa: BLE001
                self.log(f"[bili] Chrome 启动失败：{type(exc).__name__}: {exc}")
        try:
            ok = bool(webbrowser.open(url))
            self._browser = "default" if ok else ""
            self._opened = bool(ok)
            self._last_url = url
            return {"ok": ok, "browser": "default", "url": url,
                    "error": "" if ok else "系统默认浏览器没有接受 URL"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "browser": "default", "url": url,
                    "error": f"{type(exc).__name__}: {exc}"}


__all__ = [
    "ChromeBrowser", "DEFAULT_START_URL", "choose_work_area", "close_window",
    "find_chrome", "find_new_window", "is_window", "move_new_window",
    "move_window", "refresh_window", "window_title", "work_areas",
]
