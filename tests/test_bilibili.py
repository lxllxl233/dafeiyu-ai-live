"""B 站直播间互动：只测协议和消息解析，不连外网。"""
from __future__ import annotations

import base64
import json
import struct
import zlib

import pytest

import dafeiyu.live.bilibili as bili
from dafeiyu.live.bilibili import (
    OP_MESSAGE, RoomEvent, RoomLog, decode_packets, encode_packet, event_from_message,
    room_event_from_message, send_danmu, sign_wbi,
)


def test_wbi_signature_matches_reference():
    sign = sign_wbi({"foo": "114", "bar": "514", "zab": "1919810"},
                    "7cd084941338484aae1ad9425b84077c",
                    "4932caff0ff746eab6f01bf08b70ac45", now=1702204169)
    assert sign["wts"] == "1702204169"
    assert sign["w_rid"] == "8f6f2b5b3d485fe1886cec6a0be8c5d4"


def test_gift_packet_roundtrip():
    msg = {"cmd": "SEND_GIFT", "data": {
        "uname": "观众A", "giftName": "小花花", "num": 3,
        "total_coin": 300, "timestamp": 1700000000,
    }}
    raw = encode_packet(OP_MESSAGE, json.dumps(msg, ensure_ascii=False).encode())
    assert decode_packets(raw) == [msg]


def test_zlib_packet_decodes_nested_message():
    msg = {"cmd": "SEND_GIFT", "data": {"giftName": "辣条", "num": 1}}
    inner = encode_packet(OP_MESSAGE, json.dumps(msg, ensure_ascii=False).encode())
    body = zlib.compress(inner)
    packet = struct.pack(">IHHII", 16 + len(body), 16, 2, OP_MESSAGE, 1) + body
    assert decode_packets(packet) == [msg]


def test_gift_event_fields():
    msg = {"cmd": "SEND_GIFT", "data": {
        "uname": "小明", "giftName": "小心心", "num": 2,
        "total_coin": 200, "timestamp": 1700000000,
    }}
    event = event_from_message(msg, 123)
    assert event is not None
    assert event.kind == "gift" and event.username == "小明"
    assert event.gift_name == "小心心" and event.count == 2
    assert event.coin == 200 and event.amount_text == "小心心 ×2"


def test_super_chat_and_guard_are_gifts():
    sc = event_from_message({"cmd": "SUPER_CHAT_MESSAGE", "data": {
        "user_info": {"uname": "老板"}, "price": 30, "start_time": 1700000000,
    }}, 9)
    guard = event_from_message({"cmd": "GUARD_BUY", "data": {
        "uname": "舰长", "gift_name": "舰长", "num": 1,
        "price": 198000, "start_time": 1700000000,
    }}, 9)
    assert sc and sc.kind == "super_chat" and sc.coin == 30
    assert guard and guard.kind == "guard" and guard.count == 1


# ---------------------------------------------------------------- 直播间互动
def test_danmu_is_a_chat_event():
    msg = {"cmd": "DANMU_MSG:4:0:2:2:2:0", "info": [
        [0, 1, 25, 16777215, 1700000000123, 0, 0, "", 0, 0, 0],
        "这手打得不错",
        [7788, "路人甲", 0, 0, 0, 10000, 1, ""],
    ]}
    e = room_event_from_message(msg, 9)
    assert e and e.kind == "chat"
    assert e.username == "路人甲" and e.uid == 7788
    assert e.text == "这手打得不错" and e.timestamp == 1700000000
    assert e.line() == "路人甲 这手打得不错"


def test_interact_word_plain_enter_follow_share():
    enter = room_event_from_message({"cmd": "INTERACT_WORD", "data": {
        "uname": "小明", "uid": 1, "msg_type": 1, "timestamp": 1700000000}}, 9)
    follow = room_event_from_message({"cmd": "INTERACT_WORD", "data": {
        "uname": "小红", "uid": 2, "msg_type": 2, "timestamp": 1700000000}}, 9)
    share = room_event_from_message({"cmd": "INTERACT_WORD", "data": {
        "uname": "小刚", "uid": 3, "msg_type": 3, "timestamp": 1700000000}}, 9)
    assert [enter.kind, follow.kind, share.kind] == ["enter", "follow", "share"]
    assert follow.text == "关注了主播" and share.text == "分享了直播间"


def _pb_field(num: int, value) -> bytes:
    def varint(n: int) -> bytes:
        out = b""
        while True:
            b = n & 0x7F
            n >>= 7
            out += bytes([b | (0x80 if n else 0)])
            if not n:
                return out

    if isinstance(value, int):
        return varint(num << 3) + varint(value)
    raw = value.encode("utf-8")
    return varint((num << 3) | 2) + varint(len(raw)) + raw


def test_interact_word_v2_decodes_protobuf():
    pb = (_pb_field(1, 4567) + _pb_field(2, "新协议观众")
          + _pb_field(3, 2) + _pb_field(4, 1700000123))
    e = room_event_from_message({"cmd": "INTERACT_WORD_V2", "data": {
        "pb": base64.b64encode(pb).decode()}}, 9)
    assert e and e.kind == "follow"
    assert e.username == "新协议观众" and e.uid == 4567
    assert e.timestamp == 1700000123


