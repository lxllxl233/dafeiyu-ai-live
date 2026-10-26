# 决策账本（P0-1）

一次 `tools/act.py` 战斗决策写一条 `logs/decisions.jsonl`；一局 GAME_OVER 写
`logs/run_summary_<run_id>.json` 和最新指针 `logs/run_summary.json`。

`run_summary` 含胜负、楼层、HP/金币/牌组、遗物/药水、决策回合数、被拒数、
掉血总量与按楼层、药水使用数、SL 次数；这些是从账本/会话流水汇总，不是估算。

账本只记录，不参与决策、不发请求、不改游戏状态。写盘失败会被 `act.py` 吞掉，
不会影响出牌。

## 一条记录

| 字段 | 内容 |
|---|---|
| `run_id` / `act_id` / `act` / `floor` / `turn` | 身份与位置 |
| `state_hash` | 手牌/敌人/能量/血量摘要，用于对齐同一决策 |
| `input.seq` / `input.say` / `input.fx` | 模型原始输入 |
| `steps` | `sim.validate` 解析后的实际步骤 |
| `plans` | 决策时 `sim.search` 的候选（五类各一，最多 5 条；`risk` 表示该线 hp<=0） |
| `chosen_plan` | 若选中线与候选重合，记录其 `k`/`score` |
| `forecast` | 本地账本的回合末能量/血量/掉血；抽牌+end 时带 `draw_unknown`，血量/掉血为近似值 |
| `before` / `after` | 紧凑局面摘要（HP/能量/手牌/敌人/牌组张数） |
| `batch` | `cmd_batch` 的尾部日志与错误 |
| `state` | 可重放的战斗 state（`run`/`combat`/`piles`） |
| `outcome` | `executed` / `rejected_validate` / `rejected_fx` / `batch_error` / `stale_end` / `missing_seq` / `not_combat` / `no_actions` |

## 关闭/裁剪

```powershell
$env:STS2_DECISION_LOG = '0'      # 完全不写
$env:STS2_DECISION_STATE = '0'    # 不写 state 快照，只留摘要
$env:STS2_DECISION_LOG_DIR = 'D:\tmp\decisions'  # 改目录（测试用）
```

## 重放

```powershell
python tools/replay.py --last 10
python tools/replay.py --run-id 20261027_012345 --last 20
python tools/replay.py --last 10 --search   # 额外重算候选，核对 chosen_plan/score
```

重放只读账本，用记录里的 `state + input.seq` 重新跑 `sim.validate`，
核对 `steps` / `forecast`，不连游戏。历史 `run_*.jsonl` 没有完整 state，
不能凭空重放；新账本从 P0-1 之后开始积累可重放样本。

## 评估

```powershell
python tools/eval_report.py                    # 人读 Markdown
python tools/eval_report.py --json             # 机器读 JSON
python tools/eval_report.py --last 20
python tools/eval_report.py --require-samples 20
```

口径、样本纪律与限制见 [`EVALUATION.md`](EVALUATION.md)。没有 `run_summary` 时
输出 `NO_FINAL_SUMMARIES`，不把空样本算成 0% 胜率。

## 测试

```powershell
python tools/test_all.py
python tools/test_decision_log.py
```

覆盖：成功、非法牌、fx 不符、batch 失败、GAME_OVER 胜负、账本自身异常、
坏 JSONL、关闭账本、关闭 state、report 坏行与 summary 段。

## 与旧日志的分工

- `logs/run_*.jsonl`（`sts2.log`）仍是动作流水，追加了 `run_id/act_id/turn`；
- `logs/decisions.jsonl` 是决策时快照，用于复盘“当时为什么选这条”；
- `run_summary_*.json` 是终局汇总，用于评估胜率/掉血/药水/SL；
- 不改 `route_plan.json`、不改技能、不改出牌决策。
