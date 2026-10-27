# 全链路慢点审计（run92 · 2026-10-27）

> 目标：把「结算/回合之间卡很久」从**感觉**变成**数字**，修掉收益最高的公共根因，
> 剩下的写清楚为什么留着。所有数字来自 `logs/perf.jsonl`（`tools/perf_report.py` 聚合），
> 口径是「一次上层决策 → 它调用的全部子步骤」，没测的一律写 **未测**，不估。

## 1. 怎么测的

- `perf.py`（新）：`perf.trace()` 一次上层决策一条 trace，`perf.stage()` 记子阶段，
  `perf.count()` 记状态读/动作发送次数。一行 JSON 落 `logs/perf.jsonl`，
  **不写 stdout**（stdout 是给模型的 JSON）、**不落游戏状态快照**、默认开（`STS2_PERF=0` 关）。
- 每条 trace 区分：`ms`（端到端）、各 stage 的 `ms`（真实执行）、`sleep_ms`（主动等待）、
  `polls`（轮询次数）、`counters`（`state_read` 状态读次数、`action_post` 动作次数、
  `action_ms` 引擎侧动作耗时、`batch_step` 步数、`sleep_ms` 累计睡眠）。
- 聚合：`python tools/perf_report.py --n 40 --tail 3` → 中位数/p90/最长 + 阶段总耗时榜。
- 采样：run92 一局 13 个成功回合 trace + 若干 dry/失败 trace；样本量小，
  结论只对「数量级」负责（差 10 倍以上可信，差 10% 不可信）。

## 2. 慢点清单（改之前先搜全链路，不是只看 tools/act.py）

搜的关键词：`time.sleep` / `wait_*` / `get_state` / `subprocess` / `sim.search` / `search(` /
`Thread` / `sleep(`。命中 6 层：

| 层 | 位置 | 形态 |
|---|---|---|
| 模型层 | 每回合一次 tool call | 一次决策 60–95 s（不归本次范围） |
| 工具层 | `tools/act.py`：import → state → 验算/护栏 → run_batch → ctx → settle | 每步都有独立开销 |
| 批处理层 | `sts2.cmd_batch`：每步 `_player_turn_state()` + 前后各读一次 state | 固定轮询 150 ms/次 |
| 等待层 | `sts2.wait_new_turn_hand` / `wait_stable` / `wait_legal` / `wait_selection_settle` / `wait_stable_player_state` | 固定 sleep 0.12–0.4 s + 指纹判稳 |
| 结算层 | `sts2.cmd_rewards` / `tools/act.py::_auto_settle` / `cmd_do` 的重试分支 | 每步固定 sleep 0.2–0.4 s |
| 建议线 | `tools/ctx.py` → `sim.search(time_limit=0.8)`；`act.py::_candidate_plans(0.8)` | 每回合都付一次搜索预算 |
| 进程层 | 每次 `python`：启动 + import sts2/sim/ctx/decision_log | 340–560 ms |

## 3. 分类（8 类）与结论

| 类别 | 命中 | 证据 | 处理 |
|---|---|---|---|
| ① 单次调用慢 | `run_batch` 92,644 ms | 清场那一刀之后批量里剩下的 `end` 步还在等「玩家回合」 | **已修**（根因见 ④） |
| ② 重复调用 | `ctx.build_with_mem` 被 `_auto_settle` 又算一次 | settle 里 `ctx_ms` 单列 | 保留（各算一次，合并易错） |
| ③ 等待策略差 | 结算每步固定 `sleep 0.2–0.4`；每步出牌后固定 `sleep 0.15` | 一轮结算 4–6 步 → 1–2 s 纯睡眠，而引擎刷新只要 30–80 ms | **已修**：`_await_change` 指纹变更即走；`_player_turn_state` 前 0.6 s 用 50 ms 细轮询 |
| ④ 跨层重复等待 | `cmd_batch` 结束回合已等过稳定快照，`act.py::_execute` 又 `wait_new_turn_hand` 一次 | 同一回合出现两条 `wait_new_turn_hand` trace（3,104 ms + 742 ms） | **已修**：先读一眼，不是抽牌中途就用 |
| ⑤ 重复计算/读 | 批量每步 `before = get_state()`，丢掉等待函数刚拿到的快照 | 46 次状态读/回合 | **已修**：复用 `_player_turn_state()` 的返回值 |
| ⑥ 进程 & I/O 开销 | 每次 tool call 一个 python 进程，import 340–560 ms | `meta.import_ms` | **未修**（要常驻进程，另开任务） |
| ⑦ 环境固有延迟 | 引擎 `/action` POST 322–550 ms/次；敌人回合过场 1.5–4.0 s | `counters.action_ms`、`wait` 探针 | 不可控，记录 |
| ⑧ 异常卡顿 | `log_decision` 偶发 739 ms（死亡/大批量那次） | `ms.log_decision` | 未修，列入后续 |

