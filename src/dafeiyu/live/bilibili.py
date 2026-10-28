"""B 站直播间监听（匿名可读；需要登录态时可在 config 里带 Cookie）。

两件事：

1. 连上直播间的弹幕 WebSocket，把**直播间互动**（进入 / 关注 / 点赞 / 弹幕 /
   礼物 / 醒目留言 / 上舰）转成 ``RoomEvent``：一路回调给桌宠做动画，一路写进
   ``RoomLog``（本地 JSONL，只保留最近 120 条）给控制台「直播间」看；
2. ``send_danmu()`` 用登录 Cookie 往直播间发一条弹幕（控制台互动用）。

不写数据库、不上传、不打印 Cookie 或 token。
协议参考：16 字节包头 + JSON；protover=2 时包体是 zlib/brotli。
"""
from __future__ import annotations

import hashlib
import json
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

DANMU_INFO = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"
NAV = "https://api.bilibili.com/x/web-interface/nav"
FINGERPRINT = "https://api.bilibili.com/x/frontend/finger/spi"
SEND_DANMU = "https://api.live.bilibili.com/msg/send"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADER_LEN = 16
OP_HEARTBEAT = 2
OP_AUTH = 7
OP_MESSAGE = 5
ROOM_MAX_EVENTS = 120
ROOM_LOG_NAME = "bilibili_events.jsonl"

KIND_LABELS = {
    "enter": "进入", "follow": "关注", "share": "分享", "like": "点赞",
    "chat": "弹幕", "gift": "礼物", "super_chat": "醒目留言", "guard": "上舰",
    "system": "系统", "self": "本机发送",
}
GIFT_KINDS = frozenset({"gift", "super_chat", "guard"})

# 2025 起 getDanmuInfo 也走 WBI 签名；不签经常直接 -352。
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]
_wbi_cache: dict = {"at": 0.0, "img": "", "sub": ""}
_wbi_lock = threading.Lock()


@dataclass(frozen=True)
class GiftEvent:
    room_id: int
    kind: str                 # gift / super_chat / guard
    username: str
    gift_name: str
    count: int
    coin: int
    timestamp: int
    raw: dict

    @property
    def amount_text(self) -> str:
        if self.kind == "super_chat":
            return f"{self.gift_name} ¥{self.coin}"
        return f"{self.gift_name} ×{self.count}"


def _opener():
    # 本机代理经常把 B 站 WebSocket/HTTP 拦成 502；和 OBS/TTS 一样直连。
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _headers(cookie: str = "") -> dict:
    h = {"User-Agent": UA, "Referer": "https://live.bilibili.com/",
         "Accept": "application/json, text/plain, */*"}
    if cookie.strip():
        h["Cookie"] = cookie.strip()
    return h


def _http_json(url: str, cookie: str = "", timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers=_headers(cookie))
    with _opener().open(req, timeout=timeout) as resp:
        raw = resp.read()
    data = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("B 站返回不是对象")
    return data


def _cookie_with_fingerprint(cookie: str) -> str:
    """没有 buvid 时补一个匿名指纹；失败不挡连接。"""
    cookie = (cookie or "").strip()
    if "buvid3=" in cookie:
        return cookie
    try:
        data = _http_json(FINGERPRINT, cookie)
        d = data.get("data") or {}
        b3, b4 = str(d.get("b_3") or ""), str(d.get("b_4") or "")
        extra = "; ".join(x for x in (f"buvid3={b3}" if b3 else "",
                                      f"buvid4={b4}" if b4 else "") if x)
        if extra:
            return (cookie + "; " + extra).strip("; ")
    except Exception:
        pass
    return cookie


def _mixin_key(img_key: str, sub_key: str) -> str:
    raw = (img_key + sub_key)
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB[:32])


