"""陪伴状态：心情、精力、亲近度、未完话头、重要日期和主动开口的理由。

KOOK 私聊不是一个问答窗口。这里维护一份只存本机的「她此刻的状态」：

- **自我状态**：心情、精力。聊久了会累，安静一阵会缓过来；对方的话会影响心情。
- **关系**：每个人认识多久、聊过多少次、熟到什么程度。
- **未完话头**：对方说「明天面试」「晚点告诉你」这类还没结果的事，记下来；
  到期后作为主动开口的理由，问过一次就收起来。
- **重要日期**：对方明确说出的生日 / 纪念日，按年重复，临近时提醒。
- **主动候选**：把上面这些变成带分数、带理由的候选，服务不再随机问候；
  时间窗覆盖早上、傍晚和深夜，避免只在深夜才想起找人。

这里只做「状态」和「理由」，不生成句子——回什么仍然由模型决定。
数据只写 ``logs/kook/companion.json``（默认），不联网、不上传。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from .. import paths

SCHEMA_VERSION = 1

# 一屏能看懂的标签；提示词里只用这些词，不把数值交给模型。
_MOOD_LABELS = (
    (0.75, "心情很好"),
    (0.60, "心情不错"),
    (0.45, "挺平静"),
    (0.30, "有点闷"),
    (0.00, "心情低落"),
)
_ENERGY_LABELS = (
    (0.75, "精神很好"),
    (0.55, "还撑得住"),
    (0.35, "有点累"),
    (0.00, "快没电了"),
)
_CLOSENESS_LABELS = (
    (70.0, "很亲近的人"),
    (45.0, "聊得来的熟人"),
    (25.0, "渐渐熟起来的人"),
    (10.0, "刚认识不久的人"),
    (0.0, "刚认识的人"),
)

_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

_PUNCT = "，。！？、；：,.!?;:\"'“”‘’（）()【】[]{}<>《》 \t\n"

# 语气词：只做很轻的判断，不做心理分析。
_POSITIVE = (
    "开心", "高兴", "好耶", "太好了", "太棒", "喜欢", "谢谢", "爱你", "顺利",
    "过了", "成了", "成功", "哈哈", "嘿嘿", "舒服", "轻松", "惊喜", "中奖",
    "录用", "通过", "搞定", "赢了",
)
_NEGATIVE = (
    "累", "困", "烦", "难受", "难过", "哭", "生气", "吵架", "加班", "失眠",
    "焦虑", "压力", "生病", "疼", "失恋", "委屈", "孤单", "害怕", "紧张",
    "倒霉", "被骂", "没钱", "挂科", "崩溃", "绝望", "撑不住",
)
_STRONG_NEGATIVE = ("崩溃", "绝望", "分手", "撑不住", "想哭", "难受死")

# 道别：短句、没有问句，且只在一来一回已经收过尾时才用来判断沉默。
_GOODBYE = (
    "晚安", "拜拜", "再见", "明天见", "睡了", "去睡", "我睡", "下线",
    "先走", "回头聊", "改天聊", "先这样", "我撤了",
)

# 未来的时间线索：命中后才值得记成「未完话头」。
_TIME_CUE = re.compile(
    r"(明天(?:早上|上午|中午|下午|晚上)?|明早|明晚|"
    r"后天(?:早上|上午|中午|下午|晚上)?|大后天|"
    r"今晚|今天晚上|周末|下(?:周|星期)[一二三四五六日天]?|"
    r"等(?:一)?会(?:儿)?|待(?:一)?会(?:儿)?|一会儿|晚点|晚些|"
    r"回头|下班|放学|睡醒|过两天|之后|以后)"
)
_DATE_MD = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_NICKNAME = re.compile(
    r"叫我\s*([\u4e00-\u9fa5A-Za-z0-9_]{1,6}?)(?:就好|就行|吧|啦|呀|[，,。.!！]|$)")
_DATE_KEYS = ("生日", "纪念日", "认识", "在一起", "告白", "第一次见")
_DATE_LABEL = {
    "生日": "生日",
    "纪念日": "纪念日",
    "认识": "认识纪念日",
    "在一起": "在一起纪念日",
    "告白": "告白纪念日",
    "第一次见": "第一次见面纪念日",
}


# ---------------------------------------------------------------- 小工具
def _clean(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def _norm(text: str) -> str:
    return "".join(ch for ch in _clean(text) if ch not in _PUNCT)


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _midnight(ts: float) -> float:
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def _at(ts: float, days: int, hour: int, minute: int = 0) -> float:
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + int(days),
                        int(hour), int(minute), 0, 0, 0, -1))


def _weekday(ts: float) -> int:
    """0 = 周一 … 6 = 周日。"""
    return int(time.localtime(ts).tm_wday)


def _days_since(first: float, now: float) -> int:
    return max(0, int(round((_midnight(now) - _midnight(first)) / 86400.0))) + 1


def mood_label(mood: float) -> str:
    value = max(0.0, min(1.0, float(mood or 0.0)))
    for floor, label in _MOOD_LABELS:
        if value >= floor:
            return label
    return _MOOD_LABELS[-1][1]


def energy_label(energy: float) -> str:
    value = max(0.0, min(1.0, float(energy or 0.0)))
    for floor, label in _ENERGY_LABELS:
        if value >= floor:
            return label
    return _ENERGY_LABELS[-1][1]


def closeness_label(value: float) -> str:
    score = max(0.0, min(100.0, float(value or 0.0)))
    for floor, label in _CLOSENESS_LABELS:
        if score >= floor:
            return label
    return _CLOSENESS_LABELS[-1][1]


def is_goodbye(text: str) -> bool:
    """是不是一句纯收尾。带问句、太长、或者还夹着新话题都不算。"""
    body = _clean(text)
    if not body or len(body) > 16:
        return False
    if any(mark in body for mark in ("?", "？", "吗", "呢")):
        return False
    rest = body
    for word in _GOODBYE:
        rest = rest.replace(word, "")
    rest = rest.strip("，,。.！!~～ 啦呀哦啊嗯好行吧去我了先那")
    return len(rest) <= 3


def goodnight_block_active(goodnight_at: float, now: float) -> bool:
    """道别后的静默期：夜里到第二天 9 点，白天小睡只算 90 分钟。

    以前不管几点说“晚安”都压 8 小时，下午打个盹就会把整个傍晚的主动消息
    一起吞掉；而且对方回来聊天也不会解除。现在按道别的时间点算，并且
    ``Companion.note_user()`` 会在对方下一条普通消息里清掉这个标记。
    """
    try:
        said = float(goodnight_at or 0.0)
    except (TypeError, ValueError):
        return False
    if said <= 0:
        return False
    lt = time.localtime(said)
    hour = lt.tm_hour + lt.tm_min / 60.0
    if hour >= 19.0:
        end = _at(said, 1, 9, 0)          # 晚上道别 → 第二天早上 9 点
    elif hour < 5.0:
        end = _at(said, 0, 9, 0)          # 凌晨道别 → 当天早上 9 点
    else:
        end = said + 90 * 60              # 白天打盹 → 一个半小时
    return float(now) < end


def parse_quiet_hours(spec: str) -> tuple[int, int, int, int]:
    """``00:30-07:30`` → ``(0, 30, 7, 30)``；支持 ``~`` 和中文横线。"""
    text = str(spec or "").strip().replace("～", "-").replace("~", "-").replace("—", "-")
    if not text:
        raise ValueError("免打扰时段不能为空")
    match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", text)
    if not match:
        raise ValueError("免打扰时段要写成 00:30-07:30")
    sh, sm, eh, em = (int(x) for x in match.groups())
    if not (0 <= sh <= 23 and 0 <= eh <= 23 and 0 <= sm <= 59 and 0 <= em <= 59):
        raise ValueError("免打扰时段超出 00:00–23:59")
    return sh, sm, eh, em


def in_quiet(now: float, spec: str) -> bool:
    """现在是不是免打扰时段；空串 / 写错都当没限制（不挡聊天）。"""
    try:
        sh, sm, eh, em = parse_quiet_hours(spec)
    except (TypeError, ValueError):
        return False
    lt = time.localtime(now)
    cur = int(lt.tm_hour) * 60 + int(lt.tm_min)
    start = sh * 60 + sm
    end = eh * 60 + em
    if start == end:
        return False
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


def quiet_end(now: float, spec: str) -> float:
    """免打扰结束的时间戳；写错就返回 now（相当于没限制）。"""
    try:
        _sh, _sm, eh, em = parse_quiet_hours(spec)
    except (TypeError, ValueError):
        return float(now)
    end = _at(now, 0, eh, em)
    if end <= now:
        end = _at(now, 1, eh, em)
    return end


def _sentence(text: str, start: int, end: int, limit: int = 48) -> str:
    """把命中时间词的那一句话抠出来（不带前后句）。"""
    left = 0
    for mark in "。！？!?；;\n":
        pos = text.rfind(mark, 0, start)
        if pos + 1 > left:
            left = pos + 1
    right = len(text)
    for mark in "。！？!?；;\n":
        pos = text.find(mark, end)
        if pos != -1 and pos < right:
            right = pos
    return text[left:right].strip("，,、 ")[:limit]


def _due_at(now: float, cue: str) -> float:
    """把「明天 / 等会 / 周末」换成一个大概该问问结果的时间。"""
    text = str(cue or "")
    if text.startswith("明天"):
        if "早上" in text or "上午" in text:
            return _at(now, 1, 9, 30)
        if "中午" in text:
            return _at(now, 1, 13, 0)
        if "下午" in text:
            return _at(now, 1, 17, 30)
        if "晚上" in text:
            return _at(now, 1, 20, 30)
        return _at(now, 1, 19, 30)
    if text in ("明早",):
        return _at(now, 1, 9, 30)
    if text in ("明晚",):
        return _at(now, 1, 20, 30)
    if text.startswith("大后天"):
        return _at(now, 3, 19, 30)
    if text.startswith("后天"):
        return _at(now, 2, 19, 30)
    if text in ("今晚", "今天晚上"):
        target = _at(now, 0, 21, 30)
        return target if target > now + 1800 else _at(now, 1, 9, 30)
    if text == "周末":
        target = _at(now, 0, 10, 30)
        if target > now:
            return target
        # 本周六 10:30；如果今天已经是周六/周日且过了，就顺延到下一个周六。
        delta = (5 - _weekday(now)) % 7 or 7
        return _at(now, delta, 10, 30)
    if text.startswith(("下周", "下星期")):
        target_name = text[-1]
        index = "一二三四五六日天".find(target_name)
        if index < 0:
            return _at(now, 7 - _weekday(now), 19, 30)      # 只说了「下周」
        target = 6 if index == 6 else index
        delta = (7 - _weekday(now)) + target
        return _at(now, delta, 19, 30)
    if text in ("等会", "等一会儿", "等一会", "待会", "待会儿", "待一会", "一会儿", "晚点", "晚些"):
        return now + 2 * 3600
    if text in ("下班", "放学"):
        target = _at(now, 0, 18, 30)
        return target if target > now else _at(now, 1, 18, 30)
    if text == "睡醒":
        return _at(now, 1, 9, 0)
    if text in ("过两天",):
        return _at(now, 2, 20, 0)
    return now + 6 * 3600                                   # 回头 / 之后 / 以后


def _nickname(text: str) -> str:
    """从「叫我阿鱼就行」里取一个称呼；明显是拒绝（别叫我…）就不算。"""
    body = _clean(text)
    if "别叫我" in body or "不要叫我" in body:
        return ""
    match = _NICKNAME.search(body)
    if not match:
        return ""
    value = match.group(1).strip()
    if value in ("一声", "名字", "什么", "这个", "那个"):
        return ""
    return value


def _sentiment(text: str) -> tuple[float, str]:
    """返回 ``(目标心情, 一个短理由)``；没有明显语气就返回 ``(-1, "")``。"""
    body = _clean(text)
    if len(body) < 2:
        return -1.0, ""
    if any(word in body for word in _STRONG_NEGATIVE):
        return 0.15, "对方情绪很重"
    positive = sum(1 for word in _POSITIVE if word in body)
    negative = sum(1 for word in _NEGATIVE if word in body)
    if positive and negative:
        return 0.45, "对方又开心又累"
    if negative:
        return 0.28, "对方今天不太好受"
    if positive:
        return 0.78, "对方遇到好事"
    return -1.0, ""


class Companion:
    """一份本地陪伴状态；线程安全，读写都落在同一个 JSON 文件。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else paths.LOGS / "kook" / "companion.json"
        self._lock = threading.RLock()
        self._data = self._load()

    # ---------------------------------------------------------------- 存取
    def _empty(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "self": {"mood": 0.55, "energy": 0.7, "updated": 0.0, "mood_reason": ""},
            "peers": {},
            "threads": [],
            "dates": [],
            "meta": {},
        }

    def _load(self) -> dict:
        base = self._empty()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return base
        if not isinstance(raw, dict):
            return base
        self_state = raw.get("self")
        if isinstance(self_state, dict):
            for key in ("mood", "energy", "updated"):
                try:
                    base["self"][key] = float(self_state.get(key, base["self"][key]))
                except (TypeError, ValueError):
                    pass
            base["self"]["mood_reason"] = str(self_state.get("mood_reason") or "")[:80]
        for key in ("peers",):
            if isinstance(raw.get(key), dict):
                base[key] = {str(k): v for k, v in raw[key].items() if isinstance(v, dict)}
        for key in ("threads", "dates"):
            if isinstance(raw.get(key), list):
                base[key] = [row for row in raw[key] if isinstance(row, dict)]
        if isinstance(raw.get("meta"), dict):
            base["meta"] = dict(raw["meta"])
        # 重启后 id 要继续往上走，否则新话头可能和旧话头撞号。
        used = [int(row.get("id") or 0) for row in base["threads"]
                if str(row.get("id") or "").isdigit()]
        base["meta"]["thread_seq"] = max(int(base["meta"].get("thread_seq") or 0),
                                          max(used, default=0))
        return base

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass          # 状态写不进去也不能影响聊天

    # ---------------------------------------------------------------- 时间流逝
    def decay(self, now: float | None = None) -> None:
        """安静一段时间后精力回一点、心情慢慢回到平静。"""
        now = float(now if now is not None else time.time())
        with self._lock:
            sel = self._data["self"]
            last = float(sel.get("updated") or 0.0)
            hours = min(72.0, max(0.0, (now - last) / 3600.0)) if last else 0.0
            if hours >= 0.5:
                sel["energy"] = min(1.0, float(sel.get("energy") or 0.7) + 0.035 * hours)
                mood = float(sel.get("mood") or 0.55)
                sel["mood"] = mood + (0.55 - mood) * min(0.6, 0.04 * hours)
                sel["updated"] = now
                sel["mood_reason"] = ""
                self._save_locked()

    # ---------------------------------------------------------------- 关系
    def touch(self, peer_id: str, name: str = "", now: float | None = None) -> dict:
        """有人来说话：记一次见面、熟一点、算连续聊天天数。"""
        peer = str(peer_id or "").strip()
        now = float(now if now is not None else time.time())
        if not peer:
            return {}
        with self._lock:
            peers = self._data["peers"]
            p = peers.get(peer)
            if not isinstance(p, dict):
                p = {"name": "", "first_seen": now, "last_seen": now, "turns": 0,
                     "closeness": 0.0, "streak": 0, "last_day": "", "turns_today": 0,
                     "goodnight_at": 0.0}
                peers[peer] = p
            if name:
                p["name"] = str(name)[:40]
            today = _day(now)
            previous = str(p.get("last_day") or "")
            if previous != today:
                p["turns_today"] = 0
                if previous == _day(now - 86400):
                    p["streak"] = int(p.get("streak") or 0) + 1
                else:
                    p["streak"] = 1
                p["last_day"] = today
            closeness = float(p.get("closeness") or 0.0)
            gain = 0.55 * (1.0 - closeness / 100.0)
            if int(p.get("turns") or 0) == 0:
                gain += 1.0
            p["closeness"] = min(100.0, closeness + gain)
            p["turns"] = int(p.get("turns") or 0) + 1
            p["turns_today"] = int(p.get("turns_today") or 0) + 1
            p["last_seen"] = now
            self._save_locked()
            return dict(p)

    # ---------------------------------------------------------------- 对方的话
    def note_user(self, peer_id: str, text: str, now: float | None = None) -> dict:
        """读一句对方的话：影响心情，顺手记下没结果的事和重要日期。"""
        peer = str(peer_id or "").strip()
        body = _clean(text)
        now = float(now if now is not None else time.time())
        result = {"goodnight": False, "threads": 0, "dates": 0}
        if not peer:
            return result
        with self._lock:
            if peer not in self._data["peers"]:
                return result
            sel = self._data["self"]
            target, reason = _sentiment(body)
            if target >= 0:
                mood = float(sel.get("mood") or 0.55)
                sel["mood"] = max(0.0, min(1.0, mood + (target - mood) * 0.4))
                sel["mood_reason"] = reason
            else:
                mood = float(sel.get("mood") or 0.55)
                sel["mood"] = mood + (0.55 - mood) * 0.04
            sel["updated"] = now
            if body:
                p = self._data["peers"][peer]
                nick = _nickname(body)
                if nick:
                    p["nickname"] = nick
            if body and is_goodbye(body):
                p = self._data["peers"][peer]
                p["goodnight_at"] = now
                sel["energy"] = max(0.15, float(sel.get("energy") or 0.7) - 0.05)
                result["goodnight"] = True
            elif body and p.get("goodnight_at"):
                # 对方又回来说话了：之前的晚安不该继续挡主动消息。
                p["goodnight_at"] = 0.0
            if body:
                result["threads"] = self._add_threads(peer, body, now)
                result["dates"] = self._add_dates(peer, body, now)
            self._save_locked()
        return result

    def note_reply(self, peer_id: str, texts: list[str] | tuple[str, ...],
                   now: float | None = None) -> None:
        """自己回过话：精力掉一点，亲近度再涨一点；深夜更耗神。"""
        peer = str(peer_id or "").strip()
        now = float(now if now is not None else time.time())
        with self._lock:
            p = self._data["peers"].get(peer)
            if not isinstance(p, dict):
                return
            count = max(1, len([t for t in (texts or ()) if str(t).strip()]))
            cost = 0.02 + 0.008 * min(4, count)
            if int(time.localtime(now).tm_hour) in (0, 1, 2, 3, 4, 5):
                cost += 0.03
            sel = self._data["self"]
            sel["energy"] = max(0.1, float(sel.get("energy") or 0.7) - cost)
            sel["updated"] = now
            p["closeness"] = min(100.0, float(p.get("closeness") or 0.0) + 0.15)
            self._save_locked()

    # ---------------------------------------------------------------- 话头 / 日期
    def _add_threads(self, peer: str, body: str, now: float) -> int:
        added = 0
        for match in _TIME_CUE.finditer(body):
            cue = match.group(0)
            clause = _sentence(body, match.start(), match.end())
            if not clause:
                continue
            if len(_norm(clause)) - len(_norm(cue)) < 2:
                continue                      # 「明天见」这种不算话头
            if is_goodbye(clause):
                continue
            if cue in ("下班", "放学") and ("了" in clause or "刚" in clause):
                continue                      # 「我下班了」已经是结果
            if "再说" in clause or "看情况" in clause:
                continue
            if any(not row.get("done") and self._overlap(peer, row, clause)
                   for row in self._data["threads"]):
                continue
            seq = int(self._data["meta"].get("thread_seq") or 0) + 1
            self._data["meta"]["thread_seq"] = seq
            self._data["threads"].append({
                "id": seq,
                "peer": peer,
                "text": clause,
                "due_at": max(now + 1800, _due_at(now, cue)),
                "created_at": now,
                "asked": 0,
                "done": False,
            })
            added += 1
        if added:
            self._prune_threads(now)
        return added

    def _overlap(self, peer: str, row: dict, clause: str) -> bool:
        if str(row.get("peer") or "") != peer:
            return False
        left = _norm(row.get("text") or "")
        right = _norm(clause)
        return bool(left and right and (left in right or right in left))

    def _prune_threads(self, now: float) -> None:
        keep: list[dict] = []
        per_peer: dict[str, int] = {}
        for row in sorted(self._data["threads"],
                          key=lambda r: float(r.get("created_at") or 0), reverse=True):
            peer = str(row.get("peer") or "")
            done = bool(row.get("done")) or int(row.get("asked") or 0) > 0
            created = float(row.get("created_at") or 0)
            if not done and created < now - 7 * 86400:
                done = True
            if done and created < now - 14 * 86400:
                continue
            count = per_peer.get(peer, 0)
            if not done and count >= 8:
                continue
            per_peer[peer] = count + 1
            keep.append(row)
        self._data["threads"] = keep[-80:]

    def _add_dates(self, peer: str, body: str, now: float) -> int:
        added = 0
        for match in _DATE_MD.finditer(body):
            month, day = int(match.group(1)), int(match.group(2))
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue
            window = body[max(0, match.start() - 14):match.end() + 14]
            key = next((word for word in _DATE_KEYS if word in window), "")
            if not key:
                continue
            label = _DATE_LABEL[key]
            if any(str(row.get("peer")) == peer and int(row.get("month") or 0) == month
                   and int(row.get("day") or 0) == day and row.get("label") == label
                   for row in self._data["dates"]):
                continue
            self._data["dates"].append({
                "peer": peer, "month": month, "day": day, "label": label,
                "created_at": now, "last_greeted": "",
            })
            added += 1
        if added:
            self._data["dates"] = self._data["dates"][-60:]
        return added

    def remember_contact(self, peer_id: str, target_id: str,
                         channel_type: str, now: float | None = None) -> None:
        """记住最近聊过的人和发送目标；重启后不用等对方先开口。"""
        now = float(now if now is not None else time.time())
        with self._lock:
            self._data["meta"]["contact"] = {
                "peer_id": str(peer_id or ""),
                "target_id": str(target_id or ""),
                "channel_type": str(channel_type or ""),
                "at": now,
            }
            self._save_locked()

    def last_contact(self) -> dict:
        with self._lock:
            row = self._data["meta"].get("contact")
            return dict(row) if isinstance(row, dict) else {}

    def mark_thread_asked(self, thread_id) -> None:
        try:
            target = int(thread_id)
        except (TypeError, ValueError):
            return
        with self._lock:
            for row in self._data["threads"]:
                if int(row.get("id") or 0) == target:
                    row["asked"] = int(row.get("asked") or 0) + 1
                    row["done"] = True
                    row["asked_at"] = time.time()
                    self._save_locked()
                    return

    def mark_date_greeted(self, peer_id: str, month: int, day: int,
                          now: float | None = None) -> None:
        now = float(now if now is not None else time.time())
        with self._lock:
            for row in self._data["dates"]:
                if (str(row.get("peer")) == str(peer_id) and int(row.get("month") or 0) == int(month)
                        and int(row.get("day") or 0) == int(day)):
                    row["last_greeted"] = _day(now)
                    self._save_locked()
                    return

    # ---------------------------------------------------------------- 提示词
    def prompt_block(self, peer_id: str, now: float | None = None) -> str:
        """当下状态的人话版本；只给模型把握语气，不要求它汇报。"""
        peer = str(peer_id or "").strip()
        now = float(now if now is not None else time.time())
        self.decay(now)
        with self._lock:
            sel = dict(self._data["self"])
            p = self._data["peers"].get(peer) or {}
            lines = [
                "【你此刻的状态】（这是你自己的，不是对方的。用来把握语气，"
                "不要逐条汇报，也不要说数字、不要说“状态值”。）",
                "- 现在是 " + self._clock_line(now) + "。",
            ]
            mood_text = mood_label(sel.get("mood") or 0.55)
            reason = str(sel.get("mood_reason") or "").strip()
            if reason:
                mood_text += f"（{reason}）"
            energy_text = energy_label(sel.get("energy") or 0.7)
            extra = ""
            if int(p.get("turns_today") or 0) >= 8:
                extra = "，今天聊得有点久"
            lines.append(f"- 你的心情：{mood_text}；精力：{energy_text}{extra}。")
            name = str(p.get("name") or "").strip()
            nick = str(p.get("nickname") or "").strip()
            if p:
                days = _days_since(float(p.get("first_seen") or now), now)
                turns = int(p.get("turns") or 0)
                label = closeness_label(p.get("closeness") or 0.0)
                who = f"对方是 {name}" if name else ""
                if nick:
                    who += (f"，你平时叫 ta「{nick}」" if who
                            else f"对方希望你叫 ta「{nick}」")
                if who:
                    who += "；"
                if days <= 1:
                    lines.append(f"- {who}你们今天才第一次说上话，还不熟，别装得很亲近。")
                else:
                    lines.append(f"- {who}你们认识了 {days} 天，聊过 {turns} 次，"
                                 f"现在是「{label}」。")
                goodnight = float(p.get("goodnight_at") or 0.0)
                if goodnight_block_active(goodnight, now):
                    lines.append("- 对方刚跟你道过晚安，别再去打扰。")
            threads = self._due_threads(peer, now)
            if threads:
                lines.append("- 你心里还记着的事：")
                for row in threads[:2]:
                    due = float(row.get("due_at") or 0)
                    hint = "（可以问问后来怎么样了）" if due <= now else "（还没到日子）"
                    lines.append(f"  · 对方说过「{str(row.get('text') or '')[:40]}」{hint}")
            dates = self._upcoming_dates(peer, now, days=14)
            if dates:
                lines.append("- 快到的日子：")
                for row in dates[:2]:
                    label = str(row.get("label") or "重要日子")
                    when = f"{int(row['month'])} 月 {int(row['day'])} 日"
                    left = int(row["days"])
                    tail = "就是今天" if left <= 0 else f"还有 {left} 天"
                    lines.append(f"  · {when}是{nick or name or '对方'}的{label}（{tail}）")
            lines.append("- 上面这些只影响你怎么接话，不用主动提；对方问起时再自然地说。")
            return "\n".join(lines)

    @staticmethod
    def _clock_line(now: float) -> str:
        lt = time.localtime(now)
        hour = int(lt.tm_hour)
        if hour < 5:
            period = "凌晨"
        elif hour < 9:
            period = "早上"
        elif hour < 12:
            period = "上午"
        elif hour < 14:
            period = "中午"
        elif hour < 18:
            period = "下午"
        elif hour < 19:
            period = "傍晚"
        else:
            period = "晚上"
        return (f"{lt.tm_mon} 月 {lt.tm_mday} 日 {_WEEKDAYS[lt.tm_wday]} "
                f"{period} {hour:02d}:{int(lt.tm_min):02d}")

    def _due_threads(self, peer: str, now: float) -> list[dict]:
        rows = [
            row for row in self._data["threads"]
            if str(row.get("peer") or "") == peer and not row.get("done")
            and int(row.get("asked") or 0) == 0
            and now - 3 * 86400 <= float(row.get("due_at") or 0) <= now + 3 * 86400
        ]
        rows.sort(key=lambda row: float(row.get("due_at") or 0))
        return rows

    def _upcoming_dates(self, peer: str, now: float, days: int = 14) -> list[dict]:
        out: list[dict] = []
        for row in self._data["dates"]:
            if str(row.get("peer") or "") != peer:
                continue
            found = self._next_occurrence(int(row.get("month") or 0),
                                          int(row.get("day") or 0), now)
            if found is None:
                continue
            left = found
            if left <= days:
                out.append({**row, "days": left})
        out.sort(key=lambda row: row["days"])
        return out

    @staticmethod
    def _next_occurrence(month: int, day: int, now: float) -> int | None:
        base = _midnight(now)
        for year in (time.localtime(now).tm_year, time.localtime(now).tm_year + 1):
            try:
                target = time.mktime((year, month, day, 0, 0, 0, 0, 0, -1))
            except (OverflowError, ValueError):
                return None
            if target >= base:
                return int(round((target - base) / 86400.0))
        return None

    # ---------------------------------------------------------------- 主动候选
    def candidate(self, peer_id: str, now: float, idle_minutes: int) -> dict:
        """现在开口的话，最像样的理由是什么；没有就像随机问候。"""
        peer = str(peer_id or "").strip()
        now = float(now)
        with self._lock:
            p = self._data["peers"].get(peer) or {}
            goodnight = float(p.get("goodnight_at") or 0.0)
            if goodnight_block_active(goodnight, now):
                return {"blocked": "after_goodnight"}
            threads = self._due_threads(peer, now)
            for row in threads:
                if float(row.get("due_at") or 0) <= now:
                    return {
                        "kind": "thread",
                        "score": 0.95,
                        "thread_id": row.get("id"),
                        "reason": (f"你之前记着对方说过「{str(row.get('text') or '')[:40]}」，"
                                   "现在差不多可以问问结果了；别像查岗。"),
                    }
            dates = self._upcoming_dates(peer, now, days=3)
            for row in dates:
                if str(row.get("last_greeted") or "") == _day(now):
                    continue
                left = int(row["days"])
                when = "今天" if left <= 0 else f"还有 {left} 天"
                return {
                    "kind": "date",
                    "score": 0.82,
                    "month": int(row.get("month") or 0),
                    "day": int(row.get("day") or 0),
                    "reason": (f"{when}就是对方（{p.get('name') or '对方'}）的"
                               f"{row.get('label')}，可以自然提一句；别刻意煽情。"),
                }
            hour = int(time.localtime(now).tm_hour) + int(time.localtime(now).tm_min) / 60.0
            today = _day(now)
            if 7.5 <= hour < 10.5 and str(p.get("last_day") or "") != today:
                return {
                    "kind": "morning",
                    "score": 0.72,
                    "reason": "新的一天，你刚醒，想跟对方说句话；问得轻一点，别像打卡。",
                }
            if 18.5 <= hour < 21.5:
                talked = str(p.get("last_day") or "") == today
                return {
                    "kind": "evening",
                    "score": 0.55,
                    "reason": ("一天快过去了，你想问问对方今天过得怎么样；"
                               + ("别问得像打卡，也别追问。" if talked
                                  else "你们今天还没怎么说话，语气轻一点。")),
                }
            if (hour >= 21.5 or hour < 0.5) and str(p.get("last_day") or "") == today:
                return {
                    "kind": "night",
                    "score": 0.6,
                    "reason": "夜深了，对方今天跟你说过话；你想关照一句，不要催对方睡觉。",
                }
            mood = float(self._data["self"].get("mood") or 0.55)
            if mood <= 0.35:
                return {
                    "kind": "low",
                    "score": 0.52,
                    "reason": "你这两天有点闷，想找对方说说话，但不要一开口就倒情绪。",
                }
            if idle_minutes >= 360 and hour >= 11:
                return {
                    "kind": "share",
                    "score": 0.42,
                    "reason": "有一阵没聊了，你想分享一件最近记着的小事；没有就算了。",
                }
            return {
                "kind": "idle",
                "score": 0.3,
                "reason": "有一阵没说话了。没有具体的由头，就别硬找话题。",
            }

    # ---------------------------------------------------------------- 诊断
    def stats(self, now: float | None = None) -> dict:
        """设置页 / 控制台看的只读快照；不含聊天正文。"""
        now = float(now if now is not None else time.time())
        with self._lock:
            sel = dict(self._data["self"])
            peers = []
            for key, row in self._data["peers"].items():
                peers.append({
                    "id": key,
                    "name": str(row.get("nickname") or row.get("name") or ""),
                    "nickname": str(row.get("nickname") or ""),
                    "closeness": round(float(row.get("closeness") or 0.0), 1),
                    "label": closeness_label(row.get("closeness") or 0.0),
                    "days": _days_since(float(row.get("first_seen") or now), now),
                    "turns": int(row.get("turns") or 0),
                    "streak": int(row.get("streak") or 0),
                })
            peers.sort(key=lambda row: row["closeness"], reverse=True)
            threads = [
                {"text": str(row.get("text") or "")[:40],
                 "due_at": float(row.get("due_at") or 0)}
                for row in self._data["threads"]
                if not row.get("done") and int(row.get("asked") or 0) == 0
            ]
            dates = []
            for row in self._data["dates"]:
                found = self._next_occurrence(int(row.get("month") or 0),
                                              int(row.get("day") or 0), now)
                if found is not None and found <= 60:
                    dates.append({"label": str(row.get("label") or ""),
                                  "month": int(row.get("month") or 0),
                                  "day": int(row.get("day") or 0),
                                  "days": found})
            dates.sort(key=lambda row: row["days"])
            return {
                "updated": float(sel.get("updated") or 0.0),
                "mood": round(float(sel.get("mood") or 0.55), 2),
                "mood_label": mood_label(sel.get("mood") or 0.55),
                "energy": round(float(sel.get("energy") or 0.7), 2),
                "energy_label": energy_label(sel.get("energy") or 0.7),
                "peers": peers[:8],
                "threads": threads[:8],
                "dates": dates[:8],
            }


__all__ = [
    "Companion", "SCHEMA_VERSION", "closeness_label", "energy_label",
    "goodnight_block_active", "in_quiet", "is_goodbye", "mood_label",
    "parse_quiet_hours", "quiet_end",
]
