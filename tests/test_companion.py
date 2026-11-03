# -*- coding: utf-8 -*-
"""陪伴状态：心情/精力/亲近度、未完话头、重要日期、作息与主动候选。全部离线。"""
from __future__ import annotations

import json
import time

from dafeiyu.core.config import LiveConfig
from dafeiyu.live.chat_memory import Memory
from dafeiyu.live.companion import (
    Companion, closeness_label, in_quiet, is_goodbye, parse_quiet_hours,
    quiet_end,
)
from dafeiyu.live.kook.service import Inbound, KookService


def _ts(month=11, day=3, hour=20, minute=0, year=2026) -> float:
    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


def _threads(state: Companion) -> list[dict]:
    return list(state._data["threads"])


def test_relationship_grows_and_persists(tmp_path):
    path = tmp_path / "companion.json"
    now = _ts(11, 3, 20, 0)
    state = Companion(path)
    state.touch("u1", "小鱼", now=now)
    peer = state._data["peers"]["u1"]
    assert peer["name"] == "小鱼" and peer["turns"] == 1
    assert peer["closeness"] > 0

    state.touch("u1", "小鱼", now=now + 60)
    assert state._data["peers"]["u1"]["turns"] == 2
    assert state._data["peers"]["u1"]["turns_today"] == 2

    # 隔天再来：连续天数 +1，今天次数重新算。
    state.touch("u1", "小鱼", now=now + 86400)
    peer = state._data["peers"]["u1"]
    assert peer["streak"] == 2 and peer["turns_today"] == 1

    again = Companion(path)
    assert again._data["peers"]["u1"]["name"] == "小鱼"
    assert again._data["peers"]["u1"]["turns"] == 3
    assert closeness_label(peer["closeness"]) in (
        "刚认识的人", "刚认识不久的人", "渐渐熟起来的人")


def test_mood_follows_user_tone(tmp_path):
    state = Companion(tmp_path / "companion.json")
    now = _ts(11, 3, 20, 0)
    state.touch("u1", "小鱼", now=now)
    state.note_user("u1", "今天累死了，还被骂，好烦", now=now)
    low = state._data["self"]["mood"]
    assert low < 0.5

    state.note_user("u1", "哈哈哈太好了，offer 拿到了", now=now + 60)
    assert state._data["self"]["mood"] > low

    # 中性闲聊不要每句都改心情。
    before = state._data["self"]["mood"]
    state.note_user("u1", "嗯嗯", now=now + 120)
    assert abs(state._data["self"]["mood"] - before) < 0.02


def test_open_thread_becomes_proactive_reason(tmp_path):
    state = Companion(tmp_path / "companion.json")
    now = _ts(11, 3, 20, 0)
    state.touch("u1", "小鱼", now=now)
    state.note_user("u1", "我明天上午要去面试", now=now)
    rows = _threads(state)
    assert len(rows) == 1
    assert "面试" in rows[0]["text"]
    assert time.strftime("%Y-%m-%d", time.localtime(rows[0]["due_at"])) == "2026-11-04"

    # 同一句话再说一次不会攒出两条。
    state.note_user("u1", "我明天上午要去面试", now=now + 30)
    assert len(_threads(state)) == 1

    due = float(rows[0]["due_at"])
    candidate = state.candidate("u1", due + 60, idle_minutes=40)
    assert candidate["kind"] == "thread" and candidate["score"] >= 0.9
    assert "面试" in candidate["reason"]

    # 问过一次就收起来，不再反复追问。
    state.mark_thread_asked(rows[0]["id"])
    assert state.candidate("u1", due + 120, idle_minutes=40)["kind"] != "thread"


