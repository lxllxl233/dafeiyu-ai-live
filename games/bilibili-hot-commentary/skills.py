"""控制台「游戏 → B站云观影」可查看/编辑的文件清单。

只暴露 B 模式自己的人设，不碰全局 ``persona/``，也不列代码/缓存。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PERSONA = ROOT / "persona"


def _norm(rel: str) -> str:
    return str(rel or "").replace("\\", "/").strip("/")


def files() -> list[dict]:
    out: list[dict] = []
    for rel, label, group in (
        ("persona/card.json", "B站人设卡 · 名字/性格/看片偏好", "caster"),
        ("persona/style.md", "B站刷片风格 · 高能/嘴快/边界", "caster"),
        ("persona/README.md", "人设隔离说明 · 可编辑", "notes"),
    ):
        if (ROOT / rel).is_file():
            out.append({"name": rel, "label": label, "group": group, "limit": {}})
    return out


def path_of(rel: str) -> Path:
    clean = _norm(rel)
    if clean not in {item["name"] for item in files()}:
        raise ValueError(f"{clean or '（空）'} 不在 B 站人设可编辑清单里")
    path = (ROOT / clean).resolve()
    if ROOT.resolve() not in path.parents and path != ROOT.resolve():
        raise ValueError("路径越界")
    return path


def validate(rel: str, text: str) -> str | None:
    """保存前只校验结构，不限字数；返回错误文本或 None。"""
    try:
        path = path_of(rel)
    except ValueError as exc:
        return str(exc)
    body = str(text or "")
    if path.name == "card.json":
        try:
            raw = json.loads(body)
        except ValueError as exc:
            return f"JSON 语法错误：{exc}"
        if not isinstance(raw, dict):
            return "人设卡必须是 JSON 对象"
        if not str(raw.get("id") or "").strip():
            return "人设卡缺 id"
        if not str(raw.get("name") or "").strip():
            return "人设卡缺 name"
        for key in ("traits", "quips"):
            value = raw.get(key)
            if value is not None and not (
                    isinstance(value, list)
                    and all(isinstance(x, str) for x in value)):
                return f"{key} 必须是字符串数组"
        watch = raw.get("watch")
        if watch is not None and not isinstance(watch, dict):
            return "watch 必须是对象"
        if isinstance(watch, dict):
            for key in ("keywords", "avoid"):
                value = watch.get(key)
                if value is not None and not (
                        isinstance(value, (list, str))
                        and (isinstance(value, str) or all(isinstance(x, str) for x in value))):
                    return f"watch.{key} 必须是字符串数组或逗号分隔字符串"
            if watch.get("prefer") is not None and not isinstance(watch.get("prefer"), str):
                return "watch.prefer 必须是字符串"
    return None


def save_note(rel: str) -> str:
    if _norm(rel) in ("persona/card.json", "persona/style.md"):
        return "已保存；重启 B 站云观影后加载新的人设提示"
    return "已保存"


def est_tokens(text: str) -> int:
    cjk = sum(1 for ch in str(text or "") if "\u3400" <= ch <= "\u9fff")
    total = len(str(text or ""))
    return int(cjk + max(0, total - cjk) / 3.6)


def materials() -> dict:
    return {"counts": {}, "files": []}


def material_text(rel: str) -> dict:
    raise ValueError("B站云观影没有原始资料库；人设文件请在上面的编辑器查看")


__all__ = [
    "PERSONA", "ROOT", "est_tokens", "files", "material_text", "materials",
    "path_of", "save_note", "validate",
]
