"""直播输出与语音门。

输出走现有 overlay 的 ``/comment``（和 STS2 同一个单一写入路径），带
``client_id`` 让页面的音频状态可跟踪。语音门是非阻塞的：clock 线程只
``offer``，不为 TTS 等待；忙时丢弃/延后，绝不让队列无限增长。
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request
import uuid


class OverlaySink:
    def __init__(self, base_url: str = "http://127.0.0.1:8730", *,
                 prewarm: bool = True, timeout: float = 2.0,
                 status: dict | None = None) -> None:
        self.base_url = str(base_url or "http://127.0.0.1:8730").rstrip("/")
        self.prewarm = bool(prewarm)
        self.timeout = max(0.3, float(timeout))
        # 例如 {"idle_text": "看视频ing"}；overlay 会把它存进 /state 的 status。
        self.status = dict(status) if isinstance(status, dict) else {}

    def _opener(self):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def say(self, text: str, *, tag: str = "云观影") -> dict:
        text = " ".join(str(text or "").split())
        if not text:
            return {"ok": False, "error": "空台词"}
        client_id = f"bili-{uuid.uuid4().hex[:12]}"
        payload = {"speech": text, "tag": tag[:12], "client_id": client_id,
                   **self.status}
        request = urllib.request.Request(
            self.base_url + "/comment",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener().open(request, timeout=self.timeout) as response:
                body = response.read()
            data = json.loads(body.decode("utf-8")) if body else {}
            ok = bool(response.status < 400 and (not isinstance(data, dict)
                                                  or data.get("ok", True)))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                    "client_id": client_id}
        if self.prewarm:
            threading.Thread(target=self._prewarm, args=(text,), daemon=True).start()
        return {"ok": ok, "client_id": client_id, "text": text}

    def clear_idle(self) -> bool:
        """B 模式结束时清掉 /state 里的「看视频ing」，别污染下一个游戏。"""
        request = urllib.request.Request(
            self.base_url + "/status",
            data=json.dumps({"idle_text": ""}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener().open(request, timeout=self.timeout) as response:
                return response.status < 400
        except Exception:
            return False

    def _prewarm(self, text: str) -> None:
        """后台让 /tts 先合成；失败不挡发布。"""
        import urllib.parse

        url = self.base_url + "/tts?text=" + urllib.parse.quote(text[:200])
        try:
            with self._opener().open(url, timeout=max(3.0, self.timeout)) as response:
                response.read(1)
        except Exception:
            pass


class SpeechGate:
    """单槽语音门：pending 或估算播放窗口内不再接收新台词。"""

    def __init__(self, sink, *, on_spoken=None, logger=None, now=None,
                 start_delay_s: float = 2.0) -> None:
        self.sink = sink
        self.on_spoken = on_spoken
        self.log = logger or (lambda text: None)
        self._now = now or time.monotonic
        self.start_delay_s = max(0.0, float(start_delay_s))
        self._queue: queue.Queue = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._pending = False
        self._busy_until = 0.0
        self._muted_until = 0.0
        self._fails = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="bili-speech",
                                        daemon=True)
        self._thread.start()

    def _estimate(self, text: str) -> float:
        return min(20.0, max(3.0, len(str(text or "")) * 0.25))

    def busy(self, now: float | None = None) -> bool:
        now = self._now() if now is None else now
        with self._lock:
            return bool(self._pending or now < self._busy_until or now < self._muted_until)

    def offer(self, text: str, *, now: float | None = None) -> bool:
        now = self._now() if now is None else now
        with self._lock:
            if self._pending or now < self._busy_until or now < self._muted_until:
                return False
            self._pending = True
        try:
            self._queue.put_nowait(str(text))
        except queue.Full:
            with self._lock:
                self._pending = False
            return False
        return True

    def offer_when_free(self, text: str, *, wait_s: float = 6.0,
                        should_stop=None) -> bool:
        """收尾问候用：等当前一句说完再发，最多等 wait_s 秒。"""
        deadline = self._now() + max(0.0, float(wait_s))
        while self.busy():
            if should_stop is not None and should_stop():
                return False
            if self._now() >= deadline:
                return False
            time.sleep(0.1)
        return self.offer(text)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                text = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if text is None:
                break
            try:
                result = self.sink.say(text)
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            ok = bool(isinstance(result, dict) and result.get("ok"))
            with self._lock:
                self._pending = False
                if ok:
                    self._fails = 0
                    self._busy_until = self._now() + self.start_delay_s + self._estimate(text)
                else:
                    self._fails += 1
                    if self._fails >= 3:
                        self._muted_until = self._now() + 60.0
            if ok and self.on_spoken is not None:
                try:
                    self.on_spoken(text)
                except Exception:
                    pass
            if not ok:
                self.log(f"[bili] 语音输出失败：{result.get('error') if isinstance(result, dict) else result}")

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(2.0)

    def snapshot(self) -> dict:
        now = self._now()
        with self._lock:
            return {
                "pending": bool(self._pending),
                "busy": bool(self._pending or now < self._busy_until or now < self._muted_until),
                "busy_left": round(max(0.0, self._busy_until - now), 2),
                "muted_left": round(max(0.0, self._muted_until - now), 2),
                "fails": self._fails,
            }


__all__ = ["OverlaySink", "SpeechGate"]
