"""运行配置：默认值 < config.json < 环境变量。

环境变量优先，方便直播时临时改（不动文件）：

    DAFEIYU_VOICE=animal        语音层首选（mambo / animal / off）
    DAFEIYU_VOLUME=0.8          音量
    DAFEIYU_PERSONA=dafeiyu     性格层人设卡
    DAFEIYU_GAME=<game-id>      游戏包 id；空 = 用目录里的默认游戏
    DAFEIYU_OVERLAY=http://127.0.0.1:8730
    DAFEIYU_TTS_URL=http://127.0.0.1:9880
    <ALIAS>_TTS_*               游戏包声明的旧前缀继续兼容
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .. import paths

# config.json 会被桌宠（快捷键/设置页）和控制台同时写：同一进程内先串起来，
# 避免两处 read-modify-write 交错把对方刚存的字段盖回旧值。
_SAVE_LOCK = threading.Lock()


_ENV_FIELDS = {
    "PERSONA": "persona",
    "VOICE": "voice",
    "FALLBACK_VOICE": "fallback_voice",
    "GAME": "game",
    "VOLUME": "volume",
    "MIN_GAP": "min_gap",
    "AUTOPLAY_MODE": "autoplay_mode",
    "AUTOPLAY_LLM_MODEL": "autoplay_llm_model",
    "AUTOPLAY_PACE": "autoplay_pace",
    "RECORDING_ENABLED": "recording_enabled",
    "BILIBILI_ENABLED": "bilibili_enabled",
    "BILIBILI_ROOM_ID": "bilibili_room_id",
    "BILIBILI_COOKIE": "bilibili_cookie",
    "KOOK_ENABLED": "kook_enabled",
    "KOOK_BOT_TOKEN": "kook_bot_token",
    "KOOK_PERSONA": "kook_persona",
    "KOOK_MEMORY": "kook_memory",
    "KOOK_PET_SAY": "kook_pet_say",
    "KOOK_ALLOW_VOICE": "kook_allow_voice",
    "KOOK_STICKERS": "kook_stickers",
    "KOOK_PROACTIVE": "kook_proactive",
    "KOOK_PROACTIVE_MINUTES": "kook_proactive_minutes",
    "KOOK_CONTEXT_TURNS": "kook_context_turns",
    "KOOK_SUMMARY_TRIGGER": "kook_summary_trigger",
    "PERSONA_LIVE": "persona_live",
    "PET_STATUS_BAR": "pet_status_bar",
    "PET_AUTOSTART": "pet_autostart",
    "HOST": "host",
    "PORT": "port",
    "OVERLAY_PORT": "port",
    "OVERLAY": "overlay_url",
    "TTS_URL": "tts_url",
    "TTS_REF": "tts_ref",
    "TTS_PROMPT": "tts_prompt",
    "TTS_SPEED": "tts_speed",
    "TTS_TIMEOUT": "tts_timeout",
    "MAMBO_DIR": "mambo_dir",
    "LLM_PROVIDER": "llm_provider",
    "LLM_BASE_URL": "llm_base_url",
    "LLM_MODEL": "llm_model",
    "LLM_API_KEY": "llm_api_key",
}


@dataclass(slots=True)
class LiveConfig:
    # 性格层：个人（persona，常驻）/ 直播（persona/live）/ 游戏（games/<id>/persona/layer.md）
    persona: str = "dafeiyu"
    # 直播性格是否叠加；关掉就只用个人性格
    persona_live: bool = True
    voice: str = "mambo"
    fallback_voice: str = "animal"
    game: str = ""

    # 音量 / 节奏
    volume: float = 0.55
    min_gap: float = 1.2

    # 服务
    host: str = "127.0.0.1"
    port: int = 8730
    overlay_url: str = "http://127.0.0.1:8730"
    tts_url: str = "http://127.0.0.1:9880"
    tts_ref: str = ""
    tts_prompt: str = "大家好，欢迎来到我的频道，今天给大家分享一个有趣的内容"
    tts_speed: float = 1.0
    tts_timeout: float = 60.0
    # 本地曼波引擎目录；空 = 自动探测（见 voice/startup.py）
    mambo_dir: str = ""

    # 模型 / API（api_key 只写本机 config.json，绝不回显、绝不入库）
    llm_provider: str = "deepseek"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_api_key: str = ""
    llm_models: list[str] = field(default_factory=lambda: ["deepseek-chat", "deepseek-reasoner"])

    # 行为
    push_overlay: bool = True
    strict_lint: bool = False

    # 桌宠窗口自己的状态：右键设置页 / 快捷键 / 重启后读的是同一份。
    # （点击穿透、置顶、录屏隐藏以前只活在内存里，重启就丢。）
    pet_topmost: bool = True
    pet_click_through: bool = False
    pet_exclude_capture: bool = False
    pet_muted: bool = False
    # 常驻 Windows 托盘图标 / 开机自启动（注册表 HKCU Run）
    pet_status_bar: bool = False
    pet_autostart: bool = False
    # 自动玩法：local = 本地算账；llm = 战斗回合交给 LLM 规划
    autoplay_mode: str = "local"
    # 自动玩法的 LLM 模型；默认用非推理模型，避免长局面把 content 挤空
    autoplay_llm_model: str = "deepseek-chat"
    # 思维链开关：off = 都关（默认，界面和战斗都 1–2s 回）；auto = 界面关、
    # 战斗跟模型默认；on = 都开。实测 deepseek-flash 开着 thinking 时一次战斗
    # 决策要 24s、4096 reasoning token、content 还是空的；关掉同一局面 1.7s 出计划。
    autoplay_llm_thinking: str = "off"
    # voice = 每步等语音/留出发音时间；normal = 旧版快节奏
    autoplay_pace: str = "voice"

    # OBS 录像总开关；每局/不分集由游戏清单的 recording 块 + config.json 覆盖
    recording_enabled: bool = True
    recording: dict = field(default_factory=dict)

    # 每个游戏的开局设置（角色/难度…）：{game_id: {key: value}}。
    # 可选项由各游戏 game.json 的 start 块声明，核心只按声明清洗后保存。
    start_options: dict = field(default_factory=dict)

    # B 站直播间：启用后由桌宠监听礼物事件（房间号填直播间 URL 里的数字）
    bilibili_enabled: bool = False
    bilibili_room_id: int = 0
    bilibili_cookie: str = ""       # 可选：需要登录态时填 SESSDATA 等；只写本机

    # KOOK 聊天：Bot WebSocket 长连接（纯出站，不用公网 IP）。
    kook_enabled: bool = False
    kook_bot_token: str = ""        # 只写本机；to_dict() 只报是否已配置
    kook_persona: str = "companion"
    kook_memory: bool = True
    # 聊天记忆：最近多少条原文进上下文；未压缩对话超过阈值就用模型压成摘要
    kook_context_turns: int = 12
    kook_summary_trigger: int = 24
    # 只允许这些 KOOK 用户 id；空 = 不限制（单人自用可以留空）
    kook_allowed_users: list = field(default_factory=list)
    # 每句回复同时让桌宠说一遍（需要桌宠/控制台在跑）
    kook_pet_say: bool = False
    # 模型可决定发语音（KOOK 用音频卡片）；也可以主动配大肥鱼表情包
    kook_allow_voice: bool = True
    kook_stickers: bool = True
    # 安静一阵后，偶尔主动找最近聊过的人/频道说一句（不会连发）
    kook_proactive: bool = True
    kook_proactive_minutes: int = 60
    # 看图用的模型：DeepSeek 只有 flash 系支持图片输入
    llm_vision_model: str = "deepseek-flash"
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.game:
            from ..games.catalog import default_game_id

            self.game = default_game_id()

    # ---------------------------------------------------------------- 读取
    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "LiveConfig":
        cfg = cls()
        cfg.overlay_url = f"http://{cfg.host}:{cfg.port}"
        file_path = Path(path) if path else paths.CONFIG_FILE
        if file_path.exists():
            try:
                raw = json.loads(file_path.read_text(encoding="utf-8"))
                cfg._merge(raw if isinstance(raw, dict) else {})
            except (OSError, ValueError):
                pass
        cfg._apply_env()
        from ..games.catalog import resolve_game_id

        cfg.game = resolve_game_id(cfg.game)
        return cfg

    def _merge(self, raw: dict) -> None:
        known = {f.name for f in fields(self)}
        for key, value in raw.items():
            if key in known and value is not None:
                setattr(self, key, value)
            elif key not in known:
                self.extra[key] = value

    def _apply_env(self) -> None:
        from ..games.catalog import env_value, legacy_env_prefixes

        for suffix, attr in _ENV_FIELDS.items():
            val = env_value(suffix)
            if val == "":
                continue
            cur = getattr(self, attr)
            try:
                if isinstance(cur, bool):
                    setattr(self, attr, val.strip().lower() not in ("0", "false", "no", "off"))
                elif isinstance(cur, int):
                    setattr(self, attr, int(val))
                elif isinstance(cur, float):
                    setattr(self, attr, float(val))
                else:
                    setattr(self, attr, val)
            except (TypeError, ValueError):
                continue
        # 只改了旧端口、没显式给 OVERLAY 时，让 overlay_url 跟着新端口走。
        has_overlay = bool(os.environ.get("DAFEIYU_OVERLAY")) or any(
            os.environ.get(f"{prefix}_OVERLAY", "") for prefix in legacy_env_prefixes()
        )
        has_port = bool(os.environ.get("DAFEIYU_OVERLAY_PORT")) or any(
            f"{prefix}_OVERLAY_PORT" in os.environ for prefix in legacy_env_prefixes()
        )
        if has_port and not has_overlay:
            self.overlay_url = f"http://{self.host}:{self.port}"

    # ---------------------------------------------------------------- 输出
    def to_dict(self) -> dict:
        """给控制台/日志看的配置：API Key 只报是否已配置，绝不回显。"""
        out = asdict(self)
        out.pop("extra", None)
        out["tts_ref"] = bool(out.get("tts_ref"))
        key = out.pop("llm_api_key", "") or ""
        out["llm_api_key"] = ""
        out["llm_api_key_set"] = bool(key)
        cookie = out.pop("bilibili_cookie", "") or ""
        out["bilibili_cookie"] = ""
        out["bilibili_cookie_set"] = bool(cookie)
        token = out.pop("kook_bot_token", "") or ""
        out["kook_bot_token"] = ""
        out["kook_bot_token_set"] = bool(token)
        return out

    def save_patch(self, patch: dict) -> "LiveConfig":
        """把少量配置合并进本机 config.json（不入库；API Key 不回显）。"""
        with _SAVE_LOCK:
            return self._save_patch(patch)

    @classmethod
    def patch(cls, patch: dict) -> "LiveConfig":
        """在锁里现读现写：调用方不用先 load 一份可能已经过期的配置。"""
        with _SAVE_LOCK:
            cfg = cls.load()
            return cfg._save_patch(patch)

    def _save_patch(self, patch: dict) -> "LiveConfig":
        known = {f.name for f in fields(self)}
        path = paths.CONFIG_FILE
        raw: dict = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                raw = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                raw = {}
        for key, value in patch.items():
            if key not in known or key == "extra" or value is None:
                continue
            if key == "llm_api_key" and value == "***":
                continue        # 控制台回显的占位符，不是真 Key
            if key == "bilibili_cookie" and value == "***":
                continue
            if key == "kook_bot_token" and value == "***":
                continue
            setattr(self, key, value)
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "extra"}
        data.update(self.extra)
        raw.update(data)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return self


__all__ = ["LiveConfig"]
