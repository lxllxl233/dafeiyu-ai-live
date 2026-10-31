#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大肥鱼直播姬 · 命令行。

    python main.py studio            起 overlay + 直播姬控制台（:8730）
    python main.py kook             KOOK 聊天常驻服务（WebSocket 长连接）
    python main.py demo              离线跑一遍「游戏 → 性格 → 语音 → UI」
    python main.py say --thought "..." --meme "..."
    python main.py status            各层体检（JSON）
    python main.py games             游戏列表
    python main.py game state        读当前游戏状态（默认取 config.json 的 game）
    python main.py game launch       启动当前游戏（Steam / exe）
    python main.py agent run --demo  LangGraph 回合流程（观察/规划/验算/执行）
    python main.py voice test "..."  合成一句试听
    python main.py doctor            环境自检
    python main.py setup             建兼容链接（db/、tools/data/）
    python main.py replay ...        录像回放（按游戏/按局）
    python main.py obs health        OBS 控制（setup/start/stop/health）

不写子命令时 `python main.py` 直接起桌宠；右键菜单可交互。
`tools/` 下旧兼容脚本入口仍可用，但新代码统一走 `main.py`。
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
from dafeiyu.games import GameRegistry, env_value  # noqa: E402
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

    port = int(args.port) if args.port is not None else int(LiveConfig.load().port)
    print(f"{__product__} v{__version__} · studio", flush=True)
    print(f"  overlay : http://127.0.0.1:{port}/", flush=True)
    print(f"  控制台  : http://127.0.0.1:{port}/studio", flush=True)
    main(port=port)
    return 0


def cmd_kook(args) -> int:
    """KOOK 聊天常驻服务；--check 只自检，不进入消息循环。"""
    from dafeiyu.live.kook.service import check, run

    cfg = LiveConfig.load()
    return check(cfg) if getattr(args, "check", False) else run(cfg)


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
                      f"用 `python main.py studio` 起来再跑）", flush=True)
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
    try:
        from dafeiyu.agent.play import status as play_status

        status["play"] = play_status()
    except Exception:
        pass
    try:
        from dafeiyu.agent import director

        status["live"] = director.status()
    except Exception:
        pass
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
    play = status.get("play") or {}
    if play.get("running") or play.get("tokens_total"):
        print(f"  play    {'运行中' if play.get('running') else '停手'}"
              f" · {play.get('mode', '?')} · tok {play.get('tokens_total') or 0}"
              f"（入 {play.get('tokens_in') or 0} / 出 {play.get('tokens_out') or 0}）"
              f" · {'录像中' if play.get('recording') else '未录像'}")
    live = status.get("live") or {}
    if live.get("running"):
        print(f"  live    LLM 直播模式 · {live.get('module_label') or live.get('module') or '—'}"
              f" · 第 {live.get('segments') or 0} 段 · {live.get('reason') or ''}")
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


def cmd_game(args) -> int:
    """读游戏适配器状态 / 启动游戏；默认取 config 的 game。"""
    registry = GameRegistry()
    if args.game:
        from dafeiyu.games.catalog import resolve_game_id

        game_id = resolve_game_id(args.game)
    else:
        game_id = LiveConfig.load().game
    if args.action == "launch":
        from dafeiyu.games.launch import launch_game

        result = launch_game(game_id, registry)
        if args.json:
            _print_json(result)
        else:
            print(("OK  " if result.get("ok") else "ERR ")
                  + str(result.get("message") or result.get("error") or ""))
        return 0 if result.get("ok") else 1
    pack = registry.get(game_id)
    if not pack:
        print(f"ERR 没有这个游戏包：{game_id}")
        return 2
    adapter = registry.create_adapter(game_id)
    if not adapter:
        print(f"ERR 适配器加载失败：{pack.info.adapter}")
        return 2
    status = adapter.status()
    state = status.get("state") or adapter.snapshot().to_dict()
    if args.json:
        _print_json({"status": status, "state": state})
    else:
        print(_game_status_line(state))
        print(f"screen={state.get('screen') or '—'} available={bool(status.get('available'))}")
    return 0 if status.get("available") else 1


