# 杀戮尖塔 2 · 铁甲战士 A10 实况录制工程

## 本局结果

- 角色/难度：Ironclad / Ascension 10（双 Boss）
- 结果：**败**，Act 1 第 15 层，0/85 血被强制精英「Byrdois（鸟龙，90 血）」收掉（SL 常规 1/3）
- 决策动作：227 次
- 录像：`recordings/sts2_ironclad_a10_run56_lose_floor15.mp4`（1.31 GB）
- 战报：`logs/run_20261026_010149.md`；复盘：`logs/run56_review.md`；教训见 `docs/LESSONS.md` 第 56 局
- 亮点：Neow 沉重石板拿到岿然不动；地精之角 + 拆卸的连杀链；闪电霹雳挂易伤 → 拆卸两段 28 伤；SL 判定合规（10 血 vs 4 虫）
- 败因：① 第 3 层 109 金换「变 2 张」，诅咒洗成债务，全程没能删牌；② 第 6 层 -14 血换天选芝士，血线掉到 44%；③ 牌组 20 张含 6 张死牌，连续空手；④ 第 12、14 层都是唯一出口的强制精英，20% 血进必死

## 评论气泡（overlay）

- **一次只显示一条**：新评论整块替换旧评论，不堆叠、不折叠、不裁切；没有评论时整块隐藏。
- **像朋友聊天那样说话**：`思路` 与 `吐槽` 用正常标点连成一段，不加破折号、不加标签、不套固定句式——各句自己收尾，读起来就是一口气说完的几句话。
  - 例：`四园丁 120 血，清场要 12 回合而血量只够 3 回合。这不叫战斗，这叫一道已经批改完的数学题。`
- 超长自动省略（思路 ≤120 字、吐槽 ≤70 字，合计超 110/170 字降字号），全文仍在 `logs/run_*.md` 里。
- **动森式动物语音效**：每条评论按字数合成一串短音节（五声音阶随机上下行，Web Audio 合成，不需要音频素材），随气泡一起播放。默认开；`python tools/reload_overlay.py --sfx 0` 关、`--vol 0.8` 调音量（也可在地址加 `?sfx=0` / `?vol=0.8`）。OBS 的 `AI Commentary` 已设 `reroute_audio`（OBS 接管音频），音效会进录像 Track 1；`python obsctl.py health` 的「评论音效」可查，`python obsctl.py free` 会重新接管。
- 改 `overlay/index.html` 后必须让 OBS 浏览器源重新加载：`python tools/reload_overlay.py`（带 cache-buster 重设 URL）。
- 截图验收：`python tools/verify_overlay_speech.py`（发 4 条不同句式的评论并逐条截图）/ `tools/verify_overlay_single.py`（确认只显示最新一条）。
- 写 `--thought` / `--meme` 时的配合：thought 说“在算什么、打算怎么打”，meme 接着往下说人话；两句都能独立成句，别为拼接硬凑语气。

## 主播模式（桌面透明弹幕窗，2026-10-26）

OBS 里的浏览器源能吃到透明通道，**普通窗口不行**（Chromium 窗口不支持逐像素 alpha）。所以
`streamer.py` 自己开一个 Win32 分层窗口，用 Pillow 按 `index.html` 的配色/字号/圆角/阴影现画，
数据仍走 `overlay_server.py` 的 `/state`——**和 OBS 里那条气泡同一份数据、同一套设计**。

```powershell
python streamer.py                 # 起窗（overlay server 没跑会自动拉起）
python streamer.py --demo          # 不接游戏，先放一条示例试位置
python streamer.py --click-through # 开局就点击穿透（打游戏时鼠标直接穿过去）
python streamer.py --preview out.png  # 只渲一张图，验收用
```

- **左键拖动**挪位置，松手自动记到 `logs/streamer_window.json`；换显示器/拔副屏也不会跑丢（会夹回可见区）。
- **右键菜单**：置顶 / 点击穿透 / 重新显示示例 / 关闭。
- **热键**：`Ctrl+Alt+D` 点击穿透（穿透后点不到窗口，用它切回来）· `Ctrl+Alt+H` 隐藏 · `Ctrl+Alt+Q` 退出。
- 透明是真的逐像素 alpha：圆角、投影、面板 0.95/0.88 的半透明都保留，桌面和游戏都能透出来。
- 说话时头像会轻轻跳一下（对应页面里的 bounce），新弹幕滑入 + 渐显，长句自动换行并降字号。
- **只画不发声**：动森式音效仍在 OBS 的浏览器源里（`AI Commentary`），这个窗是纯视觉预览。
- **直播怎么接**：显示器采集时它就在画面里（推荐，最省事）；OBS 的**窗口采集**对分层窗口可能丢掉 alpha，
  要透明干净就继续用原来的 `AI Commentary` 浏览器源，这个窗只当自己盯着看的预览。
