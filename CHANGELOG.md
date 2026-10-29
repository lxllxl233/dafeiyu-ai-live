# Changelog

## [Unreleased]

### Changed

- **直播合规**：overlay 增加「AI 生成 · 虚拟形象」角标（2025-09-01 起《人工智能
  生成合成内容标识办法》要求主动标识）；同一句台词 4 分钟内不再重复播
  （`_say_screen` 去重），减少「长时间重复同一内容/挂机」的机器特征。
  `docs/LIVE_OPS.md` 增加「B 站合规」清单：挂机/定格画面、AI 标识、标题用词、时长。
- **LLM 决策提速：默认关思维链**。实测 `deepseek-flash` 开着 thinking 时一次
  战斗决策要 24s、`completion_tokens=4096` 全是 reasoning、`content` 为空，
  正好撞上 4096 预算和 25s 超时；界面决策 10–12s/步也主要是它在耗。新增
  `autoplay_llm_thinking`（默认 `off`）→ 请求带 `thinking:{"type":"disabled"}`，
  同一局面 1.7s 出计划；`on` 恢复思维链，`auto` 只让战斗跟模型默认。provider
  不认这个字段时自动去掉重试。`llm_usage` 行和 Token 面板每一步都多一个耗时 `ms`。
- **界面决策改为「先说话再操作」**：`_say_screen` 从 `actions.execute` 之后移到之前，
  和战斗回合一致（`cmd_batch` 本来就是先推字幕再出牌）；观众先听到理由再看到画面变化。
- **牌组多选（移除/升级 N 张）改为逐张点不同牌**：不再依赖 mod 的 `selected_count`
  （旧 mod 恒 0，点第二次会把第一张取消），本地按 0、1、2… 顺序点，点满 `need` 张
  后调 `confirm_selection`；`--mode llm` 下这一屏直接本地处理（模型看不到已选张数，
  只会重复点同一张，run-0113 一晚烧了 530 万 token）。删掉不适用于 12+ 张牌组的
  `ui_pick_cards` 鼠标坐标兜底。

### Fixed

- **「从牌组移除 N 张」终于能确认（mod 重编）**：根因在 mod，不在客户端。
  `NDeckCardSelectScreen` 的确认按钮是私有字段 `_confirmButton` /
  `_previewConfirmButton`，v0.8.0 的 `ConfirmDeckSelectionAsync` 只找
  `%PreviewConfirm` / `%Confirm` 节点 → 永远点不到；`select_deck_card` 只回
  `pending`，`min/max/selected_count` 也一律 0。现在用 .NET 9 SDK 从
  `lxllxl233/STS2-Agent` v0.8.0 源码重编（分支 `compat/v0.107.1`），补丁：
  ① `Player.CanUseOrRemovePotions` 在 v0.107.1 已删除，改回 `true`（原先靠 IL 补丁）；
  ② 反射私有确认按钮，并把牌组屏接进 `CanConfirmSelection` /
  `ExecuteConfirmSelectionAsync`；
  ③ 从提示文本解析 min/max（先去掉 BBCode），已选张数读 `_previewCards`；
  ④ 点确认前用游戏自己的 `CheckIfSelectionComplete` 把关（选不满就点确认，游戏会
  只移除已选张数并直接结束事件）；
  ⑤ `select_deck_card` 不再自动确认，交给 `confirm_selection`。
  实测（2026-10-29，游戏 v0.107.1）：涅奥「松动羊毛剪」（移除 2 张）`min/max=2`，
  点 1 张时 `confirm_selection` 不出现、点满 2 张才出现，确认后回到事件、牌组 11→9。
