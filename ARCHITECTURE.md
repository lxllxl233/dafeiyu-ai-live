# 架构说明

> 目标：把「AI 直播」拆成可替换的层。游戏只是输入源，性格只是文案策略，声音只是输出通道。
> 任何一层都能单独替换、单独测试、单独失败。

## 1. 分层与依赖方向

```
        ┌────────────────────────────────────────────┐
        │                 UI 层（ui/）                │
        │  OBS 桌宠气泡 · 控制台 · 桌面桌宠             │
        └───────────────▲───────────────┬────────────┘
                        │HTTP / SSE     │POST /api/say /api/voice
        ┌───────────────┴───────────────▼────────────┐
        │            源码 / 内核（src/dafeiyu）       │
        │  EventBus ←→ LiveSession → OverlayPublisher │
        └──────▲────────────▲────────────▲───────────┘
               │            │            │
   ┌───────────┴──┐  ┌──────┴─────┐  ┌───┴──────────┐
   │ 游戏适配器    │  │ 性格层      │  │ 语音层        │
   │ games/*      │  │ persona/*  │  │ voice/*      │
   └──────────────┘  └────────────┘  └──────────────┘
```

依赖是单向的：`games` / `persona` / `voice` 不 import UI，UI 不 import 具体游戏；
内核只依赖抽象（`GameAdapter`、`Voice` 协议、人设卡 JSON）。

仓库只有一个根代码入口 `main.py`：无参数进 `dafeiyu.ui.desktop`（桌宠 + 设置页），
带子命令进 `dafeiyu.cli`。所有 Python 实现都在 `src/dafeiyu/`；`ui/`、`persona/`、
`voice/`、`games/` 根目录只放资产与清单。

| 层 | 输入 | 输出 | 失败时 |
|---|---|---|---|
| 游戏适配器 | 游戏/兼容层状态 | `GameState`、`game.*` 事件 | 返回空状态，不影响开播 |
| 性格层 | thought / meme / 整句 | 拼句 + lint + 节奏建议 | lint 只报告，不阻断（`strict_lint` 可开） |
| 语音层 | 文本 | WAV / `voice.ready` / `voice.failed` | 回退动物语；`off` 时静音但字幕照常 |
| UI 服务 | `/api/*`、`/comment` | 桌宠气泡、控制台数据 | HTTP 错误码；页面轮询自愈 |

## 2. 事件目录

主题定义在 `src/dafeiyu/core/events.py`，总线支持 `fnmatch` 通配（`game.*`）。

| 主题 | 发布者 | 载荷（关键字段） |
|---|---|---|
| `session.started` / `session.stopped` | LiveSession | persona / voice / game |
| `game.connected` / `game.error` | LiveSession | game_id / error |
| `game.state` | 适配器或 session | screen / floor / hp / max_hp / gold / character / ascension / in_combat / turn |
| `game.turn` | 适配器 | 同上（战斗内） |
| `commentary.say` | LiveSession | text / lines / thought / meme / lint / client_id |
| `commentary.pushed` | LiveSession | ok / client_id / error |
| `voice.started` / `voice.ready` / `voice.failed` | LiveSession 配音线程 | voice / duration / bytes / error |
| `ui.pushed` | 预留 | — |

处理器同步执行、异常隔离（`EventBus.errors`），所以处理器里不要做重活。
配音是唯一的重活，放在 `LiveSession` 自己的单线程队列里，保证曼波串行。

## 3. 会话生命周期

```python
session = LiveSession(config)          # 装配 persona / voice / publisher
session.start(game_adapter)            # 起配音线程、发 session.started、读一帧状态
session.update_state({...})            # 游戏适配器/CLI 灌状态
session.say("它 21", "差 6，嗨呀")     # 拼句 → lint → 推 UI → 后台配音
session.wait_idle()                    # CLI/演示用；直播主链路不等
session.stop()                         # 排空配音队列、发 session.stopped
```

`say()` 的 payload 就是 overlay 的 `/comment` 契约：