def sign_wbi(params: dict, img_key: str, sub_key: str,
             now: float | None = None) -> dict:
    """WBI 签名：参数排序、encodeURIComponent、MD5。"""
    data = {str(k): str(v) for k, v in params.items() if v is not None}
    data["wts"] = str(int(now if now is not None else time.time()))
    filtered = {k: "".join(ch for ch in v if ch not in "!'()*")
                for k, v in data.items()}
    query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in sorted(filtered.items()))
    rid = hashlib.md5((query + _mixin_key(img_key, sub_key)).encode("utf-8")).hexdigest()
    filtered["w_rid"] = rid
    return filtered


def get_wbi_keys(cookie: str = "") -> tuple[str, str]:
    """从 nav 拿每日轮换的 img_key / sub_key；匿名也会返回（code -101）。"""
    now = time.time()
    with _wbi_lock:
        if _wbi_cache["img"] and now - _wbi_cache["at"] < 3600:
            return str(_wbi_cache["img"]), str(_wbi_cache["sub"])
    data = _http_json(NAV, cookie)
    if int(data.get("code") or 0) not in (0, -101):
        raise RuntimeError(f"B 站 nav 错误 {data.get('code')}: {data.get('message') or ''}")
    wbi = ((data.get("data") or {}).get("wbi_img") or {})
    img_url, sub_url = str(wbi.get("img_url") or ""), str(wbi.get("sub_url") or "")
    if not img_url or not sub_url:
        raise RuntimeError("B 站没有返回 WBI key")
    img = img_url.rsplit("/", 1)[-1].split(".", 1)[0]
    sub = sub_url.rsplit("/", 1)[-1].split(".", 1)[0]
    if len(img) < 32 or len(sub) < 32:
        raise RuntimeError("B 站 WBI key 格式不对")
    with _wbi_lock:
        _wbi_cache.update({"at": now, "img": img, "sub": sub})
    return img, sub


def danmu_info(room_id: int, cookie: str = "", timeout: float = 10.0) -> dict:
    """拿弹幕服务器地址和 token；失败抛异常，调用方负责重连。"""
    room_id = int(room_id)
    if room_id <= 0:
        raise ValueError("房间号必须大于 0")
    cookie = _cookie_with_fingerprint(cookie)
    params: dict = {"id": room_id, "type": 0}
    try:
        params = sign_wbi(params, *get_wbi_keys(cookie))
    except Exception:
        pass
    url = DANMU_INFO + "?" + urllib.parse.urlencode(params)
    data = _http_json(url, cookie, timeout)
    code = int(data.get("code") or 0)
    if code == -352:
        raise RuntimeError("B 站风控 -352：请在控制台「B 站 Cookie」填 SESSDATA 后重试")
    if code != 0:
        raise RuntimeError(f"B 站接口错误 {code}: {data.get('message') or ''}")
    info = data.get("data") or {}
    if not isinstance(info, dict) or not info.get("host_list"):
        raise RuntimeError("B 站没有返回弹幕服务器")
    return {"token": str(info.get("token") or ""),
            "hosts": info.get("host_list") or [],
            "cookie": cookie}


def encode_packet(op: int, body: bytes = b"", protover: int = 1) -> bytes:
    body = body or b""
    return struct.pack(">IHHII", HEADER_LEN + len(body), HEADER_LEN,
                       int(protover), int(op), 1) + body


def decode_packets(raw: bytes) -> list[dict]:
    """解出 op=5 的 JSON 消息；支持 zlib / brotli 压缩包。"""
    out: list[dict] = []
    if not raw:
        return out
    offset = 0
    while offset + HEADER_LEN <= len(raw):
        try:
            total, hlen, ver, op, _seq = struct.unpack(
                ">IHHII", raw[offset:offset + HEADER_LEN])
        except struct.error:
            break
        if total < HEADER_LEN or offset + total > len(raw):
            break
        body = raw[offset + hlen:offset + total]
        offset += total
        if ver == 2:
            try:
                body = zlib.decompress(body)
            except zlib.error:
                continue
        elif ver == 3:
            try:
                import brotli  # type: ignore
            except Exception:
                continue
            try:
                body = brotli.decompress(body)
            except Exception:
                continue
        if ver in (2, 3):
            out.extend(decode_packets(body))
            continue
        if op != OP_MESSAGE or not body:
            continue
        try:
            msg = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(msg, dict):
            out.append(msg)
    return out


