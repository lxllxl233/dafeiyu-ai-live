"""B 站公开 Web 接口的最小客户端：热门/详情/热评/弹幕。

只做 HTTP、错误分类和字段校验；不 import 人设、时钟、调度或输出。
默认匿名、不带 Cookie；接口不可用时抛结构化异常，由 runner 决定等待/跳过。
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .danmaku import parse_protobuf_segment, parse_xml_list
from .models import Comment, Danmaku, DanmakuResult, VideoDetail, VideoRef
from .search import parse_search_html

POPULAR = "https://api.bilibili.com/x/web-interface/popular"
SEARCH = "https://search.bilibili.com/all"
VIEW = "https://api.bilibili.com/x/web-interface/view"
REPLY_MAIN = "https://api.bilibili.com/x/v2/reply/main"
DANMAKU_SEG = "https://api.bilibili.com/x/v2/dm/web/seg.so"
DANMAKU_XML = "https://api.bilibili.com/x/v1/dm/list.so"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class BiliError(RuntimeError):
    def __init__(self, message: str, *, code: int = 0, http_status: int = 0,
                 retryable: bool = False) -> None:
        super().__init__(message)
        self.message = str(message)
        self.code = int(code or 0)
        self.http_status = int(http_status or 0)
        self.retryable = bool(retryable)

    def to_dict(self) -> dict:
        return {"message": self.message, "code": self.code,
                "http_status": self.http_status, "retryable": self.retryable}


class BiliNoContent(BiliError):
    """304/空段：不是故障，表示这一段没有可读内容。"""


class BiliClient:
    def __init__(self, *, timeout: float = 8.0, retries: int = 2,
                 cookie: str = "", logger=None) -> None:
        self.timeout = max(1.0, float(timeout))
        self.retries = max(0, int(retries))
        self.cookie = str(cookie or "").strip()
        self.log = logger or (lambda text: None)

    # ---------------------------------------------------------------- HTTP
    def _opener(self):
        # 和项目其它网络层一致：绕过本机代理，避免 B 站被代理拦成 502。
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _headers(self) -> dict:
        headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/html, text/plain, */*",
            "Cache-Control": "no-cache",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def _get(self, url: str, params: dict | None = None, *,
             raw: bool = False, timeout: float | None = None) -> bytes:
        query = urllib.parse.urlencode(
            {str(k): str(v) for k, v in (params or {}).items() if v is not None}
        )
        full = f"{url}?{query}" if query else url
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(full, headers=self._headers())
            try:
                with self._opener().open(request, timeout=timeout or self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 304:
                    raise BiliNoContent("B站返回 304（该段无内容）", http_status=304,
                                        retryable=False) from None
                retryable = exc.code in (408, 425, 429, 500, 502, 503, 504)
                last_error = BiliError(
                    f"HTTP {exc.code}: {exc.reason}", http_status=exc.code,
                    retryable=retryable,
                )
                if not retryable or attempt >= self.retries:
                    raise last_error from None
                time.sleep(0.8 * (attempt + 1))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = BiliError(f"网络错误：{type(exc).__name__}: {exc}",
                                       retryable=True)
                if attempt >= self.retries:
                    raise last_error from None
                time.sleep(0.8 * (attempt + 1))
        raise last_error or BiliError("B站请求失败", retryable=True)

    def _get_json(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(self.retries + 1):
            body = self._get(url, params)
            try:
                data = json.loads(body.decode("utf-8", "replace"))
            except ValueError as exc:
                raise BiliError(f"B站返回不是 JSON：{exc}", retryable=True) from None
            if not isinstance(data, dict):
                raise BiliError("B站返回不是对象", retryable=True)
            code = _int(data.get("code"))
            if code == 0:
                payload = data.get("data")
                return payload if isinstance(payload, dict) else {}
            error = BiliError(
                f"B站接口错误 {code}: {data.get('message') or ''}",
                code=code, retryable=code in (-352, -500, -503),
            )
            # 风控/临时错误在 JSON 层也会出现；退避后再试，不要立刻刷下一批。
            if error.retryable and attempt < self.retries:
                time.sleep(1.2 * (attempt + 1))
                continue
            raise error
        raise BiliError("B站接口请求失败", retryable=True)

    # ---------------------------------------------------------------- 列表/详情
    def popular(self, limit: int = 20) -> list[VideoRef]:
        data = self._get_json(POPULAR, {"ps": max(1, min(20, int(limit))), "pn": 1})
        rows = data.get("list")
        if not isinstance(rows, list):
            raise BiliError("热门列表缺少 data.list", retryable=True)
        out: list[VideoRef] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            video = VideoRef.from_popular(row)
            if video is None or video.bvid in seen:
                continue
            seen.add(video.bvid)
            out.append(video)
        return out

    def search(self, keyword: str, limit: int = 20) -> list[VideoRef]:
        """匿名搜索页 HTML；官方 JSON 搜索接口需要 WBI/Cookie，不能作为主路径。"""
        keyword = str(keyword or "").strip()
        if not keyword:
            raise BiliError("缺少搜索关键词")
        body = self._get(SEARCH, {"keyword": keyword, "page": 1})
        return parse_search_html(body, max(1, min(50, int(limit))))

    def view(self, bvid: str) -> VideoDetail:
        bvid = str(bvid or "").strip()
        if not bvid:
            raise BiliError("缺少 bvid")
        data = self._get_json(VIEW, {"bvid": bvid})
        detail = VideoDetail.from_view(data)
        if detail is None:
            raise BiliError(f"视频详情不可用：{bvid}", code=-404)
        return detail

    # ---------------------------------------------------------------- 热评
    def hot_comments(self, aid: int, limit: int = 30) -> list[Comment]:
        aid = int(aid or 0)
        if aid <= 0:
            raise BiliError("缺少 aid，无法获取评论")
        limit = max(1, min(50, int(limit)))
        out: list[Comment] = []
        seen: set[int] = set()
        cursor: Any = None
        for page in range(1, 4):
            params = {"type": 1, "oid": aid, "mode": 3, "ps": 20, "pn": page}
            if cursor not in (None, "", 0):
                params["next"] = cursor
            data = self._get_json(REPLY_MAIN, params)
            rows = []
            top = data.get("top_replies")
            if isinstance(top, list):
                rows.extend((row, True) for row in top)
            replies = data.get("replies")
            if isinstance(replies, list):
                rows.extend((row, False) for row in replies)
            for row, is_top in rows:
                if not isinstance(row, dict):
                    continue
                comment = Comment.from_reply(row, is_top=is_top)
                if comment is None or comment.rpid in seen:
                    continue
                seen.add(comment.rpid)
                out.append(comment)
                if len(out) >= limit:
                    return out
            cursor = (data.get("cursor") or {}).get("next") if isinstance(
                data.get("cursor"), dict) else None
            if cursor in (None, "", 0):
                break
        return out

    # ---------------------------------------------------------------- 弹幕
    def danmaku(self, cid: int, aid: int, duration_s: float,
                *, max_items: int = 10000) -> DanmakuResult:
        cid, aid = int(cid or 0), int(aid or 0)
        duration = max(1.0, float(duration_s or 0))
        if cid <= 0 or aid <= 0:
            raise BiliError("缺少 cid/aid，无法获取弹幕")
        expected = max(1, min(6, int(math.ceil(duration / 360.0))))
        items: list[Danmaku] = []
        segments_ok = 0
        partial = False
        error = ""
        source = "protobuf"
        for index in range(1, expected + 1):
            try:
                body = self._get(DANMAKU_SEG, {
                    "type": 1, "oid": cid, "pid": aid, "segment_index": index,
                })
                rows = parse_protobuf_segment(body)
                segments_ok += 1
                items.extend(rows)
            except BiliNoContent:
                # 段超出实际时长/无可读内容：不重试、不刷接口。
                partial = True
                continue
            except BiliError as exc:
                partial = True
                error = exc.message
                self.log(f"[bili] 弹幕段 {index} 失败：{exc.message}")
                break
        if not items and (partial or expected > 0):
            # protobuf 不可用时降级老 XML；只有最多 1000 条，必须标记 partial。
            try:
                body = self._get(DANMAKU_XML, {"oid": cid})
                items = parse_xml_list(body)
                if items:
                    source = "xml"
                    partial = True
                    segments_ok = max(1, segments_ok)
            except BiliError as exc:
                error = error or exc.message
                self.log(f"[bili] XML 弹幕降级失败：{exc.message}")
        if len(items) > max(1, int(max_items)):
            step = int(math.ceil(len(items) / max(1, int(max_items))))
            items = items[::step]
            partial = True
        return DanmakuResult(
            items=items, partial=partial, source=source,
            segments_ok=segments_ok, segments_expected=expected, error=error,
        )


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = ["BiliClient", "BiliError", "BiliNoContent"]
