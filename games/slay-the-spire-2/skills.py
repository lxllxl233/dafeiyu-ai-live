"""游戏知识文档清单：控制台「游戏详情」页编辑的白名单。

只列「读了会影响决策/口播」的文档，不列代码（`decision/prompts.py` 的常驻提示词
仍是代码，改它要走提交和 `tools/check_skill.py` 的检查）。
保存不限字数：手册可以写长，`tools/check_skill.py` 的 15000 字节 / 215 行 / 4600
token 只作为「建议长度」提示，不作为保存门槛。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # games/slay-the-spire-2

_CJK = re.compile(r"[\u3400-\u9fff\uff00-\uffef\u3000-\u303f]")


def est_tokens(text: str) -> int:
    cjk = len(_CJK.findall(text))
    return int(cjk + (len(text) - cjk) / 3.6)


def files() -> list[dict]:
    """可编辑文件：路径相对游戏包，带显示名和分组。"""
    out: list[dict] = []

    def add(rel: str, label: str, group: str) -> None:
        out.append({"name": rel, "label": label, "group": group, "limit": {}})

    add("decision/knowledge/ironclad/OS.md", "铁甲 · 决策手册（OS.md）", "strategy")
    refs = ROOT / "decision" / "knowledge" / "ironclad" / "references"
    for p in sorted(refs.glob("*.md")):
        add(f"decision/knowledge/ironclad/references/{p.name}",
            f"资料笔记 · {p.stem}", "notes")
    defect = ROOT / "decision" / "knowledge" / "defect" / "OS.md"
    if defect.exists():
        add("decision/knowledge/defect/OS.md", "缺陷 · 决策手册（OS.md）", "strategy")
    for p in sorted((ROOT / "persona").glob("*.md")):
        if p.name == "README.md":
            continue
        add(f"persona/{p.name}", f"主播稿 · {p.name}", "caster")
    return out


MATERIAL_DIR = "corpus/data/eng"
_MATERIAL_LIMIT = 20_000       # 预览截断（原始 JSON 动辄几百 KB）


def materials() -> dict:
    """游戏原始资料（只读）：卡/遗物/敌人/药水等 JSON 的条数与文件。"""
    data = ROOT / "corpus" / "data" / "eng"
    counts: dict[str, int] = {}
    for key, fn in (("cards", "cards.json"), ("relics", "relics.json"),
                    ("monsters", "monsters.json"), ("potions", "potions.json"),
                    ("events", "events.json"), ("encounters", "encounters.json"),
                    ("powers", "powers.json"), ("characters", "characters.json")):
        p = data / fn
        if not p.is_file():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            counts[key] = len(raw) if hasattr(raw, "__len__") else 0
        except (OSError, ValueError):
            continue
    files = []
    for p in sorted(data.glob("*.json")):
        files.append({"name": f"{MATERIAL_DIR}/{p.name}", "label": p.stem,
                      "bytes": p.stat().st_size})
    return {"counts": counts, "files": files}


def material_text(rel: str, limit: int = _MATERIAL_LIMIT) -> dict:
    """读一份原始资料（只读预览，超长截断）。"""
    rel = str(rel or "").replace("\\", "/").strip("/")
    if not rel.startswith(MATERIAL_DIR + "/") or rel not in {
            f["name"] for f in materials()["files"]}:
        raise ValueError(f"{rel or '（空）'} 不是可查看的游戏资料")
    p = ROOT / rel
    text = p.read_text(encoding="utf-8")
    return {"ok": True, "name": rel, "bytes": len(text.encode("utf-8")),
            "text": text[:limit], "truncated": len(text) > limit}


def path_of(rel: str) -> Path:
    rel = str(rel or "").replace("\\", "/").strip("/")
    if rel not in {f["name"] for f in files()}:
        raise ValueError(f"{rel or '（空）'} 不在可编辑清单里")
    return ROOT / rel


def validate(rel: str, text: str) -> str | None:
    """返回错误文案；None 表示可以保存。只校验路径白名单，不限字数。"""
    try:
        path_of(rel)
    except ValueError as exc:
        return str(exc)
    return None


__all__ = ["ROOT", "est_tokens", "files", "path_of", "validate",
           "materials", "material_text"]
