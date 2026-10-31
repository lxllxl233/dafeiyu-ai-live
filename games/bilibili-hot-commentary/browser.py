"""用本机 Chrome 打开 B 站视频页；只负责「打开/关闭/位置」，不控制播放。

默认（isolated）用独立 profile 的 ``--app`` 窗口：一个页面、没有标签栏，不碰用户
正常浏览器的标签页，启动后移到副屏并铺满工作区。app 窗口开着时用本机 DevTools
在页面内导航，不重复开窗/抢前台；``isolated=False`` 才退回旧版“复用当前标签页 +
地址栏导航”。不会读取 DOM、不会注入脚本，也不会把浏览器内容当成指令。Chrome 进程
独立于 autoplay 线程，启动失败只记日志，不影响虚拟时钟和语音。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

DEFAULT_START_URL = "https://www.bilibili.com/v/popular/all"


def start_from_zero(url: str) -> str:
    """给 B 站视频 URL 加 ``t=0``，压过历史记录续播。

    B 站会记住每个视频的上次进度；页面带 ``t=0`` 时才从 0:00 开始。
    只改 bilibili.com 的视频页，其它 URL 原样返回。
    """
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return text
    host = (parts.hostname or "").lower()
    if host != "bilibili.com" and not host.endswith(".bilibili.com"):
        return text
    if not parts.path.startswith("/video/"):
        return text
    query = [(key, value) for key, value in urllib.parse.parse_qsl(
        parts.query, keep_blank_values=True) if key != "t"]
    query.append(("t", "0"))
    return urllib.parse.urlunsplit((
        parts.scheme, parts.netloc, parts.path,
        urllib.parse.urlencode(query), parts.fragment,
    ))


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


def cdp_target(profile_dir) -> tuple[str, str] | None:
    """读独立 profile 的 DevTools 端口，返回 ``(page_url, websocket_url)``。

    只用于同一个 app 窗口内换页面，避免每换一条视频就关窗/重开、抢前台。
    只读 DevTools 的页面列表，不读 DOM/页面文本；找不到返回 None。
    """
    if os.name != "nt" or profile_dir is None:
        return None
    try:
        raw = (Path(profile_dir) / "DevToolsActivePort").read_text(
            encoding="utf-8").splitlines()
        port = int(str(raw[0]).strip())
    except (OSError, ValueError, IndexError):
        return None
    if port <= 0 or port > 65535:
        return None
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/list", timeout=1.5) as resp:
            pages = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(pages, list):
        return None
    for page in pages:
        if not isinstance(page, dict) or page.get("type") != "page":
            continue
        ws = str(page.get("webSocketDebuggerUrl") or "")
        if ws.startswith("ws://") or ws.startswith("wss://"):
            return str(page.get("url") or ""), ws
    return None


def cdp_navigate(profile_dir, url: str) -> bool:
    """在独立 profile 的现有 app 页面里导航；不依赖前台，不新开窗口。"""
    target = cdp_target(profile_dir)
    if not target:
        return False
    _old, ws_url = target
    try:
        from websockets.sync.client import connect

        with connect(ws_url, open_timeout=2.0, close_timeout=1.0) as sock:
            sock.send(json.dumps({
                "id": 1, "method": "Page.navigate",
                "params": {"url": str(url)},
            }))
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                raw = sock.recv(timeout=max(0.1, deadline - time.monotonic()))
                message = json.loads(raw)
                if isinstance(message, dict) and message.get("id") == 1:
                    return "error" not in message
    except Exception:  # noqa: BLE001
        return False
    return False


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


def fullscreen_window(hwnd: int, area: tuple[int, int, int, int] | None) -> bool:
    """把窗口变成无边框、铺满目标显示器工作区（比 F11 更不依赖前台）。"""
    if os.name != "nt" or not hwnd or not area:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        left, top, right, bottom = area
        width, height = max(320, int(right - left)), max(240, int(bottom - top))
        user32.ShowWindow(int(hwnd), 9)  # SW_RESTORE
        time.sleep(0.08)
        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = int(get_style(int(hwnd), -16) or 0)  # GWL_STYLE
        style &= ~(0x00C00000 | 0x00040000 | 0x00020000
                   | 0x00010000 | 0x00080000)
        set_style(int(hwnd), -16, style)
        user32.SetWindowPos(
            int(hwnd), 0, int(left), int(top), width, height,
            0x0020 | 0x0040,  # SWP_FRAMECHANGED | SWP_SHOWWINDOW
        )
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


def window_pid(hwnd: int) -> int:
    if os.name != "nt" or not hwnd:
        return 0
    try:
        import ctypes

        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(
            int(hwnd), ctypes.byref(pid))
        return int(pid.value)
    except Exception:  # noqa: BLE001
        return 0


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """返回窗口屏幕矩形；取不到返回 None。"""
    if os.name != "nt" or not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        rect = RECT()
        if not ctypes.windll.user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:  # noqa: BLE001
        return None


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


def foreground_chrome_window() -> int:
    """返回当前前台 Chrome 窗口；不是 Chrome 返回 0。"""
    if os.name != "nt":
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return 0
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value != "Chrome_WidgetWin_1":
            return 0
        return hwnd
    except Exception:  # noqa: BLE001
        return 0


def find_chrome_target(before: set[int] | None = None, *,
                       timeout: float = 8.0) -> int:
    """等一个可用的 Chrome 窗口：优先新窗口，其次前台/B站窗口。

    ``before`` 是启动前的窗口集合。Chrome 恢复上次标签页时会一次出现多个
    新窗口；这里不拿 URL 强行开新标签，只挑一个现有窗口，交给地址栏导航。
    """
    if os.name != "nt":
        return 0
    old = set(before or ())
    deadline = time.monotonic() + max(0.2, float(timeout))
    while time.monotonic() < deadline:
        windows = _chrome_windows()
        fresh = [(h, t) for h, t in windows.items() if h not in old]
        pool = fresh if fresh else list(windows.items())
        if fresh or not old:
            fg = foreground_chrome_window()
            if fg and fg in dict(pool):
                return int(fg)
            for hwnd, title in pool:
                if "bilibili" in str(title or "").lower():
                    return int(hwnd)
            if pool:
                return int(pool[0][0])
        if old:
            # 没有新窗口时，只接受一个已经是 B 站页面的现有窗口，
            # 不去覆盖用户当前正在看的其它网页。
            fg = foreground_chrome_window()
            if fg and "bilibili" in window_title(fg).lower():
                return int(fg)
            for hwnd, title in windows.items():
                if "bilibili" in str(title or "").lower():
                    return int(hwnd)
        time.sleep(0.12)
    return 0


def _send_unicode_text(text: str) -> bool:
    """把一段 ASCII/Unicode 文本发送到当前焦点，不碰剪贴板。"""
    if os.name != "nt" or not text:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class _INPUTUNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

        user32 = ctypes.windll.user32
        user32.SendInput.argtypes = [
            wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int,
        ]
        user32.SendInput.restype = wintypes.UINT
        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002
        inputs = []
        for char in text:
            for flags in (KEYEVENTF_UNICODE,
                          KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                item = INPUT()
                item.type = 1  # INPUT_KEYBOARD
                item.u.ki.wVk = 0
                item.u.ki.wScan = ord(char)
                item.u.ki.dwFlags = flags
                item.u.ki.time = 0
                item.u.ki.dwExtraInfo = 0
                inputs.append(item)
        if not inputs:
            return False
        array = (INPUT * len(inputs))(*inputs)
        sent = int(user32.SendInput(
            len(inputs), ctypes.cast(array, ctypes.POINTER(INPUT)),
            ctypes.sizeof(INPUT)))
        return sent == len(inputs)
    except Exception:  # noqa: BLE001
        return False


def navigate_window(hwnd: int, url: str) -> bool:
    """在当前标签页地址栏输入 URL 并回车，不创建新窗口/标签页。"""
    if not is_window(hwnd) or not url:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        before_title = window_title(hwnd)
        user32.ShowWindow(int(hwnd), 9)  # SW_RESTORE
        user32.SetForegroundWindow(int(hwnd))
        time.sleep(0.18)
        # 抢不到前台就绝不盲打字，避免把 URL 打进游戏/聊天框。
        if int(user32.GetForegroundWindow() or 0) != int(hwnd):
            return False
        # Ctrl+L 选中地址栏
        user32.keybd_event(0x11, 0, 0, 0)  # VK_CONTROL down
        user32.keybd_event(0x4C, 0, 0, 0)  # L down
        user32.keybd_event(0x4C, 0, 0x0002, 0)
        user32.keybd_event(0x11, 0, 0x0002, 0)
        time.sleep(0.08)
        if not _send_unicode_text(url):
            return False
        time.sleep(0.08)
        user32.keybd_event(0x0D, 0, 0, 0)  # Enter
        user32.keybd_event(0x0D, 0, 0x0002, 0)
        # 标题变化说明地址栏确实接管了；没变化就让调用方退回启动 URL。
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            title = window_title(hwnd)
            if title and (title != before_title
                          or "bilibili" in title.lower()):
                return True
            time.sleep(0.1)
        return False
    except Exception:  # noqa: BLE001
        return False


def _env_path(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def chrome_mode_pids() -> set[int]:
    """找带本模式启动开关的 Chrome 根进程；用于清掉旧 B 模式窗口。

    普通用户 Chrome 没有这些开关，不会被误伤。PowerShell 不可用时返回空。
    """
    if os.name != "nt":
        return set()
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -like "
        "'*--disable-background-media-suspend*' -and $_.CommandLine "
        "-notlike '*--type=*' } | Select-Object -ExpandProperty ProcessId"
    )
    try:
        kwargs = {"text": True, "timeout": 8}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        output = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            **kwargs,
        )
    except Exception:  # noqa: BLE001
        return set()
    out: set[int] = set()
    for line in output.splitlines():
        text = line.strip()
        if text.isdigit():
            out.add(int(text))
    return out


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
                 isolated: bool = False, profile_dir=None,
                 fullscreen: bool = False,
                 area_provider=None, positioner=None, finder=None,
                 closer=None, refresher=None, navigator=None,
                 state_path=None) -> None:
        self.exe = str(exe or "").strip() or find_chrome()
        self.log = logger or (lambda text: None)
        self._custom_launcher = launcher is not None
        self._launch = launcher or self._popen
        self.windowed = bool(windowed)
        self.isolated = bool(isolated)
        self.profile_dir = (Path(profile_dir).expanduser().resolve()
                            if profile_dir else None)
        self.fullscreen = bool(fullscreen)
        self.monitor = str(monitor or "secondary").strip().lower()
        self._area_provider = area_provider or choose_work_area
        # None = 默认 Win32 定位；测试可注入 positioner。
        self._positioner = positioner
        self._finder = finder
        self._closer = closer
        self._refresher = refresher
        self._navigator = navigator
        self._state_path = Path(state_path) if state_path else None
        self._tracked: dict[int, str] = self._load_tracked()
        self._hwnd = 0
        self._opened = False
        self._browser = ""
        self._last_url = ""
        self._title = ""
        self._open_gen = 0
        self._isolated_cleaned = False

    def _load_tracked(self) -> dict[int, str]:
        if self._state_path is None:
            return {}
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        rows = raw.get("windows") if isinstance(raw, dict) else None
        out: dict[int, str] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                hwnd = int(row.get("hwnd") or 0)
            except (TypeError, ValueError):
                continue
            if hwnd > 0:
                out[hwnd] = str(row.get("url") or "")
        return out

    def _save_tracked(self) -> None:
        if self._state_path is None:
            return
        rows = [{"hwnd": hwnd, "url": url}
                for hwnd, url in sorted(self._tracked.items())]
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_name(self._state_path.name + ".tmp")
            tmp.write_text(json.dumps({"windows": rows}, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError:
            pass

    def available(self) -> bool:
        return bool(self.exe)

    def _geometry_args(self) -> list[str]:
        if not self.windowed:
            return []
        try:
            area = self._area_provider(self.monitor)
        except Exception:  # noqa: BLE001
            area = None
        if not area:
            return []
        left, top, right, bottom = area
        width, height = max(320, int(right - left)), max(240, int(bottom - top))
        # 位置参数是尽力而为；启动后还会用 Win32 再移动/最大化一次。
        return [f"--window-position={int(left)},{int(top)}",
                f"--window-size={int(width)},{int(height)}"]

    def _window_args(self) -> list[str]:
        if not self.windowed:
            return ["--new-tab"]
        return ["--new-window", *self._geometry_args()]

    def _chrome_flags(self) -> list[str]:
        """Chrome 播放相关的启动开关；不含 URL。"""
        return [
            # 多窗口/多标签时 Chrome 会节流后台媒体；这几个开关让
            # 视频尽量保持播放，不要因为窗口被遮住就暂停。
            "--autoplay-policy=no-user-gesture-required",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-media-suspend",
            "--disable-features=CalculateNativeWinOcclusion",
            # 尽力阻止 Chrome 启动时恢复上次整批标签页；不支持的版本会忽略。
            "--disable-restore-session-state",
        ]

    def _remember(self, hwnd: int) -> None:
        self._hwnd = int(hwnd or 0)
        if self._hwnd:
            self._title = window_title(self._hwnd)
            self._tracked[self._hwnd] = self._last_url
            self._save_tracked()

    def _forget(self, hwnd: int) -> None:
        if hwnd and self._tracked.pop(int(hwnd), None) is not None:
            self._save_tracked()

    def adopt_legacy_window(self, playlist_path) -> None:
        """升级后的第一次启动：接手上一个进程写在 playlist 里的窗口句柄。"""
        if self._tracked or not playlist_path:
            return
        try:
            raw = json.loads(Path(playlist_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        browser = raw.get("browser") if isinstance(raw, dict) else None
        if not isinstance(browser, dict):
            return
        try:
            hwnd = int(browser.get("hwnd") or 0)
        except (TypeError, ValueError):
            return
        if hwnd > 0:
            self._tracked[hwnd] = str(browser.get("url") or "")
            self._save_tracked()

    def _close_orphans(self) -> None:
        """关掉本模式以前开过、但已经没在用的 Chrome 窗口。

        跨进程/崩溃后 hwnd 还在 state 文件里；不清理就会每重启一次多一个
        B 站窗口，标签页越积越多，最后把视频卡成静帧。
        """
        for hwnd in list(self._tracked):
            if int(hwnd) == int(self._hwnd or 0):
                continue
            if not is_window(hwnd):
                self._forget(hwnd)
                continue
            title = window_title(hwnd)
            recorded = str(self._tracked.get(hwnd) or "").lower()
            # 只关自己记过、且仍是 B 站页面的窗口，不碰用户自己开的窗口。
            # 自己切到 about:blank 的窗口也算自己的，避免残留。
            own = ("bilibili" in title.lower()
                   or "bilibili" in recorded or recorded == "about:blank")
            if title and not own:
                self._forget(hwnd)
                continue
            try:
                ok = (bool(self._closer(hwnd)) if self._closer is not None
                      else close_window(hwnd))
            except Exception:  # noqa: BLE001
                ok = False
            if ok or not is_window(hwnd):
                self._forget(hwnd)

    def _clean_isolated_once(self) -> None:
        """首次启用独立模式时，关掉以前由 B 模式启动的 Chrome 窗口。

        通过启动开关识别本模式进程，普通用户 Chrome（另一个 PID）不受影响；
        这样之前导航失败留下的空窗口/多个 B 站窗口不会继续叠在副屏上。
        """
        if self._isolated_cleaned:
            return
        self._isolated_cleaned = True
        if self._custom_launcher:
            return  # 测试注入 launcher 时不跑 PowerShell/不在测试里关真实窗口
        pids = chrome_mode_pids()
        if not pids:
            return
        for hwnd in list(_chrome_windows()):
            if window_pid(hwnd) not in pids:
                continue
            try:
                ok = (bool(self._closer(hwnd)) if self._closer is not None
                      else close_window(hwnd))
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                self._forget(hwnd)
                self._log_close_legacy(hwnd)

    def _log_close_legacy(self, hwnd: int) -> None:
        try:
            self.log(f"[bili] 清理 B 模式旧 Chrome 窗口 hwnd={int(hwnd)}")
        except Exception:  # noqa: BLE001
            pass

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
                    moved = (fullscreen_window(hwnd, area) if self.fullscreen
                             else move_window(hwnd, area))
                if not moved:
                    self.log("[bili] Chrome 已打开，但没有找到可移动的新窗口")
        except Exception as exc:  # noqa: BLE001
            self.log(f"[bili] Chrome 窗口定位失败：{type(exc).__name__}: {exc}")

    def _live_window(self) -> int:
        """返回本模式当前还能用的 Chrome 窗口；不碰用户其它窗口。"""
        if (self._browser == "chrome" and self._hwnd
                and is_window(self._hwnd)):
            return int(self._hwnd)
        for hwnd in list(self._tracked):
            if not is_window(hwnd):
                self._forget(hwnd)
                continue
            title = window_title(hwnd)
            recorded = str(self._tracked.get(hwnd) or "").lower()
            if ("bilibili" in title.lower()
                    or "bilibili" in recorded
                    or recorded == "about:blank"):
                return int(hwnd)
        return 0

    def _find_bilibili_window(self) -> int:
        """找现有的 B 站 Chrome 窗口，优先复用，不再新开标签页。"""
        for hwnd, title in _chrome_windows().items():
            if "bilibili" in str(title or "").lower():
                return int(hwnd)
        return 0

    def _navigate_target(self, hwnd: int, url: str, *,
                         move: bool = False) -> dict:
        """在指定窗口的当前标签页导航到 URL。"""
        try:
            if self._navigator is not None:
                ok = bool(self._navigator(hwnd, url))
            else:
                ok = navigate_window(hwnd, url)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if not ok:
            return {"ok": False, "error": "地址栏导航失败",
                    "browser": "chrome", "hwnd": int(hwnd)}
        self._hwnd = int(hwnd)
        self._browser = "chrome"
        self._opened = True
        self._last_url = url
        self._title = window_title(hwnd) or self._title
        self._tracked[self._hwnd] = url
        self._save_tracked()
        if move and self.windowed:
            try:
                area = self._area_provider(self.monitor)
            except Exception:  # noqa: BLE001
                area = None
            if area:
                move_window(self._hwnd, area)
        return {"ok": True, "browser": "chrome", "exe": self.exe,
                "url": url, "hwnd": self._hwnd, "reused": True,
                "windowed": self.windowed, "monitor": self.monitor}

    def _wait_window(self, before: set[int]) -> int:
        if self._finder is not None:
            try:
                return int(self._finder(before) or 0)
            except Exception:  # noqa: BLE001
                return 0
        return find_chrome_target(before, timeout=8.0)

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
            self._forget(self._hwnd)
            self._hwnd = 0
            self._opened = False
            self._title = ""
            time.sleep(0.18)  # 给 Chrome 一点时间收窗口，紧接着 open 不会叠窗
            return {"ok": True, "url": self._last_url}
        if self._hwnd and not is_window(self._hwnd):
            self._forget(self._hwnd)
            self._hwnd = 0
            self._opened = False
        return {"ok": False, "error": "没有可关闭的 B 站标签页", "url": self._last_url}

    def blank(self) -> dict:
        """把当前页面切到空白页；独立 app 模式优先原地导航，失败才关窗。"""
        if self.isolated:
            if (self._hwnd and is_window(self._hwnd) and self.profile_dir
                    and cdp_navigate(self.profile_dir, "about:blank")):
                self._last_url = "about:blank"
                self._tracked[int(self._hwnd)] = "about:blank"
                self._save_tracked()
                return {"ok": True, "url": "about:blank"}
            return self.close()
        if not self._hwnd or not is_window(self._hwnd):
            return {"ok": False, "error": "没有可切走的 B 站标签页"}
        try:
            if self._navigator is not None:
                ok = bool(self._navigator(self._hwnd, "about:blank"))
            else:
                ok = navigate_window(self._hwnd, "about:blank")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if ok:
            self._last_url = "about:blank"
            self._title = window_title(self._hwnd) or ""
            self._tracked[self._hwnd] = "about:blank"
            self._save_tracked()
        return {"ok": ok, "url": self._last_url}

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
            self._forget(self._hwnd)
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

    def _open_isolated(self, url: str) -> dict:
        """用独立 profile 的 Chrome app 窗口播放：一个窗口、一个页面。"""
        # 同一个 app 窗口已经开着时，优先用 DevTools 在页面内导航；不关窗、
        # 不新开窗口、不抢前台。失败才退回关闭重开。
        if (self._hwnd and is_window(self._hwnd) and self.profile_dir
                and cdp_navigate(self.profile_dir, url)):
            self._opened = True
            self._browser = "chrome"
            self._last_url = url
            self._title = window_title(self._hwnd) or self._title
            self._tracked[int(self._hwnd)] = url
            self._save_tracked()
            return {"ok": True, "browser": "chrome", "exe": self.exe,
                    "url": url, "hwnd": int(self._hwnd), "reused": True,
                    "isolated": True, "fullscreen": self.fullscreen,
                    "monitor": self.monitor}
        if self._hwnd and is_window(self._hwnd):
            self.log("[bili] app 页面导航失败，关闭后重开一个窗口")
        self.close()
        self._close_orphans()
        self._clean_isolated_once()
        profile = self.profile_dir
        if profile is not None:
            try:
                profile.mkdir(parents=True, exist_ok=True)
            except OSError:
                profile = None
        area = None
        try:
            area = self._area_provider(self.monitor) if self.windowed else None
        except Exception:  # noqa: BLE001
            area = None
        before = set(_chrome_windows())
        command = [self.exe]
        if profile is not None:
            command.append(f"--user-data-dir={profile}")
        command.extend([
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-debugging-port=0",
            f"--app={url}",
            *self._geometry_args(),
            *self._chrome_flags(),
        ])
        self._launch(command)
        self._open_gen += 1
        gen = self._open_gen
        self._browser = "chrome"
        self._opened = True
        self._last_url = url
        self._title = ""
        threading.Thread(
            target=self._position_later, args=(before, area, gen),
            name="bili-chrome-place", daemon=True,
        ).start()
        return {"ok": True, "browser": "chrome", "exe": self.exe, "url": url,
                "windowed": self.windowed, "isolated": True,
                "fullscreen": self.fullscreen, "monitor": self.monitor}

    def open(self, url: str, *, close_previous: bool = True) -> dict:
        url = start_from_zero(str(url or "").strip())
        if not url:
            return {"ok": False, "error": "空 URL"}
        self._open_gen += 1  # 作废上一轮还在找窗口的后台线程
        if self.isolated and self.exe:
            return self._open_isolated(url)
        # 优先在当前窗口/当前标签页里导航。Chrome 自己恢复上次标签页时，
        # 不再额外开一个 B 站标签页；换视频也不关窗重开。
        target = self._live_window()
        if target:
            result = self._navigate_target(target, url)
            if result.get("ok"):
                if close_previous:
                    self._close_orphans()
                return result
            self._forget(target)
            self._hwnd = 0
            self._opened = False
            self._browser = ""

        target = self._find_bilibili_window()
        if target:
            result = self._navigate_target(target, url, move=True)
            if result.get("ok"):
                if close_previous:
                    self._close_orphans()
                return result

        self._hwnd = 0
        self._opened = False
        self._browser = ""
        if self.exe:
            before: set[int] = set()
            try:
                before = set(_chrome_windows())
            except Exception:  # noqa: BLE001
                before = set()
            self._close_orphans()
            try:
                # 不把 URL 放进启动参数：先让 Chrome 启动/恢复，再在它当前
                # 标签页里输入地址，避免“恢复旧标签 + 又新开一个 B 站页”。
                command = [self.exe]
                if before:
                    command.extend(self._window_args())
                command.extend(self._chrome_flags())
                self._launch(command)
                self._open_gen += 1
                self._last_url = url
                self._title = ""
                target = self._wait_window(before)
                if target:
                    result = self._navigate_target(target, url, move=True)
                    if result.get("ok"):
                        if close_previous:
                            self._close_orphans()
                        return result
                    self.log("[bili] 地址栏导航失败，退回新开标签页")

                # 最后兜底：还是找不到窗口，就用 URL 启动（旧行为）。
                area = None
                if self.windowed:
                    try:
                        area = self._area_provider(self.monitor)
                    except Exception:  # noqa: BLE001
                        area = None
                command = [
                    self.exe,
                    *self._window_args(),
                    *self._chrome_flags(),
                    url,
                ]
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
    "chrome_mode_pids", "find_chrome", "find_chrome_target", "find_new_window",
    "foreground_chrome_window", "fullscreen_window", "is_window",
    "move_new_window", "move_window", "navigate_window", "refresh_window",
    "start_from_zero", "window_pid", "window_rect", "window_title", "work_areas",
]
