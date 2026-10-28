"""运行配置：默认值 < config.json < 环境变量。

环境变量优先，方便直播时临时改（不动文件）：

    DAFEIYU_VOICE=animal        语音层首选（mambo / animal / off）
    DAFEIYU_VOLUME=0.8          音量
    DAFEIYU_PERSONA=dafeiyu     性格层人设卡
    DAFEIYU_GAME=slay-the-spire-2
    DAFEIYU_OVERLAY=http://127.0.0.1:8730
    DAFEIYU_TTS_URL=http://127.0.0.1:9880
    STS2_TTS_*                  兼容旧工程的环境变量
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .. import paths


def _env_key(name: str) -> str:
    return name.upper()


@dataclass(slots=True)
class LiveConfig:
    # 层
    persona: str = "dafeiyu"
    voice: str = "mambo"
    fallback_voice: str = "animal"
    game: str = "slay-the-spire-2"

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
    # 自动玩法：local = 本地算账；llm = 战斗回合交给 LLM 规划
    autoplay_mode: str = "local"
    # 自动玩法的 LLM 模型；默认用非推理模型，避免长局面把 content 挤空
    autoplay_llm_model: str = "deepseek-chat"
    # voice = 每步等语音/留出发音时间；normal = 旧版快节奏
    autoplay_pace: str = "voice"

    # B 站直播间：启用后由桌宠监听礼物事件（房间号填直播间 URL 里的数字）
    bilibili_enabled: bool = False
    bilibili_room_id: int = 0
    bilibili_cookie: str = ""       # 可选：需要登录态时填 SESSDATA 等；只写本机
    extra: dict = field(default_factory=dict)

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
        return cfg

    def _merge(self, raw: dict) -> None:
        known = {f.name for f in fields(self)}
        for key, value in raw.items():
            if key in known and value is not None:
                setattr(self, key, value)
            elif key not in known:
                self.extra[key] = value

    def _apply_env(self) -> None:
        pairs = {
            "DAFEIYU_PERSONA": "persona",
            "DAFEIYU_VOICE": "voice",
            "DAFEIYU_FALLBACK_VOICE": "fallback_voice",
            "DAFEIYU_GAME": "game",
            "DAFEIYU_VOLUME": "volume",
            "DAFEIYU_MIN_GAP": "min_gap",
            "DAFEIYU_AUTOPLAY_MODE": "autoplay_mode",
            "DAFEIYU_AUTOPLAY_LLM_MODEL": "autoplay_llm_model",
            "DAFEIYU_AUTOPLAY_PACE": "autoplay_pace",
            "DAFEIYU_BILIBILI_ENABLED": "bilibili_enabled",
            "DAFEIYU_BILIBILI_ROOM_ID": "bilibili_room_id",
            "DAFEIYU_BILIBILI_COOKIE": "bilibili_cookie",
            "DAFEIYU_HOST": "host",
            "DAFEIYU_PORT": "port",
            "DAFEIYU_OVERLAY": "overlay_url",
            "DAFEIYU_TTS_URL": "tts_url",
            "DAFEIYU_TTS_REF": "tts_ref",
            "DAFEIYU_TTS_PROMPT": "tts_prompt",
            "DAFEIYU_TTS_SPEED": "tts_speed",
            "DAFEIYU_TTS_TIMEOUT": "tts_timeout",
            "DAFEIYU_MAMBO_DIR": "mambo_dir",
            "DAFEIYU_LLM_PROVIDER": "llm_provider",
            "DAFEIYU_LLM_BASE_URL": "llm_base_url",
            "DAFEIYU_LLM_MODEL": "llm_model",
            "DAFEIYU_LLM_API_KEY": "llm_api_key",
            # 旧工程的环境变量，继续认
            "STS2_TTS_URL": "tts_url",
            "STS2_TTS_REF": "tts_ref",
            "STS2_TTS_PROMPT": "tts_prompt",
            "STS2_TTS_SPEED": "tts_speed",
            "STS2_OVERLAY": "overlay_url",
            "STS2_OVERLAY_PORT": "port",
            "STS2_MAMBO_DIR": "mambo_dir",
        }
        for env, attr in pairs.items():
            val = os.environ.get(env)
            if val is None or val == "":
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
        if "STS2_OVERLAY_PORT" in os.environ and "DAFEIYU_OVERLAY" not in os.environ:
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
        return out

    def save_patch(self, patch: dict) -> "LiveConfig":
        """把少量配置合并进本机 config.json（不入库；API Key 不回显）。"""
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
