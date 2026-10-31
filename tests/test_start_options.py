# -*- coding: utf-8 -*-
"""开局设置（角色/难度…）的通用清洗与保存。

核心不认识具体游戏：game.json 声明选项，这里只按声明清洗、写 config.json 的
start_options，游戏包下一局自己读。
"""
from __future__ import annotations

import json

from dafeiyu import paths
from dafeiyu.games import start_options


SPEC = {
    "title": "开局设置",
    "note": "下一局生效",
    "options": [
        {"key": "character", "label": "角色", "type": "choice", "default": "IRONCLAD",
         "choices": [{"value": "IRONCLAD", "label": "铁甲战士"},
                     {"value": "DEFECT", "label": "故障机器人"}]},
        {"key": "ascension", "label": "进阶", "type": "int",
         "default": 10, "min": 0, "max": 20},
        {"key": "hardcore", "label": "硬核", "type": "bool", "default": False},
    ],
}


def test_spec_from_manifest_accepts_only_declared_options():
    assert start_options.spec_from_manifest(None) == {}
    assert start_options.spec_from_manifest({"start": {"options": "x"}}) == {}
    assert start_options.spec_from_manifest(
        {"start": {"options": [{"label": "没 key"}]}}) == {}
    spec = start_options.spec_from_manifest({"start": SPEC})
    assert [opt["key"] for opt in spec["options"]] == [
        "character", "ascension", "hardcore"]


def test_defaults_use_declared_values_and_fallbacks():
    assert start_options.defaults(SPEC) == {
        "character": "IRONCLAD", "ascension": 10, "hardcore": False}
    implicit = {"options": [
        {"key": "mode", "choices": ["a", "b"]},       # 没写 default → 第一个
        {"key": "level", "min": 3},                    # 没写 default → min
    ]}
    assert start_options.defaults(implicit) == {"mode": "a", "level": 3}


def test_normalize_clamps_and_drops_unknown_keys():
    got = start_options.normalize(SPEC, {
        "character": "DEFECT", "ascension": 99, "hardcore": "off",
        "not_declared": "x",
    })
    assert got == {"character": "DEFECT", "ascension": 20, "hardcore": False}
    assert "not_declared" not in got

    got = start_options.normalize(SPEC, {
        "character": "不存在的角色", "ascension": -5, "hardcore": "on"})
    assert got == {"character": "IRONCLAD", "ascension": 0, "hardcore": True}

    # 存成字符串也要能认回来（控制台/手改 config.json 都可能）
    assert start_options.normalize(SPEC, {"ascension": "7"})["ascension"] == 7


def test_store_and_load_roundtrip_in_config_json(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(paths, "CONFIG_FILE", cfg)

    values = start_options.store_values("demo-game", SPEC, {
        "character": "DEFECT", "ascension": 4})
    assert values == {"character": "DEFECT", "ascension": 4, "hardcore": False}
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    assert raw["start_options"]["demo-game"] == values

    # 另一个游戏的值不能被覆盖掉
    start_options.store_values("other-game", SPEC, {"ascension": 1})
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    assert set(raw["start_options"]) == {"demo-game", "other-game"}

    assert start_options.load_values("demo-game", SPEC)["character"] == "DEFECT"
    assert start_options.load_values("other-game", SPEC)["ascension"] == 1
    # 没存过的游戏拿默认值
    assert start_options.load_values("new-game", SPEC)["ascension"] == 10


def test_load_values_ignores_broken_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text("{不是 json", encoding="utf-8")
    monkeypatch.setattr(paths, "CONFIG_FILE", cfg)
    assert start_options.load_values("demo-game", SPEC) == {
        "character": "IRONCLAD", "ascension": 10, "hardcore": False}
