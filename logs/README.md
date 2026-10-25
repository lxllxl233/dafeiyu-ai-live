# logs/

运行时产物目录：

- `run_YYYYMMDD_HHMMSS.jsonl`：`sts2.py` 每次动作的原始日志（状态快照 + 动作 + 思路/吐槽）
- `run_YYYYMMDD_HHMMSS.md`：`python sts2.py report` 生成的 Markdown 战报
- `run<N>_review.md`：人工/agent 写的复盘
- `route_plan.json`：`route --commit` 存的路线快照
- `intent_memory.json`：敌人意图循环记忆（自动累积）

`.jsonl` 与 `run_*.md` 已进 `.gitignore`；想留公开复盘就把 `run<N>_review.md` 提交上来。
