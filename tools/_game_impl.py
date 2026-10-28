"""兼容层加载器：把 ``tools/*.py`` 旧入口指向游戏包实现。

真正的代码已迁到 ``games/slay-the-spire-2/decision/``；这个文件只负责在
``tools`` 目录仍位于 ``sys.path`` 时，按旧模块名加载同一份实现，避免复制。
"""
from __future__ import annotations

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECISION = os.path.join(ROOT, "games", "slay-the-spire-2", "decision")


def load(module: str):
    """加载 ``DECISION/<module>.py`` 到稳定的私有模块名。"""
    if DECISION in sys.path:
        sys.path.remove(DECISION)
    sys.path.insert(0, DECISION)
    name = f"sts2_decision_{module}"
    if name in sys.modules:
        return sys.modules[name]
    target = os.path.join(DECISION, f"{module}.py")
    spec = importlib.util.spec_from_file_location(name, target)
    if spec is None or spec.loader is None:
        raise ImportError(f"找不到游戏实现：{target}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


def run(module: str, *, argv: bool = False) -> int:
    """跑迁移后的 CLI；没有 ``main`` 的库模块返回 0。"""
    mod = load(module)
    main = getattr(mod, "main", None)
    if main is None:
        return 0
    result = main(sys.argv[1:]) if argv else main()
    return int(result or 0)