def test_birthday_and_prompt_block(tmp_path):
    state = Companion(tmp_path / "companion.json")
    now = _ts(11, 3, 20, 0)
    state.touch("u1", "小鱼", now=now)
    state.note_user("u1", "我生日是11月8日", now=now)
    state.note_user("u1", "明天要早起去医院", now=now)
    assert state._data["dates"][0]["month"] == 11
    assert state._data["dates"][0]["day"] == 8

    block = state.prompt_block("u1", now=now)
    assert "【你此刻的状态】" in block
    assert "小鱼" in block and "心情" in block and "精力" in block
    assert "生日" in block and "还有 5 天" in block
    assert "医院" in block
    assert "0." not in block           # 只给状态词，不把数值丢给模型

    # 先收掉「明天去医院」那条话头，才轮到生日这个理由。
    state.mark_thread_asked(_threads(state)[0]["id"])
    candidate = state.candidate("u1", _ts(11, 6, 20, 0), idle_minutes=60)
    assert candidate["kind"] == "date"
    state.mark_date_greeted("u1", 11, 8, now=_ts(11, 6, 20, 0))
    later = state.candidate("u1", _ts(11, 6, 22, 0), idle_minutes=60)
    assert later["kind"] != "date"


def test_nickname_from_request(tmp_path):
    state = Companion(tmp_path / "companion.json")
    now = _ts(11, 3, 20, 0)
    state.touch("u1", "小鱼", now=now)
    state.note_user("u1", "你可以叫我阿鱼就行", now=now)
    assert state._data["peers"]["u1"]["nickname"] == "阿鱼"
    assert "阿鱼" in state.prompt_block("u1", now=now)

    # 「别叫我…」不能把拒绝当成新称呼。
    state.note_user("u1", "别叫我宝宝", now=now + 10)
    assert state._data["peers"]["u1"]["nickname"] == "阿鱼"


def test_quiet_hours_parse_and_overnight():
    assert parse_quiet_hours("00:30-07:30") == (0, 30, 7, 30)
    assert parse_quiet_hours("0:30~7:30") == (0, 30, 7, 30)
    assert in_quiet(_ts(11, 3, 1, 0), "00:30-07:30") is True
    assert in_quiet(_ts(11, 3, 12, 0), "00:30-07:30") is False
    assert in_quiet(_ts(11, 3, 23, 30), "23:00-02:00") is True
    assert in_quiet(_ts(11, 3, 1, 0), "23:00-02:00") is True
    assert in_quiet(_ts(11, 3, 12, 0), "23:00-02:00") is False
    assert in_quiet(_ts(11, 3, 12, 0), "") is False
    assert quiet_end(_ts(11, 3, 1, 0), "00:30-07:30") == _ts(11, 3, 7, 30)
    try:
        parse_quiet_hours("晚上")
    except ValueError as exc:
        assert "00:30" in str(exc)
    else:
        raise AssertionError("应拒绝写错的时段")


def test_goodbye_detection():
    assert is_goodbye("晚安") is True
    assert is_goodbye("晚安，明天见") is True
    assert is_goodbye("我先去睡了明天见") is True
    assert is_goodbye("晚安吗") is False
    assert is_goodbye("晚安，我今天面试过了") is False
    assert is_goodbye("今天好累") is False


def test_candidate_blocks_after_goodnight(tmp_path):
    state = Companion(tmp_path / "companion.json")
    now = _ts(11, 3, 23, 0)
    state.touch("u1", "小鱼", now=now)
    note = state.note_user("u1", "晚安", now=now)
    assert note["goodnight"] is True
    assert state.candidate("u1", now + 1800, idle_minutes=40)["blocked"] == "after_goodnight"
    # 夜里道别 → 第二天早上 9 点才恢复，不能只压 8 小时。
    assert state.candidate("u1", now + 9 * 3600, idle_minutes=540)["blocked"] == "after_goodnight"
    later = state.candidate("u1", now + 10 * 3600, idle_minutes=600)
    assert "blocked" not in later


def test_daytime_nap_goodnight_is_short(tmp_path):
    state = Companion(tmp_path / "companion.json")
    now = _ts(11, 3, 14, 59)
    state.touch("u1", "小鱼", now=now)
    assert state.note_user("u1", "我去睡个午觉，晚安", now=now)["goodnight"] is True
    # 白天打盹只静默 90 分钟，不能把整个傍晚的主动消息吞掉。
    assert state.candidate("u1", now + 1800, idle_minutes=40)["blocked"] == "after_goodnight"
    assert "blocked" not in state.candidate("u1", now + 2 * 3600, idle_minutes=120)


