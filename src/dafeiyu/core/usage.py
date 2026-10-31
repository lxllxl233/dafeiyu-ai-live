"""Token 账单合并：自动玩账本 + KOOK 聊天账本。

控制台只读，不改账本。两个进程各写各的 JSONL，这里按时间把最近几步拼在
一起，并保留 ``sources`` 让页面能看出钱花在「自动玩」还是「KOOK 聊天」。
"""

from __future__ import annotations


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _steps(data: dict, source: str, label: str) -> list[dict]:
    out: list[dict] = []
    for raw in data.get("steps") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row.setdefault("source", source)
        row.setdefault("source_label", label)
        out.append(row)
    return out


def _source(data: dict, source: str, label: str) -> dict:
    return {
        "source": source,
        "label": str(data.get("label") or label),
        "total": _int(data.get("total")),
        "input": _int(data.get("input")),
        "output": _int(data.get("output")),
        "calls": _int(data.get("calls")),
    }


def merge_usage(primary: dict | None, secondary: dict | None,
                limit: int = 40) -> dict:
    """合并两份账单；返回结构沿用 ``AutoplayHost.token_usage``，另加来源。

    ``primary`` 是自动玩（``logs/autoplay.jsonl``），``secondary`` 是 KOOK
    聊天（``logs/kook/usage.jsonl``）。没有文件时两边都可以是空 dict。
    """
    play = primary if isinstance(primary, dict) else {}
    chat = secondary if isinstance(secondary, dict) else {}
    limit = max(1, int(limit or 40))

    steps = _steps(play, "play", "自动玩") + _steps(chat, "kook", "KOOK 聊天")
    steps.sort(key=lambda row: (float(row.get("at") or 0.0),
                                0 if row.get("source") == "play" else 1))
    steps = steps[-limit:]

    total = _int(play.get("total")) + _int(chat.get("total"))
    # 页面显示「累计」用全局账单，不只算当前可见的几十步。
    running = total
    for row in reversed(steps):
        row["cumulative"] = running
        running -= _int(row.get("total"))

    kinds: dict[str, dict] = {}
    for data in (play, chat):
        for item in data.get("kinds") or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "其它")
            slot = kinds.setdefault(kind, {"kind": kind, "total": 0, "calls": 0})
            slot["total"] += _int(item.get("total"))
            slot["calls"] += _int(item.get("calls"))
    if not kinds:
        for row in steps:
            kind = str(row.get("kind") or "其它")
            slot = kinds.setdefault(kind, {"kind": kind, "total": 0, "calls": 0})
            slot["total"] += _int(row.get("total"))
            slot["calls"] += 1

    runs: dict[str, dict] = {}
    for data in (play, chat):
        for item in data.get("by_run") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("run_id") or "?")
            slot = runs.setdefault(key, {"run_id": key, "total": 0, "calls": 0,
                                         "last_at": 0.0, "note": ""})
            slot["total"] += _int(item.get("total"))
            slot["calls"] += _int(item.get("calls"))
            slot["last_at"] = max(slot["last_at"], float(item.get("last_at") or 0.0))
            slot["note"] = str(item.get("note") or slot["note"])

    paths: list[str] = []
    for data in (play, chat):
        for path in data.get("paths") or [data.get("path")]:
            text = str(path or "")
            if text and text not in paths:
                paths.append(text)

    sources = [_source(play, "play", "自动玩"),
               _source(chat, "kook", "KOOK 聊天")]
    last = _int(steps[-1].get("total")) if steps else (
        _int(play.get("last")) or _int(chat.get("last")))

    return {
        "total": total,
        "input": _int(play.get("input")) + _int(chat.get("input")),
        "output": _int(play.get("output")) + _int(chat.get("output")),
        "calls": _int(play.get("calls")) + _int(chat.get("calls")),
        "last": last,
        "steps": steps,
        "kinds": sorted(kinds.values(), key=lambda item: -item["total"]),
        "by_run": sorted(runs.values(), key=lambda item: -item["last_at"])[:10],
        "sources": sources,
        "paths": paths,
        "path": paths[0] if paths else "",
        "play": play,
        "chat": chat,
    }


__all__ = ["merge_usage"]
