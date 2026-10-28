"""LLM 规划器：只负责「这一回合怎么打」，不负责 TTS、OBS 和 UI。

调用的是 OpenAI 兼容的 `/chat/completions`，所以 DeepSeek / OpenAI / 本地
vLLM 都能用；配置来自本机 config.json，Key 不写日志、不进轨迹。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

from ..core.config import LiveConfig

# 这些状态码是「过一会儿再试可能就好」的：限流 / 网关 / 上游超时。
_RETRY_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})


def _merge_usage(first: dict, second: dict | None) -> dict:
    """合并两次调用的 token；``ms`` 取两段耗时之和（重试也是真花的时间）。"""
    second = second if isinstance(second, dict) else {}

    def _i(d: dict, k: str) -> int:
        try:
            return int(d.get(k) or 0)
        except (TypeError, ValueError):
            return 0

    out = {"input": _i(first, "input") + _i(second, "input"),
           "output": _i(first, "output") + _i(second, "output"),
           "total": _i(first, "total") + _i(second, "total"),
           "ms": _i(first, "ms") + _i(second, "ms")}
    if out["total"] <= 0:
        out["total"] = out["input"] + out["output"]
    return out


def _retryable(resp) -> bool:
    try:
        return int(resp.status_code) in _RETRY_HTTP
    except (TypeError, ValueError, AttributeError):
        return False


def _extract_plan(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S)
    candidates = [raw]
    # 模型偶发把数组结尾写成 `"]"`，多一个引号；只在解析失败时尝试修。
    repaired = re.sub(r'"\]"(\s*[,}])', r'"]\1', raw)
    if repaired != raw:
        candidates.append(repaired)
    # 尾逗号也常见。
    candidates.append(re.sub(r",(\s*[}\]])", r"\1", raw))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
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

    def __init__(self, cfg: LiveConfig | None = None, *, timeout: float = 60.0,
                 model: str | None = None, system_prompt: str = "",
                 thinking: bool | None = None) -> None:
        self.cfg = cfg or LiveConfig.load()
        self.timeout = timeout
        self.model = str(model or self.cfg.llm_model or "deepseek-chat")
        self.system_prompt = str(system_prompt or "").strip()
        # None = 不传，跟模型默认；False = thinking: disabled（快）；True = enabled。
        # deepseek 的推理模型会把 completion 预算全花在思维链上（实测 24s / 4096
        # reasoning token / content 为空），界面决策这种简单选择没必要付这个钱。
        self.thinking = thinking

    def _url(self) -> str:
        base = (self.cfg.llm_base_url or "https://api.deepseek.com/v1").rstrip("/")
        return base if base.endswith("/chat/completions") else base + "/chat/completions"

    def available(self) -> tuple[bool, str]:
        if not self.cfg.llm_api_key.strip():
            return False, "未配置 API Key（控制台「基础配置」或 config.json）"
        return True, ""

    def plan(self, context: dict, feedback: str = "", goal: str = "",
             *, _retry: int = 0) -> dict:
        ok, why = self.available()
        if not ok:
            return {"ok": False, "error": why, "planner": self.name}
        if not self.system_prompt:
            return {"ok": False, "planner": self.name,
                    "error": "没有决策提示词；应由游戏插件提供 system_prompt"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": json.dumps({
                    "goal": goal or "打一回合，先别死，能赢就赢",
                    "feedback": feedback,
                    "context": context,
                }, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            # 推理型模型（如 deepseek-flash）会先消耗 reasoning token；
            # 900/2200 在长局面下仍可能把 content 挤空，给足预算。
            "max_tokens": 4096,
            "stream": False,
        }
        if self.thinking is not None:
            payload["thinking"] = {"type": "enabled" if self.thinking else "disabled"}
        t0 = time.perf_counter()
        resp = None
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                resp = requests.post(
                    self._url(),
                    headers={"Authorization": f"Bearer {self.cfg.llm_api_key}",
                             "Content-Type": "application/json"},
                    json=payload, timeout=self.timeout,
                )
            except Exception as exc:  # noqa: BLE001
                # 无人值守连播时，一次超时/连接重置不该让这一手直接判死：
                # 退避重试两次（1.2s / 2.4s），还不行才交给调用方降级。
                if attempt < attempts:
                    time.sleep(1.2 * attempt)
                    continue
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                        "planner": self.name}
            if _retryable(resp) and attempt < attempts:
                time.sleep(1.2 * attempt)
                continue
            break
        if resp is not None and resp.status_code in (400, 422) and "thinking" in payload:
            # 换 provider / 换模型后可能不认 thinking 字段：去掉再试一次，
            # 记住这个实例以后都不带，别让一个可选优化把规划打死。
            payload.pop("thinking", None)
            self.thinking = None
            try:
                resp = requests.post(
                    self._url(),
                    headers={"Authorization": f"Bearer {self.cfg.llm_api_key}",
                             "Content-Type": "application/json"},
                    json=payload, timeout=self.timeout,
                )
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                        "planner": self.name}
        if resp is None:
            return {"ok": False, "error": "没有拿到响应", "planner": self.name}
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code >= 400:
            hint = "（API Key 无效或过期，去控制台「基础配置」更新）" \
                if resp.status_code in (401, 403) else ""
            return {"ok": False, "planner": self.name,
                    "error": f"HTTP {resp.status_code}: {resp.text[:240]}{hint}"}
        try:
            body = resp.json()
            choice = body["choices"][0]
            content = choice["message"]["content"] or ""
            reasoning = choice["message"].get("reasoning_content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return {"ok": False, "planner": self.name, "error": f"响应格式错误：{exc}"}
        if os.environ.get("DAFEIYU_LLM_RAW"):
            try:
                Path("logs").mkdir(parents=True, exist_ok=True)
                Path("logs/llm_raw.txt").write_text(
                    f"model={self.model}\nfinish={choice.get('finish_reason')}\n"
                    f"content:\n{content}\n\nreasoning:\n{reasoning}\n",
                    encoding="utf-8")
            except OSError:
                pass
        plan = _extract_plan(content)
        if not plan and reasoning:
            # 少数推理模型把最终 JSON 落在 reasoning_content 末尾；能解析就用。
            plan = _extract_plan(reasoning)
        usage = body.get("usage") or {}
        first = {"input": int(usage.get("prompt_tokens") or 0),
                 "output": int(usage.get("completion_tokens") or 0),
                 "total": int(usage.get("total_tokens") or 0),
                 "ms": elapsed_ms}
        if not plan:
            if _retry < 1:
                # 只重试一次：网络/格式抖动通常第二次就好；别无限烧 token。
                retry = self.plan(
                    context,
                    feedback=(feedback + " 上次输出不是合法 JSON。只输出一个 JSON 对象，"
                              "不要解释、不要多引号。").strip(),
                    goal=goal, _retry=_retry + 1)
                if retry.get("ok"):
                    # 第一次那笔 token 也是真花掉的，账单要算上。
                    retry["usage"] = _merge_usage(first, retry.get("usage"))
                return retry
            finish = str(choice.get("finish_reason") or "")
            return {"ok": False, "planner": self.name,
                    "error": (f"模型没有返回可解析的 JSON 计划（content {len(content)} 字，"
                              f"reasoning {len(reasoning)} 字，finish={finish or '?'}）")}
        return {"ok": True, "plan": plan, "planner": self.name, "usage": first}


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
