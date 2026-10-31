"""B 站云观影的模块级运行器。

生命周期故意和 toy-duel/STS2 一样：模块级 host/thread，`agent.play` 每次
控制时新建 wrapper 也能找到同一局。B 站专属逻辑都在本包；核心只收到
状态、日志、token 和停止信号。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from dafeiyu import paths
from dafeiyu.agent.host import AutoplayHost
from dafeiyu.core.config import LiveConfig

from .api import BiliClient, BiliError
from .browser import ChromeBrowser, start_from_zero
from .cache import Stores
from .config import CommentaryConfig, load_commentary_config
from . import control
from .liker import BiliLiker
from .live_chat import LiveChatResponder
from .models import (
    Comment, DanmakuResult, VideoDetail, VideoProfile, VideoRef,
    comment_drop_reason, is_big_fish_text, strip_danmaku_word,
)
from .persona_watch import PersonaWatch, load_bili_persona
from .profile import ProfileBuilder, ProfileError
from .prompts import SEARCH_SYSTEM, commentary_system
from .search_intent import SearchIntent, SearchIntentPlanner
from .schedule import (
    CueScheduler, ReplyScheduler, VirtualClock, build_plan,
)
from .video_comment import BiliVideoCommenter
from .select import select_candidates
from .speaker import OverlaySink, SpeechGate

GAME_ID = "bilibili-hot-commentary"
DISPLAY_NAME = "B站热门视频云观影"
# B站历史进度的刷新间隔约 15 秒；连续 30 秒没有前进就认为视频没在播放。
NOT_PLAYING_AFTER_S = 30.0

_host = AutoplayHost(GAME_ID)
_lock = _host.lock
_thread: threading.Thread | None = None
_engine: "BiliEngine | None" = None


def bind_host(host: AutoplayHost) -> AutoplayHost:
    global _host, _lock
    if _thread is not None and _thread.is_alive() and host is not _host:
        _host.log("bind_host_ignored", reason="commentary thread alive")
        return _host
    _host = host
    _lock = host.lock
    return host


class BiliEngine:
    def __init__(
        self,
        host: AutoplayHost,
        config: CommentaryConfig,
        *,
        client=None,
        stores: Stores | None = None,
        builder=None,
        sink=None,
        persona: PersonaWatch | None = None,
        search: SearchIntentPlanner | None = None,
        browser=None,
        liker=None,
        commenter=None,
        live_chat=None,
        now=None,
        control_root: str | Path | None = None,
        config_path: str | Path | None = None,
        wait_s: float = 30.0,
        sleep: float = 0.5,
    ) -> None:
        self.host = host
        self.config = config
        self.stores = stores or Stores()
        if client is None:
            try:
                cookie = LiveConfig.load().bilibili_cookie
            except Exception:
                cookie = ""
            self.client = BiliClient(
                timeout=8.0, retries=2, cookie=cookie,
                logger=self._client_log,
            )
        else:
            self.client = client
        self._now = now or time.monotonic
        self._wall = time.time
        self._control_root = Path(control_root) if control_root else paths.LOGS
        self._pause_path = self._control_root / "bilibili_commentary.pause"
        self._skip_path = self._control_root / "bilibili_commentary.skip"
        self._run_lock_path = self._control_root / "bilibili_commentary.run.lock"
        self._run_lock_fd: int | None = None
        self._config_path = Path(config_path) if config_path else paths.CONFIG_FILE
        self._config_mtime = self._mtime()
        # B 可以经 --game 覆盖启动，此时 config.json 里的 game 仍是别的模式；
        # 只有“相对启动时发生了变化”才代表用户切模式，不能拿它和 B 硬比。
        self._config_game = self._read_config_game()
        self.wait_s = max(0.05, float(wait_s))
        self.sleep_s = max(0.02, float(sleep))
        if sink is None:
            try:
                overlay = LiveConfig.load().overlay_url
            except Exception:
                overlay = "http://127.0.0.1:8730"
            sink = OverlaySink(
                overlay, prewarm=config.prewarm_tts,
                status={"idle_text": "看视频ing"})
        self.sink = sink
        self.speech = SpeechGate(
            sink, on_spoken=self._on_spoken, logger=self._client_log, now=self._now)
        self.persona = persona or load_bili_persona()
        self.builder = builder or ProfileBuilder(
            config, cache=self.stores.profiles, now=self._wall,
            persona=self.persona)
        if search is None and config.search_enabled:
            search = SearchIntentPlanner(
                config, self.persona, cache=self.stores.search_intents,
                planner=(None if config.search_keywords
                         else _make_search_planner(config)),
                logger=self._client_log,
            )
        self.search = search
        if browser is None and config.open_browser:
            browser = ChromeBrowser(
                exe=config.browser_path, logger=self._client_log,
                windowed=config.browser_window,
                isolated=config.browser_isolated,
                profile_dir=(config.browser_profile_dir or
                             self._control_root / "bilibili_commentary.chrome-profile"),
                fullscreen=config.browser_fullscreen,
                monitor=config.browser_monitor,
                state_path=self._control_root / "bilibili_commentary.chrome.json",
            )
        self.browser = browser
        adopt = getattr(self.browser, "adopt_legacy_window", None)
        if callable(adopt):
            try:
                adopt(self._control_root / "bilibili_commentary.playlist.json")
            except Exception:  # noqa: BLE001
                pass
        if liker is None and config.like_on_watch:
            try:
                cookie = LiveConfig.load().bilibili_cookie
            except Exception:
                cookie = ""
            liker = BiliLiker(cookie, logger=self._client_log)
        self.liker = liker
        self._like_skip_logged = False
        if commenter is None and config.video_comment_enabled:
            try:
                cookie = LiveConfig.load().bilibili_cookie
            except Exception:
                cookie = ""
            commenter = BiliVideoCommenter(cookie, logger=self._client_log)
        elif commenter is False:
            commenter = None
        self.commenter = commenter
        self._comment_skip_logged = False
        self._comment_inflight = False
        self._comment_day = ""
        self._comment_count = 0
        self._comment_last_at = 0.0
        self._commented_bvids: set[str] = set()
        self._comment_state_path = (
            self._control_root / "bilibili_video_comments.json")
        self._load_comment_state()
        self._browser_title_seen = False
        self._browser_hwnd_seen = False
        self._browser_newtab_since = 0.0
        self._history_error_logged = False
        self._live_title = ""
        self._live_same_fish = False
        self._live_send_skip_logged = False
        self._live_fail_logged = 0.0
        self.live_speech = None
        if config.live_reply_speak and (config.live_chat_enabled or live_chat):
            self.live_speech = SpeechGate(
                self.sink, on_spoken=self._on_spoken,
                logger=self._client_log, now=self._now)
        if live_chat is None and config.live_chat_enabled:
            live_chat = LiveChatResponder(
                config, self.persona,
                speak=(self._live_speak if self.live_speech is not None else None),
                send=self._live_send,
                usage=self._live_usage,
                context=self._live_context,
                logger=self._client_log,
            )
        self.live_chat = live_chat
        self._intent: SearchIntent | None = None
        self._stop_reason = ""
        self._local_stop = False
        self._video_count = 0
        self._cues_fired = 0
        self._replies_fired = 0
        self._cues_dropped = 0
        self._llm_calls = 0
        self._last_status = 0.0
        self._last_position = -1.0
        # 当前视频播放时就把下一个视频的详情/档案备好，避免结束后干等 LLM。
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: threading.Thread | None = None
        self._prefetch_result = None
        self._prefetch_gen = 0
        # 控制台播放列表/命令；文件通信，跨进程也能用。
        self._playlist_lock = threading.Lock()
        self._playlist_refs: list[VideoRef] = []
        self._playlist_current: dict = {}
        self._override_bvid = ""
        self._switching = False
        # 本轮从“大肥鱼”高播放量轨道拿到的候选。select_candidates 会按
        # 评分重排，这里记住 bvid，准备时把过关的大肥鱼提到第一条。
        self._priority_bvids: set[str] = set()

    # ---------------------------------------------------------------- 基础 I/O
    def _client_log(self, text: str) -> None:
        try:
            self.host.log("bili", message=str(text)[:240])
        except Exception:
            pass

    def _log(self, msg: str, **data) -> None:
        try:
            self.host.log(msg, **data)
        except Exception:
            pass

    def _on_spoken(self, text: str) -> None:
        try:
            self.host.remember_speech(text)
        except Exception:
            pass

    # ---------------------------------------------------------------- 直播弹幕
    def _live_context(self) -> dict:
        return {"current_video": {
            "title": self._live_title[:120],
            "same_fish": bool(self._live_same_fish)
            or is_big_fish_text(self._live_title),
        }}

    def _live_usage(self, usage: dict) -> None:
        total = 0
        try:
            total = int((usage or {}).get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        if total <= 0:
            return
        self._llm_calls += 1
        try:
            self.host.add_usage(
                usage, note="直播弹幕回复", step=self._video_count + 1)
        except Exception:
            pass

    def _live_speak(self, text: str) -> None:
        if self.live_speech is None:
            return
        if not self.live_speech.offer(text):
            self._log("live_chat_speech_dropped", reason="语音忙")

    def _live_send(self, text: str, event) -> None:
        """把回复发到自己的直播间；失败只记日志，不影响语音/视频。"""
        if not self.config.live_reply_send_danmu:
            return
        try:
            from dafeiyu.live.bilibili import send_danmu

            cfg = LiveConfig.load()
        except Exception as exc:  # noqa: BLE001
            self._log("live_chat_send_failed", error=f"{type(exc).__name__}: {exc}"[:160])
            return
        if not cfg.bilibili_enabled or int(cfg.bilibili_room_id or 0) <= 0 \
                or not str(cfg.bilibili_cookie or "").strip():
            if not self._live_send_skip_logged:
                self._live_send_skip_logged = True
                self._log("live_chat_send_skipped",
                          reason="直播间未启用/未配置房间号或 Cookie")
            return
        text = " ".join(str(text or "").split())
        limit = max(1, int(self.config.live_reply_danmu_chars))
        if len(text) > limit:
            text = text[:max(1, limit - 1)] + "…"
        if not text:
            return
        try:
            send_danmu(int(cfg.bilibili_room_id), text, cfg.bilibili_cookie)
            self._log("live_chat_send_ok", kind=getattr(event, "kind", ""),
                      chars=len(text))
        except Exception as exc:  # noqa: BLE001
            now = time.time()
            if now - self._live_fail_logged >= 30.0:
                self._live_fail_logged = now
                self._log("live_chat_send_failed",
                          error=f"{type(exc).__name__}: {exc}"[:160])

    def _mtime(self) -> int:
        try:
            return self._config_path.stat().st_mtime_ns
        except OSError:
            return 0

    def _read_config_game(self) -> str:
        try:
            cfg = LiveConfig.load(self._config_path)
            return str(getattr(cfg, "game", "") or "").strip()
        except Exception:
            return ""

    def _should_stop(self) -> bool:
        return bool(self._local_stop or self.host.stopping())

    def _sleep(self, seconds: float) -> None:
        """分段睡，期间检查停止/切模式；不让 30 秒等待拖慢退出。"""
        deadline = time.monotonic() + max(0.0, float(seconds))
        while not self._should_stop():
            left = deadline - time.monotonic()
            if left <= 0:
                return
            if self._config_switched():
                self._local_stop = True
                self._stop_reason = "配置已切换到其他模式"
                return
            time.sleep(min(0.2, left))

    def _status(self, **fields) -> None:
        base = {
            "running": True, "game": GAME_ID, "mode": "live",
            "pace": getattr(self.host, "pace", "voice"),
            "pid": os.getpid(), "idle_text": "看视频ing",
        }
        base.update(fields)
        try:
            self.host.set_status(**base)
        except Exception:
            pass

    def _maybe_status(self, screen: str, position: float, duration: float,
                      title: str, extra: dict | None = None) -> None:
        now = self._wall()
        if now - self._last_status < 2.0 and abs(position - self._last_position) < 2.0:
            return
        self._last_status = now
        self._last_position = position
        fields = {
            "screen": screen,
            "step": self._video_count,
            "last": f"{title[:34]} · 模拟 {_fmt(position)}/{_fmt(duration)}",
            "bvid": extra.get("bvid", "") if extra else "",
            "video_title": title[:80],
            "position_s": round(position, 2),
            "duration_s": round(duration, 2),
            "cues_fired": self._cues_fired,
            "cues_dropped": self._cues_dropped,
            "replies_fired": self._replies_fired,
            "llm_calls": self._llm_calls,
            "speech_busy": bool(self.speech.busy()),
            "speech": self.speech.snapshot(),
        }
        if self.live_chat is not None:
            try:
                fields["live_chat"] = self.live_chat.snapshot()
            except Exception:
                pass
        if extra:
            fields.update({k: v for k, v in extra.items() if k not in fields})
        self._status(**fields)

    def _acquire_run_lock(self) -> bool:
        """跨进程只允许一个云观影引擎，避免两个进程同时选到同一条。"""
        try:
            self._run_lock_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return True  # 锁目录不可写时不让锁阻止启动
        for _ in range(2):
            try:
                fd = os.open(self._run_lock_path,
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii", "ignore"))
                self._run_lock_fd = fd
                return True
            except FileExistsError:
                try:
                    owner = int(self._run_lock_path.read_text(
                        encoding="utf-8").strip() or 0)
                except (OSError, ValueError):
                    owner = 0
                if owner and self.host.pid_alive(owner):
                    self._log("commentary_already_running", pid=owner)
                    return False
                # 进程已死/同进程上一个线程留下的锁，清掉重试。
                try:
                    self._run_lock_path.unlink()
                except OSError:
                    return False
        return False

    def _release_run_lock(self) -> None:
        fd, self._run_lock_fd = self._run_lock_fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            owner = int(self._run_lock_path.read_text(
                encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            owner = 0
        if owner == os.getpid():
            try:
                self._run_lock_path.unlink()
            except OSError:
                pass

    # ---------------------------------------------------------------- 生命周期
    def run(self) -> None:
        if not self._acquire_run_lock():
            self._stop_reason = "已有云观影在跑，拒绝重复启动"
            try:
                self.host.set_status(
                    running=False, stopping=False, screen="STOPPED",
                    game=GAME_ID, last=self._stop_reason, error=self._stop_reason,
                    speech_busy=False, idle_text="",
                )
            except Exception:
                pass
            return
        try:
            self._clear_controls()
            rec = self.host.start_recording(note="云观影") or {}
            self._log("commentary_record", ok=bool(rec.get("ok")),
                      path=rec.get("path"), error=rec.get("error"))
            self._status(
                screen="SELECTING", step=0, last="开始按人设选视频", error="",
                bvid="", video_title="", position_s=0.0, duration_s=0.0,
                cues_fired=0, cues_dropped=0, replies_fired=0, llm_calls=0,
                speech_busy=False, speech=self.speech.snapshot(),
            )
            self._log("commentary_start", speed=self.config.speed,
                      max_duration_s=self.config.max_duration_s,
                      persona=self.persona.persona_id,
                      search=bool(self.config.search_enabled),
                      open_browser=bool(self.config.open_browser),
                      live_chat=bool(self.live_chat))
            if self.live_chat is not None:
                self.live_chat.start()
                self._log("live_chat_start")
            self._publish_playlist(refs=[], current={}, state="idle")
            self._apply_control()
            while not self._should_stop():
                if self._config_switched():
                    self._stop_reason = "配置已切换到其他模式"
                    break
                self._apply_control()
                # 正常连续刷：播放当前视频时已经预取好下一条，直接接上；
                # 第一次或预取失败时才同步准备。用户指定播放时作废预取。
                if self._override_bvid:
                    self._drop_prefetch()
                    prepared = self._prepare()
                else:
                    prepared = self._take_prefetch() or self._prepare()
                if prepared is None:
                    if self._should_stop() or self._config_switched():
                        break
                    self._status(
                        screen="WAITING", last="暂无合适视频，等待热门更新",
                        error=self._stop_reason or "没有可用候选",
                        speech=self.speech.snapshot(),
                    )
                    self._log("commentary_wait", reason=self._stop_reason or "no_candidate")
                    self._wait_with_control(self.wait_s)
                    continue
                detail, profile, danmaku, info = prepared
                self._play(detail, profile, danmaku, info)
            self._log("commentary_stop", reason=self._stop_reason or "stopped")
        except Exception as exc:  # noqa: BLE001
            self._log("commentary_fatal", error=f"{type(exc).__name__}: {exc}")
            self._status(screen="STOPPED", last="异常停止",
                         error=f"{type(exc).__name__}: {exc}")
        finally:
            try:
                self.host.stop_recording("live_end")
            except Exception:
                pass
            try:
                if self.browser is not None and hasattr(self.browser, "close"):
                    self.browser.close()
            except Exception:
                pass
            try:
                self._publish_playlist(state="stopped")
            except Exception:
                pass
            try:
                if self.live_chat is not None:
                    self.live_chat.stop()
            except Exception:
                pass
            try:
                if self.live_speech is not None:
                    self.live_speech.stop()
            except Exception:
                pass
            try:
                self.speech.stop()
            except Exception:
                pass
            try:
                clear_idle = getattr(self.sink, "clear_idle", None)
                if callable(clear_idle):
                    clear_idle()
            except Exception:
                pass
            try:
                self.host.set_status(
                    running=False, stopping=False, screen="STOPPED", game=GAME_ID,
                    last=self._stop_reason or "已停手", speech_busy=False,
                    idle_text="", speech=self.speech.snapshot(),
                )
            except Exception:
                pass
            self._release_run_lock()

    # ------------------------------------------------ 控制台播放列表/命令
    def _publish_playlist(self, refs=None, *, current=None, state: str = "") -> None:
        """把控制台要看的候选/当前视频写成小 JSON，不写简介/评论/弹幕原文。"""
        with self._playlist_lock:
            if refs is not None:
                self._playlist_refs = list(refs)
            if current is not None:
                self._playlist_current = dict(current)
            current = dict(self._playlist_current)
            current_bvid = str(current.get("bvid") or "")
            items: list[dict] = []
            if current_bvid:
                items.append(current)
            for ref in self._playlist_refs:
                bvid = str(getattr(ref, "bvid", "") or "")
                if not bvid or bvid == current_bvid:
                    continue
                items.append(control.item_from_ref(ref, status="candidate"))
        if not state:
            state = "playing" if current_bvid else "idle"
        browser = {}
        if self.browser is not None and hasattr(self.browser, "snapshot"):
            try:
                browser = self.browser.snapshot()
            except Exception:
                browser = {}
        try:
            control.publish_playlist(
                items, current_bvid=current_bvid, state=state,
                browser=browser, root=self._control_root)
        except Exception as exc:  # noqa: BLE001 - 控制台状态不能拖垮视频
            self._log("playlist_write_error", error=f"{type(exc).__name__}: {exc}"[:160])

    def _take_command(self) -> dict | None:
        try:
            command = control.consume_command(self._control_root)
        except Exception as exc:  # noqa: BLE001
            self._log("control_read_error", error=f"{type(exc).__name__}: {exc}"[:160])
            return None
        if command:
            self._log("control_command", action=str(command.get("action") or ""),
                      bvid=str(command.get("bvid") or ""))
        return command

    def _handle_command(self, command: dict, current_bvid: str = "") -> str:
        action = str((command or {}).get("action") or "").strip().lower()
        if action == "close":
            result = {"ok": False, "error": "没有浏览器"}
            if self.browser is not None and hasattr(self.browser, "close"):
                try:
                    result = self.browser.close()
                except Exception as exc:  # noqa: BLE001
                    result = {"ok": False, "error": str(exc)}
            self._log("browser_close", ok=bool(result.get("ok")),
                      error=str(result.get("error") or "")[:120])
            self._publish_playlist(state="playing" if current_bvid else "idle")
            return "close"
        if action == "refresh":
            result = {"ok": False, "error": "没有浏览器"}
            if self.browser is not None and hasattr(self.browser, "refresh"):
                try:
                    result = self.browser.refresh()
                except Exception as exc:  # noqa: BLE001
                    result = {"ok": False, "error": str(exc)}
            if not result.get("ok") and current_bvid:
                url = str(self._playlist_current.get("url") or "")
                if url and self.browser is not None:
                    try:
                        result = self.browser.open(url)
                    except Exception as exc:  # noqa: BLE001
                        result = {"ok": False, "error": str(exc)}
            self._log("browser_refresh", ok=bool(result.get("ok")),
                      error=str(result.get("error") or "")[:120])
            self._publish_playlist(state="playing" if current_bvid else "idle")
            return "refresh"
        if action == "play":
            bvid = str((command or {}).get("bvid") or "").strip()
            if not bvid:
                return ""
            if current_bvid and bvid == current_bvid:
                return self._handle_command({"action": "refresh"}, current_bvid)
            self._override_bvid = bvid
            self._switching = True
            self._log("video_switch_request", bvid=bvid)
            return "switch"
        return ""

    def _apply_control(self, current_bvid: str = "") -> str:
        command = self._take_command()
        return self._handle_command(command, current_bvid) if command else ""

    def _wait_with_control(self, seconds: float, current_bvid: str = "") -> bool:
        """等待时也响应控制台；返回 True 表示用户指定了要切换的视频。"""
        deadline = time.monotonic() + max(0.05, float(seconds))
        while not self._should_stop():
            if self._apply_control(current_bvid) == "switch":
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._sleep(min(0.5, remaining))
        return False

    def _take_forced_ref(self) -> VideoRef | None:
        bvid = str(self._override_bvid or "").strip()
        if not bvid:
            return None
        self._override_bvid = ""
        for ref in self._playlist_refs:
            if ref.bvid == bvid:
                return ref
        try:
            detail = self._detail(VideoRef(bvid=bvid))
            return detail.to_ref()
        except Exception as exc:  # noqa: BLE001
            self._log("playlist_play_failed", bvid=bvid,
                      error=f"{type(exc).__name__}: {exc}"[:160])
            # 不要因为一次详情接口失败就偷偷改播别的视频；把 bvid 交回
            # _prepare，让它按用户点播失败给出明确状态。
            return VideoRef(bvid=bvid)

    def _candidate_refs(self) -> list[VideoRef]:
        self._priority_bvids = set()
        if not self.config.search_enabled or self.search is None:
            return self._popular()
        intent = self._get_intent()
        if intent is None or not intent.keywords:
            if self._high_view_due():
                high = self._high_view_refs()
                enriched_high = self._enrich_search_refs(high) if high else []
                if enriched_high:
                    self._priority_bvids = {ref.bvid for ref in enriched_high}
                    self._log("high_view_candidates", count=len(enriched_high),
                              step=self._video_count + 1)
                    return enriched_high
            self._log("search_skipped", reason="没有搜索意图，回退热门")
            return self._popular()
        refs = self._search_videos(intent)
        high: list[VideoRef] = []
        if self._high_view_due():
            high = self._high_view_refs()
            if high:
                # 高播放量候选放前面，后面仍保留搞笑候选兜底。
                high_ids = {ref.bvid for ref in high}
                self._priority_bvids = high_ids
                refs = high + [ref for ref in refs if ref.bvid not in high_ids]
                self._log("high_view_candidates", count=len(high),
                          step=self._video_count + 1)
        if not refs:
            self._log("search_empty", keywords=list(intent.keywords),
                      reason="搜索结果为空，回退热门")
            return self._popular()
        enriched = self._enrich_search_refs(refs)
        if enriched:
            self._log("search_candidates", keywords=list(intent.keywords),
                      count=len(enriched), source=intent.source)
            return enriched
        self._log("search_enrich_empty", keywords=list(intent.keywords),
                  reason="搜索候选详情都不可用，回退热门")
        return self._popular()

    def _get_intent(self) -> SearchIntent | None:
        if self._intent is not None:
            return self._intent
        try:
            self._intent = self.search.get()
        except Exception as exc:  # noqa: BLE001
            self._log("search_intent_error", error=f"{type(exc).__name__}: {exc}")
            self._intent = None
            return None
        usage = getattr(self.search, "last_usage", {}) if self.search is not None else {}
        if isinstance(usage, dict) and usage:
            self._llm_calls += 1
            try:
                self.host.add_usage(
                    usage, note="搜索意图", step=self._video_count)
            except Exception:
                pass
        self._log(
            "search_intent", keywords=list(self._intent.keywords),
            avoid=list(self._intent.avoid), source=self._intent.source,
            reason=self._intent.reason[:120],
        )
        return self._intent

    def _text_blocked(self, *parts: str) -> bool:
        """标题/简介是否命中当前游戏的排除词。"""
        words = [str(word).strip().lower() for word in self.config.search_exclude
                 if str(word).strip()]
        if not words:
            return False
        text = " ".join(str(part or "") for part in parts).lower()
        return any(word in text for word in words)

    def _search_blocked(self, *parts: str) -> bool:
        """标题/简介是否命中当前游戏的排除词；手写关键词时尊重用户覆盖。"""
        if self._intent is not None and self._intent.source == "config":
            return False
        return self._text_blocked(*parts)

    def _high_view_due(self) -> bool:
        """按视频计数轮换：每 high_view_every 条安排一条高播放量。"""
        every = int(self.config.high_view_every or 0)
        return bool(self.config.high_view_keywords and every > 0
                    and (self._video_count + 1) % every == 0)

    def _high_view_refs(self) -> list[VideoRef]:
        """搜索高播放量的“大肥鱼”视频；结果按播放量从高到低。"""
        words = [str(word).strip()
                 for word in self.config.high_view_keywords if str(word).strip()]
        if not words:
            return []
        out: list[VideoRef] = []
        seen: set[str] = set()
        floor = max(0, int(self.config.high_view_min_view or 0))
        for keyword in words[:5]:
            if self._should_stop():
                break
            try:
                rows = self.client.search(
                    keyword, limit=self.config.search_limit)
            except BiliError as exc:
                self._log("high_view_search_failed", keyword=keyword,
                          error=exc.message[:160])
                continue
            for ref in rows:
                if not ref.bvid or ref.bvid in seen:
                    continue
                stat = ref.stat if isinstance(ref.stat, dict) else {}
                try:
                    view = int(stat.get("view") or 0)
                except (TypeError, ValueError):
                    view = 0
                if view < floor:
                    continue
                if not (self.config.min_duration_s <= ref.duration_s
                        <= self.config.max_duration_s):
                    continue
                # 搜索卡片只有播放量，没有评论数；评论门槛等补详情后由
                # select_candidates 统一过滤，不能在这里把候选全砍掉。
                if self._text_blocked(ref.title, ref.desc):
                    continue
                # 高播放量榜前面的往往已经看过；在这里先排掉，才能继续从
                # “大肥鱼 直播/切片”这些补充搜索里找到没刷过的。
                if self.stores.seen.is_seen(
                        ref.bvid, self.config.seen_ttl_days):
                    continue
                if self.stores.failed.is_failed(ref.bvid):
                    continue
                seen.add(ref.bvid)
                out.append(ref)
            if len(out) >= 8:
                break
        out.sort(key=_ref_view, reverse=True)
        self._log("high_view_search", keywords=words[:5], floor=floor,
                  count=len(out))
        return out

    def _search_videos(self, intent: SearchIntent) -> list[VideoRef]:
        out: list[VideoRef] = []
        seen: set[str] = set()
        avoid = [word.lower() for word in intent.avoid if word]
        if intent.source != "config":
            # 手写 search_keywords 是用户明确覆盖，允许用户主动搜当前游戏；
            # 自动/人设搜索则必须隔离，不能把《杀戮尖塔2》搜回来。
            avoid.extend(word.lower() for word in self.config.search_exclude if word)
        for keyword in intent.keywords[:5]:
            if self._should_stop():
                break
            try:
                rows = self.client.search(
                    keyword, limit=self.config.search_limit)
            except BiliError as exc:
                self._log("search_failed", keyword=keyword,
                          error=exc.message[:160])
                continue
            for ref in rows:
                if not ref.bvid or ref.bvid in seen:
                    continue
                seen.add(ref.bvid)
                text = f"{ref.title} {ref.desc}".lower()
                if any(word in text for word in avoid):
                    continue
                if not (self.config.min_duration_s <= ref.duration_s <= self.config.max_duration_s):
                    continue
                if self.stores.seen.is_seen(
                        ref.bvid, self.config.seen_ttl_days,
                        now=self._wall()):
                    continue
                if self.stores.failed.is_failed(ref.bvid, now=self._wall()):
                    continue
                out.append(ref)
        # 以前这里又截了整个列表的 20 条，等于每个关键词只吃前几条；
        # 现在每个关键词自己限流，保留所有去重后的候选。
        return out

    def _enrich_search_refs(self, refs: list[VideoRef]) -> list[VideoRef]:
        out: list[VideoRef] = []
        for ref in refs:
            if self._should_stop() or len(out) >= self.config.search_prefetch:
                break
            try:
                detail = self._detail(ref)
            except BiliError as exc:
                self._log("search_detail_skipped", bvid=ref.bvid,
                          error=exc.message[:160])
                continue
            out.append(detail.to_ref())
        return out

    def _forced_profile(self, detail: VideoDetail) -> VideoProfile:
        """点播兜底档案：LLM 失败也先把视频放起来，开场/收尾由排期补。"""
        return VideoProfile(
            bvid=detail.bvid,
            url=detail.url,
            title=detail.title,
            duration_s=max(1, int(detail.duration_s or 0)),
            owner={"mid": detail.owner_mid, "name": detail.owner_name},
            confidence={"topic": 0.2, "summary": 0.2, "audience": 0.2,
                        "timeline": 0.0, "content": 0.0},
            meta={"forced_fallback": True},
        )

    def _prepare(self, *, exclude_bvid: str = "", quiet: bool = False):
        if not quiet:
            self._apply_control()
            self._stop_reason = ""
            self._status(screen="SELECTING", last="根据人设选视频", error="",
                         bvid="", video_title="", position_s=0.0, duration_s=0.0)
        # 控制台明确点了播放时，直接按 bvid 准备；不再走“已看/失败/排除词”
        # 过滤，否则用户点了列表里的视频又会被自动规则弹回去。
        forced = None if quiet else self._take_forced_ref()
        if forced is not None:
            self._priority_bvids = set()
            # 点播只改变“先准备哪一条”，不要把控制台里原有的候选列表
            # 整个清空；否则刚点完播放，新列表就只剩这一条，大肥鱼也没了。
            refs = [forced] + [
                ref for ref in self._playlist_refs
                if ref.bvid and ref.bvid != forced.bvid]
            videos = [forced]
            self._publish_playlist(refs=refs, state="selecting")
        else:
            refs = self._candidate_refs()
            if exclude_bvid:
                refs = [ref for ref in refs if ref.bvid != exclude_bvid]
            if not refs:
                self._stop_reason = "没有可用候选"
                return None
            self._publish_playlist(
                refs=refs, state="" if quiet else "selecting")
            result = select_candidates(
                refs, self.config, self.stores.seen, self.stores.failed)
            if not result.candidates:
                self._stop_reason = "候选都被过滤"
                self._log("commentary_no_candidate", rejected=result.rejected[:8])
                return None
            videos = [candidate.video for candidate in result.candidates]
            if self._priority_bvids:
                # 评分可能把大肥鱼压到第二、第三；用户要的是“多刷大肥鱼”，
                # 只要它过了已看/失败/评论门槛，本轮就先播它。
                first = next(
                    (video for video in videos
                     if video.bvid in self._priority_bvids), None)
                if first is not None:
                    videos = [first] + [video for video in videos
                                        if video.bvid != first.bvid]
                    self._log("high_view_selected", bvid=first.bvid,
                              title=first.title[:80],
                              step=self._video_count + 1)
        last_error = ""
        for video in videos[:self.config.max_candidates_per_cycle]:
            if self._should_stop() or self._config_switched():
                return None
            if forced is None and exclude_bvid and video.bvid == exclude_bvid:
                continue
            if not quiet:
                self._status(screen="PREPARING", last=f"准备：{video.title[:34]}",
                             bvid=video.bvid, video_title=video.title[:80],
                             error="")
            try:
                detail = self._detail(video)
                if forced is None and self._search_blocked(detail.title, detail.desc):
                    self._mark_failed(video.bvid, "命中当前游戏排除词")
                    self._log("commentary_excluded", bvid=video.bvid,
                              title=detail.title[:80], reason="search_exclude")
                    continue
                try:
                    comments = self._comments(detail)
                except BiliError as exc:
                    if forced is None:
                        raise
                    comments = []
                    self._log("forced_comments_skipped", bvid=video.bvid,
                              error=exc.message[:160])
                # 用户在控制台点播时，评论少也照播；否则 0 热评视频会被
                # 门槛挡掉，表现成“点了播放但没反应”。
                if forced is None and len(comments) < self.config.min_comments:
                    last_error = f"热评不足（{len(comments)} < {self.config.min_comments}）"
                    self._mark_failed(video.bvid, "热评不足")
                    continue
                try:
                    danmaku = self._danmaku(detail)
                except BiliError as exc:
                    if forced is None:
                        raise
                    danmaku = DanmakuResult(error=exc.message[:160])
                    self._log("forced_danmaku_skipped", bvid=video.bvid,
                              error=exc.message[:160])
                profile, info = self.builder.build(detail, comments, danmaku)
                usage = (info or {}).get("usage") or {}
                if usage:
                    self._llm_calls += 1
                    try:
                        self.host.add_usage(usage, note=f"视频档案 {video.bvid}",
                                            step=self._video_count + 1)
                    except Exception:
                        pass
                self._log(
                    "commentary_profile", bvid=video.bvid,
                    cached=bool((info or {}).get("cached")),
                    cues=len(profile.cues), replies=len(profile.replies),
                    danmaku=len(danmaku.items), partial=danmaku.partial,
                    same_fish=bool((profile.meta or {}).get("same_fish")),
                    confidence=profile.confidence,
                )
                return detail, profile, danmaku, info or {}
            except (BiliError, ProfileError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"[:160]
                if forced is not None and isinstance(exc, ProfileError):
                    profile = self._forced_profile(detail)
                    self._log("forced_profile_fallback", bvid=video.bvid,
                              error=str(exc)[:160])
                    return detail, profile, danmaku, {
                        "cached": False, "fallback": True}
                self._mark_failed(video.bvid, str(exc)[:160])
                self._log("commentary_skip", bvid=video.bvid,
                          error=f"{type(exc).__name__}: {exc}"[:240])
                continue
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"[:160]
                self._mark_failed(video.bvid, f"{type(exc).__name__}: {exc}")
                self._log("commentary_candidate_error", bvid=video.bvid,
                          error=f"{type(exc).__name__}: {exc}"[:240])
                continue
        if forced is not None:
            self._stop_reason = (
                f"点播失败：{last_error}" if last_error else "点播失败：没有可用档案")
        else:
            self._stop_reason = "本轮候选全部失败"
        return None

    def _start_prefetch(self, current_bvid: str) -> None:
        """当前视频还在播时，后台准备下一条；失败不挡当前播放。"""
        if self._should_stop() or self._config_switched():
            return
        with self._prefetch_lock:
            if self._prefetch_thread is not None and self._prefetch_thread.is_alive():
                return
            self._prefetch_gen += 1
            gen = self._prefetch_gen
            self._prefetch_result = None
            thread = threading.Thread(
                target=self._prefetch_worker, args=(current_bvid, gen),
                name="bili-prefetch", daemon=True,
            )
            self._prefetch_thread = thread
            thread.start()

    def _prefetch_worker(self, current_bvid: str, gen: int) -> None:
        try:
            result = self._prepare(exclude_bvid=current_bvid, quiet=True)
        except Exception as exc:  # noqa: BLE001
            self._log("prefetch_error", error=f"{type(exc).__name__}: {exc}"[:240])
            result = None
        with self._prefetch_lock:
            if gen != self._prefetch_gen:
                return  # 已经被 stop/新一局作废，不能写回旧结果
            if result is not None:
                self._log("prefetch_ready", bvid=result[0].bvid,
                          cues=len(result[1].cues))
            self._prefetch_result = result

    def _drop_prefetch(self) -> None:
        """用户指定播放时作废正在准备的下一条，不能让预取抢走 override。"""
        with self._prefetch_lock:
            self._prefetch_gen += 1
            self._prefetch_result = None
            self._prefetch_thread = None

    def _take_prefetch(self):
        if self._override_bvid:
            self._drop_prefetch()
            return None
        with self._prefetch_lock:
            thread = self._prefetch_thread
        if thread is not None and thread.is_alive():
            self._status(screen="SELECTING", last="下一个准备中",
                         position_s=0.0, duration_s=0.0)
            # 档案 LLM 通常 10 秒内；等一会儿比结束后重新选片更连续。
            deadline = time.monotonic() + max(5.0, self.config.llm_timeout_s + 5.0)
            while thread.is_alive() and time.monotonic() < deadline:
                thread.join(timeout=0.25)
                # 等待预取时也处理控制台；指定播放要作废预取，不能被它抢走。
                if self._apply_control() == "switch":
                    self._drop_prefetch()
                    return None
            if thread.is_alive():
                self._log("prefetch_timeout", reason="等待下一个档案超时")
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_gen += 1  # 超时线程晚到的结果也不能再写回
            self._prefetch_result = None
            self._prefetch_thread = None
        return result

    def _start_like(self, detail: VideoDetail) -> None:
        """刷到视频先点赞；不阻塞播放，失败只记日志。"""
        if self.liker is None:
            return
        if not self.liker.available():
            if not self._like_skip_logged:
                self._like_skip_logged = True
                self._log("like_skipped", reason="未配置登录 Cookie（SESSDATA/bili_jct）")
            return
        threading.Thread(
            target=self._like_worker, args=(detail,),
            name="bili-like", daemon=True,
        ).start()

    def _like_worker(self, detail: VideoDetail) -> None:
        try:
            result = self.liker.like(detail.aid, detail.bvid)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if result.get("ok"):
            self._log("like_ok", bvid=detail.bvid, aid=int(detail.aid or 0))
        elif result.get("skipped"):
            if not self._like_skip_logged:
                self._like_skip_logged = True
                self._log("like_skipped", reason=str(result.get("reason") or "")[:120])
        else:
            self._log("like_failed", bvid=detail.bvid,
                      code=int(result.get("code") or 0),
                      error=str(result.get("error") or "")[:160])

    def _load_comment_state(self) -> None:
        try:
            raw = json.loads(self._comment_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(raw, dict):
            return
        day = str(raw.get("date") or "")
        if day != time.strftime("%Y-%m-%d"):
            return
        try:
            count = max(0, int(raw.get("count") or 0))
        except (TypeError, ValueError):
            count = 0
        try:
            last_at = max(0.0, float(raw.get("last_at") or 0.0))
        except (TypeError, ValueError):
            last_at = 0.0
        self._comment_day = day
        self._comment_count = count
        self._comment_last_at = last_at
        rows = raw.get("bvids")
        if isinstance(rows, list):
            self._commented_bvids = {
                str(item) for item in rows if str(item).strip()}

    def _save_comment_state(self) -> None:
        payload = {
            "date": self._comment_day or time.strftime("%Y-%m-%d"),
            "count": int(self._comment_count),
            "last_at": float(self._comment_last_at),
            "bvids": sorted(self._commented_bvids)[-50:],
        }
        try:
            self._comment_state_path.parent.mkdir(parents=True, exist_ok=True)
            self._comment_state_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _start_video_comment(self, detail: VideoDetail, profile) -> None:
        """同类视频且模型写了评论时，异步发一条；每日上限/间隔防止刷屏。"""
        if not self.config.video_comment_enabled:
            return
        meta = dict(getattr(profile, "meta", {}) or {})
        if not meta.get("same_fish"):
            return
        if detail.bvid in self._commented_bvids:
            return
        text = strip_danmaku_word(
            str(meta.get("video_comment") or "")).strip()
        max_chars = int(self.config.video_comment_max_chars or 0)
        if max_chars > 0:
            text = text[:max_chars].strip()
        if not text:
            # 模型自己判断“这次没什么想说的”，不是错误。
            return
        dropped = comment_drop_reason(
            text, tuple(getattr(self.persona, "quips", ()) or ()))
        if dropped:
            # 旧缓存/profile 里可能还留着“同类看同类”这类台本，发出去更像机器人。
            self._log("video_comment_skipped", bvid=detail.bvid,
                      reason="像在背人设台词，这次不发了", kind=dropped)
            return
        if self.commenter is None:
            return
        try:
            available = bool(self.commenter.available())
        except Exception:  # noqa: BLE001
            available = False
        if not available:
            if not self._comment_skip_logged:
                self._comment_skip_logged = True
                self._log("video_comment_skipped",
                          reason="未配置登录 Cookie（SESSDATA/bili_jct）")
            return
        if self._comment_inflight:
            self._log("video_comment_skipped", bvid=detail.bvid,
                      reason="上一条评论还没发完")
            return
        day = time.strftime("%Y-%m-%d")
        if self._comment_day != day:
            self._comment_day = day
            self._comment_count = 0
        limit = int(self.config.video_comment_max_per_day or 0)
        if limit > 0 and self._comment_count >= limit:
            self._log("video_comment_skipped", bvid=detail.bvid,
                      reason=f"今日评论上限 {limit}")
            return
        now = self._wall()
        gap = max(0.0, float(self.config.video_comment_min_gap_s or 0.0))
        if self._comment_last_at and now - self._comment_last_at < gap:
            self._log("video_comment_skipped", bvid=detail.bvid,
                      reason="距上一条评论太近")
            return
        self._comment_inflight = True
        self._comment_last_at = now
        threading.Thread(
            target=self._video_comment_worker, args=(detail, text),
            name="bili-video-comment", daemon=True,
        ).start()

    def _video_comment_worker(self, detail: VideoDetail, text: str) -> None:
        try:
            result = self.commenter.comment(detail.aid, detail.bvid, text)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            self._comment_inflight = False
        if result.get("ok"):
            self._comment_count += 1
            self._commented_bvids.add(detail.bvid)
            self._save_comment_state()
            self._record_video_comment(detail, text, result.get("rpid"))
            self._log("video_comment_ok", bvid=detail.bvid,
                      rpid=str(result.get("rpid") or ""),
                      chars=len(text))
        elif result.get("skipped"):
            if not self._comment_skip_logged:
                self._comment_skip_logged = True
                self._log("video_comment_skipped",
                          reason=str(result.get("reason") or "")[:120])
        else:
            self._log("video_comment_failed", bvid=detail.bvid,
                      code=int(result.get("code") or 0),
                      error=str(result.get("error") or "")[:160])

    def _record_video_comment(self, detail: VideoDetail, text: str, rpid) -> None:
        """把真发出去的评论记进控制台能读的本地账本：视频链接/标题/评论内容。"""
        try:
            control.append_video_comment(
                bvid=detail.bvid, title=detail.title, text=text,
                url=detail.url, owner=detail.owner_name, rpid=rpid,
                root=self._control_root)
        except Exception:  # noqa: BLE001 - 记账失败不影响看视频
            pass

    def _browser_lost(self) -> bool:
        """Chrome 窗口被关掉时返回 True；只读窗口句柄/标题，不读页面。"""
        browser = self.browser
        if browser is None or not hasattr(browser, "snapshot"):
            return False
        try:
            snapshot = browser.snapshot()
        except Exception:  # noqa: BLE001
            return False
        if not isinstance(snapshot, dict) or snapshot.get("browser") != "chrome":
            return False
        title = str(snapshot.get("title") or "").strip()
        new_tab = any(word in title for word in (
            "新标签页", "新建标签页", "New Tab", "新建标签"))
        if title and not new_tab:
            self._browser_title_seen = True
        if snapshot.get("opened"):
            self._browser_hwnd_seen = True
        elif not self._browser_hwnd_seen:
            # Chrome 窗口句柄是异步找到的，刚打开那几秒不能算“被关掉”。
            return False
        else:
            self._browser_newtab_since = 0.0
            return True
        # 只在已经看到过视频标题后，才把“新标签页”当成标签页被关掉；
        # 再留 8 秒宽限，避免用户临时开个新标签页就把视频切走。
        if self._browser_title_seen and new_tab:
            now = float(self._wall())
            if not self._browser_newtab_since:
                self._browser_newtab_since = now
            return now - self._browser_newtab_since >= 8.0
        self._browser_newtab_since = 0.0
        return False

    def _history_sample(self, bvid: str) -> dict | None:
        """读一次自己账号的历史进度；没有 Cookie/接口失败返回 None。"""
        method = getattr(self.client, "history_progress", None)
        if not callable(method):
            return None
        try:
            progress = method(bvid)
        except BiliError as exc:
            if not self._history_error_logged:
                self._history_error_logged = True
                self._log("history_progress_failed", error=exc.message[:120])
            return None
        except Exception as exc:  # noqa: BLE001
            if not self._history_error_logged:
                self._history_error_logged = True
                self._log("history_progress_error",
                          error=f"{type(exc).__name__}: {exc}"[:120])
            return None
        if not isinstance(progress, dict):
            return None
        try:
            return {
                "progress": int(progress.get("progress") or 0),
                "duration": int(progress.get("duration") or 0),
                "view_at": int(progress.get("view_at") or 0),
            }
        except (TypeError, ValueError):
            return None

    def _history_end(self, bvid: str, since: float) -> bool:
        """用自己账号的历史进度发现用户把进度拖到了结尾。"""
        sample = self._history_sample(bvid)
        if not sample:
            return False
        # 必须是本次播放期间的新记录，不能拿上次看完的旧历史直接跳。
        if sample["duration"] <= 10 or sample["view_at"] < int(since) - 5:
            return False
        return sample["progress"] >= sample["duration"] - 2

    def _play(self, detail: VideoDetail, profile, danmaku: DanmakuResult,
              info: dict) -> None:
        plan = build_plan(profile, self.config, speed=self.config.speed)
        if not plan.cues:
            self._mark_failed(detail.bvid, "没有可用 cue")
            self._stop_reason = "没有可用 cue"
            return
        # 进入播放即记已看：重启/中途停掉也不会再把同一条选回来。
        # 自然播完仍保留这个标记；跳过会额外进失败冷却。
        self.stores.seen.mark(detail.bvid)
        self._browser_title_seen = False
        self._browser_hwnd_seen = False
        self._browser_newtab_since = 0.0
        play_started_at = self._wall()
        play_url = start_from_zero(detail.url)
        if self.browser is not None:
            opened = self.browser.open(play_url)
            self._log("browser_open", bvid=detail.bvid,
                      ok=bool(opened.get("ok")),
                      browser=str(opened.get("browser") or ""),
                      url=play_url,
                      error=str(opened.get("error") or "")[:160])
            try:
                snapshot = self.browser.snapshot()
                if (isinstance(snapshot, dict)
                        and snapshot.get("browser") == "chrome"
                        and snapshot.get("opened")):
                    self._browser_hwnd_seen = True
            except Exception:  # noqa: BLE001
                pass
        self._start_like(detail)
        self._start_video_comment(detail, profile)
        self._live_title = profile.title
        self._live_same_fish = is_big_fish_text(
            detail.title, detail.desc, detail.owner_name)
        self._video_count += 1
        self._switching = False
        self._publish_playlist(
            current=control.item_from_ref(detail, status="playing"),
            state="playing",
        )
        scheduler = CueScheduler(plan.cues)
        replies = ReplyScheduler(
            plan.replies, profile.timeline,
            min_gap_s=self.config.reply_min_gap_s,
        )
        self._status(
            screen="PLAYING", step=self._video_count,
            last=f"{profile.title[:34]} · 模拟 {_fmt(0)}/{_fmt(plan.duration_s)}",
            bvid=detail.bvid, video_title=profile.title[:80],
            position_s=0.0, duration_s=plan.duration_s,
            cues_fired=0, cues_dropped=0, replies_fired=0,
            llm_calls=self._llm_calls, speech=self.speech.snapshot(), error="",
            profile_confidence=profile.confidence,
            danmaku_count=len(danmaku.items), danmaku_partial=danmaku.partial,
        )
        self._log("video_start", bvid=detail.bvid, title=profile.title[:80],
                  duration=plan.duration_s, speed=plan.speed,
                  cues=len(plan.cues), replies=len(plan.replies),
                  danmaku=len(danmaku.items), cached=bool(info.get("cached")))
        self._start_prefetch(detail.bvid)
        self._open_greeting(plan, scheduler, detail)
        clock = VirtualClock(plan.duration_s, plan.speed, now=self._now)
        stopped = False
        skipped = False
        reached_end = False
        browser_lost = False
        not_playing = False
        position = 0.0
        last_status = 0.0
        last_history_check = 0.0
        last_browser_check = 0.0
        history_last_progress = -1
        history_last_change_at = play_started_at
        while not self._should_stop():
            if self._config_switched():
                stopped = True
                self._stop_reason = "配置已切换到其他模式"
                break
            if self._apply_control(detail.bvid) == "switch":
                break
            if self._pause_path.exists():
                if not clock.paused:
                    clock.pause()
                    self._status(screen="PAUSED", last="已暂停（模拟时钟冻结）")
                    self._log("video_pause", bvid=detail.bvid)
                self._sleep(0.5)
                continue
            if clock.paused:
                clock.resume()
                self._status(screen="PLAYING", last="继续播放")
                self._log("video_resume", bvid=detail.bvid)
            if self._consume_skip():
                skipped = True
                self._log("video_skip", bvid=detail.bvid)
                break

            position = clock.tick()
            now = self._wall()
            if position >= 3.0 and now - last_browser_check >= 2.0:
                last_browser_check = now
                if self._browser_lost():
                    browser_lost = True
                    self._log("video_browser_lost", bvid=detail.bvid)
                    break
            # 历史进度既用来发现“拖到结尾”，也用来发现画面卡住/暂停。
            # 没有 Cookie 时安静跳过，仍由虚拟时钟负责自然结束。
            if position >= 3.0 and now - last_history_check >= 10.0:
                last_history_check = now
                sample = self._history_sample(detail.bvid)
                if (sample and sample["duration"] > 10
                        and sample["view_at"] >= int(play_started_at) - 5):
                    if sample["progress"] >= sample["duration"] - 2:
                        reached_end = True
                        self._log("video_history_end", bvid=detail.bvid,
                                  position=round(position, 1))
                        break
                    if history_last_progress < 0:
                        history_last_progress = sample["progress"]
                        history_last_change_at = now
                    elif sample["progress"] != history_last_progress:
                        history_last_progress = sample["progress"]
                        history_last_change_at = now
                    elif now - history_last_change_at >= NOT_PLAYING_AFTER_S:
                        not_playing = True
                        self._log("video_not_playing", bvid=detail.bvid,
                                  progress=sample["progress"],
                                  stalled_s=round(now - history_last_change_at, 1))
                        break
            due = scheduler.due(position)
            if due:
                cue = due[-1]
                for old in due[:-1]:
                    scheduler.mark_fired(old)
                    self._cues_dropped += 1
                    self._log("cue_skipped_late", bvid=detail.bvid,
                              cue_id=old.cue_id)
                if cue.kind == "closing" and self.speech.busy():
                    # 收尾问候不能因为上一句还在响就丢；留到 _close_cues 等语音空出来。
                    self._log("closing_wait_speech", bvid=detail.bvid,
                              cue_id=cue.cue_id)
                elif self.speech.busy():
                    scheduler.mark_fired(cue)
                    self._cues_dropped += 1
                    self._log("cue_dropped_speech_busy", bvid=detail.bvid,
                              cue_id=cue.cue_id)
                elif self.speech.offer(strip_danmaku_word(cue.text)):
                    scheduler.mark_fired(cue)
                    self._cues_fired += 1
                    self._log("cue_say", bvid=detail.bvid, cue_id=cue.cue_id,
                              kind=cue.kind,
                              text=strip_danmaku_word(cue.text)[:80])
                else:
                    scheduler.mark_fired(cue)
                    self._cues_dropped += 1
                    self._log("cue_dropped", bvid=detail.bvid, cue_id=cue.cue_id)
            elif self.config.dynamic_reactions or self.config.max_replies > 0:
                reply = replies.due(position)
                if reply is not None and not self.speech.busy():
                    reply_text = strip_danmaku_word(reply.text)
                    if self.speech.offer(reply_text):
                        replies.mark_used(reply, position)
                        self._replies_fired += 1
                        self._log("danmaku_reply", bvid=detail.bvid,
                                  reply_id=reply.reply_id,
                                  text=reply_text[:80])

            if position >= plan.duration_s:
                reached_end = True
                break
            if now - last_status >= 2.0:
                last_status = now
                self._maybe_status("PLAYING", position, plan.duration_s,
                                   profile.title, {"bvid": detail.bvid})
                # Chrome 的 hwnd 是异步找到的；顺手刷新控制台浏览器状态。
                self._publish_playlist(state="playing")
            self._sleep(self.sleep_s)

        if self._switching and not self._should_stop():
            # 控制台指定了另一条视频：关掉旧标签页，回到 run() 用 override 准备。
            self._switching = False
            if self.browser is not None and hasattr(self.browser, "close"):
                try:
                    self.browser.close()
                except Exception:
                    pass
            self._log("video_switch_out", bvid=detail.bvid,
                      target=self._override_bvid)
            self._publish_playlist(state="selecting")
            return
        if browser_lost:
            self._status(screen="SELECTING", last="浏览器已关闭，准备下一个",
                         bvid=detail.bvid, video_title=profile.title[:80],
                         position_s=0.0, duration_s=0.0)
            self._log("video_browser_closed", bvid=detail.bvid)
            self._publish_playlist(state="between")
            return
        if not_playing:
            # 保持同一个 Chrome 窗口/标签页，先切空白页避免静帧继续被采集；
            # 下一条会在同一个标签页里导航，不再关窗重开触发 Chrome 恢复旧标签。
            blank = getattr(self.browser, "blank", None) if self.browser else None
            if callable(blank):
                try:
                    result = blank()
                except Exception as exc:  # noqa: BLE001
                    result = {"ok": False, "error": str(exc)}
                self._log("video_not_playing_blank",
                          ok=bool(result.get("ok")),
                          error=str(result.get("error") or "")[:120])
                if not result.get("ok") and hasattr(self.browser, "close"):
                    try:
                        self.browser.close()
                    except Exception:  # noqa: BLE001
                        pass
            elif self.browser is not None and hasattr(self.browser, "close"):
                try:
                    result = self.browser.close()
                except Exception as exc:  # noqa: BLE001
                    result = {"ok": False, "error": str(exc)}
                self._log("video_not_playing_close",
                          ok=bool(result.get("ok")),
                          error=str(result.get("error") or "")[:120])
            self.stores.failed.mark(
                detail.bvid, self.config.skip_cooldown_min * 60)
            self._status(screen="SELECTING", last="视频没有播放，强制下一条",
                         bvid=detail.bvid, video_title=profile.title[:80],
                         position_s=0.0, duration_s=0.0)
            self._log("video_not_playing_skip", bvid=detail.bvid)
            self._publish_playlist(state="between")
            return
        # 外部停止/切换配置/暂停中被停止都不是自然结束；不能发 closing，
        # 也不能把视频误记为已看。
        if not skipped and not reached_end:
            stopped = True
        if not stopped and not skipped:
            self._close_cues(plan, scheduler, detail)
        if stopped:
            self._log("video_stopped", bvid=detail.bvid)
            return
        if skipped:
            self.stores.failed.mark(
                detail.bvid, self.config.skip_cooldown_min * 60)
            self._status(screen="SELECTING", last="已跳过，准备下一个",
                         bvid=detail.bvid, video_title=profile.title[:80],
                         position_s=0.0, duration_s=0.0)
        else:
            self._status(screen="SELECTING", last="视频结束，准备下一个",
                         bvid=detail.bvid, video_title=profile.title[:80],
                         position_s=0.0, duration_s=0.0)
        self._log("video_end", bvid=detail.bvid, skipped=skipped,
                  cues_fired=self._cues_fired, cues_dropped=self._cues_dropped,
                  replies_fired=self._replies_fired)
        self._publish_playlist(state="between")

    def _open_greeting(self, plan, scheduler: CueScheduler,
                       detail: VideoDetail) -> None:
        """开场先打招呼；不跟普通 cue 抢单槽语音。"""
        opening = next((cue for cue in plan.cues if cue.kind == "opening"), None)
        if opening is None or opening.cue_id in scheduler.fired:
            return
        scheduler.mark_fired(opening)
        offer = getattr(self.speech, "offer_when_free", None)
        if callable(offer):
            ok = offer(opening.text, wait_s=6.0, should_stop=self._should_stop)
        elif not self.speech.busy():
            ok = self.speech.offer(opening.text)
        else:
            ok = False
        if ok:
            self._cues_fired += 1
            self._log("cue_say", bvid=detail.bvid, cue_id=opening.cue_id,
                      kind="opening", text=opening.text[:80])
        else:
            self._cues_dropped += 1

    def _close_cues(self, plan, scheduler: CueScheduler, detail: VideoDetail) -> None:
        closing = [cue for cue in plan.cues
                   if cue.kind == "closing" and cue.cue_id not in scheduler.fired]
        if not closing:
            return
        cue = closing[-1]
        scheduler.mark_fired(cue)
        offer = getattr(self.speech, "offer_when_free", None)
        if callable(offer):
            ok = offer(cue.text, wait_s=6.0, should_stop=self._should_stop)
        elif not self.speech.busy():
            ok = self.speech.offer(cue.text)
        else:
            ok = False
        if ok:
            self._cues_fired += 1
            self._log("cue_say", bvid=detail.bvid, cue_id=cue.cue_id,
                      kind="closing", text=cue.text[:80])
        else:
            self._cues_dropped += 1

    # ---------------------------------------------------------------- 数据
    def _popular(self) -> list[VideoRef]:
        cached = self.stores.popular.get("popular:60")
        if isinstance(cached, list):
            refs = [VideoRef.from_dict(row) for row in cached if isinstance(row, dict)]
            refs = [ref for ref in refs if ref is not None]
            if refs:
                self._log("popular_cache_hit", count=len(refs))
                return refs
        try:
            refs = self.client.popular(limit=60)
        except BiliError as exc:
            self._log("popular_failed", error=exc.message[:200])
            return []
        self.stores.popular.put(
            "popular:60", [ref.to_dict() for ref in refs],
            now=self._wall())
        return refs

    def _detail(self, ref: VideoRef) -> VideoDetail:
        cached = self.stores.videos.get(ref.bvid)
        if isinstance(cached, dict):
            detail = VideoDetail.from_dict(cached)
            if detail is not None and detail.duration_s > 0:
                return detail
        try:
            detail = self.client.view(ref.bvid)
        except BiliError as exc:
            # 热门列表已经带了 duration/cid/owner/stat；没有 cid 才必须详情。
            if ref.cid <= 0 or ref.duration_s <= 0:
                raise
            self._log("view_fallback", bvid=ref.bvid, error=exc.message[:160])
            detail = VideoDetail(
                bvid=ref.bvid, aid=ref.aid, title=ref.title, desc=ref.desc,
                duration_s=ref.duration_s, owner_mid=ref.owner_mid,
                owner_name=ref.owner_name, stat=ref.stat, cid=ref.cid,
                videos=ref.videos, tname=ref.tname, pubdate=ref.pubdate,
            )
        if detail.videos > 1 and not self.config.allow_multi_part:
            raise BiliError("多 P 视频，默认跳过")
        if detail.duration_s <= 0:
            raise BiliError("视频时长不可用")
        self.stores.videos.put(ref.bvid, detail.to_dict(), now=self._wall())
        return detail

    def _comments(self, detail: VideoDetail) -> list[Comment]:
        cached = self.stores.comments.get(detail.bvid)
        if isinstance(cached, list):
            rows = [Comment.from_dict(row) for row in cached if isinstance(row, dict)]
            rows = [row for row in rows if row is not None]
            if rows:
                return rows
        rows = self.client.hot_comments(detail.aid, self.config.target_comments)
        self.stores.comments.put(
            detail.bvid, [row.to_dict() for row in rows], now=self._wall())
        return rows

    def _danmaku(self, detail: VideoDetail) -> DanmakuResult:
        cached = self.stores.danmaku.get(detail.bvid)
        if isinstance(cached, dict):
            result = DanmakuResult.from_dict(cached)
            if result.items or result.segments_expected > 0:
                return result
        result = self.client.danmaku(
            detail.cid, detail.aid, detail.duration_s,
            max_items=self.config.max_danmaku_per_video,
        )
        self.stores.danmaku.put(
            detail.bvid, result.to_dict(), now=self._wall())
        return result

    # ---------------------------------------------------------------- 控制/状态
    def _clear_controls(self) -> None:
        for path in (self._pause_path, self._skip_path):
            try:
                path.unlink()
            except OSError:
                pass

    def _consume_skip(self) -> bool:
        if not self._skip_path.exists():
            return False
        try:
            self._skip_path.unlink()
        except OSError:
            pass
        return True

    def _config_switched(self) -> bool:
        mtime = self._mtime()
        if not mtime or mtime == self._config_mtime:
            return False
        self._config_mtime = mtime
        game = self._read_config_game()
        if game and self._config_game and game != self._config_game:
            self._local_stop = True
            self._stop_reason = "配置已切换到其他模式"
            return True
        self._config_game = game
        return False

    def _mark_failed(self, bvid: str, reason: str) -> None:
        self.stores.failed.mark(bvid, self.config.failed_cooldown_min * 60)
        self._log("video_failed", bvid=bvid, reason=str(reason)[:200])


def _make_search_planner(config: CommentaryConfig):
    try:
        from dafeiyu.agent.planner import LLMPlanner

        return LLMPlanner(
            LiveConfig.load(), timeout=config.llm_timeout_s,
            system_prompt=SEARCH_SYSTEM, thinking=False,
        )
    except Exception:
        return None


def _ref_view(ref: VideoRef) -> int:
    stat = ref.stat if isinstance(ref.stat, dict) else {}
    return _as_int(stat.get("view"))


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fmt(seconds: float) -> str:
    seconds = max(0, int(float(seconds or 0)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# ---------------------------------------------------------------- 模块级入口
def _active() -> bool:
    return bool(_thread is not None and _thread.is_alive())


def _empty_status() -> dict:
    return {
        "running": False, "game": GAME_ID, "screen": "", "step": 0,
        "last": "", "error": "", "mode": "", "pace": "", "pid": 0,
        "started_at": 0.0, "updated_at": 0.0, "stopping": False,
        "stop_requested": False,
    }


def _own_status() -> dict:
    raw = _host.status()
    if str(raw.get("game") or "") != GAME_ID:
        return _empty_status()
    return raw


def start(game: str = "", *, mode: str | None = None,
          pace: str | None = None) -> dict:
    global _thread, _engine
    mode_raw = str(mode or "llm").strip().lower()
    if mode_raw == "local":
        return {
            "ok": False, "game": GAME_ID,
            "message": "B站云观影需要 LLM 模式：请选「LLM 规划」或 --mode llm",
        }
    if mode_raw not in ("", "llm", "live", "commentary"):
        return {"ok": False, "game": GAME_ID,
                "message": f"不支持的云观影模式：{mode_raw}"}
    pace_raw = str(pace or _host.pace or "voice").strip().lower()
    if pace_raw not in ("voice", "normal"):
        return {"ok": False, "game": GAME_ID, "message": "节奏只能是 voice 或 normal"}

    with _lock:
        if _active():
            return {"ok": True, "already": True, "game": GAME_ID,
                    "message": "云观影已经在跑", "status": _own_status()}
        current = _own_status()
        if current.get("running"):
            pid = _as_int(current.get("pid"))
            # 同进程但没有线程/其他进程且 pid 已死，都是上次崩溃留下的脏状态。
            if pid and pid != os.getpid() and _host.pid_alive(pid):
                return {"ok": True, "already": True, "game": GAME_ID,
                        "message": "云观影已经在跑", "status": current}
            if pid == os.getpid() and _active():
                return {"ok": True, "already": True, "game": GAME_ID,
                        "message": "云观影已经在跑", "status": current}
        raw = _host.status()
        if raw.get("running") and str(raw.get("game") or "") != GAME_ID:
            return {"ok": False, "game": GAME_ID,
                    "message": f"另一个模式正在跑（{raw.get('game')}），先停手再切云观影",
                    "status": raw}

        cfg = LiveConfig.load()
        commentary = load_commentary_config(
            Path(__file__).parent, live_config=cfg)
        persona = load_bili_persona()
        try:
            from dafeiyu.agent.planner import LLMPlanner

            planner = LLMPlanner(
                cfg, timeout=commentary.llm_timeout_s,
                system_prompt=commentary_system(persona), thinking=False,
            )
            ok, why = planner.available()
            if not ok:
                return {"ok": False, "game": GAME_ID, "message": why}
        except Exception as exc:
            return {"ok": False, "game": GAME_ID,
                    "message": f"LLM 初始化失败：{type(exc).__name__}: {exc}"}

        _host.clear_stop()
        _host.reset(GAME_ID, mode="live", pace=pace_raw)
        _host.set_status(
            running=True, game=GAME_ID, mode="live", pace=pace_raw,
            screen="SELECTING", step=0, last="启动云观影", error="",
            idle_text="看视频ing",
            started_at=time.time(), pid=os.getpid(), stopping=False,
        )
        stores = Stores()
        builder = ProfileBuilder(commentary, planner=planner,
                                 cache=stores.profiles, now=time.time,
                                 persona=persona)
        _engine = BiliEngine(
            _host, commentary, stores=stores, builder=builder,
            persona=persona,
        )
        _thread = threading.Thread(
            target=_engine.run, name="dafeiyu-bili-commentary", daemon=True)
        _thread.start()
        return {"ok": True, "game": GAME_ID, "mode": "live", "pace": pace_raw,
                "message": "云观影已开始", "status": _own_status()}


def stop() -> dict:
    global _thread, _engine
    current = _own_status()
    if not _active() and not current.get("running"):
        return {"ok": True, "game": GAME_ID, "message": "云观影没有在跑",
                "status": current}
    _host.request_stop()
    thread = _thread
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(3.0)

    current = _own_status()
    remote = bool(
        current.get("running")
        and int(current.get("pid") or 0) not in (0, os.getpid())
        and _host.pid_alive(current.get("pid"))
    )
    if remote or (thread is not None and thread.is_alive()):
        _host.set_status(stopping=True, last="正在停止云观影…")
        return {"ok": True, "message": "正在停止…", "status": _own_status()}

    _thread = None
    _engine = None
    _host.clear_stop()
    _host.set_status(
        running=False, stopping=False, screen="STOPPED", game=GAME_ID,
        last="已停手", speech_busy=False, idle_text="",
    )
    return {"ok": True, "message": "已停手", "status": _own_status()}


def status() -> dict:
    return _own_status()


__all__ = ["BiliEngine", "GAME_ID", "bind_host", "start", "status", "stop"]
