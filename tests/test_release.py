"""发布元数据门禁：版本串和发布文件必须保持同步。"""

from __future__ import annotations

from pathlib import Path

from tools import release_check

ROOT = Path(__file__).resolve().parents[1]


def test_release_versions_are_synced():
    info = release_check.version_info()
    assert info["expected"] == "2.5.0"
    assert info["package"] == info["expected"]
    assert info["pyproject"] == info["expected"]
    assert info["game"] == info["expected"]
    assert info["readme"] == "2.5"
    assert "2.5.0" in info["changelog"]
    assert release_check.version_issues(info) == []


def test_release_assets_exist():
    for rel in (
        "CHANGELOG.md",
        "docs/RELEASE.md",
        "tools/README.md",
        "tools/release_check.py",
        ".github/workflows/ci.yml",
        ".gitattributes",
    ):
        assert (ROOT / rel).is_file(), rel
