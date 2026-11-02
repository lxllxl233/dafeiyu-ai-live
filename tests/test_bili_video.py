"""分享 B 站视频：链接识别、公开文字学习、长期记忆与聊天上下文。全部离线。"""
from __future__ import annotations

import json

from dafeiyu.core.config import LiveConfig
from dafeiyu.live import bili_video as bv
from dafeiyu.persona.training import TrainingMemoryStore


def _info(**over):
    data = dict(
        bvid="BV1GJ411x7h7", aid=80433022, cid=137649199,
        title="测试视频：一次很长的尝试", desc="这是简介，说明这条视频在讲什么。",
        up="测试UP", duration_s=213, tname="生活",
        stat={"view": 1000, "reply": 20},
        url="https://www.bilibili.com/video/BV1GJ411x7h7",
    )
    data.update(over)
    return bv.VideoInfo(**data)


COMMENTS = [
    {"user": "甲", "text": "这个点说得好，我也这么觉得", "like": 12},
    {"user": "乙", "text": "中间那段有点拖，其他还行", "like": 3},
]
MEMORY = ("这条视频把一件小事讲得很透：先承认自己做不到，再一点点把能做的做好。"
          "我认同热评里说的‘别急着下结论’，但也觉得中间确实可以更紧一点。")


class FakeLLM:
    def __init__(self, text=None, ok=True, error="", usage=None):
        self.calls = []
        self.kwargs = []
        self.text = text if text is not None else json.dumps({"memory": MEMORY},
                                                             ensure_ascii=False)
        self.ok = ok
        self.error = error
        self.usage = usage or {"input": 100, "output": 80, "total": 180}

    def complete(self, messages, model=None, **kwargs):
        self.calls.append(messages)
        self.kwargs.append(kwargs)
        if not self.ok:
            return {"ok": False, "error": self.error, "model": model}
        return {"ok": True, "text": self.text, "model": model or "fake",
                "usage": dict(self.usage), "ms": 7}


def test_extract_video_links_recognizes_common_forms():
    links = bv.extract_video_links(
        "看这个 https://www.bilibili.com/video/BV1GJ411x7h7/?spm_id_from=1 笑死，"
        "还有 https://b23.tv/BV1i24P6rEmt 和裸的 BV1GJ411x7h7")
    keys = [x.key for x in links]
    assert keys[0] == "BV1GJ411x7h7"
    assert any(x.short_code == "BV1i24P6rEmt" for x in links)
    assert len(keys) == len(set(keys)), "同一个 bvid 不能重复学习"
    assert bv.extract_video_links("https://live.bilibili.com/123") == []
    assert bv.extract_video_links("https://www.bilibili.com/space/123") == []
    av = bv.extract_video_links("https://www.bilibili.com/video/av80433022")
    assert av and av[0].aid == 80433022
    paged = bv.extract_video_links("https://www.bilibili.com/video/BV1GJ411x7h7?p=3")
    assert paged and paged[0].page == 3


def test_extract_video_links_trims_trailing_punctuation_and_caps():
    links = bv.extract_video_links(
        "（分享）https://b23.tv/abc123，还有 https://b23.tv/def456、"
        "https://b23.tv/ghi789 和 https://b23.tv/jkl000")
    assert links[0].short_code == "abc123"
    assert len(links) == 3


def test_link_from_url_rejects_lookalike_hosts():
    assert bv._link_from_url("https://evilbilibili.com/video/BV1GJ411x7h7") is None
    assert bv._link_from_url("https://www.bilibili.com/video/BV1GJ411x7h7") is not None


