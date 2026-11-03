"""桌宠待机/拖动动画：朝向防抖、弹簧回正、朝向交叉淡入、帧率档位。"""
from __future__ import annotations

import time
from collections import deque
from types import SimpleNamespace

import pytest

from dafeiyu.ui import desktop
from dafeiyu.ui.desktop import PetMotion


def _motion() -> PetMotion:
    m = PetMotion()
    m.tick(0.0)          # 第一帧只校准时间基准
    return m


def _pet(**kwargs):
    base = dict(
        pet_facing="front", pet_face_right=False, pet_energy=0.0, pet_lean=0.0,
        talk_start_at=0.0, talk_until=0.0, celebrate_start=0.0, celebrate_until=0.0,
        enter_at=0.0, bubble_until=0.0, room_bubbles=deque(maxlen=8),
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_horizontal_drag_turns_side_after_dwell():
    m = _motion()
    m.on_move(8, 1, 0.01, 1.0)
    assert m.facing == "front", "朝向要等 DWELL 再换，不能一个鼠标事件就切"
    assert m.lean_target < 0, "向右拖，鱼身应该向左倾"
    assert m.energy_target > 0.5

    m.tick(0.06)
    assert m.facing == "front"
    m.tick(0.09)
    assert m.facing == "side" and m.face_right is True
    assert m.face_prev == ("front", False)
    assert m.face_mix_at == pytest.approx(0.09)

    m.tick(0.5)
    assert m.lean < -5.0, "倾斜应该真的追上目标，而不是只记在目标里"


def test_quick_direction_change_waits_for_dwell():
    m = _motion()
    m.on_move(8, 0, 0.01, 1.0)
    m.tick(0.09)
    assert m.facing == "side" and m.face_right is True

    m.on_move(-8, 0, 0.10, 1.0)
    m.tick(0.12)
    assert m.face_right is True, "刚反向 20ms，不该立刻镜像"
    m.tick(0.18)
    assert m.facing == "side" and m.face_right is False
    assert m.face_prev == ("side", True), "换向要留下上一张图给渲染层淡入"


def test_vertical_drag_switches_back_and_front():
    m = _motion()
    m.on_move(8, 0, 0.01, 1.0)
    m.tick(0.09)
    m.on_move(1, -8, 0.10, 1.0)
    m.tick(0.18)
    assert m.facing == "back"
    m.on_move(1, 8, 0.20, 1.0)
    m.tick(0.28)
    assert m.facing == "front"
    assert abs(m.lean_target) <= m.TILT_Y_MAX


def test_release_springs_back_with_a_little_bounce():
    m = _motion()
    t = 0.0
    m.on_move(8, 0, 0.001, 1.0)
    t = 0.001
    for _ in range(25):            # 按住一会儿，让倾斜追到目标
        t += 1 / 60
        m.tick(t)
    assert m.lean < -5.0
    m.on_release(t)
    vals = []
    for _ in range(60):
        t += 1 / 60
        m.tick(t)
        vals.append(m.lean)
    assert max(vals) > 0.2, "松手回正应该轻轻过冲，而不是生硬地停在 0"
    assert abs(m.lean) < 0.1
    assert m.energy < 0.02
    assert not m.active(t)


def test_holding_still_relaxes_but_keeps_facing():
    m = _motion()
    m.on_move(8, 0, 0.01, 1.0)
    m.tick(0.09)
    t = 0.09
    for _ in range(20):
        t += 1 / 60
        m.tick(t)
    m.relax()
    for _ in range(60):
        t += 1 / 60
        m.tick(t)
    assert m.facing == "side", "按住不动不该把朝向收回去"
    assert abs(m.lean) < 0.1
    assert m.energy < 0.02


def test_larger_scale_tilts_less_per_pixel():
    small, big = _motion(), _motion()
    small.on_move(6, 0, 0.01, 0.5)
    big.on_move(6, 0, 0.01, 1.8)
    assert abs(big.lean_target) < abs(small.lean_target)


def test_frame_rate_idle_vs_drag():
    win = desktop.PetWindow.__new__(desktop.PetWindow)
    win.motion = PetMotion()
    win.motion.tick(0.0)
    win._dragging = False
    win._drag_until = win.talk_until = win.celebrate_until = 0.0
    assert win._frame_interval(10.0) == pytest.approx(1 / desktop.PET_IDLE_FPS)
    win.motion.energy = 0.5
    assert win._frame_interval(10.0) == pytest.approx(1 / desktop.PET_DRAG_FPS)
    win.motion.energy = 0.0
    win._dragging = True
    assert win._frame_interval(10.0) == pytest.approx(1 / desktop.PET_DRAG_FPS)


def test_render_pet_crossfades_between_facings():
    now = time.perf_counter()
    plain = desktop.render_pet("", "idle", 1.0, now, _pet(pet_facing="side"))
    fading = desktop.render_pet(
        "", "idle", 1.0, now,
        _pet(pet_facing="side", pet_face_prev=("front", False),
             pet_face_mix_at=now - desktop.PET_FACE_BLEND / 2),
    )
    done = desktop.render_pet(
        "", "idle", 1.0, now,
        _pet(pet_facing="side", pet_face_prev=("front", False),
             pet_face_mix_at=now - desktop.PET_FACE_BLEND - 0.01),
    )
    assert fading.tobytes() != plain.tobytes(), "淡入中途应该混着上一张图"
    assert done.tobytes() == plain.tobytes(), "淡入结束后必须回到纯当前朝向"


def test_pet_idle_actions_are_bounded_and_decay():
    idle = desktop.PetIdle(seed=1)
    idle.start("blink", 10.0)
    assert idle.active(10.0) is True
    assert idle.blink_amount(10.08) == pytest.approx(1.0, abs=0.01)
    assert idle.blink_amount(10.16) == pytest.approx(0.0, abs=0.01)

    idle.start("hop", 20.0)
    assert idle.hop_offset(20.275, 1.0) == pytest.approx(-10.0, abs=0.05)
    assert idle.until == pytest.approx(20.55)

    idle.start("tilt", 30.0)
    assert abs(idle.tilt_angle(30.45)) == pytest.approx(2.4, abs=0.05)

    idle.start("wiggle", 40.0)
    assert abs(idle.sway_offset(40.09, 1.0)) > 1.0
    assert idle.active(40.55) is False


def test_render_pet_blinks_without_mutating_sprite():
    before = desktop.load_pet_sprite("front", 300).tobytes()
    now = time.perf_counter()
    idle = desktop.PetIdle(seed=1)
    idle.start("blink", now - 0.08)       # 正中间：眼睛闭合
    blinked = desktop.render_pet("", "idle", 1.0, now, _pet(pet_idle=idle))
    plain = desktop.render_pet("", "idle", 1.0, now, _pet())
    diff = sum(1 for a, b in zip(blinked.tobytes(), plain.tobytes()) if a != b)
    assert diff > 500, "眨眼没有改到眼睛区域"
    assert desktop.load_pet_sprite("front", 300).tobytes() == before


def test_tap_reaction_and_double_tap_upgrade():
    win = desktop.PetWindow.__new__(desktop.PetWindow)
    win.pet_idle = desktop.PetIdle(seed=2)
    win.pet_react_start = 0.0
    win.pet_react_until = 0.0
    win.pet_react_kind = ""
    win.pet_react_text = ""
    win._last_tap_at = 0.0
    win.dirty = False

    win._on_tap(10.0)
    assert win.pet_react_kind == "pat"
    assert win.pet_react_text in desktop.PET_PAT_LINES
    assert win.pet_react_until > 10.0 and win.dirty is True

    win._on_tap(10.2)
    assert win.pet_react_kind == "double"
    assert win.pet_react_text in desktop.PET_DOUBLE_LINES

    win._on_tap(11.0)
    assert win.pet_react_kind == "pat"


def test_pointer_up_only_taps_when_short_and_still(monkeypatch):
    monkeypatch.setattr(desktop.user32, "ReleaseCapture", lambda: 0)
    win = desktop.PetWindow.__new__(desktop.PetWindow)
    win.motion = _motion()
    win._dragging = True
    win._release_at = 0.0
    win._drag_started_at = 10.0
    win._drag_moved = 0.0
    win._save_pos = lambda: None
    win.pet_idle = desktop.PetIdle(seed=3)
    win.pet_react_start = win.pet_react_until = 0.0
    win.pet_react_kind = win.pet_react_text = ""
    win._last_tap_at = 0.0
    win.dirty = False

    win._end_drag(10.2)
    assert win.pet_react_kind == "pat", "短按不动应该算摸摸头"

    win._dragging = True
    win._drag_started_at = 11.0
    win._drag_moved = 12.0
    win.pet_react_kind = ""
    win._end_drag(11.2)
    assert win.pet_react_kind == "", "真的拖动过就不该触发摸摸头"


def test_reaction_text_only_wins_over_idle():
    win = desktop.PetWindow.__new__(desktop.PetWindow)
    win.status = {}
    win.text = ""
    win.pet_react_text = "唔…再摸一下。"
    win.pet_react_until = time.perf_counter() + 5.0
    assert win.display_text("idle") == "唔…再摸一下。"
    assert win.display_text("thinking") == desktop.THINKING_TEXT
    win.pet_react_until = 0.0
    assert win.display_text("idle") == desktop.IDLE_TEXT


def test_render_pet_does_not_mutate_cached_sprites():
    before = desktop.load_pet_sprite("side", 300).tobytes()
    now = time.perf_counter()
    desktop.render_pet(
        "", "idle", 1.0, now,
        _pet(pet_facing="side", pet_face_right=True, pet_face_prev=("front", False),
             pet_face_mix_at=now - 0.05),
    )
    assert desktop.load_pet_sprite("side", 300).tobytes() == before
