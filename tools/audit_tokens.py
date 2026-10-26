#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计「一局爬塔到底把 token 花在哪」——直接读 DSH 的 session 日志，不猜。

日志位置：%DSH_HOME%/sessions/--<工作区 slug>--/session-*/session.v4.jsonl.zstd
每条 assistant/message 带 usage（inputTokens / cacheReadTokens / outputTokens），
每条 tool/result 带完整回显文本。所以能算清三件事：

  1. 请求数、每次请求的上下文体积（= 每回合被重复计费的量）
  2. 各命令被调了多少次、回显多少字符（谁在推高上下文）
  3. skill 到底被加载了几次、占多少

用法：
    python tools/audit_tokens.py                # 全部 sts2 会话汇总
    python tools/audit_tokens.py --per-command  # 再加命令明细
    python tools/audit_tokens.py --top 15       # 单条最大回显（找整篇读文档的）
    python tools/audit_tokens.py --session 9365 # 只看某个会话（id 前缀）
    python tools/audit_tokens.py --json         # 机读输出
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict

try:
    import zstandard
except ImportError:  # pragma: no cover
    print("需要 zstandard：pip install zstandard")
    raise SystemExit(2)

CJK = re.compile(r"[\u3400-\u9fff\uff00-\uffef\u3000-\u303f]")


def est_tokens(text: str) -> int:
    """粗估 token：中文约 1 token/字，ASCII 约 1 token/3.6 字符。

    只用来说明量级（±15%），不替代真实 tokenizer。
    实测校准：本仓库 SKILL.md 与 sts2.py 回显的字符/token 比都落在这个区间。
    """
    cjk = len(CJK.findall(text))
    other = len(text) - cjk
    return int(cjk + other / 3.6)


def sessions_dir() -> str:
    home = os.environ.get("DSH_HOME") or os.path.expanduser("~/.dsh")
    return os.path.join(home, "sessions")


def find_logs(session_filter: str | None = None):
    root = sessions_dir()
    out = []
    for path in glob.glob(os.path.join(root, "**", "session.v4.jsonl.zstd"), recursive=True):
        slug = os.path.basename(os.path.dirname(os.path.dirname(path)))
        if "sljt" not in slug.lower():
            continue
        sid = os.path.basename(os.path.dirname(path))
        if session_filter and session_filter not in sid:
            continue
        out.append(path)
    return sorted(out, key=os.path.getmtime)


def classify(name: str, args: str) -> str:
    """把一次工具调用归到「花 token 的桶」里。"""
    if name == "skill":
        try:
            sk = json.loads(args).get("name", "?")
        except Exception:
            sk = "?"
        return f"skill:{sk}"
    if name == "read":
        try:
            fp = json.loads(args).get("file_path", "?")
        except Exception:
            fp = "?"
        base = fp.replace("\\", "/").rstrip("/").split("/")[-1]
        return f"read:{base}"
    m = re.search(r"(sts2\.py|obsctl\.py)\s+([a-z_]+)", args or "")
    if m:
        return f"{m.group(1)} {m.group(2)}"
    if name == "pwsh":
        return "pwsh"
    return name or "?"


def scan(path: str):
    d = zstandard.ZstdDecompressor()
    with open(path, "rb") as f:
        raw = d.stream_reader(f).read().decode("utf-8", "replace")
    calls = {}
    row = {
        "id": os.path.basename(os.path.dirname(path))[:16],
        "requests": 0,
        "input": 0,
        "cache_read": 0,
        "output": 0,
        "total": 0,
        "ctx": [],
    }
    per_cmd = defaultdict(lambda: [0, 0, 0])  # calls, chars, est_tokens
    singles = []
    for line in raw.splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        kind = ev.get("type")
        data = ev.get("data") or {}
        if kind == "assistant/message":
            usage = data.get("usage") or {}
            if not usage:
                continue
            row["requests"] += 1
            row["input"] += usage.get("inputTokens", 0)
            row["cache_read"] += usage.get("cacheReadTokens", 0)
            row["output"] += usage.get("outputTokens", 0)
            row["total"] += usage.get("totalTokens", 0)
            row["ctx"].append(usage.get("inputTokens", 0) + usage.get("cacheReadTokens", 0))
        elif kind == "tool/call":
            calls[data.get("callId")] = (data.get("name"), data.get("arguments") or "")
        elif kind == "tool/result":
            msg = data.get("message") or {}
            cid = msg.get("toolCallId") or (msg.get("source") or {}).get("callId")
            name, args = calls.get(cid, ("?", ""))
            text = "".join(b.get("text", "") for b in (msg.get("content") or []) if b.get("type") == "text")
            bucket = classify(name, args)
            per_cmd[bucket][0] += 1
            per_cmd[bucket][1] += len(text)
            per_cmd[bucket][2] += est_tokens(text)
            singles.append((len(text), bucket, args[:110].replace("\n", " ")))
    row["per_cmd"] = per_cmd
    row["singles"] = singles
    return row


