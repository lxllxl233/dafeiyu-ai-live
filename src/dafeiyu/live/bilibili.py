"""B 站直播礼物监听（匿名可读；需要登录态时可在 config 里带 Cookie）。

只做一件事：连上直播间的弹幕 WebSocket，把 SEND_GIFT / 醒目留言 / 上舰
转成 ``GiftEvent`` 回调。不写数据库、不上传、不打印 Cookie 或 token。

协议参考：16 字节包头 + JSON；protover=2 时包体是 zlib。
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
from dataclasses import dataclass
from typing import Callable

DANMU_INFO = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"
NAV = "https://api.bilibili.com/x/web-interface/nav"
FINGERPRINT = "https://api.bilibili.com/x/frontend/finger/spi"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADER_LEN = 16
OP_HEARTBEAT = 2
OP_AUTH = 7
OP_MESSAGE = 5

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


def event_from_message(msg: dict, room_id: int) -> GiftEvent | None:
    cmd = str(msg.get("cmd") or "")
    data = msg.get("data")
    if not isinstance(data, dict):
        return None
    try:
        if cmd.startswith("SEND_GIFT"):
            gift = data.get("giftName") or data.get("gift_name") or "礼物"
            return GiftEvent(
                room_id=int(room_id), kind="gift",
                username=str(data.get("uname") or "观众"),
                gift_name=str(gift), count=max(1, int(data.get("num") or 1)),
                coin=max(0, int(data.get("total_coin") or data.get("price") or 0)),
                timestamp=int(data.get("timestamp") or time.time()),
                raw=data,
            )
        if cmd.startswith("SUPER_CHAT_MESSAGE"):
            user = data.get("user_info") or {}
            return GiftEvent(
                room_id=int(room_id), kind="super_chat",
                username=str(user.get("uname") or data.get("uname") or "观众"),
                gift_name="醒目留言", count=1,
                coin=max(0, int(data.get("price") or 0)),
                timestamp=int(data.get("start_time") or time.time()),
                raw=data,
            )
        if cmd.startswith("GUARD_BUY"):
            return GiftEvent(
                room_id=int(room_id), kind="guard",
                username=str(data.get("uname") or "观众"),
                gift_name=str(data.get("gift_name") or "上舰"),
                count=max(1, int(data.get("num") or 1)),
                coin=max(0, int(data.get("price") or 0)),
                timestamp=int(data.get("start_time") or time.time()),
                raw=data,
            )
    except (TypeError, ValueError):
        return None
    return None


class BilibiliGiftListener:
    """后台线程监听一个直播间；断了自动重连，stop() 后退出。"""

    def __init__(self, room_id: int, on_gift: Callable[[GiftEvent], None],
                 cookie: str = "", *, logger: Callable[[str], None] | None = None):
        self.room_id = int(room_id)
        self.on_gift = on_gift
        self.cookie = cookie or ""
        self.log = logger or (lambda text: print(text, flush=True))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._status: dict = {
            "ok": False, "connected": False, "room_id": self.room_id,
            "last_event": "", "gifts": 0, "error": "", "updated_at": 0.0,
        }
        self._ws = None

    # ---------------------------------------------------------------- 状态
    def _set_status(self, **patch) -> None:
        with self._lock:
            self._status.update(patch)
            self._status["updated_at"] = time.time()

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def start(self) -> dict:
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
            try:
                info = danmu_info(self.room_id, self.cookie)
                self._set_status(ok=True, error="", room_id=self.room_id)
                self._connect_once(info)
                delay = 3.0
            except Exception as exc:  # noqa: BLE001
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
        with connect(url, open_timeout=10, close_timeout=3,
                     max_size=8 * 1024 * 1024) as ws:
            self._ws = ws
            ws.send(encode_packet(OP_AUTH, json.dumps(
                auth, ensure_ascii=False).encode("utf-8")))
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
        event = event_from_message(msg, self.room_id)
        if event is None:
            return
        with self._lock:
            self._status["gifts"] = int(self._status.get("gifts") or 0) + 1
            self._status["last_event"] = event.amount_text
            self._status["updated_at"] = time.time()
        try:
            self.on_gift(event)
        except Exception as exc:  # noqa: BLE001
            self.log(f"[bilibili] 礼物回调失败：{type(exc).__name__}: {exc}")


__all__ = ["BilibiliGiftListener", "GiftEvent", "danmu_info",
           "decode_packets", "encode_packet", "event_from_message"]
