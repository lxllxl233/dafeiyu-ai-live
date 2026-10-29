"""B 站视频点赞：只在用户明确开启 ``like_on_watch`` 且配置了登录 Cookie 时写一次。

不打印 Cookie、不把 Cookie 写进错误信息；Cookie 失效/风控只影响点赞，不影响看视频。
点赞接口是写操作，所以调用方必须在选中视频后调用，并且每个视频只调用一次。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

LIKE_URL = "https://api.bilibili.com/x/web-interface/archive/like"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def cookie_value(cookie: str, name: str) -> str:
    """从 Cookie 串里取一个值；只读，不打印整串。"""
    for part in str(cookie or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key.strip() == name:
            return value.strip()
    return ""


class BiliLiker:
    def __init__(self, cookie: str = "", *, timeout: float = 6.0,
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

    def like(self, aid: int, bvid: str = "") -> dict:
        aid = int(aid or 0)
        if aid <= 0:
            return {"ok": False, "skipped": True, "reason": "缺少 aid"}
        if not self.available():
            return {"ok": False, "skipped": True,
                    "reason": "未配置登录 Cookie（需要 SESSDATA 和 bili_jct）"}
        bvid = str(bvid or "").strip()
        form = urllib.parse.urlencode({
            "aid": str(aid), "like": "1", "csrf": self.csrf,
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
            LIKE_URL, data=form, headers=headers, method="POST")
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
            return {"ok": False, "error": "B站点赞返回不是 JSON"}
        if not isinstance(data, dict):
            return {"ok": False, "error": "B站点赞返回不是对象"}
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
        reason = hints.get(code) or message or f"B站点赞错误 {code}"
        return {"ok": False, "code": code, "error": reason[:160]}


__all__ = ["BiliLiker", "LIKE_URL", "cookie_value"]
