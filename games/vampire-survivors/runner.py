"""《吸血鬼幸存者》的自动玩循环（本地 API 版）。

和 toy-duel / STS2 / 云观影共用 ``AutoplayHost``：状态、停止、日志、Token 都由
核心提供，本包只决定「下一步怎么动」。

- 所有观察/动作走游戏侧桥的 ``127.0.0.1:8091``，不找窗口、不切前台、不发键盘；
- ``local``：本地走位策略，不调用模型；
- ``llm``：每 ``LLM_INTERVAL_S`` 秒问一次模型要 4–8 步微观走位，中间用本地
  走位衔接；模型超时/输出不可执行时记 ``llm_fallback`` 并降级。

这个游戏是实时的：循环不调用 ``host.wait_voice()``，否则角色会站着被怪打死。
"""

from __future__ import annotations

import os
import threading
import time

from dafeiyu.agent.host import AutoplayHost
from dafeiyu.core.config import LiveConfig

from .engine import GAME_ID, VampireSurvivorsTools

DISPLAY_NAME = "吸血鬼幸存者"
LLM_INTERVAL_S = 12.0
LAUNCH_WAIT_S = 180.0
MAX_FAILURES = 8
DIRECTIONS = ("right", "down", "left", "up")

_host = AutoplayHost(GAME_ID)
_lock = _host.lock
_thread: threading.Thread | None = None
_planner = None


def bind_host(host: AutoplayHost) -> AutoplayHost:
    global _host, _lock
    if _thread is not None and _thread.is_alive() and host is not _host:
        _host.log("bind_host_ignored", reason="survivors thread alive")
        return _host
    _host = host
    _lock = host.lock
    return host


def _active() -> bool:
    return bool(_thread is not None and _thread.is_alive())


def _status() -> dict:
    return _host.status()


def _log(msg: str, **data) -> None:
    try:
        _host.log(msg, **data)
    except Exception:
        pass


def _add_usage(usage: dict | None, note: str, step: int) -> None:
    try:
        _host.add_usage(usage, note=note, step=step)
    except Exception:
        pass


def _cardinal(dx: float, dy: float) -> str:
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


def _local_plan(ctx: dict, step: int) -> dict:
    """本地走位：升级选第一项；贴脸先撤，远一点就压上去打，顺路吃经验。"""
    screen = str(ctx.get("screen") or "").upper()
    if screen == "LEVEL_UP":
        choices = ctx.get("choices") or []
        key = str((choices[0] or {}).get("key") or "1") if choices else "1"
        return {"seq": [{"press": key}]}
    if screen in ("MENU", "GAME_OVER", "LOADING", ""):
        return {"seq": [{"wait": 0.6}]}

    hp = float(ctx.get("hp") or 0)
    max_hp = float(ctx.get("max_hp") or 0)
    low = bool(max_hp and hp / max_hp < 0.35)
    enemies = [e for e in (ctx.get("enemies") or [])
               if isinstance(e, dict) and (e.get("x") or e.get("y"))]
    nearest = sorted(enemies, key=lambda e: float(e.get("x") or 0) ** 2
                     + float(e.get("y") or 0) ** 2)[:5] if enemies else []
    dist2 = (float(nearest[0].get("x") or 0) ** 2
             + float(nearest[0].get("y") or 0) ** 2) if nearest else 0.0

    if nearest and (dist2 < 1.2 ** 2 or low):
        ax = sum(float(e.get("x") or 0) for e in nearest)
        ay = sum(float(e.get("y") or 0) for e in nearest)
        away = _cardinal(-ax, -ay)
        side = "down" if away in ("left", "right") else "right"
        return {"seq": [
            {"move": away, "seconds": 1.0},
            {"move": side, "seconds": 0.5},
        ]}

    gems = [g for g in (ctx.get("gems") or [])
            if isinstance(g, dict) and (g.get("x") or g.get("y"))]
    if gems:
        gem = min(gems, key=lambda g: float(g.get("x") or 0) ** 2
                  + float(g.get("y") or 0) ** 2)
        gx, gy = float(gem.get("x") or 0), float(gem.get("y") or 0)
        gdist2 = gx * gx + gy * gy
        if not nearest or gdist2 < dist2 * 0.8:
            return {"seq": [{"move": _cardinal(gx, gy), "seconds": 0.9}]}

    if nearest:
        ex = float(nearest[0].get("x") or 0)
        ey = float(nearest[0].get("y") or 0)
        if dist2 > 2.0 ** 2:
            # 压上去让鞭子/武器朝怪群打；贴脸时已经切到上面的撤退分支。
            return {"seq": [{"move": _cardinal(ex, ey), "seconds": 0.9}]}
        # 中距离绕侧面，保持移动又不正对怪口。
        if abs(ex) >= abs(ey):
            side = "down" if ey <= 0 else "up"
        else:
            side = "right" if ex <= 0 else "left"
        return {"seq": [
            {"move": side, "seconds": 0.8},
            {"move": _cardinal(-ex, -ey), "seconds": 0.4},
        ]}

    first = DIRECTIONS[step % len(DIRECTIONS)]
    second = DIRECTIONS[(step + 1) % len(DIRECTIONS)]
    return {"seq": [
        {"move": first, "seconds": 0.9},
        {"move": second, "seconds": 0.9},
    ]}


