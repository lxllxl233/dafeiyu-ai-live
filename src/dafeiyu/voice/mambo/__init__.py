"""曼波语（Mambo）：本地 MamboTTS / GPT-SoVITS 合成。

引擎默认在 ``127.0.0.1:9880``，POST ``/`` 拿 WAV；页面和桌面窗共用 ``logs/tts_cache``。
引擎不在线不是错误——路由层会回退到动物语，直播不哑。

配置优先级：构造参数 > 环境变量 > ``voice/mambo/ref.json`` > 默认值。
参考音频（ref_wav）是机器本地路径，不进仓库；用 ``DAFEIYU_TTS_REF``（或游戏包声明的旧前缀）指过去。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

from ...games.catalog import env_value
from ..base import VoiceResult, is_wav, read_cache, wav_duration, wav_gain, write_cache

DEFAULTS = {
    "url": "http://127.0.0.1:9880",
    "prompt": "大家好，欢迎来到我的频道，今天给大家分享一个有趣的内容",
    "prompt_language": "zh",
    "text_language": "zh",
    "speed": 1.0,
    "timeout": 60.0,
    "volume": 0.55,
    "cut_punc": "，。？！；：、…,.;?!",
    "ref_wav": "",
}


def _load_config(path: Path | None) -> dict:
    cfg = dict(DEFAULTS)
    if path and path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
    return cfg


class MamboVoice:
    name = "mambo"

    def __init__(
        self,
        url: str | None = None,
        *,
        ref_wav: str | None = None,
        prompt: str | None = None,
        speed: float | None = None,
        volume: float | None = None,
        cache_dir: str | os.PathLike[str] | None = None,
        timeout: float | None = None,
        config_path: str | os.PathLike[str] | None = None,
    ) -> None:
        base = Path(__file__).resolve().parents[3] / "voice" / "mambo" / "ref.json"
        self.cfg = _load_config(Path(config_path) if config_path else base)
        self.url = (url or env_value("TTS_URL", "") or self.cfg["url"]).rstrip("/")
        self.ref_wav = ref_wav or env_value("TTS_REF", "") or self.cfg["ref_wav"]
        self.prompt = prompt or env_value("TTS_PROMPT", "") or self.cfg["prompt"]
        self.speed = float(speed if speed is not None else self.cfg["speed"])
        self.volume = float(volume if volume is not None else self.cfg["volume"])
        self.timeout = float(timeout if timeout is not None else self.cfg["timeout"])
        if cache_dir is None:
            cache_dir = Path(__file__).resolve().parents[3] / "logs" / "tts_cache"
        self.cache_dir = Path(cache_dir)
        self._lock = threading.Lock()
        self._stat = {"ok": 0, "fail": 0, "last_ok": 0.0, "last_fail": 0.0, "last_ms": 0, "consec_fail": 0}
        self._online: tuple[float, bool] = (0.0, False)

    # ---------------------------------------------------------------- 在线检查
    def available(self, ttl: float = 5.0) -> bool:
        now = time.time()
        ts, ok = self._online
        if now - ts < ttl:
            return ok
        ok = self.ping()
        self._online = (now, ok)
        return ok

    def ping(self, timeout: float = 1.5) -> bool:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(self.url + "/control", timeout=timeout) as r:
                r.read(64)
            return True
        except Exception:
            return False

    # ---------------------------------------------------------------- 合成
    def _post(self, text: str, volume: float) -> bytes | None:
        payload = {
            "text": text,
            "text_language": self.cfg.get("text_language", "zh"),
            "speed": self.speed,
            "cut_punc": self.cfg.get("cut_punc", ""),
        }
        if self.ref_wav:
            payload["refer_wav_path"] = self.ref_wav
            payload["prompt_text"] = self.prompt
            payload["prompt_language"] = self.cfg.get("prompt_language", "zh")
        req = urllib.request.Request(
            self.url + "/",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        t0 = time.time()
        try:
            with opener.open(req, timeout=self.timeout) as r:
                data = r.read()
        except Exception as exc:
            self._record(False, t0, str(exc))
            return None
        if not is_wav(data):
            self._record(False, t0, "返回不是 WAV")
            return None
        self._record(True, t0, "")
        return data

    def _record(self, ok: bool, t0: float, err: str) -> None:
        ms = int((time.time() - t0) * 1000)
        with self._lock:
            if ok:
                self._stat.update(ok=self._stat["ok"] + 1, last_ok=time.time(),
                                  last_ms=ms, consec_fail=0)
            else:
                self._stat.update(fail=self._stat["fail"] + 1, last_fail=time.time(),
                                  last_ms=ms, consec_fail=self._stat["consec_fail"] + 1)

    def synth(self, text: str, volume: float | None = None) -> VoiceResult:
        text = (text or "").strip()
        if not text:
            return VoiceResult(voice=self.name, error="空台词")
        vol = self.volume if volume is None else volume
        cached = read_cache(self.cache_dir, text)
        if cached:
            return VoiceResult(wav=wav_gain(cached, vol), voice=self.name, cached=True,
                               duration=wav_duration(cached))
        raw = self._post(text, 1.0)
        if not raw:
            return VoiceResult(voice=self.name, error=f"引擎无响应（{self.url}）")
        write_cache(self.cache_dir, text, raw)
        return VoiceResult(wav=wav_gain(raw, vol), voice=self.name,
                           duration=wav_duration(raw))

    def status(self) -> dict:
        online = self.available()
        with self._lock:
            stat = dict(self._stat)
        return {
            "name": self.name,
            "available": online,
            "engine": "MamboTTS / GPT-SoVITS",
            "url": self.url,
            "ref_wav": bool(self.ref_wav),
            "speed": self.speed,
            "volume": self.volume,
            "cache": str(self.cache_dir),
            **stat,
        }


__all__ = ["MamboVoice"]
