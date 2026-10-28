# tools/ 边界

`tools/` 不是核心代码，也不再是决策实现。这里只有三类东西：

1. **兼容 shim**：保留 v1 的命令/模块名，内部转发到新实现；
2. **运维脚本**：建库、审计、截图、重放、发布检查等一次性或人工工具；
3. **回归测试**：`tools/test_*.py`，用 `python tools/test_all.py` 跑。

## 兼容 shim 映射

| 旧入口 | 真实实现 |
|---|---|
| `tools/ctx.py` | `games/slay-the-spire-2/decision/ctx.py` |
| `tools/act.py` | `games/slay-the-spire-2/decision/act.py` |
| `tools/advisor.py` | `games/slay-the-spire-2/decision/advisor.py` |
| `tools/sim.py` | `games/slay-the-spire-2/decision/sim.py` |
| `tools/gates.py` | `games/slay-the-spire-2/decision/gates.py` |
| `tools/dps.py` | `games/slay-the-spire-2/decision/dps.py` |
| `tools/path.py` | `games/slay-the-spire-2/decision/path.py` |
| `tools/noncombat.py` | `games/slay-the-spire-2/decision/noncombat.py` |
| `tools/mech.py` | `games/slay-the-spire-2/decision/mech.py` |
| `tools/decision_log.py` | `games/slay-the-spire-2/decision/decision_log.py` |
| `tools/hard_rules.py` | `games/slay-the-spire-2/decision/hard_rules.py` |
| `tools/lesson_index.py` | `games/slay-the-spire-2/decision/lesson_index.py` |
| `tools/perf.py` | `games/slay-the-spire-2/decision/perf.py` |
| `tools/sts2.py` | `games/slay-the-spire-2/compat/sts2.py` |
| `tools/streamer.py` | `src/dafeiyu/ui/desktop.py`（旧服务入口） |
| `tools/obsctl.py` | `src/dafeiyu/recording/obs.py`（或 `main.py obs`） |

规则：

- **核心 `src/dafeiyu/` 不 import `tools/` 的 shim**；只有旧命令/旧 skill 可以。
- 新功能加到 `main.py`、`src/dafeiyu/` 或对应 `games/<id>/`，不新增 shim。
- shim 加载真实实现，不复制业务规则；改规则只改真实实现。
- 兼容环境变量 `<ALIAS>_*`（如 `STS2_*`）继续保留到 3.0 再评估，文档以
  `DAFEIYU_*` 为准。

## 退役条件

当前版本（2.2）**不删除 shim**，因为 `games/slay-the-spire-2/decision/knowledge`、`docs/` 和旧 runbook 仍引用
`python tools/ctx.py` / `tools/act.py`。退役必须同时满足：

1. 至少经过一个发布周期（2.2 → 2.3）；
2. `games/slay-the-spire-2/decision/knowledge/`、`docs/`、`games/*/game-skill/` 不再出现旧入口；
3. `tools/test_engine_migration.py` 的 shim 断言能直接删掉，而不是改成跳过；
4. `tools/release_check.py` 与 CI 全绿。

删除时按映射表逐个删，不要一次性删整个 `tools/`；运维脚本和回归测试还要留。

## 常用命令

```powershell
python tools/release_check.py --metadata-only  # 只查版本串
python tools/release_check.py                  # 发布默认门禁
python tools/release_check.py --full           # 加 v1 全量回归
python tools/release_check.py --ci             # CI 轻量组合

$env:PYTHONIOENCODING='utf-8'
python tools/test_all.py                       # 39 个工具回归
```

Windows 默认 GBK 控制台打印 `☰` 会 `UnicodeEncodeError`；这是终端编码问题，
设置 `PYTHONIOENCODING=utf-8` 即可，不是测试失败。