def _planner_for(cfg: LiveConfig):
    """LLM 模式用；每次启动按当前配置新建，避免拿到旧 Key。"""
    from dafeiyu.agent.planner import LLMPlanner
    from dafeiyu.persona.layers import prompt_prefix

    from .prompts import DECISION_PROMPT

    prefix = prompt_prefix(cfg, game_id="vampire-survivors", live=True)
    return LLMPlanner(
        cfg, timeout=45.0, model=str(cfg.autoplay_llm_model or cfg.llm_model),
        system_prompt=(prefix + "\n\n" + DECISION_PROMPT).strip(), thinking=False,
    )


def _ask_llm(tools: VampireSurvivorsTools, planner, ctx: dict, step: int) -> dict | None:
    """问模型要一段微观走位；不可执行时返回 None，由调用方降级。"""
    if planner is None:
        return None
    goal = ("吸血鬼幸存者：按局面给 4–8 步走位，躲开贴脸怪、优先吃经验；"
            "升级界面按选项编号选强化。只输出约定 JSON。")
    try:
        result = planner.plan(ctx, goal=goal)
    except Exception as exc:  # noqa: BLE001
        _log("llm_fallback", step=step, reason=f"{type(exc).__name__}: {exc}"[:160])
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        _log("llm_fallback", step=step,
             reason=str((result or {}).get("error") or "模型没有返回计划")[:160])
        return None
    _add_usage(result.get("usage"), f"幸存者 走位 {step}", step)
    checked = tools.validate(result.get("plan") or {})
    if not checked.ok:
        _log("llm_fallback", step=step, reason=checked.message[:160])
        return None
    seq = list(checked.data.get("seq") or [])
    return {"seq": seq} if seq else None


def _wait_for_api(tools: VampireSurvivorsTools, seconds: float) -> bool:
    deadline = time.time() + max(0.0, float(seconds))
    while time.time() < deadline:
        if _host.stopping():
            return False
        if tools.available():
            return True
        _host.sleep(0.5)
    return tools.available()