def fmt(n: int) -> str:
    return f"{n:,}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-command", action="store_true")
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--session")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    paths = find_logs(a.session)
    if not paths:
        print("没找到 sts2 会话日志（检查 DSH_HOME）")
        return 1

    rows = [scan(p) for p in paths]
    rows = [r for r in rows if r["requests"]]
    rows.sort(key=lambda r: -r["total"])

    if a.json:
        out = [
            {
                "id": r["id"],
                "requests": r["requests"],
                "input": r["input"],
                "cache_read": r["cache_read"],
                "output": r["output"],
                "total": r["total"],
                "mean_ctx": (sum(r["ctx"]) // len(r["ctx"])) if r["ctx"] else 0,
                "max_ctx": max(r["ctx"]) if r["ctx"] else 0,
                "commands": {k: {"calls": v[0], "chars": v[1]} for k, v in r["per_cmd"].items()},
            }
            for r in rows
        ]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    tot_req = tot_tok = tot_out = tot_inp = tot_cr = 0
    print(f"{'session':17}{'请求':>7}{'输入':>12}{'缓存读':>15}{'输出':>10}{'计费合计':>15}{'均上下文':>11}{'峰值':>10}")
    for r in rows:
        mean_ctx = sum(r["ctx"]) // len(r["ctx"])
        print(
            f"{r['id']:17}{r['requests']:7,}{fmt(r['input']):>12}{fmt(r['cache_read']):>15}"
            f"{fmt(r['output']):>10}{fmt(r['total']):>15}{fmt(mean_ctx):>11}{fmt(max(r['ctx'])):>10}"
        )
        tot_req += r["requests"]
        tot_tok += r["total"]
        tot_out += r["output"]
        tot_inp += r["input"]
        tot_cr += r["cache_read"]
    print(
        f"{'TOTAL':17}{tot_req:7,}{fmt(tot_inp):>12}{fmt(tot_cr):>15}"
        f"{fmt(tot_out):>10}{fmt(tot_tok):>15}{fmt(tot_tok // max(1, tot_req)):>11}"
    )
    print(
        f"\n平均每次请求上下文 {fmt(tot_tok // max(1, tot_req))} token"
        f"（输入 {fmt(tot_inp // max(1, tot_req))} + 缓存重读 {fmt(tot_cr // max(1, tot_req))}）"
    )
    print(f"平均每次请求输出 {fmt(tot_out // max(1, tot_req))} token")

    if a.per_command:
        agg = defaultdict(lambda: [0, 0, 0])
        for r in rows:
            for k, v in r["per_cmd"].items():
                for i in range(3):
                    agg[k][i] += v[i]
        print("\n按命令（全部会话）：")
        print(f"  {'calls':>6}  {'chars':>11}  {'tok≈':>9}  {'平均tok':>8}  命令")
        for k, (c, ch, tk) in sorted(agg.items(), key=lambda x: -x[1][2])[:20]:
            print(f"  {c:6,}  {fmt(ch):>11}  {fmt(tk):>9}  {tk // max(1, c):8,}  {k}")

    if a.top:
        singles = [s for r in rows for s in r["singles"]]
        print(f"\n单条最大回显 top {a.top}：")
        for ch, bucket, snippet in sorted(singles, reverse=True)[: a.top]:
            print(f"  {fmt(ch):>9} ch  ~{fmt(ch // 2):>8} tok  {bucket:20} {snippet[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