```json
{
  "thought": "它 21，我最多挡 15",
  "meme": "差 6，嗨呀。",
  "speech": "它 21，我最多挡 15。差 6，嗨呀。",
  "client_id": "9f3c1a2b7d4e",
  "character": "铁甲战士", "floor": 6, "hp": 52, "max_hp": 80,
  "ascension": 10, "gold": 99, "screen": "COMBAT"
}
```

页面优先用 `speech`（已拼好的整句），没有才回退 `thought + meme`。
`client_id` 把客户端发布、页面气泡渲染、音频起播三端串起来（`logs/av_*.jsonl`）。

## 4. 语音层

`VoiceRouter` 是唯一出口：

```python
result = router.speak(text, voice="mambo")   # 首选 + 回退
result.ok / result.voice / result.duration / result.cached
```

- `available()` 是「能不能用」：曼波 ping `/control`，动物语恒真。
- 曼波失败（离线 / 超时 / 坏 WAV）→ 动物语，`counts.fallbacks +1`。
- 缓存按 `sha1(text)[:20]`，`logs/tts_cache` 同时服务页面 `/tts` 和桌面窗。
- 人设卡的 `voice` 只是默认值，`/api/say` 可以逐句覆盖。

## 5. 游戏适配器契约

```python
class GameAdapter:
    game_id: str
    def available(self) -> bool          # 短超时，不抛异常
    def snapshot(self) -> GameState      # 读不到返回空 GameState
    def events(stop, interval) -> Iterator[Event]   # 默认实现只发 GAME_STATE
    def status(self) -> dict             # 控制台/诊断用
```

约定：

- 适配器**只读**游戏状态，不执行动作。出牌/菜单操作仍归兼容层 CLI（`tools/sts2.py`）或 Agent。
- `GameState.digest()` 决定去重：只有字段变化才发事件。
- 适配器挂掉 = 游戏离线，不是直播故障；`LiveSession.status()` 会带上错误。

### 游戏插件

`game.json` 的 `entry` 指向插件类（P1 起是 `plugin.py:Sts2Plugin`），
`src/dafeiyu/games/registry.py` 用合成包名加载带连字符的游戏目录，因此游戏包内部
可以正常使用相对 import。插件实现 `dafeiyu.games.contracts.GamePlugin`：

| 能力 | 契约 | 当前用途 |
|---|---|---|
| 适配器 | `adapter(**kwargs)` | 只读状态，仍复用原 adapter |
| 决策工具 | `tools()` → `GameTools` | 读局面 / 验算 / 执行；统一返回 `ActionResult` |
| 自动玩 | `runner()` → `AutoplayRunner` | `start` / `stop` / `status` |
| 提示词 | `prompt("decision")` | 交给 `LLMPlanner`，核心不内置游戏规则 |

核心 `agent/` 只保留跨游戏的编排、协议和演示工具；`Sts2Tools`、整局循环和
游戏 System Prompt 都在 `games/slay-the-spire-2/`。`agent/play.py` 是 facade：
按配置/状态文件找插件，创建 `AutoplayHost` 并通过 `plugin.runner(host)` 注入，
再转发 `start_autoplay / stop_autoplay / status`。

**开局设置（可选）**：游戏包可以在 `game.json` 里声明 `start.options`
（`choice` / `int` / `bool`，带 label、default、choices 或 min/max）。控制台按声明渲染
「开局设置」卡片，值存 `config.json` 的 `start_options.<game-id>`；清洗逻辑在
`src/dafeiyu/games/start_options.py`（不认识任何具体游戏），游戏包自己的模块
（如 `games/slay-the-spire-2/start_options.py`）负责在开新局前读取。

工具返回值统一走 `dafeiyu.games.contracts.ActionResult`（`ok / action / code /
message / retryable / data`）。迁移期 LangGraph 入口仍接受旧 `{ok, err}` dict，
通过 `as_action_result()` 归一化；旧调用方也可以用 `ActionResult.to_legacy()`
拿回摊平的 dict。这样新插件不需要复制一套错误字段约定，旧脚本也不会立刻坏。

