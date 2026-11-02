#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 站写操作的最小核心客户端：点赞、发评论、评论清洗。

KOOK 里分享视频时要用，但核心层不能 import ``games/bilibili-hot-commentary``；
这里保留一份只做 HTTP 的客户端，游戏包里的摸鱼训练仍用自己的 liker/commenter。
两条路径共用同一份 ``config.json``（``bilibili_cookie`` / ``bili_training_like`` /
``bili_training_comment`` / ``bili_training_comment_chars``）。

安全边界：不打印 Cookie、不把 Cookie 写进错误信息；没有 ``SESSDATA`` 和
``bili_jct`` 时直接返回 ``skipped``，不发请求。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

LIKE_URL = "https://api.bilibili.com/x/web-interface/archive/like"
REPLY_ADD_URL = "https://api.bilibili.com/x/v2/reply/add"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAX_COMMENT_CHARS = 1000
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CANNED_COMMENT = (
    "同类看同类", "同类看同類", "我也是条鱼", "我也是條魚",
    "我们鱼界", "我們魚界", "这波我先共情", "先别急着笑",
)


def cookie_value(cookie: str, name: str) -> str:
    """从 Cookie 串里取一个值；只读，不打印整串。"""
    for part in str(cookie or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key.strip() == name:
            return value.strip()
    return ""


def has_login(cookie: str) -> bool:
    """点赞/评论都需要登录态：SESSDATA 和 bili_jct（CSRF）。"""
    return bool(cookie_value(cookie, "SESSDATA") and cookie_value(cookie, "bili_jct"))


def _default_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(url: str, form: dict, *, cookie: str, referer: str,
          timeout: float, opener=None) -> tuple[int, dict, str]:
    body = urllib.parse.urlencode(form).encode("utf-8")
    headers = {
        "User-Agent": UA,
        "Referer": referer,
        "Origin": "https://www.bilibili.com",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": str(cookie or ""),
    }
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    factory = opener or _default_opener
    try:
        with factory().open(request, timeout=max(1.0, float(timeout))) as response:
            raw = response.read()
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        return int(exc.code or 0), {}, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, {}, f"{type(exc).__name__}: {exc}"[:160]
    try:
        data = json.loads(raw.decode("utf-8", "replace")) if raw else {}
    except ValueError:
        return status, {}, "B站返回不是 JSON"
    if not isinstance(data, dict):
        return status, {}, "B站返回不是对象"
    return status, data, ""


def like_video(cookie: str, aid: int, bvid: str = "", *, timeout: float = 6.0,
               opener=None) -> dict:
    aid = int(aid or 0)
    if aid <= 0:
        return {"ok": False, "skipped": True, "reason": "缺少 aid"}
    if not has_login(cookie):
        return {"ok": False, "skipped": True,
                "reason": "未配置登录 Cookie（需要 SESSDATA 和 bili_jct）"}
    bvid = str(bvid or "").strip()
    status, data, error = _post(
        LIKE_URL, {"aid": str(aid), "like": "1",
                   "csrf": cookie_value(cookie, "bili_jct")},
        cookie=cookie,
        referer=(f"https://www.bilibili.com/video/{bvid}"
                 if bvid else "https://www.bilibili.com/"),
        timeout=timeout, opener=opener)
    if error:
        return {"ok": False, "error": error}
    code = int(data.get("code") or 0)
    message = str(data.get("message") or data.get("msg") or "").strip()
    if status < 400 and code == 0:
        return {"ok": True, "code": 0}
    hints = {
        -101: "未登录或 Cookie 过期",
        -111: "CSRF 校验失败",
        -400: "点赞请求参数错误",
        -403: "点赞被拒绝（CSRF 或权限不足）",
        -650: "点赞太频繁或视频不可点赞",
    }
    return {"ok": False, "code": code,
            "error": (hints.get(code) or message or f"B站点赞错误 {code}")[:160]}


def comment_video(cookie: str, aid: int, bvid: str, text: str, *,
                  timeout: float = 8.0, opener=None) -> dict:
    aid = int(aid or 0)
    text = str(text or "").strip()
    if aid <= 0:
        return {"ok": False, "skipped": True, "reason": "缺少 aid"}
    if not text:
        return {"ok": False, "skipped": True, "reason": "评论为空"}
    if len(text) > MAX_COMMENT_CHARS:
        text = text[:MAX_COMMENT_CHARS]
    if not has_login(cookie):
        return {"ok": False, "skipped": True,
                "reason": "未配置登录 Cookie（需要 SESSDATA 和 bili_jct）"}
    bvid = str(bvid or "").strip()
    status, data, error = _post(
        REPLY_ADD_URL, {"oid": str(aid), "type": "1", "message": text,
                        "csrf": cookie_value(cookie, "bili_jct"), "plat": "1"},
        cookie=cookie,
        referer=(f"https://www.bilibili.com/video/{bvid}"
                 if bvid else "https://www.bilibili.com/"),
        timeout=timeout, opener=opener)
    if error:
        return {"ok": False, "error": error}
    code = int(data.get("code") or 0)
    message = str(data.get("message") or data.get("msg") or "").strip()
    if status < 400 and code == 0:
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        return {"ok": True, "code": 0,
                "rpid": str(payload.get("rpid") or payload.get("rpid_str") or "")}
    hints = {
        -101: "未登录或 Cookie 过期",
        -111: "CSRF 校验失败",
        -400: "评论请求参数错误",
        -403: "评论被拒绝（CSRF 或权限不足）",
        -404: "视频不存在或评论功能已关闭",
        -509: "评论太频繁，已被限流",
        -650: "评论太频繁或视频不可评论",
    }
    return {"ok": False, "code": code,
            "error": (hints.get(code) or message or f"B站评论错误 {code}")[:160]}


# ---------------------------------------------------------------- 评论文本
def _clean_generated(text: str, limit: int) -> str:
    body = str(text or "").strip()
    body = re.sub(r"^```(?:json|markdown|text)?\s*", "", body, flags=re.I)
    body = re.sub(r"\s*```$", "", body).strip()
    body = body.strip("“”\"'")
    body = re.sub(r"^(?:好的|当然|没问题)[，,：:\s]*", "", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body[: max(1, int(limit))]


def excerpt_comment(text: str, limit: int = 160) -> str:
    """从长记忆里取开头一小段做中评：尽量收在完整句号，不只剩一句短评。"""
    body = _clean_generated(text, max(240, int(limit or 160) * 3))
    if not body:
        return ""
    body = re.sub(r"^(?:我记住的是|我最有感觉的是|看完(?:这条|这个)?(?:视频)?[，,：:]?)",
                  "", body).strip()
    limit = max(40, min(300, int(limit or 160)))
    if len(body) <= limit:
        return body
    head = body[:limit]
    floor = max(40, int(limit * 0.6))
    for mark in ("。", "！", "？", "!", "?", "；", ";"):
        pos = head.rfind(mark)
        if pos + 1 >= floor:
            return head[:pos + 1].strip()
    head = head[:limit - 1].rstrip()
    for mark in ("，", ",", "、", "：", ":", " "):
        pos = head.rfind(mark)
        if pos + 1 >= floor:
            head = head[:pos]
            break
    return head.rstrip("，,；;：:、 ") + "…"


def clean_comment(text: str, limit: int = 160) -> str:
    """把模型写的短评洗成评论区能直接发的一段话。"""
    body = _clean_generated(text, max(120, int(limit or 160) * 3))
    if not body:
        return ""
    body = re.sub(r"^(?:评论|短评|留言|回复)[：:\s]+", "", body)
    body = body.strip("“”\"'")
    body = re.sub(r"^(?:我记住的是|看完(?:这条|这个)?(?:视频)?[，,：:]?)", "", body).strip()
    body = body.replace("弹幕", "评论")
    body = re.sub(r"\s+", " ", body).strip()
    if not body:
        return ""
    if len(body) <= limit:
        return body
    return excerpt_comment(body, limit)


def comment_drop_reason(text: str) -> str:
    """评论是不是“背台本”；返回空串表示可以发。"""
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return ""
    if any(phrase in compact for phrase in _CANNED_COMMENT):
        return "template"
    return ""


__all__ = [
    "MAX_COMMENT_CHARS", "clean_comment", "comment_drop_reason", "comment_video",
    "cookie_value", "excerpt_comment", "has_login", "like_video",
]