def test_returning_clears_goodnight(tmp_path):
    state = Companion(tmp_path / "companion.json")
    now = _ts(11, 3, 23, 0)
    state.touch("u1", "小鱼", now=now)
    state.note_user("u1", "晚安", now=now)
    assert state.candidate("u1", now + 60, idle_minutes=30)["blocked"] == "after_goodnight"
    # 过了十分钟又回来说话：之前的晚安不再挡主动消息。
    state.note_user("u1", "睡不着，起来喝口水", now=now + 600)
    assert "blocked" not in state.candidate("u1", now + 660, idle_minutes=40)


def test_stats_snapshot(tmp_path):
    state = Companion(tmp_path / "companion.json")
    now = _ts(11, 3, 20, 0)
    state.touch("u1", "小鱼", now=now)
    state.note_user("u1", "我明天要面试", now=now)
    stats = state.stats(now=now)
    assert stats["peers"][0]["name"] == "小鱼"
    assert stats["peers"][0]["label"]
    assert stats["threads"] and "面试" in stats["threads"][0]["text"]
    json.dumps(stats, ensure_ascii=False)


def test_evening_checkin_candidate(tmp_path):
    state = Companion(tmp_path / "companion.json")
    now = _ts(11, 3, 19, 0)
    state.touch("u1", "小鱼", now=now)
    candidate = state.candidate("u1", now + 60, idle_minutes=90)
    assert candidate["kind"] == "evening" and candidate["score"] > 0.5
    assert "今天过得怎么样" in candidate["reason"]


def test_reason_raises_willingness():
    thread = KookService._send_willingness({"score": 0.95})
    idle = KookService._send_willingness({"score": 0.3})
    assert 0.8 <= thread <= 0.85
    assert idle < thread
    assert 0.2 <= KookService._send_willingness(None) <= 0.5


# ---------------------------------------------------------------- 接入服务
class _Client:
    def __init__(self):
        self.sent = []

    def send_text(self, target_id, text, **kw):
        self.sent.append((target_id, text, kw))
        return {"ok": True}


class _LLM:
    def __init__(self, reply: str = '{"reply": "嗯", "voice": false}'):
        self.reply = reply
        self.seen: list[dict] = []

    def complete(self, messages, model=None):
        self.seen = messages
        return {"ok": True, "model": "fake", "text": self.reply}


def test_prompt_includes_life_block(tmp_path):
    cfg = LiveConfig(kook_memory=True, kook_allow_voice=False,
                     kook_stickers=False)
    memory = Memory(tmp_path / "kook.db")
    try:
        client, llm = _Client(), _LLM()
        service = KookService(cfg, memory, client, llm=llm, work_dir=tmp_path,
                              log_path=tmp_path / "events.jsonl")
        out = service.handle(Inbound(message_id="m1", target_id="c1",
                                     channel_type="GROUP", author_id="u1",
                                     author_name="小鱼", msg_type=1,
                                     text="今天有点累"))
        assert out["ok"] is True
        joined = "\n".join(str(item.get("content") or "") for item in llm.seen)
        assert "【你此刻的状态】" in joined
        assert "小鱼" in joined and "心情" in joined and "精力" in joined
        state = service._state()
        assert state is not None
        assert state._data["peers"]["u1"]["turns"] == 1
        assert (tmp_path / "companion.json").is_file()
    finally:
        memory.close()


