"""把仿真世界包成一个 HTTP 桥，方便没有真机时手工验收 ``mc-bridge/v1``。

    python games/minecraft/bridge/server.py --port 8092

然后另开一个终端：

    $env:MINECRAFT_BRIDGE = "http"
    python main.py game state --game minecraft
    python main.py play --game minecraft --mode local

它只是 ``bridge/sim.py`` 的传输壳；真机桥（Fabric 模组）要实现同样的
``GET /state`` / ``POST /act``，见 ``games/minecraft/README.md``。
"""

from __future__ import annotations

import argparse
import json
import sys
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load_sim():
    """按合成包名加载游戏包，脚本直接运行时相对 import 也能成立。"""
    for path in (ROOT / "src", ROOT):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    name = "dafeiyu_game_minecraft"
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str(ROOT / "games" / "minecraft")]
        package.__package__ = name
        sys.modules[name] = package
    import importlib

    return importlib.import_module(f"{name}.bridge.sim")


def make_handler(bridge):
    class Handler(BaseHTTPRequestHandler):
        server_version = "McSimBridge/1"

        def log_message(self, *args) -> None:  # 安静，别刷屏
            return

        def _send(self, payload: dict, status: int = 200) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/state", "/"):
                self._send(bridge.state())
                return
            self._send({"ok": False, "error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/act":
                self._send({"ok": False, "error": "not found"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                self._send({"ok": False, "error": "bad json"}, 400)
                return
            result = bridge.act(payload if isinstance(payload, dict) else {})
            self._send(result, 200 if result.get("ok") else 400)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Minecraft 仿真桥（mc-bridge/v1）")
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--state", default="", help="仿真世界 JSON 路径")
    parser.add_argument("--difficulty", default="normal",
                        choices=("peaceful", "normal", "hard"))
    args = parser.parse_args(argv)

    sim = _load_sim()
    bridge = sim.SimBridge(args.state or None, difficulty=args.difficulty)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(bridge))
    print(f"[mc] sim bridge listening on http://{args.host}:{args.port} "
          f"state={bridge.path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mc] stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
