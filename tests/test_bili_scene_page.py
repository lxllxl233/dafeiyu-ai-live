"""B 站云观影直播场景页：独立的透明浏览器源，不依赖 Studio 大 payload。"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "ui" / "overlay" / "bili.html").read_text(encoding="utf-8")


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key == "id" and value:
                self.ids.append(value)


def _ids():
    p = _Ids()
    p.feed(HTML)
    return p.ids


def test_scene_page_has_required_blocks():
    ids = set(_ids())
    assert {"brand", "side", "state", "nowTitle", "bar", "chat", "next",
            "petWrap", "bubble", "bubbleText", "footerState"}.issubset(ids)


def test_scene_page_uses_lightweight_api_and_tts():
    assert "/api/bilibili/scene" in HTML
    assert "/state?after=" in HTML
    assert "/tts?text=" in HTML
    assert "AI 生成" in HTML
    assert "直播弹幕" in HTML


def test_scene_page_claims_live_audio_owner():
    # 页面必须把自己登记为 live 音频主，否则桌宠窗口会同时播 TTS，直播里就是重音。
    assert "/events?role=live" in HTML
    assert "/mark?ev=audio&role=live" in HTML
    assert "claimLiveAudio" in HTML


def test_scene_page_boosts_tts_without_clipping():
    # 曼波 TTS 平均音量低、峰值高：页面必须用增益 + 限幅，不能只把 src 接到 destination。
    assert "TTS_GAIN" in HTML
    assert "TTS_MAKEUP" in HTML
    assert "createGain" in HTML
    assert "createDynamicsCompressor" in HTML
    assert "src.connect(ttsOutput(ctx))" in HTML
    assert "src.connect(ctx.destination)" not in HTML


def test_scene_page_only_plays_audio_inside_obs():
    # 普通浏览器/直播姬内置浏览器里的 /bili 只显示字幕，避免多出第二路 TTS。
    assert "IS_OBS" in HTML
    assert "navigator.userAgent" in HTML
    assert "if (!IS_OBS" in HTML


def test_scene_page_does_not_use_unsafe_inner_html_for_room_text():
    # 场景页的动态弹幕必须走 textContent；保留 innerHTML 只用于静态/空的场景。
    assert "chatEl.innerHTML" not in HTML
    assert "bubble.innerHTML" not in HTML
