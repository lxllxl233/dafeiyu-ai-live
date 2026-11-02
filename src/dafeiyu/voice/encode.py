"""把 TTS 的 WAV 转成 MP3 再上传。

KOOK 手机端的卡片 audio 播放器对 PCM WAV 重播不稳定：第一次能放，播完再点
经常没声音；MP3 走的是更通用的解码路径。没有 lameenc 时安静回退 WAV，语音
功能本身不能因此失败。
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import wave


def _ffmpeg_mp3(wav: bytes, bitrate: int) -> bytes | None:
    """lameenc 不在时用 ffmpeg；imageio-ffmpeg 自带的可执行文件也认。"""
    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg

            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # noqa: BLE001 - 没有 ffmpeg 就继续回退 WAV
            return None
    if not exe:
        return None
    handle, name = tempfile.mkstemp(prefix="dafeiyu-mp3-", suffix=".wav")
    os.close(handle)
    try:
        with open(name, "wb") as target:
            target.write(wav)
        proc = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-i", name,
             "-vn", "-codec:a", "libmp3lame", "-b:a", f"{int(bitrate)}k",
             "-f", "mp3", "pipe:1"],
            capture_output=True, timeout=120,
        )
        return proc.stdout if proc.returncode == 0 and proc.stdout else None
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


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
        try:
            import lameenc

            encoder = lameenc.Encoder()
            encoder.set_bit_rate(int(bitrate))
            encoder.set_in_sample_rate(rate)
            encoder.set_channels(channels)
            encoder.set_quality(5)
            data = encoder.encode(frames) + encoder.flush()
            if data:
                return bytes(data)
        except Exception:  # noqa: BLE001 - 继续尝试 ffmpeg
            pass
        return _ffmpeg_mp3(wav, bitrate)
    except Exception:
        # 编码器缺失/不支持的采样格式：回退 WAV，别把回复一起拖垮。
        return None


__all__ = ["wav_to_mp3"]
