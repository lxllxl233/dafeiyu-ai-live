#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌宠设置页（``/settings``）：页面入口、读写接口和「重启/退出」。

设置不另存一份状态：这里读写的始终是 :class:`LiveConfig`（``config.json``），
和桌宠、直播姬控制台共用同一份配置。页面打开时 GET 到的、保存时 POST 回去的、
控制台下一次轮询看到的，都是同一份；所以不存在「界面改了、后台还是旧值」。

只有端口这类在进程启动时绑定过一次的值需要重启才生效——保存接口在
``restart_required`` 里点名，页面据此点亮「重启桌宠」。

页面本身是 ``ui/settings/index.html``（深色、分组、无外链），由
``overlay_server`` 在同一个端口上提供；桌宠右键「⚙️ 设置」用 Edge 的应用窗口
（无地址栏）打开，没有 Edge 就退回默认浏览器。
"""
from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

from .. import __codename__, __product__, __version__, paths
from ..core.config import LiveConfig

PAGE = paths.UI / "settings" / "index.html"

# 这些值在进程/服务启动时读一次，保存后必须重启才生效。
RESTART_FIELDS = ("host", "port", "tts_url")

# 设置页可写的布尔字段（其余字段见 save() 里的显式校验）。
_BOOL_FIELDS = (
    "push_overlay",
    "strict_lint",
    "pet_topmost",
    "pet_click_through",
    "pet_exclude_capture",
    "pet_muted",
    "pet_status_bar",
    "recording_enabled",
    "bilibili_enabled",
    "kook_enabled",
    "kook_memory",
    "kook_pet_say",
    "kook_allow_voice",
    "kook_stickers",
    "kook_proactive",
    "persona_live",
)

# 纯字符串字段（长度在 _clean_text 里统一截断）。
_TEXT_FIELDS = (
    "llm_provider",
    "llm_base_url",
    "llm_model",
    "autoplay_llm_model",
    "llm_vision_model",
    "mambo_dir",
    "kook_persona",
)

_ENUM_FIELDS = {
    "voice": ("mambo", "animal", "off"),
    "autoplay_mode": ("local", "llm"),
    "autoplay_pace": ("voice", "normal"),
}

_SECRET_FIELDS = {
    "llm_api_key": "clear_api_key",
    "bilibili_cookie": "clear_bilibili_cookie",
    "kook_bot_token": "clear_kook_token",
}

# 环境变量优先级比 config.json 高：被环境变量压住的字段在页面上标出来，
# 免得用户改了保存、重启后发现还是旧值。
_ENV_HINTS = {
    "port": ("DAFEIYU_OVERLAY_PORT", "DAFEIYU_PORT"),
    "tts_url": ("DAFEIYU_TTS_URL",),
    "voice": ("DAFEIYU_VOICE",),
    "llm_model": ("DAFEIYU_LLM_MODEL",),
    "llm_api_key": ("DAFEIYU_LLM_API_KEY",),
}

_BOOT_AT = time.time()
_HOOKS: dict = {}
_HOOK_LOCK = threading.Lock()
# 页面每 5s 轮询一次；曼波引擎探测要连一次 /control，缓存 3 秒别把轮询拖慢。
_VOICE_CACHE: dict = {"at": 0.0, "value": {"mambo_online": False}}


# ---------------------------------------------------------------- 小工具
def page_path() -> Path:
    return PAGE


def default_port(cfg: LiveConfig | None = None) -> int:
    """控制台/叠加层端口；配置坏掉时退回 8730。"""
    try:
        cfg = cfg or LiveConfig.load()
        port = int(cfg.port or 0)
    except Exception:
        return 8730
    return port if 1 <= port <= 65535 else 8730


def port_of(url: str, fallback: int = 0) -> int:
    """从 http://127.0.0.1:9880 里取端口；取不到用 fallback。"""
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
        if parsed.port:
            return int(parsed.port)
    except ValueError:
        pass
    return int(fallback or 0)


def tts_url_for(port: int, current: str = "") -> str:
    """只换端口，保留原来的主机名和协议。"""
    scheme, host = "http", "127.0.0.1"
    try:
        parsed = urllib.parse.urlsplit(str(current or ""))
        scheme = parsed.scheme or scheme
        host = parsed.hostname or host
    except ValueError:
        pass
    return f"{scheme}://{host}:{int(port)}"


def config_rev() -> str:
    """配置版本（mtime + 大小）：页面靠它发现「后台已经变了」。"""
    try:
        st = os.stat(paths.CONFIG_FILE)
        return f"{st.st_mtime_ns}-{st.st_size}"
    except OSError:
        return ""


