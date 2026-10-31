# -*- coding: utf-8 -*-
"""右键设置按钮：只测离屏绘制，不建 Win32 窗口。"""
from __future__ import annotations

from dafeiyu.ui.desktop import PetWindow, SettingsBubbleWindow


def _bubble(scale: float = 1.0) -> SettingsBubbleWindow:
    bubble = SettingsBubbleWindow.__new__(SettingsBubbleWindow)
    bubble.scale = scale
    bubble.w = int(SettingsBubbleWindow.W * scale)
    bubble.h = int(SettingsBubbleWindow.H * scale)
    return bubble


def test_bubble_is_concentric_blue_gear():
    bubble = _bubble()
    img = SettingsBubbleWindow._render(bubble)
    assert img.size == (bubble.w, bubble.h)
    assert img.getbbox() is not None
    cx, cy = bubble.w // 2, bubble.h // 2

    # 中心孔是机身蓝；齿轮白色且比旧版更大；左侧是内层蓝色圆。
    hole = img.getpixel((cx, cy))
    assert hole[3] > 180 and hole[2] > 180 and hole[2] > hole[0]
    gear = img.getpixel((cx, cy - 7))
    assert gear[3] > 220 and min(gear[:3]) > 220
    big_tooth = img.getpixel((cx, cy - 11))
    assert big_tooth[3] > 220 and min(big_tooth[:3]) > 220
    body = img.getpixel((cx - 15, cy))
    assert body[3] > 180 and body[2] > 180 and body[2] > body[0] + 40

    # 外层浅蓝圆上下对称，证明底座没有向下偏移。
    top = img.getpixel((cx, cy - 22))
    bottom = img.getpixel((cx, cy + 22))
    for px in (top, bottom):
        assert px[3] > 180 and px[2] > 210 and px[1] > 180 and px[0] > 120
    assert abs(top[0] - bottom[0]) <= 40


def test_bubble_is_compact_and_round():
    bubble = _bubble()
    img = SettingsBubbleWindow._render(bubble)
    assert bubble.w <= 72 and bubble.h <= 72
    assert img.getpixel((0, 0))[3] == 0       # 圆形外的四角透明
    assert img.getpixel((bubble.w - 1, 0))[3] == 0


def test_bubble_follows_pet_scale():
    bubble = _bubble()
    bubble.set_scale(1.6)
    assert bubble.w == int(SettingsBubbleWindow.W * 1.6)
    assert bubble.h == int(SettingsBubbleWindow.H * 1.6)
    bubble.set_scale(0.5)
    assert bubble.w == int(SettingsBubbleWindow.W * 0.5)


def test_pop_and_fade_frames_are_smaller_and_translucent():
    bubble = _bubble()
    full = SettingsBubbleWindow._render(bubble)
    small = SettingsBubbleWindow._render(bubble, 0.55, 0.5)
    full_box = full.getbbox()
    small_box = small.getbbox()
    assert full_box and small_box
    assert (small_box[2] - small_box[0]) < (full_box[2] - full_box[0])
    assert small.getchannel("A").getextrema()[1] <= 140


def test_same_reply_does_not_restart_bubble_or_audio():
    """聊天重连/重放同一条回复时，不能把气泡收掉再弹一次。"""
    class Speaker:
        enabled = True

        def __init__(self):
            self.calls = []

        def speak(self, text):
            self.calls.append(text)

    win = PetWindow.__new__(PetWindow)
    win.text = ""
    win.status = {}
    win.enter_at = 0.0
    win.talk_start_at = 0.0
    win.talk_until = 0.0
    win.bubble_until = 0.0
    win.dirty = False
    win.presence = None
    win.speaker = Speaker()
    win.push("在的", "")
    win.push("在的", "")
    assert len(win.speaker.calls) == 1
    assert win.text.startswith("在的")


def test_fish_right_edge_skips_upper_speech_bubble():
    pet = PetWindow.__new__(PetWindow)
    pet.scale = 1.0
    w, h = 40, 300
    alpha = bytearray(w * h)
    # 说话气泡很长，一直伸到右上角；鱼身只在下面 20 行。
    for y in range(0, 30):
        alpha[y * w + 39] = 255
    for y in range(h - 20, h):
        for x in range(0, 11):
            alpha[y * w + x] = 255
    pet._hit_alpha = (w, h, bytes(alpha))
    edge = PetWindow._fish_right_edge(pet, w)
    assert edge == 10
