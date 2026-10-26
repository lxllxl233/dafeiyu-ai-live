# P1-1 战斗数学口径

本文只描述**离线模拟器**（`tools/sim.py`）与 `ctx`/`sts2` 共用的战斗数学契约，以及当前已验证/未验证的边界。它不是对实时 A10 胜率的承诺。

## 1. 入伤（incoming）

### 1.1 `total_damage` 是最终值

mod 的 `intent.total_damage` 已经包含：

- 敌人力量；
- 敌人虚弱；
- 玩家易伤（Vulnerable）；
- 多段攻击的段数。

因此：

```text
incoming += intent.total_damage          # 直接用，不再乘段数，也不再补易伤/虚弱
```

以前 `combat_incoming` 和 `Sim.incoming` 在 `total_damage` 上又乘了一次玩家易伤，这是 P1-1 修掉的 double-count。

### 1.2 只有缺字段才回落

```text
per_hit = intent.damage + enemy_strength
per_hit = per_hit × (enemy_weak ? 0.75 : 1)   # 向下取整
per_hit = per_hit × (player_vulnerable ? 1.5 : 1)  # 向下取整
incoming += per_hit × intent.hits
```

顺序固定为：**力量 → 敌人虚弱 → 玩家易伤**。每一步用 `int()` 截断，和游戏里的分段取整一致。

### 1.3 当前实现位置

- `sts2._intent_damage_parts()`：拆 `(final_total, raw_parts)`；
- `sts2.combat_incoming()`：`final_total` 原样加，`raw_parts` 才补虚弱/易伤；
- `Sim.incoming()`：同一口径；
- `ctx._intent()`：缺 `total_damage` 时才补虚弱/易伤，屏幕上的 `it.tot` 不会二次膨胀。

## 2. 格挡（block / max_block）

```text
card_block = (卡牌基础格挡 + 敏捷) × (Frail ? 0.75 : 1)
block      = 已有格挡 + Σ card_block + Plating + Metallicize
```

- **Frail 只砍卡牌格挡**；已有格挡、Plating、Metallicize 不砍。
- `sim.max_block(state)` 走同一套 `Sim.play`，所以 `ctx.calc.blk` 和 `BLOCK_POOL` 会同步看到 Frail。
- `sts2.hand_block_pool(hand, frail)` / `block_pool_note(...)` 也已接 Frail；默认参数 0 保持旧调用兼容。

## 3. 输出伤害

```text
per_hit = 卡牌伤害 + 本回合新增力量（Strength）
per_hit = per_hit × (敌人 Vulnerable ? 1.5 : 1)
总伤害  = Σ per_hit × hits - 敌人格挡
```

- **力量先加、易伤后乘**；不能先把卡牌伤害乘 1.5 再加力量。
- 药水伤害不叠本次新增力量；当前实现也不给药水伤害补易伤（保守，避免未验证的加成）。
- 敌人格挡、Intangible、Slippery、Thorns 仍由 `Sim.hurt_enemy` 处理。

## 4. 回合末状态税（status tax）

只统计**留在手里、未打出的状态牌**。当前 STS2 卡库：

| 状态牌 | 数值 | 类型 |
|---|---:|---|
| `BURN` 燃烧 | 2 | 可格挡伤害 |
| `INFECTION` 感染 | 3 | 可格挡伤害 |
| `DECAY` 衰败 | 2 | 可格挡伤害 |
| `TOXIC` 剧毒 | 5 | 可格挡伤害 |
| `DISINTEGRATION` 崩解 | 6 | 可格挡伤害 |
| `BECKON` 呼唤 | 6 | 无视格挡失去生命 |
| `BAD_LUCK` 厄运 | 13 | 无视格挡失去生命 |
| `REGRET` 悔恨 | 每张手牌 1 | 无视格挡失去生命 |
| `SLIMED` 黏液 | 0 | 1 费抽 1，已无回合末 3 伤 |
| `WOUND` 伤口 | 0 | 无效果 |
| `DAZED` / `VOID` | 0 | 当前不产生回合末 HP 税 |

未知状态牌按 **0** 处理，并在实现注释/文档标 `unknown`，不猜伤害。

结算：

```text
end_taken = max(0, incoming + tax_blockable - block)
taken     = end_taken + tax_unblockable + thorns_taken
hp_after  = hp - end_taken - tax_unblockable
```

`tax_unblockable` 不进 `end_taken`，所以不会被格挡吃掉；`taken` 是**这回合总账**，包含荆棘反伤，但 `hp_after` 不会二次扣荆棘。

## 5. 荆棘（Thorns）

- 荆棘是**每次攻击命中后**的反伤；当前按“有实际伤害的命中”触发。
- 反伤走 `hurt_player`，直接减 HP，并记入 `thorns_taken`。
- `end_turn().taken` 包含 `thorns_taken`；`hp_after` 用已经扣过荆棘的 `hp`，因此不会重复减。
- 荆棘不进入 `incoming`，因为它不是敌人意图攻击。

## 6. ctx 字段

```json
"calc": {
  "inc": 10,        // 敌人意图入伤（最终口径）
  "blk": 5,         // 本回合最大可叠格挡（含 Frail）
  "tax": 3,         // tax_dmg + tax_hp
  "tax_dmg": 3,     // 可格挡状态伤害
  "tax_hp": 0,      // 无视格挡失去生命
  "net": 8,         // max(0, inc + tax_dmg - blk) + tax_hp
  "full": false     // net == 0
}
```

- `net` 现在是**含状态税后的净掉血**，不再只是 `inc - blk`。
- `full` 为真表示攻击 + 可格挡状态伤害都被挡住，且没有无视格挡失血。
- `calc.tax` 为 0 时不写这三个税字段，保持旧 ctx 体积。

## 7. 已知边界 / 未验证项

- `total_damage` 的口径来自 mod；若 mod 某版本把它改成“单段伤害”，本口径会少算。代码注释已标死契约，升级 mod 后要重跑 `tools/test_combat_math_p1.py`。
- 状态表按 `tools/data/eng/cards.json` 当前卡库；新状态牌默认 0，不自动猜。
- `DISINTEGRATION` 的英文描述没有“if in hand”，当前仍按手牌状态牌处理；若实测是“回合末无论在哪都 6 伤”，需要单独修。
- 荆棘只在“有实际伤害的命中”触发；0 伤害命中、纯 debuff 牌不触发，未做逐张实战校验。
- 药水伤害不吃本次力量；是否吃易伤未做离线验证，当前保守不加。
- 本文所有数字来自离线单测，**没有**用实时 A10 对局验证胜率。

## 8. 回归测试

```powershell
$env:STS2_DECISION_LOG='0'
python tools/test_combat_math_p1.py
python tools/test_sim.py
python tools/test_block_pool.py
python tools/test_ctx_act.py
```

`tools/test_combat_math_p1.py` 覆盖：

1. `total_damage=20` + 玩家易伤 → 入伤仍是 20；
2. 缺 `total_damage` 的 `5×4` + 敌力量 2 + 敌虚弱 + 玩家易伤 → 28；
3. 力量先于易伤 → 每段 12、两段 24；
4. Frail 下 Defend 5 → `max_block=3`、10 入伤净掉 7；
5. 荆棘进 `taken`、`hp_after` 不二次扣；
6. 感染/燃烧/呼唤/悔恨/剧毒的状态税分档；
7. `ctx.calc` 的 `tax/net/full`。