- 回归：`python tools/test_streamer.py`（渲染尺寸/透明/换行/裁字/帧耗时）。

## 技术方案

```
游戏窗口（1080p 副屏 DISPLAY5，窗口化最大化）
  └─ STS2-Agent v0.8.0 mod（本地 HTTP :8080）
        ↑ /state、/actions/available、/action
  sts2.py（压缩状态、动作、等待、JSONL 战报）
  overlay_server.py :8730 + overlay/index.html
        ↑ 浏览器源（透明弹幕）        ↑ streamer.py（桌面透明弹幕窗，主播模式）
  OBS 32.2.2（场景 StS2：显示器采集裁切 + 浏览器源；NVENC 1080p60）
        └─ recordings/*.mp4
```

- 决策不使用截图识别；只读取结构化 JSON。
- 未使用 mod 的 LLM Auto-Play，未配置模型端点。
- 控制方式：Steam 启动 + `--windowed`，再用 Win32 把窗口移到副屏 (2560,0) 并最大化。

## 关键改动与备份

1. **mod**：`STS2AIAgent v0.8.0` 安装到游戏 `mods/`，并在 `settings.save` 中启用。
2. **兼容性补丁**：v0.8.0 在 v0.107.1 上会调用不存在的
   `Player.CanUseOrRemovePotions`，导致 `/state` 500。已把
   `STS2AIAgent.dll` 中 `IsPotionDiscardable` 的该调用改为返回 true
   （`pop; ldc.i4.1`），原 DLL 备份在 `backups/STS2AIAgent.dll.orig`。
3. **解锁 A10**：游戏内控制台（Shift+8）执行 `unlock all`，解锁全部卡牌/遗物/进阶；
   这只会影响 modded profile。相关存档备份在 `backups/`。
4. **OBS**：启用 obs-websocket（127.0.0.1:4455），场景 `StS2`：
   - `StS2 Monitor`：DISPLAY5 采集，裁掉标题栏后拉伸到 1080p；
   - `AI Commentary`：透明浏览器源，顶部弹幕；
   - `StS2 Audio`：进程音频采集（SlayTheSpire2.exe），Track 1/AAC。

## 战斗数值行（state 里自动算）

每回合不用心算，`state` 直接给出逐卡逐敌的结果：

```
ANCHOR enemies=2 incoming=21 energy=3 hand=5 | self: hp=23 blk=0 力2 虚弱1 | TOADPOLE[Attack dmg=7x3=21 多段3次 td=21]
ENEMIES 2
 [0] GARDENER hp=31/31 block=0 intent=Attack 8 powers=(胆小:7)
 [1] GARDENER hp=27/27 block=0 intent=Attack 10 powers=(力量:3, 易伤:2)
EFF [0] BASH 8伤 → e0:1(胆小7) e1:12(易伤) | 反伤-2
EFF [3] STRIKE_IRONCLAD+ 9伤 → e0:2(胆小7) e1:13(易伤)
ORDER 先打 [0] BASH 挂易伤 → STRIKE+ 9→13
COPIES STRIKE_IRONCLAD: 升级版 [3] vs 未升级 [1] → 打升级版
```

