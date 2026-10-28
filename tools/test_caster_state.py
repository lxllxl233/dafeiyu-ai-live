"""主播状态机单测（tools/caster_state.py）。

对应的需求验收：
  · 不讲话、有待办 → THINKING（显示「思考中……」）
  · 讲话 → SPEAKING（说话状态）
  · 语音播完但还有待办 → 回 THINKING（不能变 IDLE，也不能变回空白）
  · 没有待办 → IDLE（按产品原有空闲状态处理）
  · 迟到/重复事件、断线超时 → 不能永久停在错误状态

全部用假时钟，跑得快且不靠 sleep。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import caster_state as cs  # noqa: E402

ok = True


def check(name, got, want=True):
    global ok
    good = got == want
    ok = ok and good
    print(("PASS " if good else "FAIL ") + name + f" -> got={got!r} want={want!r}")


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def adv(self, dt: float):
        self.t += dt


def mk():
    c = Clock()
    return cs.CasterState(think_stale=60.0, clock=c), c


# ---------------------------------------------------------------- 基本三态
s, clk = mk()
check("初始 IDLE", s.state, cs.IDLE)
s.set_thinking(True, note="读局面")
check("上报 on → THINKING", s.state, cs.THINKING)
check("没有台词时就是「思考中」", s.thinking, True)
s.on_line("这一手先算账，它 21，我最多挡 15。")
check("有台词 → SPEAKING", s.state, cs.SPEAKING)
s.speech_started(2.0)
check("起播仍是 SPEAKING", s.state, cs.SPEAKING)
clk.adv(2.05)
s.tick()
check("语音结束、仍有待办 → 回 THINKING", s.state, cs.THINKING)
s.set_thinking(False)
check("待办结束 → IDLE", s.state, cs.IDLE)
check("IDLE 不是说话态", s.speaking, False)

# ---------------------------------------------------------------- 说话压过思考
s, clk = mk()
s.set_thinking(True)
s.on_line("憨鱼思考。")
check("思考中来新台词 → SPEAKING", s.state, cs.SPEAKING)
clk.adv(30)
s.tick()
check("台词超时但没取消思考 → THINKING", s.state, cs.THINKING)

# ---------------------------------------------------------------- seq 顺序
s, clk = mk()
s.set_thinking(True, seq=5)
check("seq=5 on → THINKING", s.state, cs.THINKING)
s.set_thinking(False, seq=3)
check("迟到的 seq=3 off 被丢弃", s.state, cs.THINKING)
s.set_thinking(True, seq=5)
check("同 seq 重复 on 不改变状态", s.state, cs.THINKING)
s.set_thinking(False, seq=6)
check("更新的 seq=6 off 生效", s.state, cs.IDLE)
check("seq 记到 6", s.thinking_seq, 6)

# ---------------------------------------------------------------- 断线保护
s, clk = mk()
s.set_thinking(True, seq=1, note="算这一步")
check("on → THINKING", s.state, cs.THINKING)
clk.adv(59.0)
s.tick()
check("59s 未超时仍是 THINKING", s.state, cs.THINKING)
clk.adv(2.0)
s.tick()
check("超过 60s 没刷新 → IDLE（断线保护，不是伪造进度）", s.state, cs.IDLE)
check("标记 stale", s.stale, True)
check("stale 时 thinking_on=False", s.thinking_on, False)

# ---------------------------------------------------------------- 明确收工
s, clk = mk()
s.set_thinking(True)
s.on_line("收工啦。")
s.mark_idle()
check("mark_idle 立刻停说话", s.speaking, False)
check("mark_idle 立刻回 IDLE", s.state, cs.IDLE)

# ---------------------------------------------------------------- 文案 & 快照
check("思考文案就是需求里的「思考ing」", cs.THINKING_TEXT, "思考ing")
check("空闲文案就是需求里的「吃饭ing」", cs.IDLE_TEXT, "吃饭ing")
s, clk = mk()
s.set_thinking(True, note="x")
snap = s.snapshot()
check("快照 state", snap["state"], cs.THINKING)
check("快照带 thinking_seq", snap["thinking_seq"] >= 1, True)
check("快照 thinking_age 是数字", isinstance(snap["thinking_age"], float), True)

# 估计时长有上下界（别让长句把说话态卡几十秒）
check("空台词估计 ≥1.2s", cs._est_speech_seconds("") >= 1.2, True)
check("超长台词估计 ≤12s", cs._est_speech_seconds("啊" * 500) <= 12.0, True)

# ------------------------------------------------ 真实播放生命周期（2026-10-27 修复）
# 病根：配音要等 2–6s 合成才起播，而字数估计从**发布时刻**起算 → 话没说完就切思考中。
# 下面按「发布 → 合成空档 → 起播 → 播完」的真实顺序走一遍。
s, clk = mk()
s.set_thinking(True, seq=1, note="读局面")
s.on_line("痛击把易伤压上去，最后 1 费一刀 9 点——够杀。")   # 23 字，估计 ~4.6s
check("台词到达 → SPEAKING", s.state, cs.SPEAKING)
clk.adv(2.0)
s.audio_queue(True)            # 合成中：服务端说队列里还有这一条
s.tick()
check("合成空档（估计还没到期）仍是 SPEAKING", s.state, cs.SPEAKING)
clk.adv(3.0)                   # 估计窗口已过（4.6s < 5.0s），但音频还没起
s.audio_queue(True)            # 队列续命（真实队列信号）
s.tick()
check("估计到期但队列还有台词 → 仍是 SPEAKING（不许提前切思考中）", s.state, cs.SPEAKING)

s.audio_started(9.5, cid="abc")   # 这一句真的起播了，真实时长 9.5s（比估计长得多）
check("真实起播 → SPEAKING", s.state, cs.SPEAKING)
clk.adv(8.0)
s.audio_queue(False)
s.tick()
check("播到第 8 秒（估计早就过期）仍是 SPEAKING", s.state, cs.SPEAKING)
clk.adv(1.6)
s.tick()
check("真实时长走完 → 回 THINKING（有待办）", s.state, cs.THINKING)
check("audio 到期后 audio_on=False", s.audio_on, False)

# 播完就该立刻回思考中，不能靠字数估计的长尾
s, clk = mk()
s.set_thinking(True, seq=1)
s.on_line("短句。" * 6)          # 估计 ~7.6s
s.audio_started(2.0)             # 但真实只有 2s
clk.adv(2.2)
s.audio_ended()
check("真实 2s 播完立刻回 THINKING（估计 7.6s 不许拖尾巴）", s.state, cs.THINKING)

# 队列空档：上一句播完、下一句还在合成 → 期间不许闪成思考中
s, clk = mk()
s.set_thinking(True, seq=1)
s.audio_started(1.0)
clk.adv(1.05)
s.audio_queue(True)              # 队列里还有下一条
s.tick()
check("块间空档不闪状态（队列非空 → SPEAKING）", s.state, cs.SPEAKING)
clk.adv(0.2)                     # 0.45s 续命还没到期
s.audio_queue(True)
s.tick()
check("续命期内保持 SPEAKING", s.state, cs.SPEAKING)
clk.adv(1.0)                     # 续命过期（轮询断了）
s.tick()
check("续命过期 → 回 THINKING（不假装还在说）", s.state, cs.THINKING)

# 迟到的旧播放帧不许覆盖新状态
s, clk = mk()
s.set_thinking(True, seq=1)
s.audio_started(5.0, cid="new")
clk.adv(1.0)
s.audio_ended()                  # 真的结束了
check("结束后 → THINKING", s.state, cs.THINKING)
s.audio_started(4.0, cid="old")  # 迟到的「起播」事件（窗口侧按 seq 丢，状态机也必须能自愈）
clk.adv(4.5)
s.tick()
check("迟到事件最多维持自己的剩余时长，不会永久卡住", s.state, cs.THINKING)

# 思考态在说话期间不许抢
s, clk = mk()
s.on_line("在说。")
s.set_thinking(True, seq=9)
check("说话期间上报思考不改变状态", s.state, cs.SPEAKING)
snap = s.snapshot()
check("快照带 audio_on", "audio_on" in snap, True)
check("快照带 queued", "queued" in snap, True)

print()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
