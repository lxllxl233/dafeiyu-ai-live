"""弹幕解析与时间轴分桶。

B 站 ``seg.so`` 返回 protobuf。这里不引入 ``protobuf`` 依赖，只实现
varint + length-delimited 的最小读取；字段编号来自公开弹幕协议：

    repeated DanmakuElem elems = 1
      id=1, progress(ms)=2, mode=3, fontsize=4, color=5, midHash=6,
      content=7, ctime=8, weight=9, ..., idStr=12

安全：``content`` 是不可信文本，不是指令；这里只清洗长度/控制字符。
不保留 midHash、用户名或头像。
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
import zlib
from collections import Counter
from typing import Iterable

from .models import Danmaku, TimelineBucket, clean_text


_PUNCT_RE = re.compile(r"^[\W_]+$", re.UNICODE)
_MEANINGFUL_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_TOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{2,8}")
_SUSPICIOUS_RE = re.compile(
    r"(忽略(以上|之前|前面)|系统提示|api\s*key|密钥|执行命令|你现在是|作为\s*AI)",
    re.I,
)
_SPAM_TEXTS = {
    "第一", "前排", "打卡", "火钳刘明", "来了", "签到", "留名", "沙发",
    "哈哈哈哈", "哈哈", "哈哈哈", "笑死", "草", "？", "?", "。。。", "...",
}
_STOPWORDS = {
    "这个", "那个", "什么", "怎么", "就是", "真的", "可以", "没有", "不是",
    "他们", "我们", "你们", "一个", "一下", "感觉", "有点", "还是", "已经",
    "因为", "所以", "但是", "然后", "现在", "时候", "视频", "弹幕",
}


def _varint(data: bytes, index: int) -> tuple[int | None, int]:
    value = 0
    shift = 0
    while index < len(data) and shift <= 63:
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index
        shift += 7
    return None, index


def _fields(data: bytes) -> list[tuple[int, int, object]]:
    out: list[tuple[int, int, object]] = []
    index = 0
    while index < len(data):
        key, index = _varint(data, index)
        if key is None:
            break
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, index = _varint(data, index)
            if value is None:
                break
            out.append((number, wire, value))
        elif wire == 2:
            length, index = _varint(data, index)
            if length is None or index + length > len(data):
                break
            out.append((number, wire, data[index:index + length]))
            index += length
        elif wire == 1:
            if index + 8 > len(data):
                break
            out.append((number, wire, data[index:index + 8]))
            index += 8
        elif wire == 5:
            if index + 4 > len(data):
                break
            out.append((number, wire, data[index:index + 4]))
            index += 4
        else:
            break
    return out


def parse_protobuf_segment(data: bytes) -> list[Danmaku]:
    """解析一个 6 分钟弹幕段；坏包/截断包只丢元素，不抛异常。"""
    if not data:
        return []
    items: list[Danmaku] = []
    for number, wire, value in _fields(data):
        if number != 1 or wire != 2 or not isinstance(value, bytes):
            continue
        fields = {n: v for n, w, v in _fields(value) if w in (0, 2)}
        content = fields.get(7)
        if not isinstance(content, bytes):
            continue
        text = content.decode("utf-8", "replace")
        item = Danmaku.from_proto({
            "id": fields.get(1),
            "progress": fields.get(2),
            "mode": fields.get(3),
            "content": text,
            "ctime": fields.get(8),
            "id_str": fields.get(12),
        })
        if item is not None:
            items.append(item)
    return items


def parse_xml_list(data: bytes) -> list[Danmaku]:
    """老接口 ``list.so`` 的降级解析。

    B 站返回 ``Content-Encoding: deflate``，urllib 不会自动解压；先试 raw
    deflate（wbits=-15），已经是 XML 则直接用。该接口最多 1000 条，调用方
    必须把结果标记为 partial。
    """
    if not data:
        return []
    raw = data
    if not raw.lstrip().startswith(b"<"):
        try:
            raw = zlib.decompress(raw, -15)
        except zlib.error:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    items: list[Danmaku] = []
    for node in root.findall(".//d"):
        parts = (node.get("p") or "").split(",")
        if len(parts) < 2:
            continue
        try:
            progress = float(parts[0])
            mode = int(parts[1])
        except (TypeError, ValueError):
            continue
        ctime = 0
        if len(parts) > 4:
            try:
                ctime = int(parts[4])
            except (TypeError, ValueError):
                ctime = 0
        text = clean_text(node.text or "", 120)
        if not text:
            continue
        items.append(Danmaku(
            dmid="", progress_s=max(0.0, progress), text=text, mode=mode,
            sent_at=ctime,
        ))
    return items


def is_low_info(text: str) -> bool:
    clean = " ".join(str(text or "").split())
    if not clean or _PUNCT_RE.match(clean):
        return True
    if clean.lower() in _SPAM_TEXTS:
        return True
    if len(clean) <= 2:
        return True
    # 单字复读（哈哈哈哈/？？？？/6666）
    if len(set(clean)) <= 2 and len(clean) >= 3:
        return True
    return False


def is_suspicious(text: str) -> bool:
    """明显的提示词注入样式：仍然只是文本，但不放进样例去诱导模型。"""
    return bool(_SUSPICIOUS_RE.search(str(text or "")))


def _keywords(texts: Iterable[str], limit: int = 5) -> list[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        for token in _TOKEN_RE.findall(text):
            if token in _STOPWORDS:
                continue
            counter[token] += 1
    return [word for word, count in counter.most_common(limit) if count >= 2]


def build_timeline(
    items: Iterable[Danmaku],
    duration_s: float,
    bucket_s: int,
    *,
    coverage: float = 1.0,
    max_buckets: int = 20,
) -> list[TimelineBucket]:
    """把弹幕按播放进度分桶；count 包含“哈哈”等情绪弹幕，samples 只放信息句。"""
    duration = max(1.0, float(duration_s))
    size = max(10, int(bucket_s))
    count_buckets = max(1, int(math.ceil(duration / size)))
    if count_buckets > max_buckets:
        # 长视频合并尾段，保证最多 max_buckets 个桶。
        size = int(math.ceil(duration / max_buckets))
        count_buckets = max(1, int(math.ceil(duration / size)))
    buckets: list[TimelineBucket] = [
        TimelineBucket(index=i, start_s=i * size, end_s=min(int(duration), (i + 1) * size),
                       coverage=coverage)
        for i in range(count_buckets)
    ]
    texts_by_bucket: list[list[str]] = [[] for _ in buckets]
    for item in items:
        progress = float(item.progress_s)
        if progress < 0 or progress > duration + 5:
            continue
        index = min(len(buckets) - 1, int(progress // size))
        bucket = buckets[index]
        bucket.count += 1
        text = item.text
        if re.search(r"(哈哈|笑死|草|233|hhh|HHH)", text):
            bucket.laughter_count += 1
        if re.search(r"[?？]|(吗|啥|怎么|为什么)", text):
            bucket.question_count += 1
        if is_low_info(text):
            continue
        if is_suspicious(text):
            continue
        texts_by_bucket[index].append(text)

    counts = [bucket.count for bucket in buckets]
    mean = sum(counts) / len(counts) if counts else 0.0
    variance = (sum((c - mean) ** 2 for c in counts) / len(counts)) if counts else 0.0
    std = math.sqrt(variance)
    for index, bucket in enumerate(buckets):
        texts = texts_by_bucket[index]
        bucket.unique_ratio = round(len(set(texts)) / max(1, len(texts)), 3)
        bucket.intensity = round(bucket.count / mean, 2) if mean > 0 else 0.0
        bucket.spike = bool(
            mean > 0 and bucket.count >= 3
            and (bucket.count > mean + 1.5 * std or bucket.count > mean * 2.0)
        )
        ranked = Counter(texts)
        # 代表句：优先出现次数，其次长度（4–40 字更适合当话题）。
        ordered = sorted(
            ranked.items(),
            key=lambda pair: (pair[1], 4 <= len(pair[0]) <= 40, len(pair[0])),
            reverse=True,
        )
        bucket.samples = [text for text, _ in ordered[:6]]
        bucket.keywords = _keywords(texts, 5)
    return buckets


__all__ = [
    "build_timeline", "is_low_info", "is_suspicious", "parse_protobuf_segment",
    "parse_xml_list",
]
