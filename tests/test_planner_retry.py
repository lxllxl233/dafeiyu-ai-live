"""LLM 调用失败的重试与降级：无人值守连播时，一次抖动不该让整晚上停摆。

- 超时 / 连接重置 / 429 / 5xx：退避重试（最多 3 次）；
- 401/403：不重试，直接报「API Key 无效」，省得白烧请求；
- 3 次都失败：返回 ok=False，由 runner 落本地兜底并写 llm_fallback。
"""
from __future__ import annotations

import requests

from dafeiyu.agent.planner import LLMPlanner
from dafeiyu.core.config import LiveConfig


class _Resp:
    def __init__(self, status: int = 200, content: str = '{"act":"proceed"}'):
        self.status_code = status
        self.text = f"status-{status}"
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2}}


def _planner(monkeypatch, replies: list):
    monkeypatch.setattr("dafeiyu.agent.planner.time.sleep", lambda *_: None)
    sent: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        item = replies.pop(0) if replies else _Resp()
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("dafeiyu.agent.planner.requests.post", fake_post)
    cfg = LiveConfig.load()
    cfg.llm_api_key = "test-key"      # 只在内存里，不写 config.json
    return LLMPlanner(cfg, system_prompt="SYS"), sent


def test_timeout_and_reset_are_retried(monkeypatch):
    planner, sent = _planner(monkeypatch, [
        requests.exceptions.ReadTimeout("read timed out"),
        requests.exceptions.ConnectionError("connection reset"),
        _Resp(),
    ])
    out = planner.plan({"screen": "MAP"}, goal="选路")
    assert out["ok"] is True
    assert len(sent) == 3


def test_rate_limit_is_retried(monkeypatch):
    planner, sent = _planner(monkeypatch, [_Resp(429), _Resp()])
    out = planner.plan({"screen": "MAP"}, goal="选路")
    assert out["ok"] is True
    assert len(sent) == 2


def test_gateway_errors_exhaust_retries(monkeypatch):
    planner, sent = _planner(monkeypatch, [_Resp(503), _Resp(503), _Resp(503)])
    out = planner.plan({"screen": "MAP"}, goal="选路")
    assert out["ok"] is False
    assert "HTTP 503" in out["error"]
    assert len(sent) == 3


def test_bad_api_key_is_not_retried(monkeypatch):
    planner, sent = _planner(monkeypatch, [_Resp(401)])
    out = planner.plan({"screen": "MAP"}, goal="选路")
    assert out["ok"] is False
    assert "API Key" in out["error"]
    assert len(sent) == 1


def test_invalid_json_retries_once_then_fails(monkeypatch):
    planner, sent = _planner(monkeypatch, [_Resp(content="not json"),
                                           _Resp(content="still not json")])
    out = planner.plan({"screen": "MAP"}, goal="选路")
    assert out["ok"] is False
    assert len(sent) == 2


def test_invalid_json_retry_bills_both_calls(monkeypatch):
    """第一次返回坏 JSON 的 token 也是真花掉的，账单不能只算第二次。"""
    planner, sent = _planner(monkeypatch, [_Resp(content="nope"), _Resp()])
    out = planner.plan({"screen": "MAP"}, goal="选路")
    assert out["ok"] is True
    assert out["usage"]["total"] == 4      # 两次调用各 2 token
    assert out["usage"]["input"] == 2 and out["usage"]["output"] == 2