### 根因 1（结算卡 90 秒）
清场后屏幕已经不是 COMBAT，但批量里剩余的步还在调 `_player_turn_state()`，
旧版对着非战斗屏幕**空转满 8 秒超时**，外层再重试 10 次 → 8×11 ≈ **92.6 秒**，
而真正的结算只有 1.1 秒。→ 非 COMBAT 立刻返回 `_BATTLE_OVER`，调用方直接跳过 `end`。
回归：R16d（REWARD/MAP/CARD_SELECTION 必须 <0.5 s 返回；COMBAT 无动作仍要超时返回 None）。

### 根因 2（结算每步白等）
`cmd_rewards` / `cmd_do` 每个动作后面挂 0.2–0.4 s 固定 sleep。实测引擎 30–80 ms 就刷新完了，
慢的时候 0.4 s 又不够。→ 改成 `_await_change()`：指纹一变立刻走，3 s 兜底。

### 根因 3（同一步等两次）
见 ④。一条回合里 `wait_new_turn_hand` 被调用两次（`cmd_batch` 收尾 + `_execute` 收尾）。

### 根因 4（建议线吃满预算）
`ctx` 里 `sim.search` 的 0.8 s 预算占了 ctx 包 0.76 s 中的 0.7 s，而它只产出「建议线」，
最终决策还是模型自己写。→ 默认 0.3 s（`STS2_CTX_SIM_S` 可调回 0.8）。

## 4. 验收表

| 场景 | 优化前耗时 | 优化后耗时 | 调用/轮询次数变化 | 正确性回归 |
|---|---|---|---|---|
| 自动结算（击杀回合端到端） | `run_batch` **92,644 ms**；execute 92,738 ms | `run_batch` **789 ms**（`execute` 785–919 ms） | 8 s 超时轮询 **11 次 → 0 次**；状态读 46 → 10 | R16d（4 个分支）✅ |
| 普通动作（5 步批量端到端） | 7,940 / 7,125 ms（2 个样本） | 中位 **4,668 ms**（n=13，p90 5,928，最快 919） | 每步 150 ms 固定轮询 → 前 0.6 s 50 ms；重复读 -1 次/步 | `test_regressions.py` ALL OK ✅ |
| 状态转换（end_turn → 新回合手牌） | 3,104 + 742 ms（等两次） | 1,400–3,300 ms（只等一次；余下是引擎过场） | `wait_new_turn_hand` **2 次 → 1 次** | R16d + 手牌就绪判定未放宽（防过渡快照）✅ |
| 奖励处理（一个 REWARD 界面收完） | total **1,888–2,488 ms**（loop_acts 1,605 / read_state 28–36 / wait_stable 253） | total **300 ms**（loop_acts 10 / read_state 47 / wait_stable 242） | 固定 sleep 4–6 次 → 指纹等待；动作数不变 | R16c + R16e（自动挑牌排序）✅ |
| SL 恢复（save_and_quit → continue_run） | 未测 | 未测 | — | run91 人工验证过等价恢复，但**这次没埋点测** |
| 建议线（`ctx` 包） | 764 ms（sim.search 0.8 s 预算） | <200 ms（0.3 s 预算，实测常 10–150 ms） | 搜索节点上限 120k → 60k | ctx 输出字段不变 ✅ |

> 小样本说明：普通动作只有 2 个「优化前」样本、13 个「优化后」样本，
> 中位数可信、p90 只能说量级；96 秒那个是单点实测（可复现：清场回合必现）。

## 5. 剩下的慢点（为什么留着）

1. **`wait_new_turn_hand` 均值 2.7 s**：不是空等。探针（`STS2_WAIT_DEBUG=1`）实录：
   t≈1.5 s 敌人攻击结算完 → t≈2.0 s 能量恢复 → t≈3.2 s 手牌才补齐 → t≈3.9 s `play_card` 才出现。
   再过 0.24 s 指纹稳两次才返回。想再快只能把「手牌 ≥4 且能量满」当就绪，
   但过渡快照正是 run30/run69 漏费漏甲的根因，收益 <0.4 s，**不划算，保留**。
2. **每次 tool call 一个 python 进程（import 340–560 ms）**：要修得做常驻 server，
   跨进程状态一致性风险高，另开任务。
3. **引擎侧 `/action` 322–550 ms/次**（每回合 4–5 次 = 1.6–2.7 s）：mod 内部等动画，不可控。
4. **`log_decision` 偶发 739 ms**：只在死亡/大批量那次出现，怀疑写账本时序列化了整份 state，
   未定位；不影响出牌（异常全部吞掉）。
5. **状态读 40±5 次/回合**：来自各等待循环的 poll。已按 50/120/150 ms 分层，
   要再降就得合并等待（风险同上）。

## 6. 复现命令

```powershell
python tools/perf_report.py                 # 中位数/p90/阶段榜
python tools/perf_report.py --tail 3        # 最近 3 条 trace 的阶段分解
$env:STS2_WAIT_DEBUG="1"; python tools/act.py --file tmp_plan.json   # 新回合等待逐帧探针
$env:STS2_PERF="0"                          # 关埋点（对比开销用）
```
