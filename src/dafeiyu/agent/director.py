"""LLM 直播导演：决定这一段直播播什么，并自己切模块。

核心不写死任何游戏：``games/registry.json`` 里每个 ``game.json`` 只要声明了

    "live": {"enabled": true, "label": "刷视频", "role": "适合什么时候播"}

就会被列进候选。导演每隔一段就把「候选模块 + 最近播过什么」交给模型，模型
只回一个 JSON：

    {"module": "<模块 id>", "minutes": 20, "say": "…", "reason": "…"}

然后导演停掉当前模块、把 ``config.game`` 切过去、按模块声明的模式重新开播。
模型不可用/输出不可执行时按顺序轮换（记 ``director_fallback``），不会让直播间
停在空气上。

状态写 ``logs/live_director.json``（跨进程可读），停止标志是
``logs/live_director.stop``；Token 账单仍然走 ``AutoplayHost.add_usage``，
note 写「直播导演 选择模块」，控制台按这一步能看到花销。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import paths
from ..core.config import LiveConfig
from ..games.registry import GameRegistry
from .host import AutoplayHost

# 一段播多久（分钟）：模型给的建议会被夹在这个区间里。
MIN_SEGMENT_MIN = 5
MAX_SEGMENT_MIN = 90
DEFAULT_SEGMENT_MIN = 20
HISTORY_MAX = 12
# 刚切过去时给模块的启动宽限；太早判断「没在跑」会误切。
IDLE_GRACE_S = 120.0
# 模块启动失败后退避多久再问模型。
RETRY_BACKOFF_S = 45.0

STATUS_PATH = paths.LOGS / "live_director.json"
STOP_PATH = paths.LOGS / "live_director.stop"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_status(path: Path | None = None) -> dict:
    try:
        raw = json.loads((path or STATUS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        except OSError:
            pass


@dataclass(frozen=True)
class LiveModule:
    """一个可直播模块（一个 game.json 的 ``live`` 声明）。"""

    id: str
    label: str
    role: str = ""
    name: str = ""
    tags: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()
    status: str = "ready"
    weight: int = 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "role": self.role,
            "name": self.name,
            "tags": list(self.tags),
            "modes": list(self.modes),
            "status": self.status,
            "weight": self.weight,
        }


def modules(registry: Any = None) -> list[LiveModule]:
    """扫描 ``games/``：有 ``live.enabled`` 且有自动玩能力的包才算模块。"""
    try:
        packs = (registry or GameRegistry()).packs()
    except Exception:
        return []
    out: list[LiveModule] = []
    for pack in packs:
        raw = pack.manifest.get("live") if isinstance(pack.manifest, dict) else None
        if not isinstance(raw, dict) or not raw.get("enabled"):
            continue
        caps = pack.contract.capabilities if pack.contract else None
        if caps is None or not caps.autoplay:
            continue
        label = str(raw.get("label") or pack.info.name or pack.info.id).strip()
        role = " ".join(str(raw.get("role") or pack.info.notes or "").split())
        out.append(LiveModule(
            id=pack.info.id,
            label=label or pack.info.id,
            role=role[:240],
            name=pack.info.name or label,
            tags=tuple(pack.info.tags),
            modes=tuple(caps.modes),
            status=pack.info.status,
            weight=max(1, min(5, _int(raw.get("weight"), 1))),
        ))
    return out


def build_prompt(items: list[LiveModule]) -> str:
    lines = []
    for item in items:
        tags = "/".join(item.tags[:4]) or "无"
        lines.append(f"- {item.id}｜{item.label}｜标签：{tags}｜适合：{item.role or '未说明'}")
    return (
        "你是这个直播间的导演，负责决定下一段直播播什么。\n"
        "可选的直播模块（只能从这些 id 里选一个）：\n"
        + "\n".join(lines)
        + "\n\n只输出一个 JSON 对象，不要解释、不要 Markdown：\n"
        '{"module": "<模块 id>", "minutes": 20, "say": "对观众说的一句话", '
        '"reason": "为什么现在选它"}\n'
        "规则：\n"
        f"1. module 必须是上面出现过的 id；minutes 取 {MIN_SEGMENT_MIN}–{MAX_SEGMENT_MIN} 的整数；\n"
        "2. say 不超过 40 个字，像主播自然换节目时说的一句话，不要报菜名；\n"
        "3. 不要连续三次选同一个模块；刚播了很短一段可以多留一会儿；\n"
        "4. reason 用一句中文说明理由，方便观众和回放看懂。"
    )


def parse_decision(plan: Any, items: list[LiveModule]) -> dict | None:
    """把模型输出夹成可执行决定；id/显示名都认，其余一律判无效。"""
    if not isinstance(plan, dict):
        return None
    raw_id = str(plan.get("module") or plan.get("game") or "").strip()
    if not raw_id:
        return None
    key = raw_id.lower()
    chosen = None
    for item in items:
        if key in (item.id.lower(), item.label.lower(), item.name.lower()):
            chosen = item
            break
    if chosen is None:
        return None
    minutes = _int(plan.get("minutes"), DEFAULT_SEGMENT_MIN)
    minutes = max(MIN_SEGMENT_MIN, min(MAX_SEGMENT_MIN, minutes))
    say = " ".join(str(plan.get("say") or "").split())[:80]
    reason = " ".join(str(plan.get("reason") or "").split())[:240]
    return {"module": chosen, "minutes": minutes, "say": say, "reason": reason}


class _UsageHost(AutoplayHost):
    """只借 ``add_usage`` 记账，不覆盖正在跑的模块状态文件。"""

    def _write_locked(self) -> None:  # noqa: D401 - 有意 no-op
        return

    def event(self, kind: str, label: str = "", **extra) -> None:
        return


class LiveDirector:
    """一个进程内的直播导演；跨进程状态仍靠文件。"""

    name = "director"

    def __init__(
        self,
        *,
        planner_factory: Callable[[list[LiveModule]], Any] | None = None,
        registry: Any = None,
        status_path: Path | None = None,
        stop_path: Path | None = None,
        global_stop_path: Path | None = None,
        overlay_url: str = "",
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._planner_factory = planner_factory
        self._registry = registry
        self._status_path = Path(status_path) if status_path else STATUS_PATH
        self._stop_path = Path(stop_path) if stop_path else STOP_PATH
        self._global_stop_path = (Path(global_stop_path) if global_stop_path
                                  else paths.LOGS / "autoplay.stop")
        self._overlay_url = str(overlay_url or "")
        self._now = now
        self._sleep_fn = sleep
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: dict = dict(_read_status(self._status_path)) or {}
        self._last.setdefault("running", False)
        self._last.setdefault("phase", "idle")
        self._last.setdefault("history", [])
        self._tokens = _int(self._last.get("tokens"))
        self._calls = _int(self._last.get("calls"))
        self._segments = _int(self._last.get("segments"))
        self._session_started = 0.0

    # ---------------------------------------------------------------- 候选
    def modules(self) -> list[LiveModule]:
        return modules(self._registry)

    # ---------------------------------------------------------------- 状态
    def _write(self, **fields) -> dict:
        with self._lock:
            self._last.update(fields)
            self._last["updated_at"] = self._now()
            self._last["tokens"] = self._tokens
            self._last["calls"] = self._calls
            payload = dict(self._last)
        _write_json(self._status_path, payload)
        return payload

    def _alive(self) -> bool:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return True
        raw = _read_status(self._status_path)
        if not raw.get("running"):
            return False
        pid = _int(raw.get("pid"))
        if not pid or pid == os.getpid():
            return False
        try:
            return bool(AutoplayHost.pid_alive(pid))
        except Exception:
            return False

    def status(self) -> dict:
        with self._lock:
            merged = dict(self._last)
        file_state = _read_status(self._status_path)
        if file_state:
            merged.update(file_state)
        alive = self._alive()
        if merged.get("running") and not alive:
            merged["running"] = False
            merged["phase"] = "stopped"
        merged["alive"] = alive
        merged["modules"] = [item.to_dict() for item in self.modules()]
        return merged

    def available(self) -> tuple[bool, str]:
        if self._planner_factory is not None:
            return True, ""
        try:
            cfg = LiveConfig.load()
        except Exception as exc:  # noqa: BLE001
            return False, f"读配置失败：{type(exc).__name__}: {exc}"
        if not str(cfg.llm_api_key or "").strip():
            return False, "未配置 API Key（控制台「基础配置」或 config.json）"
        return True, ""

    # ---------------------------------------------------------------- 启停
    def start(self, *, module: str | None = None) -> dict:
        current = self.status()
        if current.get("running") and current.get("alive"):
            return {"ok": True, "already": True,
                    "message": "直播模式已经在跑", "status": current}
        # 上一次 stop 还在收尾（线程在 LLM 调用里 / 别的进程还没退出）时不能开新的，
        # 否则两个导演循环会同时切模块。
        thread = self._thread
        if thread is not None and thread.is_alive():
            return {"ok": False, "message": "直播模式正在停止，等它停完再开"}
        raw = _read_status(self._status_path)
        if str(raw.get("phase") or "") == "stopping":
            pid = _int(raw.get("pid"))
            if pid and pid != os.getpid() and AutoplayHost.pid_alive(pid):
                return {"ok": False, "message": "直播模式正在停止，等它停完再开"}
        items = self.modules()
        if not items:
            return {"ok": False,
                    "message": "games/ 下没有可直播模块（game.json 需要 live.enabled）"}
        if module and not any(item.id == module for item in items):
            return {"ok": False, "message": f"没有这个直播模块：{module}"}
        ok, why = self.available()
        if not ok:
            return {"ok": False, "message": why}
        try:
            if self._stop_path.exists():
                self._stop_path.unlink()
        except OSError:
            pass
        # 上一次会话遗留的全局停手文件也要清掉，否则刚开就会立刻停。
        try:
            self._global_stop_path.unlink()
        except OSError:
            pass
        self._stop.clear()
        self._segments = 0
        self._session_started = self._now()
        self._write(
            running=True, phase="thinking", module="", module_label="",
            reason="", say="", minutes=0, segments=0, history=[],
            pid=os.getpid(), started_at=self._now(),
            segment_started_at=0.0, next_at=0.0, error="",
        )
        self._thread = threading.Thread(
            target=self._loop, args=(module,), name="dafeiyu-live-director",
            daemon=True)
        self._thread.start()
        return {"ok": True, "message": "LLM 直播模式已开始",
                "status": self.status()}

    def stop(self) -> dict:
        was_alive = bool(self.status().get("alive"))
        self._stop.set()
        try:
            self._stop_path.parent.mkdir(parents=True, exist_ok=True)
            self._stop_path.write_text(str(self._now()), encoding="utf-8")
        except OSError:
            pass
        play: dict = {}
        try:
            from .play import stop_autoplay

            play = stop_autoplay()
        except Exception:
            play = {}
        thread = self._thread
        if thread is not None and thread.is_alive() \
                and thread is not threading.current_thread():
            thread.join(3.0)
        if thread is not None and thread.is_alive():
            # 线程还在 LLM 调用里：先标记 stopping，等它自己收尾写 stopped。
            self._write(running=False, phase="stopping", next_at=0.0, error="")
        elif thread is None and was_alive:
            # 导演在别的进程：那份 stop 文件由它消费，状态也由它改成 stopped。
            self._write(running=False, phase="stopping", next_at=0.0, error="")
        else:
            self._thread = None
            self._write(running=False, phase="stopped", next_at=0.0, error="")
        # 不在这里删 stop 文件：导演线程在别的进程时，要靠这份文件才能看到停止；
        # 它退出时由 _cleanup() 自己删。下一次 start() 也会先清掉旧文件。
        return {"ok": True, "message": "直播模式已停止", "play": play,
                "status": self.status()}

    # ---------------------------------------------------------------- 循环
    def _stopping(self) -> bool:
        """停止信号：本导演的 stop 文件，或用户按「让她停手」写的全局停手文件。"""
        try:
            if self._stop.is_set() or self._stop_path.exists():
                return True
            return (self._global_stop_path).exists()
        except OSError:
            return self._stop.is_set()

    def _log(self, msg: str, **data) -> None:
        try:
            self._usage_host.log(msg, **data)
        except Exception:
            pass

    @property
    def _usage_host(self) -> _UsageHost:
        host = getattr(self, "_usage_host_obj", None)
        if host is None:
            host = _UsageHost()
            self._usage_host_obj = host
        return host

    def _add_usage(self, usage: Any, note: str) -> None:
        try:
            result = self._usage_host.add_usage(usage, note=note)
        except Exception:
            return
        total = _int((result or {}).get("total"))
        if total > 0:
            self._tokens += total
            self._calls += 1

    def _make_planner(self, items: list[LiveModule]):
        if self._planner_factory is not None:
            return self._planner_factory(items)
        from .planner import LLMPlanner

        cfg = LiveConfig.load()
        return LLMPlanner(
            cfg, timeout=60.0,
            model=str(cfg.autoplay_llm_model or cfg.llm_model),
            system_prompt=build_prompt(items), thinking=False,
        )

    def _fallback(self, items: list[LiveModule]) -> dict:
        last = ""
        with self._lock:
            if self._last.get("history"):
                last = str((self._last["history"][-1] or {}).get("module") or "")
        ordered = list(items)
        if last:
            index = next((i for i, item in enumerate(items) if item.id == last), -1)
            ordered = items[index + 1:] + items[:index + 1]
        chosen = ordered[0]
        return {"module": chosen, "minutes": DEFAULT_SEGMENT_MIN,
                "say": f"换一段，来点{chosen.label}。",
                "reason": "模型不可用，按顺序轮换", "fallback": True}

    def _decide(self, items: list[LiveModule], feedback: str = "") -> dict:
        planner = self._make_planner(items)
        if self._planner_factory is None:
            try:
                ok, why = planner.available()
            except Exception as exc:  # noqa: BLE001
                ok, why = False, f"{type(exc).__name__}: {exc}"
            if not ok:
                self._log("llm_fallback", stage="director", reason=why)
                return self._fallback(items)
        context = {
            "modules": [item.to_dict() for item in items],
            "history": list(self._last.get("history") or [])[-HISTORY_MAX:],
            "session_minutes": (round((self._now() - self._session_started) / 60, 1)
                                if self._session_started else 0),
            "local_time": time.strftime("%Y-%m-%d %H:%M"),
        }
        try:
            result = planner.plan(
                context, feedback=feedback,
                goal="挑下一段直播内容，给一个模块 id、时长和一句过渡台词")
        except Exception as exc:  # noqa: BLE001
            self._log("llm_fallback", stage="director",
                      reason=f"{type(exc).__name__}: {exc}"[:200])
            return self._fallback(items)
        if isinstance(result, dict):
            self._add_usage(result.get("usage"), "直播导演 选择模块")
        if not isinstance(result, dict) or not result.get("ok"):
            reason = str((result or {}).get("error") or "模型没有返回计划")[:200]
            self._log("llm_fallback", stage="director", reason=reason)
            return self._fallback(items)
        parsed = parse_decision(result.get("plan"), items)
        if parsed is None:
            self._log("llm_fallback", stage="director", reason="模型输出不是合法模块",
                      plan=str(result.get("plan"))[:200])
            return self._fallback(items)
        return parsed

    def _switch(self, item: LiveModule) -> dict:
        from .play import start_autoplay, stop_autoplay

        try:
            stop_autoplay()
        except Exception as exc:  # noqa: BLE001
            self._log("director_previous_stop_failed",
                      error=f"{type(exc).__name__}: {exc}"[:160])
        try:
            LiveConfig.load().save_patch({"game": item.id})
        except Exception as exc:  # noqa: BLE001
            self._log("director_config_failed",
                      error=f"{type(exc).__name__}: {exc}"[:160])
        mode = ("llm" if "llm" in item.modes
                else ("local" if "local" in item.modes else "live"))
        try:
            return start_autoplay(game=item.id, mode=mode)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "game": item.id,
                    "message": f"{type(exc).__name__}: {exc}"}

    def _play_status(self) -> dict:
        try:
            from .play import status as play_status

            return play_status()
        except Exception:
            return {}

    def _sleep_until(self, deadline: float) -> None:
        """分段睡到 deadline，期间响应停止信号（失败退避不能卡住停手）。"""
        while not self._stopping():
            left = deadline - self._now()
            if left <= 0:
                return
            self._sleep_fn(min(1.0, max(0.05, left)))

    def _wait_segment(self, deadline: float, module_id: str) -> None:
        grace = self._now() + IDLE_GRACE_S
        idle = 0
        while not self._stopping():
            now = self._now()
            if now >= deadline:
                return
            self._sleep_fn(min(2.0, max(0.2, deadline - now)))
            if self._stopping():
                return
            now = self._now()
            if now < grace:
                continue
            state = self._play_status()
            if state.get("running"):
                idle = 0
                continue
            idle += 1
            if idle >= 3:
                self._log("director_segment_ended", module=module_id,
                          last=str(state.get("last") or "")[:160])
                return

    def _post_say(self, text: str) -> None:
        text = " ".join(str(text or "").split())[:80]
        if not text:
            return
        try:
            cfg = LiveConfig.load()
            base = (self._overlay_url or cfg.overlay_url
                    or f"http://{cfg.host}:{cfg.port}").rstrip("/")
            payload = {
                "speech": text, "tag": "直播导演",
                "client_id": f"director-{uuid.uuid4().hex[:12]}",
            }
            request = urllib.request.Request(
                base + "/comment",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=2.0) as response:
                response.read()
        except Exception as exc:  # noqa: BLE001
            self._log("director_say_failed",
                      error=f"{type(exc).__name__}: {exc}"[:160])

    def _announce(self, decision: dict) -> None:
        text = str(decision.get("say") or "").strip()
        if not text:
            return
        try:
            threading.Thread(target=self._post_say, args=(text,),
                             name="dafeiyu-director-say", daemon=True).start()
        except Exception:
            pass

    def _loop(self, forced_module: str | None = None) -> None:
        error = ""
        try:
            while not self._stopping():
                items = self.modules()
                if not items:
                    error = "没有可直播模块"
                    self._write(phase="error", error=error)
                    break
                if forced_module:
                    chosen = next((x for x in items if x.id == forced_module), None)
                    forced_module = None
                    if chosen is None:
                        error = "指定的直播模块不存在"
                        break
                    decision = {"module": chosen, "minutes": DEFAULT_SEGMENT_MIN,
                                "say": "", "reason": "手动指定"}
                else:
                    decision = self._decide(items, error)
                item = decision["module"]
                minutes = int(decision["minutes"])
                self._write(
                    running=True, phase="switching", module=item.id,
                    module_label=item.label, reason=decision.get("reason", ""),
                    say=decision.get("say", ""), minutes=minutes, error=error,
                )
                self._announce(decision)
                self._log("director_choose", module=item.id, minutes=minutes,
                          reason=decision.get("reason", ""),
                          fallback=bool(decision.get("fallback")))
                started = self._switch(item)
                if not started.get("ok"):
                    error = str(started.get("message") or "模块启动失败")[:200]
                    self._log("director_switch_failed", module=item.id,
                              error=error)
                    self._write(phase="error", error=error)
                    self._sleep_until(self._now() + RETRY_BACKOFF_S)
                    continue
                error = ""
                self._segments += 1
                now = self._now()
                with self._lock:
                    history = list(self._last.get("history") or [])
                    history.append({
                        "module": item.id, "label": item.label,
                        "at": now, "minutes": minutes,
                        "reason": decision.get("reason", ""),
                    })
                    history = history[-HISTORY_MAX:]
                self._write(
                    running=True, phase="playing", module=item.id,
                    module_label=item.label, minutes=minutes, history=history,
                    segments=self._segments, segment_started_at=now,
                    next_at=now + minutes * 60.0, error="",
                )
                self._log("director_segment", module=item.id, minutes=minutes)
                self._wait_segment(now + minutes * 60.0, item.id)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            self._log("director_fatal", error=error[:240])
        finally:
            self._cleanup(error)

    def _cleanup(self, error: str = "") -> None:
        self._thread = None
        try:
            from .play import stop_autoplay

            stop_autoplay()
        except Exception:
            pass
        self._write(running=False, phase="error" if error else "stopped",
                    next_at=0.0, error=error)
        try:
            self._stop_path.unlink()
        except OSError:
            pass


_DIRECTOR: LiveDirector | None = None
_DIRECTOR_LOCK = threading.Lock()


def director() -> LiveDirector:
    global _DIRECTOR
    with _DIRECTOR_LOCK:
        if _DIRECTOR is None:
            _DIRECTOR = LiveDirector()
        return _DIRECTOR


def start(module: str | None = None) -> dict:
    return director().start(module=module)


def stop() -> dict:
    return director().stop()


def status() -> dict:
    try:
        return director().status()
    except Exception as exc:  # noqa: BLE001
        return {"running": False, "alive": False, "error":
                f"{type(exc).__name__}: {exc}"}


def available() -> tuple[bool, str]:
    try:
        return director().available()
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def live_modules() -> list[dict]:
    return [item.to_dict() for item in modules()]


__all__ = [
    "DEFAULT_SEGMENT_MIN", "LiveDirector", "LiveModule", "MAX_SEGMENT_MIN",
    "MIN_SEGMENT_MIN", "STATUS_PATH", "STOP_PATH", "available", "build_prompt",
    "director", "live_modules", "modules", "parse_decision", "start", "status",
    "stop",
]
