"""自动玩的跨游戏运行宿主：状态、停止、录像、说话节奏和日志。

游戏包只负责「看到什么界面、下一步做什么」；这些生命周期能力由核心提供，
所以第二个游戏不需要复制一份 ``logs/autoplay.json`` / OBS / 语音等待逻辑。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable

from .. import paths

DEFAULT_STATE: dict = {
    "running": False, "screen": "", "floor": 0, "hp": 0, "max_hp": 0,
    "step": 0, "last": "", "error": "", "started_at": 0, "updated_at": 0,
    "game": "", "pid": 0, "mode": "local", "pace": "voice",
    "tokens_in": 0, "tokens_out": 0, "tokens_total": 0, "llm_calls": 0,
    "tokens_last": 0,
    "recording": False, "record_path": "", "record_error": "",
    "stopping": False,
    "route_seq": "", "route_option": None, "route_note": "", "route_floor": 0,
}


class AutoplayHost:
    """一个进程内的一局宿主；路径可替换，方便单测不碰真实直播状态。"""

    def __init__(self, game: str = "", *, mode: str = "local",
                 pace: str = "voice") -> None:
        self.game = str(game or "")
        self.mode = str(mode or "local")
        self.pace = str(pace or "voice")
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.speech_until = 0.0
        self.state = dict(DEFAULT_STATE)
        self.state.update(game=self.game, mode=self.mode, pace=self.pace)
        self.status_path = paths.LOGS / "autoplay.json"
        self.stop_path = paths.LOGS / "autoplay.stop"
        self.log_path = paths.LOGS / "autoplay.jsonl"

    # ---------------------------------------------------------------- 状态
    def reset(self, game: str | None = None, *, mode: str | None = None,
              pace: str | None = None) -> dict:
        with self.lock:
            self.game = str(game or self.game or "")
            self.mode = str(mode or self.mode or "local")
            self.pace = str(pace or self.pace or "voice")
            self.stop_event.clear()
            self.speech_until = 0.0
            self.state.clear()
            self.state.update(DEFAULT_STATE)
            self.state.update(game=self.game, mode=self.mode, pace=self.pace)
            self._write_locked()
            return dict(self.state)

    def _write_locked(self) -> None:
        self.state["updated_at"] = time.time()
        payload = json.dumps(self.state, ensure_ascii=False, indent=1)
        try:
            paths.LOGS.mkdir(parents=True, exist_ok=True)
            tmp = self.status_path.with_suffix(self.status_path.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.status_path)
        except OSError:
            try:
                self.status_path.write_text(payload, encoding="utf-8")
            except OSError:
                pass

    def set_status(self, **updates) -> dict:
        with self.lock:
            self.state.update(updates)
            self._write_locked()
            return dict(self.state)

    def status(self) -> dict:
        """优先读文件，管理端和桌宠进程看到同一份。"""
        raw = None
        for attempt in range(3):
            try:
                raw = json.loads(self.status_path.read_text(encoding="utf-8"))
                break
            except OSError:
                break
            except ValueError:
                if attempt < 2:
                    time.sleep(0.02)      # 对方可能正在写，稍后重试一次
        if isinstance(raw, dict):
            with self.lock:
                self.state.update({k: raw.get(k, v) for k, v in self.state.items()})
            return {**self.state, "stop_requested": self.stop_path.exists()}
        with self.lock:
            return {**self.state, "stop_requested": self.stop_path.exists()}

    # ---------------------------------------------------------------- 停止/日志
    def stopping(self) -> bool:
        return self.stop_event.is_set() or self.stop_path.exists()

    def request_stop(self) -> None:
        try:
            paths.LOGS.mkdir(parents=True, exist_ok=True)
            self.stop_path.write_text(str(time.time()), encoding="utf-8")
        except OSError:
            pass
        self.stop_event.set()
        self.speech_until = 0.0

    def clear_stop(self) -> None:
        self.stop_event.clear()
        try:
            self.stop_path.unlink()
        except OSError:
            pass

    def sleep(self, seconds: float) -> None:
        end = time.time() + max(0.0, float(seconds))
        while time.time() < end:
            if self.stopping():
                return
            time.sleep(min(0.2, max(0.02, end - time.time())))

    def log(self, msg: str, **data) -> None:
        row = {"ts": time.time(), "msg": msg, **data}
        try:
            paths.LOGS.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ---------------------------------------------------------------- Token
    def add_usage(self, usage: dict | None, run_id: str = "") -> dict:
        """累计 API 返回的 usage；失败重试的 token 也算，观众看到的是真账单。"""
        usage = usage if isinstance(usage, dict) else {}
        try:
            tin = int(usage.get("input") or 0)
            tout = int(usage.get("output") or 0)
            total = int(usage.get("total") or (tin + tout))
        except (TypeError, ValueError):
            tin = tout = total = 0
        if total <= 0 and tin <= 0 and tout <= 0:
            return {"input": 0, "output": 0, "total": 0}
        with self.lock:
            self.state["tokens_in"] = int(self.state.get("tokens_in") or 0) + tin
            self.state["tokens_out"] = int(self.state.get("tokens_out") or 0) + tout
            self.state["tokens_total"] = int(self.state.get("tokens_total") or 0) + total
            self.state["llm_calls"] = int(self.state.get("llm_calls") or 0) + 1
            self.state["tokens_last"] = total
            self._write_locked()
        self.log("llm_usage", run_id=run_id, input=tin, output=tout, total=total,
                 cumulative=int(self.state.get("tokens_total") or 0))
        self.event("llm_turn", f"LLM 回合 · {total} tok", run_id=run_id,
                   tokens=total, tokens_in=tin, tokens_out=tout)
        return {"input": tin, "output": tout, "total": total}

    # ---------------------------------------------------------------- 录像
    def event(self, kind: str, label: str = "", **extra) -> None:
        try:
            from ..recording import auto

            auto.record_event(kind, label, **extra)
        except Exception:
            pass

    def start_recording(self, note: str = "") -> dict:
        try:
            from ..recording import auto

            res = auto.start_session(self.game, note=note or "自动玩")
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.set_status(
            recording=bool(res.get("ok")),
            record_path=str(res.get("path") or ""),
            record_error="" if res.get("ok") else str(res.get("error") or "录像启动失败"),
        )
        self.log("record_start" if res.get("ok") else "record_failed",
                 path=res.get("path"), error=res.get("error"))
        return res

    def stop_recording(self, result: str = "") -> dict:
        try:
            from ..recording import auto

            res = auto.stop_session(result=result)
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.set_status(
            recording=False,
            record_path=str(res.get("path") or self.state.get("record_path") or ""),
            record_error="" if res.get("ok") else str(res.get("error") or ""),
        )
        self.log("record_stop" if res.get("ok") else "record_stop_failed",
                 path=res.get("path"), error=res.get("error"))
        return res

    # ---------------------------------------------------------------- 节奏/语音
    def settings(self) -> tuple[str, str]:
        try:
            from ..core.config import LiveConfig

            cfg = LiveConfig.load()
            mode = str(cfg.autoplay_mode or "local").strip().lower()
            pace = str(cfg.autoplay_pace or "voice").strip().lower()
            return (mode if mode in ("local", "llm") else "local",
                    pace if pace in ("voice", "normal") else "voice")
        except Exception:
            return "local", "voice"

    def remember_speech(self, text: str) -> None:
        clean = " ".join(str(text or "").split())
        if not clean:
            return
        seconds = min(10.0, max(1.6, len(clean) * 0.22 + 1.0))
        self.speech_until = max(self.speech_until, time.time() + seconds)
        self.event("speech", clean[:160], text=clean)

    def say(self, text: str, *, facts: dict | None = None,
            sender: Callable[[str], None] | None = None) -> None:
        _ = facts
        self.remember_speech(text)
        if sender is not None:
            try:
                sender(str(text))
            except Exception:
                pass

    def wait_voice(self) -> None:
        if self.stopping():
            return
        _, pace = self.settings()
        if pace == "normal":
            self.sleep(0.7)
            return
        self.sleep(max(2.0, self.speech_until - time.time()))

    # ---------------------------------------------------------------- 进程
    @staticmethod
    def pid_alive(pid: int | None) -> bool:
        if not pid:
            return False
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                                    ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return False
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return False
                return code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(h)
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError):
            return False


__all__ = ["AutoplayHost", "DEFAULT_STATE"]
