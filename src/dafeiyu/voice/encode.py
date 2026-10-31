"""把 TTS 的 WAV 转成 MP3 再上传。

KOOK 手机端的卡片 audio 播放器对 PCM WAV 重播不稳定：第一次能放，播完再点
经常没声音；MP3 走的是更通用的解码路径。没有 lameenc 时安静回退 WAV，语音
功能本身不能因此失败。
"""

from __future__ import annotations

import io
import wave


def wav_to_mp3(wav: bytes, bitrate: int = 64) -> bytes | None:
    """WAV(PCM 16-bit, mono/stereo) → MP3；转不了返回 None。"""
    if not wav:
        return None
    try:
        with wave.open(io.BytesIO(wav), "rb") as handle:
            channels = int(handle.getnchannels())
            rate = int(handle.getframerate())
            width = int(handle.getsampwidth())
            frames = handle.readframes(handle.getnframes())
        if width != 2 or channels not in (1, 2) or not frames:
            return None
        import lameenc

        encoder = lameenc.Encoder()
        encoder.set_bit_rate(int(bitrate))
        encoder.set_in_sample_rate(rate)
        encoder.set_channels(channels)
        encoder.set_quality(5)
        data = encoder.encode(frames) + encoder.flush()
        return bytes(data) if data else None
    except Exception:
        # 编码器缺失/不支持的采样格式：回退 WAV，别把回复一起拖垮。
        return None


__all__ = ["wav_to_mp3"]
