"""直播姬桌面叠加层：模板渲染、状态文案和选择持久化。"""
from __future__ import annotations

import json

from dafeiyu.ui import stream_overlay as overlay


def _state():
    return {
        "comments": [{"speech": "这波它 21，我最多挡 15。"}],
        "status": {
            "character": "铁甲战士", "floor": 8, "hp": 23, "max_hp": 80,
            "screen": "COMBAT", "last": "第 8 层 · 战斗",
        },
    }


def test_templates_are_selectable_and_have_distinct_labels():
    assert list(overlay.TEMPLATES) == ["bar", "cinema", "chat", "minimal"]
    assert len(set(overlay.TEMPLATES.values())) == 4
    assert overlay.DEFAULT_TEMPLATE == "off"


def test_each_template_renders_at_target_size_with_transparency():
    for name in overlay.TEMPLATES:
        img = overlay.render_overlay(name, _state(), [], 1280, 720)
        assert img.mode == "RGBA"
        assert img.size == (1280, 720)
        assert img.getbbox() is not None
    # 状态条只在左上角有内容；弹幕栏只在右侧；极简字幕只在底部。
    assert overlay.render_overlay("bar", _state(), [], 1280, 720).getpixel((1279, 719))[3] == 0
    assert overlay.render_overlay("chat", _state(), [], 1280, 720).getpixel((5, 5))[3] == 0
    assert overlay.render_overlay("minimal", _state(), [], 1280, 720).getpixel((5, 5))[3] == 0
    assert overlay.render_overlay("cinema", _state(), [], 1280, 720).getpixel((640, 360))[3] == 0


def test_status_line_prefers_video_progress_and_falls_back_to_game_stats():
    video = overlay.status_line({"video_title": "测试视频", "position_s": 65,
                                 "duration_s": 245})
    assert "视频" in video and "01:05" in video and "04:05" in video
    game = overlay.status_line({"character": "铁甲战士", "floor": 8,
                                "hp": 23, "max_hp": 80})
    assert "铁甲战士" in game and "第 8 层" in game and "HP 23/80" in game


def test_template_choice_round_trips(tmp_path):
    path = tmp_path / "overlay.json"
    assert overlay.load_template(path) == "off"
    assert overlay.save_template("cinema", path) == "cinema"
    assert json.loads(path.read_text(encoding="utf-8"))["template"] == "cinema"
    assert overlay.load_template(path) == "cinema"
    assert overlay.save_template("unknown", path) == "off"


def test_read_chats_skips_system_and_keeps_gift_fields(tmp_path):
    path = tmp_path / "events.jsonl"
    rows = [
        {"kind": "system", "user": "系统", "text": "忽略"},
        {"kind": "chat", "user": "观众A", "text": "这手稳"},
        {"kind": "gift", "user": "老板", "gift": "小心心", "count": 3},
        {"kind": "like", "user": "观众B"},
    ]
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows),
                    encoding="utf-8")
    chats = overlay.read_chats(path)
    assert [row["kind"] for row in chats] == ["chat", "gift"]
    assert overlay.chat_line(chats[1]) == ("老板", "送出 小心心 ×3", True)
