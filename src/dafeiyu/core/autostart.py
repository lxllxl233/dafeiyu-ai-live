#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开机自启动：写当前用户的 ``HKCU\\...\\Run``，不需要管理员权限。

只启动桌宠（``main.py``），不启动控制台/KOOK 服务；路径用 pythonw，开机时
不弹黑框。写注册表是设置页的动作，失败会把错误回给页面，不会静默吞掉。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DafeiyuPet"


def available() -> bool:
    return os.name == "nt"


def pythonw() -> str:
    """优先 pythonw.exe（无控制台）；找不到就退回当前解释器。"""
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        cand = exe.with_name("pythonw.exe")
        if cand.is_file():
            return str(cand)
    return str(exe)


def command(entry: Path | None = None) -> str:
    """注册表里写的命令行：带引号的解释器和 main.py。"""
    root = Path(entry or (Path(__file__).resolve().parents[3] / "main.py"))
    return f'"{pythonw()}" "{root}"'


def _open(access: int):
    import winreg

    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)


def current() -> str:
    """注册表里当前的值；没写过返回空串。"""
    if not available():
        return ""
    try:
        import winreg

        with _open(winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value or "")
    except OSError:
        return ""


def enabled(entry: Path | None = None) -> bool:
    """注册表里写的正是当前这份 main.py 才算开着；路径搬过会自愈。"""
    value = current()
    return bool(value) and value == command(entry)


def set_enabled(on: bool, entry: Path | None = None) -> dict:
    """开/关开机自启动；返回注册表里的新状态。"""
    if not available():
        return {"ok": False, "enabled": False, "error": "只有 Windows 支持开机自启动"}
    try:
        import winreg

        with _open(winreg.KEY_SET_VALUE) as key:
            if on:
                cmd = command(entry)
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        return {"ok": False, "enabled": enabled(entry),
                "error": f"写注册表失败：{type(exc).__name__}: {exc}"}
    return {"ok": True, "enabled": bool(on), "command": command(entry) if on else ""}


def status(entry: Path | None = None) -> dict:
    value = current()
    expected = command(entry)
    return {
        "available": available(),
        "enabled": bool(value) and value == expected,
        "command": value,
        "expected": expected,
    }


__all__ = ["RUN_KEY", "VALUE_NAME", "available", "command", "current", "enabled",
           "pythonw", "set_enabled", "status"]