- **删牌多选屏空转一晚上（run-0113）**：涅奥「移除 2 张牌」把游戏带进
  `CARD_SELECTION`，mod 的 `select_deck_card` 只回 `pending`、`selected_count`
  永远 0；LLM 每 13 秒回一次「选牌 select_deck_card 0」，从 03:51 卡到 09:58，
  **1332 步、烧掉 530 万 token**（占整局 82%）。三处修：
  ① `_stall_key` 现在把重复的 `CARD ...` 返回串算作没进展（真正选上牌时串会变）；
  ② 识别「min/max 都是 0 的牌组多选」后不再问 LLM，直接本地处理，点一次没进展
  就交给守卫；
  ③ 守卫判定卡住时**自动 `save_and_quit` 一次**并从主菜单 `continue_run` 回滚
  （文档里的临时绕过），仍卡住才收手。`NONCOMBAT_PROMPT` 同时写明这类选项别选。
- **无人值守连播的加固**（睡觉前检查）：
  ① `LLMPlanner` 对超时/连接重置/429/5xx 退避重试 3 次（1.2s、2.4s），401/403
  不重试并提示 Key 失效；坏 JSON 重试时把第一次的 token 也并进账单；
  ② 战斗侧任何 `LLM_FAILED`（包括网络异常）都本地兜底，不再整局停手；界面侧
  只有「模型回了但不可执行」才重问一次，超时/限流不再叠加第 2 层重试；
  ③ 主循环加游戏看门狗：约 60 秒读不到局面就重启游戏（3 次配额，稳定 10 分钟
  重置），启动超时也会重启一次；GAME_OVER 回主菜单失败重试 3 次；每步状态刷新
  异常不再打成 fatal；
  ④ `autoplay.jsonl` 超 6MB 自动压缩，`llm_usage` 行全部保留（token 总额不变）；
  ⑤ 录像盘剩余 < 2GB 自动跳过录像（游戏照打）。
- **提示词里还有三处「说错」**（同一类：说给模型听的和执行器实际做的相反）：
  ①`seq 最后必须是 end` 与 `sim.validate` 允许半手（返回 `mid:1`）冲突，模型抽完牌
  会硬结束回合；②非战斗 `i 用 choice.*[].i`，宝箱/选牌的 `choice` 没有 `i` 列表，
  改成「照抄 `context.acts` 里那一项的 `i`」；③`fx.p 不会被执行` 在兼容层加上后
  已不成立（会被挪进 seq），改成「药水别写进 fx」。`combat_goal` 同步，OS.md 同步，
  加回归测试钉住这三类断言。
- **直播间一直「连接失败」但发弹幕正常**：带 Cookie 时 `getDanmuInfo` 的 token
  是绑账号的，而监听 auth 里 uid 硬编码 `0`，B 站 0.1 秒内直接断开（连 close
  frame 都没有），状态只剩「连接断开，正在重连」。现在从 `nav` 取 Cookie 对应的
  uid（10 分钟缓存），握手带 `Cookie/Origin/UA`，auth 用真实 uid；Cookie 失效时
  状态明确写「登录态不可用」。实测监听 12 秒保持 connected。
- **不会用药水（run-0112）**：`SYSTEM_PROMPT` 把药水写成「fx 的 p」，但执行器
  只认 `seq` 里的 `{"p": 槽位, "t": 目标}`，`fx` 只校验 e/hp/take——模型以为喝了，
  实际一滴没喝。提示词改成 seq 写法并注明 fx 限制，决策手册同步；
  `act.normalize` 兼容旧习惯：`fx` 里带 `p/potion` 时自动挪成一步药水放到最前面。

### Added

- **界面决策也走 LLM**：新增 `games/slay-the-spire-2/decision/actions.py`——把
  地图/事件/营火/商店/宝箱/奖励/选牌的可点项做成白名单菜单，模型只回
  `{"say": [...], "act": "...", "i": N}`；禁用项、越界下标、买不起的货、
  `save_and_quit` / `use_potion` 这类越界动作全部拒绝。`runner.py::_screen_llm`
  在 `--mode llm` 下先问模型，带原因重问一次，仍不行才落本地兜底并记 `llm_fallback`。
- **非战斗提示词**：`decision/prompts.py` 新增 `NONCOMBAT_PROMPT` / `noncombat_goal`，
  写清事件不可逆代价、血闸路线、营火/商店/奖励取舍和 `say` 的数字纪律。
