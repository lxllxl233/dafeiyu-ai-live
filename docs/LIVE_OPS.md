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
- 来源拆分（自动玩 / KOOK 聊天）；
- 最近 20 步：时间、说明（`战斗 f7 t3`、`地图 choose_map_node 2`、`KOOK 聊天`…）、每步入/出/合计、累计；
- 按类型汇总（战斗 / 地图 / 事件 / 营火 / 商店 / 宝箱 / 奖励 / 选牌 / KOOK 聊天 / 未标注）。

数据源：`logs/autoplay.jsonl` 的 `llm_usage` 行（`AutoplayHost.add_usage` 写）+
`logs/kook/usage.jsonl`（KOOK 聊天进程写，带 `msg_id`）；控制台跨进程读同一份文件，
3 秒轮询用 `(mtime, size)` 缓存，不会反复解析。

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
   但体感是「一步好久」。设置页「模型 → 播报节奏」选「普通节奏」固定 0.7s。

### 连续玩会不会带上一局上下文

**不会。** `LLMPlanner.plan()` 每次只发两条消息：`system` + 一条 `user`
（`{"goal","feedback","context"}`）。`context` 由当前局面现场构建；`feedback`
只在同一回合的验算失败重试里出现。换局以后模型拿不到上一局的楼层、牌组、
台词、run_id 或聊天历史。

跨局唯一共享的输入是：5 条硬闸（`decision/hard_rules.py`）、至多 3 张经验卡、
以及提示词/人设。局面包里连 `run_id` 字段都没有。回归测试：
`tests/test_context_isolation.py`。

## 让她玩

设置页「运行」和控制台「Agent 流程」：本地算账 / LLM 规划；播报节奏在
「模型 → 播报节奏」（慢速 / 普通），「让她停手」在「运行」页。
LLM 模式把**所有玩家决策**交给模型：战斗走 LangGraph `run_turn` + `decision/act.py`，
地图/事件/营火/商店/宝箱/奖励/选牌走 `decision/actions.py` 的白名单菜单
（`runner.py::_screen_llm`），默认模型 `deepseek-chat`；主菜单/角色选择/确认框仍是本地管道。
模型超时、限流、连不上或输出不可执行都会先本地兜底并记 `llm_fallback`，不会直接停手；
GAME_OVER 会回主菜单再开一局。默认慢速播报按台词长度等语音，至少 2 秒一步；普通节奏是旧版 0.7 秒。整局循环在
`src/dafeiyu/agent/play.py`，状态 `logs/autoplay.json`，步骤 `logs/autoplay.jsonl`，
停手文件 `logs/autoplay.stop`。只处理已知界面；未知界面会停下，不猜按钮；
同一画面同一失败连续 3 次也会收手。

## 开局设置（角色 / 进阶）

控制台「游戏 → 开局设置」按当前游戏 `game.json` 的 `start` 块渲染选项（杀戮尖塔 2 是
**角色 + 进阶难度**）。保存写 `config.json` 的 `start_options.<game-id>`，
`runner.py::_ensure_run` 在**每次回主菜单/选角色**时按 `config.json` 的 mtime 重读一次，
所以改完不用重启桌宠，**下一局**生效；已经开着的局不会被改写。

- 角色按 `character_select.characters[].character_id` 选；目标角色没解锁/这一版没有时
  不硬点，记 `start_character_unavailable` 并沿用当前角色照常开局。
- 进阶两个方向都会调：游戏记得上次的 A10 时，选 A0/A3 会用 `decrease_ascension` 降下来；
  超过游戏 `max_ascension` 就停在上限。
- 手动开局脚本同样读这份设置：`python tools/run_start.py`（可用 `--character` /
  `--ascension` 临时覆盖）。
- 决策手册主要按铁甲战士 A10 整理；换角色/更高进阶时选牌会更保守，提示词里已注明
  「角色/难度以 ctx 为准」。

## LLM 直播模式（导演）

设置页「运行 → 开启 LLM 直播模式」：导演扫 `games/<id>/game.json`
的 `live` 块，把候选 + 最近播过什么交给模型，模型只回
`{module, minutes, say, reason}`；导演停当前模块、切 `config.game`、按模块声明的
模式重新开播。运行中点「停止直播模式」即停（连带停模块）。

- 候选来自游戏包，核心不写死 id；验收用的 `toy-duel` 没声明 `live`，不会混进来。
- 模型失败/输出不合法 → 按顺序轮换并记 `llm_fallback`（`stage=director`）。
- 状态 `logs/live_director.json`，停止标志 `logs/live_director.stop`；
  Token 仍记进 `logs/autoplay.jsonl`（note「直播导演 选择模块」）。