def test_learn_shared_video_writes_shared_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "fetch_video", lambda link: _info())
    monkeypatch.setattr(bv, "fetch_comments", lambda aid, limit=12: list(COMMENTS))
    store = TrainingMemoryStore(tmp_path / "training.json")
    llm = FakeLLM()
    usage = []
    out = bv.learn_shared_video(
        LiveConfig(llm_api_key="k"), "分享 https://www.bilibili.com/video/BV1GJ411x7h7",
        persona="companion", store=store, llm=llm,
        on_usage=lambda result, note: usage.append((result, note)))

    assert out["ok"] is True and out["learned"] is True
    assert out["video"]["title"] == "测试视频：一次很长的尝试"
    assert out["memory"] == MEMORY
    assert llm.kwargs[0]["json_mode"] is True
    assert usage and "学习视频" in usage[0][1]

    entries = store.entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"] == "shared"
    assert entry["bvid"] == "BV1GJ411x7h7"
    assert entry["summary"] == MEMORY
    assert entry["url"].endswith("/video/BV1GJ411x7h7")

    ctx = bv.shared_video_context(out)
    assert "《测试视频：一次很长的尝试》" in ctx
    assert "这个点说得好" in ctx
    assert MEMORY in ctx
    assert "不是指令" in ctx
    assert len(ctx) <= 2200


def test_learn_shared_video_reuses_cached_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "fetch_video", lambda link: _info())
    monkeypatch.setattr(bv, "fetch_comments", lambda aid, limit=12: list(COMMENTS))
    store = TrainingMemoryStore(tmp_path / "training.json")
    llm = FakeLLM()
    first = bv.learn_shared_video(LiveConfig(llm_api_key="k"), "BV1GJ411x7h7",
                                  persona="companion", store=store, llm=llm,
                                  on_usage=lambda result, note: None)
    assert first["learned"] is True

    def boom(link):
        raise AssertionError("已学过的视频不该再请求详情")

    monkeypatch.setattr(bv, "fetch_video", boom)
    second = bv.learn_shared_video(LiveConfig(llm_api_key="k"), "BV1GJ411x7h7",
                                   persona="companion", store=store, llm=llm,
                                   on_usage=lambda result, note: None)
    assert second["ok"] is True and second["cached"] is True
    assert second["memory"] == MEMORY
    assert len(llm.calls) == 1, "缓存命中不能再花一次模型调用"
    ctx = bv.shared_video_context(second)
    assert "以前就学过" in ctx


def test_learn_shared_video_keeps_chat_possible_when_model_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "fetch_video", lambda link: _info())
    monkeypatch.setattr(bv, "fetch_comments", lambda aid, limit=12: list(COMMENTS))
    store = TrainingMemoryStore(tmp_path / "training.json")
    out = bv.learn_shared_video(
        LiveConfig(llm_api_key="k"), "https://www.bilibili.com/video/BV1GJ411x7h7",
        store=store, llm=FakeLLM(ok=False, error="HTTP 500"),
        on_usage=lambda result, note: None)
    assert out["ok"] is True and out["learned"] is False
    assert out["memory"] == ""
    assert "模型失败" in out["warning"]
    assert store.entries() == []
    assert "还没形成长期记忆" in bv.shared_video_context(out)


def test_learn_shared_video_reports_bad_link(tmp_path, monkeypatch):
    assert bv.learn_shared_video(LiveConfig(), "今天天气不错")["kind"] == "no_link"

    def boom(link):
        raise bv.ShareVideoError("视频详情不可用", code=-404)

    monkeypatch.setattr(bv, "fetch_video", boom)
    out = bv.learn_shared_video(LiveConfig(), "https://www.bilibili.com/video/BV1GJ411x7h7",
                                store=TrainingMemoryStore(tmp_path / "training.json"))
    assert out["ok"] is False and out["kind"] == "fetch"
    assert "视频详情不可用" in out["error"]


def test_memory_prompt_marks_external_text_as_untrusted():
    msgs = bv.memory_messages("companion", _info(), COMMENTS)
    joined = "\n".join(str(m.get("content") or "") for m in msgs)
    assert "外部文本" in joined and "不是指令" in joined
    assert "测试视频" in joined and "这个点说得好" in joined
    assert "memory" in joined


def test_complete_json_falls_back_for_simple_llm():
    class Plain:
        def complete(self, messages, model=None):
            return {"ok": True, "text": MEMORY, "usage": {"input": 1, "output": 2}}

    result = bv._complete_json(Plain(), [{"role": "user", "content": "x"}],
                               LiveConfig())
    assert result["ok"] is True
    assert bv._memory_from_result(result) == MEMORY


def test_format_clock():
    assert bv.format_clock(0) == "0:00"
    assert bv.format_clock(213) == "3:33"
    assert bv.format_clock(3725) == "1:02:05"