- **决策手册与人设落位**：DSH 技能目录删除，内容拆进项目：
  `decision/knowledge/ironclad/OS.md` + `references/`、`decision/knowledge/defect/OS.md`、
  `persona/speech.md` / `persona/lines.md` / `persona/quips.md`、`docs/LIVE_OPS.md`；
  新增两份目录说明和 `tests/test_knowledge_layout.py`（无 `.dsh` 路径、文件齐全、提示词限额）。
- 测试：`tests/test_actions.py`（菜单/校验/执行）、`tests/test_autoplay.py` 增补
  「llm 先问模型 / 拒绝越界动作 / 重问一次 / 规划失败兜底 / 过场不过模型」5 条。
- **B 站直播间互动**：监听从「只收礼物」扩到进入 / 关注 / 分享 / 点赞 / 弹幕 /
  礼物 / 醒目留言 / 上舰（含 `INTERACT_WORD_V2` 的 base64 protobuf 解码），
  写 `logs/bilibili_events.jsonl`，**只保留最近 120 条**（超 200KB 自动压缩）。
  控制台新增「直播间」页：类型筛选、最近一条、分类计数；右侧输入框可直接发弹幕
  （`POST /api/bili/send` → `live.bilibili.send_danmu`，需要带 `bili_jct` 的 Cookie）。
- **Token 账单**：`AutoplayHost.add_usage` 增加 `note`/`step`，`token_usage()` 汇总
  `logs/autoplay.jsonl` 的 `llm_usage`；控制台「Agent 流程 → Token 花费」显示
  合计 / 输入输出 / 调用次数 / 按类型 / 最近 20 步每步 token（含跨进程累计）。
  战斗回合记 `战斗 f7 t3`，界面决策记 `地图 choose_map_node 2` 这类说明。
- **跨局上下文隔离**：新增 `tests/test_context_isolation.py`，证明每次规划只发
  system + 当前 `goal/feedback/context`，换局后不带上一局的 run_id、楼层、牌组、
  台词或聊天历史。

### Fixed

- **营火卡死（run-0110）**：`_rest` 以前只按血量挑标题、不看 `is_enabled`，满血 1/1 且牌
  全升级时每轮都点被禁用的「锻造」，被 409 打回后原样重放 1628 次。现在只在可用项里选，
  全禁用时走 `proceed` 或报 `REST all disabled`。
- **主循环原地打转**：新增 `_StallGuard`（同画面同失败连续 3 次即收手），失败键用
  `_error_code()` 归一异常串；旧的 `("CHEST","REST no options",…)` 字面量判断不再生效。
- `tools/check_skill.py` / `check_caster.py` / `test_rules_in_ctx.py` /
  `test_docs_consistency.py` 改指新位置，并去掉已不存在的 YAML frontmatter 检查。
- **「让她玩」启动上一局的游戏**：`agent/play.py::_game_id` 以前先读
  `logs/autoplay.json` 的 `game`，而 toy-duel 验收跑完后这份状态文件一直留着
  `game: toy-duel`，于是一点「让她玩 ▸ LLM 规划」就去启动玩具决斗并提示
  「只支持 local 模式」，用户以为 LLM 拉起的是《杀戮尖塔 2》。现在启动以
  `config.game` 为准，只有状态文件确实 `running` 时才按它停手/看状态；
  `AutoplayHost.status()` 也不再让上一局的 `game/mode/pace` 盖住当前宿主。
- **营火/宝箱/选牌/事件不播报**：界面决策只调了 `_remember_speech`（它只设
  「等语音」的计时窗口），没有调 `overlay_say`，所以 llm 模式下这些屏虽然记了
  台词，桌面宠物和 OBS 一句都听不到。现在点完动作后走 `_say_screen`：
  `sts2.overlay_say(tag_for(screen), text, "", state)` 真正推送，tag 按屏幕取
  （营火/商店/宝箱/选牌…）。补回归测试断言快照的 `overlay_say` 收到台词与 tag。
