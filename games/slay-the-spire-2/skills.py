"""游戏知识文档清单：控制台「对战手册」页编辑的白名单。

只列「读了会影响决策/口播」的 Markdown，不列代码（`decision/prompts.py` 的常驻
提示词仍是代码，改它要走提交和 `tools/check_skill.py` 的限额检查）。
限额和 `tools/check_skill.py` 保持一致：OS.md 15000 字节 / 215 行 / 4600 token。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # games/slay-the-spire-2
MAX_BYTES = 40_000                              # 单文件保存上限

_CJK = re.compile(r"[\u3400-\u9fff\uff00-\uffef\u3000-\u303f]")


def est_tokens(text: str) -> int:
    cjk = len(_CJK.findall(text))
    return int(cjk + (len(text) - cjk) / 3.6)


def files() -> list[dict]:
    """可编辑文件：路径相对游戏包，带显示名和限额。"""
    out: list[dict] = []

    def add(rel: str, label: str, limit: dict | None = None) -> None:
        out.append({"name": rel, "label": label, "limit": dict(limit or {})})

    add("decision/knowledge/ironclad/OS.md", "铁甲 · 决策手册（OS.md）",
        {"bytes": 15_000, "lines": 215, "tokens": 4600})
    refs = ROOT / "decision" / "knowledge" / "ironclad" / "references"
    for p in sorted(refs.glob("*.md")):
        add(f"decision/knowledge/ironclad/references/{p.name}", f"铁甲 · {p.stem}")
    defect = ROOT / "decision" / "knowledge" / "defect" / "OS.md"
    if defect.exists():
        add("decision/knowledge/defect/OS.md", "缺陷 · 决策手册（OS.md）")
    for p in sorted((ROOT / "persona").glob("*.md")):
        if p.name == "README.md":
            continue
        add(f"persona/{p.name}", f"主播稿 · {p.name}")
    return out


def path_of(rel: str) -> Path:
    rel = str(rel or "").replace("\\", "/").strip("/")
    if rel not in {f["name"] for f in files()}:
        raise ValueError(f"{rel or '（空）'} 不在可编辑清单里")
    return ROOT / rel


def validate(rel: str, text: str) -> str | None:
    """返回错误文案；None 表示可以保存。"""
    raw = str(text or "").encode("utf-8")
    if len(raw) > MAX_BYTES:
        return f"文件超过 {MAX_BYTES // 1000}KB，不适合在控制台里存"
    lim = next((f["limit"] for f in files() if f["name"] == rel), {})
    if lim.get("bytes") and len(raw) > lim["bytes"]:
        return f"超过 {lim['bytes']} 字节上限（现在 {len(raw)}）"
    lines = str(text or "").count("\n") + (0 if str(text or "").endswith("\n") else 1)
    if lim.get("lines") and lines > lim["lines"]:
        return f"超过 {lim['lines']} 行上限（现在 {lines}）"
    tok = est_tokens(str(text or ""))
    if lim.get("tokens") and tok > lim["tokens"]:
        return f"估算 {tok} token，超过 {lim['tokens']} 上限"
    return None


__all__ = ["ROOT", "MAX_BYTES", "est_tokens", "files", "path_of", "validate"]