def _loop(host: AutoplayHost, mode: str, planner, pace: str) -> None:
    global _thread
    tools = VampireSurvivorsTools(stop_check=host.stopping)
    if mode == "llm" and planner is None:
        planner = _planner_for(LiveConfig.load())
        ok, why = planner.available()
        if not ok:
            host.set_status(running=False, stopping=False, error=why,
                            last="LLM 不可用")
            with _lock:
                _thread = None
            return
    delay = 0.05 if pace == "normal" else 0.15
    step = 0
    failures = 0
    last_start = 0.0
    recording = False
    rec_attempted = False
    try:
        from dafeiyu.recording import policy

        rec_split = str(policy.policy_for(GAME_ID).get("split") or "run")
    except Exception:  # noqa: BLE001
        rec_split = "run"
    # 先本地走 5 秒再问模型：实时游戏不能一上来就站着等 1–2 秒的 API。
    last_llm_at = time.time() - LLM_INTERVAL_S + 5.0
    stopped = False
    try:
        if not tools.available():
            host.set_status(screen="LAUNCHING", last="启动游戏…")
            _log("survivors_launch", game=GAME_ID)
            try:
                from dafeiyu.games.launch import launch_game

                launched = launch_game(GAME_ID)
                _log("survivors_launch_result", ok=bool(launched.get("ok")),
                     message=str(launched.get("message") or
                                 launched.get("error") or "")[:160])
            except Exception as exc:  # noqa: BLE001
                _log("survivors_launch_failed", error=f"{type(exc).__name__}: {exc}"[:160])
            if not _wait_for_api(tools, LAUNCH_WAIT_S):
                host.set_status(
                    running=False, stopping=False, screen="",
                    error="等不到 VS 本地 API（确认游戏已启动、BepInEx 桥已装好）",
                    last="等不到 VS API")
                _log("survivors_no_api")
                return

        try:
            tools.start_run()
        except Exception as exc:  # noqa: BLE001
            _log("survivors_start_run_failed",
                 error=f"{type(exc).__name__}: {exc}"[:160])

        while not host.stopping():
            step += 1
            ctx = tools.snapshot()
            if ctx.get("source") != "api":
                failures += 1
                host.set_status(last="VS API 断开", error="VS API 断开")
                if failures >= MAX_FAILURES:
                    host.set_status(
                        running=False, stopping=False, screen="",
                        error="VS 本地 API 断开（游戏退出或插件停了）",
                        last="API 断开")
                    _log("survivors_api_lost", step=step)
                    return
                host.sleep(1.0)
                continue

            screen = str(ctx.get("screen") or "").upper()
            if screen == "PLAYING" and not recording and not rec_attempted:
                rec_attempted = True
                res = host.start_recording(note="自动玩 · 吸血鬼幸存者") or {}
                recording = bool(res.get("ok") and res.get("recording", True))
            elif screen == "GAME_OVER" and recording and rec_split == "run":
                host.stop_recording("run_end")
                recording = False
                rec_attempted = False
            if screen in ("MENU", "GAME_OVER", "LOADING", ""):
                # 菜单/结算通常由桥自己推进；这里补一次 start，防止桥的
                # 自动流程在异常后停住（节流，避免每帧刷请求）。
                now_start = time.time()
                if screen in ("MENU", "GAME_OVER") and now_start - last_start > 2.0:
                    last_start = now_start
                    try:
                        tools.start_run()
                    except Exception as exc:  # noqa: BLE001
                        _log("survivors_restart_failed",
                             error=f"{type(exc).__name__}: {exc}"[:120])
                failures = 0
                host.set_status(
                    running=True, game=GAME_ID, mode=mode, pace=pace,
                    screen=screen, step=step,
                    last={"MENU": "菜单中…", "GAME_OVER": "结算中…",
                          "LOADING": "读盘中…"}.get(screen, "等待中…"),
                    error="",
                )
                host.sleep(0.5)
                continue

            plan = None
            now = time.time()
            if (mode == "llm" and screen == "PLAYING"
                    and now - last_llm_at >= LLM_INTERVAL_S):
                last_llm_at = now
                plan = _ask_llm(tools, planner, ctx, step)
            if plan is None:
                plan = _local_plan(ctx, step)

            result = tools.execute(plan, dry_run=False)
            if not result.ok:
                failures += 1
                host.set_status(last=result.message[:120],
                                error=result.message[:200])
                _log("survivors_action_error", step=step,
                     code=result.code, error=result.message[:160])
                if failures >= MAX_FAILURES:
                    host.set_status(running=False, stopping=False,
                                    error=result.message[:200],
                                    last="连续失败，停手")
                    return
                host.sleep(0.6)
                continue

            failures = 0
            after = dict(result.data.get("context") or ctx)
            host.set_status(
                running=True, game=GAME_ID, mode=mode, pace=pace,
                screen=after.get("screen") or "",
                floor=int(after.get("level") or 0),
                hp=int(after.get("hp") or 0),
                max_hp=int(after.get("max_hp") or 0),
                kills=int(after.get("kills") or 0),
                level=int(after.get("level") or 0),
                stage=str(after.get("stage") or ""),
                step=step,
                last=result.message,
                error="",
            )
            _log("survivors_step", step=step, mode=mode,
                 screen=after.get("screen"), level=after.get("level"),
                 sent=result.data.get("sent"))
            host.sleep(delay)
    except Exception as exc:  # noqa: BLE001
        host.set_status(error=f"{type(exc).__name__}: {exc}", last="异常停止")
        _log("survivors_fatal", error=f"{type(exc).__name__}: {exc}"[:240])
    finally:
        if recording:
            try:
                host.stop_recording("autoplay_end")
            except Exception:  # noqa: BLE001
                pass
        stopped = stopped or host.stopping()
        try:
            host.set_status(
                running=False, stopping=False,
                last="已停手" if stopped else "这一局结束",
            )
        finally:
            with _lock:
                _thread = None


