"""控制台单页自检：直播间 / Token 卡的元素和接口约定都在。"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "ui" / "studio" / "index.html").read_text(encoding="utf-8")


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key == "id" and value:
                self.ids.append(value)


def _ids() -> list[str]:
    parser = _Ids()
    parser.feed(HTML)
    return parser.ids


REQUIRED = {
    # 直播间
    "navRoom", "view-room", "roomState", "roomId", "roomCount", "roomLast",
    "roomKinds", "roomHint", "roomText", "roomSend", "roomSendHint",
    "roomFilters", "roomFeed", "roomSeq", "roomRefresh",
    # Agent / Token
    "tokApiTotal", "tokApiIO", "tokApiCalls", "tokApiLast", "tokSteps",
    "tokKinds", "tokApiHint",
}


def test_required_elements_exist():
    ids = set(_ids())
    assert REQUIRED <= ids, sorted(REQUIRED - ids)


def test_ids_are_unique():
    ids = _ids()
    dupes = {x for x in ids if ids.count(x) > 1}
    assert dupes == set()


def test_room_view_is_registered():
    assert HTML.count('data-view="room"') == 2      # 左侧导航 + 内容区
    assert 'room:"room"' in HTML                    # hash 路由
    assert "ROOM_FILTERS" in HTML and "renderRoom" in HTML
    assert "renderTokens" in HTML


def test_room_send_hits_the_server_contract():
    assert "/api/bili/send" in HTML
    assert "POST" in HTML
    # 发送框限长与后端一致（40 字）
    assert 'maxlength="40"' in HTML


def test_token_card_is_wired_to_api_payload():
    assert "agent" in HTML and "tokens" in HTML
    assert "cumulative" in HTML
    assert re.search(r"t\.steps", HTML)
