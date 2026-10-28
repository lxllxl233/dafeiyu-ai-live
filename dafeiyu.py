#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：真实代码在 src/dafeiyu/cli.py。

    python dafeiyu.py studio
    python dafeiyu.py demo
    python dafeiyu.py --version

（`src/` 不入 sys.path，所以这里先把它插到最前面；再把仓库根从 path 里摘掉，
避免这个同名文件反过来遮住 `dafeiyu` 包。）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
sys.path[:] = [SRC] + [
    p for p in sys.path
    if os.path.abspath(p or os.getcwd()) != HERE
]

from dafeiyu.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