STS2 的决策引擎也已整体迁进游戏包：`ctx.py` / `act.py` / `advisor.py` /
`sim.py` / `gates.py` / `dps.py` / `path.py` / `noncombat.py` / `mech.py` /
`decision_log.py`，以及它们依赖的 `hard_rules.py` / `lesson_index.py` / `perf.py`，
都在 `games/slay-the-spire-2/decision/`。`tools/` 下同名文件只是兼容 shim，
用旧模块名加载同一份实现；老命令（`python tools/ctx.py` 等）和旧 skill 不受影响。

`games/toy-duel/` 是第二个完整插件，也是跨游戏门禁的活样例：`adapter.py` 只读
`logs/toy_duel_state.json`，`engine.py` 的 `ToyTools` 返回 `ActionResult`，
`runner.py` 用同一个 `AutoplayHost` 自动玩 12 步；不启动 Steam、不读 STS2 状态。
它证明 `GameRegistry` / facade / host / 工具协议没有绑定《杀戮尖塔 2》。

`games/vampire-survivors/` 是第三个包，演示实时动作类怎么「用 API 而不是窗口」
接：游戏侧 `compat/mod/VsBridge`（BepInEx 6 IL2CPP）在 `127.0.0.1:8091` 暴露
`GET /state` 和 `POST /act`，`compat/vs.py` 只走 HTTP；`engine.py` 不发键盘、
不找窗口，`adapter.py` 以桥是否在线判断可用性。桥负责过菜单、升级和结算，
Python 只负责走位/选择，所以游戏可以在后台跑，不会抢焦点。实现参考了 MIT 的
jev_vampire_survivors（Linux/Mono）和 Off the Clock 的 BepInEx 6 IL2CPP 模板。

`games/catalog.py` 负责跨游戏的“身份”查询：默认游戏、别名、显示名和旧环境变量
前缀都从 `games/registry.json` + `game.json` 读取。`LiveConfig.game` 为空时自动
选默认游戏；录像目录、`replay --game` 和 `DAFEIYU_*` / 旧 `<ALIAS>_*` 环境变量
也走同一个解析，核心不再写死游戏 id 或显示名。

### 启动游戏

`game.json` 的 `launch` 是唯一启动配置，`src/dafeiyu/games/launch.py` 负责执行：

| type | 字段 | 行为 |
|---|---|---|
| `steam` | `app_id` | 打开 `steam://rungameid/<app_id>` |
| `exe` | `path` / `args` / `cwd` | 相对游戏包目录或绝对路径启动 |
| `url` | `url` | 默认浏览器打开 |

设置页「运行 → 只启动游戏」、控制台按钮、`POST /api/game/launch` 和
`python main.py game launch` 共用这个入口；没配置 `launch` 的游戏明确报错，不会悄悄失败。

### 让她玩

`src/dafeiyu/agent/play.py` 是只负责转发的 facade；真正整局循环在游戏包的
`autoplay/runner.py`。STS2 先 `launch_game`，再按 `screen` 分派战斗/奖励/地图/
营火/事件/商店/宝箱；`toy-duel` 不启动外部程序，只在自己的状态文件上跑
`observe → plan → execute`。通用生命周期由 `src/dafeiyu/agent/host.py` 的
`AutoplayHost` 提供：状态文件、停止文件、OBS 录像、token 累计、说话等待和步骤日志；
游戏 runner 只调用 host，不再关心这些 I/O。
战斗读 `games/slay-the-spire-2/decision/ctx.py` 同源局面：

- `local`：`decision/advisor.py` 本地算伤害/格挡/斩杀，走 `sts2 batch`；
- `llm`：**每个玩家决策都问模型**。战斗回合走 LangGraph `run_turn` + `LLMPlanner` +
  `decision/act.py`（`seq` 协议）；界面点击走 `autoplay/runner.py::_screen_llm` +
  `decision/actions.py` 的白名单菜单（只列当前真正可点的项，禁用/越界动作直接拒）。
  默认模型 `deepseek-chat`；模型超时/输出不可执行才本地兜底并记 `llm_fallback`，
  执行失败才停手。`MAIN_MENU` / `CHARACTER_SELECT` / `MODAL` 是过场管道，不调用模型。
