# AGENTS.md

This repository is a Python live-streaming pet that is **driven by a DeepSeek Harness
agent**. The kernel does not run its own hidden LLM loop; the agent decides what to
say and which game action to take. The optional `agent run` LLM planner is explicit
and dry-run by default; keep that boundary unless the task asks for a new planner.

## Start here

- Read `README.md` for the product surface, `ARCHITECTURE.md` for the layers.
- Decision knowledge lives in `games/slay-the-spire-2/decision/knowledge/`:
  `ironclad/OS.md` (决策手册), `ironclad/references/` (卡表/敌人/药水/教训),
  `defect/OS.md`. Speaking lives in `games/slay-the-spire-2/persona/`
  (`speech.md`, `lines.md`, `quips.md`). 没有 `.dsh` 技能目录：模型读的是这些项目文件，
  运行时提示词是 `decision/prompts.py` 里的精简版。
- LangGraph turn orchestration: `docs/AGENT_LANGGRAPH.md`.
- The game packets are under `games/<id>/`; the registry is
  `games/registry.json`. `games/toy-duel/` is the second, fully offline plugin
  used to prove the core is not STS2-specific; keep it working when changing
  the plugin/host/tools contracts. `games/vampire-survivors/` is the third
  packet (BepInEx 6 IL2CPP bridge + local HTTP `:8091`; no window focus and no
  keyboard injection) and `games/bilibili-hot-commentary/` is the non-game watch
  mode.

## Commands

```powershell
python main.py                  # desktop pet
python main.py studio           # overlay + management console (:8730)
python main.py game state       # current game state
python main.py game launch      # only launch current game (Steam / exe)
python main.py play             # launch + autonomous run; Ctrl+C or --stop
python main.py play --mode llm  # LLM decides every player decision (needs API key)
python main.py play --pace voice # default: wait for speech; normal = old fast
python main.py play --game toy-duel --mode local  # offline second-game acceptance
python main.py play --stop      # cross-process stop
python main.py live             # LLM live-mode director: picks a live module and switches
python main.py live --status    # director status + candidate modules
python main.py live --stop      # stop director (and the running module)
python main.py kook             # KOOK bot WebSocket chat (optional)
python main.py agent run --demo # offline LangGraph turn
python main.py agent run        # real ctx + LLM, dry-run
python main.py agent run --execute
python main.py agent trace
python main.py agent graph
python tools/release_check.py --metadata-only  # version sync only
python tools/release_check.py                  # release gate (tests/docs/compile)
python -m pytest -q tests
```

Version bumps must stay in sync across `pyproject.toml`, `src/dafeiyu/__init__.py`,
`README.md`, `games/slay-the-spire-2/game.json`, and `CHANGELOG.md`. `v22` in the
decision manual (`decision/knowledge/ironclad/OS.md`) is a separate manual version,
not the product version.

## Invariants

- **Never print, log, commit, or copy the API key.** `config.json` is git-ignored;
  the HTTP API and console only expose `llm_api_key_set`.
- Only `main.py` lives at the repo root; core Python code belongs under
  `src/dafeiyu/`, game-specific code belongs under `games/<id>/`. Core must not
  hardcode a game id, display name, or alias; use `games/catalog.py` and
  `game.json` metadata.
- The desktop right-click opens a custom **gear button tight against the pet's right
  side** (light-blue outer ring, blue inner ring, one larger centered gear icon,
  all concentric; scales with the pet, pop/fade animation), clamped to the pet's
  monitor, not a Win32 popup menu.
  All toggles and service ports
  live in `ui/settings/index.html`, served by `src/dafeiyu/ui/settings.py`; that page
  and the console read/write the same `LiveConfig` (`config.json`) — do not add a
  second settings store or another desktop entry.
- `pet_status_bar` now controls the **Windows tray icon** (left click show/hide,
  right click settings); `pet_autostart` writes HKCU Run. Both are Windows-only
  side effects in `ui/desktop.py` / `core/autostart.py`; the settings page only
  edits `LiveConfig`.
- Persona is three layers: personal (`persona/<id>/`) → live (`persona/live/`) →
  game (`games/<id>/persona/layer.md`). Assemble them only through
  `src/dafeiyu/persona/layers.py::PersonaStack`; do not concatenate persona files
  in a runner. KOOK chat uses `config.kook_persona` with `live=False`.
- KOOK memory keeps all turns in SQLite; context is recent turns + summaries.
  When pending turns exceed `kook_summary_trigger`, compress the oldest batch and
  advance `meta.summary_upto`; keep the raw turns. Do not silently drop history.
