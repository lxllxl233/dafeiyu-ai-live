from __future__ import annotations

import time


def test_start_mambo_launches_run_engine(tmp_path):
    from dafeiyu.voice.startup import start_mambo

    script = tmp_path / "run_engine.py"
    script.write_text(
        "from pathlib import Path\nPath('started.txt').write_text('ok', encoding='utf-8')\n",
        encoding="utf-8",
    )
    result = start_mambo(str(tmp_path), url="http://127.0.0.1:1")
    assert result["ok"] is True
    assert result["starting"] is True
    marker = tmp_path / "started.txt"
    content = ""
    for _ in range(60):
        try:
            content = marker.read_text(encoding="utf-8")
        except OSError:
            content = ""
        if content == "ok":
            break
        time.sleep(0.05)
    assert content == "ok"


def test_start_mambo_reports_missing_script(tmp_path):
    from dafeiyu.voice.startup import start_mambo

    result = start_mambo(str(tmp_path), url="http://127.0.0.1:1")
    assert result["ok"] is False
    assert "run_engine.py" in result["message"]
