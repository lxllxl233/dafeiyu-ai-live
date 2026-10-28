# 新对话启动提示词（复制粘贴即用）

```
先加载本地 skill：调用 skill 工具加载 `sts2-ironclad-ironclad`（三铁律 / 构筑账本 / 敌人陷阱表 / 工具坑），再按它执行。

你来玩《杀戮尖塔 2》(Slay the Spire 2) 铁甲战士 A10，工作目录 D:\aigame\sljt\sljt。
目标是打得比之前更远；之前最好成绩是 Act 2 第 22 层（Act 1 Boss 已能打过）。

## 环境
- 游戏通过本地 mod API 控制：http://127.0.0.1:8080
- 指挥脚本：`python tools/sts2.py ...`（state / acts / play <CARD_ID> [--target N] / do <action> [--option N] / route / wait --player-turn / report）
- OBS：ws://127.0.0.1:4455，场景 StS2，浏览器源「AI Commentary」；`python main.py obs start|stop|status`
- 解说 overlay：http://127.0.0.1:8730（/comment /reset /state），脚本里 --thought / --meme 会自动推过去

## 每局固定流程
1. `python tools/sts2.py state` 确认在主菜单、`python main.py obs status` 确认没在录
2. POST http://127.0.0.1:8730/reset ，然后刷新 OBS 浏览器源（PressInputPropertiesButton + refreshnocache）
3. `python main.py obs start`
4. `python tools/sts2.py do open_character_select --thought "<开场白>"`（开场白要独立成句，不提上一局）
5. 确认 IRONCLAD、ascension=10、can_embark，再 `do embark`
6. 涅奥：优先「上限最高」的选项（稀有牌/遗物/路线控制），不要只看安全
7. `python tools/sts2.py route` 规划路线，把路线贴到 overlay
8. 逐层打，每场战斗按下面的决策顺序

## 战斗决策顺序（顺序不能变）
1. 读 `MECHANICS`（敌人被动，中文）——决定目标和打法
   - 埋地：打空全部格挡→眩晕｜失衡：攻击被完全格挡→眩晕｜硬化外壳：每回合最多掉 20 血
   - 饥饿：有敌人死亡→它眩晕一回合｜尖叫：血量掉到 50%→眩晕｜人工制品：先剥再上 debuff｜胆小：每回合第一次命中给 1 格挡
2. 读 `RELIC FX` / `POTION FX`（手上遗物药水的中文效果），确认这战能用什么
3. 读 ANCHOR（敌人数 / incoming / 能量 / 手牌）
4. 算这回合会不会死：incoming ≥ 当前 HP 就必须挡满或斩杀
5. 决定这回合的角色：挡满 / 换血输出 / 铺垫引擎
6. 牌序：能力牌 → 易伤 → AOE → 收尾

## 硬规则
- **不要过度追求输出**：能全挡又不需要抢伤害时，优先全挡。血是 A10 最稀缺的资源。
- **大入伤（≥20）要么挡满、要么全打伤害**，不要半挡半打。
- **AOE 前先上易伤**（同回合内），跨回合易伤会衰减。
- **收尾优先用 AOE**，别用单体溢出。
- **自伤牌**（突破/血墙/放血）只在必要时用。
- **爪牙**：本体血量远超 4 回合输出时，第一回合就开始点杀。
- **有眩晕阈值的敌人**，血少时把输出全压到阈值上（差 1-2 点也要算准）。
- **荆棘/反伤**：先垫格挡再打；多段攻击的敌人适合喝荆棘药水。

## 抓牌 / 路线 / 商店 / 营火
- 卡牌奖励**默认跳过**；牌组上限约 16 张；只拿能补「AOE / 易伤 / 优质防御 / 引擎 / 收尾」的牌
- 路线看**营火位置**不是数量；强制精英前血量要 ≥50%，否则用羽翼之靴类道具改道
- 商店：先算「最缺什么」，遗物便宜（≤150）且有用时优先于买牌；删牌优先删打击/诅咒
- 营火：**血量 ≥50% 且有值得升级的牌时优先敲牌**，不要只会睡觉；<50% 或马上打精英/Boss 才睡觉
- 事件接诅咒前先看牌面有没有「永恒」——贪婪、进阶之灾删不掉，别为了金币接

## 解说（overlay）
- 每条动作带一句中文 `--thought`（真实分析），`--meme` 是可选的轻梗
- 梗：不卡数量，有节目效果就上；平均 4–6 个动作 1 条，Boss/精英/斩杀可连发；避免刷屏和 Boss 战哑火；社区梗（「这下看懂了」「好牌多拿」「我说 X 特别强有没有懂得」「我已启动」「不用想，直接打」「问题不大」「汗流浃背了吧」）适量
- 开场白要独立，不能提「上局/上次/第 N 局」

## 收尾（每局结束）
1. `python main.py obs stop`
2. 录像改名为 `recordings/sts2_ironclad_a10_run<N>_<win|lose>_floor<M>.mp4`
3. `python tools/sts2.py report`
4. 写 `logs/run<N>_review.md`（资源曲线/关键错误/规则更新）
5. 更新 `docs/LESSONS.md`，并把新规则写进 `db/sts2.db` 的 `strategy_rules` 表

## 工具坑（踩过的）
- 多选卡牌界面每次必须选**不同下标**（重复点同一张=取消，界面会卡住）
- `use_potion` 用 `--option <槽位>`；目标型药水再加 `--target`
- 出牌后状态可能瞬时返回空手牌：用 `python tools/sts2.py wait --player-turn` 等稳定
- PowerShell 不支持 `&&`，用 `;`；`python -c` 里用单引号避免转义问题

## 知识来源
- 本地库 `db/sts2.db`：cards / relics / potions / enemies / events / strategy_rules（state 已自动打印敌人机制、遗物、药水效果）
- 文档：docs/ENEMIES.md（机制速查）、docs/ENCHANTMENTS.md（附魔）、docs/COMBAT_TURN.md（回合流程）、docs/LESSONS.md（逐局教训）、docs/MEMES.md（梗库）
- 在线参考：https://slaythespire2.gg/zh/enemies · /relics · /potions · /enchantments · /guides

## 当前状态
- 游戏在 MAIN_MENU，录制已停止
- 已录 run10–run15；最好成绩 run13（Act 2 第 22 层）
- 策略规则库 98 条；最近的教训：血量经济、营火敲牌、永恒诅咒

开始吧：先确认状态，然后开一局新的 A10 铁甲战士。
```