- `GAME_OVER`：回主菜单再开一局；未知界面/同一失败连续 3 次会停手并写 `error`。

播报节奏也在同一份配置里：`voice` 按上一句字数估算播完时间（至少 2 秒），
`normal` 是旧版 0.7 秒。模式/节奏写 `config.json`，桌宠设置页、控制台和 CLI 共用；
跨进程控制不靠内存：`logs/autoplay.stop` 是停手文件，`logs/autoplay.json` 是状态，
`logs/autoplay.jsonl` 是步骤日志。未知界面不猜按钮，直接停在那一步并写 `last/error`。

### LLM 直播模式（导演）

`src/dafeiyu/agent/director.py` 是跨游戏的编排层，只做三件事：

```
modules()  ← games/<id>/game.json 的 live 块（enabled/label/role/weight）
   │
   ▼
LLMPlanner.plan(候选 + 最近历史 + 已播时长)  →  {module, minutes, say, reason}
   │
   ▼
stop_autoplay() → config.game = <module> → start_autoplay(game, mode)
```

- 候选完全来自游戏包：核心不写死 id / 显示名，`toy-duel` 这类验收包不声明
  `live` 就不会出现；`bilibili-hot-commentary` 声明了 `live` 就自动进候选。
- 模型只决定「播哪个、播多久、过渡说一句」；怎么玩仍由各游戏包的 runner 决定。
- 模型不可用/输出不合法 → 按顺序轮换并记 `director_fallback`；状态写
  `logs/live_director.json`，停止标志是 `logs/live_director.stop`。
- Token 账单仍走 `AutoplayHost.add_usage(..., note="直播导演 选择模块")`，
  汇总仍由 `logs/autoplay.jsonl` 出。
- 手动「让她玩」/「让她停手」/`play --stop` 会先停导演，避免手动启动的模块被
  导演下一段切走。

## 6. UI 服务

只起一个 HTTP 服务（默认 `127.0.0.1:8730`），因为 OBS 浏览器源和控制台必须看同一份状态：

| 路径 | 作用 |
|---|---|
| `/` | 桌宠气泡页（OBS 源）：大肥鱼精灵 + 漫画气泡 |
| `/studio` | 控制台单页 |
| `/api/studio` | 四层状态聚合 + 决策/日志/Token 遥测（人设/语音/游戏/overlay/最近台词） |
| `/api/config` | GET 配置（Key 只报是否已配置）/ POST 保存语音、模型、游戏、API |
| `/api/game/launch` | 按 `config.game` 或 `{game}` 只启动游戏 |
| `/api/game/start` | 开局设置：GET 返回声明 + 当前值，POST `{values}` 按声明清洗后写 `config.json` |
| `/api/play/start` | `{mode?, pace?}` 启动自动玩；`mode=director` 启动 LLM 直播模式；默认读 config |
| `/api/play/stop` | 跨进程停手 |
| `/api/agent/run` | 跑一轮离线 Agent 演示（不碰游戏） |
| `/api/voice/start` | 启动本地曼波引擎（MamboTTS `run_engine.py`） |
| `/api/say` | 拼句 + lint + `add_comment`；`dry_run` 只算 lint |
| `/api/voice` | 同步合成返回 WAV（试听） |
| `/events` | SSE，新台词即时到页 |
| `/state`、`/health`、`/mark`、`/tts`、`/route` | v1 弹幕/播放状态/合成接口，保持兼容 |

关键约束：**弹幕只有一条写入路径** `add_comment()`。控制台、CLI、Agent 都必须走它，
这样 seq 单调、页面不会把新台词当旧消息、桌面窗和 OBS 永远同一句。`add_comment()` 同时
写决策和日志环形缓冲，并用本地字符估算 Token；API Key 只在内存/`config.json`，不进响应。

