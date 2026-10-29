"""B 站搜索页 HTML 的最小解析器。

官方 ``x/web-interface/search/type`` 匿名请求会 412；搜索页 HTML 仍可匿名打开，
因此先用 HTML 拿 BVID/标题/时长/播放量，再用详情接口补 aid/UP/评论数。

解析只认 ``bilibili-video-card`` 里的视频链接，不执行页面脚本；标题/简介仍是
不可信文本，后续统一走文本清洗和过滤。
"""

from __future__ import annotations

import html as html_lib
import re

from .models import VideoRef, clean_text

_ANCHOR_RE = re.compile(
    r'href="//www\.bilibili\.com/video/(BV[0-9A-Za-z]{10})/?[^"]*"', re.I)
_TITLE_RE = re.compile(r'alt="([^"]+)"', re.I)
_DURATION_RE = re.compile(
    r'bili-video-card__stats__duration[^>]*>([0-9]{1,3}(?::[0-9]{2}){1,2})<', re.I)
_VIEW_RE = re.compile(
    r'bili-video-card__stats--item[^>]*>.*?<span[^>]*>([^<]+)</span>', re.I | re.S)


def parse_search_html(body: bytes | str, limit: int = 20) -> list[VideoRef]:
    if isinstance(body, bytes):
        text = body.decode("utf-8", "replace")
    else:
        text = str(body or "")
    if not text:
        return []
    matches = list(_ANCHOR_RE.finditer(text))
    out: list[VideoRef] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        bvid = match.group(1)
        if bvid in seen:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else min(
            len(text), match.start() + 8000)
        card = text[match.start():end]
        title = _first_group(_TITLE_RE, card)
        duration_s = _duration(_first_group(_DURATION_RE, card))
        view = _count(_first_group(_VIEW_RE, card))
        if not title or duration_s <= 0:
            continue
        seen.add(bvid)
        out.append(VideoRef(
            bvid=bvid,
            title=clean_text(html_lib.unescape(title), 200),
            duration_s=duration_s,
            stat={"view": view},
            videos=1,
        ))
        if len(out) >= max(1, int(limit)):
            break
    return out


def _first_group(pattern: re.Pattern, text: str) -> str:
    match = pattern.search(text)
    return html_lib.unescape(match.group(1)) if match else ""


def _duration(value: str) -> int:
    parts = [p for p in str(value or "").split(":") if p.strip().isdigit()]
    if not parts:
        return 0
    try:
        number = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(number) == 2:
        return number[0] * 60 + number[1]
    if len(number) == 3:
        return number[0] * 3600 + number[1] * 60 + number[2]
    return 0


def _count(value: str) -> int:
    text = str(value or "").strip().replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([万萬亿億]?)", text)
    if not match:
        return 0
    try:
        number = float(match.group(1))
    except ValueError:
        return 0
    unit = match.group(2)
    if unit in ("万", "萬"):
        number *= 10000
    elif unit in ("亿", "億"):
        number *= 100000000
    return max(0, int(number))


__all__ = ["parse_search_html"]
