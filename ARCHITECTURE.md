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

仓库只有一个根代码入口 `main.py`：无参数进 `dafeiyu.ui.desktop`（桌宠 + 右键菜单），
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

## 6. UI 服务

只起一个 HTTP 服务（默认 `127.0.0.1:8730`），因为 OBS 浏览器源和控制台必须看同一份状态：

| 路径 | 作用 |
|---|---|
| `/` | 桌宠气泡页（OBS 源）：大肥鱼精灵 + 漫画气泡 |
| `/studio` | 控制台单页 |
| `/api/studio` | 四层状态聚合（人设/语音/游戏/overlay/最近台词） |
| `/api/say` | 拼句 + lint + `add_comment`；`dry_run` 只算 lint |
| `/api/voice` | 同步合成返回 WAV（试听） |
| `/events` | SSE，新台词即时到页 |
| `/state`、`/health`、`/mark`、`/tts`、`/route` | v1 弹幕/播放状态/合成接口，保持兼容 |

关键约束：**弹幕只有一条写入路径** `add_comment()`。控制台、CLI、Agent 都必须走它，
这样 seq 单调、页面不会把新台词当旧消息、桌面窗和 OBS 永远同一句。

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
- `recordings/INDEX.md` 由 `scan()` 生成；视频被 `.gitignore` 排除，只提交说明和索引。

## 7. 扩展点

| 想做什么 | 改哪里 |
|---|---|
| 换人设/语气/禁语/招牌 | `persona/<id>/card.json` |
| 换开场/收尾/口头禅 | 同上 `openers` / `closers` / `quips` |
| 换动物语音色 | `voice/animal/scale.json` |
| 换曼波参考音 | `voice/mambo/ref.json` 或 `STS2_TTS_REF` |
| 换默认语音/音量/间奏 | `config.json` 或 `DAFEIYU_*` 环境变量 |
| 加游戏 | `games/<id>/` + `game-skill/adapter.py` + `games/registry.json` |
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
  保证 `import streamer` / `import sts2` 不变。
- `python main.py doctor`：环境层自检（人设/UI/两种语音/overlay/语料/游戏）。

## 9. 兼容与迁移

- 兼容 shim 全部在 `tools/`（`sts2.py` / `streamer.py` / `obsctl.py` / `caster_state.py` /
  `instance_lock.py`），用 `sys.modules[__name__] = 真模块`；根目录不再有代码文件。
- `db/`、`tools/data/` 是兼容链接（`python main.py setup` 创建），真实内容在
  `games/slay-the-spire-2/corpus/`。这样 v1 里几十个 `"db/sts2.db"` 相对路径不用逐个改。
- 迁移只搬文件、不改业务逻辑；唯一的行为修复是桌面窗 `push` / `start_talking` 对
  `presence` 缺省值的容错（单测用 `__new__` 构造窗口时没有该属性）。

## 10. 已知取舍

- **不做截图识别**：状态来自 mod 的结构化 JSON，稳定、便宜、可回放。
- **不在内核里跑 LLM**：说什么由 Agent/人设决定；内核只做规则、节奏和分发。
- **不并发合成曼波**：引擎是单实例，并发只会互相拖死；串行 + 缓存更稳。
- **控制台不另起服务**：同一端口避免 OBS 源和控制台状态分叉。
- **Windows 优先**：透明分层窗和 OBS 音频路由是 Windows 实现；其他平台仍有 overlay/控制台。
