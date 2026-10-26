# P0-2：BRPS 路线生存评估

BRPS = Boss-Ready Path Survival。目标不是“这条路线资源多”，而是回答：

1. 按当前牌组/血量走完这条线，到 Boss 前还剩多少血？
2. 打 Boss 需要几回合（TTK），这段血能活几回合？
3. 哪些精英/危险节点是单线或全路径必经，绕不开？
4. 这个计划属于哪一局、哪一幕、哪张地图？换局后还能不能沿用？

## 文件

- `tools/path.py`：纯离线模型（不调用游戏、不 import `sts2`）。
- `tools/test_path_survival.py`：离线测试。
- `logs/route_plan.json`：提交的计划 + 预测路径 + `actual` 实际路径。
- `logs/route_actual.jsonl`：每进入一个地图节点追加一行实际记录（含预测 HP 和 delta）。

## 投影规则

- 普通怪：按当前牌组 DPS 算 TTK，但最多按 3 回合计伤害，避免高血量多怪遭遇把每条路都算死。
- 精英：最多按 6 回合计伤害；TTK 仍记录真实估计。
- 营火：当前 HP 低于上限时回复 30% 最大生命；满血不回复。
- 未知：按半场普通战计，避免把问号路当免费回血。
- Boss：不把 Boss 伤害重复计入路径；单独做 `boss_gate`。
- 每回合入伤 = `max(0, 敌方平均入伤 - 当前每回合格挡)`。
- 牌组信息来自 `run.deck` + `CARD_DB`，不是手牌；没有战斗内 buff。

## Boss 击杀窗口

`boss_gate` 对当前幕的每个 Boss 选项分别计算：

```text
TTK       = ceil(Boss HP / deck.dps)
可活回合   = floor((到 Boss 时的 HP + 药水格挡估算) / 每回合净入伤)
窗口       = 可活回合 - TTK
```

- `ok` 要求当前幕所有 Boss 选项都能过；不把“最好打的 Boss”当默认。
- 药水只按每瓶 8 点格挡估算，不假设具体药水。
- `expected` 是各 Boss 的平均 TTK；`worst` 是最小窗口。
- 数据缺失时用 `GENERIC` 并标 `assumed`，不会假装知道。

## 强制节点

- `single_child_chain`：路径中精英的前驱只有唯一子节点，进入前驱后精英必打。
- `dominates_all_paths`：所有候选路径都经过同一个精英，也是必打。
- `forced_elite` 只标记精英，不把每局必打的 Boss 重复当风险。
- 标记是提示，不直接拒绝路线；P0-2 先警告，等实际账本校准后再考虑硬闸。

## 身份与旧计划

计划顶层新增：

```json
"identity": {"run_id": "...", "act_id": 0, "map_sig": "..."}
```

- `run_id` 变化 → `PLAN_STALE`
- `act_id` 变化 → `PLAN_STALE`
- 地图结构变化 → `PLAN_STALE`
- 旧计划没有 identity → `PLAN_STALE`，绝不输出 `PLAN_OK`

## 预测 vs 实际

`route --commit` 保存选中路径的 `predicted.steps`（每步 `hp_out` 等）。
进入节点时：

- 先按动作前 `AVAILABLE[option_index]` 定位所选节点（动作后地图可能不再有 `is_current`）；
- 把实际进入节点时的 HP 写进 `route_plan.json` 的 `actual`，与预测 `hp_in` 对齐；同时保留 `pred_hp_out` 供下一步核对；
- 同时追加 `route_actual.jsonl`；
- 实际符号与原 `seq` 不一致时输出 `ROUTE_DEVIATED`。

如果当前 run/act/map 与计划 identity 不匹配，只写 `route_actual.jsonl`，不污染旧计划的 `actual`，并打印 `ROUTE_PLAN_STALE`。

## 已知限制

- 普通/精英遭遇是当前幕的平均估计，不是本节点实际遭遇；`assumed` 会标出缺失数据。
- 不模拟牌组成长、遗物触发、Boss 阶段变化、具体药水效果；因此 Boss 窗口是保守提示，不是保证。
- 幕 Boss 选项按 `act_id` 0-based 映射；旧 fixture 只有 `act` 时按 1-based 处理。映射不确定时会回退并用 `assumed`/通用数据。
- 历史 `logs/run_*.jsonl` 没有完整地图/预测快照，不能回溯生成 P0-2 的预测对比；从本功能启用后的新计划开始积累。
- 只有 `route --commit` 之后才有可对账的 `predicted`；没有计划时只记录实际路径。

## 测试

```powershell
python tools/test_path_survival.py
python tools/test_route_plan.py
```

测试覆盖：

- 64 HP 与 37 HP 的同图路线排序不同；
- 单线精英被标 `forced_elite`；
- 弱牌组 Boss gate 失败、强牌组通过；
- 新 run / 新 act / 旧计划不输出 `PLAN_OK`；
- `route_actual.jsonl` 写入实际节点并关联预测 HP。
