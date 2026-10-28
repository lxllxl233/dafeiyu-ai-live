"""玩具决斗的自动玩循环：和 STS2 共用 ``AutoplayHost``，但不启动外部程序。

这个循环刻意做得短（``MAX_STEPS`` 步）：它验证的是插件契约，不是游戏内容。
每一步都走 ``ToyTools.observe -> plan -> execute``，因此控制面和状态面是同一套。
"""

from __future__ import annotations

import os
import threading
import time

from dafeiyu.agent.host import AutoplayHost

from . import engine

GAME_ID = engine.GAME_ID

_host = AutoplayHost(GAME_ID)
_lock = _host.lock
_thread: threading.Thread | None = None


def bind_host(host: AutoplayHost) -> AutoplayHost:
    global _host, _lock
    _host = host
    _lock = host.lock
    return host


def _active() -> bool:
    return bool(_thread is not None and _thread.is_alive())


def _pace_seconds(pace: str) -> float:
    return 0.05 if str(pace).lower() == "normal" else 0.15


def _loop(host: AutoplayHost, pace: str) -> None:
    global _thread
    tools = engine.ToyTools()
    delay = _pace_seconds(pace)
    step = 0
    stopped = False
    try:
        while step < engine.MAX_STEPS:
            if host.stopping():
                stopped = True
                break
            step += 1
            observed = tools.observe()
            if not observed.ok:
                raise RuntimeError(observed.message or "observe 失败")
            ctx = dict(observed.data.get("context") or {})
            host.set_status(
                running=True,
                game=GAME_ID,
                screen=ctx.get("screen", ""),
                floor=ctx.get("turn", 0),
                hp=(ctx.get("hero") or {}).get("hp", 0),
                max_hp=(ctx.get("hero") or {}).get("max_hp", 0),
                step=step,
                last=ctx.get("last", ""),
            )
            if ctx.get("screen") != "COMBAT":
                host.log("toy_round_end", screen=ctx.get("screen"), step=step)
                host.set_status(last=f"{ctx.get('screen')} · 准备下一局")
                host.sleep(0.2)
                if host.stopping():
                    stopped = True
                    break
                engine.new_state(run=step + 1)
                continue

            plan = engine.plan_for(ctx)
            result = tools.execute(plan, dry_run=False)
            if not result.ok:
                host.set_status(error=result.message, last="动作失败")
                host.log("toy_action_error", step=step, error=result.message)
                break
            after = dict(result.data.get("context") or {})
            host.set_status(
                screen=after.get("screen", ""),
                floor=after.get("turn", 0),
                hp=(after.get("hero") or {}).get("hp", 0),
                max_hp=(after.get("hero") or {}).get("max_hp", 0),
                step=step,
                last=result.message,
            )
            host.log(
                "toy_step",
                step=step,
                seq=plan.get("seq"),
                message=result.message,
                receipt=result.data.get("receipt"),
            )
            host.sleep(delay)
    except Exception as exc:  # noqa: BLE001
        host.set_status(error=f"{type(exc).__name__}: {exc}", last="演示异常")
        host.log("toy_fatal", error=f"{type(exc).__name__}: {exc}")
    finally:
        stopped = stopped or host.stopping()
        try:
            host.set_status(
                running=False,
                stopping=False,
                last="已停手" if stopped else "演示结束",
            )
        finally:
            with _lock:
                _thread = None


def start(game: str = "", *, mode: str | None = None,
          pace: str | None = None) -> dict:
    """开始演示；立即返回，循环在后台线程。"""
    global _thread
    mode = str(mode or _host.mode or "local").strip().lower()
    if mode != "local":
        return {"ok": False, "game": GAME_ID,
                "message": "玩具决斗只支持 local 模式（它不调用模型）"}
    pace = str(pace or _host.pace or "voice").strip().lower()
    if pace not in ("voice", "normal"):
        return {"ok": False, "game": GAME_ID,
                "message": "节奏只能是 voice 或 normal"}
    with _lock:
        if _active():
            return {"ok": True, "already": True, "game": GAME_ID,
                    "message": "她已经在玩了", "status": status()}
        current = _host.status()
        if (current.get("running")
                and int(current.get("pid") or 0) not in (0, os.getpid())
                and _host.pid_alive(current.get("pid"))):
            return {"ok": True, "already": True, "game": GAME_ID,
                    "message": "她已经在玩了", "status": current}
        _host.clear_stop()
        _host.reset(GAME_ID, mode=mode, pace=pace)
        engine.new_state()
        _host.set_status(
            running=True, game=GAME_ID, mode=mode, pace=pace,
            screen="COMBAT", step=0, last="启动中…", error="",
            started_at=time.time(), pid=os.getpid(), stopping=False,
        )
        _host.log("toy_start", mode=mode, pace=pace)
        _thread = threading.Thread(target=_loop, args=(_host, pace),
                                   name="dafeiyu-toy-duel", daemon=True)
        _thread.start()
        return {"ok": True, "game": GAME_ID, "mode": mode, "pace": pace,
                "message": "玩具决斗已开始", "status": status()}


def stop() -> dict:
    """停止演示；同进程 join，跨进程靠 stop 文件并等远端收尾。"""
    global _thread
    _host.request_stop()
    thread = _thread
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(2.0)

    current = _host.status()
    remote = bool(
        current.get("running")
        and int(current.get("pid") or 0) not in (0, os.getpid())
        and _host.pid_alive(current.get("pid"))
    )
    if remote:
        # play --stop 在另一个进程：给远端最多 1.5 秒收尾，期间保留停止文件。
        deadline = time.time() + 1.5
        while time.time() < deadline and current.get("running"):
            time.sleep(0.05)
            current = _host.status()
        remote = bool(
            current.get("running")
            and int(current.get("pid") or 0) not in (0, os.getpid())
            and _host.pid_alive(current.get("pid"))
        )
    if remote or (thread is not None and thread.is_alive()):
        _host.set_status(stopping=True, last="正在停手…")
        return {"ok": True, "message": "正在停手…", "status": _host.status()}

    _thread = None
    _host.clear_stop()
    _host.set_status(running=False, stopping=False, last="已停手")
    return {"ok": True, "message": "已停手", "status": _host.status()}


def status() -> dict:
    return _host.status()


__all__ = ["GAME_ID", "bind_host", "start", "status", "stop"]
