# sts2-ironclad-ironclad · 《杀戮尖塔 2》铁甲战士 A10 决策 OS

一套给 AI agent 用的《杀戮尖塔 2》（Slay the Spire 2）**铁甲战士 A10 实况决策 skill**：不是攻略文章，是一份可执行的「每回合检查清单 + 数值公式 + 反模式」。

- 版本：**v10**（2026-10-25）
- 适用：v0.107.1 主线（beta v0.111.0 同为参考），A10 双 Boss
- 配套：`sts2.py`（本地 mod HTTP API 客户端）、OBS 录屏 + 弹幕 overlay；说话系统见另一个 skill `sts2-caster`

## 这是什么

SKILL.md 是常驻上下文（约 16 KB），只放**每次决策都要看**的东西：

| 小节 | 内容 |
|---|---|
| §0 三条铁律 | 先算死 / 小伤害必挡（≤10% 最大生命一律挡满）/ 结束前三查 |
| §2 每回合 8 步 | 战前准入（血闸·多怪闸·机制闸）→ 读状态 → 量斩杀 → 定回合类型 → 排序 → 逐张重读 → 收尾 |
| §3 数值与斩杀 | 单卡伤害修正（易伤/虚弱/脆弱/格挡/胆小/荆棘）、多段、自伤、每费价值 |
| §4 血预算 / 药水 / SL | 血闸硬线、道中预算、**药水四问**、SL 只在必死 |
| §5–6 路线 / 构筑 | 路线打分与 REPLAN、牌组红线（张数·打击·防御·AOE·收尾·运转）、抓牌与删牌 |
| §7–8 敌人 / 精英与 Boss | 机制速查 + `T × N > 血量 × 0.6 不打` 的总账公式 |
| §9–10 工具坑 / 反模式 | 25 条「见到就停」 |
| §11 收尾 | 录像改名、report、复盘、回写 skill、加 strategy_rules |

细节放 `references/`，按需加载：

| 文件 | 用途 |
|---|---|
| `references/enemies.md` | 按幕排的三幕遭遇表（≥80 条），Boss 行必读 |
| `references/cards.md` | 铁甲战士 87 张卡的中文名 + 数值 + 抓位 |
| `references/potions.md` | 药水四问细则、何时交/何时留、`use_potion` 工具坑、实战反例 |
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
    └── potions.md
```

```bash
git clone https://github.com/lxllxl233/sts2-ironclad-Ironclad.git /tmp/sts2-ironclad-ironclad
mkdir -p <workspace>/.dsh/skills
cp -r /tmp/sts2-ironclad-ironclad <workspace>/.dsh/skills/sts2-ironclad-ironclad
```

SKILL.md 的 frontmatter（`name` / `description`）会被 agent 的技能目录读取；**不要改名**，否则 skill 会从目录里消失。

## 依赖

skill 里的命令默认项目目录里有一套工具（不在本仓库）：

- `sts2.py`：读 `/state`、发 `/action`、压缩出 `ANCHOR / EFF / BLOCK_POOL / INTENT_MEM` 等便于决策的行
- 游戏侧：`STS2AIAgent` mod 在 `127.0.0.1:8080` 提供结构化 JSON（不靠截图识别）
- `OBS`：录游戏画面 + 浏览器源叠字（可选）
- `db/sts2.db`：卡/敌/遗物/药水/规则库（201 条 `strategy_rules`）

没有这套工具也能读，但 `state`、`ANCHOR` 这类行需要自己实现等价物。

## 版本记录

### v10（2026-10-25）

- **铁律 2 重写**：入伤 ≤10% 最大生命（80 血 = ≤8）**一律先挡满**，宁可少打一张刀；要吃伤必须写清算式「省下的回合 × 敌方单回合入伤 > 这次掉的血」。起因：第 49 局泥浆纺锤意图 7，手里 2 张防御却打 Inflame + 打击×2 硬吃 7。
- **药水从「当前版本无效」改成必须规划**：四问（解什么 / 槽满还留不留 / 这回合会不会被打穿 / 留给下一场值不值）+ `references/potions.md`；新增反模式 ㉔㉕。
- 新增 `references/potions.md`；`SKILL.md` 压缩到 16 KB 以内，细节外置。

### v9（2026-10-25）

- A1 Boss 时序（瀑布巨兽 Buff→Stun→DeathBlow 44、仪式兽 150 晕 + Ringing 等）、Buff/Stun 回合不交防御资源、自伤联动（狱火 + 放血/突破）。

### v8 及更早

- 每回合 7 步 + 战前准入三闸、斩杀公式、路线 `route/plan` 对账、构筑红线。

## 已知限制

- 数值以游戏内实时文本为准（卡牌改版频繁：2026-04-16 / 06-18 两次大改，2025 年及更早的 StS1 攻略不适用）。
- A10 只到第 10 阶；A6 起删牌更贵。
- 药水在不同版本行为不同：`use_potion` 若返回 `pending`，按「重读 state → 确认 `is_queued` → 最多重试 1 次」处理，别连点。

## License

自用/实况用，未指定许可证；引用请注明来源。游戏内容版权归 Mega Crit 所有。
