# -*- coding: utf-8 -*-
"""主播模式（streamer.py）回归：渲染尺寸/透明/换行/拼句/帧耗时。

不建窗口、不碰游戏，纯离屏渲染，能在 CI/无桌面环境跑。
用法：python tools/test_streamer.py
"""
import io
import os
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import streamer as S  # noqa: E402

STATUS = {"character": "铁甲战士", "ascension": 10, "floor": 8, "hp": 23, "max_hp": 80, "gold": 20}
ok = True


def check(cond, msg):
    global ok
    if not cond:
        print(f"FAIL {msg}")
        ok = False


def main():
    # 1) 尺寸：1.0 倍 = 面板 1180 + 两边留白
    img = S.render("它 21，我最多挡 15。差 6，嗨呀。", STATUS, 1.0)
    check(img.size[0] == 1180 + 2 * 26, f"宽度应 1232，实际 {img.size[0]}")
    check(img.mode == "RGBA", "必须是 RGBA（要逐像素 alpha）")

    # 2) 透明：左上角（阴影外）必须全透明，面板中间必须不透明
    check(img.getpixel((3, 3))[3] == 0, f"左上角应透明白，实际 {img.getpixel((3, 3))}")
    check(img.getpixel((img.width // 2, 60))[3] > 200, "面板中部应该是不透明的")

    # 3) 换行：长句要多行；裁到上限后还能塞进面板（不溢出）
    img = S.render("它 21，我最多挡 15。差 6，嗨呀。", STATUS, 1.0)
    long_txt = "四园丁 120 血，我最大格挡 16-21，清场要 12 回合而血量只够 3 回合。" * 2
    lines = S._layout(long_txt, 1.0)["lines"]
    check(len(lines) >= 2, f"长句应换到 2 行以上，实际 {len(lines)}")
    check(len(S.clip("字" * 200, S.MAX_THOUGHT)) == S.MAX_THOUGHT, "thought 要裁到 120 字")
    check(len(S.clip("字" * 200, S.MAX_MEME)) == S.MAX_MEME, "meme 要裁到 70 字")
    # 裁完的最长一句（120+70 字）也不能把面板撑到比窗口高
    worst = "字" * S.MAX_THOUGHT + "。" + "字" * S.MAX_MEME + "。"
    check(S.render(worst, STATUS, 1.0).size == img.size, "最长一句不该改变窗口尺寸")

    # 4) 拼句：和页面 composeSentence 一致（思路补句号，吐槽接上）
    check(S.compose("它 21，我最多挡 15", "差 6，嗨呀") == "它 21，我最多挡 15。差 6，嗨呀。",
          f"拼句不对：{S.compose('它 21，我最多挡 15', '差 6，嗨呀')}")
    check(S.compose("", "稳了") == "稳了。", "只有吐槽时也要收句号")
    check(S.compose("算完了", "") == "算完了。", "只有思路时也要收句号")

    # 5) 状态行：没有字段时不能崩
    S.render("x", {}, 1.0)
    S.render("x", {"hp": 1}, 1.0)

    # 6) 帧耗时：说话动画 60fps 也不能卡（缓存生效的话单帧 < 40ms）
    t0 = time.perf_counter()
    for _ in range(30):
        S.render("它 21，我最多挡 15。差 6，嗨呀。", STATUS, 1.0, talk=0.5)
    per = (time.perf_counter() - t0) / 30 * 1000
    check(per < 40, f"单帧 {per:.1f}ms，太慢（检查 layout/portrait 缓存）")
    print(f"单帧 {per:.1f}ms")

    # 7) 声音：和页面同款动物语，合成成能播的 WAV
    wav = S.animalese_wav("它 21，我最多挡 15。", 0.55)
    check(wav is not None and wav[:4] == b"RIFF", "应该合成出 WAV")
    if wav:
        with wave.open(io.BytesIO(wav)) as wf:
            check(wf.getnchannels() == 1 and wf.getframerate() == 44100, "单声道 44.1k")
            sec = wf.getnframes() / wf.getframerate()
            check(0.3 < sec < 3.5, f"时长异常：{sec:.2f}s")
            raw = wf.readframes(wf.getnframes())
        peak = max(abs(int.from_bytes(raw[i:i + 2], "little", signed=True))
                   for i in range(0, len(raw) - 1, 2))
        check(peak > 3000, f"合成出来是静音/太小（peak={peak}）")
    check(S.animalese_wav("……，。", 0.55) is None, "只有标点不该发声")
    t0 = time.perf_counter()
    S.animalese_wav("字" * 120, 0.55)
    ms = (time.perf_counter() - t0) * 1000
    check(ms < 400, f"合成 {ms:.0f}ms 太慢（会拖住重绘）")
    print(f"配音 {ms:.0f}ms / 120 字")

    # 8) 缩放范围与等比性（拖动/滚轮都走这几个函数）
    check(S.StreamerWindow._clamp_scale(0.01) == S.MIN_SCALE, "缩得太小要夹到 MIN_SCALE")
    check(S.StreamerWindow._clamp_scale(99) <= S.MAX_SCALE + 1e-6, "放得太大要夹到 MAX_SCALE")
    half = S.render("x", STATUS, 0.5)
    check(half.width == round(S.BASE_W * 0.5), f"0.5 倍宽应 ~{round(S.BASE_W*0.5)}，实际 {half.width}")
    check(half.height < S.render("x", STATUS, 1.0).height, "缩小后应更矮")

    # 9) 命中测试：右下角 20px = 缩放把手，其它地方 = 拖动
    import ctypes
    from ctypes import wintypes
    box = wintypes.RECT()
    box.left, box.top, box.right, box.bottom = 100, 100, 100 + S.BASE_W, 100 + 276
    hit = S.StreamerWindow._hit_test_point
    check(hit(box.right - 5, box.bottom - 5, box, 1.0) == S.HTBOTTOMRIGHT, "右下角应识别为缩放")
    check(hit(box.right - 40, box.bottom - 5, box, 1.0) == S.HTCAPTION, "把手左边应是拖动")
    check(hit(box.left + 50, box.top + 50, box, 1.0) == S.HTCAPTION, "中间应是拖动")
    # 小窗时把手不跟着缩没（最小 10px）
    check(hit(box.right - 8, box.bottom - 8, box, S.MIN_SCALE) == S.HTBOTTOMRIGHT, "小尺寸也要能抓到角")

    # 10) 声音开关：关掉时 speak 不发线程也不报错
    sp = S.Speaker(0.5, enabled=False)
    sp.speak("这就不该响")
    check(sp.enabled is False, "初始应为静音")
    check(sp.toggle() is True, "toggle 应打开")
    check(sp.toggle() is False, "toggle 应关闭")

    # 11) 主播窗时钟：push 只做文案入场，不得提前设置说话动画
    class FakeSpeaker:
        def __init__(self):
            self.spoken = []

        def speak(self, text):
            self.spoken.append(text)

    w = object.__new__(S.StreamerWindow)
    w.text = ""
    w.status = {}
    w.talk_start_at = 0.0
    w.talk_until = 0.0
    w.enter_at = 0.0
    w.dirty = False
    w.speaker = FakeSpeaker()
    w.push("它 21，我最多挡 15", "差 6，嗨呀")
    check(w.talk_until == 0.0 and w.talk_start_at == 0.0,
          "push 阶段不能开始说话动画（否则头像会早于声音）")
    check(w.enter_at > 0 and w.dirty, "文案仍要立刻入场")
    check(w.speaker.spoken == [w.text], "push 应把同一句交给 Speaker")

    w.start_talking(1.25, w.text)
    check(w.talk_until - w.talk_start_at > 1.2,
          f"说话动画应按真实音频长度，实际 {w.talk_until - w.talk_start_at:.2f}s")
    old_text = w.text
    w.push("下一句", "")
    w.start_talking(1.0, old_text)
    check(w.talk_start_at == 0.0, "上一句的迟到回调不能抖新弹幕")

    # 12) Speaker 必须在 PlaySound 之后才回调 on_start（不能提前抖）
    calls = []
    events = []

    class FakeWinsound:
        SND_FILENAME = 1
        SND_ASYNC = 2
        SND_NODEFAULT = 4

        @staticmethod
        def PlaySound(*args):
            events.append("play")

    wav = S.animalese_wav("测试回调", 1.0)
    check(wav is not None, "测试 WAV 应能合成")
    if wav:
        orig_cached = S.mambo_cached_wav
        try:
            S.mambo_cached_wav = lambda text, volume: wav
            sp2 = S.Speaker(0.55, on_start=lambda dur, text: (
                events.append("start"), calls.append((dur, text))))
            sp2.delegate = False
            sp2._winsound = FakeWinsound
            sp2._play("测试回调")
        finally:
            S.mambo_cached_wav = orig_cached
        check(events == ["play", "start"], f"应先 PlaySound 再动画，实际顺序 {events}")
        check(calls and calls[0][0] > 0.1 and calls[0][1] == "测试回调",
              f"回调应收到真实时长/文本，实际 {calls}")

    # 9) 静止帧命中缓存（第二次应明显快）
    t0 = time.perf_counter()
    S.render("缓存测试", STATUS, 1.0)
    a = time.perf_counter() - t0
    t0 = time.perf_counter()
    S.render("缓存测试", STATUS, 1.0)
    b = time.perf_counter() - t0
    check(b <= a, f"静止帧没有走缓存：{a*1000:.1f}ms → {b*1000:.1f}ms")

    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