**设置页和后台是同一份状态**：右键鱼身在右侧浮出「⚙️ 设置」小气泡，点气泡打开
`GET /settings` 得到
`ui/settings/index.html`，页面读写 `GET/POST /api/settings`（`src/dafeiyu/ui/settings.py`）。
保存直接合并进 `LiveConfig`（`config.json`），控制台下一次轮询和桌宠的 1.2s 配置轮询看到的
就是同一份；`POST /api/config`（控制台）与它落的是同一个文件。端口这类启动时绑定过的值
（`port` / `tts_url` / `host`）保存后回 `restart_required`，页面点亮「重启桌宠」：
`POST /api/app/restart` 调用桌宠进程注册的 hook，拉起新进程并关掉旧窗口（单实例锁接替）。
「退出桌宠」走 `POST /api/app/quit`，同样是 hook，不在桌宠进程里时明确报错。
语音/音量/置顶/穿透/录屏隐藏/静音由桌宠的 `_watch_config` 轮询 `config.json` 直接应用，
快捷键改的点击穿透和静音也写回同一份配置。空闲/思考气泡文案在 `caster_state.py` 固定为
「吃饭ing」「思考ing」，说话时显示台词。

### 录像回放

`src/dafeiyu/recording/replay.py` 管目录和元数据，OBS 只负责开/关：

```text
recordings/<game-id>/run-NNNN/
  run.mp4     一局一个（默认）
  meta.json   game/run/character/ascension/result/size/started_at/ended_at
```

- `start` 先 `SetRecordDirectory` 指到该局，再 `StartRecord`；老 OBS 不支持就退回默认目录。
- `stop` 读 `StopRecord.outputPath`，`os.replace` 成 `run.mp4`（同盘瞬间），再写 meta。
- 目标 `run.mp4` 已存在默认报错，不静默覆盖；多出的段进 `_extra/`。
- 每游戏策略在 `src/dafeiyu/recording/policy.py`：`games/<id>/game.json` 的 `recording`
  （`enabled` + `split=run|none`）是默认值，控制台保存到 `config.json` 的
  `recording.games.<id>` 覆盖；`recording_enabled` 是总开关。打开录像时先检查
  `game-skill/obs.json`，没有就按游戏名生成，再 `ensure_scene/ensure_game_capture/ensure_audio`。
- 自动玩（STS2 / 幸存者）按 `split=run` 每局开一段；`split=none` 用
  `recordings/<id>/continuous/` 连续录。刷B站默认关闭、不分集。
- 控制台「当前游戏 → OBS 录像」走 `src/dafeiyu/recording/manual.py`：先探 `GetRecordStatus`，没录就按
  当前游戏策略开、在录就停；有本进程会话就按 run/continuous 收尾，没有就走旧的
  `replay`/`stop_recording_now` 兜底。连不上 OBS 时尝试拉起，仍失败只返回提示，异常不上抛到窗口线程。
- `recordings/INDEX.md` 由 `scan()` 生成；视频被 `.gitignore` 排除，只提交说明和索引。

## 6.5 Agent 层（LangGraph）

Agent 层是**可选**的、接在一回合边界上的状态机，不替换内核：

```
observe ──► plan ──► validate ──► execute ──► reflect
              ▲          │            │
              └──────────┴────────────┘
```

| 节点 | 实现 | 边界 |
|---|---|---|
| `observe` | 游戏插件的 `tools().observe()` → `games/slay-the-spire-2/decision/ctx.py` | 只读局面 |
| `plan` | `ProvidedPlanner` / `LLMPlanner` | 输出 `{say, seq}`，不直接操作游戏 |
| `validate` | `games/slay-the-spire-2/decision/act.py --dry` | 费用/目标/斩杀/硬护栏 |
| `execute` | `games/slay-the-spire-2/decision/act.py`（默认 dry-run） | 唯一出牌路径 |
| `reflect` | `TraceRecorder` | 写轨迹，状态回给下一轮 |

- `src/dafeiyu/agent/` 只做编排、重试和轨迹；不复制 `ctx.py` / `act.py` 的逻辑。
- 外部 DSH Agent 用 `--plan` 注入计划；内置 LLM 用 OpenAI 兼容接口，Key 仍只在本机。
- 轨迹写入 `logs/agent_trace.jsonl`（只存摘要），`/api/studio` 的 `agent` 字段供
  控制台「Agent 流程」展示；不依赖 LangSmith。
