# 决策知识库

模型做决策时要读的资料都放在这里，不再有 `.dsh` 技能目录，也不依赖「加载技能」这个动作。

```
knowledge/
├── ironclad/
│   ├── OS.md            铁甲战士 A10 决策手册（总纲：七步、三条铁律、血闸、路线、反模式）
│   ├── README.md        这份手册怎么用、怎么改（含 v22 版本说明）
│   └── references/      卡表、敌人、药水、构筑、教训（按需查，别整份塞上下文）
└── defect/OS.md         故障机器人（另一个角色的总纲）
```

## 谁在读它

| 读者 | 读什么 | 位置 |
| --- | --- | --- |
| 运行时模型（每回合） | `decision/prompts.py` 的 `SYSTEM_PROMPT` / `NONCOMBAT_PROMPT`（手册的精简版） | 常驻输入 |
| 运行时模型（查细节） | 本目录的 `OS.md` 与 `references/*.md` | 按需读取 |
| 硬闸 | `decision/hard_rules.py` 生成的 `rules` 字段（每份局面包都带） | 由 `ctx.py` 注入 |
| 本地兜底 | `decision/advisor.py` 与 `runner.py` 的启发式 | 模型不可用时 |

`decision/ctx.py` 构建局面包时**不读本目录**——手册被改、被压缩都不影响注入；
这条由 `tools/test_rules_in_ctx.py` 守着。

## 改规则

1. 阈值/机制改动 → 先改 `decision/hard_rules.py`（唯一来源），
   再同步 `OS.md` 的锚点和 `decision/prompts.py` 的精简版。
2. 跑 `python tools/check_skill.py`：查手册小节、阈值/关键词覆盖、反模式编号、
   硬闸同步、常驻提示词体积。
3. 跑 `python tools/test_docs_consistency.py`：查版本串与旧阈值。
4. 新增角色时建 `knowledge/<character>/OS.md`，并在 `game.json` 里登记角色名。

## 版本

`OS.md` 的 `v22` 是手册版本，与产品版本（`pyproject.toml`）无关；
两边都改时各自加一。
