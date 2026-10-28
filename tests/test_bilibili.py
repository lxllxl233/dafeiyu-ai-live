"""B 站礼物监听：只测协议和消息解析，不连外网。"""
from __future__ import annotations

import json
import struct
import zlib

from dafeiyu.live.bilibili import (
    OP_MESSAGE, decode_packets, encode_packet, event_from_message, sign_wbi,
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
