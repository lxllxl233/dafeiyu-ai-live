# sts2-ironclad-ironclad · 《杀戮尖塔 2》铁甲战士 A10 决策 OS

一套给 AI agent 用的《杀戮尖塔 2》（Slay the Spire 2）**铁甲战士 A10 实况决策 skill**：不是攻略文章，是一份可执行的「一回合一条命令 + 数值公式 + 反模式」。

- 版本：**v18**（2026-10-26）
- 适用：v0.107.1 主线（beta v0.111.0 同为参考），A10 双 Boss
- 配套：`sts2.py`（本地 mod HTTP API 客户端）、OBS 录屏 + 弹幕 overlay；说话系统见另一个 skill `sts2-caster`

## v18 改了什么（只改传输，不改决策）

旧协议是「每刀一条 `play` + 每回合一条 `state`」——一回合 5–7 次模型请求，而每次请求都要把整段上下文重读一遍（本仓库实测：**平均 37.6 万 token/请求**，占计费 99%）。

v18 把一回合压成一条 `batch`：

```powershell
python sts2.py batch '{"actions":[
  {"action":"play_card","card_id":"BASH","target_index":0,"thought":"…","meme":"…"},
  {"action":"play_card","card_id":"STRIKE_IRONCLAD","target_index":0},
  {"action":"end_turn","thought":"…","meme":"…"}]}'
```

- 末尾直接给**下一回合完整快照**，下一回合不用再 `state`。
- 安全留在工具里：`card_id` 逐步重解析、撞过渡快照自动重读、缺牌 `BATCH STOP`、超费 `PLAN_OVER_BUDGET`、目标已死自动改打活敌、最后一刀打完自动跳过 `end_turn`。
- 实测同一回合（3 刀）：**请求 5 → 1，回显 2,279 → 610 字符**。

SKILL.md 本体：13,006 → 11,632 字节（~4.0k → ~3.6k token），规则一条没删——`tools/check_skill.py` 现在会逐条断言阈值/关键词/反模式编号还在。

## 这是什么

SKILL.md 是常驻上下文，只放**每次决策都要看**的东西：

| 小节 | 内容 |
|---|---|
| §0 传输协议 | 一回合一条 batch、什么时候退回单张、STOP 之后怎么办 |
| §0.5 记忆分层 | L1 `memory` / L2 快照 / L3 `journal`，换幕开新会话 |
| §1 三条铁律 | 先算死 / 小伤害必挡（≤8 挡满、9–14 吃满 BLOCK_POOL）/ 结束前三查 |
| §2 准入闸 | 血闸 50/60/70、多怪闸、换血闸 |
| §3 每回合七步 | 状态 → 机制 → 斩杀 → 回合类型 → 排序 → 一条 batch → 收尾 |
| §4 数值与斩杀 | 易伤/虚弱/脆弱/格挡/胆小/荆棘、多段、自伤、每费价值 |
| §5 血预算 / 药水 / SL | 两回合账、道中预算、药水四问、**Boss 战不省药** |
| §6–7 路线 / 构筑 | REPLAN 触发、牌组红线（张数·打击·防御·AOE·收尾·运转） |
| §8–9 敌人 / 精英与 Boss | 机制速查 + `T × N > 血量×0.6 不打` 的总账公式 |
| §10–11 工具坑 / 反模式 | batch 之后只剩的坑 + 25 条「见到就停」 |
| §12–13 收尾 / 本季教训 | 录像改名、report、复盘、回写 skill |

细节放 `references/`，按需加载（带 offset/limit，别整份读）：

| 文件 | 用途 |
|---|---|
| `references/enemies.md` | 按幕排的三幕遭遇表（≥80 条），Boss 行必读 |
| `references/cards.md` | 铁甲战士 87 张卡的中文名 + 数值 + 抓位 |
| `references/potions.md` | 药水四问细则、何时交/何时留、`use_potion` 工具坑 |
| `references/archetypes.md` | 攻杀/防杀清单、循环解（剑耸） |
| `references/builds_tier.md` | 流派强度（S 消耗 > A 力量 ≈ A 格挡 > B 易伤/自伤） |

## 安装

把整个目录放到项目（或任意工作区）的 skills 目录下即可：

```text
<workspace>/.dsh/skills/sts2-ironclad-ironclad/
├── SKILL.md
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

skill 里的命令默认项目目录里有一套工具（不在本仓库）：

- `sts2.py`：读 `/state`、发 `/action`、压缩出 `ANCHOR / EFF / BLOCK_POOL / INTENT_MEM` 等便于决策的行；`batch` 负责一回合一条
- 游戏侧：`STS2AIAgent` mod 在 `127.0.0.1:8080` 提供结构化 JSON（不靠截图识别）
- `OBS`：录游戏画面 + 浏览器源叠字（可选）
- `db/sts2.db`：卡/敌/遗物/药水/规则库

没有这套工具也能读，但 `batch`、`ANCHOR` 这类行需要自己实现等价物。

## 版本记录

### v18（2026-10-26）

- **一回合一条 `batch`**：把每刀一条命令改成整回合一次调用，batch 尾部直接给下一回合快照。起因：审计 14 个会话（8,334 次请求 / 31.4 亿 token）发现 **99.6% 的输入是缓存重读**，请求数就是账单。
- `sts2.py batch` 同步硬化：过渡快照重读、超费动手前拒、目标失效重定向、战斗结束自动跳过 `end_turn`、`end_turn` 后等新回合手牌再打快照。
- `tools/check_skill.py` 加规则覆盖断言（阈值/关键词/反模式编号/协议标记），压缩不再靠「感觉还在」。
- 新增 `tools/audit_tokens.py`（读 DSH session 日志算真实账）与 `tools/compare_turn_protocol.py`（新旧协议回显对比）。

### v10（2026-10-25）

- 铁律 2 重写（≤10% 最大生命一律挡满）、药水四问、`references/potions.md`。

### v9 及更早

- A1 Boss 时序、Buff/Stun 回合不交防御资源、自伤联动；每回合七步 + 战前准入三闸、斩杀公式、路线对账、构筑红线。

## 已知限制

- 数值以游戏内实时文本为准（卡牌改版频繁：2026-04-16 / 06-18 两次大改，2025 年及更早的 StS1 攻略不适用）。
- A10 只到第 10 阶；A6 起删牌更贵。
- `batch` 不适合「要靠抽牌才知道下一步」的回合——那种情况退回单张 `play`，多花 2–3 次请求买到准确性。
- 药水在不同版本行为不同：`use_potion` 若返回 `pending`，按「重读 state → 确认 `is_queued` → 最多重试 1 次」处理，别连点。

## License

自用/实况用，未指定许可证；引用请注明来源。游戏内容版权归 Mega Crit 所有。