- `langgraph` 是可选依赖（`pip install -e .[agent]`）；不装时桌宠、语音、游戏启动照常。

## 6.6 性格层与 KOOK 记忆

性格按「个人 → 直播 → 游戏」三段拼接，由 `src/dafeiyu/persona/layers.py` 统一装配：

| 层 | 来源 | 何时生效 |
|---|---|---|
| 个人（常驻） | `persona/<id>/card.json` + `style.md` | 永远；KOOK 聊天用 `kook_persona`，桌宠/直播用 `persona` |
| 直播 | `persona/live/card.json` + `style.md` | `live=True` 且 `persona_live` 打开；KOOK 不加载 |
| 游戏 | `games/<id>/persona/layer.md` | 传入 `game_id` 时叠加（游戏 runner / 自动玩） |

`PersonaStack.load(...).prompt()` 只负责拼文本，不改人设卡/lint；设置页 `persona_layers`
返回三层的来源、字数和最终提示词，方便定位「这句话从哪层来的」。

KOOK 聊天的长期记忆在 `src/dafeiyu/live/chat_memory.py`：

- `turns` 保存全部原文；`facts` 保存抽取出的长期事实；`seen` 做事件去重。
- `summaries` + `meta.summary_upto` 做压缩：未压缩原文超过 `kook_summary_trigger`
  时，保留最后 8 条，把更老的一段交给 LLM 压成摘要；原文不删。
- `memory.context(turns)` = 最近 N 条原文 + 最近几段摘要，`facts` 由提示词另行注入；
  这样上下文有上限，但长期信息不丢。
- 机器人收消息走 `src/dafeiyu/live/kook/service.py` 的 WebSocket 循环，发消息走
  `KookClient`；频道用 `/message/create`，私聊用 `/direct-message/create`。
- 图片：`type=2` 直接取 `content`/`attachments.url`；网页端粘贴图片会变成 `type=10`
  卡片，服务从卡片 `image` 模块取 `src`，下载后走 `chat.image_message()` 视觉模型。
- 语音：TTS 出 WAV → `lameenc` 转 MP3 → `/asset/create` 上传 → `type=10`
  卡片 `audio` 模块 + 「重听」link 按钮；KOOK 机器人直发 `type=8` 会被拒（40000）。
  手机端内嵌播放器重播 WAV 不稳定，MP3/按钮是兼容兜底；表情包同样先上传 PNG，再发 `type=2`。
- 回复协议是 `messages` 数组（1–3 条），服务逐条发；旧 `reply` 单字符串仍兼容。
  安静够久后会按概率主动发一条，只发到最近聊过的 `target_id`；发过一次后要等
  对方回话才重新计时，不会连发。
- Token：聊天进程每次回复/记忆抽取/压缩/主动消息写 `logs/kook/usage.jsonl`（带 `msg_id`），
  控制台把该文件与 `logs/autoplay.jsonl` 合并成一张账单，自动玩和聊天各占一个来源。

## 7. 扩展点

| 想做什么 | 改哪里 |
|---|---|
| 换人设/语气/禁语/招牌 | `persona/<id>/card.json` |
| 加/改直播性格 | `persona/live/style.md`（设置页可关 `persona_live`） |
| 加/改某游戏性格 | `games/<id>/persona/layer.md` |
| 换聊天表情包 | `persona/stickers/stickers.json` + `tools/import_stickers.py` |
| 改 KOOK 上下文/压缩阈值 | `config.json` 的 `kook_context_turns` / `kook_summary_trigger` |
| 换开场/收尾/口头禅 | 同上 `openers` / `closers` / `quips` |
| 换动物语音色 | `voice/animal/scale.json` |
| 换曼波参考音 | `voice/mambo/ref.json` 或 `DAFEIYU_TTS_REF` |
| 换默认语音/音量/间奏 | `config.json` 或 `DAFEIYU_*` 环境变量 |
| 换默认游戏/别名 | `games/registry.json` + `game.json` 的 `aliases` |
| 加游戏 | `games/<id>/` + `plugin.py:Plugin` + `games/registry.json`（最小完整示例：`games/toy-duel/`） |
| 让游戏进「LLM 直播模式」候选 | 该游戏 `game.json` 加 `live.enabled/label/role/weight` |
| 加 UI 面板 | `ui/studio/index.html` + `/api/studio` 字段 |
| 换 OBS 场景/采集源名 | `games/<id>/game-skill/obs.json` |
| 换桌宠精灵 | `src/dafeiyu/ui/assets/pet/`（正面/侧面/背面） |
| 换 OBS 气泡样式 | `ui/overlay/index.html` 的 `#main` |
| 改录像目录规则 | `src/dafeiyu/recording/replay.py` |

