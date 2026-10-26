# sts2-ironclad-ironclad · 《杀戮尖塔 2》铁甲战士 A10 决策 OS

一套给 AI agent 用的《杀戮尖塔 2》（Slay the Spire 2）**铁甲战士 A10 实况决策 skill**：不是攻略文章，是一份可执行的「结构化局面 → 结构化牌序 + 数值公式 + 反模式」。

- 版本：**v20**（2026-10-26）
- 适用：v0.107.1 主线（beta v0.111.0 同为参考），A10 双 Boss
- 配套：`sts2.py`（本地 mod HTTP API 客户端）、`tools/sim.py`（单回合模拟器）、`tools/ctx.py`（局面包）、`tools/act.py`（校验并执行牌序）、OBS 录屏 + 弹幕 overlay；说话系统见另一个 skill `sts2-caster`

## v20 修了什么：把 v19 的「降智」补回来

v19 把输入输出换成 JSON，但层面太薄：看不到抽/弃/消耗堆、变费与回能、药水没有进牌序、意图历史几乎不更新，`plans` 只会在「当前手牌」里打转。v20 的做法是：

```powershell
python tools/ctx.py                       # ① 局面包：所有事实 + 本地账本
# ② 模型输出 say + seq（牌和药水混排）
python tools/act.py --file tmp_plan.json  # ③ 逐步验算后执行，回下一回合 ctx
```

```json
{"say":["它 16，痛击加一刀刚好带走。","另一只吃尸体，这回合没人打我。"],
 "seq":[{"c":"BLOODLETTING"},
        {"c":"BASH","t":0},
        {"c":"STRIKE_IRONCLAD","t":0},
        {"p":1,"t":0},
        {"end":1}],
 "fx":{"e":1,"hp":59}}
```

- **输入补齐**：手牌实费与效果、抽/弃/消耗堆、遗物、药水（槽位/效果/目标/可用）、双方 buff、每怪当前意图 + 下一次意图 + 机制、可上的 debuff、可变的费、可抽的牌、能量、斩杀线、击杀连锁（杀一只→谁晕）。
- **输出补齐**：`seq` 是「牌 + 药水」的顺序；本地模拟器逐步算能量/变费/抽牌/消耗/加牌/击杀，顺序不成立就在那一步打回，一张都不打。
- **药水进牌序**：补能量在攻击前，斩杀药水补刀，格挡/回血在 `end` 前；targeted 必须写 `t`。
- **变费能算**：`UNRELENTING` 后下一张攻击 0 费、`STOMP` 每打一张攻击 -1 费、`CORRUPTION` 技能 0 费，`plans` 会真的按变费后的费用排。
- **意图循环真的会更新**：按「层 + 敌人 id + 下标」记历史，周期检测推 `nx`；同一场两只同名怪不会互相污染。
- **击杀连锁/召唤者**：`calc.chain` 给出「杀谁→谁晕→省多少」；`爪牙` 主人死则撤；`饥饿` 杀同伴才晕。
- **少请求**：杂怪 `{"auto":{"turns":3,"stop_hp":40}}` 本地连打；精英/Boss 才逐回合。

## 这是什么

SKILL.md 是常驻上下文，只放**每次决策都要看**的东西：

| 小节 | 内容 |
|---|---|
| §0 传输协议 | `ctx` → `seq` → `act`；`fx` 自报账；`auto` 连打；`ok:false` 怎么回退 |
| §0.5 局面包字段 | me/relics/potions/hand/piles/enemies/calc/can/plans/warn/choice |
| §0.6 输出顺序 | 0 费/回能/变费 → 易伤/力量 → 高伤 → 药水 → end；抽牌/烧牌/加牌的位置 |
| §0.7 每回合七步 | 读 warn → 意图/机制 → calc → 回合类型 → 选线 → 写 seq → 收尾三查 |
| §0.8 记忆分层 | L1 `memory` / L2 `ctx` / L3 `journal`，换幕开新会话 |
| §1 三条铁律 | 先算死 / 小伤害必挡（≤8 挡满、9–14 吃满）/ 结束前三查 |
| §2 准入闸 | 血闸 50/60/70、多怪闸、换血闸 |
| §3 数值与斩杀 | 易伤/虚弱/脆弱/格挡/胆小/荆棘、多段、自伤、每费价值 |
| §4 多怪 / 机制 | 目标优先级、杀→晕、爪牙、眩晕窗口、聚焦 vs 分散、两回合账 |
| §5 血预算 / 药水 / SL | 道中预算、药水四问、Boss 不省药、SL 流程 |
| §6–7 路线 / 构筑 | REPLAN、牌组红线（张数·打击·防御·AOE·收尾·运转） |
| §8 精英 / Boss | `T × N > 血量×0.6 不打`、Act1/Act2 Boss 账、Boss 前检查 |
| §9–10 工具坑 / 反模式 | 协议/数学/防守/构筑/操作五类「见到就停」 |
| §11–12 收尾 / 本季教训 | 录像改名、report、复盘、回写 skill |