def cmd_agent(args) -> int:
    """LangGraph 回合流程：观察 → 规划 → 验算 → 执行 → 反思。"""
    from dafeiyu.agent import (DemoPlanner, DemoTools, LLMPlanner,
                               graph_mermaid, read_runs, run_turn)
    from dafeiyu.core.config import LiveConfig

    action = getattr(args, "action", "run")
    if action == "graph":
        try:
            print(graph_mermaid())
        except Exception as exc:  # noqa: BLE001
            print(f"ERR {exc}；运行 pip install -e .[agent]")
            return 2
        return 0
    if action == "trace":
        rows = read_runs(limit=int(getattr(args, "limit", 10) or 10))
        if getattr(args, "json", False):
            _print_json(rows)
            return 0
        if not rows:
            print("还没有 Agent 轨迹；先跑 python main.py agent run --demo")
            return 0
        for run in rows:
            print(f"{run['id']}  {run['status']:<6s} {run['duration_ms']:>5d}ms  "
                  f"{run['tokens'].get('total', 0):>5d} tok  {run['goal'][:50]}")
            for step in run.get("steps", []):
                print(f"  {step['node']:<9s} {step['status']:<5s} "
                      f"{step.get('duration_ms', 0):>5d}ms  {step.get('summary', '')}")
        return 0

    cfg = LiveConfig.load()
    game = getattr(args, "game", None) or cfg.game
    plan = None
    plan_file = str(getattr(args, "plan", "") or "").strip()
    if plan_file:
        try:
            plan = json.loads(Path(plan_file).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            print(f"ERR 读计划失败：{exc}")
            return 2
        if not isinstance(plan, dict):
            print("ERR 计划必须是 JSON 对象")
            return 2
    demo = bool(getattr(args, "demo", False))
    if demo:
        tools, planner, mode = DemoTools(), (None if plan else DemoPlanner()), "demo"
    else:
        from dafeiyu.games.registry import GameRegistry

        plugin = GameRegistry().load_plugin(game)
        if plugin is None:
            print(f"ERR 找不到游戏插件：{game or '未配置'}")
            return 2
        tools = plugin.tools()
        if tools is None:
            print(f"ERR 游戏插件没有提供决策工具：{game}")
            return 2
        prompt = plugin.prompt("decision")
        if not prompt:
            print(f"ERR 游戏插件没有提供决策提示词：{game}")
            return 2
        planner = None if plan else LLMPlanner(cfg, system_prompt=prompt)
        mode = "live"
        if not plan and not planner.available()[0]:
            print(f"ERR {planner.available()[1]}；也可以 --plan plan.json 或 --demo")
            return 2
    result = run_turn(
        str(getattr(args, "goal", "") or "打一回合：先看局面，再决定打谁"),
        tools=tools, planner=planner, plan=plan, game=game,
        dry_run=not bool(getattr(args, "execute", False)),
        max_attempts=int(getattr(args, "max_attempts", 2) or 2), mode=mode,
    )
    if getattr(args, "json", False):
        _print_json({k: v for k, v in result.items() if k != "context"})
    else:
        print(f"{'OK' if result.get('ok') else 'ERR'} {result.get('run_id')} "
              f"{result.get('status')} · {result.get('error') or '流程完成'}")
        for step in result.get("steps", []):
            print(f"  {step['node']:<9s} {step['status']:<5s} "
                  f"{step.get('duration_ms', 0):>5d}ms  {step.get('summary', '')}")
        print(f"  trace {result.get('trace')}")
    return 0 if result.get("ok") else 1


def cmd_play(args) -> int:
    """让她玩：启动游戏 + 自动回合循环（前台阻塞，Ctrl+C 停）。"""
    import time

    from dafeiyu.agent.play import start_autoplay, status, stop_autoplay

    if getattr(args, "stop", False):
        # 停手要连「LLM 直播模式」一起停，否则导演会在几秒后把模块又开起来。
        try:
            from dafeiyu.agent import director

            director.stop()
        except Exception:
            pass
        result = stop_autoplay()
        print(result.get("message") or "已请求停手")
        return 0
    result = start_autoplay(getattr(args, "game", None),
                            mode=getattr(args, "mode", None),
                            pace=getattr(args, "pace", None))
    print(result.get("message") or result.get("error") or "")
    if not result.get("ok"):
        return 1
    last = None
    started_pid = int((result.get("status") or {}).get("pid") or 0)
    foreign = 0
    try:
        while True:
            now = status()
            if not now.get("running"):
                # 状态文件被别的局/别的进程覆盖（实机：跑测试或第二游戏验收把
                # logs/autoplay.json 写成了另一局、running=false），
                # 但本进程的线程还在打——别跟着退出，否则直播间会被静默停掉。
                # pid=0 是「状态被半写」：本进程线程下一秒会带着 running/pid 写回来，
                # 先多等几轮（以前只等 10 分钟就整条直播退出）。
                stale_pid = int(now.get("pid") or 0)
                if started_pid and stale_pid != started_pid:
                    foreign += 1
                    if foreign < (600 if stale_pid else 3600):
                        time.sleep(1.0)
                        continue
                break
            foreign = 0
            key = (now.get("screen"), now.get("floor"), now.get("hp"), now.get("step"),
                   now.get("last"), now.get("tokens_total"))
            if key != last:
                print(f"[{now.get('screen') or '?'}] f{now.get('floor') or '?'} "
                      f"HP {now.get('hp') or '?'}/{now.get('max_hp') or '?'} "
                      f"step {now.get('step') or 0} · {now.get('last') or ''} "
                      f"· tok {now.get('tokens_total') or 0}")
                last = key
            time.sleep(1.0)
    except KeyboardInterrupt:
        stop_autoplay()
        print("已停手")
    return 0


def cmd_live(args) -> int:
    """LLM 直播模式：模型自己决定播哪个模块，并在模块之间自动切换。"""
    import time

    from dafeiyu.agent import director

    if getattr(args, "stop", False):
        result = director.stop()
        print(result.get("message") or "已停止直播模式")
        return 0
    if getattr(args, "status", False):
        _print_json({"live": director.status(),
                     "modules": director.live_modules()})
        return 0
    ok, why = director.available()
    if not ok:
        print(f"ERR {why}")
        return 2
    items = director.live_modules()
    if not items:
        print("ERR games/ 下没有可直播模块（game.json 需要 live.enabled）")
        return 2
    print("直播模块：" + "、".join(str(x.get("label") or x.get("id"))
                                    for x in items))
    result = director.start(module=getattr(args, "module", None))
    print(result.get("message") or result.get("error") or "")
    if not result.get("ok"):
        return 1
    last = None
    try:
        while True:
            state = director.status()
            if not state.get("running"):
                print(f"直播模式已停：{state.get('error') or state.get('phase') or ''}")
                break
            key = (state.get("module"), state.get("phase"),
                   state.get("segments"), state.get("next_at"))
            if key != last:
                print(f"[{state.get('phase') or '?'}] "
                      f"{state.get('module_label') or '—'} · "
                      f"{state.get('reason') or ''} · "
                      f"第 {state.get('segments') or 0} 段 · "
                      f"tok {state.get('tokens') or 0}")
                last = key
            time.sleep(2.0)
    except KeyboardInterrupt:
        director.stop()
        print("已停止直播模式")
    return 0


def cmd_obs(args) -> int:
    from dafeiyu.recording.obs import main as obs_main

    return int(obs_main([args.action] + list(getattr(args, "rest", []) or [])) or 0)


def cmd_voice(args) -> int:
    cfg = LiveConfig.load()
    router = build_router(cfg)
    if args.action == "status":
        _print_json(router.status())
        return 0
    text = " ".join(args.text) if isinstance(args.text, list) else str(args.text or "")
    if not text:
        print("用法：python main.py voice test \"一句话\" [--voice mambo|animal]")
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

    try:
        from dafeiyu.recording import replay

        rr = replay.scan()
        total = sum(r["size_bytes"] for r in rr)
        add("OK", "replay", f"{len(rr)} 局 · {replay.human_size(total)}")
    except Exception as exc:
        add("WARN", "replay", f"{type(exc).__name__}: {exc}")

    registry = GameRegistry()
    corpus_db = None
    for pack in registry.packs():
        rel = str((pack.manifest.get("corpus") or {}).get("db") or "")
        if rel and pack.root and (pack.root / rel).exists():
            corpus_db = pack.root / rel
            break
    add("OK" if corpus_db else "BAD", "corpus",
        f"{corpus_db.stat().st_size // 1024} KB" if corpus_db else "没有可用的 corpus db")

    cfg = LiveConfig.load()
    adapter = registry.create_adapter(cfg.game)
    try:
        game_ok = bool(adapter and adapter.available())
    except Exception:
        game_ok = False
    add("OK" if game_ok else "WARN", "game",
        f"{cfg.game} 在线" if game_ok else f"{cfg.game} 不在线（不影响离线演示）")

    for level, name, detail in rows:
        print(f"{level:<4s} {name:<8s} {detail}")
    bad = [r for r in rows if r[0] == "BAD"]
    print(f"\n{len(rows)} 项检查，{len(bad)} 个 BAD。")
    return 1 if bad else 0


def cmd_setup(args) -> int:
    """按 game.json 的 links 建兼容链接，让老脚本相对路径继续可用。"""
    pairs = []
    for pack in GameRegistry().packs():
        for rel, target in (pack.manifest.get("links") or {}).items():
            if pack.root:
                pairs.append((paths.ROOT / str(rel), pack.root / str(target)))
    if not pairs:
        print("没有需要创建的兼容链接。")
        return 0
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


def cmd_replay(args) -> int:
    """录像回放：按游戏分目录、一局一个 run.mp4。"""
    from dafeiyu.recording import replay

    action = args.action
    if action == "list":
        rows = replay.scan(args.game)
        if args.json:
            _print_json(rows)
            return 0
        if not rows:
            print("还没有录像。开一局：python main.py replay start")
            return 0
        print(f"{'局':>5}  {'游戏':<18} {'角色':<10} {'进阶':<5} {'结果':<22} {'大小':>9}")
        for r in rows:
            asc = f"A{r['ascension']}" if r["ascension"] is not None else "—"
            print(f"{r['run'] or '—':>5}  {r['game']:<18} {r['character'] or '—':<10} "
                  f"{asc:<5} {(r['result'] or r['note'] or '—'):<22} "
                  f"{replay.human_size(r['size_bytes']):>9}")
        return 0

    if action == "new":
        run_dir = replay.create_run(args.game, args.run, args.character,
                                    args.ascension, args.note)
        print(run_dir)
        return 0

    if action == "start":
        run_dir = Path(args.path).expanduser() if args.path else replay.get_current()
        if not run_dir or not run_dir.is_dir():
            run_dir = replay.create_run(args.game, args.run, args.character,
                                        args.ascension, args.note)
        replay.set_current(run_dir)
        print(f"RUN {run_dir}")
        try:
            replay.start_recording(run_dir)
            print("RECORDING（OBS 已开录；结束后 python main.py replay stop --result ...）")
        except Exception as exc:
            print(f"WARN OBS 没开录（{type(exc).__name__}: {exc}）")
            print("     目录已建好；OBS 恢复后重跑 replay start，或手动把录像放进该目录的 run.mp4")
            return 1
        return 0

    if action == "stop":
        run_dir = Path(args.path).expanduser() if args.path else replay.get_current()
        if not run_dir or not run_dir.is_dir():
            print("ERR 没有正在录的局（先 replay start）")
            return 1
        try:
            dst = replay.stop_recording(run_dir, args.result, args.note)
            print(f"OK {dst}（{replay.human_size(dst.stat().st_size)}）")
        except Exception as exc:
            print(f"WARN 停止 OBS 失败（{type(exc).__name__}: {exc}）")
            if not replay.video_path(run_dir):
                return 1
            try:
                dst = replay.finalize(run_dir, None, args.result, args.note, force=args.force)
            except Exception as exc2:
                print(f"ERR 收尾失败：{exc2}")
                return 1
            print(f"OK 已收尾 {dst}")
        replay.clear_current()
        replay.write_index()
        return 0

    if action == "index":
        p = replay.write_index()
        print(p)
        return 0

    if action == "migrate":
        moved = replay.migrate_legacy(force=args.force)
        print(f"moved {len(moved)}")
        for m in moved:
            print(m)
        replay.write_index()
        return 0

    if action == "path":
        cur = replay.get_current()
        print(cur or "（没有当前局）")
        return 0 if cur else 1

    print(f"unknown replay action: {action}")
    return 2


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
    p.add_argument("--port", type=int, default=None,
                   help="端口（默认用 config.json 的 port；环境变量 DAFEIYU_OVERLAY_PORT 仍优先）")
    p.set_defaults(func=cmd_studio)

    p = sub.add_parser("kook", help="KOOK 聊天常驻服务（Bot + WebSocket）")
    p.add_argument("--check", action="store_true", help="只自检 Token/网关，不进入消息循环")
    p.set_defaults(func=cmd_kook)

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

    p = sub.add_parser("game", help="读游戏状态 / 启动（默认取 config.json 的 game）")
    p.add_argument("action", nargs="?", default="state", choices=["state", "status", "launch"])
    p.add_argument("--game", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_game)

    p = sub.add_parser("agent", help="LangGraph Agent 流程（观察/规划/验算/执行）")
    ag = p.add_subparsers(dest="action", required=True)
    ar = ag.add_parser("run", help="跑一轮图")
    ar.add_argument("--goal", default="打一回合：先看局面，再决定打谁")
    ar.add_argument("--plan", default="", help="外部计划 JSON 文件（不调用 LLM）")
    ar.add_argument("--demo", action="store_true", help="离线演示图，不碰游戏")
    ar.add_argument("--execute", action="store_true", help="真的出牌（默认 dry-run）")
    ar.add_argument("--game", default=None)
    ar.add_argument("--max-attempts", type=int, default=2)
    ar.add_argument("--json", action="store_true")
    ar.set_defaults(func=cmd_agent)
    at = ag.add_parser("trace", help="看最近轨迹")
    at.add_argument("--limit", type=int, default=10)
    at.add_argument("--json", action="store_true")
    at.set_defaults(func=cmd_agent)
    agg = ag.add_parser("graph", help="打印图的 Mermaid")
    agg.set_defaults(func=cmd_agent)

    p = sub.add_parser("play", help="启动游戏并让她自动玩（Ctrl+C 停）")
    p.add_argument("--game", default=None)
    p.add_argument("--mode", choices=["local", "llm"], default=None,
                   help="local=本地算账；llm=战斗回合交给 LLM")
    p.add_argument("--pace", choices=["voice", "normal"], default=None,
                   help="voice=等语音；normal=旧版快节奏")
    p.add_argument("--stop", action="store_true", help="只停手，不启动")
    p.set_defaults(func=cmd_play)

    p = sub.add_parser("live", help="LLM 直播模式：模型决定播哪个模块并自动切换")
    p.add_argument("--stop", action="store_true", help="停止直播模式（连带停手）")
    p.add_argument("--status", action="store_true", help="只打印状态和候选模块")
    p.add_argument("--module", default=None, help="第一段固定用这个模块 id")
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("obs", help="OBS WebSocket 控制")
    p.add_argument("action", choices=["status", "setup", "start", "stop", "shot",
                                      "ticker", "mute", "free", "health", "nomute"])
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_obs)

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

    p = sub.add_parser("replay", help="录像回放：按游戏分目录，一局一个 run.mp4")
    rs = p.add_subparsers(dest="action", required=True)
    q = rs.add_parser("list", help="列出现有录像")
    q.add_argument("--game", default=None)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_replay)

    q = rs.add_parser("new", help="建一局的空目录（不录像）")
    q.add_argument("--game", default=None)
    q.add_argument("--run", type=int, default=None)
    q.add_argument("--character", default="")
    q.add_argument("--ascension", type=int, default=None)
    q.add_argument("--note", default="")
    q.set_defaults(func=cmd_replay)

    q = rs.add_parser("start", help="开录一局（OBS 目录指到该局）")
    q.add_argument("--path", default="", help="指定已有局目录（默认用 / 新建当前局）")
    q.add_argument("--game", default=None)
    q.add_argument("--run", type=int, default=None)
    q.add_argument("--character", default="")
    q.add_argument("--ascension", type=int, default=None)
    q.add_argument("--note", default="")
    q.set_defaults(func=cmd_replay)

    q = rs.add_parser("stop", help="停录并收进 <局>/run.mp4")
    q.add_argument("--path", default="")
    q.add_argument("--result", default="")
    q.add_argument("--note", default="")
    q.add_argument("--force", action="store_true", help="覆盖已有 run.mp4")
    q.set_defaults(func=cmd_replay)

    q = rs.add_parser("index", help="重写 recordings/INDEX.md")
    q.set_defaults(func=cmd_replay)

    q = rs.add_parser("migrate", help="把根目录散放的旧 mp4 收进分游戏目录")
    q.add_argument("--force", action="store_true")
    q.set_defaults(func=cmd_replay)

    q = rs.add_parser("path", help="打印当前局目录")
    q.set_defaults(func=cmd_replay)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