- 切模块前一定先 `stop_autoplay()`；手动「让她玩 / 让她停手 / play --stop /
  控制台停手」都会先停导演，避免刚开的模块被下一段切走。
- CLI：`python main.py live`、`live --status`、`live --stop`。

## 无人值守一晚上

- **LLM 调用**：超时 / 连接重置 / 429 / 5xx 退避重试 3 次（间隔 1.2s、2.4s）；
  401/403 不重试，状态里直接写「API Key 无效」。3 次都失败就本地兜底，日志
  `llm_fallback`。重试期间第一次坏 JSON 的 token 也会算进账单。
- **游戏掉线**：连续约 60 秒读不到局面（游戏崩了 / 被关了）会自动重启游戏；
  重启后连不上就再等，连续 3 次无效才收手；稳定跑 10 分钟后重启配额重置。
- **日志**：`logs/autoplay.jsonl` 超过 6MB 自动压缩，`llm_usage` 账单行一行不丢，
  控制台的总 token 数始终对得上。
- **录像**：录像盘剩余 < 2GB 就不开录（状态 `record_error` 写原因），游戏照打。
  是否自动录、一局一集还是不分集由 `games/<id>/game.json` 的 `recording` 决定，
  控制台「当前游戏 → OBS 录像」写 `config.json` 覆盖；杀塔 / 幸存者默认开启一局一集，
  刷B站默认关闭不分集。开启时先检查/创建 `game-skill/obs.json`。
- **会主动收手的情况**（其余时间一直循环）：未知界面连续 3 次、同一画面同一失败
  连续 3 次、游戏失联且 3 次重启无效、单局 5000 步上限。收手会写
  `logs/autoplay.json` 的 `last` / `error`，控制台能看到原因。
- **牌组多选（删牌/升级 N 张）卡死**：这是 mod 的已知 blocker（点击只回
  `pending`）。现在识别出来后不问 LLM、也不空点 1000 次，卡住守卫会**自动
  `save_and_quit` 一次**，再从主菜单 `continue_run` 回滚；仍卡住才收手。

## B 站合规（AI 直播）

B 站《主播直播规范》B 类里的「直播无意义内容」（第 1(17) 条，明确写了
**长时间挂机、长时间定格同一或相似画面**）和「恶意/消极直播游戏」（第 1(26) 条，
挂机、无意义内容）是这类直播最容易踩的两条；另外第 1(25) 条禁止展示外挂/辅助
工具。2025-09-01 起《人工智能生成合成内容标识办法》施行，AI 生成的画面/声音
要**主动标识**，不标会被限流、下架甚至封号。

所以这套直播的合规底线：

- **画面要有变化**：卡屏 3 步就停/回滚（`_StallGuard` + `_recover_blind_screen`），
  同一句台词 4 分钟内不重复播（`_say_screen` 去重）；overlay 默认带
  「AI 生成 · 虚拟形象」角标。
- **标题/简介/分区**：写明「AI 生成」「虚拟形象」，别写「无人直播 / 24 小时挂机 /
  自动打牌 / 脚本」这类词——既容易被判挂机，也容易被判展示辅助工具。
- **别通宵无人值守**：单场建议 2–4 小时；人离开前先停手，或留人定期互动。
- **保持互动**：读弹幕、回应礼物（控制台「直播间」就是干这个的）。
- 被判违规时先在主播中心看**具体条款号和截图**再申诉；把卡屏那一段回放删掉。

## 直播姬直接用（不用 OBS）

如果 OBS 推流时进人卡，想直接用哔哩哔哩直播姬开播，叠加层走桌面透明窗口：

1. 启动桌宠，右键鱼身 →「⚙️ 设置」→「桌宠 → 叠加层模板」，选 **状态条 / 电影框 / 弹幕栏 / 极简字幕**；
   再选同一个模板不会重复开，选「关闭」收起。
2. 把桌宠拖到你要直播的那块屏；叠加层默认跟着桌宠所在屏幕走。
3. 直播姬里加「显示器捕获 / 屏幕捕获」（不同版本叫法可能不同），抓这块屏；
   叠加层是置顶、点击穿透的透明窗口，会和游戏/Chrome 画面一起进直播。
   音频选桌面音频/系统声音，不要只抓游戏进程，否则大肥鱼的 TTS 进不去。
4. **不要只用游戏捕获或单个窗口捕获**：那只抓游戏/浏览器窗口本身，桌面叠加层
   不在那个窗口里，会看不到。必须抓整个显示器。
