#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大肥鱼直播姬 · 唯一根入口。

    python main.py                 起桌宠（大肥鱼形象 + 右键菜单）
    python main.py --preview x.png 只渲染一张图（验收/截图）
    python main.py studio          起 overlay + 直播姬控制台
    python main.py replay list     录像回放
    python main.py doctor          环境自检

`python main.py` 默认顺带在后台起 overlay 服务；端口已被占用就复用，
不会抢正在跑的 `python main.py studio`。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

_GUI_ALIASES = {"pet", "gui", "desktop"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    is_gui = not argv or argv[0] in _GUI_ALIASES or argv[0].startswith("-")
    if not is_gui:
        from dafeiyu.cli import main as cli_main

        return int(cli_main(argv) or 0)

    if argv and argv[0] in _GUI_ALIASES:
        argv = argv[1:]
    if argv[:1] in (["--version"], ["-V"]):
        from dafeiyu import __version__

        print(f"dafeiyu-ai-live {__version__}")
        return 0
    # overlay 服务由桌宠进程里的 ensure_server 拉起（和 OBS 用同一个服务）：
    # 放在桌宠启动之后，重启时才能等旧进程把端口让出来再绑。
    from dafeiyu.ui.desktop import main as pet_main

    return int(pet_main(argv) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
