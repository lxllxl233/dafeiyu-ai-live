#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""录像回放目录：按游戏分目录，一局一个文件夹，默认一个 ``run.mp4``。

目录约定（根目录 ``recordings/``）::

    recordings/
      README.md                 说明（入库）
      INDEX.md                  自动生成的索引（入库）
      <game-id>/                游戏 id（和 games/registry.json 对齐）
        run-0071/
          run.mp4               一局一个录像
          meta.json             角色 / 进阶 / 结果 / 时长 / 大小
        _legacy/                对不上局号的旧文件

这个模块只做文件与元数据；OBS 的开关在 ``start_recording`` / ``stop_recording``。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import paths
from ..games import catalog

ROOT = paths.ROOT
RECORDINGS = paths.RECORDINGS
CURRENT = RECORDINGS / ".current.json"


def game_id(name: str | None = None) -> str:
    return catalog.resolve_game_id(name)


def game_dir(name: str | None = None) -> Path:
    p = RECORDINGS / game_id(name)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _meta_path(run_dir: Path) -> Path:
    return run_dir / "meta.json"


def read_meta(run_dir: Path) -> dict:
    try:
        d = json.loads(_meta_path(run_dir).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def write_meta(run_dir: Path, **fields) -> dict:
    meta = read_meta(run_dir)
    meta.update({k: v for k, v in fields.items() if v is not None})
    run_dir.mkdir(parents=True, exist_ok=True)
    _meta_path(run_dir).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
    return meta


def run_number(run_dir: Path) -> int | None:
    m = re.search(r"run-(\d+)", run_dir.name)
    if m:
        return int(m.group(1))
    m = re.search(r"run(\d+)", run_dir.name, re.I)
    return int(m.group(1)) if m else None


def runs(game: str | None = None) -> list[Path]:
    """所有「有录像或元数据」的局目录，按局号倒序。"""
    roots = [game_dir(game)] if game else (
        [p for p in RECORDINGS.iterdir() if p.is_dir()] if RECORDINGS.is_dir() else []
    )
    out: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.iterdir():
            if p.is_dir() and not p.name.startswith((".", "_")) and (
                (p / "run.mp4").exists() or _meta_path(p).exists()
            ):
                out.append(p)
    return sorted(out, key=lambda p: (run_number(p) or 0, p.stat().st_mtime), reverse=True)


def next_number(game: str | None = None) -> int:
    nums = [n for p in runs(game_id(game)) if (n := run_number(p)) is not None]
    return (max(nums) + 1) if nums else 1


def create_run(game: str | None = None, number: int | None = None,
               character: str = "", ascension: int | None = None,
               note: str = "") -> Path:
    """建一局的空文件夹（已有就复用），写 meta。"""
    gid = game_id(game)
    no = int(number) if number is not None else next_number(gid)
    run_dir = game_dir(gid) / f"run-{no:04d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    write_meta(run_dir, game=gid, run=no, character=character or "",
               ascension=ascension, note=note or "",
               started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
               status="open", video="run.mp4")
    return run_dir


def set_current(run_dir: Path) -> None:
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    CURRENT.write_text(json.dumps({"path": str(run_dir)}, ensure_ascii=False), encoding="utf-8")


def get_current() -> Path | None:
    try:
        p = Path(json.loads(CURRENT.read_text(encoding="utf-8"))["path"])
        return p if p.is_dir() else None
    except Exception:
        return None


def clear_current() -> None:
    try:
        CURRENT.unlink()
    except OSError:
        pass


