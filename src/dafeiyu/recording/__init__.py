"""录制层：OBS WebSocket 控制 + 一局一个录像的目录/元数据。

- ``obs.py``：连接 OBS，场景/采集/音频/录像开关、健康检查。
- ``policy.py``：每游戏默认策略（自动开关 / 一局一集 / 不分集）+ 脚本检查。
- ``auto.py``：自动玩会话（按策略开录、收尾、事件时间轴）。
- ``manual.py``：右键「OBS 录像」，按当前游戏策略手动开/停。
- ``replay.py``：``recordings/<游戏>/run-NNNN/run.mp4`` 的建目录、收尾、索引。
"""

from __future__ import annotations

from .obs import Obs, ObsError, load_ws_config
from .replay import (
    RECORDINGS,
    create_run,
    finalize,
    get_current,
    migrate_legacy,
    scan,
    start_recording,
    stop_recording,
    write_index,
)

__all__ = [
    "Obs", "ObsError", "load_ws_config",
    "RECORDINGS", "create_run", "finalize", "get_current", "migrate_legacy",
    "scan", "start_recording", "stop_recording", "write_index",
]
