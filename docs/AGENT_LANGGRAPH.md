# Agent 流程（LangGraph）

> 结论：**只在一回合的边界上接 LangGraph，不重写游戏工具、人设和 TTS。**
> 这是当前代价最小、又能把决策过程变透明的做法。

## 1. 为什么不是重写整个项目

现有项目已经有三块很值钱的东西：

| 已有能力 | 位置 | 重写代价 |
|---|---|---|
| 局面压缩 / 规则 / 教训 | `games/slay-the-spire-2/decision/ctx.py` | 高：626 卡、127 敌、98 条规则的兼容层 |
| 出牌验算 / 硬护栏 / 回执 | `games/slay-the-spire-2/decision/act.py` | 高：一回合一条 batch 的协议已经稳定 |
| 人设 / 语音 / overlay | `persona/`、`voice/`、`ui/` | 高：和直播链路强绑定 |

所以 Agent 层不碰这三块，只把它们包成节点：

```
observe ──► plan ──► validate ──► execute ──► reflect
              ▲          │            │
              └──────────┴────────────┘   验算/执行失败 → 回 plan 重试
```

- `observe`：`decision/ctx.py` 读一份紧凑局面；
- `plan`：外部计划（DSH Agent / `--plan`）或 `LLMPlanner`；
- `validate`：`decision/act.py --dry`，算术/费用/目标/斩杀线都在这里拦；
- `execute`：默认 dry-run；只有 `--execute` 才真的走 `decision/act.py`；
- `reflect`：写轨迹、给下一轮留下失败原因。

工具层统一返回 `dafeiyu.games.contracts.ActionResult`（`ok / action / code /
message / retryable / data`）。图入口用 `as_action_result()` 归一化，所以迁移期
仍能接旧 `{ok, err}` dict；节点内部继续用摊平后的 dict，轨迹和 `reflect` 的
判断不变。旧调试脚本需要 dict 时调用 `ActionResult.to_legacy()`。

`tools/ctx.py`、`tools/act.py` 等旧路径已变成兼容 shim，实际加载
`games/slay-the-spire-2/decision/` 下的同一份实现；旧命令和旧 skill 不需要改。

这比「LangChain Agent 自己决定调什么工具」更可控：工具集合固定、状态可回放、
每一步都能在控制台看到耗时和重试次数。

**为什么没有直接用 LangChain AgentExecutor**：它的价值在「模型自由选工具」，
但这个项目的工具边界已经很清楚（ctx / act / say），自由选工具只会增加不可控
和 token。LangGraph 提供图、条件边和状态；LLM 用 OpenAI 兼容 HTTP 直接调，
少一层 `langchain-openai` 依赖，也方便以后换成任何兼容端点。

## 2. 两种规划器

| 模式 | 命令 | 适用 |
|---|---|---|
| 外部 plan | `python main.py agent run --plan tmp_plan.json` | DSH Agent 继续用 `.dsh/skills` 决策；图只负责验算/执行/轨迹 |
| 内置 LLM | `python main.py agent run` | 想对比「模型自己打」和「技能 + 工具」的差距 |
| 离线演示 | `python main.py agent run --demo` | 不碰游戏、不调 API，验证图本身 |

内置 LLM 只调用 OpenAI 兼容的 `/chat/completions`，配置来自本机
`config.json` 的 `llm_base_url` / `llm_model` / `llm_api_key`。没有 Key 时不会
偷偷回退到别的服务，会直接提示配置。

## 3. 透明性：本机轨迹，不上云

每个节点写一行 JSONL 到 `logs/agent_trace.jsonl`：

```json
{"run_id":"run-...","node":"validate","status":"ok","duration_ms":12,
 "attempt":1,"summary":"通过"}
```

控制台左侧「Agent 流程」直接读这份文件，显示：

- 最近一次运行的节点列表、每步耗时、第几次尝试；
- 最近 12 次运行的状态 / 总耗时 / token；
- 轨迹文件路径和条数。

只看三件事就能优化：

1. **哪个节点最慢**（通常是 `plan` 的 LLM 延迟）；
2. **validate 重试几次**（规则没写清，或模型没读局面）；
3. **每次 token**（prompt 是否塞了无关局面）。

轨迹只存摘要，不存完整 `context` 和 plan，也不写 API Key；需要完整局面时用
`tools/ctx.py --out` 单独落盘。