def video_path(run_dir: Path) -> Path | None:
    p = run_dir / "run.mp4"
    if p.exists() and p.stat().st_size > 0:
        return p
    hits = sorted(run_dir.glob("*.mp4"), key=lambda q: q.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def finalize(run_dir: Path, output: str | Path | None = None, result: str = "",
             note: str = "", duration: float | None = None,
             force: bool = False) -> Path:
    """把 OBS 输出收进 ``<run>/run.mp4``，并补全 meta。

    默认一局只有一个录像：目标已存在且没有 ``force`` 就报错，不悄悄覆盖。
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    dst = run_dir / "run.mp4"
    src = Path(output) if output else None
    if src and not src.exists():
        src = None
    if src is None:
        src = video_path(run_dir)
    if src is None:
        raise FileNotFoundError(f"没有找到录像文件：{run_dir}")
    if src.resolve() != dst.resolve():
        if dst.exists() and not force:
            raise FileExistsError(f"{dst} 已存在；要替换加 force=True")
        try:
            os.replace(src, dst)          # 同盘瞬间完成
        except OSError:
            shutil.move(str(src), str(dst))
    size = dst.stat().st_size
    fields: dict[str, Any] = {
        "video": "run.mp4",
        "size_bytes": size,
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "done",
    }
    if result:
        fields["result"] = result
    if note:
        fields["note"] = note
    if duration:
        fields["duration_sec"] = round(float(duration), 1)
    write_meta(run_dir, **fields)
    # 一局只留一个视频：把同目录其它的 mp4 挪进 _extra（不默认删）
    for extra in run_dir.glob("*.mp4"):
        if extra.resolve() == dst.resolve():
            continue
        extra_dir = run_dir / "_extra"
        extra_dir.mkdir(exist_ok=True)
        try:
            os.replace(extra, extra_dir / extra.name)
        except OSError:
            pass
    return dst


# ---------------------------------------------------------------- OBS
def _obs():
    from .obs import Obs, load_ws_config
    cfg = load_ws_config()
    return Obs(cfg.get("url", ""), cfg.get("password", "")).connect()


def start_recording(run_dir: Path) -> str:
    """把 OBS 录像目录指到这一局，然后开录。失败会把异常抛给调用方。"""
    obs = _obs()
    try:
        try:
            obs.request("SetRecordDirectory", {"recordDirectory": str(run_dir)})
        except Exception:
            pass        # 老 OBS 没这个请求：录到默认目录，stop 时用 outputPath 收
        obs.start_record()
    finally:
        obs.close()
    write_meta(run_dir, status="recording", obs_dir=str(run_dir))
    return "RECORDING"


def stop_recording(run_dir: Path, result: str = "", note: str = "",
                   wait: float = 1.2) -> Path:
    obs = _obs()
    out = ""
    try:
        resp = obs.stop_record()
        out = str((resp or {}).get("outputPath") or "")
    finally:
        obs.close()
    time.sleep(max(0.0, wait))     # 等 OBS 把文件句柄落盘
    return finalize(run_dir, out or None, result=result, note=note)


def is_recording() -> bool:
    """OBS 现在是否在录；不看本进程有没有 session（跨进程判断用）。"""
    obs = _obs()
    try:
        st = obs.status()
    finally:
        obs.close()
    return bool((st or {}).get("outputActive"))


# ---------------------------------------------------------------- 索引
def scan(game: str | None = None) -> list[dict]:
    rows = []
    for d in runs(game):
        meta = read_meta(d)
        v = video_path(d)
        rows.append({
            "game": meta.get("game") or d.parent.name,
            "run": run_number(d),
            "dir": str(d),
            "character": meta.get("character") or "",
            "ascension": meta.get("ascension"),
            "result": meta.get("result") or "",
            "note": meta.get("note") or "",
            "started_at": meta.get("started_at") or "",
            "size_bytes": (v.stat().st_size if v else 0),
            "video": v.name if v else "",
        })
    return rows


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def render_index() -> str:
    groups: dict[str, list[dict]] = {}
    for row in scan():
        groups.setdefault(row["game"], []).append(row)
    lines = [
        "# 录像回放",
        "",
        "> 一局一个文件夹，默认一个 `run.mp4`；这个文件由 `python main.py replay index` 生成。",
        "> 视频不进 Git（`.gitignore` 只放行本文件和 README）。",
        "",
    ]
    if not groups:
        lines.append("（还没有录像。`python main.py replay new` 开一局。）")
    for gid in sorted(groups):
        lines += [f"## {catalog.game_name(gid)}（`{gid}`）", "",
                  "| 局 | 角色 | 进阶 | 结果 | 大小 | 文件 |",
                  "|---|---|---|---|---|---|"]
        for r in groups[gid]:
            char = r["character"] or "—"
            asc = f"A{r['ascension']}" if r["ascension"] is not None else "—"
            res = r["result"] or r["note"] or "—"
            rel = f"{gid}/run-{r['run']:04d}/{r['video']}" if r["run"] else r["video"]
            lines.append(f"| {r['run'] or '—'} | {char} | {asc} | {res} | "
                         f"{human_size(r['size_bytes'])} | `{rel}` |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_index() -> Path:
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    p = RECORDINGS / "INDEX.md"
    p.write_text(render_index(), encoding="utf-8", newline="\n")
    return p


# ---------------------------------------------------------------- 兼容旧文件
# 旧文件名形如 <game>_<character>_a<ascension>_run<number>_<result>.mp4；
# game 段可以是登记过的短名/别名，由 catalog 解析，核心不认具体游戏。
_LEGACY_RE = re.compile(
    r"^(?P<game>[a-z0-9]+)_(?P<character>[a-z0-9]+)_a(?P<ascension>\d+)"
    r"_run(?P<run>\d+)_(?P<result>.+)\.mp4$",
    re.I,
)


def migrate_legacy(force: bool = False) -> list[str]:
    """把根目录散放的旧 mp4 收进对应游戏的 ``run-NNNN/run.mp4``。"""
    moved: list[str] = []
    if not RECORDINGS.is_dir():
        return moved
    legacy_dir = game_dir() / "_legacy"
    for f in sorted(RECORDINGS.glob("*.mp4")):
        m = _LEGACY_RE.match(f.name)
        if m:
            gid = game_id(m.group("game"))
            no = int(m.group("run"))
            d = game_dir(gid) / f"run-{no:04d}"
            d.mkdir(parents=True, exist_ok=True)
            meta = read_meta(d)
            if not meta:
                write_meta(d, game=gid, run=no,
                           character=m.group("character").lower(),
                           ascension=int(m.group("ascension")),
                           result=m.group("result"), legacy=True,
                           started_at="", status="done")
            dst = d / "run.mp4"
            if dst.exists() and not force:
                continue
            os.replace(f, dst)
            moved.append(str(dst))
        else:
            legacy_dir.mkdir(parents=True, exist_ok=True)
            dst = legacy_dir / f.name
            if dst.exists() and not force:
                continue
            os.replace(f, dst)
            moved.append(str(dst))
    return moved


__all__ = [
    "ROOT", "RECORDINGS", "game_id", "game_dir", "runs", "next_number", "create_run",
    "set_current", "get_current", "clear_current", "video_path", "finalize",
    "start_recording", "stop_recording", "is_recording", "scan", "render_index", "write_index",
    "migrate_legacy", "human_size", "read_meta", "write_meta", "run_number",
]
