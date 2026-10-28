# 杀戮尖塔 2 · 本地资料库与机制笔记

数据来源：公开 Wiki `https://slaythespire2.gg/zh`（仅作参考使用，未调用 `/api/`）
+ 游戏 mod 自带的英文数据 `tools/data/eng/`。
本地数据库：`db/sts2.db`（SQLite），原始 JSON 在 `db/raw/`。

## 一、资料库规模

| 表 | 数量 | 说明 |
|---|---:|---|
| `cards` | 626 | 卡牌：中英文名、费用、类型、稀有度、描述、升级描述 |
| `enemies` | 127 | 敌人：HP、类型、招式、意图、能力、备注 |
| `relics` | 311 | 遗物：中英文名、稀有度、描述 |
| `potions` | 65 | 药水：中英文名、稀有度、使用时机、描述 |
| `events` | 56 | 事件：标题、描述、选项 |
| `powers` | 321 | 能力/关键词：中英文名、描述 |
| `enchantments` | 22 | 附魔 |
| `keywords` | 8 | 基础关键词 |
| `mechanics` | 16 | 手工整理的机制与战术笔记 |

查询命令：

```powershell
cd D:\aigame\sljt\sljt
python tools/gamedb.py stats
python tools/gamedb.py enemy 活雾
python tools/gamedb.py card 重锤
python tools/gamedb.py summoners
python tools/gamedb.py bosses
python tools/gamedb.py event 自助
python tools/gamedb.py mechanics
python tools/gamedb.py search 易伤
```

## 二、最重要的机制修正

### 1. 召唤物随召唤者死亡消失（Minion）

`GAS_BOMB` 的能力：

> **Minion / 爪牙**：Minions abandon combat without their leader.
> 爪牙会在领导者死亡时放弃战斗。

所以面对 **活雾（Living Fog）** 时：

- 活雾 80 HP，招式：
  - `Advanced Gas / 先进毒气`：攻击 + 给手牌上负面状态；
  - `Bloat / 膨胀`：攻击 + **召唤 Gas Bomb**；
  - `Super Gas Blast / 超级毒气爆炸`：攻击。
- 能力 `Smoggy / 烟雾弥漫`：**每回合只能打出 1 张技能牌**。
- Gas Bomb 只有 7-8 HP，`Explode / DeathBlow` 约 9 点。

**正确打法**：把伤害全部灌到活雾本体，**不要先清 Gas Bomb**；活雾一死，炸弹自动消失。
同时因为 Smoggy，每回合只能在“血墙/防御/过牌”里选 **1 张技能**，其余费用打攻击。

这正是第 3 局第 11 层犯的错：先清炸弹，给了活雾继续召唤和输出的时间。下次直接 Bludgeon/攻击集火本体。

### 2. 其他召唤者

数据库里带 `SUMMON` 或 `MINION` 的敌人包括：

- `LIVING_FOG` 活雾（召唤 Gas Bomb）
- `FABRICATOR` 组装师（150 HP）
- `FOGMOG` 雾菇（74 HP）
- `OVICOPTER` 直飞产卵虫（124-130 HP）
- `THE_OBSCURA` 胧光怪（123 HP）
- `HATCHLING` 幼虫 / `KIN_FOLLOWER` 同族信徒 等

统一原则：**能杀召唤者就先杀召唤者**；小怪会在召唤者死后消失。

### 3. 仪式（Ritual）

`CALCIFIED_CULTIST` 钙化邪教徒 38-41 HP，`Ritual 2`：回合结束 +2 力量。
`DAMP_CULTIST`、`DEVOTED_SCULPTOR` 也有仪式。
**必须优先击杀**，不能和它慢慢打。

### 4. 其他关键能力

| 能力 | 效果 | 应对 |
|---|---|---|
| `Artifact` 人工制品 | 抵挡 1 次负面状态 | 先用低价值 debuff 破，或直接伤害 |
| `Plating` 覆甲 | 回合结束获得格挡 | 岩石铠甲 4 层，长期防御 |
| `Vulnerable` 易伤 | 受到攻击伤害 +50% | 先上易伤再爆发 |
| `Weak` 虚弱 | 造成攻击伤害 -25% | 爆发前可等一回合 |
| `Frail` 脆弱 | 从卡牌获得的格挡 -25% | 低血时优先血墙/固定格挡 |
| `Smoggy` 烟雾弥漫 | 每回合只能 1 张技能 | 只选最关键的一张 |

## 三、第一章威胁表

