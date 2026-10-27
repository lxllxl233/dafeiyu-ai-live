"""主播窗 set_playback：真实播放状态 → 说话/思考/空闲三态（不建 Win32 窗口）。

对应需求：
  · 音频还在播 → 一直是说话状态（不许提前切「思考中……」）；
  · 合成/排队空档（队列非空、还没起播）→ 说话状态，但头像不抖（还没出声）；
  · 真的播完 → 有待办就回思考中，没待办回空闲；
  · 迟到的旧 playback 帧不许覆盖新状态（seq 单调）。

用 `StreamerWindow.__new__` 只测状态逻辑：不注册窗口类、不开线程、不碰注册表。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import caster_state as cs  # noqa: E402
import streamer  # noqa: E402

ok = True


def check(name, got, want=True):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


class Clock:
    def __init__(self, t: float = 500.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def adv(self, dt: float):
        self.t += dt


def mkwin():
    w = streamer.StreamerWindow.__new__(streamer.StreamerWindow)
    clk = Clock()
    w.presence = cs.CasterState(think_stale=60.0, clock=clk)
    w._audio_seq = -1
    w._pb_playing = False
    w._pb_speaking = False
    w.talk_start_at = 0.0
    w.talk_until = 0.0
    w.text = "痛击压易伤，一刀送走。"
    w.dirty = False
    return w, clk


# ---------------------------------------------------------------- 只有队列，还没出声
w, clk = mkwin()
w.presence.set_thinking(True, seq=1, note="读局面")
w.set_playback({"seq": 1, "playing": False, "remaining": 0.0, "dur": 0.0,
                "pending": 1, "speaking": True})
check("队列里有台词（合成中）→ 说话状态", w.presence.state, cs.SPEAKING)
check("还没出声 → 头像不抖", w.talk_until, 0.0)
check("此时显示台词而不是思考中", w.display_text(w.presence.state), w.text)

# ---------------------------------------------------------------- 真的起播
w.set_playback({"seq": 2, "playing": True, "remaining": 9.0, "dur": 9.0,
                "pending": 0, "speaking": True})
check("起播 → 说话状态", w.presence.state, cs.SPEAKING)
check("起播 → 头像开始抖", w.talk_until > 0, True)
start1 = w.talk_start_at

clk.adv(6.0)                       # 字数估计（23 字 ≈ 4.6s）早就过期了
w.set_playback({"seq": 3, "playing": True, "remaining": 3.0, "dur": 9.0,
                "pending": 0, "speaking": True})
check("播到第 6 秒仍是说话状态（估计窗口过了也不许切思考中）", w.presence.state, cs.SPEAKING)
check("抖动相位不因每轮轮询重置", w.talk_start_at, start1)
clk.adv(3.1)
w.tick_presence()
check("真实 9s 走完 → 有待办就回思考中", w.presence.state, cs.THINKING)
check("播完头像停抖", w.talk_until, 0.0)

# ---------------------------------------------------------------- 播完且没待办
w, clk = mkwin()
w.set_playback({"seq": 1, "playing": True, "remaining": 1.0, "dur": 1.0,
                "pending": 0, "speaking": True})
clk.adv(1.05)
w.set_playback({"seq": 2, "playing": False, "remaining": 0.0, "dur": 1.0,
                "pending": 0, "speaking": False})
check("播完且没有待办 → 空闲", w.presence.state, cs.IDLE)

# ---------------------------------------------------------------- 迟到的旧帧
w, clk = mkwin()
w.set_playback({"seq": 5, "playing": True, "remaining": 5.0, "dur": 5.0,
                "pending": 0, "speaking": True})
check("seq=5 起播", w.presence.state, cs.SPEAKING)
w.set_playback({"seq": 3, "playing": False, "remaining": 0.0, "dur": 5.0,
                "pending": 0, "speaking": False})
check("迟到的 seq=3「播完」被丢弃（还在说）", w.presence.state, cs.SPEAKING)
check("seq 游标没被旧帧改回去", w._audio_seq, 5)

# ---------------------------------------------------------------- 数据坏了不许崩
w, clk = mkwin()
w.set_playback(None)
w.set_playback({})
w.set_playback({"seq": "x", "playing": True, "remaining": "bad"})
check("脏数据不抛异常（状态可查）", isinstance(w.presence.state, str), True)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