细节放 `references/`，按需加载（带 offset/limit，别整份读）：

| 文件 | 用途 |
|---|---|
| `references/enemies.md` | 按幕排的三幕遭遇表（≥80 条），Boss 行必读 |
| `references/cards.md` | 铁甲战士 87 张卡的中文名 + 数值 + 抓位 |
| `references/potions.md` | 药水四问细则、何时交/何时留、`use_potion` 工具坑 |
| `references/archetypes.md` | 攻杀/防杀清单、循环解（剑耸） |
| `references/builds_tier.md` | 流派强度（S 消耗 > A 力量 ≈ A 格挡 > B 易伤/自伤） |
| `references/playbook.md` | 路线、精英、Boss、收尾的总账 |

## 安装

把整个目录放到项目（或任意工作区）的 skills 目录下即可：

```text
<workspace>/.dsh/skills/sts2-ironclad-ironclad/
├── SKILL.md
├── README.md
└── references/
    ├── archetypes.md
    ├── builds_tier.md
    ├── cards.md
    ├── enemies.md
    ├── playbook.md
    └── potions.md
```

SKILL.md 的 frontmatter（`name` / `description`）会被 agent 的技能目录读取；**不要改名**，否则 skill 会从目录里消失。

## 依赖

skill 里的命令默认项目目录里有一套工具：

- `tools/sim.py`：单回合模拟器（实费/变费/回能/抽牌/消耗/加牌/药水/多怪/击杀连锁）
- `tools/ctx.py`：把 `/state` 加工成局面包 JSON（含牌堆、意图历史、候选计划）
- `tools/act.py`：逐步校验 `seq`，执行后回下一回合 ctx；`ok:false` 不盲打
- `sts2.py`：读 `/state`、发 `/action`；`batch` 是 `act.py` 的底层执行器
- 游戏侧：`STS2AIAgent` mod 在 `127.0.0.1:8080` 提供结构化 JSON（不靠截图识别）
- `OBS`：录游戏画面 + 浏览器源叠字（可选）
- `db/sts2.db`：卡/敌/遗物/药水/规则库（ctx 的机制/意图预判来源）

没有这套工具也能读，但 `ctx.py` / `act.py` 需要自己实现等价物。

## 版本记录

### v20（2026-10-26）

- **输入补齐**：`hand` 合并同名牌但保留每张的实费/全部效果；新增 `piles`（抽/弃/消耗堆）；`enemies` 增加 `it/nx/hist/mech`；`calc` 增加 `lethal/chain/aoe/tax`；`can` 从计数改成带 id 的清单。
- **输出补齐**：`seq` 支持牌+药水混排；`fx` 可自报回合末能量/血量/净掉血，错了解释器打回。
- **模拟器**：`tools/sim.py` 逐步算能量、变费、回能、抽牌、消耗、加牌、药水、多怪、击杀连锁；`plans` 不再只按当前手牌。
- **意图循环**：按「层 + 敌人 id + 下标」记历史，周期检测推 `nx`；修复 v19 只在 `compact()` 里更新意图记忆的问题。
- **药水进顺序**：药水位置（补费/补刀/保命）写进 skill，`plans.potion` 只作候选，不默认交。
- `tools/check_skill.py` 换成 v20 断言（ctx/act/seq/say/fx/auto + 阈值/反模式编号）。

### v19（2026-10-26）

- 输入输出结构化：`tools/ctx.py` 局面包 + `tools/act.py` 执行 `plan+say`。
- 杀→晕连锁、意图下一手、`auto` 连打。

### v18（2026-10-26）

- **一回合一条 `batch`**：把每刀一条命令改成整回合一次调用，batch 尾部直接给下一回合快照。起因：审计 14 个会话（8,334 次请求 / 31.4 亿 token）发现 **99.6% 的输入是缓存重读**，请求数就是账单。
- `sts2.py batch` 同步硬化：过渡快照重读、超费动手前拒、目标失效重定向、战斗结束自动跳过 `end_turn`。

### v10（2026-10-25）

- 铁律 2 重写（≤10% 最大生命一律挡满）、药水四问、`references/potions.md`。

## 已知限制

- 数值以游戏内实时文本为准（卡牌改版频繁：2026-04-16 / 06-18 两次大改，2025 年及更早的 StS1 攻略不适用）。
- A10 只到第 10 阶；A6 起删牌更贵。
- `ctx.py` 的 `nx` 依赖意图历史；第一次遇到的怪只能给当前意图，按 `mech` 与 `references/enemies.md` 的最坏情况留甲。
- `plans` 不假设抽牌收益（抽到什么是随机的）；靠抽牌才能成立的线要模型自己用 `mid` 分两步打。
- `auto` 只管普通杂怪，不替模型交药；精英/Boss/残血/需要抽牌才知道下一步的回合必须逐回合。
- 药水在不同版本行为不同：`use_potion` 若返回 `pending`，按「重读 state → 确认 `is_queued` → 最多重试 1 次」处理，别连点。

## License

自用/实况用，未指定许可证；引用请注明来源。游戏内容版权归 Mega Crit 所有。
