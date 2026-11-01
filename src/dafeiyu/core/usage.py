"""Token 账单合并：自动玩账本 + KOOK 聊天账本 + B站训练账本。

控制台只读，不改账本。三个进程各写各的 JSONL，这里按时间把最近几步拼在
一起，并保留 ``sources`` 让页面能看出钱花在「自动玩」「KOOK 聊天」还是
「B站训练」。
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
                training: dict | None = None, limit: int = 40) -> dict:
    """合并自动玩 / KOOK / B站训练三份账单；返回结构沿用 ``token_usage``。

    ``primary`` 是自动玩（``logs/autoplay.jsonl``），``secondary`` 是 KOOK
    聊天（``logs/kook/usage.jsonl``），``training`` 是 B 站训练
    （``logs/bili_training/usage.jsonl``）。没有文件时都可以是空 dict。
    """
    play = primary if isinstance(primary, dict) else {}
    chat = secondary if isinstance(secondary, dict) else {}
    train = training if isinstance(training, dict) else {}
    limit = max(1, int(limit or 40))

    datasets = [
        (play, "play", "自动玩", 0),
        (chat, "kook", "KOOK 聊天", 1),
    ]
    if train:
        datasets.append((train, "bili_training", "B站训练", 2))

    steps: list[dict] = []
    for data, source, label, _priority in datasets:
        steps.extend(_steps(data, source, label))
    priority = {source: order for _data, source, _label, order in datasets}
    steps.sort(key=lambda row: (float(row.get("at") or 0.0),
                                priority.get(str(row.get("source") or ""), 9)))
    steps = steps[-limit:]

    total = sum(_int(data.get("total")) for data, _s, _l, _p in datasets)
    # 页面显示「累计」用全局账单，不只算当前可见的几十步。
    running = total
    for row in reversed(steps):
        row["cumulative"] = running
        running -= _int(row.get("total"))

    kinds: dict[str, dict] = {}
    for data, _source_name, _label, _priority in datasets:
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
    for data, _source_name, _label, _priority in datasets:
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
    for data, _source_name, _label, _priority in datasets:
        for path in data.get("paths") or [data.get("path")]:
            text = str(path or "")
            if text and text not in paths:
                paths.append(text)

    sources = [_source(data, source, label)
               for data, source, label, _priority in datasets]
    last = _int(steps[-1].get("total")) if steps else max(
        (_int(data.get("last")) for data, _s, _l, _p in datasets), default=0)

    out = {
        "total": total,
        "input": sum(_int(data.get("input")) for data, _s, _l, _p in datasets),
        "output": sum(_int(data.get("output")) for data, _s, _l, _p in datasets),
        "calls": sum(_int(data.get("calls")) for data, _s, _l, _p in datasets),
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
    if train:
        out["training"] = train
    return out


__all__ = ["merge_usage"]
