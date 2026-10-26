# P0-3：执行安全

目标：把“模型写出危险动作”挡在工具层，而不是靠模型记得。P0-3 不改变战斗策略，只改变
`sim.validate` / `act` / `cmd_batch` 对非法计划的处理。

## 1. end 必须恰好一次、且在最后

`sim.validate` 遇到 `end` 时：

- 如果后面还有动作 → 返回 `False`，不截断前半手、不假装成功；
- 如果出现第二次 `end` → 同样整手拒绝；
- 只有最后一步是 `end` 才通过。

`act._execute` 在 `sim.validate` 失败时一张牌都不发，只落 `rejected_validate` 账本。
`cmd_batch` 直接收到原始动作时也会在动手前检查：

- `end_turn` 出现两次 → `BATCH_FIX`，退出码 2；
- `end_turn` 后面还有动作 → `BATCH_FIX`，退出码 2。

## 2. 防止 mid 后补 end / 跨调用双 end

`logs/.act_turn.json` 记录：

```json
{"turn": 5, "mid": true, "ended_turn": 4}
```

只有真正执行成功的 `act` 才更新这份记忆；被拒/`rejected_fx`/批处理报错不会
把上一手的 `mid` 清掉。

`act._stale_end` 在**纯 end** 时拒绝：

- 上一手是 `mid`，但当前 `turn` 已经变了：再 end 会空过新回合；
- 同一 `turn` 已经 `end_turn` 过一次：状态还没推进，重复 end 会重复结束；
- 上一手刚 end，当前是新回合且手里还有能打的牌：必须先用牌，不能纯 end 空过；
- `seq` 里出现多个 end：直接拒绝整手。

新回合确实没有可打牌（手牌空/全部不可打/能量不足）时，允许合法的纯 end。

## 3. 抽牌边界（P1-3）

`sim.validate` 遇到抽牌牌时只增加 `sim.draws`，不会把未知牌放进 `sim.hand`：

- 抽牌后写一张不在抽牌前手里的牌 → `抽牌边界` 错误，整手拒绝、一张不发；
- 抽牌后打抽牌前已有的牌/药水/end → 允许；
- 只打抽牌牌不写 end → `act` 执行后返回 `mid:1`、`next:read_ctx`、
  `mid_reason`，模型必须读新 `ctx` 再出下一手；
- 抽牌+end 的 `fx` 带 `draw_unknown`，此时只硬校验能量，`hp/take` 由下一回合
  `ctx` 复核。

完整协议见 [`DRAW_BOUNDARY.md`](DRAW_BOUNDARY.md)。

## 4. 敌人 current_hp 与原始下标

- 活敌人统一按 `current_hp`，缺失才回退 `hp`；`is_alive == false` 一律不算活。
- `_auto_target_index` 返回接口的**原始敌人下标**（`enemies[i].index`，缺失才回退列表位置），
  不再返回 `alive` 列表里的位置。
- `cmd_batch` 的 `TARGET_FIX` 用同一套原始下标；目标已死时改打当前血最低的活怪。
- 没有活怪时目标已失效的动作直接 STOP，不把刀交给尸体。

## 5. 测试

```powershell
python tools/test_execution_safety.py
python tools/test_draw_boundary.py
```

覆盖：

- end 后还有动作、重复 end 被拒，且不发动作；
- 战斗中双 end 被 `act` 整手拒绝；
- mid 后换回合纯 end 被拒；新回合有牌时纯 end 被拒；
- 抽牌后未知牌被拒、抽牌后已知牌放行、只打抽牌牌回 mid；
- 尸体不计入活怪，`_auto_target_index` 返回原始下标；
- `cmd_batch` 目标 0 已死时改打 original index 2；
- `cmd_batch` 重复 end / end 后接动作退出码 2。

## 6. 已知限制

- `_has_playable` 用 `sim` 的当下费用做保守判断，不建模未知抽牌/遗物触发；它只用于“新回合
  纯 end”这一条护栏，不参与战斗计划评分。
- `cmd_batch` 只在动作显式带 `target_index` 时做目标修复；`act` 的正常路径会先用 `sim`
  解析目标，因此两边共用原始下标约定。
- 这是离线行为验证，不等于已经完成实机 A10 验证；实机仍按 state 重读和日志对账。