- `EFF` = 每段伤害 × 段数，含**易伤 ×1.5 / 敌人格挡池 / 胆小首击加盾 / 条件多段（拆卸 8x2）/ 荆棘反伤**，`★斩` 标可斩杀目标。
- `ORDER` = 手里有易伤牌时，先挂能把其它刀抬多少。
- `COPIES` / `WRONG_COPY` = 同名不同级提醒，防止把未升级那张打出去。
- 多段意图统一印成 `dmg=8x4=32 多段4次`，挡牌按较大值。
- **省 token 设计（2026-10-26，第 56 局后）**：`state` 默认只打**增量**——药水/遗物/诅咒/机制只在一行 `HUD` 里常驻，完整 `POTION FX / RELIC FX / DECK_BAD / MECHANICS` 只在变化时出现；手里见过的牌走短格式（`DISMANTLE c1 敌 | Damage=8`），第一次见到才打全卡面。实测重复读态比旧版短 **31–45%**。要全量用 `state --full`（新卡、新药水、换幕时看一次）。
- **出牌后只回一行**：`play` / `do play_card` 在战斗内只打 `DELTA E2→1 blk0 hp53 · e0:32 · hand4` + 极简手牌/敌人/入伤（约 **280 B**，比热读再省 **73%**）；要看逐卡 EFF/斩杀表就 `state` 一次（每回合 1 次足够），或 `play … --full`。
- **一回合一条 `batch`（v18，省 token 的主开关）**：整个回合的出牌 + `end_turn` 写进一条命令，末尾**直接吐出下一回合的完整快照**，下一回合不用再 `state`。安全由工具保证——每步等「玩家回合」快照、按 `card_id` 重解析下标、撞过渡快照自动重读、缺牌/超费/目标失效立即停并打印当前状态。实测一拍 3 刀的回合同比：**请求 5 次 → 1 次，回显 2,279 → 610 字符**（`python tools/compare_turn_protocol.py`）。顺序确实定不下来时才退回单张。

## 分层记忆（2026-10-26，回答「能不能用结构化摘要」）

**能，而且比压输出更狠**——压输出是省每次回显，分层记忆是省「整局聊天都留在上下文里」。

| 层 | 命令 | 体积 | 何时用 |
|---|---|---|---|
| L0 规则 | skill（`.dsh/skills/sts2-ironclad-ironclad`，v18） | ~3.6k tok | 常驻（一次，别重复加载） |
| L1 结构化记忆 | `python sts2.py memory`（`--write` 落盘 `logs/run_memory.md`） | ~150 tok | 开局/换幕/新 session 恢复 |
| L2 当前状态 | `batch` 尾部快照（没有就用 `state`） | 90–350 tok | 每回合 1 次 |
| L3 细节检索 | `python sts2.py journal --floor 12 --last 20` | 按需 | 回溯某层怎么掉的、复盘 |

`memory` 从当前 `state` + 本局 JSONL + `route_plan.json` 现算，输出一屏（实测 449 B / 9 行）：

```
MEMORY run_56 | IRONCLAD A10 | act1 f12 | HP 17/85 (20%) | gold 59 | deck 20 | SL 1/3
DECK 20: atk8 def5 skill5 pwr2 curse2 up0 | AOE 1
KEY BASH,DISMANTLEx2,BLUDGEON,THUNDERCLAP,IMPERVIOUS,TAUNT,TORIC_TOUGHNESS
RELICS BURNING_BLOOD,HEFTY_TABLET,CHOSEN_CHEESE,GREMLIN_HORN,MEAL_TICKET
CURSES DEBT
HP_CURVE f1:64 f3:53 f6:47 f9:37 f12:17 | min 17
PLAN opt0 四营火线，末尾商店删受伤
LAST f12 save_and_quit hp17 | f12 continue_run hp17 | f12 choose_map_node hp17
RISK 血闸<50% | 牌组>18 | 诅咒1
```

- **新 session 恢复**：`memory` + `state` 两条就够，不用把上一局聊天带进来；建议**每局开一个新会话**（这局的 255K 里有相当一部分是上一局 + 改代码 + 复盘）。
- **换幕/收尾**：`memory --write`，下一幕/下一局直接读它；原始 `logs/run_*.jsonl` 永远不进上下文。
- 回归测试：`python tools/test_memory.py`。

## 常用命令

