#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大肥鱼直播姬 · 命令行。

    python dafeiyu.py studio            起 overlay + 直播姬控制台（:8730）
    python dafeiyu.py demo              离线跑一遍「游戏 → 性格 → 语音 → UI」
    python dafeiyu.py say --thought "..." --meme "..."
    python dafeiyu.py status            各层体检（JSON）
    python dafeiyu.py games             游戏列表
    python dafeiyu.py voice test "..."  合成一句试听
    python dafeiyu.py doctor            环境自检
    python dafeiyu.py setup             建兼容链接（db/、tools/data/）

仓库根的同名入口（sts2.py / streamer.py / overlay_server.py / obsctl.py）是兼容 shim，
老命令继续可用。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许 `python src/dafeiyu/cli.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dafeiyu import __product__, __version__  # noqa: E402
from dafeiyu import paths  # noqa: E402
from dafeiyu.core.config import LiveConfig  # noqa: E402
from dafeiyu.core.session import LiveSession  # noqa: E402
from dafeiyu.games import GameRegistry  # noqa: E402
from dafeiyu.persona import Persona  # noqa: E402
from dafeiyu.voice import build_router  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _session(args) -> LiveSession:
    cfg = LiveConfig.load()
    if getattr(args, "voice", None):
        cfg.voice = args.voice
    if getattr(args, "no_push", False):
        cfg.push_overlay = False
    session = LiveSession(cfg)
    return session


def _game_status_line(state: dict) -> str:
    parts = []
    if state.get("character"):
        parts.append(str(state["character"]))
    if state.get("ascension") is not None:
        parts.append(f"A{state['ascension']}")
    if state.get("floor") is not None:
        parts.append(f"F{state['floor']}")
    if state.get("hp") is not None:
        parts.append(f"HP {state['hp']}/{state.get('max_hp') or '?'}")
    if state.get("screen"):
        parts.append(str(state["screen"]))
    return " · ".join(parts) or "—"


# ---------------------------------------------------------------- 命令
def cmd_studio(args) -> int:
    from dafeiyu.ui.overlay_server import main

    print(f"{__product__} v{__version__} · studio", flush=True)
    print(f"  overlay : http://127.0.0.1:{args.port}/", flush=True)
    print(f"  控制台  : http://127.0.0.1:{args.port}/studio", flush=True)
    main(port=args.port)
    return 0


def cmd_demo(args) -> int:
    from dafeiyu.games.demo import DemoAdapter

    cfg = LiveConfig.load()
    cfg.voice = args.voice
    cfg.push_overlay = not args.no_push
    adapter = DemoAdapter(delay=args.delay)
    session = LiveSession(cfg).start(adapter)
    try:
        first = True
        for step in adapter.scenario:
            session.update_state(step["state"])
            print(f"  [{_game_status_line(step['state'])}]", flush=True)
            thought, meme = step.get("say", ("", ""))
            res = session.say(thought, meme, source="demo", wait_voice=False)
            print(f"  大肥鱼> {res.text}", flush=True)
            if res.pushed.get("ok") is False and first:
                print(f"  （overlay 没连上：{res.pushed.get('error')}；"
                      f"用 `python dafeiyu.py studio` 起来再跑）", flush=True)
                first = False
            if args.once:
                break
            import time
            time.sleep(max(0.0, float(step.get("delay", 0.0))))
        session.wait_idle(60)
        print("  语音队列已排空。" if session._voice_ok or session._voice_fail else "  无配音。", flush=True)
        return 0
    finally:
        session.stop(drain=False)


def cmd_say(args) -> int:
    session = _session(args).start()
    try:
        res = session.say(args.thought or "", args.meme or "", text=args.text or "",
                          source="cli", wait_voice=False)
        session.wait_idle(90)
        if args.json:
            _print_json(res.to_dict())
        else:
            print(f"大肥鱼> {res.text}")
            lint = res.lint or {}
            print(f"  lint  {'PASS' if lint.get('ok') else 'FAIL'} "
                  f"({lint.get('fails', 0)} fail / {lint.get('warns', 0)} warn)")
            for issue in lint.get("issues", []):
                print(f"        {issue['level']}  {issue['message']}")
            push = res.pushed or {}
            print(f"  push  {'ok id=' + str(push.get('id')) if push.get('ok') else '离线/' + str(push.get('error') or '跳过')}")
            if session._last_voice:
                v = session._last_voice
                print(f"  voice {v.get('voice') or '-'} {v.get('duration', 0)}s"
                      f"{'（缓存）' if v.get('cached') else ''}")
        return 0 if res.lint.get("ok", True) else 2
    finally:
        session.stop(drain=False)


def cmd_status(args) -> int:
    cfg = LiveConfig.load()
    registry = GameRegistry()
    session = LiveSession(cfg)
    pack = registry.get(cfg.game)
    if pack:
        adapter = registry.create_adapter(cfg.game)
        if adapter is not None:
            session.attach(adapter)
    status = session.status()
    status["product"] = {"name": __product__, "version": __version__, "root": str(paths.ROOT)}
    status["games"] = [p.info.to_dict() for p in registry.packs()]
    if args.json:
        _print_json(status)
        return 0
    print(f"{__product__} v{__version__}")
    print(f"  repo    {paths.ROOT}")
    print(f"  persona {status['persona']['name']}（{status['persona']['id']}）· {status['persona']['voice']}")
    voice = status["voice"]
    print(f"  voice   首选 {voice['primary']} · 回退 {voice['fallback']} · 当前 {voice['active']}")
    for name, info in voice["voices"].items():
        print(f"          {name:6s} {'在线' if info.get('available') else '离线'}"
              f"  {info.get('engine', '')}")
    pub = status["publisher"]
    print(f"  overlay {'在线' if pub.get('ok') else '离线'}"
          f"{' · clients=' + str(pub.get('clients')) if pub.get('ok') else ' · ' + str(pub.get('error', ''))}")
    game = status["game"]
    if game.get("adapter"):
        print(f"  game    {game['adapter']} · {_game_status_line(game.get('state') or {})}")
    else:
        print(f"  game    {cfg.game}（未连接）")
    return 0


def cmd_games(args) -> int:
    registry = GameRegistry()
    rows = []
    for pack in registry.packs():
        adapter = registry.create_adapter(pack.info.id)
        try:
            live = bool(adapter and adapter.available())
        except Exception:
            live = False
        rows.append({**pack.info.to_dict(), "connected": live})
    if args.json:
        _print_json(rows)
        return 0
    print(f"游戏列表（{len(rows)}）")
    for row in rows:
        flag = "● 在线" if row["connected"] else "○ 离线"
        print(f"  {flag}  {row['name']:<10s} {row['id']:<18s} {row['platform']}")
        if row.get("notes"):
            print(f"          {row['notes']}")
    return 0


def cmd_voice(args) -> int:
    cfg = LiveConfig.load()
    router = build_router(cfg)
    if args.action == "status":
        _print_json(router.status())
        return 0
    text = " ".join(args.text) if isinstance(args.text, list) else str(args.text or "")
    if not text:
        print("用法：python dafeiyu.py voice test \"一句话\" [--voice mambo|animal]")
        return 2
    result = router.speak(text, args.voice)
    if not result.ok:
        print(f"合成失败：{result.error}")
        return 1
    out = Path(args.out or (paths.LOGS / "voice_test.wav"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(result.wav)
    print(json.dumps({"ok": True, "voice": result.voice, "cached": result.cached,
                      "bytes": len(result.wav), "duration": round(result.duration, 3),
                      "file": str(out)}, ensure_ascii=False))
    return 0


def cmd_doctor(args) -> int:
    rows: list[tuple[str, str, str]] = []

    def add(level, name, detail):
        rows.append((level, name, detail))

    add("OK" if sys.version_info >= (3, 10) else "BAD", "python",
        f"{sys.version.split()[0]}（需要 ≥3.10）")
    add("OK" if (paths.PERSONA / "dafeiyu" / "card.json").exists() else "BAD",
        "persona", "大肥鱼人设卡" if (paths.PERSONA / "dafeiyu" / "card.json").exists() else "缺 card.json")
    add("OK" if (paths.UI / "overlay" / "index.html").exists() else "BAD",
        "ui", "OBS 浏览器源页面" if (paths.UI / "overlay" / "index.html").exists() else "缺 ui/overlay/index.html")

    try:
        AnimalTester = build_router(LiveConfig.load()).voices["animal"]
        wav = AnimalTester.synth("测试 123").wav
        add("OK" if wav else "BAD", "animal", f"{len(wav or b'')} 字节" if wav else "合成失败")
    except Exception as exc:
        add("BAD", "animal", f"{type(exc).__name__}: {exc}")

    mambo = build_router(LiveConfig.load()).voices["mambo"]
    online = mambo.available()
    add("OK" if online else "WARN", "mambo",
        f"引擎在线 {mambo.url}" if online else f"引擎离线（回退动物语）{mambo.url}")

    pub = LiveSession(LiveConfig.load()).publisher
    ping = pub.ping()
    add("OK" if ping.get("ok") else "WARN", "overlay",
        f"clients={ping.get('clients')} seq={ping.get('seq')}" if ping.get("ok")
        else f"离线（{ping.get('error')}）")

    db = paths.GAMES / "slay-the-spire-2" / "corpus" / "db" / "sts2.db"
    add("OK" if db.exists() else "BAD", "corpus", f"{db.stat().st_size // 1024} KB" if db.exists() else f"缺 {db}")

    registry = GameRegistry()
    adapter = registry.create_adapter("slay-the-spire-2")
    try:
        sts2_ok = bool(adapter and adapter.available())
    except Exception:
        sts2_ok = False
    add("OK" if sts2_ok else "WARN", "sts2", "mod :8080 在线" if sts2_ok
        else "游戏/mod 不在线（不影响离线演示）")

    for level, name, detail in rows:
        print(f"{level:<4s} {name:<8s} {detail}")
    bad = [r for r in rows if r[0] == "BAD"]
    print(f"\n{len(rows)} 项检查，{len(bad)} 个 BAD。")
    return 1 if bad else 0


def cmd_setup(args) -> int:
    """建兼容链接：让老脚本继续用 db/ 和 tools/data/ 的相对路径。"""
    pairs = [
        (paths.ROOT / "db", paths.ROOT / "games" / "slay-the-spire-2" / "corpus" / "db"),
        (paths.TOOLS / "data", paths.ROOT / "games" / "slay-the-spire-2" / "corpus" / "data"),
    ]
    ok = True
    for link, target in pairs:
        shown = os.path.relpath(link, paths.ROOT)
        if link.exists() or link.is_symlink():
            print(f"SKIP  {shown} 已存在（{target.name}）")
            continue
        if not target.exists():
            print(f"BAD   目标不存在：{target}")
            ok = False
            continue
        try:
            if os.name == "nt":
                r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    raise OSError((r.stderr or r.stdout).strip())
            else:
                os.symlink(target, link, target_is_directory=True)
            print(f"OK    {shown} -> {os.path.relpath(target, paths.ROOT)}")
        except Exception as exc:
            print(f"BAD   {shown}：{exc}")
            ok = False
    paths.ensure_dirs()
    print(f"logs  {paths.LOGS}")
    return 0 if ok else 1


# ---------------------------------------------------------------- 入口
def build_parser() -> argparse.ArgumentParser:
    from dafeiyu import __codename__

    ap = argparse.ArgumentParser(
        prog="dafeiyu",
        description=f"{__product__}（{__codename__}）· AI 直播工具",
    )
    ap.add_argument("--version", action="version", version=f"{__product__} {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("studio", help="起 overlay + 直播姬控制台")
    p.add_argument("--port", type=int, default=int(os.environ.get("STS2_OVERLAY_PORT", "8730")))
    p.set_defaults(func=cmd_studio)

    p = sub.add_parser("demo", help="离线演示整条链路（不需要游戏）")
    p.add_argument("--voice", default="animal", choices=["mambo", "animal", "off"])
    p.add_argument("--delay", type=float, default=0.0, help="每步额外停顿（秒）")
    p.add_argument("--no-push", action="store_true", help="不推 overlay（只走性格+语音）")
    p.add_argument("--once", action="store_true", help="只跑第一步")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("say", help="说一句（拼句 + lint + 推 overlay + 配音）")
    p.add_argument("--thought", default="")
    p.add_argument("--meme", default="")
    p.add_argument("--text", default="", help="整句直接发（跳过拼句，仍过 lint）")
    p.add_argument("--voice", default=None, choices=["mambo", "animal", "off"])
    p.add_argument("--no-push", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_say)

    p = sub.add_parser("status", help="各层状态")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("games", help="游戏列表")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_games)

    p = sub.add_parser("voice", help="语音层")
    vs = p.add_subparsers(dest="action", required=True)
    t = vs.add_parser("test", help="合成一句")
    t.add_argument("text", nargs="+")
    t.add_argument("--voice", default=None, choices=["mambo", "animal"])
    t.add_argument("--out", default="")
    t.set_defaults(func=cmd_voice)
    s = vs.add_parser("status", help="语音层状态")
    s.set_defaults(func=cmd_voice, text=[])

    p = sub.add_parser("doctor", help="环境自检")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("setup", help="建 db/、tools/data/ 兼容链接")
    p.set_defaults(func=cmd_setup)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