@dataclass(frozen=True)
class RoomEvent:
    """一条直播间互动；控制台只读这个结构。"""

    room_id: int
    kind: str                     # enter/follow/like/chat/gift/super_chat/guard/system
    username: str = ""
    text: str = ""
    uid: int = 0
    gift_name: str = ""
    count: int = 0
    coin: int = 0
    timestamp: int = 0
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    def line(self) -> str:
        """一行展示（桌宠日志 / 状态里的 last_event）。"""
        who = self.username or "观众"
        if self.kind == "gift":
            return f"{who} 送出 {self.gift_name} ×{self.count}"
        if self.kind == "super_chat":
            return f"{who} 醒目留言 ¥{self.coin}：{self.text}"
        if self.kind == "guard":
            return f"{who} 上舰 {self.gift_name}"
        return f"{who} {self.text}" if self.text else f"{who} {self.label}"

    def to_dict(self) -> dict:
        return {"at": time.time(), "kind": self.kind, "label": self.label,
                "user": self.username, "uid": self.uid, "text": self.text,
                "gift": self.gift_name, "count": self.count, "coin": self.coin,
                "room_id": self.room_id, "ts": self.timestamp or int(time.time())}


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cookie_value(cookie: str, name: str) -> str:
    """从 Cookie 串里取一个值（只读，不打印整串）。"""
    for part in str(cookie or "").split(";"):
        key, _, val = part.strip().partition("=")
        if key.strip() == name:
            return val.strip()
    return ""