def test_goodbye_loop_stays_silent(tmp_path):
    cfg = LiveConfig(kook_memory=True, kook_allow_voice=False,
                     kook_stickers=False)
    memory = Memory(tmp_path / "kook.db")
    memory.add_turn("assistant", "晚安，明天见", "", "")
    try:
        class BoomLLM:
            def complete(self, *args, **kwargs):
                raise AssertionError("已经道过晚安，不该再叫模型")

        client = _Client()
        service = KookService(cfg, memory, client, llm=BoomLLM(),
                              work_dir=tmp_path, log_path=tmp_path / "events.jsonl")
        out = service.handle(Inbound(message_id="m2", target_id="u1",
                                     channel_type="PERSON", author_id="u1",
                                     msg_type=1, text="晚安"))
        assert out["action"] == "silence"
        assert client.sent == []
        # 用户这句仍进记忆，第二天还能接着聊。
        assert memory.recent(1)[0]["text"] == "晚安"
        rows = [json.loads(line) for line in
                (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        assert any(row["kind"] == "silent_goodbye" for row in rows)
    finally:
        memory.close()


def test_restart_restores_last_contact(tmp_path):
    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False,
                     kook_stickers=False)
    first = KookService(cfg, None, _Client(), llm=_LLM(), work_dir=tmp_path,
                        log_path=tmp_path / "events.jsonl")
    first.handle(Inbound(message_id="m1", target_id="u1",
                         channel_type="PERSON", author_id="u1",
                         msg_type=1, text="在吗"))
    assert first._last_target == "u1"

    # 新进程（模拟重启）不该退回「从没聊过」，否则永远等对方先开口。
    second = KookService(cfg, None, _Client(), llm=_LLM(), work_dir=tmp_path,
                         log_path=tmp_path / "events.jsonl")
    assert second._last_target == "u1"
    assert second._last_peer_id == "u1"
    assert second._proactive_armed is True
    assert second._last_active_at <= time.time()


def test_service_returning_message_clears_goodnight(tmp_path):
    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False,
                     kook_stickers=False)
    service = KookService(cfg, None, _Client(), llm=_LLM(), work_dir=tmp_path,
                          log_path=tmp_path / "events.jsonl")
    service.handle(Inbound(message_id="m1", target_id="u1",
                           channel_type="PERSON", author_id="u1",
                           msg_type=1, text="晚安"))
    assert service._goodnight_at > 0
    service.handle(Inbound(message_id="m2", target_id="u1",
                           channel_type="PERSON", author_id="u1",
                           msg_type=1, text="睡不着，又起来了"))
    assert service._goodnight_at == 0


def test_merge_followups_joins_quick_messages(tmp_path):
    cfg = LiveConfig(kook_memory=False, kook_merge_seconds=1,
                     kook_allow_voice=False, kook_stickers=False)
    service = KookService(cfg, None, _Client(), work_dir=tmp_path,
                          log_path=tmp_path / "events.jsonl")
    first = Inbound(message_id="m1", target_id="c1", channel_type="GROUP",
                    author_id="u1", msg_type=1, text="跟你说")
    second = Inbound(message_id="m2", target_id="c1", channel_type="GROUP",
                     author_id="u1", msg_type=1, text="我今天面试过了")
    third = Inbound(message_id="m3", target_id="c1", channel_type="GROUP",
                    author_id="u2", msg_type=1, text="我也说一句")
    service._queue.put_nowait(second)
    service._queue.put_nowait(third)

    merged = service._merge_followups(first)
    assert merged.text == "跟你说 我今天面试过了"
    # 别人的消息不并进来，放回队列下一轮正常处理。
    assert service._queue.qsize() == 1
    assert service._queue.get_nowait().author_id == "u2"
    service._queue.task_done()


def test_typing_pace_between_split_messages(tmp_path):
    cfg = LiveConfig(kook_memory=False, kook_allow_voice=False,
                     kook_stickers=False)
    client = _Client()
    service = KookService(cfg, None, client, work_dir=tmp_path,
                          log_path=tmp_path / "events.jsonl")
    delays: list[float] = []
    service._sleep = delays.append
    msg = Inbound(message_id="m1", target_id="c1", channel_type="GROUP",
                  author_id="u1", msg_type=1, text="在吗")
    assert service._send_texts(msg, ["在", "刚看到一只猫", "像你"]) is True
    assert [text for _, text, _ in client.sent] == ["在", "刚看到一只猫", "像你"]
    assert len(delays) == 2 and all(0 < gap <= 0.9 for gap in delays)