def start(game: str = "", *, mode: str | None = None,
          pace: str | None = None) -> dict:
    global _thread, _planner
    mode = str(mode or "local").strip().lower()
    if mode not in ("local", "llm"):
        return {"ok": False, "game": GAME_ID,
                "message": "吸血鬼幸存者只支持 local / llm 模式"}
    pace = str(pace or _host.pace or "voice").strip().lower()
    if pace not in ("voice", "normal"):
        return {"ok": False, "game": GAME_ID, "message": "节奏只能是 voice 或 normal"}

    planner = None
    if mode == "llm":
        try:
            planner = _planner_for(LiveConfig.load())
            ok, why = planner.available()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "game": GAME_ID,
                    "message": f"LLM 初始化失败：{type(exc).__name__}: {exc}"}
        if not ok:
            return {"ok": False, "game": GAME_ID, "message": why}

    with _lock:
        if _active():
            return {"ok": True, "already": True, "game": GAME_ID,
                    "message": "她已经在打了", "status": _status()}
        current = _host.status()
        if (current.get("running")
                and int(current.get("pid") or 0) not in (0, os.getpid())
                and _host.pid_alive(current.get("pid"))):
            return {"ok": True, "already": True, "game": GAME_ID,
                    "message": "她已经在打了", "status": current}
        _host.clear_stop()
        _host.reset(GAME_ID, mode=mode, pace=pace)
        _host.set_status(
            running=True, game=GAME_ID, mode=mode, pace=pace,
            screen="STARTING", step=0, last="启动中…", error="",
            started_at=time.time(), pid=os.getpid(), stopping=False,
        )
        _log("survivors_start", mode=mode, pace=pace)
        _thread = threading.Thread(
            target=_loop, args=(_host, mode, planner, pace),
            name="dafeiyu-vampire-survivors", daemon=True)
        _thread.start()
        return {"ok": True, "game": GAME_ID, "mode": mode, "pace": pace,
                "message": "吸血鬼幸存者已开始", "status": _status()}


def stop() -> dict:
    global _thread
    _host.request_stop()
    try:
        VampireSurvivorsTools().stop_control()
    except Exception:
        pass
    thread = _thread
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(3.0)

    current = _host.status()
    remote = bool(
        current.get("running")
        and int(current.get("pid") or 0) not in (0, os.getpid())
        and _host.pid_alive(current.get("pid"))
    )
    if remote or (thread is not None and thread.is_alive()):
        _host.set_status(stopping=True, last="正在停手…")
        return {"ok": True, "message": "正在停手…", "status": current}

    _thread = None
    _host.clear_stop()
    _host.set_status(running=False, stopping=False, last="已停手")
    return {"ok": True, "message": "已停手", "status": _host.status()}


def status() -> dict:
    return _host.status()


__all__ = ["GAME_ID", "bind_host", "start", "status", "stop"]