```powershell
cd D:\aigame\sljt\sljt
python sts2.py state            # 压缩状态（只打增量：药水/遗物/机制/卡面说明变化时才出现）
python sts2.py state --full     # 全量状态（新卡/新药水/换幕时看一眼）
python sts2.py acts             # 当前合法动作
python sts2.py do play_card --card 0 --target 0 --thought "..." --meme "..."
python sts2.py batch '{"actions":[{"action":"play_card","card_id":"BASH","target_index":0},{"action":"play_card","card_id":"DISMANTLE","target_index":0},{"action":"end_turn","thought":"...","meme":"..."}]}'
                                # 一回合一条：每步等玩家回合快照、按 card_id 找牌、异常即停，
                                # 末尾直接给下一回合快照（下一回合不用再 state）
python sts2.py do end_turn --thought "..." --meme "..."
python sts2.py do select_deck_card --options 0,2 --thought "..."   # 多选界面一次点完（自动确认）
python sts2.py say --thought "..." --meme "..."
python sts2.py route            # 路线规划（分数已含 HP/防御数/AOE/药水）
python sts2.py route --commit 0 --note "商店线"   # 把 opt0 记为当前计划
python sts2.py plan             # 对账：PLAN_OK / REPLAN（状态一变就提示重规划）
python sts2.py report           # JSONL -> Markdown
python tools/test_state_compact.py   # 状态增量输出回归
python tools/test_batch.py           # batch 回归（等快照/按 card_id/超费/过渡态/异常停）
python tools/audit_tokens.py          # token 账本：请求数/上下文/各命令回显（读 DSH 日志）
python tools/compare_turn_protocol.py # 旧协议 vs v18 一回合一条 batch 的回显/请求对比
python tools/check_skill.py           # skill 规则覆盖断言（阈值/关键词/反模式编号不许丢）
python streamer.py                    # 主播模式：桌面透明弹幕窗（左键拖 · 右键菜单 · Ctrl+Alt+D 穿透）
python tools/test_streamer.py         # 主播模式渲染回归（尺寸/透明/换行/帧耗时）
python obsctl.py free           # 切窗口/游戏采集（切到 B 站也继续录游戏画面）
python obsctl.py free window    # 游戏采集不工作时，退回窗口采集（WGC）
python obsctl.py health         # 录制自检：有没有画面/声音、游戏窗口是否最小化
python obsctl.py status|start|stop|shot preview.png
```

## 录制时去做别的事（窗口采集 + 关后台静音）

**原来为什么要一直盯着**，两个原因叠在一起：

1. **画面**：录的是「显示器采集」。切到 B 站/别的程序，录到的就是那个画面。
2. **声音**：游戏自带 `mute_in_background`（后台静音），窗口失焦约 20 秒后把音量 tween 到 0；这跟 OBS 无关，任何采集都救不回来。

**已经改好**：

```powershell
python obsctl.py free      # 画面切到 game_capture（窗口模式），关掉显示器采集
python obsctl.py start
python obsctl.py health    # 看「出帧 / 游戏窗口正常 / 后台静音：已关 / 音频：开」
```

- `prefs.save` 里的 `mute_in_background` 已从 `true` 改成 `false`（三个 profile 都改了，原件备份在 `backups/prefs*.save.bak`）；以后若被游戏/云存档改回去，跑 `python obsctl.py nomute` 再重启游戏。
- 实测：游戏**失焦但窗口还开着**时，画面继续出帧、音频继续录（Neow 界面 10 秒失焦后仍有 -44 dB）；`health` 会检查这个开关有没有被游戏/云存档改回去。
- **唯一不能做的是最小化**：最小化后游戏停帧、音频也停（实测 6 秒画面冻结 + 静音）。刷 B 站请把窗口盖在游戏上，或放到另一个显示器；别点「最小化」。
- `python obsctl.py free window` 是 WGC 窗口采集的备用方案（但这游戏失焦时 WGC 会黑屏，优先用默认的 game_capture）。
- 想让失焦时也保持 60fps，可在游戏设置里关掉「后台限制帧率」（`limit_fps_in_background`）。

**已知代价**：最小化时无解；真要长时间最小化，只能改用桌面音频（会连 B 站声音一起录）。

## 路线计划与重规划

- 开局 `route` 选一条大路线，`route --commit <opt>` 存进 `logs/route_plan.json`（含当时的 HP/金币/药水/牌组快照）。
- 每到节点先 `plan` 对账：脚本比对「当前」和「规划时」，列出该重规划的理由——
  血线跌破 70%、本段掉血 ≥20% 最大生命、防御牌跨过 6 张、拿到第一张 AOE、药水用光、金币到 100+、牌组变大 3 张、多了诅咒。
- `PLAN_OK` → 按原计划；`REPLAN` → 重新 `route` 看排序、`route --commit <新opt>` 覆盖，再在 banner 说明改线原因。
- `route` 还会打印 `ELITE_GATE`：防御≥6 / AOE≥1 / 药水≥1，三项全「是」才把精英当选项（评分里的精英惩罚会按这三项加权）。
- `plan --clear` 换幕或彻底改线时清空。

## 选牌界面（CARD_SELECTION）速查

mod 的 `selection` 对象（2026-10-25 实测字段）：`kind`（如 `deck_card_select`）、`prompt`、
`min_select`、`max_select`、`selected_count`、`requires_confirmation`、`can_confirm`、`cards[]`
（每张带 `index`、`card_id`、`upgraded`）。三种界面要分开处理：

