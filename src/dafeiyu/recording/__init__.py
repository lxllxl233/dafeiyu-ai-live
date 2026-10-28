"""录制层：OBS WebSocket 控制 + 一局一个录像的目录/元数据。

- ``obs.py``：连接 OBS，场景/采集/音频/录像开关、健康检查。
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
