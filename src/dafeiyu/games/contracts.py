"""游戏插件的稳定契约。

这一层只放「跨游戏协议」，不放任何具体游戏的规则、牌表或界面名：

- :class:`GameManifest`：游戏 ID、版本、入口、能力、Skill/Prompt 声明；
- :class:`GameCapabilities`：观察、控制、自动玩、回放等能力；
- :class:`GameStateEnvelope`：通用状态摘要 + 插件自己的类型化 data；
- :class:`GameEvent`：状态变化、动作结果、阶段事件的领域事件；
- :class:`ActionResult`：动作执行状态、错误原因和结果摘要。

兼容说明
--------
``dafeiyu.games.base.GameState`` 仍是现有适配器的运行时快照类型，P0 不动它；
本模块的 :class:`GameStateEnvelope` 是后续插件契约的目标形状，先由测试和
manifest 校验使用。两者在 P1/P2 迁移时再做显式转换，避免现在改变直播 payload。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

MANIFEST_SCHEMA = 2
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_CAPABILITY_FLAGS = ("observe", "control", "autoplay", "replay", "commentary")


def _text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _stable(value: Any) -> str:
    """把任意 JSON 值变成稳定字符串；非 JSON 类型退回 repr，保证 digest 可比较。"""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


@dataclass(frozen=True)
class ManifestIssue:
    """一条清单校验结果；error 阻止加载，warning 只提示迁移债务。"""

    level: str
    field: str
    message: str

    def to_dict(self) -> dict:
        return {"level": self.level, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class GameCapabilities:
    """游戏能做什么；没有声明的能力一律视为不支持。"""

    observe: bool = False
    control: bool = False
    autoplay: bool = False
    replay: bool = False
    commentary: bool = True
    modes: tuple[str, ...] = ()
    state_schema: str = ""
    action_schema: str = ""

    @classmethod
    def from_dict(cls, raw: dict | None) -> "GameCapabilities":
        raw = raw if isinstance(raw, dict) else {}
        modes_raw = raw.get("modes")
        if isinstance(modes_raw, str):
            modes = (modes_raw.strip(),) if modes_raw.strip() else ()
        else:
            modes = tuple(
                str(item).strip() for item in (modes_raw or ())
                if str(item).strip()
            )
        return cls(
            observe=_bool(raw.get("observe")),
            control=_bool(raw.get("control")),
            autoplay=_bool(raw.get("autoplay")),
            replay=_bool(raw.get("replay")),
            commentary=_bool(raw.get("commentary"), True),
            modes=modes,
            state_schema=_text(raw.get("state_schema")),
            action_schema=_text(raw.get("action_schema")),
        )

    def supports(self, name: str) -> bool:
        """按能力名查询；未知能力返回 False，不抛异常。"""
        if name in _CAPABILITY_FLAGS:
            return bool(getattr(self, name, False))
        return False

    def to_dict(self) -> dict:
        return {
            "observe": self.observe,
            "control": self.control,
            "autoplay": self.autoplay,
            "replay": self.replay,
            "commentary": self.commentary,
            "modes": list(self.modes),
            "state_schema": self.state_schema,
            "action_schema": self.action_schema,
        }


@dataclass(frozen=True)
class GameManifest:
    """``games/<id>/game.json`` 的规范化视图。

    旧包没有 ``schema/version/entry/capabilities`` 时仍能加载：``schema`` 视为 1，
    ``entry`` 回退到 ``adapter``，能力默认只有 ``observe``。新包应显式声明这些字段。
    """

    schema: int = 1
    id: str = ""
    version: str = ""
    name: str = ""
    entry: str = ""
    adapter: str = ""
    capabilities: GameCapabilities = field(default_factory=GameCapabilities)
    aliases: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    prompts: dict[str, str] = field(default_factory=dict)
    obs: str = ""
    launch: dict[str, Any] = field(default_factory=dict)
    entry_fallback: bool = False

    @classmethod
    def from_dict(
        cls,
        raw: dict | None,
        *,
        fallback_id: str = "",
        fallback_name: str = "",
        fallback_adapter: str = "",
    ) -> "GameManifest":
        raw = raw if isinstance(raw, dict) else {}
        game_id = _text(raw.get("id"), fallback_id)
        name = _text(raw.get("name"), fallback_name or game_id)
        adapter = _text(raw.get("adapter"), fallback_adapter)
        entry_raw = _text(raw.get("entry"))
        entry = entry_raw or adapter
        try:
            schema = int(raw.get("schema") or 1)
        except (TypeError, ValueError):
            schema = 1
        if schema < 1:
            schema = 1

        cap_raw = raw.get("capabilities")
        if isinstance(cap_raw, dict):
            capabilities = GameCapabilities.from_dict(cap_raw)
        else:
            # 旧清单没有能力声明：有 adapter 就至少能观察，不能假设能自动玩。
            capabilities = GameCapabilities(
                observe=bool(adapter or entry), commentary=True)

        aliases_raw = raw.get("aliases")
        if isinstance(aliases_raw, str):
            aliases = (aliases_raw.strip(),) if aliases_raw.strip() else ()
        else:
            aliases = tuple(
                str(item).strip() for item in (aliases_raw or ())
                if str(item).strip()
            )

        skills_raw = raw.get("skills")
        if isinstance(skills_raw, str):
            skills = (skills_raw.strip(),) if skills_raw.strip() else ()
        else:
            skills = tuple(
                str(item).strip() for item in (skills_raw or ())
                if str(item).strip()
            )

        prompts_raw = raw.get("prompts")
        prompts = {
            str(k): str(v) for k, v in (prompts_raw or {}).items()
            if str(k).strip() and str(v).strip()
        } if isinstance(prompts_raw, dict) else {}

        launch = dict(raw.get("launch") or {}) if isinstance(raw.get("launch"), dict) else {}
        return cls(
            schema=schema,
            id=game_id,
            version=_text(raw.get("version")),
            name=name,
            entry=entry,
            adapter=adapter,
            capabilities=capabilities,
            aliases=aliases,
            skills=skills,
            prompts=prompts,
            obs=_text(raw.get("obs")),
            launch=launch,
            entry_fallback=not bool(entry_raw) and bool(adapter),
        )

    def validate(self) -> tuple[ManifestIssue, ...]:
        """返回结构化问题；P0 的 registry 只记录，不据此拒绝旧包。"""
        issues: list[ManifestIssue] = []

        def add(level: str, field_name: str, message: str) -> None:
            issues.append(ManifestIssue(level=level, field=field_name, message=message))

        if not self.id:
            add("error", "id", "缺少游戏 id")
        elif not _ID_RE.match(self.id):
            add("error", "id", f"游戏 id 不合法：{self.id!r}（只允许小写字母/数字/._-）")
        if not self.name:
            add("warning", "name", "缺少 name，控制台会显示 id")
        if self.schema < MANIFEST_SCHEMA:
            add("warning", "schema",
                f"清单 schema={self.schema}，建议升级到 {MANIFEST_SCHEMA}")
        if not self.version:
            add("warning", "version", "缺少游戏版本，历史录像/回归无法按版本对齐")
        if not self.entry:
            add("error", "entry", "缺少入口（entry 或 adapter）")
        elif self.entry_fallback:
            add("warning", "entry", "未声明 entry，暂用 adapter 作为入口")
        if not any((self.capabilities.observe, self.capabilities.control,
                    self.capabilities.autoplay)):
            add("warning", "capabilities",
                "observe/control/autoplay 都为 false，核心不知道如何使用这个游戏")
        if self.capabilities.autoplay and not self.version:
            add("warning", "capabilities.autoplay",
                "声明了自动玩但缺少版本，回放/回归不可追溯")
        return tuple(issues)

    @property
    def errors(self) -> tuple[ManifestIssue, ...]:
        return tuple(i for i in self.validate() if i.level == "error")

    @property
    def warnings(self) -> tuple[ManifestIssue, ...]:
        return tuple(i for i in self.validate() if i.level == "warning")

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "entry": self.entry,
            "adapter": self.adapter,
            "capabilities": self.capabilities.to_dict(),
            "aliases": list(self.aliases),
            "skills": list(self.skills),
            "prompts": dict(self.prompts),
            "obs": self.obs,
            "launch": dict(self.launch),
            "entry_fallback": self.entry_fallback,
        }


@dataclass
class GameStateEnvelope:
    """通用状态信封：``summary`` 只给展示，``data`` 由插件按 schema 解释。"""

    game_id: str = ""
    revision: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy(
        cls,
        raw: dict | None,
        *,
        game_id: str = "",
        revision: str = "",
    ) -> "GameStateEnvelope":
        """从旧 ``GameState.to_dict()`` 形状构造，供迁移期适配器使用。"""
        raw = raw if isinstance(raw, dict) else {}
        summary_keys = (
            "screen", "floor", "hp", "max_hp", "gold", "character",
            "ascension", "in_combat", "turn",
        )
        summary = {key: raw[key] for key in summary_keys if key in raw}
        data = raw.get("data")
        if not isinstance(data, dict):
            data = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
        return cls(
            game_id=_text(game_id or raw.get("game_id")),
            revision=_text(revision or raw.get("revision")),
            summary=summary,
            data=dict(data),
        )

    def digest(self) -> tuple:
        return (
            self.game_id,
            self.revision,
            _stable(self.summary),
            _stable(self.data),
        )

    def to_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "revision": self.revision,
            "summary": dict(self.summary),
            "data": dict(self.data),
        }


@dataclass(frozen=True)
class GameEvent:
    """游戏领域事件；EventBus 只是传输，不定义游戏语义。"""

    game_id: str
    kind: str
    seq: int = 0
    at: float = 0.0
    summary: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    def to_bus(self):
        """转成核心 EventBus 事件；延迟 import 避免 contracts 依赖 core。"""
        from ..core.events import Event, Topics

        mapping = {
            "state": Topics.GAME_STATE,
            "turn": Topics.GAME_TURN,
            "error": Topics.GAME_ERROR,
        }
        topic = mapping.get(self.kind, Topics.GAME_EVENT)
        payload = {
            **dict(self.facts),
            **dict(self.data),
            "kind": self.kind,
            "seq": int(self.seq),
        }
        if self.summary:
            payload.setdefault("summary", self.summary)
        return Event(topic, payload, source=self.game_id, ts=self.at or time.time())


class ActionCode:
    """动作结果错误码；插件可扩展，但保留这几类是稳定语义。"""

    OK = "ok"
    INVALID_PLAN = "invalid_plan"
    ILLEGAL_ACTION = "illegal_action"
    STALE_STATE = "stale_state"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INTERNAL = "internal"


@dataclass(frozen=True)
class ActionResult:
    """一次动作/计划执行的结果，替代各工具各自约定的 ``{ok, err}`` 字典。"""

    ok: bool
    action: str = ""
    code: str = ""
    message: str = ""
    retryable: bool = False
    state: GameStateEnvelope | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        action: str = "",
        *,
        message: str = "",
        state: GameStateEnvelope | None = None,
        data: dict | None = None,
    ) -> "ActionResult":
        return cls(ok=True, action=action, code=ActionCode.OK,
                   message=message, state=state, data=dict(data or {}))

    @classmethod
    def failure(
        cls,
        action: str = "",
        *,
        code: str = ActionCode.INTERNAL,
        message: str = "",
        retryable: bool = False,
        state: GameStateEnvelope | None = None,
        data: dict | None = None,
    ) -> "ActionResult":
        return cls(ok=False, action=action, code=code or ActionCode.INTERNAL,
                   message=message, retryable=bool(retryable), state=state,
                   data=dict(data or {}))

    @classmethod
    def from_legacy(cls, raw: dict | None, *, action: str = "") -> "ActionResult":
        """把现有 ``{ok, err, ctx, receipt}`` 字典映射成稳定结果。"""
        raw = raw if isinstance(raw, dict) else {}
        ok = _bool(raw.get("ok"))
        message = _text(raw.get("err") or raw.get("error") or raw.get("message"))
        code = _text(raw.get("code")) or (ActionCode.OK if ok else ActionCode.INVALID_PLAN)
        state_raw = raw.get("state")
        if isinstance(state_raw, GameStateEnvelope):
            state = state_raw
        elif isinstance(state_raw, dict):
            state = GameStateEnvelope.from_legacy(state_raw)
        else:
            state = None
        data = {
            key: value for key, value in raw.items()
            if key not in {"ok", "err", "error", "message", "code", "retryable", "state"}
        }
        return cls(
            ok=ok,
            action=action,
            code=code,
            message=message,
            retryable=bool(raw.get("retryable", not ok)),
            state=state,
            data=data,
        )

    def to_dict(self) -> dict:
        out = {
            "ok": self.ok,
            "action": self.action,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "data": dict(self.data),
        }
        if self.state is not None:
            out["state"] = self.state.to_dict()
        return out

    def to_legacy(self) -> dict:
        """兼容旧调用方的 ``{ok, err, ...}`` 形状。

        新代码应直接读 :class:`ActionResult`；这个方法只给还没迁移的
        LangGraph 节点、调试脚本或第三方工具使用。``data`` 里的字段会
        原样摊平，因此旧的 ``context / receipt / dry_run`` 读取方式不坏。
        """
        out: dict[str, Any] = dict(self.data)
        out["ok"] = self.ok
        out["code"] = self.code
        if self.message:
            out.setdefault("message", self.message)
            if not self.ok:
                # 旧工具有的用 err、有的用 error；迁移层两个都保留。
                out.setdefault("err", self.message)
                out.setdefault("error", self.message)
        if self.retryable:
            out["retryable"] = True
        if self.state is not None:
            out.setdefault("state", self.state.to_dict())
        return out


def as_action_result(value: Any, *, action: str = "") -> ActionResult:
    """把插件工具的返回值统一成 :class:`ActionResult`。

    迁移期同时接受新契约和旧 ``{ok, err}`` dict；其他类型一律按内部错误
    处理，避免把 ``None`` 或半个对象误当成功。
    """
    if isinstance(value, ActionResult):
        return value
    if isinstance(value, dict):
        return ActionResult.from_legacy(value, action=action)
    return ActionResult.failure(
        action,
        code=ActionCode.INTERNAL,
        message=f"工具返回了不支持的类型：{type(value).__name__}",
    )


@runtime_checkable
class GameTools(Protocol):
    """可选的一回合工具集；纯观察游戏不实现它。"""

    def observe(self) -> ActionResult: ...

    def validate(self, plan: dict) -> ActionResult: ...

    def execute(self, plan: dict, *, dry_run: bool = True) -> ActionResult: ...


class AutoplayHost(Protocol):
    """核心提供给插件的运行宿主：停止、状态、表达和节奏由核心负责。"""

    def stopping(self) -> bool: ...

    def set_status(self, **fields) -> None: ...

    def say(self, text: str, *, facts: dict | None = None) -> None: ...

    def event(self, kind: str, label: str = "", **data) -> None: ...

    def wait_voice(self) -> None: ...


@runtime_checkable
class AutoplayRunner(Protocol):
    """插件自己的整局循环；核心不规定它必须用哪种决策流程。"""

    def start(self, *, game: str = "", mode: str | None = None,
              pace: str | None = None) -> dict: ...

    def stop(self) -> dict: ...

    def status(self) -> dict: ...


@runtime_checkable
class GamePlugin(Protocol):
    """插件入口形状；P1 起由 registry 的 entry 指向实现。"""

    manifest: GameManifest

    def adapter(self, **kwargs): ...

    def tools(self) -> GameTools | None: ...

    def runner(self, host: AutoplayHost | None = None) -> AutoplayRunner | None: ...

    def prompt(self, name: str) -> str: ...


__all__ = [
    "ActionCode", "ActionResult", "AutoplayHost", "AutoplayRunner",
    "GameCapabilities", "GameEvent", "GameManifest", "GamePlugin",
    "GameStateEnvelope", "GameTools", "MANIFEST_SCHEMA", "ManifestIssue",
    "as_action_result",
]