## 8. 测试策略

- `tests/`：纯单元，不依赖游戏/OBS/网络（离线引擎用 `127.0.0.1:1` 模拟）。
  - 总线：通配订阅、异常隔离、history
  - 性格：拼句、全部 lint 规则、节奏/招牌冷却
  - 语音：WAV 合法性、增益、离线失败、回退
  - 游戏：注册表、manifest 路径、离线适配器、语料库行数
  - 会话：推送 payload、事件顺序、状态合并
- `tools/test_*.py`：v1 的决策/渲染/overlay 回归，继续保留；`tools/` 下的同名 shim
  保证 `import ctx` / `import act` / `import sts2` 不变，`tests/test_engine_migration.py`
  钉住它们确实加载 `games/slay-the-spire-2/decision/` 的实现。
- `python main.py doctor`：环境层自检（人设/UI/两种语音/overlay/语料/游戏）。
- `tools/release_check.py`：发布门禁（版本串 + `tests/` + 关键 tools + 编译）；
  `--ci` 给 GitHub Actions，`--full` 加 `tools/test_all.py`。实机验收步骤在
  `docs/RELEASE.md`，不在 CI 里伪造游戏/OBS/直播间。

## 9. 兼容与迁移

- 兼容 shim 全部在 `tools/`（`sts2.py` / `ctx.py` / `act.py` / `advisor.py` / `sim.py` /
  `streamer.py` / `obsctl.py` / `caster_state.py` / `instance_lock.py` 等），用
  `sys.modules[__name__] = 真模块` 或 `_game_impl.load()` 加载同一份实现；根目录不再有代码文件。
  2.2 不删 shim：`games/slay-the-spire-2/decision/knowledge`、docs 和旧 runbook 仍引用旧入口；映射和退役条件见
  `tools/README.md`，不要在 `src/dafeiyu/` 里 import shim。
- `db/`、`tools/data/` 是兼容链接（`python main.py setup` 创建），真实内容在
  `games/slay-the-spire-2/corpus/`。这样 v1 里几十个 `"db/sts2.db"` 相对路径不用逐个改。
- 迁移只搬文件、不改业务逻辑；决策引擎（P6）迁移时只把路径基准从仓库根改为
  游戏包 `corpus/`，算法、阈值和动作协议未改。桌面窗 `push` / `start_talking` 对
  `presence` 缺省值的容错仍是唯一的行为修复（单测用 `__new__` 构造窗口时没有该属性）。

## 10. 已知取舍

- **不做截图识别**：状态来自 mod 的结构化 JSON，稳定、便宜、可回放。
- **不在内核里跑 LLM**：说什么由 Agent/人设决定；内核只做规则、节奏和分发。
- **Agent 层只接一回合边界**：图负责观察/验算/重试/轨迹，不接管人设、TTS 和 OBS；
  这样换模型或换编排不用重写游戏兼容层。
- **不并发合成曼波**：引擎是单实例，并发只会互相拖死；串行 + 缓存更稳。
- **控制台不另起服务**：同一端口避免 OBS 源和控制台状态分叉。
- **Windows 优先**：透明分层窗和 OBS 音频路由是 Windows 实现；其他平台仍有 overlay/控制台。
