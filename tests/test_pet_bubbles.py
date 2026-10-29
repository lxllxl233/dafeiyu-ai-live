"""桌宠侧面的直播间互动气泡：点赞/弹幕/进场/礼物。"""
from __future__ import annotations

import time
from collections import deque
from types import SimpleNamespace

import pytest

from dafeiyu.ui import desktop


def _win():
    """room_event 只碰这几样东西，不必真的建 Win32 窗口。"""
    return SimpleNamespace(room_bubbles=deque(maxlen=8),
                           _room_lock=desktop.threading.Lock(), dirty=False)


def _pet(bubbles=()):
    now = time.perf_counter()
    pet = SimpleNamespace(
        pet_facing="front", pet_face_right=False, pet_energy=0.0, pet_lean=0.0,
        talk_start_at=0.0, talk_until=0.0, celebrate_start=0.0, celebrate_until=0.0,
        enter_at=0.0, bubble_until=0.0, room_bubbles=deque(bubbles, maxlen=8))
    return pet, now


def test_text_for_each_interaction():
    text = desktop.room_bubble_text
    assert text("enter", "小明") == "欢迎 小明！"
    assert text("enter") == "欢迎新观众！"
    assert text("like") == "点赞"
    assert text("like", "", "", 5) == "点赞 ×5"
    assert text("chat", "", "这手打得可以啊") == "这手打得可以啊"
    assert text("gift", "", "", 2, "小心心") == "×2 小心心"
    assert text("follow", "阿花") == "阿花 关注了"
    # 观众内容洗过：控制字符/换行清掉，长度受限
    assert text("chat", "", "a\nb\x00c") == "a b c"
    assert len(text("chat", "", "字" * 200)) == 60


def test_room_event_coalesces_likes_and_keeps_chat_separate():
    win = _win()
    for _ in range(3):
        desktop.StreamerWindow.room_event(win, "like")
    assert len(win.room_bubbles) == 1
    assert win.room_bubbles[0]["text"] == "点赞 ×3"
    desktop.StreamerWindow.room_event(win, "chat", "鱼哥", "这手稳")
    assert len(win.room_bubbles) == 2
    assert win.room_bubbles[-1]["kind"] == "chat"
    # 未知类型直接忽略
    desktop.StreamerWindow.room_event(win, "system")
    assert len(win.room_bubbles) == 2
    assert win.dirty is True


def test_room_event_merges_same_gift_and_splits_different_gift():
    win = _win()
    desktop.StreamerWindow.room_event(win, "gift", "阿花", "", 1, "小心心")
    desktop.StreamerWindow.room_event(win, "gift", "阿花", "", 2, "小心心")
    assert len(win.room_bubbles) == 1
    assert win.room_bubbles[0]["text"] == "×3 小心心"
    desktop.StreamerWindow.room_event(win, "gift", "阿花", "", 1, "舰长")
    assert len(win.room_bubbles) == 2


def test_super_chat_shows_the_message():
    win = _win()
    desktop.StreamerWindow.room_event(win, "super_chat", "老板", "鱼哥加油", 1, "醒目留言")
    assert win.room_bubbles[-1]["kind"] == "gift"
    assert win.room_bubbles[-1]["text"] == "鱼哥加油"


def test_render_pet_draws_side_bubbles():
    pet, now = _pet()
    pet.room_bubbles.extend([
        {"kind": "like", "text": "点赞 ×7", "at": now - 2.0, "count": 7},
        {"kind": "enter", "text": "欢迎 小明！", "at": now - 1.2, "count": 1},
        {"kind": "gift", "text": "×2 小心心", "at": now - 0.6, "count": 2},
        {"kind": "chat", "text": "这手打得可以啊，鱼哥", "at": now - 0.1, "count": 1},
    ])
    img = desktop.render_pet("", "idle", 1.0, now, pet)
    pet2, _ = _pet()
    plain = desktop.render_pet("", "idle", 1.0, now, pet2)
    diff = sum(1 for a, b in zip(img.tobytes(), plain.tobytes()) if a != b)
    assert diff > 500, "侧边气泡没有画出来"
    # 气泡在精灵右侧（x>320），不是头顶那颗
    bbox = img.getbbox()
    assert bbox is not None and bbox[2] > 320


def test_render_pet_ignores_expired_bubbles():
    pet, now = _pet()
    pet.room_bubbles.append({"kind": "chat", "text": "早就过期了", "at": now - 30, "count": 1})
    img = desktop.render_pet("", "idle", 1.0, now, pet)
    pet2, _ = _pet()
    plain = desktop.render_pet("", "idle", 1.0, now, pet2)
    assert img.tobytes() == plain.tobytes()


def test_gift_bridge_forwards_every_interaction(monkeypatch):
    seen = []

    class _Win:
        def room_event(self, *a):
            seen.append(a)

    bridge = desktop.GiftBridge.__new__(desktop.GiftBridge)
    bridge.win = _Win()
    event = SimpleNamespace(kind="enter", username="小明", text="进入直播间",
                            count=0, gift_name="", line=lambda: "小明 进入直播间")
    bridge._on_event(event)
    assert seen == [("enter", "小明", "进入直播间", 0, "")]


def test_room_event_does_not_need_a_real_window():
    # 防御：GiftBridge 可能在其他形态（面板窗）上调用
    win = _win()
    desktop.StreamerWindow.room_event(win, "chat", "", "hi")
    assert win.room_bubbles[-1]["kind"] == "chat"