| 界面 | 判据 | 正确动作 |
|---|---|---|
| 卡牌奖励三选一 | `max_select=0` 且 legal 有 `choose_reward_card` | `choose_reward_card --option <i>` / `skip_reward_cards` |
| 单选（删 1 张 / 升级 1 张） | `min_select=1, max_select=1` | `do select_deck_card --options <i>` |
| 多选（删 2 张等） | `min_select≥2` | `do select_deck_card --options 0,2`（自动确认；列表在确认前不变，重复点同一张会取消） |

`state` 会打印 `SELECTION_STATE 已选 k/N（最少 m）`，不用再靠猜。

## 重开一局

1. 确保 OBS 与 overlay 服务在跑：
   `python overlay_server.py`、OBS 场景 `StS2`。
2. 从 Steam 启动：`steam.exe -applaunch 2868840 --windowed`
3. 把游戏窗口移到副屏并最大化（或用 `sts2.py` 前的 Win32 脚本）。
4. `python obsctl.py start`，然后 `python sts2.py do return_to_main_menu` /
   `open_character_select` / `select_character` / `increase_ascension` / `embark`。
5. 结束时 `python obsctl.py stop`。

## 已知限制

- OBS 的显示器采集在游戏**全屏独占**下会黑屏；必须窗口化/无边框窗口。
- v0.8.0 mod 是旧版本，若 Steam 把游戏更新到 v0.111+，该 DLL 会不兼容；
  需要换 v0.16.x 并重新验证。
- 本局录像是“涅奥开局”起，不含角色选择界面（角色选择在更早的测试片里）。
- 视频中的停顿是实时思考/读状态的时间，未做剪辑。

## A10 铁甲战士战略框架

- `docs/A10_IRONCLAD.md`：三条不变量（血量≥70%、烧牌最高抓位、牌组 15–20）、四阶段抓牌链路、力量/防御/消耗三轴关系、删牌曲线、微观战斗检查清单。
- `docs/COMBAT_TURN.md`：**每回合出牌链路**（态势锚定→生存下限→节奏转化→牌序工程→结束前检查），与抓牌链路分开。
- `docs/SHOP_DECISIONS.md`：**商店决策链路**（买牌五问、删牌、药水、组合总账、第 5 局 139 金案例）。
- `docs/CAMPFIRE_UPGRADES.md`：**篝火决策**（睡觉 vs 锻造、升级优先级、不要连敲两张打击）。
- `docs/NEOW_DECISIONS.md`：**开局 Neow 决策**（稀缺性比较、变 2 牌 vs 删 1 牌、第 7 局反例）。
- `docs/ACT1_PLAYBOOK.md`：**第一幕实战手册**（牌组蓝图、怪/精英/Boss 打法、奖励/事件/问号）。
- `docs/ACT2_ACT3_PLAYBOOK.md`：**第二/三幕路线图**（牌组定稿、Queen 锁牌、双 Boss、Boss 前检查）。
- `docs/A10_GAP_ANALYSIS.md`：**差距分析**（当前库 vs 通关，P0–P5 优先级）。
- `docs/RUN_REVIEW.md`：**每局复盘模板**（资源曲线、三个关键错误、规则更新）。
- `db/sts2.db` 的 `strategy_rules` 表：53 条可查询规则（含战斗、商店、药水、篝火/升级、Neow、Act1/2/3 规则）。
- `python tools/gamedb.py strategy`：按阶段查看规则。
- 本地校正：Queen 400 HP，`Chains of Binding / 魂缚锁链` 让每回合前 3 张抽牌变 Bound；Ceremonial Beast 262 HP，160 触发 Plow 眩晕，Ringing 阶段每回合只能 1 张牌。
- 重建 `sts2.db` 后需再跑 `python tools/build_memes.py` 和 `python tools/build_strategy.py`。

## 本地资料库

- `db/sts2.db`：SQLite 数据库，626 张卡、127 个敌人、311 件遗物、65 瓶药水、56 个事件、321 条能力/关键词、16 条机制笔记。
- `docs/GAME_DB.md`：机制笔记、第一章威胁表、铁甲战士抓牌优先级、活雾正确打法。
- `tools/gamedb.py`：查询 CLI：
  ```powershell
  python tools/gamedb.py enemy 活雾
  python tools/gamedb.py card 重锤
  python tools/gamedb.py summoners
  python tools/gamedb.py bosses
  python tools/gamedb.py mechanics
  ```
