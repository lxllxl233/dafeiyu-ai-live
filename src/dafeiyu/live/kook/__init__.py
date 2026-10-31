"""KOOK 聊天接入：Bot WebSocket 长连接，频道/私聊都走同一条消息管道。

入口：``python main.py kook``；实现见 :mod:`dafeiyu.live.kook.service`。
"""

from .client import KookClient, KookError

__all__ = ["KookClient", "KookError"]
