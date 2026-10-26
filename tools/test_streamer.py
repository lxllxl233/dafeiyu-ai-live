# -*- coding: utf-8 -*-
"""主播模式（streamer.py）回归：渲染尺寸/透明/换行/拼句/帧耗时。

不建窗口、不碰游戏，纯离屏渲染，能在 CI/无桌面环境跑。
用法：python tools/test_streamer.py
"""
import io
import os
import sys
import time

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

    # 7) 静止帧命中缓存（第二次应明显快）
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