def env_overrides() -> dict:
    out: dict = {}
    for field, names in _ENV_HINTS.items():
        for name in names:
            if os.environ.get(name):
                out[field] = name
                break
    return out


def game_options() -> list[dict]:
    try:
        from ..games.registry import GameRegistry

        return [{"id": p.info.id, "name": p.info.name} for p in GameRegistry().packs()]
    except Exception:
        return []


def overlay_state() -> dict:
    try:
        from .stream_overlay import TEMPLATES, load_template

        return {"template": load_template(),
                "options": [{"key": key, "label": label}
                            for key, label in TEMPLATES.items()]}
    except Exception as exc:  # noqa: BLE001 - 叠加层坏了不能拖垮设置页
        return {"template": "off", "options": [], "error": f"{type(exc).__name__}: {exc}"}


def game_api() -> dict:
    """当前游戏包声明的桥接地址（只读展示：端口归游戏包自己管）。"""
    out = {"game": "", "name": "", "api": ""}
    try:
        from ..games.registry import GameRegistry

        cfg = LiveConfig.load()
        out["game"] = cfg.game
        pack = GameRegistry().get(cfg.game)
        if pack is not None:
            out["name"] = pack.info.name
            manifest = pack.manifest or {}
            api = (manifest.get("capabilities") or {}).get("api") or ""
            out["api"] = str(api)
    except Exception:
        pass
    return out


def voice_state() -> dict:
    """曼波引擎在不在线（只探一下 /control，不拉起进程）；3 秒缓存。"""
    now = time.time()
    if now - float(_VOICE_CACHE["at"]) < 3.0:
        return dict(_VOICE_CACHE["value"])
    try:
        from ..voice.startup import engine_online

        out = {"mambo_online": bool(engine_online(LiveConfig.load().tts_url, timeout=0.8))}
    except Exception:
        out = {"mambo_online": False}
    _VOICE_CACHE.update(at=now, value=dict(out))
    return out


def memory_state() -> dict:
    """KOOK 聊天记忆库统计（只读；打不开就返回 available=False）。"""
    try:
        from ..live.chat_memory import Memory

        memory = Memory()
        try:
            stats = memory.stats()
        finally:
            memory.close()
        return {"available": True, **stats}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}


def persona_state(cfg: LiveConfig) -> dict:
    """三层性格：个人（常驻）/ 直播 / 游戏；带预览文本。"""
    try:
        from ..persona.layers import PersonaStack

        stack = PersonaStack.load(cfg.persona, game_id=cfg.game,
                                  live=bool(cfg.persona_live), include_inactive=True)
        out = stack.to_dict()
        out["preview"] = stack.prompt()[:4000]
        out["live_enabled"] = bool(cfg.persona_live)
        out["chat_persona"] = cfg.kook_persona
        return out
    except Exception as exc:  # noqa: BLE001
        return {"persona": cfg.persona, "layers": [], "preview": "",
                "live_enabled": bool(cfg.persona_live), "error": f"{type(exc).__name__}: {exc}"}


def sticker_state() -> list[dict]:
    try:
        from ..persona import stickers as stickers_mod

        items = stickers_mod.options()
        for row in items:
            row["url"] = f"/stickers/{row.get('file') or ''}"
        return items
    except Exception:
        return []


def autostart_state() -> dict:
    try:
        from ..core import autostart

        return autostart.status()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "enabled": False,
                "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------- 读
def payload() -> dict:
    """页面要的全部状态：配置（脱敏）+ 端口 + 模板 + 运行信息。"""
    cfg = LiveConfig.load()
    data = cfg.to_dict()
    data["tts_port"] = port_of(cfg.tts_url, 9880)
    data["config_rev"] = config_rev()
    data["config_path"] = paths.rel(paths.CONFIG_FILE)
    data["env_overrides"] = env_overrides()
    data["overlay"] = overlay_state()
    data["games"] = game_options()
    data["game_api"] = game_api()
    data["logs_path"] = paths.rel(paths.LOGS)
    data["persona_layers"] = persona_state(cfg)
    data["stickers"] = sticker_state()
    data["memory"] = {**memory_state(), "enabled": bool(cfg.kook_memory)} if cfg.kook_memory else {
        "available": False, "enabled": False}
    data["autostart"] = autostart_state()
    # 显示真实端口：CLI --port / 环境变量可能覆盖 config.json，重启后要跳到
    # 真正在跑的那个地址，不能拿配置里的旧端口糊弄。
    try:
        from .overlay_server import bound_port

        port = bound_port() or default_port(cfg)
    except Exception:
        port = default_port(cfg)
    host = cfg.host or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    data["server"] = {
        "pid": os.getpid(),
        "uptime": int(max(0.0, time.time() - _BOOT_AT)),
        "port": port,
        "url": f"http://{host}:{port}",
    }
    data.update(voice_state())
    return {
        "ok": True,
        "product": {
            "name": __product__,
            "version": __version__,
            "codename": __codename__,
        },
        "config": data,
    }


