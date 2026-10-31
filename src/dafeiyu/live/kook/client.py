"""KOOK 开放平台的最小 HTTP 封装：网关、发文字/图片/语音卡片、发私信。

只依赖 ``requests``，不把 WebSocket 逻辑塞进来；token 只留在内存/本机配置里，
日志只记错误码，不记 token。
"""

from __future__ import annotations

import json
from typing import Any

import requests

_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}


class KookError(RuntimeError):
    pass


class KookClient:
    BASE = "https://www.kookapp.cn/api/v3"

    def __init__(self, token: str, timeout: float = 15.0) -> None:
        self.token = str(token or "").strip()
        self.timeout = float(timeout)
        if not self.token:
            raise KookError("缺少 KOOK Bot Token")

    # ---------------------------------------------------------------- 请求
    def _call(self, method: str, path: str, *, params: dict | None = None,
              body: dict | None = None, files: dict | None = None) -> dict:
        headers = {"Authorization": f"Bot {self.token}"}
        kwargs: dict = {"params": params, "timeout": self.timeout}
        if files is None:
            headers["Content-Type"] = "application/json"
            kwargs["json"] = body
        else:
            # 让 requests 自己生成 multipart boundary，不能手写 Content-Type。
            kwargs["files"] = files
            kwargs["data"] = body or {}
        try:
            resp = requests.request(method, self.BASE + path,
                                    headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise KookError(f"网络错误：{type(exc).__name__}: {exc}") from exc
        try:
            payload = resp.json() if resp.content else {}
        except ValueError as exc:
            raise KookError(f"HTTP {resp.status_code}: 返回不是 JSON") from exc
        if not isinstance(payload, dict):
            raise KookError(f"HTTP {resp.status_code}: 返回格式不对")
        code = payload.get("code")
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = -1
        if resp.status_code >= 400 or code_int != 0:
            msg = str(payload.get("message") or payload.get("msg") or "")
            raise KookError(f"{code_int}: {msg or f'HTTP {resp.status_code}'}")
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def gateway(self) -> str:
        """取 WebSocket 网关；compress=0 让服务端发纯文本。"""
        data = self._call("GET", "/gateway/index", params={"compress": 0})
        url = str(data.get("url") or "")
        if not url:
            raise KookError("网关没有返回 url")
        return url

    def me(self) -> dict:
        return self._call("GET", "/user/me")

    def guilds(self) -> list[dict]:
        data = self._call("GET", "/guild/list", params={"page_size": 50})
        return [g for g in (data.get("items") or []) if isinstance(g, dict)]

    def channels(self, guild_id: str) -> list[dict]:
        data = self._call("GET", "/channel/list",
                          params={"guild_id": str(guild_id), "type": 1,
                                  "page_size": 50})
        return [c for c in (data.get("items") or []) if isinstance(c, dict)]

    def messages(self, target_id: str, limit: int = 20) -> list[dict]:
        """读频道最近消息；只给图片轮询兜底用。"""
        data = self._call("GET", "/message/list",
                          params={"target_id": str(target_id),
                                  "page_size": max(1, min(int(limit or 20), 50))})
        return [m for m in (data.get("items") or []) if isinstance(m, dict)]

    def _send(self, body: dict, channel_type: str) -> dict:
        path = ("/direct-message/create" if str(channel_type).upper() == "PERSON"
                else "/message/create")
        data = self._call("POST", path, body=body)
        return {"ok": True, "msg_id": str(data.get("msg_id") or "")}

    def send_text(self, target_id: str, text: str, channel_type: str = "GROUP",
                  quote: str = "", reply_msg_id: str = "") -> dict:
        """发文字；频道用 message/create，私信用 direct-message/create。"""
        body: dict[str, Any] = {
            "type": 1,
            "target_id": str(target_id or ""),
            "content": str(text),
        }
        if quote:
            body["quote"] = str(quote)
        if reply_msg_id:
            body["reply_msg_id"] = str(reply_msg_id)
        return self._send(body, channel_type)

    def upload_asset(self, data: bytes, filename: str,
                     content_type: str = "application/octet-stream") -> str:
        """上传图片/音频/文件，返回 KOOK 自己的资源 URL。

        KOOK 要求图片/语音/文件消息里的 URL 必须由机器人先上传；直接塞外链
        会被拒绝（“找不到资源”）。上传接口用 multipart，不能走 JSON。
        """
        if not data:
            raise KookError("上传内容为空")
        result = self._call(
            "POST", "/asset/create",
            files={"file": (str(filename or "asset.bin"), data,
                            str(content_type or "application/octet-stream"))})
        url = str(result.get("url") or "")
        if not url:
            raise KookError("上传成功但没有返回 url")
        return url

    def send_image(self, target_id: str, url: str, channel_type: str = "GROUP",
                   quote: str = "", reply_msg_id: str = "") -> dict:
        """发图片；url 必须是 upload_asset() 的返回值。"""
        body: dict[str, Any] = {
            "type": 2,
            "target_id": str(target_id or ""),
            "content": str(url or ""),
        }
        if quote:
            body["quote"] = str(quote)
        if reply_msg_id:
            body["reply_msg_id"] = str(reply_msg_id)
        return self._send(body, channel_type)

    def send_card(self, target_id: str, card: list | dict, channel_type: str = "GROUP",
                  quote: str = "", reply_msg_id: str = "") -> dict:
        """发卡片消息（type=10），content 是卡片 JSON 字符串。"""
        body: dict[str, Any] = {
            "type": 10,
            "target_id": str(target_id or ""),
            "content": json.dumps(card, ensure_ascii=False),
        }
        if quote:
            body["quote"] = str(quote)
        if reply_msg_id:
            body["reply_msg_id"] = str(reply_msg_id)
        return self._send(body, channel_type)

    def send_audio_card(self, target_id: str, url: str, channel_type: str = "GROUP",
                        title: str = "大肥鱼语音", cover: str = "",
                        quote: str = "", reply_msg_id: str = "",
                        replay_button: bool = True) -> dict:
        """用卡片里的 audio 模块发语音。

        KOOK 机器人直接发 ``type=8`` 音频消息会被平台拒绝（40000
        “无法发送此消息类型”）；卡片类型的 ``audio`` 模块是官方支持的替代路径。
        手机端内嵌播放器第二次点同一条语音可能没声音，所以再放一个「重听」
        link 按钮，点开用浏览器播放。
        """
        module: dict[str, Any] = {"type": "audio", "src": str(url or ""),
                                  "title": str(title or "大肥鱼语音")}
        if cover:
            module["cover"] = str(cover)
        modules: list[dict] = [module]
        if replay_button and url:
            modules.append({
                "type": "action-group",
                "elements": [{
                    "type": "button", "theme": "primary",
                    "value": str(url), "click": "link", "text": "重听",
                }],
            })
        card = [{"type": "card", "theme": "none", "size": "sm",
                 "modules": modules}]
        return self.send_card(target_id, card, channel_type=channel_type,
                              quote=quote, reply_msg_id=reply_msg_id)

    def download(self, url: str, max_bytes: int = 10 * 1024 * 1024) -> tuple[bytes, str]:
        """下载用户发来的图片；只接受 http(s)，限制体积。"""
        link = str(url or "").strip()
        if not link.startswith(("https://", "http://")):
            raise KookError("图片地址不是 http(s)")
        try:
            resp = requests.get(link, timeout=self.timeout, stream=True,
                                headers={"User-Agent": "dafeiyu-kook/1.0"})
            resp.raise_for_status()
            mime = str(resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if mime and mime not in _IMAGE_MIME:
                raise KookError(f"不是图片：{mime}")
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_content(64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise KookError("图片太大")
                chunks.append(chunk)
        except requests.RequestException as exc:
            raise KookError(f"图片下载失败：{type(exc).__name__}") from exc
        if not chunks:
            raise KookError("图片是空的")
        if not mime:
            mime = "image/jpeg"
        return b"".join(chunks), mime


__all__ = ["KookClient", "KookError"]
