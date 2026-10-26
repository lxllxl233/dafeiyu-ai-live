# -*- coding: utf-8 -*-
"""主播链路体检 / 自愈：一键回答「弹幕窗还在吗、曼波还说话吗、OBS 那路还活着吗」。

用法：
  python tools/live.py check                 # 体检（默认端口 8730）
  python tools/live.py restart-streamer      # 只重启主播窗（会等心跳回来）
  python tools/live.py watch --interval 30   # 常驻看护：心跳超时自动重启主播窗
  python tools/live.py check --no-tts        # 跳过语音合成测试（省 3 秒）

判据：主播窗每轮询成功都会写 logs/streamer_heartbeat.json（≤2 秒一次）。
心跳超过 --stale 秒（默认 20）没更新 = 窗口卡死，即使进程还在。
"""
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PORT = int(os.environ.get("STS2_OVERLAY_PORT", "8730"))
HEARTBEAT = os.path.join(ROOT, "logs", "streamer_heartbeat.json")

OK = "OK  "
WARN = "WARN"
BAD = "BAD "


def http_json(url, timeout=3.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def http_bytes(url, timeout=60.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def pid_alive(pid):
    if not pid:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def read_heartbeat():
    try:
        with open(HEARTBEAT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def check_overlay():
    try:
        d = http_json(f"http://127.0.0.1:{PORT}/health", timeout=2)
        if d.get("ok"):
            return OK, f"overlay :{PORT} 在跑"
        return WARN, f"overlay :{PORT} 有响应但 ok=False"
    except Exception as e:
        return BAD, f"overlay :{PORT} 连不上（{type(e).__name__}）— 先 python overlay_server.py"


def check_tts():
    text = f"链路体检 {int(time.time()) % 10000}"
    t0 = time.time()
    try:
        wav = http_bytes(f"http://127.0.0.1:{PORT}/tts?text={urllib.parse.quote(text)}", timeout=90)
    except Exception as e:
        return BAD, f"曼波合成失败（{type(e).__name__}: {e}）— 看 MamboTTS 是否在 9880"
    dt = time.time() - t0
    if len(wav) < 2000:
        return BAD, f"曼波返回异常（{len(wav)} 字节）"
    return OK, f"曼波合成 {len(wav) // 1024} KB / {dt:.1f}s"


def check_streamer(stale=20):
    hb = read_heartbeat()
    if not hb:
        return BAD, "没有心跳文件（主播窗没起，或版本太旧）— python tools/live.py restart-streamer"
    age = time.time() - float(hb.get("ts") or 0)
    pid = hb.get("pid")
    if not pid_alive(pid):
        return BAD, f"主播窗进程 {pid} 不在了 — python tools/live.py restart-streamer"
    if age > stale:
        return BAD, f"主播窗进程 {pid} 在，但心跳 {age:.0f}s 没动（卡死）— python tools/live.py restart-streamer"
    return OK, f"主播窗 pid {pid}，心跳 {age:.1f}s 前，seq {hb.get('seq')}"


def check_obs():
    try:
        import obsctl
    except Exception:
        return WARN, "import obsctl 失败，跳过 OBS 检查"
    try:
        cfg = obsctl.load_ws_config()
        o = obsctl.Obs(cfg["url"], cfg.get("password", ""))
        o.connect()
        try:
            items = o.request("GetSceneItemList", {"sceneName": "StS2"})
            names = [i.get("sourceName") for i in (items.get("sceneItems") or [])]
            has = "AI Commentary" in names
            rec = o.request("GetRecordStatus", {})
            o.close()
            state = "录制中" if rec.get("outputActive") else "未录制"
            if not has:
                return WARN, f"OBS 场景里没有 AI Commentary（有：{names}）"
            return OK, f"OBS 源齐全，{state}"
        except Exception:
            o.close()
            raise
    except Exception as e:
        return WARN, f"OBS 检查失败（{type(e).__name__}）— OBS 没开可忽略"


def check_game():
    try:
        s = http_json("http://127.0.0.1:8080/state", timeout=3).get("data") or {}
    except Exception as e:
        return WARN, f"游戏 API 不可达（{type(e).__name__}）"
    run = s.get("run") or {}
    return OK, f"游戏 {s.get('screen')} · 第 {run.get('floor')} 层 · HP {run.get('current_hp')}/{run.get('max_hp')}"


def cmd_check(args):
    rows = [("overlay", check_overlay()), ("tts", (OK, "跳过") if args.no_tts else check_tts()),
            ("streamer", check_streamer(args.stale)), ("obs", check_obs()), ("game", check_game())]
    worst = OK
    for name, (st, msg) in rows:
        print(f"{st} {name:<9} {msg}")
        if st == BAD:
            worst = BAD
        elif st == WARN and worst != BAD:
            worst = WARN
    print("RESULT", "全绿" if worst == OK else ("有警告" if worst == WARN else "需要修"))
    return 0 if worst != BAD else 1


def cmd_restart_streamer(args):
    hb = read_heartbeat()
    old = hb.get("pid")
    if old and pid_alive(old):
        print(f"停掉旧主播窗 pid {old}")
        subprocess.run(["taskkill", "/PID", str(old), "/F"], capture_output=True)
        time.sleep(1.5)
    try:
        os.remove(HEARTBEAT)
    except OSError:
        pass
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    # 一定要用 pythonw：python.exe 是控制台程序，父控制台一关，Tk 窗口跟着退。
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    cmd = [exe, "streamer.py"]
    if args.capture:
        cmd.append("--capture")
    if args.voice:
        cmd += ["--voice", args.voice]
    subprocess.Popen(cmd, cwd=ROOT, close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
    print("已拉起主播窗，等心跳…")
    for _ in range(40):
        time.sleep(0.5)
        hb = read_heartbeat()
        if hb and float(hb.get("ts") or 0) > time.time() - 5:
            print(f"OK   主播窗就绪 pid {hb.get('pid')}")
            return 0
    print("BAD  10 秒内没等到心跳，看看是不是被 sandbox/权限挡住")
    return 1


def cmd_watch(args):
    print(f"看护中：每 {args.interval}s 检查一次（心跳阈值 {args.stale}s）")
    while True:
        st, msg = check_streamer(args.stale)
        stamp = time.strftime("%H:%M:%S")
        if st == BAD and not args.dry_run:
            print(f"[{stamp}] {msg} -> 自动重启")
            cmd_restart_streamer(args)
        elif st == BAD:
            print(f"[{stamp}] {msg}（dry-run 不重启）")
        else:
            print(f"[{stamp}] {msg}")
        time.sleep(args.interval)


def main():
    ap = argparse.ArgumentParser(description="主播链路体检 / 自愈")
    ap.add_argument("--port", type=int, default=PORT)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="体检整条链路")
    p.add_argument("--no-tts", action="store_true")
    p.add_argument("--stale", type=int, default=20)
    p.set_defaults(func=cmd_check)

    for name, fn, hlp in (("restart-streamer", cmd_restart_streamer, "重启主播窗并等心跳"),
                          ("watch", cmd_watch, "常驻看护，心跳卡住自动重启")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("--stale", type=int, default=20)
        p.add_argument("--interval", type=int, default=30)
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--capture", action="store_true", default=True)
        p.add_argument("--voice", default="mambo")
        p.set_defaults(func=fn)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
