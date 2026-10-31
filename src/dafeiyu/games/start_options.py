"""游戏开局设置（角色 / 难度…）的通用读写。

游戏包在 ``game.json`` 里声明自己有哪些开局选项，控制台按声明渲染，核心不认识
任何具体游戏：

    "start": {
      "options": [
        {"key": "character", "label": "角色", "type": "choice",
         "default": "HERO_A",
         "choices": [{"value": "HERO_A", "label": "角色甲"},
                     {"value": "HERO_B", "label": "角色乙"}]},
        {"key": "ascension", "label": "难度", "type": "int",
         "default": 10, "min": 0, "max": 20}
      ]
    }

用户选择存在 ``config.json`` 的 ``start_options["<game-id>"]``（不是核心字段，
也不回显到直播画面）。写入前一律按声明清洗：没声明的 key 丢掉、choice 必须在
列表里、int 夹在 min/max 之间；不合法就退回默认值，保证游戏包拿到的一定能用。
"""

from __future__ import annotations

from typing import Any

from ..core.config import LiveConfig


def spec_from_manifest(manifest: dict | None) -> dict:
    """取 ``game.json`` 的 ``start`` 块；没有/写歪了返回空 dict。"""
    raw = manifest if isinstance(manifest, dict) else {}
    spec = raw.get("start")
    if not isinstance(spec, dict):
        return {}
    options = spec.get("options")
    if not isinstance(options, list):
        return {}
    clean = [opt for opt in options if isinstance(opt, dict) and str(opt.get("key") or "").strip()]
    return {**spec, "options": clean} if clean else {}


def _choice_values(opt: dict) -> list[str]:
    out: list[str] = []
    for item in opt.get("choices") or ():
        if isinstance(item, dict):
            value = item.get("value")
        else:
            value = item
        text = str(value).strip() if value is not None else ""
        if text and text not in out:
            out.append(text)
    return out


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def default_of(opt: dict) -> Any:
    """单个选项的默认值：优先声明，其次第一个 choice / min / false。"""
    if opt.get("default") is not None:
        return opt.get("default")
    choices = _choice_values(opt)
    if choices:
        return choices[0]
    if opt.get("min") is not None:
        return _as_int(opt.get("min"), 0)
    return 0 if opt.get("type") == "int" else ""


def defaults(spec: dict) -> dict:
    out: dict = {}
    for opt in (spec or {}).get("options") or ():
        key = str(opt.get("key") or "").strip()
        if key:
            out[key] = default_of(opt)
    return out


def _cast(opt: dict, value: Any) -> Any:
    kind = str(opt.get("type") or "").strip().lower()
    if not kind:
        kind = "choice" if _choice_values(opt) else (
            "int" if (opt.get("min") is not None or opt.get("max") is not None) else "text")
    if kind == "choice":
        choices = _choice_values(opt)
        text = str(value).strip() if value is not None else ""
        return text if text in choices else default_of(opt)
    if kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() not in ("", "0", "false", "no", "off")
        return bool(value)
    if kind == "int":
        number = _as_int(value, _as_int(default_of(opt), 0))
        if opt.get("min") is not None:
            number = max(_as_int(opt.get("min"), number), number)
        if opt.get("max") is not None:
            number = min(_as_int(opt.get("max"), number), number)
        return number
    text = " ".join(str(value if value is not None else "").split())
    return text[:200]


def normalize(spec: dict, raw: dict | None) -> dict:
    """按声明清洗一份用户选择；缺的 key 用默认值补上。"""
    raw = raw if isinstance(raw, dict) else {}
    out: dict = {}
    for opt in (spec or {}).get("options") or ():
        key = str(opt.get("key") or "").strip()
        if key:
            out[key] = _cast(opt, raw.get(key, default_of(opt)))
    return out


def _stored(game_id: str, cfg: LiveConfig) -> dict:
    rows = getattr(cfg, "start_options", None)
    if not isinstance(rows, dict):
        return {}
    row = rows.get(game_id)
    return row if isinstance(row, dict) else {}


def load_values(game_id: str, spec: dict, *, cfg: LiveConfig | None = None) -> dict:
    """当前生效的开局设置：默认值 < config.json 里存的值，再按声明清洗。"""
    cfg = cfg or LiveConfig.load()
    return normalize(spec, {**defaults(spec), **_stored(game_id, cfg)})


def store_values(game_id: str, spec: dict, raw: dict | None,
                 *, cfg: LiveConfig | None = None) -> dict:
    """清洗并写回 ``config.json`` 的 ``start_options[<game-id>]``。"""
    values = normalize(spec, raw)
    cfg = cfg or LiveConfig.load()
    rows = getattr(cfg, "start_options", None)
    merged = dict(rows) if isinstance(rows, dict) else {}
    merged[game_id] = values
    cfg.save_patch({"start_options": merged})
    return values


__all__ = [
    "default_of", "defaults", "load_values", "normalize", "spec_from_manifest",
    "store_values",
]