def test_like_and_online_are_system_events():
    click = room_event_from_message({"cmd": "LIKE_INFO_V3_CLICK", "data": {
        "uname": "老粉", "uid": 5, "like_text": "为主播点赞了",
        "timestamp": 1700000000}}, 9)
    update = room_event_from_message({"cmd": "LIKE_INFO_V3_UPDATE", "data": {
        "click_count": 1234}}, 9)
    online = room_event_from_message({"cmd": "ONLINE_RANK_COUNT", "data": {
        "count": 88}}, 9)
    assert click.kind == "like" and click.username == "老粉"
    assert update.kind == "like" and "1234" in update.text
    assert online.kind == "system" and "88" in online.text


def test_unknown_messages_are_ignored():
    assert room_event_from_message({"cmd": "STOP_LIVE_ROOM_LIST"}, 9) is None
    assert room_event_from_message({"cmd": "SEND_GIFT"}, 9) is None
    assert room_event_from_message({"cmd": "DANMU_MSG", "info": []}, 9) is None


def test_room_log_keeps_only_the_latest_120(tmp_path):
    log = RoomLog(tmp_path / "bili.jsonl", maxlen=120)
    for i in range(135):
        log.append(RoomEvent(room_id=9, kind="chat", username=f"u{i}",
                             text=f"msg{i}", timestamp=1700000000 + i))
    rows = log.recent()
    assert len(rows) == 120
    assert rows[0]["user"] == "u15" and rows[-1]["user"] == "u134"
    # 控制台/重启后再读：还是 120 条
    again = RoomLog(tmp_path / "bili.jsonl", maxlen=120)
    assert len(again.recent()) == 120
    assert again.recent()[-1]["text"] == "msg134"


def test_room_log_append_returns_display_row(tmp_path):
    log = RoomLog(tmp_path / "bili.jsonl")
    row = log.append(RoomEvent(room_id=9, kind="gift", username="老板",
                               text="送出 小花花 ×3", gift_name="小花花",
                               count=3, coin=300))
    assert row["kind"] == "gift" and row["label"] == "礼物"
    assert row["gift"] == "小花花" and row["count"] == 3 and row["at"] > 0


# ---------------------------------------------------------------- 发弹幕
def test_send_danmu_requires_login_cookie():
    with pytest.raises(RuntimeError):
        send_danmu(9, "hi", "")
    with pytest.raises(RuntimeError):
        send_danmu(9, "hi", "SESSDATA=abc")
    with pytest.raises(ValueError):
        send_danmu(9, "   ", "SESSDATA=a; bili_jct=b")
    with pytest.raises(ValueError):
        send_danmu(9, "长" * 41, "SESSDATA=a; bili_jct=b")
    with pytest.raises(ValueError):
        send_danmu(0, "hi", "SESSDATA=a; bili_jct=b")


def test_send_danmu_posts_form(monkeypatch):
    seen = {}

    def fake_post(url, form, cookie, timeout=10.0):
        seen.update(url=url, form=form, cookie=cookie)
        return {"code": 0, "message": "0"}

    monkeypatch.setattr(bili, "_http_post_form", fake_post)
    res = send_danmu(9527, "  这手 不亏  ", "SESSDATA=s; bili_jct=jct-token")
    assert res["ok"] and res["text"] == "这手 不亏"
    assert seen["url"].endswith("/msg/send")
    assert seen["form"]["msg"] == "这手 不亏"
    assert seen["form"]["roomid"] == "9527"
    assert seen["form"]["csrf"] == "jct-token"
    assert seen["form"]["csrf_token"] == "jct-token"


def test_send_danmu_surfaces_bili_errors(monkeypatch):
    monkeypatch.setattr(bili, "_http_post_form",
                        lambda *a, **k: {"code": -101, "message": "账号未登录"})
    with pytest.raises(RuntimeError, match="-101"):
        send_danmu(9, "hi", "SESSDATA=a; bili_jct=b")


# ---------------------------------------------------------------- 监听接线
def test_listener_records_everything_but_only_animates_gifts(tmp_path):
    log = RoomLog(tmp_path / "bili.jsonl")
    gifts, events = [], []
    listener = bili.BilibiliGiftListener(
        9, gifts.append, on_event=events.append, room_log=log,
        logger=lambda text: None)

    listener._handle({"cmd": "DANMU_MSG", "info": [
        [0, 1, 25, 16777215, 1700000000000], "你好", [1, "观众A", 0]]})
    listener._handle({"cmd": "LIKE_INFO_V3_CLICK", "data": {
        "uname": "观众B", "uid": 2, "like_text": "点赞了"}})
    listener._handle({"cmd": "SEND_GIFT", "data": {
        "uname": "老板", "giftName": "小花花", "num": 2, "total_coin": 200,
        "timestamp": 1700000000}})

    assert [e.kind for e in events] == ["chat", "like", "gift"]
    assert len(gifts) == 1 and gifts[0].gift_name == "小花花"
    assert len(log.recent()) == 3
    status = listener.status()
    assert status["counts"] == {"chat": 1, "like": 1, "gift": 1}
    assert status["gifts"] == 1 and status["events"] == 3
    assert "小花花" in status["last_event"]