# ---------------------------------------------------------------- 写
def _number(value, label: str, low: float, high: float) -> float:
    try:
        num = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{label}需要数字") from None
    if num < low or num > high:
        raise ValueError(f"{label}要在 {low:g}–{high:g} 之间")
    return num


def _as_bool(value, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{label}需要 true / false")


def _clean_text(value, label: str, limit: int = 300) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise ValueError(f"{label}太长了（最多 {limit} 字）")
    return text


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.35):
            return True
    except OSError:
        return False


def save(body: dict) -> dict:
    """把页面上改过的字段合并进 config.json；返回新状态 + 需要重启的字段。"""
    if not isinstance(body, dict):
        raise ValueError("请求体必须是 JSON 对象")
    cfg = LiveConfig.load()
    before = {name: getattr(cfg, name) for name in RESTART_FIELDS}
    patch: dict = {}

    for field in _BOOL_FIELDS:
        if field in body:
            patch[field] = _as_bool(body[field], field)

    for field in _TEXT_FIELDS:
        if field in body:
            patch[field] = _clean_text(body[field], field)

    if not patch.get("llm_base_url", "http://x").startswith(("http://", "https://")):
        raise ValueError("接口地址需要以 http:// 或 https:// 开头")
    for field in ("llm_model", "autoplay_llm_model"):
        if field in patch and not patch[field]:
            raise ValueError("模型名不能为空")

    for field, choices in _ENUM_FIELDS.items():
        if field in body:
            value = str(body[field] or "").strip().lower()
            if value not in choices:
                raise ValueError(f"{field} 只能是 {' / '.join(choices)}")
            patch[field] = value

    if "game" in body:
        game_id = str(body["game"] or "").strip()
        known = {row["id"] for row in game_options()}
        if game_id and game_id not in known:
            raise ValueError("未知游戏")
        patch["game"] = game_id

    if "volume" in body:
        patch["volume"] = _number(body["volume"], "音量", 0.0, 1.5)
    if "min_gap" in body:
        patch["min_gap"] = _number(body["min_gap"], "最短间隔", 0.2, 10.0)
    if "tts_speed" in body:
        patch["tts_speed"] = _number(body["tts_speed"], "语速", 0.5, 2.0)
    if "tts_timeout" in body:
        patch["tts_timeout"] = _number(body["tts_timeout"], "超时", 5.0, 180.0)
    if "bilibili_room_id" in body:
        patch["bilibili_room_id"] = int(_number(
            body["bilibili_room_id"], "B 站房间号", 0, 10 ** 12))
    for field, (label, low, high) in {
        "kook_context_turns": ("聊天上下文条数", 4, 40),
        "kook_summary_trigger": ("记忆压缩阈值", 8, 80),
        "kook_proactive_minutes": ("主动消息间隔", 10, 720),
    }.items():
        if field in body:
            patch[field] = int(_number(body[field], label, low, high))

    # 端口：port = 控制台/叠加层，tts_port = 曼波引擎；tts_url 保留给高级用户。
    if "port" in body:
        patch["port"] = int(_number(body["port"], "控制台端口", 1, 65535))
        patch["overlay_url"] = f"http://{cfg.host or '127.0.0.1'}:{patch['port']}"
    if "tts_port" in body:
        patch["tts_url"] = tts_url_for(
            int(_number(body["tts_port"], "配音端口", 1, 65535)), cfg.tts_url)
    if "tts_url" in body:
        url = _clean_text(body["tts_url"], "配音地址")
        if not url.startswith(("http://", "https://")):
            raise ValueError("配音地址需要以 http:// 或 https:// 开头")
        patch["tts_url"] = url

    final_port = int(patch.get("port", cfg.port))
    final_tts = port_of(patch.get("tts_url", cfg.tts_url), 9880)
    if final_port == final_tts:
        raise ValueError("控制台端口和配音端口不能是同一个")
    if "port" in patch and patch["port"] != int(cfg.port) and _port_open(patch["port"]):
        raise ValueError(f"端口 {patch['port']} 已经被占用，换一个再保存")

    # 密钥类：只看「有没有新值」，*** 是页面回显占位符，空 = 保持原值。
    for field, clear_flag in _SECRET_FIELDS.items():
        value = str(body.get(field) or "").strip()
        if value and value != "***":
            if len(value) > 4096:
                raise ValueError(f"{field} 太长了")
            patch[field] = value
        if body.get(clear_flag):
            patch[field] = ""

    if "llm_models" in body and isinstance(body["llm_models"], list):
        models = [str(x).strip() for x in body["llm_models"] if str(x).strip()]
        patch["llm_models"] = models[:12]

    # 换了对话模型就顺手写进模型列表，右键/控制台的模型下拉才有它。
    for field in ("llm_model", "autoplay_llm_model"):
        model = patch.get(field)
        if not model:
            continue
        models = [str(m).strip() for m in (cfg.llm_models or []) if str(m).strip()]
        if model not in models:
            models.insert(0, model)
        patch["llm_models"] = models[:12]

    # 叠加层模板走 stream_overlay 自己的状态文件（桌宠主循环 1.5s 内跟上）。
    overlay_key = None
    if "overlay_template" in body:
        from .stream_overlay import TEMPLATES, save_template

        key = str(body["overlay_template"] or "off").strip().lower()
        if key != "off" and key not in TEMPLATES:
            raise ValueError("未知叠加层模板")
        overlay_key = save_template(key)

    # 开机自启动是注册表副作用：前面都校验通过后再写，失败就别保存配置。
    if "pet_autostart" in body:
        want = _as_bool(body["pet_autostart"], "开机自启动")
        try:
            from ..core import autostart

            result = autostart.set_enabled(want)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"设置开机自启动失败：{type(exc).__name__}: {exc}") from None
        if not result.get("ok"):
            raise ValueError(result.get("error") or "设置开机自启动失败")
        patch["pet_autostart"] = want

    if patch:
        cfg = LiveConfig.patch(patch)
    changed = sorted(patch)
    restart_required = [name for name in RESTART_FIELDS
                        if before.get(name) != getattr(cfg, name)]
    out = payload()
    out["changed"] = changed
    out["restart_required"] = restart_required
    if overlay_key is not None:
        out["overlay_template"] = overlay_key
    return out


