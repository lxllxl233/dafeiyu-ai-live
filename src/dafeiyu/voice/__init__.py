"""语音层：动物语 + 曼波语，统一走 VoiceRouter。

    from dafeiyu.voice import build_router
    router = build_router(cfg)          # 读 config/环境变量
    result = router.speak("它 21，我最多挡 15。")
    result.to_dict()                    # {"ok": True, "voice": "mambo", "duration": 2.31, ...}
"""

from __future__ import annotations

from .. import paths
from .animal import AnimalVoice
from .base import Voice, VoiceResult, wav_duration, wav_gain
from .mambo import MamboVoice
from .router import VoiceRouter


def build_router(cfg=None) -> VoiceRouter:
    """按配置装配语音层；cfg 可以是 LiveConfig，也可以缺省。"""
    volume = float(getattr(cfg, "volume", 0.55) or 0.55)
    tts_url = getattr(cfg, "tts_url", None) or "http://127.0.0.1:9880"
    tts_ref = getattr(cfg, "tts_ref", "") or ""
    tts_prompt = getattr(cfg, "tts_prompt", None)
    speed = float(getattr(cfg, "tts_speed", 1.0) or 1.0)
    timeout = float(getattr(cfg, "tts_timeout", 60.0) or 60.0)
    voices: dict[str, Voice] = {
        "animal": AnimalVoice(config_path=paths.VOICE / "animal" / "scale.json", volume=volume),
        "mambo": MamboVoice(
            url=tts_url,
            ref_wav=tts_ref or None,
            prompt=tts_prompt,
            speed=speed,
            volume=volume,
            cache_dir=paths.CACHE,
            timeout=timeout,
            config_path=paths.VOICE / "mambo" / "ref.json",
        ),
    }
    return VoiceRouter(
        voices,
        primary=str(getattr(cfg, "voice", "mambo") or "mambo"),
        fallback=str(getattr(cfg, "fallback_voice", "animal") or "animal"),
        volume=volume,
    )


__all__ = ["AnimalVoice", "MamboVoice", "VoiceRouter", "VoiceResult", "build_router",
           "wav_duration", "wav_gain"]
