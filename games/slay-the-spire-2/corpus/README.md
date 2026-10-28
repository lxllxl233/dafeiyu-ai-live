# 游戏语料库 · 杀戮尖塔 2

这一层是「这个游戏的知识」：卡牌、敌人、遗物、药水、事件、机制、梗、策略规则。
性格层和决策层都从这里取事实，不各自抄一份。

## 目录

```
corpus/
├── db/
│   ├── sts2.db          # SQLite：结构化资料 + 策略规则 + 梗（1.9 MB）
│   ├── memes.json       # 梗库（和 db 的 memes 表同源）
│   ├── strategy.json    # 策略规则（和 db 的 strategy_rules 表同源）
│   ├── mech.json        # 机制笔记（敌人被动/关键词）
│   ├── act1_enemies.txt / enemy_moves.txt / checks.txt / overview.txt
│   └── raw/             # 原始抓取数据（cards/enemies/relics/potions/events…）
└── data/eng/            # mod 自带英文数据（卡面/遗物/怪物/意图…，20 个 JSON）
```

## 数据表（`db/sts2.db`）

| 表 | 行数 | 用途 |
|---|---|---|
| `cards` | 626 | 卡牌（中英文名/费用/类型/效果） |
| `enemies` | 127 | 敌人（HP/被动/招式） |
| `relics` | 311 | 遗物 |
| `potions` | 65 | 药水 |
| `events` | 56 | 事件 |
| `powers` | 321 | 能力/关键词说明 |
| `keywords` | 8 | 关键词 |
| `mechanics` | 16 | 机制笔记 |
| `memes` | 118 | 梗（分类/强度/触发场景） |
| `strategy_rules` | 226 | 可查询策略规则（战斗/商店/药水/篝火/Neow/Act1-3） |

查询 CLI：

```powershell
python tools/gamedb.py card 重锤
python tools/gamedb.py enemy 活雾
python tools/gamedb.py bosses
python tools/gamedb.py mechanics
python tools/memedb.py suggest --situation boss --max-intensity 2
```

## 重建

```powershell
python tools/build_db.py            # 从 db/raw 重建 sts2.db（会清表）
python tools/build_memes.py         # 重写 memes（JSON + DB）
python tools/build_strategy.py      # 重写 strategy_rules
python tools/build_cards_v3.py      # 生成技能卡表 references/cards.md
python tools/build_enemies_v3.py    # 生成技能敌人表 references/enemies.md
```

注意：重建顺序是 `build_db → build_memes → build_strategy`；`build_db` 会重建库，
后两个必须在它之后跑，否则梗/规则表为空。

## 和技能/文档的关系

- 玩法手册：`docs/A10_IRONCLAD.md`、`docs/COMBAT_TURN.md`、`docs/ACT1_PLAYBOOK.md`、
  `docs/NEOW_DECISIONS.md`、`docs/SHOP_DECISIONS.md`、`docs/CAMPFIRE_UPGRADES.md`
- 复盘与教训：`docs/LESSONS.md`（按局）、`docs/RUN_REVIEW.md`（模板）
- Agent 技能：`.dsh/skills/sts2-ironclad-ironclad`（决策）、`.dsh/skills/sts2-caster`（说话）
- 台词素材：`docs/IRONCLAD_VOICE.md`

## 来源与边界

牌/遗物/怪物/事件数据来自公开 Wiki（`slaythespire2.gg/zh`，未调用其 API）与 mod 自带英文数据；
策略规则、教训、梗是本地实战记录。二进制数据不入 git 历史之外的发布物，避免再分发风险。
