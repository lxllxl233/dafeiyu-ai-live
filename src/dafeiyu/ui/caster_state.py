"""主播在场状态机：SPEAKING / THINKING / IDLE —— 纯逻辑，不碰 Win32，可单测。

需求（2026-10-27）：主播不讲话时显示「思考ing」，讲话时切换到说话状态。
硬约束：**思考状态必须对应真实待办**，不许用固定定时器「显示几秒自动消失」。

三个状态的来源都是真实信号：

  THINKING ← overlay 的 `/thinking`：producer（tools/ctx.py、tools/act.py）在每一轮
             决策开始时上报 on。这不是猜的——它等价于「Agent 已经拿到局面、还没给出动作」。
  SPEAKING ← ① 新台词到达（`on_line`，给一个估计时长，先让字出现）
             ② Speaker 真的开始播（`speech_started`，用 WAV 实测时长覆盖估计值）
             ③ **真实音频生命周期**（`audio_started` / `audio_queue`）：页面
                （直播姬/OBS 的 browser source）才是唯一喇叭时，窗口看不到
                `on_start`，只能靠 overlay 上报的「正在播 + 还剩多久 + 队列里
                还有几条」来判断。这条是 2026-10-27 修的「话没说完就显示
                思考中」的根因：只靠字数估时长，而配音要等 2–6s 合成才起播，
                估计窗口必然早于真实结束。
  IDLE     ← 台词窗口结束且没有待办；`/thinking` 太久没刷新（进程没了/连接断了）
             也回 IDLE —— 这是**断线保护**，不是伪造进度。

`audio_queue(on)` 是**真实队列信号**的续命：页面队列里还有待播台词时，服务端
每轮都会回一个「还在排队」，窗口据此把说话窗口往后延 0.45s——轮询一停（窗口
没了/服务断了）续命就自然失效，不需要任何「N 秒后自动消失」的定时器。

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

#: 页面队列信号每次续命的时长：比窗口轮询间隔（0.35s）略长，
#: 轮询一停就自然失效，不会留下「假装还在说话」的长尾。
QUEUE_HOLD = 0.45


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
        # 真实音频生命周期（页面自报 → overlay 汇总）
        self.audio_until = 0.0      # 这一句真声还剩到什么时候
        self.audio_cid = ""
        self.audio_on = False
        self.queued = False         # 页面队列里还有待播台词（真实信号）

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

    # -------------------------------------------------- 真实音频生命周期
    def audio_started(self, remaining: float, cid: str = "",
                      at: float | None = None) -> str:
        """页面真的开始播这一句了：用**真实剩余时长**说话，别用字数估。"""
        now = self._clock() if at is None else float(at)
        left = max(0.0, float(remaining or 0.0))
        if cid:
            self.audio_cid = str(cid)
        self.audio_on = left > 0.0
        self.audio_until = now + left
        if left > 0.0:
            self.speak_until = max(self.speak_until, self.audio_until)
            self.speak_started = True
            self.dirty = True
        return self._settle(now)

    def audio_queue(self, queued: bool, at: float | None = None,
                    hold: float = QUEUE_HOLD) -> str:
        """页面队列里还有待播台词（合成中/排队中）：说话窗口续命一点点。

        续命只来自「服务端说队列非空」这个真实信号，而且下一轮轮询还会再报一次；
        轮询停了就自动失效。不是「显示 N 秒后消失」的定时器。
        """
        now = self._clock() if at is None else float(at)
        self.queued = bool(queued)
        if self.queued:
            self.speak_until = max(self.speak_until, now + max(0.0, float(hold)))
            self.dirty = True
        return self._settle(now)

    def audio_ended(self, at: float | None = None) -> str:
        """服务端说「没在播、队列也空了」：这一句真的结束了（含估计值一起清）。"""
        now = self._clock() if at is None else float(at)
        self.audio_on = False
        self.audio_until = 0.0
        self.queued = False
        self.speak_until = 0.0
        self.speak_started = False
        self.dirty = True
        return self._settle(now)

    def mark_idle(self, at: float | None = None) -> str:
        """明确收工（对局结束/用户关闭）：立刻回 IDLE，不再等超时。"""
        now = self._clock() if at is None else float(at)
        self.thinking_on = False
        self.thinking_at = 0.0
        self.speak_until = 0.0
        self.speak_started = False
        self.audio_on = False
        self.audio_until = 0.0
        self.queued = False
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
        if self.audio_until and now >= self.audio_until:
            self.audio_until = 0.0
            self.audio_on = False
        st = self._resolve(now)
        if st != self._state:
            self._state = st
            self.dirty = True
        return st

    def _resolve(self, now: float) -> str:
        if self.speak_until and now < self.speak_until:
            return SPEAKING
        if self.audio_until and now < self.audio_until:
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
            "audio_on": self.audio_on,
            "audio_left": round(max(0.0, self.audio_until - now), 2),
            "audio_cid": self.audio_cid,
            "queued": self.queued,
            "stale": self.stale,
            "note": self.note,
        }


#: 三态气泡文案（需求原文）。集中一处，测试直接引用，避免改文案改漏。
THINKING_TEXT = "思考ing"
IDLE_TEXT = "吃饭ing"
