"""LLM 规划器：只负责「这一回合怎么打」，不负责 TTS、OBS 和 UI。

调用的是 OpenAI 兼容的 `/chat/completions`，所以 DeepSeek / OpenAI / 本地
vLLM 都能用；配置来自本机 config.json，Key 不写日志、不进轨迹。
"""
from __future__ import annotations

import json
import re

import requests

from ..core.config import LiveConfig

_SYSTEM = """你是《杀戮尖塔 2》的回合决策器。只输出一个 JSON 对象，不要 markdown、不要解释。
格式：{"say":["一句中文解说"],"seq":[{"c":"CARD_ID","t":0},{"end":1}]}
规则：
- seq 最后必须是 {"end":1}；c 用卡牌 id；t 是敌人下标，AOE/无目标可省略。
- 先看 context 里的 rules / mechanics / incoming；能斩杀就斩杀，不能斩杀先挡满。
- 不要输出未在 hand 里出现的牌；不确定就少打，别硬凑。
- say 只写一句，带数字，符合大肥鱼口吻（认真算账、偶尔自嘲）。"""


def _extract_plan(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S)
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except ValueError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(raw[start:end + 1])
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}
    return {}


class LLMPlanner:
    name = "llm"

    def __init__(self, cfg: LiveConfig | None = None, *, timeout: float = 60.0) -> None:
        self.cfg = cfg or LiveConfig.load()
        self.timeout = timeout

    def _url(self) -> str:
        base = (self.cfg.llm_base_url or "https://api.deepseek.com/v1").rstrip("/")
        return base if base.endswith("/chat/completions") else base + "/chat/completions"

    def available(self) -> tuple[bool, str]:
        if not self.cfg.llm_api_key.strip():
            return False, "未配置 API Key（控制台「基础配置」或 config.json）"
        return True, ""

    def plan(self, context: dict, feedback: str = "", goal: str = "") -> dict:
        ok, why = self.available()
        if not ok:
            return {"ok": False, "error": why, "planner": self.name}
        payload = {
            "model": self.cfg.llm_model or "deepseek-chat",
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": json.dumps({
                    "goal": goal or "打一回合，先别死，能赢就赢",
                    "feedback": feedback,
                    "context": context,
                }, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            "max_tokens": 900,
            "stream": False,
        }
        try:
            resp = requests.post(
                self._url(),
                headers={"Authorization": f"Bearer {self.cfg.llm_api_key}",
                         "Content-Type": "application/json"},
                json=payload, timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "planner": self.name}
        if resp.status_code >= 400:
            return {"ok": False, "planner": self.name,
                    "error": f"HTTP {resp.status_code}: {resp.text[:240]}"}
        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return {"ok": False, "planner": self.name, "error": f"响应格式错误：{exc}"}
        plan = _extract_plan(content)
        if not plan:
            return {"ok": False, "planner": self.name,
                    "error": "模型没有返回可解析的 JSON 计划"}
        usage = body.get("usage") or {}
        return {
            "ok": True, "plan": plan, "planner": self.name,
            "usage": {
                "input": int(usage.get("prompt_tokens") or 0),
                "output": int(usage.get("completion_tokens") or 0),
                "total": int(usage.get("total_tokens") or 0),
            },
        }


class ProvidedPlanner:
    """外部（DSH Agent 或控制台）已经给了 plan；这里只负责回放。"""

    name = "provided"

    def __init__(self, plan: dict) -> None:
        self._plan = dict(plan or {})

    def plan(self, context: dict, feedback: str = "", goal: str = "") -> dict:
        if feedback:
            return {"ok": False, "planner": self.name,
                    "error": "外部 plan 验算失败；请修改计划后重试"}
        return {"ok": True, "plan": self._plan, "planner": self.name,
                "usage": {"input": 0, "output": 0, "total": 0}}


__all__ = ["LLMPlanner", "ProvidedPlanner", "_extract_plan"]
