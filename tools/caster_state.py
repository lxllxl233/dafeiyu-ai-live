"""主播在场状态机：SPEAKING / THINKING / IDLE —— 纯逻辑，不碰 Win32，可单测。

需求（2026-10-27）：主播不讲话时显示「思考中……」，讲话时切换到说话状态。
硬约束：**思考状态必须对应真实待办**，不许用固定定时器「显示几秒自动消失」。

三个状态的来源都是真实信号：

  THINKING ← overlay 的 `/thinking`：producer（tools/ctx.py、tools/act.py）在每一轮
             决策开始时上报 on。这不是猜的——它等价于「Agent 已经拿到局面、还没给出动作」。
  SPEAKING ← ① 新台词到达（`on_line`，给一个估计时长，先让字出现）
             ② Speaker 真的开始播（`speech_started`，用 WAV 实测时长覆盖估计值）
  IDLE     ← 台词窗口结束且没有待办；`/thinking` 太久没刷新（进程没了/连接断了）
             也回 IDLE —— 这是**断线保护**，不是伪造进度。

seq 规则（对应「旧请求的迟到事件不得覆盖当前请求状态」）：
  只接受 seq 更大的事件；同一 seq 的重复上报只刷新存活时间、不改变状态。

`think_stale` 不是「思考动画时长」，是**存活超时**：超过这么久没收到刷新，
说明上报方已经不在了，再显示「思考中……」就是撒谎。
"""
from __future__ import annotations

import time

SPEAKING = "speaking"
THINKING = "thinking"
IDLE = "idle"

#: 一轮决策最多能算多久仍算「在思考」。实测模型回合 30–120s，留到 240s。
THINK_STALE = 240.0


def _est_speech_seconds(text: str) -> float:
    """估计一句台词的时长（只用于「字先出现、声音还没起」的空档兜底）。

    曼波配音实测约 0.16s/字 + 起播延迟；上下夹住，避免长句把状态卡几十秒。
    """
    n = len(text or "")
    return min(12.0, max(1.2, 0.9 + n * 0.16))


class CasterState:
    def __init__(self, think_stale: float = THINK_STALE, clock=time.monotonic):
        self.think_stale = float(think_stale)
        self._clock = clock
        self._state = IDLE
        self.dirty = True
        # 思考
        self.thinking_on = False
        self.thinking_seq = 0
        self.thinking_at = 0.0
        self.note = ""
        self.stale = False
        # 说话
        self.speak_until = 0.0
        self.speak_started = False

    # ---------------------------------------------------------------- 事件
    def set_thinking(self, on: bool, seq: int | None = None, note: str = "",
                     at: float | None = None) -> str:
        """上报「开始/结束一轮思考」。seq 单调，旧事件直接丢弃。"""
        now = self._clock() if at is None else float(at)
        if seq is None:
            seq = self.thinking_seq + 1
        seq = int(seq)
        if seq < self.thinking_seq:
            return self.state          # 迟到事件：不覆盖更新的状态
        if seq == self.thinking_seq and bool(on) == self.thinking_on:
            # 同一请求的重复上报：只续命（存活心跳），不改状态
            self.thinking_at = now
            if note:
                self.note = note
            self.stale = False
            return self.state
        self.thinking_seq = seq
        self.thinking_on = bool(on)
        self.thinking_at = now
        self.note = note or ""
        self.stale = False
        return self._settle(now)

    def on_line(self, text: str, seconds: float | None = None,
                at: float | None = None) -> str:
        """新台词到达：立刻算「说话状态」（字先出现，声音几百毫秒后才起）。"""
        now = self._clock() if at is None else float(at)
        dur = _est_speech_seconds(text) if seconds is None else float(seconds)
        until = now + min(30.0, max(0.2, dur))
        if until > self.speak_until:
            self.speak_until = until
        self.dirty = True
        return self._settle(now)

    def speech_started(self, seconds: float, at: float | None = None) -> str:
        """Speaker 真正起播：用实测时长覆盖估计值。"""
        now = self._clock() if at is None else float(at)
        dur = min(30.0, max(0.12, float(seconds or 0.0)))
        self.speak_until = now + dur
        self.speak_started = True
        self.dirty = True
        return self._settle(now)

    def speech_ended(self, at: float | None = None) -> str:
        now = self._clock() if at is None else float(at)
        self.speak_until = 0.0
        self.speak_started = False
        return self._settle(now)

    def mark_idle(self, at: float | None = None) -> str:
        """明确收工（对局结束/用户关闭）：立刻回 IDLE，不再等超时。"""
        now = self._clock() if at is None else float(at)
        self.thinking_on = False
        self.thinking_at = 0.0
        self.speak_until = 0.0
        self.speak_started = False
        return self._settle(now)

    # ---------------------------------------------------------------- 结算
    def _settle(self, now: float) -> str:
        if self.thinking_on and (now - self.thinking_at) > self.think_stale:
            self.thinking_on = False
            self.stale = True
            self.dirty = True
        if self.speak_until and now >= self.speak_until:
            self.speak_until = 0.0
            self.speak_started = False
        st = self._resolve(now)
        if st != self._state:
            self._state = st
            self.dirty = True
        return st

    def _resolve(self, now: float) -> str:
        if self.speak_until and now < self.speak_until:
            return SPEAKING
        if self.thinking_on:
            return THINKING
        return IDLE

    def tick(self, at: float | None = None) -> str:
        """主循环每帧调：结算超时，返回当前状态。"""
        now = self._clock() if at is None else float(at)
        return self._settle(now)

    @property
    def state(self) -> str:
        return self._state

    @property
    def speaking(self) -> bool:
        return self._state == SPEAKING

    @property
    def thinking(self) -> bool:
        return self._state == THINKING

    def snapshot(self, at: float | None = None) -> dict:
        now = self._clock() if at is None else float(at)
        return {
            "state": self._state,
            "thinking_on": self.thinking_on,
            "thinking_seq": self.thinking_seq,
            "thinking_age": round(max(0.0, now - self.thinking_at), 1) if self.thinking_at else None,
            "speak_left": round(max(0.0, self.speak_until - now), 2),
            "stale": self.stale,
            "note": self.note,
        }


#: 思考态显示文案（需求原文）。集中一处，测试直接引用，避免改文案改漏。
THINKING_TEXT = "思考中……"
