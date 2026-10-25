# 下一轮交接（新对话窗口用）

## 当前状态

- 游戏：`MAIN_MENU`，没有进行中的局。
- 录像：**已停止**；OBS `outputActive=false`。
- 上一局：A10 铁甲战士 run9，第 2 层因 **状态过渡快照** 误发 `end_turn` 异常结束；不是正常战败。
- run9 录像：`recordings/2026-10-24 23-18-57.mp4`（168.8 MB，未重命名）。
- 新对话开始后：**从主菜单新开一局 A10，从选人开始录**，不要恢复上一局。

## 环境

- 工作目录：`D:\aigame\sljt\sljt`
- 游戏：Slay the Spire 2 v0.107.1；mod **STS2-Agent v0.8.0**，HTTP `127.0.0.1:8080`
- OBS WebSocket `127.0.0.1:4455`；场景 `StS2`；浏览器源 `AI Commentary`
- Overlay server `127.0.0.1:8730`
- 录制：游戏画面 + 游戏音频；mic/桌面音频静音；1920×1080@60
- DB：`db/sts2.db`（cards/enemies/relics/potions/events/powers/mechanics/memes/strategy_rules）
- 策略库：**53 条** `strategy_rules`

## 必读顺序（新对话先读，不要直接打）

1. `README.md`
2. `docs/A10_GAP_ANALYSIS.md` — 当前差距/P0–P5
3. `docs/ACT1_PLAYBOOK.md` — 第一幕怪/精英/Boss/牌组蓝图
4. `docs/COMBAT_TURN.md` — 每回合链路、ANCHOR、状态稳定、0费清理
5. `docs/SHOP_DECISIONS.md` — 购物清单/删牌/药水/组合总账
6. `docs/CAMPFIRE_UPGRADES.md` — 睡觉 vs 锻造、升级优先级
7. `docs/NEOW_DECISIONS.md` — 开局稀缺性/变2牌
8. `docs/ACT2_ACT3_PLAYBOOK.md` — 后续幕目标
9. `docs/LESSONS.md` — 历届错误
10. `docs/RUN_REVIEW.md` — 每局复盘模板

## 已修复的技术点（很重要）

- `sts2.py` 现在 `cmd_do` 使用 `wait_stable_player_state()`，**不再直接信任动作响应里的过渡 state**。
- `wait_legal()`：动作不在 `available_actions` 里就等待；等不到就 **不发送**（尤其 `end_turn`）。
- 每次行动后打印 `ANCHOR enemies=... incoming=... energy=... hand=...`。
- `incoming` 计算已按 `(total_damage 或 damage) × hits`，多段不会少算。
- `end_turn` 若手里有 0 费可打牌会警告。
- 纯防御牌在 `incoming=0` 时会警告。
- 发现“手牌突然清空但能量没扣”会警告，提示重读，不要结束。

## 下一轮硬规则（P0）

1. **每张牌后重锚定**：敌人死亡/召唤/意图变化 → 重算 incoming、重读目标索引；不要用旧计划。
2. **incoming=0 不纯防御**：除非为 Body Slam/Barricade/Feel No Pain/保留格挡铺垫。
3. **结束前清 0 费**：Spite/Anger/Bloodletting/Battle Trance 等，先打再结束（除非 Thorns/自伤致死）。
4. **状态稳定再行动**：`play_card/end_turn` 不合法就等；不在过渡态结束回合。
5. **HP<50% 下一格必须营火**；HP<35% 不接双怪/精英。
6. **商店先写清单**：最缺/预算/必买/不买；第二次进店没有目标就离开；不买“便宜但不明所以”的牌。
7. **篝火不要默认睡觉**：HP≥70% 且有关键质变升级就锻造；不连敲两张打击/防御。
8. **Neow 比较稀缺性**：商店能补的删牌降权；变 2 张基础牌在血线能兜住时优先。
9. **Act1 只追稳定**：2 AOE、1–2 易伤、2–3 优质防御、1–2 运转、1 收尾；牌组≤15；最多 1 精英；Boss 前花光金币。
10. **Boss 前硬检查**：HP≥70%、2 张优质防御、药水可用、金币≤20、Ringing 回合只打 1 张。

## 启动流程

```powershell
cd D:\aigame\sljt\sljt
python sts2.py state              # 应为 MAIN_MENU
python obsctl.py status           # 应为 outputActive=false

# 1) 重置 overlay + 刷新浏览器源（新对话可让 agent 执行）
# 2) python obsctl.py start
# 3) python sts2.py do open_character_select
# 4) 确认 A10 / IRONCLAD / can_embark
# 5) python sts2.py do embark
# 6) Neow -> python sts2.py route -> 先发路线，再选第一格
```

