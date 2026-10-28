"""直播平台接入：目前是 B 站礼物监听。"""
from .bilibili import BilibiliGiftListener, GiftEvent

__all__ = ["BilibiliGiftListener", "GiftEvent"]