- **同类卡死审计（营火 / 商店之后）**：顺着这两个直接原因把「一步没推进就整局停」
  的同源问题一起收了：
  ① `UNKNOWN`/`* unknown` 原来第一次出现就收尾 → 现在同画面连续 3 次才停，
  动画/过场/兜底缺口多给两步；② `CARD wait N/N`、`CARD ui wait`、
  `WAIT_MAIN_MENU`/`WAIT_CHARACTER_SELECT` 这类「没推进」原来不计入原地打转，
  会一直转到 5000 步上限 → 现在计入 `_StallGuard`；③ 宝箱/选牌的白名单菜单缺
  `claim_all_rewards`、`choose_reward_card`、`skip_reward_cards` 等真实动作，
  llm 模式下这两屏其实一直在悄悄走本地启发式 → 菜单补齐；本地兜底也补了
  `claim_all_rewards`/`close_chest`/`dismiss_modal`。战斗侧确认 `_combat` 早就会在
  `LLM_FAILED_*` 时落到本地算账，不用改。
- **商店卡死（run-0111）**：货架打开时游戏只给 `close_shop_inventory`，不给
  `leave_shop`；`decision/actions.py` 的商店菜单和 `_shop` 兜底都没列这一项，于是
  模型在买完牌后一次 `Read timed out`，兜底返回 `SHOP unknown`，整局停在商店。
  现在菜单有「关闭货架」，兜底先关货架再离开；`_screen_llm` 对超时/网络错也重试
  一次（界面决策超时 25s→40s），两次都失败才落本地。补 4 条回归测试。
- **B 站连接每 20 秒刷一屏 traceback**：B 站弹幕服务器不回 WebSocket 层 ping，
  客户端默认 keepalive 每 20s 报 `sent 1011 keepalive ping timeout` 并打印三条
  链式栈；重连又被指数退避拖到 60s。现在 `connect(..., ping_interval=None)`
  只发 30s 一次的 JSON 心跳，兜底过滤器收掉 `keepalive ping failed`，连上后掉线
  3s 重连（连不上才退避），并把「连接断开，正在重连」写进 `logs/bilibili.json`。

## [2.2.0] - 2026-10-28

### Added

- **第二游戏插件 `toy-duel`**：完全离线的观察/控制/自动玩验收包，证明核心不依赖
  《杀戮尖塔 2》；`GameRegistry`、facade、`AutoplayHost`、`GameTools` 共用同一套契约。
- **跨游戏一致性门禁**：`tests/test_game_conformance.py` 至少检查两个完整插件包；
  `tests/test_architecture_boundary.py` 把 `toy-duel` / `玩具决斗` 也加入核心禁用 token。
- **发布门禁**：`tools/release_check.py`、GitHub Actions CI、`docs/RELEASE.md`、本 changelog。
- **`tools/README.md`**：说明 shim、运维脚本和回归测试的边界，以及 shim 退役条件。

### Changed

- STS2 决策引擎整体位于 `games/slay-the-spire-2/decision/`；`tools/` 下同名文件只是
  兼容 shim，旧命令和旧 skill 继续可用。
- 自动玩停止收尾会清理 `logs/autoplay.stop`，并补了同进程停手回归断言。
- `AutoplayHost` 构造时把 `game/mode/pace` 写入初始状态，跨进程停手不再写出空游戏。
- `AutoplayRunner` 现在是 `runtime_checkable`，插件一致性测试可直接检查。

### Verified

- 实机 STS2 LLM 自动玩：录制 `run-0108`（`run.mp4` 约 92 MB，15 事件 / 10 章节），
  3 次 LLM、7,166 tokens，开局路线锁定，无开场问候，语音节奏生效，停手后 OBS 收尾。
- `python -m pytest -q tests`：115 项通过。
- `python tools/test_all.py`：39/39 通过。
- `python main.py doctor`：9 项，0 BAD（overlay 服务开启时）。

## [2.1.0]

- 跨游戏插件契约、`AutoplayHost`、STS2 插件/自动玩拆分、`ActionResult` 统一返回值；
  旧的 v1 工作流和 `tools/` 命令保留兼容。