- 数据来源：公开 Wiki `slaythespire2.gg/zh`（参考使用，未调用 `/api/`）+ mod 自带英文数据。
- 已确认关键机制：**活雾死掉后 Gas Bomb 自动消失**；活雾的 Smoggy 让每回合只能出 1 张技能；召唤者/仪式怪优先击杀。

## 开局路线规划

每局 Neow 结束、地图亮出来后，**先规划再走**：

```powershell
python sts2.py route
```

- 它会枚举当前可选节点到 Boss 的所有路径，按“营火/商店/宝箱 vs 精英/小怪”打分，并考虑当前 HP。
- 选一条最优路线，用 `python sts2.py say --thought "路线：..."` 把计划发到 overlay（只写内容，不要“大肥鱼：”前缀）。
- 以下情况**重新规划**并简短说明：HP < 35、单场掉血 > 20、遇到计划外精英/双怪、商店/奖励让牌组明显变化、Boss 前没营火。
- 计划不是死的：路线让位于血量、关键遗物和删牌机会。
- banner 只显示内容，不写“大肥鱼：/DeepSeek:”前缀；代码会自动剥离。

## 梗数据库

- `db/memes.json`：102 条中英文/社区梗，按角色、卡牌、敌人、发牌员、死亡、大肥鱼、**社区口头禅**等分类。
- `db/sts2.db` 的 `memes` 表：同样的 102 条，含强度（1–2）、冷却、触发场景、示例。
- `docs/MEMES.md`：使用手册——每动作最多 1 条、每回合最多 2 条、强度 2 每 2–3 回合一次、逆风只自嘲不刷屏；**梗 ≤1 条/分钟（20 分钟 ≈ 15–25 条，其中口头禅 6–10 条）**；**开场白独立，不提上局**。
- 修改梗库请改 `tools/build_memes.py` 的 `M` 列表后重跑 `python tools/build_memes.py`（同时写 JSON 和 DB 表）。
- `tools/memedb.py`：查询/抽梗 CLI：
  ```powershell
  python tools/memedb.py suggest --situation boss --max-intensity 2
  python tools/memedb.py suggest --situation low_hp --max-intensity 1
  python tools/memedb.py random --tag fish
  python tools/memedb.py list --category 死亡失败
  ```
- 重建 `sts2.db` 后需要再跑一次 `python tools/build_memes.py`，否则 `memes` 表会被重建流程清掉。

## 文件

- `sts2.py`：API 客户端 / 状态压缩 / 动作 / 战报
- `obsctl.py`：OBS WebSocket 控制
- `overlay_server.py`、`overlay/index.html`：弹幕服务与页面
- `streamer.py`：主播模式——桌面透明弹幕窗（Win32 分层窗口 + Pillow，逐像素 alpha）
- `tools/data/eng/`：v0.8.0 自带英文卡牌/遗物/怪物数据（用于可读状态）
- `logs/`：JSONL 与 Markdown 战报
- `recordings/`：成片；`recordings/_tests/`：测试片
- `backups/`：settings、存档、原版 mod DLL、OBS 配置

## 大肥鱼弹幕形象

- 形象按用户提供的参考图制作：`overlay/assets/deepseek_fish.jpg`（蓝发、鲸鱼围裙的 Q 版 DeepSeek）。
- `overlay/index.html` 用圆形头像框展示，带“大肥鱼 · DeepSeek”名牌。
- 新评论出现时，鱼会进入 `talking` 状态：头像上下浮动、右侧语音条跳动；
  说话时长按文本长度自适应（约 2.4–8.5 秒）。
- 预览：
  - `preview_fish5.png`：静帧
  - `recordings/preview_deepseek_talking.mp4`：约 10 秒说话效果
- 弹幕面板宽 1400，位于顶部中央，避开左侧 HP 与右侧药水。

## 音频

- 已静音 OBS 的“麦克风/辅助音频”和“桌面音频”，只保留
  `StS2 Audio`（`wasapi_process_output_capture`，SlayTheSpire2.exe）Track 1。
- 录制中不会出现真人麦克风声音；只保留游戏/电脑音频。
- 预览片音轨为 AAC 立体声 48kHz；正式片同样。

## 复盘文档

- `docs/LESSONS.md`：每局结束后的打法经验与教训；已写入第 1 局 A10 失利的完整复盘。
- 下一局开始前先读该文档，重点修正“防御不足、索引连打、精确斩杀算错”。

