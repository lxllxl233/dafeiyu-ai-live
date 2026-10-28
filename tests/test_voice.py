from __future__ import annotations

import io
import wave

from dafeiyu.voice import AnimalVoice, MamboVoice, VoiceRouter
from dafeiyu.voice.base import is_wav, wav_duration, wav_gain


def _peak(wav: bytes) -> int:
    with wave.open(io.BytesIO(wav), "rb") as w:
        frames = w.readframes(w.getnframes())
    return max(abs(int.from_bytes(frames[i:i + 2], "little", signed=True))
               for i in range(0, len(frames) - 1, 2))


def test_animal_synth_produces_valid_wav():
    voice = AnimalVoice(seed=7)
    result = voice.synth("它 21，我最多挡 15。")
    assert result.ok and result.voice == "animal"
    assert is_wav(result.wav)
    assert result.duration > 0.2
    assert _peak(result.wav) > 1000


def test_animal_ignores_punctuation_only():
    assert AnimalVoice().synth("……，。！").wav is None


def test_wav_gain_scales_samples():
    voice = AnimalVoice(seed=3)
    wav = voice.synth("挡满再说").wav
    quiet = wav_gain(wav, 0.5)
    assert _peak(quiet) < _peak(wav)
    assert abs(wav_duration(quiet) - wav_duration(wav)) < 0.01


def test_mambo_offline_returns_error(tmp_path):
    voice = MamboVoice(url="http://127.0.0.1:1", timeout=0.3, cache_dir=tmp_path)
    assert voice.available() is False
    result = voice.synth("它 21")
    assert not result.ok
    assert "引擎无响应" in result.error
    assert list(tmp_path.glob("*.wav")) == []


def test_router_falls_back_to_animal(tmp_path):
    router = VoiceRouter(
        {
            "mambo": MamboVoice(url="http://127.0.0.1:1", timeout=0.2, cache_dir=tmp_path),
            "animal": AnimalVoice(seed=1),
        },
        primary="mambo",
        fallback="animal",
    )
    result = router.speak("差 6，嗨呀。")
    assert result.ok and result.voice == "animal"
    status = router.status()
    assert status["counts"]["fallbacks"] == 1
    assert status["voices"]["animal"]["available"] is True


def test_router_off_is_silent():
    router = VoiceRouter({"animal": AnimalVoice()})
    result = router.speak("随便", voice="off")
    assert not result.ok
    assert "关闭" in result.error
