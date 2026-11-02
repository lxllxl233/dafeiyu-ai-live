"""B 站点赞/评论核心客户端：Cookie 检查、表单、错误码和评论清洗。"""
from __future__ import annotations

import json
import urllib.parse

from dafeiyu.live import bili_actions as ba


class _Response:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.request = None

    def open(self, request, timeout=None):
        self.request = request
        return _Response(self.payload, self.status)


def _form(request):
    return dict(urllib.parse.parse_qsl(request.data.decode("utf-8")))


def test_like_video_posts_csrf_and_aid():
    opener = _Opener({"code": 0, "message": "0"})
    result = ba.like_video("SESSDATA=s; bili_jct=j", 80433022, "BV1GJ411x7h7",
                           opener=lambda: opener)
    assert result["ok"] is True
    assert opener.request.full_url == ba.LIKE_URL
    assert _form(opener.request) == {"aid": "80433022", "like": "1", "csrf": "j"}


def test_like_video_skips_without_login_cookie():
    opener = _Opener({"code": 0})
    result = ba.like_video("", 1, "BV1", opener=lambda: opener)
    assert result["ok"] is False and result["skipped"] is True
    assert opener.request is None


def test_like_video_maps_error_code():
    opener = _Opener({"code": -101, "message": "账号未登录"})
    result = ba.like_video("SESSDATA=s; bili_jct=j", 1, "BV1",
                           opener=lambda: opener)
    assert result["ok"] is False
    assert result["code"] == -101
    assert "未登录" in result["error"]


def test_comment_video_posts_message_and_returns_rpid():
    opener = _Opener({"code": 0, "data": {"rpid": 123456}})
    result = ba.comment_video("SESSDATA=s; bili_jct=j", 80433022,
                              "BV1GJ411x7h7", "这段我也有同感，先拆小真的有用。",
                              opener=lambda: opener)
    assert result["ok"] is True and result["rpid"] == "123456"
    form = _form(opener.request)
    assert form["oid"] == "80433022"
    assert form["type"] == "1"
    assert form["csrf"] == "j"
    assert "先拆小" in form["message"]


def test_comment_video_skips_empty_text_and_missing_cookie():
    assert ba.comment_video("", 1, "BV1", "x")["skipped"] is True
    assert ba.comment_video("SESSDATA=s; bili_jct=j", 1, "BV1", "")["skipped"] is True


def test_clean_comment_removes_meta_and_truncates():
    text = "看完这条视频，我觉得先把问题拆小真的有用，上次硬扛半天不如列个清单。"
    cleaned = ba.clean_comment(text, 60)
    assert "看完" not in cleaned
    assert "先把问题拆小" in cleaned
    assert "弹幕" not in ba.clean_comment("弹幕里都在说这点，我也觉得有用。", 60)
    assert len(cleaned) <= 60
    assert ba.comment_drop_reason("同类看同类，这波我先共情") == "template"
    assert ba.comment_drop_reason("先把问题拆小，我上次也这样。") == ""
