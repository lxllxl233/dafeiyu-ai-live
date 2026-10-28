# 大肥鱼直播姬 · 本机操作卡

这个仓库是 **DeepSeek Harness 驱动的 Python 应用**：外层 Agent 负责决策/说话，
内核只做规则、节奏、TTS、overlay 和游戏适配。不要在内核里再写一个隐藏 LLM 循环。

## 起服务

```powershell
python main.py                 # 桌宠
python main.py studio          # overlay + 控制台 http://127.0.0.1:8730/studio
python main.py game launch     # 只按 config.game 启动（STS2 = Steam 2868840）
python main.py play            # 启动 + 让她自动跑；Ctrl+C 停
python main.py play --mode llm # 全部决策交给 LLM（需 API Key；本地只兜底）
python main.py play --stop     # 跨进程停手
python main.py doctor          # 环境自检
```

## Agent 流程

```powershell
python main.py agent run --demo    # 离线图，不碰游戏
python main.py agent run           # 真实 ctx + LLM，只 dry-run
python main.py agent run --plan p.json  # 外部计划（保留技能决策）
python main.py agent trace --limit 5
```

图：`observe → plan → validate → execute → reflect`；验算失败回 `plan`。
轨迹在 `logs/agent_trace.jsonl`，控制台「Agent 流程」可看；不存 API Key。

### Token 账单

控制台「Agent 流程 → Token 花费」是**真实 API 账单**，不是台词估算：

- 合计 / 输入 / 输出 / 调用次数；
- 最近 20 步：时间、说明（`战斗 f7 t3`、`地图 choose_map_node 2`…）、每步入/出/合计、累计；
- 按类型汇总（战斗 / 地图 / 事件 / 营火 / 商店 / 宝箱 / 奖励 / 选牌 / 未标注）。

数据源：`logs/autoplay.jsonl` 的 `llm_usage` 行（`AutoplayHost.add_usage` 写），
控制台跨进程读同一份文件；3 秒轮询用 `(mtime, size)` 缓存，不会反复解析。

### 为什么有时候「思考」很久

先排除上下文：一次决策的输入只有 1.3–2.9k token（system 约 1k 字 + 当前局面），
离模型上限很远。慢在三处：

1. **思维链**：`deepseek-flash` 这类推理模型会把 `max_tokens` 预算先花在
   reasoning 上。实测同一局面：开 thinking 24s、reasoning 4096 token、
   `content` 为空（直接判失败）；关掉 thinking 1.7s 就回 JSON。
   配置 `autoplay_llm_thinking`：`off`（默认，都关）/ `auto`（界面关、战斗跟模型）/
   `on`（都开）。请求里对应 `thinking: {"type": "disabled"}`。
2. **超时与重试**：战斗 25s、界面 40s；超时后按设计落本地兜底，并记
   `llm_failed`/`llm_fallback`。日志里 `llm_usage` 的 `ms` 字段是单次真实耗时。
3. **语音节奏**：`voice` 每步要等台词播完（`0.22s/字`，上限 10s），这不算思考，
   但体感是「一步好久」。右键「让她玩 → 普通节奏」固定 0.7s。

### 连续玩会不会带上一局上下文

**不会。** `LLMPlanner.plan()` 每次只发两条消息：`system` + 一条 `user`
（`{"goal","feedback","context"}`）。`context` 由当前局面现场构建；`feedback`
只在同一回合的验算失败重试里出现。换局以后模型拿不到上一局的楼层、牌组、
台词、run_id 或聊天历史。

跨局唯一共享的输入是：5 条硬闸（`decision/hard_rules.py`）、至多 3 张经验卡、
以及提示词/人设。局面包里连 `run_id` 字段都没有。回归测试：
`tests/test_context_isolation.py`。

## 让她玩

右键「让她玩」二级菜单：本地算账 / LLM 规划 / 慢速播报 / 普通节奏；「让她停手」单独一项。
LLM 模式把**所有玩家决策**交给模型：战斗走 LangGraph `run_turn` + `decision/act.py`，
地图/事件/营火/商店/宝箱/奖励/选牌走 `decision/actions.py` 的白名单菜单
（`runner.py::_screen_llm`），默认模型 `deepseek-chat`；主菜单/角色选择/确认框仍是本地管道。
模型超时或输出不可执行才本地兜底并记 `llm_fallback`，执行失败才停手；
GAME_OVER 会回主菜单再开一局。默认慢速播报按台词长度等语音，至少 2 秒一步；普通节奏是旧版 0.7 秒。整局循环在
`src/dafeiyu/agent/play.py`，状态 `logs/autoplay.json`，步骤 `logs/autoplay.jsonl`，
停手文件 `logs/autoplay.stop`。只处理已知界面；未知界面会停下，不猜按钮；
同一画面同一失败连续 3 次也会收手。

## 直播间互动

- 桌宠进程监听 B 站直播间（`config.json` 的 `bilibili_enabled` / `bilibili_room_id` /
  `bilibili_cookie`），把进入 / 关注 / 分享 / 点赞 / 弹幕 / 礼物 / 醒目留言 / 上舰
  写进 `logs/bilibili_events.jsonl`，**只保留最近 120 条**（文件超 200KB 自动压缩）。
- 控制台「直播间」页读同一份文件：可筛选类型、看最近一条、看各类型计数；
  连接状态来自 `logs/bilibili.json`。
- 页面右侧可以直接发弹幕（`POST /api/bili/send`），需要 Cookie 里有
  `SESSDATA` 和 `bili_jct`（csrf）；发送成功后本机先显示一条「本机」记录。
- 只开控制台、没开桌宠时，面板只有历史，没有新事件；发弹幕仍然可用。

## 边界

- 游戏内决策：`games/slay-the-spire-2/decision/knowledge/ironclad/OS.md`；说话：
  `games/slay-the-spire-2/persona/speech.md`。
- 出牌走 `tools/act.py` 同款 batch 协议（自动玩法用 `sts2.cmd_batch`）；读局面只走 `tools/ctx.py`。
- API Key 只在本机 `config.json`，绝不回显/提交/写日志。
- B 站 Cookie 同理：只写本机，接口只回 `cookie_set`。
- 右键菜单是 Win32 弹窗，新增项要保留已有 ID。