- KOOK monitoring is a separate process; when `kook_enabled` is true the pet
  starts/stops it via `live/kook/supervisor.py` and `logs/kook/service.lock`
  prevents a manual launch from creating a second connection. Keep that lock
  protocol when changing startup/restart behavior.
- Combat actions use the `games/slay-the-spire-2/decision/act.py` batch protocol
  through `sts2.cmd_batch`; screen transitions use
  `games/slay-the-spire-2/decision/sts2.py` (compat client). Game launch goes
  through `src/dafeiyu/games/launch.py`. The autoplay facade is
  `src/dafeiyu/agent/play.py`, the generic status/stop/recording/timing host is
  `src/dafeiyu/agent/host.py`; the actual loop and tools live in the game package
  (STS2: `games/slay-the-spire-2/autoplay/runner.py`,
  `games/slay-the-spire-2/decision/`) and control the game only through the same
  tool boundary; unknown screens must stop rather than guess. `tools/ctx.py`,
  `tools/act.py`, `tools/advisor.py`, `tools/sim.py` etc. are compatibility shims.
- Keep the UI palette (`--bg:#0b0d12`, `--gold:#f4c968`, dark panels).
- **决策归属**：`--mode llm` 下玩家决策全部由 LLM 做——战斗出牌走
  `decision/act.py` 的 `seq` 协议，界面点击走 `decision/actions.py` 的白名单菜单
  （`autoplay/runner.py::_screen_llm`）。本地启发式只作降级（模型超时/输出不可执行），
  并必须落 `llm_fallback` 日志；主菜单/角色选择/确认框这类过场仍走本地管道。新增屏幕决策时
  先加进 `actions.available()`，不要把动作硬编码回 runner；`save_and_quit` / `use_potion`
  这类越界动作永远不进菜单。
- **直播间**：监听只在桌宠进程（`ui/desktop.py::GiftBridge`），事件写
  `logs/bilibili_events.jsonl`（最近 120 条，`live/bilibili.py::RoomLog`）；控制台
  `ui/studio` 只读这份文件，发弹幕走 `POST /api/bili/send`（需要 `bili_jct`）。
  不要在控制台进程再开一个监听，否则同一房间会写重复事件。
- **Token 账单**：自动玩每次 LLM 调用必须经 `AutoplayHost.add_usage(..., note=...)`，`note`
  写清「战斗 f7 t3」或「地图 choose_map_node 2」；控制台 Agent 页按它出每步 token。
  KOOK 聊天是独立进程，每次回复/记忆抽取/压缩写 `logs/kook/usage.jsonl`
  （`live/kook/usage.py`，带 `msg_id`），控制台与自动玩账单合并展示。
  KOOK 回复可拆成 1–3 条独立消息；主动消息只发最近聊过的 `target_id`，
  间隔 `kook_proactive_minutes` 后按概率触发，不能连发。
- **LLM 直播模式**：导演候选只来自各游戏包 `game.json` 的 `live` 块
  （`live.enabled/label/role/weight`），`src/dafeiyu/agent/director.py` 不写死任何
  游戏 id/显示名；模型只决定「播哪个/多久/过渡台词」，怎么玩仍归各游戏 runner。
  导演切换前先 `stop_autoplay()`，停手/手动「让她玩」都必须先停导演
  （`director.stop()`），否则下一段会把刚开的模块切走；状态文件是
  `logs/live_director.json`，停止标志 `logs/live_director.stop`。
- **开局设置**：角色/难度这类开局选项由各游戏包 `game.json` 的 `start.options`
  声明（`choice` / `int` / `bool`），控制台按声明渲染，值写 `config.json` 的
  `start_options.<game-id>`；清洗逻辑只在 `src/dafeiyu/games/start_options.py`，
  那里不许写具体游戏/角色 id。游戏包自己的模块（如
  `games/slay-the-spire-2/start_options.py`）在开新局前读取，不塞进 `LiveConfig`
  的具名字段。改完值必须下一局生效，不能改写已经开着的局。
- Agent traces are local JSONL (`logs/agent_trace.jsonl`); do not send them to a
  cloud service or include full game state / secrets.

## When changing agent behavior

1. Keep `games/slay-the-spire-2/decision/ctx.py` and `decision/act.py` as the
   tool boundary; `tools/ctx.py` / `tools/act.py` only forward for old commands.
2. Add observation fields rather than a second game-state reader.
3. Record node-level timing/status/tokens in `TraceRecorder`.
4. Update `docs/AGENT_LANGGRAPH.md` and the console Agent view when the graph changes.
5. 改决策规则时同步两处：`decision/prompts.py`（常驻精简版）和
   `decision/knowledge/ironclad/OS.md`（完整手册）；`tools/check_skill.py` 查漂移。
6. Run the agent tests and the full suite before committing.