5. 模板选择会写进 `logs/streamer_overlay.json`，下次启动桌宠自动恢复；不想用时
   设置页选「关闭」。

叠加层只读 `/state` 和 `logs/bilibili_events.jsonl`，不读页面、不发送弹幕、不抢鼠标。
OBS 仍然用浏览器源 `/bili`；两种方式不要同时开两个叠加层，二选一即可。

## B 站云观影场景

在直播姬里单独建一个 **B站云观影 / Bili Watch** 场景，不要和 `StS2` 混用：

- 底层放 Chrome 的窗口捕获或副屏显示器捕获；
- 上面放浏览器源 `http://127.0.0.1:8730/bili`（1920×1080，透明，开音频）；
  OBS 源列表里浏览器源必须在采集源**上面**，否则会被视频整层盖住；页面会登记为
  live 音频主，桌宠窗口自动让位，避免同一句 TTS 双重播放；TTS 在页面内提 6 dB、
  限幅后再补 3 dB，OBS 源音量保持 100% 即可，不要再叠增益滤镜；只有 OBS 浏览器源会出声，
  普通浏览器/直播姬内置浏览器打开只显示字幕，不抢 live 音频；
- 音频用桌面音频，不要按 Chrome app 窗口抓（换视频在 app 页面内导航，仍建议桌面音频）；优先复用 OBS
  已有的桌面音频，避免再建一个桌面源导致双倍音量；
- 只保留这个场景页作为 TTS 出声口，不要再同时挂 `/` 桌宠气泡源，避免双声；
- 场景页显示当前视频、虚拟进度、直播弹幕、下一条和「AI 生成 · 虚拟形象」标识。

OBS WebSocket 用户可先切到 B 模式，再 `python main.py obs setup` 自动建
`Bili Watch`、副屏采集和 `Bili Scene`；正在直播不想切当前画面时加 `noswitch`，
只建场景。手动直播姬按上面的源顺序添加即可；
`games/bilibili-hot-commentary/game-skill/obs.json` 里有 `monitor_index` 等参数。

## 直播间互动

- 桌宠进程监听 B 站直播间（`config.json` 的 `bilibili_enabled` / `bilibili_room_id` /
  `bilibili_cookie`），把进入 / 关注 / 分享 / 点赞 / 弹幕 / 礼物 / 醒目留言 / 上舰
  写进 `logs/bilibili_events.jsonl`，**只保留最近 120 条**（文件超 200KB 自动压缩）。
- 控制台「直播间」页读同一份文件：可筛选类型、看最近一条、看各类型计数；
  连接状态来自 `logs/bilibili.json`。
- 页面右侧可以直接发弹幕（`POST /api/bili/send`），需要 Cookie 里有
  `SESSDATA` 和 `bili_jct`（csrf）；发送成功后本机先显示一条「本机」记录。
- **带 Cookie 时 auth 的 uid 必须跟着 Cookie 走**：`getDanmuInfo` 的 token 绑账号，
  uid 写 0 会在 0.1 秒内被 B 站无帧断开（控制台显示「连接失败」，发弹幕却正常）。
  监听会从 `nav` 取 uid（10 分钟缓存）并带 `Cookie/Origin/UA` 握手；Cookie 失效时
  状态里写「登录态不可用」。
- 只开控制台、没开桌宠时，面板只有历史，没有新事件；发弹幕仍然可用。

## 边界

- 游戏内决策：`games/slay-the-spire-2/decision/knowledge/ironclad/OS.md`；说话：
  `games/slay-the-spire-2/persona/speech.md`。
- 出牌走 `tools/act.py` 同款 batch 协议（自动玩法用 `sts2.cmd_batch`）；读局面只走 `tools/ctx.py`。
- API Key 只在本机 `config.json`，绝不回显/提交/写日志。
- B 站 Cookie 同理：只写本机，接口只回 `cookie_set`。
- 桌宠右键会紧贴鱼身右侧弹出蓝色齿轮按钮（黑色打底、只有设置图标），点它才打开设置页；不再是
  Win32 菜单。开关/端口都在 `ui/settings/` + `src/dafeiyu/ui/settings.py`，不要再加
  第二个设置入口，也不要另起一份设置状态。
- 「桌宠 → 启动与常驻」的 **Windows 托盘图标**：左键唤起/收起桌宠，右键打开设置；
  关掉后托盘图标移除。开机自启动写 HKCU Run，不需要管理员。
- 曼波引擎吃内存：不用时在「语音 → 关闭引擎」停掉，`/api/voice/stop` 只杀
  python/pythonw 进程树；下次点「启动」/「试听」会重新拉起。
