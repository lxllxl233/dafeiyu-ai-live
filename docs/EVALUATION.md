# 评估与测试硬化（P3-1）

P3-1 把「看一局、凭感觉调」换成可重复的数字：

- `tools/eval_report.py`：从已有账本算胜率、楼层、掉血、被拒、药水、SL；
- `tools/test_all.py`：一个命令跑完全部 `tools/test_*.py`；
- `tools/test_eval_report.py`：锁死评估口径，防止把空样本算成 0%。

它仍然**只读本地日志**，不连游戏，不产生胜率承诺。

## 1. 数据源

| 文件 | 作用 |
|---|---|
| `logs/run_summaries.jsonl` | 每局终局 summary；优先数据源 |
| `logs/run_summary_*.json` | 没有 JSONL 时的回退；按 `run_id` 去重 |
| `logs/decisions.jsonl` | 每条战斗决策的 outcome / 重定向 / 药水 / 拦截 |

`run_summary` 由 `decision_log.record_run_summary` 在 `GAME_OVER` 时写。没有这个文件的旧局，
只能看动作流水，不能回溯出胜负；评估器不会猜。

## 2. 口径

- **胜率** = `wins / (wins + losses)`；`unknown` 不塞进分母。
- **A10** = `ascension == 10` 的终局样本，单独一列；没有样本就明确警告。
- **楼层**：summary 的最终 `floor`；胜局是通关楼层，败局是死亡楼层。
- **掉血 / 药水 / 被拒 / SL**：以 summary 的终局汇总为准。
  `potions_used` = **药水槽净消耗**（2026-10-28 起；之前的旧 summary 按 `seq` 步数计，会把
  `pending` 被吞的药水也算进去，run105 记 5 实为 2、run98 记 1 实为 0）。
  `rejected` 现在含 `rejected_guard` / `rejected_clear_overflow`（旧 summary 漏计）。
- **战斗执行**：来自 decisions：
  - `executed` / `rejected` / `reject_rate`（`receipt_lost` 算 rejected，不算 executed）
  - `draw_boundary_rejects`：`err` 含「抽牌边界」的被拒
  - `retargets`：目标重定向次数
  - `potion_uses`：`input.seq` 里带 `p` 的步骤——**尝试次数，不是消耗数**
    （`pending` 被吞也算；要真实消耗看 `summary.potions_used` 或 journal 的 `effect` 列）
  - `mid_decisions`：分两步执行的回合
- 没有任何 finalized summary 时输出 `NO_FINAL_SUMMARIES`，`win_rate` 为 `null`，不写 0%。

## 3. 命令

```powershell
# 人读的 Markdown
python tools/eval_report.py

# 机器读的 JSON
python tools/eval_report.py --json

# 最近 20 局，写报告
python tools/eval_report.py --last 20 --md-out logs/eval_latest.md

# 样本不足时 CI 失败（已知胜负 < 20）
python tools/eval_report.py --require-samples 20
```

`--last N` 会按 summary 时间取最近 N 局，并把 decisions 过滤到这些 `run_id`；
没有 summary 的局不会被算进胜率。

## 4. 测试

```powershell
# 全部测试
python tools/test_all.py

# 只看评估器
python tools/test_eval_report.py
```

`test_all.py` 逐个起独立子进程，任一失败返回 1；`--fail-fast`、`--verbose`、
`--pattern`、`--timeout` 可选。它排除自身，避免递归。

`test_eval_report.py` 覆盖：

- 胜/负/unknown 的分母；
- A10 与全样本分开；
- summary 重复 `run_id` 取最新；
- `--last` 过滤 decisions；
- 坏 JSONL 跳过；
- 无 summary、无 A10 的警告；
- `--require-samples` 退出码 2。

## 5. 样本纪律

- 少量样本只看方向，不宣称「A10 胜率提升」；至少 20 局同版本 A10 再谈比例。
- 评估器只能回答「账本里发生了什么」，不能证明「某张牌导致赢/输」。
- 改规则前后要同版本、同角色、同 Ascension 对比；跨版本样本分开看。
- 实机中断/重开可能没有 GAME_OVER，不会产生 summary；这类局不计入分母。

## 6. 限制

- 没有非战斗选择的独立结构化账本；P2-1 的 `choice.eval` 目前不落盘，
  后续需要时再加（P3-1 不伪造这块数据）。
- 旧 `logs/run_*.jsonl` 没有终局 summary 时，不能用于胜率。
- 不做逐场因果归因；`rejected` 低不等于决策好，只说明执行链稳。