## 4. 怎么观察和优化

建议一次只改一个变量，跑同一局面/同一 goal 对比：

| 改动 | 看什么 | 期望 |
|---|---|---|
| 改系统 prompt / 加规则 | plan 耗时、validate 重试率 | 重试下降，胜率不降 |
| 换模型 | plan token、耗时、执行成功率 | 单位成本下的成功率 |
| 改 `ctx.py` 字段 | token、validate 错误类型 | 只留决策需要的字段 |
| 改 `act.py` 护栏 | execute 失败率、STOP 原因 | 失败前移到 validate |

图中每一步都有 `attempt`，重试链能直接看出来；这比只看最终台词更容易定位
「是模型不会算，还是工具没给对信息」。

## 5. 命令

```powershell
# 离线跑图（不碰游戏）
python main.py agent run --demo

# 真实局面 + 内置 LLM，默认只验算不出牌
python main.py agent run

# 真的出牌（危险；先确认游戏在前台、OBS 状态）
python main.py agent run --execute

# 外部 Agent 给计划（推荐：保留现有技能决策）
python main.py agent run --plan logs/plan.json

# 看轨迹 / 图
python main.py agent trace --limit 5
python main.py agent graph
```

外部计划格式与 `tools/act.py` 一致：

```json
{"say": ["它 16，痛击加一刀刚好带走。"],
 "seq": [{"c": "BASH", "t": 0}, {"c": "STRIKE_IRONCLAD", "t": 0}, {"end": 1}]}
```

## 6. 整局自动玩法与回合图的分工

`python main.py play` / 右键「让她玩 ▸ 本地算账」先走 `src/dafeiyu/agent/play.py`
这个 facade，再转给游戏包的 `autoplay/runner.py`（当前是
`games/slay-the-spire-2/`）。它是一个**整局循环**，不是每个动作都过 LangGraph。
原因是整局包含大量不需要 LLM 的界面切换（地图、奖励、营火、商店），每一步都进图
会慢且费 token。状态/停止/录像/说话节奏由核心 `src/dafeiyu/agent/host.py`
提供，游戏包只保留界面分派和决策。

右键「让她玩 ▸ LLM 规划」只把**战斗回合**交给图：游戏包里的 `_combat_llm` 调
`run_turn(..., tools=Sts2Tools, planner=LLMPlanner, dry_run=False)`，所以战斗决策、验算、
执行和轨迹都能在「Agent 流程」看到；地图/奖励等仍由本地循环处理。默认用
`deepseek-chat`（非推理模型），避免长局面把 reasoning 预算吃完、content 为空。
游戏 System Prompt 不再放在核心：`LLMPlanner` 通过构造参数接收，来源是游戏插件的
`prompt("decision")`（STS2 在 `games/slay-the-spire-2/decision/prompts.py`）。

两者共用同一套工具边界：

| 层 | 自动玩法 | LangGraph 单回合 |
|---|---|---|
| 读局面 | `decision/ctx.py` 同源 | `observe` 节点 |
| 算牌/出牌 | `advisor.py` 或 `run_turn` | `validate` / `execute` |
| 轨迹 | `logs/autoplay.jsonl` | `logs/agent_trace.jsonl` |
| 控制 | `logs/autoplay.stop` | `--execute` 显式开关 |

如果要把整局自动玩法的战斗段再升级，最小改法是改 `_combat_llm` 里的 goal / 模型，
或把 `LLMPlanner` 换成外部计划；保留 `run_turn` 的观察/验算/执行和 trace。先不要把整局
所有界面都塞进图里。

## 7. 和 DeepSeek Harness 的分工

- **DSH Harness**：外层 Agent 运行时。负责加载 `.dsh/skills`、理解人设、决定
  「这回合想做什么」，也可以直接调用现有 CLI。
- **LangGraph**：内层一回合的状态机。负责把「观察 → 规划 → 验算 → 执行」拆开、
  重试、记录，让过程可观察。
- 两者不是二选一：`--plan` 模式就是 DSH 出计划、LangGraph 跑验算/执行/轨迹。

## 8. 可选依赖

```powershell
pip install -e .[agent]       # langgraph>=1.0
```

不装也能用桌宠、语音、控制台和游戏启动；只是 `python main.py agent ...` 会提示
安装。测试里对 LangGraph 使用 `importorskip`，不会让基础安装失败。
