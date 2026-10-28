"""知识库落位自检：项目里不再有 .dsh 技能目录，资料都在游戏包内。"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 这些目录/文件是运行时产物或外部工具，不参与「项目文件」扫描。
SKIP_DIRS = {".git", ".pytest_cache", "__pycache__", "logs", "recordings",
             "backups", "dist", "build"}
SKIP_FILES = {
    # 审计 DSH 会话成本的工具，路径指向 harness 自己的 home（~/.dsh），与本项目无关
    "tools/audit_tokens.py",
    # 本文件自己就写着要查的字符串
    "tests/test_knowledge_layout.py",
}
EXTS = {".md", ".py", ".json", ".txt", ".toml", ".cs", ".ps1", ".cmd", ".jsonl"}

KNOWLEDGE = ROOT / "games" / "slay-the-spire-2" / "decision" / "knowledge"
PERSONA = ROOT / "games" / "slay-the-spire-2" / "persona"


def _iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in EXTS:
            continue
        rel = path.relative_to(ROOT)
        if set(rel.parts) & SKIP_DIRS:
            continue
        yield rel.as_posix(), path


def test_no_dsh_directory():
    assert not (ROOT / ".dsh").exists(), "技能目录应已拆分进项目"
    assert not (ROOT / ".dsh" / "skills").exists()


def test_no_dsh_references_in_project_files():
    """不允许再出现指向 .dsh 技能目录的路径（「以前是」这类叙述可以留）。"""
    pattern = re.compile(r"\.dsh[\\/]")
    offenders = []
    for rel, path in _iter_files():
        if rel in SKIP_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - 读不了就当没引用
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{rel}:{i}:{line.strip()[:60]}")
    assert offenders == [], f"仍有 .dsh 路径引用：{offenders}"


def test_knowledge_files_exist_and_are_not_empty():
    for rel in ("ironclad/OS.md", "ironclad/README.md", "defect/OS.md",
                "ironclad/references/cards.md", "ironclad/references/enemies.md",
                "ironclad/references/lessons.md"):
        p = KNOWLEDGE / rel
        assert p.is_file(), f"缺少 {rel}"
        assert p.stat().st_size > 500, f"{rel} 太小，像占位文件"


def test_persona_files_exist_and_are_not_empty():
    for rel in ("speech.md", "lines.md", "quips.md"):
        p = PERSONA / rel
        assert p.is_file(), f"缺少 persona/{rel}"
        assert p.stat().st_size > 500, f"persona/{rel} 太小"


def test_references_are_flat():
    refs = KNOWLEDGE / "ironclad" / "references"
    assert not (refs / "references").exists(), "references/ 不该再套一层 references/"


def test_runtime_prompt_stays_small():
    text = (ROOT / "games/slay-the-spire-2/decision/prompts.py").read_text(
        encoding="utf-8")
    assert len(text.encode("utf-8")) < 8000
    for field in ("calc.line", "calc.inc", "allow_leftover", "context.acts"):
        assert field in text, f"提示词缺字段 {field}"


def test_runner_has_llm_first_dispatch():
    text = (ROOT / "games/slay-the-spire-2/autoplay/runner.py").read_text(
        encoding="utf-8")
    assert "LLM_SCREENS" in text
    assert "_screen_llm" in text
    assert "NONCOMBAT_PROMPT" in text
    # 兜底路径必须留痕，否则又变成「悄悄降级」
    assert 'llm_fallback' in text


def test_actions_whitelist_has_no_dangerous_actions():
    text = (ROOT / "games/slay-the-spire-2/decision/actions.py").read_text(
        encoding="utf-8")
    body = text.split('"""', 2)[-1]  # 跳过模块 docstring
    for bad in ("save_and_quit", "open_timeline", "discard_potion"):
        assert not re.search(rf'"a":\s*"{bad}"', body), f"{bad} 不该出现在菜单里"


def test_prompts_put_potions_in_seq_not_fx():
    """药水必须写进 seq 的 {"p": 槽位}；旧提示词写成 fx.p，执行器不认、一滴不喝。"""
    text = (ROOT / "games/slay-the-spire-2/decision/prompts.py").read_text(
        encoding="utf-8")
    assert '{"p": 槽位' in text
    assert "fx 只写 e/hp/take" in text
    assert "fx 的 p" not in text