# ---------------------------------------------------------------- 重启 / 退出
def set_app_hook(kind: str, fn) -> None:
    """桌宠进程注册 restart / quit：设置页按钮最终落到同一个窗口上。"""
    with _HOOK_LOCK:
        _HOOKS[str(kind)] = fn


def clear_app_hooks() -> None:
    with _HOOK_LOCK:
        _HOOKS.clear()


def _hook(kind: str):
    with _HOOK_LOCK:
        return _HOOKS.get(kind)


def _fire(kind: str, hook) -> None:
    try:
        hook()
    except Exception as exc:  # noqa: BLE001 - 后台线程炸了别影响服务
        print(f"设置页 {kind} 失败：{type(exc).__name__}: {exc}", flush=True)


def _request(kind: str, what: str) -> dict:
    hook = _hook(kind)
    if hook is None:
        return {"ok": False, "error": f"桌宠不在这个进程里，{what}请手动重新运行 python main.py"}
    threading.Timer(0.5, _fire, args=(kind, hook)).start()
    try:
        from .overlay_server import bound_port

        port = bound_port() or default_port()
    except Exception:
        port = default_port()
    return {"ok": True, kind: True, "port": port, "pid": os.getpid()}


def request_restart() -> dict:
    return _request("restart", "重启")


def request_quit() -> dict:
    return _request("quit", "退出")


# ---------------------------------------------------------------- 打开窗口
def edge_path() -> str:
    """Edge 的应用窗口（--app）看起来就是一个设置窗，没有地址栏。"""
    if os.name != "nt":
        return ""
    roots = [
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for root in roots:
        if not root:
            continue
        exe = Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if exe.is_file():
            return str(exe)
    return ""


def open_window(port: int | None = None) -> dict:
    """打开设置页：优先 Edge 应用窗口，其次默认浏览器。"""
    port = int(port or default_port())
    url = f"http://127.0.0.1:{port}/settings"
    edge = edge_path()
    if edge:
        try:
            subprocess.Popen(
                [edge, f"--app={url}", "--window-size=1160,840",
                 "--disable-features=msEdgeSidebarV2"],
                stdin=subprocess.DEVNULL, close_fds=True,
            )
            return {"ok": True, "url": url, "via": "edge"}
        except OSError as exc:
            print(f"Edge 打开设置页失败，改用默认浏览器：{exc}", flush=True)
    webbrowser.open(url)
    return {"ok": True, "url": url, "via": "browser"}


__all__ = [
    "PAGE", "RESTART_FIELDS", "clear_app_hooks", "config_rev", "default_port",
    "edge_path", "open_window", "page_path", "payload", "port_of", "request_quit",
    "request_restart", "save", "set_app_hook", "tts_url_for",
]