## 录制/结束

- 从选人开始录；录到本局结束（胜/败/中止）。
- 结束时 `python obsctl.py stop`，把文件重命名为：
  `recordings/sts2_ironclad_a10_run10_<result>.mp4`
- `python sts2.py report`；用 `docs/RUN_REVIEW.md` 复盘；更新 `docs/LESSONS.md` 和策略规则。
- 如果出现异常：**先停手/停录像**，记录最后 state、日志、视频路径，不要连续重开。

## 给新对话的提示词（复制下面整段）

```text
你现在接手一个 Slay the Spire 2 项目：在 Windows 工作目录 D:\aigame\sljt\sljt 下，用本地 mod API 打 A10 铁甲战士，同时录制游戏视频+游戏音频，并用 OBS 浏览器源显示 DeepSeek 大肥鱼 overlay 解说。

当前状态：游戏在 MAIN_MENU，录像已停止，没有进行中的局。上一局 run9 因“状态过渡快照”误发 end_turn 异常结束，不是正常战败；不要恢复上一局。

开始前先读这些文件（按顺序）：
README.md
docs/A10_GAP_ANALYSIS.md
docs/ACT1_PLAYBOOK.md
docs/COMBAT_TURN.md
docs/SHOP_DECISIONS.md
docs/CAMPFIRE_UPGRADES.md
docs/NEOW_DECISIONS.md
docs/ACT2_ACT3_PLAYBOOK.md
docs/LESSONS.md
docs/RUN_REVIEW.md

目标：新开一局 A10 铁甲战士，从选人开始完整录制，打到本局结束；每局结束写战报/复盘/lessons。

启动流程：
1. python sts2.py state 确认 MAIN_MENU；python obsctl.py status 确认未录制。
2. 重置 overlay（POST http://127.0.0.1:8730/reset），刷新 OBS 浏览器源 AI Commentary（PressInputPropertiesButton / refreshnocache）。
3. python obsctl.py start（从选人开始录；只要游戏画面+游戏音频，mic/桌面静音）。
4. python sts2.py do open_character_select；确认 IRONCLAD、A10、can_embark。
5. python sts2.py do embark；Neow 按 docs/NEOW_DECISIONS.md 比较稀缺性（商店能补的删牌降权；变2牌在血线能兜住时优先），不要默认安全。
6. 进地图先 python sts2.py route，把路线发到 overlay，再选第一格；HP<50% 下一格必须营火，HP<35% 不接双怪/精英。

战斗/操作硬规则：
- 每张牌后重读敌人/incoming/目标索引；敌人死亡或意图变化就丢弃旧计划。
- incoming=0 时不要打纯防御（除非为 Body Slam/Barricade/Feel No Pain/保留格挡铺垫）。
- 结束回合前清 0 费牌（Spite/Anger/Bloodletting/Battle Trance 等），除非 Thorns/自伤致死。
- 只信稳定玩家回合：play_card/end_turn 不在 available_actions 就等待，不在过渡态发 end_turn；ANCHOR 的 enemies/incoming 是每次决策依据。
- 用 python sts2.py play <CARD_ID> 出牌，不凭记忆连打；每次动作后看 ANCHOR 和手牌/能量。
- HP<50% 下一格必须营火；商店先写“最缺/预算/必买/不买”，第二次进店没有目标就离开；不买便宜但不解决主要矛盾的牌。
- 篝火 HP≥70% 且有关键质变升级就锻造；不连敲两张打击/防御。
- Act1 目标：2 AOE、1–2 易伤、2–3 优质防御、1–2 运转、1 收尾，牌组≤15、最多 1 精英、Boss 前花光金币；Boss 前 HP≥70%、2 张优质防御、药水可用。
- 解说 banner 只写一句自然中文，不写“大肥鱼/DeepSeek/AI:”前缀，不拆 tag/黄字，整句同色；适度社区梗，不要每句都玩梗。

结束：
python obsctl.py stop；把录像重命名为 recordings/sts2_ironclad_a10_run10_<result>.mp4；python sts2.py report；按 docs/RUN_REVIEW.md 复盘；更新 docs/LESSONS.md 和策略规则。

现在开始：先读文件，确认状态，然后开新局；不要先问我难度，默认 A10。
```