# ---------------------------------------------------------------- 房间互动解析
def _varint(raw: bytes, i: int) -> tuple[int | None, int]:
    shift = val = 0
    while i < len(raw) and shift <= 63:
        b = raw[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7
    return None, i


def _pb_fields(raw: bytes) -> dict[int, list]:
    """极小 protobuf 读取：INTERACT_WORD_V2 的 pb 只有 varint 和 length-delimited。"""
    out: dict[int, list] = {}
    i = 0
    while i < len(raw):
        key, i = _varint(raw, i)
        if key is None:
            break
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _varint(raw, i)
            if val is None:
                break
            out.setdefault(num, []).append(val)
        elif wire == 2:
            ln, i = _varint(raw, i)
            if ln is None or i + ln > len(raw):
                break
            out.setdefault(num, []).append(raw[i:i + ln])
            i += ln
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        else:
            break
    return out


def _pb_text(fields: dict[int, list], num: int) -> str:
    for val in fields.get(num) or []:
        if isinstance(val, bytes):
            try:
                return val.decode("utf-8", "replace")
            except Exception:
                return ""
    return ""


def _interact_event(data: dict, room_id: int) -> RoomEvent | None:
    """进入直播间 / 关注 / 分享：老协议是明文字段，新协议是 base64 的 pb。"""
    kind_map = {1: "enter", 2: "follow", 3: "share"}
    if str(data.get("pb") or "").strip():
        import base64

        try:
            fields = _pb_fields(base64.b64decode(str(data["pb"])))
        except Exception:
            fields = {}
        uid = _as_int((fields.get(1) or [0])[0])
        uname = _pb_text(fields, 2)
        kind = kind_map.get(_as_int((fields.get(3) or [0])[0]), "enter")
        ts = _as_int((fields.get(4) or [0])[0])
        text = {"enter": "进入直播间", "follow": "关注了主播", "share": "分享了直播间"}[kind]
        return RoomEvent(room_id=room_id, kind=kind, username=uname or "观众",
                         text=text, uid=uid, timestamp=ts or int(time.time()))
    kind = kind_map.get(_as_int(data.get("msg_type"), 0), "enter")
    text = {"enter": "进入直播间", "follow": "关注了主播", "share": "分享了直播间"}[kind]
    return RoomEvent(room_id=room_id, kind=kind,
                     username=str(data.get("uname") or "观众"),
                     text=text, uid=_as_int(data.get("uid")),
                     timestamp=_as_int(data.get("timestamp")) or int(time.time()))


def room_event_from_message(msg: dict, room_id: int) -> RoomEvent | None:
    """把一条 WS 消息解析成 RoomEvent；认不出的返回 None。"""
    if not isinstance(msg, dict):
        return None
    cmd = str(msg.get("cmd") or "")
    data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
    if not data and not cmd.startswith("DANMU_MSG"):
        # 礼物/互动类都靠 data；空 data 的消息是残包，不能当成「观众送出礼物」。
        return None
    try:
        if cmd.startswith("DANMU_MSG"):
            info = msg.get("info") or []
            if len(info) < 3 or not isinstance(info[2], list):
                return None
            text = str(info[1] or "")
            user = info[2]
            ts = _as_int((info[0] or [0, 0, 0, 0, 0])[4] if isinstance(info[0], list) else 0)
            if ts > 10_000_000_000:      # 毫秒
                ts //= 1000
            if not ts and isinstance(info[9], dict):
                ts = _as_int(info[9].get("ts"))
            return RoomEvent(room_id=room_id, kind="chat",
                             username=str(user[1] if len(user) > 1 else "观众"),
                             text=text, uid=_as_int(user[0] if user else 0),
                             timestamp=ts or int(time.time()))
        if cmd.startswith("INTERACT_WORD"):
            return _interact_event(data, room_id)
        if cmd.startswith("LIKE_INFO_V3_CLICK"):
            return RoomEvent(room_id=room_id, kind="like",
                             username=str(data.get("uname") or "观众"),
                             text=str(data.get("like_text") or "点赞了主播"),
                             uid=_as_int(data.get("uid")),
                             timestamp=_as_int(data.get("timestamp")) or int(time.time()))
        if cmd.startswith("LIKE_INFO_V3_UPDATE"):
            count = _as_int(data.get("click_count"))
            return RoomEvent(room_id=room_id, kind="like", username="",
                             text=f"本场累计点赞 {count}" if count else "本场有人点赞",
                             timestamp=int(time.time()))
        if cmd.startswith("ONLINE_RANK_COUNT"):
            count = _as_int(data.get("count") or data.get("online_count"))
            return RoomEvent(room_id=room_id, kind="system", username="",
                             text=f"在线 {count} 人" if count else "在线人数更新",
                             timestamp=int(time.time()))
        if cmd.startswith("SEND_GIFT"):
            num = max(1, _as_int(data.get("num"), 1))
            gift = str(data.get("giftName") or data.get("gift_name") or "礼物")
            return RoomEvent(room_id=room_id, kind="gift",
                             username=str(data.get("uname") or "观众"),
                             text=f"送出 {gift} ×{num}", gift_name=gift, count=num,
                             coin=max(0, _as_int(data.get("total_coin")
                                                 or data.get("price"))),
                             uid=_as_int(data.get("uid")),
                             timestamp=_as_int(data.get("timestamp"))
                             or int(time.time()), raw=data)
        if cmd.startswith("SUPER_CHAT_MESSAGE"):
            user = data.get("user_info") or {}
            return RoomEvent(room_id=room_id, kind="super_chat",
                             username=str(user.get("uname") or data.get("uname") or "观众"),
                             text=str(data.get("message") or ""),
                             gift_name="醒目留言", count=1,
                             coin=max(0, _as_int(data.get("price"))),
                             uid=_as_int(user.get("uid")),
                             timestamp=_as_int(data.get("start_time"))
                             or int(time.time()), raw=data)
        if cmd.startswith("GUARD_BUY"):
            num = max(1, _as_int(data.get("num"), 1))
            gift = str(data.get("gift_name") or "上舰")
            return RoomEvent(room_id=room_id, kind="guard",
                             username=str(data.get("uname") or "观众"),
                             text=f"上舰 {gift}", gift_name=gift, count=num,
                             coin=max(0, _as_int(data.get("price"))),
                             uid=_as_int(data.get("uid")),
                             timestamp=_as_int(data.get("start_time"))
                             or int(time.time()), raw=data)
    except (TypeError, ValueError, IndexError):
        return None
    return None


class RoomLog:
    """最近 N 条直播间互动；JSONL 落盘，进程重启也能看到上一场尾巴。

    只由监听进程写；控制台只读。写入是「追加一行」，文件超过 200KB 时压缩成
    最近 ``maxlen`` 条，避免一晚上把磁盘写满。
    """

    def __init__(self, path: str | Path | None = None, maxlen: int = ROOM_MAX_EVENTS):
        self.path = Path(path) if path is not None else _default_log_path()
        self.maxlen = max(1, int(maxlen))
        self._lock = threading.Lock()
        self._items: list[dict] = self._load()

    def _load(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows: list[dict] = []
        for line in lines[-self.maxlen * 2:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("kind"):
                rows.append(row)
        return rows[-self.maxlen:]

    def recent(self, limit: int | None = None) -> list[dict]:
        with self._lock:
            n = self.maxlen if not limit else max(0, int(limit))
            return [dict(x) for x in self._items[-n:]]

    def append(self, event) -> dict:
        row = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        row.setdefault("at", time.time())
        with self._lock:
            self._items.append(row)
            if len(self._items) > self.maxlen:
                self._items = self._items[-self.maxlen:]
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                if self.path.stat().st_size > 200_000:
                    self._compact()
            except OSError:
                pass
            return dict(row)

    def _compact(self) -> None:
        try:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n"
                                   for x in self._items[-self.maxlen:]),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def clear(self) -> None:
        with self._lock:
            self._items = []
            try:
                self.path.unlink()
            except OSError:
                pass


def _default_log_path() -> Path:
    from .. import paths

    return paths.LOGS / ROOM_LOG_NAME


def _http_post_form(url: str, form: dict, cookie: str, timeout: float = 10.0) -> dict:
    body = urllib.parse.urlencode(form).encode("utf-8")
    headers = _headers(cookie)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers)
    with _opener().open(req, timeout=timeout) as resp:
        raw = resp.read()
    data = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("B 站返回不是对象")
    return data


def send_danmu(room_id: int, text: str, cookie: str = "", timeout: float = 10.0) -> dict:
    """往直播间发一条弹幕。需要带 ``bili_jct`` 的登录 Cookie。"""
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("弹幕不能为空")
    if len(text) > 40:
        raise ValueError("弹幕最多 40 个字")
    room = int(room_id or 0)
    if room <= 0:
        raise ValueError("直播间号必须大于 0")
    csrf = _cookie_value(cookie, "bili_jct")
    if not str(cookie or "").strip() or not csrf:
        raise RuntimeError("发弹幕需要登录 Cookie（含 SESSDATA 和 bili_jct）")
    data = _http_post_form(SEND_DANMU, {
        "bubble": "0", "msg": text, "color": "16777215", "mode": "1",
        "fontsize": "25", "rnd": str(int(time.time())), "roomid": str(room),
        "csrf": csrf, "csrf_token": csrf,
    }, cookie, timeout=timeout)
    code = _as_int(data.get("code"))
    message = str(data.get("message") or data.get("msg") or "").strip()
    if code != 0:
        hints = {-101: "未登录或 Cookie 过期", -111: "被禁言",
                 10030: "弹幕格式不对", -400: "请求参数不对",
                 1003212: "发送太频繁"}
        raise RuntimeError(f"发弹幕失败 {code}：{hints.get(code) or message or '未知错误'}")
    return {"ok": True, "text": text, "room_id": room}


def event_from_message(msg: dict, room_id: int) -> GiftEvent | None:
    """兼容旧接口：只把礼物类消息转成 GiftEvent（桌宠动画用）。"""
    event = room_event_from_message(msg, room_id)
    if event is None or event.kind not in GIFT_KINDS:
        return None
    return GiftEvent(
        room_id=int(room_id),
        kind=event.kind,
        username=event.username or "观众",
        gift_name=event.gift_name or "醒目留言",
        count=max(1, event.count or 1),
        coin=max(0, event.coin),
        timestamp=event.timestamp or int(time.time()),
        raw=event.raw or {},
    )


_WS_LOGS_QUIET = False


def _quiet_websockets_logs() -> None:
    """收掉 websockets keepalive 线程的整段 traceback。

    B 站弹幕服务器不回 WebSocket 层 ping，旧代码没关 ``ping_interval``，每 20s
    就会打一次 ``keepalive ping failed`` + 三条链式栈。现在已设
    ``ping_interval=None``；这个过滤器只是兜底，别让将来的参数改动再把刷屏带回来。
    我们自己的 ``status()["error"]`` 仍会记录断开原因。
    """
    global _WS_LOGS_QUIET
    if _WS_LOGS_QUIET:
        return
    import logging

    class _DropKeepalive(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "keepalive ping failed" not in record.getMessage()

    # 客户端协议实际用的是 websockets.client（protocol.logger），只给父级加过滤器
    # 拦不住子 logger 发出的记录。
    for name in ("websockets", "websockets.client"):
        logging.getLogger(name).addFilter(_DropKeepalive())
    _WS_LOGS_QUIET = True


class BilibiliGiftListener:
    """后台线程监听一个直播间；断了自动重连，stop() 后退出。

    ``on_gift`` 只管礼物类（桌宠动画）；``on_event`` 收全部互动；
    ``room_log`` 给了就把每条互动写进最近 120 条。
    """

    def __init__(self, room_id: int, on_gift: Callable[[GiftEvent], None],
                 cookie: str = "", *, logger: Callable[[str], None] | None = None,
                 on_event: Callable[[RoomEvent], None] | None = None,
                 room_log: RoomLog | None = None):
        self.room_id = int(room_id)
        self.on_gift = on_gift
        self.on_event = on_event
        self.room_log = room_log
        self.cookie = cookie or ""
        self.log = logger or (lambda text: print(text, flush=True))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._status: dict = {
            "ok": False, "connected": False, "room_id": self.room_id,
            "last_event": "", "gifts": 0, "events": 0, "counts": {},
            "error": "", "updated_at": 0.0,
        }
        self._ws = None
        self._live = False

    # ---------------------------------------------------------------- 状态
    def _set_status(self, **patch) -> None:
        with self._lock:
            self._status.update(patch)
            self._status["updated_at"] = time.time()

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def start(self) -> dict:
        _quiet_websockets_logs()
        if self._thread and self._thread.is_alive():
            return self.status()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="dafeiyu-bilibili",
                                        daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        th = self._thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(4.0)

    # ---------------------------------------------------------------- 连接
    def _run(self) -> None:
        delay = 3.0
        while not self._stop.is_set():
            self._live = False
            try:
                info = danmu_info(self.room_id, self.cookie)
                self._set_status(ok=True, error="", room_id=self.room_id)
                self._connect_once(info)
                delay = 3.0
            except Exception as exc:  # noqa: BLE001
                if self._live:
                    # 连上过又断（B 站会主动踢长连接）：立刻重连，不走指数退避——
                    # 否则一场直播断一次就等 60 秒，弹幕/礼物全漏。
                    self._set_status(ok=True, connected=False,
                                     error="连接断开，正在重连")
                    self.log("[bilibili] 连接断开，3s 后重连")
                    self._stop.wait(3.0)
                    delay = 3.0
                    continue
                msg = f"{type(exc).__name__}: {exc}"
                self._set_status(ok=False, connected=False, error=msg)
                self.log(f"[bilibili] {msg}，{delay:.0f}s 后重连")
                self._stop.wait(delay)
                delay = min(60.0, delay * 1.7)

    def _connect_once(self, info: dict) -> None:
        from websockets.sync.client import connect  # type: ignore

        hosts = info.get("hosts") or []
        host = str((hosts[0] or {}).get("host") or "broadcastlv.chat.bilibili.com")
        port = int((hosts[0] or {}).get("wss_port") or 443)
        url = f"wss://{host}:{port}/sub"
        auth = {
            "uid": 0, "roomid": self.room_id, "protover": 2,
            "platform": "web", "type": 2, "key": info.get("token") or "",
        }
        # ping_interval=None：B 站弹幕服务器不回 WebSocket 层的 ping，客户端每 20s
        # 一次 keepalive 会 1011 超时，然后 websockets 自己打一整段 traceback。保活
        # 改由下面 30s 一次的 JSON 心跳（OP_HEARTBEAT）负责，和官方网页一致。
        with connect(url, open_timeout=10, close_timeout=3,
                     ping_interval=None, max_size=8 * 1024 * 1024) as ws:
            self._ws = ws
            ws.send(encode_packet(OP_AUTH, json.dumps(
                auth, ensure_ascii=False).encode("utf-8")))
            self._live = True
            self._set_status(ok=True, connected=True, error="")
            last_ping = time.monotonic()
            while not self._stop.is_set():
                if time.monotonic() - last_ping >= 30:
                    ws.send(encode_packet(OP_HEARTBEAT, b"[object Object]"))
                    last_ping = time.monotonic()
                try:
                    raw = ws.recv(timeout=1.0)
                except TimeoutError:
                    continue
                if isinstance(raw, str):
                    raw = raw.encode("utf-8", "replace")
                for msg in decode_packets(raw):
                    self._handle(msg)
        self._ws = None
        self._set_status(connected=False)
        if not self._stop.is_set():
            raise ConnectionError("B 站连接已断开")

    def _handle(self, msg: dict) -> None:
        event = room_event_from_message(msg, self.room_id)
        if event is None:
            return
        with self._lock:
            self._counts[event.kind] = int(self._counts.get(event.kind) or 0) + 1
            self._status["events"] = int(self._status.get("events") or 0) + 1
            if event.kind in GIFT_KINDS:
                self._status["gifts"] = int(self._status.get("gifts") or 0) + 1
            self._status["last_event"] = event.line()[:120]
            self._status["counts"] = dict(self._counts)
            self._status["updated_at"] = time.time()
        if self.room_log is not None:
            try:
                self.room_log.append(event)
            except Exception as exc:  # noqa: BLE001
                self.log(f"[bilibili] 互动落盘失败：{type(exc).__name__}: {exc}")
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception as exc:  # noqa: BLE001
                self.log(f"[bilibili] 互动回调失败：{type(exc).__name__}: {exc}")
        if event.kind not in GIFT_KINDS:
            return
        gift = event_from_message(msg, self.room_id)
        if gift is None:
            return
        try:
            self.on_gift(gift)
        except Exception as exc:  # noqa: BLE001
            self.log(f"[bilibili] 礼物回调失败：{type(exc).__name__}: {exc}")


__all__ = ["BilibiliGiftListener", "GiftEvent", "RoomEvent", "RoomLog",
           "KIND_LABELS", "ROOM_MAX_EVENTS", "danmu_info", "send_danmu",
           "decode_packets", "encode_packet", "event_from_message",
           "room_event_from_message"]