| 敌人 | HP | 类型 | 关键点 |
|---|---:|---|---|
| Ceremonial Beast 仪式兽 | 252 | Boss | 150 HP 以下会眩晕；有 `Ringing`（每回合只能 1 张牌）阶段；需要 50+ HP 和稳定格挡 |
| Bygone Effigy 旧日雕像 | 127 | Elite | `Slow`：每打一张牌，本回合受到攻击伤害 +10% |
| Byrdonis 多尼斯异鸟 | 81-84 | Elite | 回合结束 +1 力量，多段攻击 |
| Phrog Parasite 异蛙寄生虫 | 61-64 | Elite | 死亡时召唤，优先本体 |
| Calcified Cultist 钙化邪教徒 | 38-41 | Normal | 仪式 2，必须优先杀 |
| Seapunk 海洋混混 | 44-46 | Normal | 叠力量/格挡，A10 可打 8×4 |
| Living Fog 活雾 | 80 | Normal | 召唤 + Smoggy；**杀本体，别清炸弹** |
| Corpse Slug 噬尸蛞蝓 | 25-27 | Normal | 双只一起时总输出高，低血别进 |
| Mawler 蛮兽 | 72 | Normal | 10×2 后接 Buff，会滚雪球 |
| Vine Shambler 藤蔓蹒跚者 | 61 | Normal | 14×2，第一局第 15 层死在这里 |
| Sludge Spinner 淤泥旋螺 | 37-39 | Normal | 虚弱/脆弱，拖久伤害不够 |

## 四、铁甲战士 A10 的抓牌优先级

### 防御（优先级最高）
- **Blood Wall 血墙**：2 费，失去 2 HP，获得 16 格挡。
- **Stone Armor 岩石铠甲**：1 费 Power，4 层覆甲（每回合结束给格挡）。
- **Shrug It Off 耸肩无视**：1 费，8 格挡 + 抽 1。
- **Flame Barrier 火焰屏障**：2 费，12 格挡 + 反伤 4。
- **Taunt 挑衅**：1 费，7 格挡 + 1 易伤。
- **Armaments 武装**：1 费，5 格挡 + 升级手牌。
- **True Grit 坚毅**：1 费，7 格挡 + 随机消耗 1 张。
- **Impervious 岿然不动**：2 费，30 格挡（稀有）。

### 输出/爆发
- **Bludgeon 重锤**：3 费 32（升级 42），Boss 斩杀核心。
- **Perfected Strike 完美打击**：2 费，按“打击”数量加伤；5 张打击时约 18。
- **Molten Fist 熔火之拳**：1 费 10 伤 + 易伤翻倍。
- **Bash 痛击**：2 费 8 伤 + 2 易伤。
- **Anger 愤怒**：0 费 6 伤 + 复制。
- **Uppercut 上勾拳**：2 费 13 伤 + 1 虚弱 + 1 易伤。

### 过牌/能量
- **Pommel Strike 剑柄打击**：1 费 9 伤 + 抽 1。
- **Battle Trance 战斗专注**：0 费抽 3，但本回合不能再抽。
- **Offering 祭品**：0 费失去 6 HP，获得 2 能量，抽 3。
- **Bloodletting 放血**：0 费失去 3 HP，获得 2 能量。

### 消耗流
- **Feel No Pain 无惧疼痛**：消耗牌时获得格挡。
- **Dark Embrace 黑暗之拥**：消耗牌时抽 1。
- **Corruption 腐化**：技能牌费用变 0 并消耗。

## 五、路线与战斗规则（下一局执行）

1. **牌组 ≤ 16 张**；商店优先删 1 张打击/防御（100 金），再买防御/过牌。
2. **血量 < 35 不进双怪/精英**；优先营火和商店。
3. **每回合先算总伤害**：8×4=32、14×2=28、10×2=20、9×2=18；不能斩杀就先把格挡堆到总伤害。
4. **目标优先级**：召唤者/仪式怪 > 高输出攻击者 > 小怪；召唤者死后小怪消失。
5. **活雾战**：只打活雾本体；每回合只出 1 张技能；用 Bludgeon/攻击抢血。
6. **人工制品**：先破 artifact 再上易伤。
7. **药水**：精英/Boss 前主动用；白兽雕像（战斗必掉药水）可以大胆喝。
8. **操作**：每次出牌后重新读 `/state`，确认手牌索引、能量、目标；不凭记忆连打。

## 六、开局路线规划协议

每局 Neow 后、第一次选节点前：

1. 运行 `python tools/sts2.py route`，枚举当前可选节点到 Boss 的路径，按 HP 给营火/商店/宝箱与精英/小怪打分。
2. 选最优路径，把计划发到 overlay：`python tools/sts2.py say --thought "路线：opt0 ...；理由：..."`（只写内容，不加“大肥鱼：”）。
3. 按计划走；遇到以下情况重新跑 `route` 并简短说明：
   - HP < 35；
   - 单场掉血 > 20；
   - 计划外精英/双怪/未知战斗；
   - 商店/奖励让牌组明显变化（例如删不掉牌、防御牌没来）；
   - Boss 前 3 层没有营火。
4. 路线服从血量与关键资源：能绕精英就绕，能睡就睡；只有 HP 健康且需要遗物时才考虑精英。



## 七、当前数据库的局限

- Wiki 数据标记为 Early Access，部分描述缺少动态数值（如“造成点伤害”），以游戏内 `/state` 的 `resolved_rules_text` 为准。
- 事件/遗物/药水的部分中文描述来自列表页，可能不是完整数值版。
- 敌人招式的具体伤害数字在 Wiki 中是通用意图说明，实际数值以战斗状态 `intents[].damage/total_damage` 为准。
