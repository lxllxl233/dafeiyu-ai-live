"""B 站视频评论：只在“大肥鱼同类”视频且模型主动给出评论时写一次。

这是公开写操作，和点赞一样需要登录 Cookie（``SESSDATA`` + ``bili_jct``）。
不打印 Cookie、不把 Cookie 写进错误信息；Cookie 失效/风控只影响评论，
不影响看视频。调用方负责同视频只调用一次和频率上限。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .liker import UA, cookie_value

REPLY_ADD_URL = "https://api.bilibili.com/x/v2/reply/add"
MAX_COMMENT_CHARS = 1000


class BiliVideoCommenter:
    def __init__(self, cookie: str = "", *, timeout: float = 8.0,
                 opener=None, logger=None) -> None:
        self.cookie = str(cookie or "").strip()
        self.timeout = max(1.0, float(timeout))
        self.log = logger or (lambda text: None)
        self._opener = opener or self._default_opener

    @staticmethod
    def _default_opener():
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @property
    def csrf(self) -> str:
        return cookie_value(self.cookie, "bili_jct")

    def available(self) -> bool:
        return bool(self.cookie and self.csrf
                    and cookie_value(self.cookie, "SESSDATA"))

    def comment(self, aid: int, bvid: str, text: str) -> dict:
        aid = int(aid or 0)
        if aid <= 0:
            return {"ok": False, "skipped": True, "reason": "缺少 aid"}
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "skipped": True, "reason": "评论为空"}
        if len(text) > MAX_COMMENT_CHARS:
            text = text[:MAX_COMMENT_CHARS]
        if not self.available():
            return {
                "ok": False, "skipped": True,
                "reason": "未配置登录 Cookie（需要 SESSDATA 和 bili_jct）",
            }
        bvid = str(bvid or "").strip()
        form = urllib.parse.urlencode({
            "oid": str(aid),
            "type": "1",
            "message": text,
            "csrf": self.csrf,
            "plat": "1",
        }).encode("utf-8")
        headers = {
            "User-Agent": UA,
            "Referer": (f"https://www.bilibili.com/video/{bvid}"
                        if bvid else "https://www.bilibili.com/"),
            "Origin": "https://www.bilibili.com",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": self.cookie,
        }
        request = urllib.request.Request(
            REPLY_ADD_URL, data=form, headers=headers, method="POST")
        try:
            with self._opener().open(request, timeout=self.timeout) as response:
                body = response.read()
                status = int(getattr(response, "status", 200) or 200)
        except urllib.error.HTTPError as exc:
            return {"ok": False, "code": int(exc.code or 0),
                    "error": f"HTTP {exc.code}"}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:160]}
        try:
            data = json.loads(body.decode("utf-8", "replace")) if body else {}
        except ValueError:
            return {"ok": False, "error": "B站评论返回不是 JSON"}
        if not isinstance(data, dict):
            return {"ok": False, "error": "B站评论返回不是对象"}
        code = int(data.get("code") or 0)
        message = str(data.get("message") or data.get("msg") or "").strip()
        if status < 400 and code == 0:
            payload = data.get("data")
            rpid = ""
            if isinstance(payload, dict):
                rpid = str(payload.get("rpid") or payload.get("rpid_str") or "")
            return {"ok": True, "code": 0, "rpid": rpid}
        hints = {
            -101: "未登录或 Cookie 过期",
            -111: "CSRF 校验失败",
            -400: "评论请求参数错误",
            -403: "评论被拒绝（CSRF 或权限不足）",
            -404: "视频不存在或评论功能已关闭",
            -509: "评论太频繁，已被限流",
            -650: "评论太频繁或视频不可评论",
        }
        reason = hints.get(code) or message or f"B站评论错误 {code}"
        return {"ok": False, "code": code, "error": reason[:160]}


__all__ = ["BiliVideoCommenter", "REPLY_ADD_URL", "MAX_COMMENT_CHARS"]
