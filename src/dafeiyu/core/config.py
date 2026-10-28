"""运行配置：默认值 < config.json < 环境变量。

环境变量优先，方便直播时临时改（不动文件）：

    DAFEIYU_VOICE=animal        语音层首选（mambo / animal / off）
    DAFEIYU_VOLUME=0.8          音量
    DAFEIYU_PERSONA=dafeiyu     性格层人设卡
    DAFEIYU_GAME=slay-the-spire-2
    DAFEIYU_OVERLAY=http://127.0.0.1:8730
    DAFEIYU_TTS_URL=http://127.0.0.1:9880
    STS2_TTS_*                  兼容旧工程的环境变量
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .. import paths


def _env_key(name: str) -> str:
    return name.upper()


@dataclass(slots=True)
class LiveConfig:
    # 层
    persona: str = "dafeiyu"
    voice: str = "mambo"
    fallback_voice: str = "animal"
    game: str = "slay-the-spire-2"

    # 音量 / 节奏
    volume: float = 0.55
    min_gap: float = 1.2

    # 服务
    host: str = "127.0.0.1"
    port: int = 8730
    overlay_url: str = "http://127.0.0.1:8730"
    tts_url: str = "http://127.0.0.1:9880"
    tts_ref: str = ""
    tts_prompt: str = "大家好，欢迎来到我的频道，今天给大家分享一个有趣的内容"
    tts_speed: float = 1.0
    tts_timeout: float = 60.0

    # 行为
    push_overlay: bool = True
    strict_lint: bool = False
    extra: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- 读取
    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "LiveConfig":
        cfg = cls()
        cfg.overlay_url = f"http://{cfg.host}:{cfg.port}"
        file_path = Path(path) if path else paths.CONFIG_FILE
        if file_path.exists():
            try:
                raw = json.loads(file_path.read_text(encoding="utf-8"))
                cfg._merge(raw if isinstance(raw, dict) else {})
            except (OSError, ValueError):
                pass
        cfg._apply_env()
        return cfg

    def _merge(self, raw: dict) -> None:
        known = {f.name for f in fields(self)}
        for key, value in raw.items():
            if key in known and value is not None:
                setattr(self, key, value)
            elif key not in known:
                self.extra[key] = value

    def _apply_env(self) -> None:
        pairs = {
            "DAFEIYU_PERSONA": "persona",
            "DAFEIYU_VOICE": "voice",
            "DAFEIYU_FALLBACK_VOICE": "fallback_voice",
            "DAFEIYU_GAME": "game",
            "DAFEIYU_VOLUME": "volume",
            "DAFEIYU_MIN_GAP": "min_gap",
            "DAFEIYU_HOST": "host",
            "DAFEIYU_PORT": "port",
            "DAFEIYU_OVERLAY": "overlay_url",
            "DAFEIYU_TTS_URL": "tts_url",
            "DAFEIYU_TTS_REF": "tts_ref",
            "DAFEIYU_TTS_PROMPT": "tts_prompt",
            "DAFEIYU_TTS_SPEED": "tts_speed",
            "DAFEIYU_TTS_TIMEOUT": "tts_timeout",
            # 旧工程的环境变量，继续认
            "STS2_TTS_URL": "tts_url",
            "STS2_TTS_REF": "tts_ref",
            "STS2_TTS_PROMPT": "tts_prompt",
            "STS2_TTS_SPEED": "tts_speed",
            "STS2_OVERLAY": "overlay_url",
            "STS2_OVERLAY_PORT": "port",
        }
        for env, attr in pairs.items():
            val = os.environ.get(env)
            if val is None or val == "":
                continue
            cur = getattr(self, attr)
            try:
                if isinstance(cur, bool):
                    setattr(self, attr, val.strip().lower() not in ("0", "false", "no", "off"))
                elif isinstance(cur, int):
                    setattr(self, attr, int(val))
                elif isinstance(cur, float):
                    setattr(self, attr, float(val))
                else:
                    setattr(self, attr, val)
            except (TypeError, ValueError):
                continue
        if "STS2_OVERLAY_PORT" in os.environ and "DAFEIYU_OVERLAY" not in os.environ:
            self.overlay_url = f"http://{self.host}:{self.port}"

    # ---------------------------------------------------------------- 输出
    def to_dict(self) -> dict:
        out = asdict(self)
        out.pop("extra", None)
        out["tts_ref"] = bool(out.get("tts_ref"))
        return out


__all__ = ["LiveConfig"]
