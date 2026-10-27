# run86 前置阶段：修复 + 回归报告

> **这不是 run86。** 这一阶段只做两件事：按 `docs/RUN85_VERIFICATION.md` 的 P0/P1 逐项定位根因并修实现，
> 再把 run85 的真实翻车局面固化成回归用例。**没有开局、没有真实验证**，所以本文件里任何一个百分比
> 都不能当作「修好了」的证据 —— 它只能说明「离线能复现的错已经不会错第二遍」。

- 代码基线：`main` @ `7f6aed3`（run85 报告）+ 本阶段改动（未提交时统计，提交见文末）
- 环境：Windows / `python -X utf8` / 引擎同一版本（`--dry` 可用，游戏当前 `GAME_OVER`）
- run85 账本：`run_id = FNVZ5NS2U5`，72 条（executed 61）

---

## 0. 一句话结论

| 项 | 结论 |
|---|---|
| P0/P1 是否全部修完 | **P1–P8 已改实现 + 已加回归**；P9 只加了策略提示（它不是 bug） |
| 未建模机制是否明确标记 | **已标记**：ctx 输出 `unmodeled`，forecast 输出 `approx`，账本留痕（§1.10） |
| 是否靠放宽护栏过关 | **没有**。没有新增任何 `allow_*`、没有下调阈值、没有删断言（唯一改过的旧测试是 `test_decision_log.py` 的 fixture，见 §3.2） |
| 全量测试 | **27/27 测试文件 ALL PASS**，`preflight` PASS（新增 `test_free_attack.py` 已进门禁） |
| 历史重放 | 全库 135 条可重放：**114 → 115 完全吻合**，0 回退；run85 子集 12 条：**9 → 10** |
| 能不能开 run86 | **可以开，但只能当"测量局"**：5 条验收口径全部尚未达标、本文也不声称达标，理由见 §5 |

---

## 1. 修复清单（逐项：根因 → 改动 → 回归）

### P1 免费攻击把能量模型打穿（batch 中断 4/61）

- **根因（数据实证）**：`FREE_ATTACK` 的 DB 描述是 *"Your next Attack costs 0."*，但 buff 生效期间引擎把
  **手牌里所有攻击牌的 `energy_cost` 都报成 0**（run85 f11 t4：头槌/痛击/打击全 `c0`，能量 3）。计划和
  sim 都按「整手免费」算，打到第三、四张时真实能量见底 → `UNPLAYABLE(not_enough_energy)` → 整批中断。
- **改动**
  - `tools/sim.py:316 _free_attack_count()`：读 `FREE_ATTACK(_POWER)` 的 `amount`，得到「还能免几张」。
  - `tools/sim.py:540`：`from_state` 里记 `s.free_atk`；**并把手牌里被引擎报成 0 的攻击牌还原成 `c_base`**
    （`sim.py:546-553`），只在 `cost_of` 里按「下一张攻击 0 费」扣减（`cost_of`: `free_atk > 0 → c = 0`），
    每打出一张攻击就 `free_atk -= 1`。
  - `tools/sim.py` `parse_card` 新增 `c_base`（DB 原费）与 `eternal`（文本含「永恒」）。
  - `tools/ctx.py:565-569`：手牌显示**原费**，并单独给一条口径
    `free_atk: {n:1, t:"A", why:"免费攻击：只有下一张攻击牌 0 费，不是整手都免"}`。
- **为什么不改成「整手免费」**：run85 的两次中断本身就是反证 —— f17 t3 手牌 4 张攻击全报 0 费、能量 3，
  按整手免费 4 张全打得完，实际却在中途 `UNPLAYABLE`；f11 t4 也是能量先归零。卡面文字 + 实测都指向"下一张"。
  **这条语义仍有残留歧义（引擎为什么全报 0），见 §4.1，用 run86 的数据裁决，不靠猜。**
- **回归**：`test_free_attack.py`（14 条断言，独立文件）+ `test_regressions.py` R7（6）/R8（4）/R9（2）。
  覆盖用户点名的四件事：多张攻击只有部分免费、连续出牌把能量打空、**出牌顺序改变结果**、0 费牌还原不误伤「永恒」。

### P2 毒素被当成攻击牌（决策价值评估，不只是 sim）

- **根因**：DB 里 `TOXIC` 是 `Status`、描述是「回合末在手牌中受到 5 点伤害」，但引擎给了 `Damage=5` 动态值。
  `sts2._attack_profile()` 只看动态值不看牌型 → `VALUE_RANK ATTACK TOXIC 5伤/1费=5.0`，还进了 3 个 plan；
  `_attack_effect_table` 也给它发了 `EFF [0] TOXIC 5伤 → e0:5`。
- **改动**
  - `sts2.py:1879 card_is_status_like()`：`card_type/type` 或 DB `type` 命中 `status|curse` 即判状态牌。
  - `sts2.py _attack_profile()`：状态/诅咒牌直接返回 `None`（攻击榜、EFF、斩杀估算全部不再采信它）。
  - `sts2.py _hand_value_rank()`：跳过状态/诅咒牌。
- **回归**：R10（4 条）—— 毒素无攻击画像、不进 EFF 表、不进价值榜，同一手牌里的打击照常上榜（防「一刀切」）。

### P3/P7 遗物与回合初自伤不进预测（入伤高估的主因）

- **P3 根因（重新核对，纠正 run85 报告的一处归因）**：
  - **永冻冰晶 `PERMAFROST`**：首张能力牌 +7 格挡 —— 旧 sim 完全不知道，f17 t2 预测挨 23、实际 16，
    差额 **正好 7**（`17 - 7 + 6`，6 是呼唤的状态税）。已修。
  - **红面具 `RED_MASK`**：给全体敌人 1 层虚弱 —— **不需要在 sim 里建模**：`incoming()` 以引擎给的
    `total_damage` 为准（`sim.py` 注释与 `atk_final` 分支），虚弱已经算进那个数了；只有回落到
    `damage×hits` 的兜底路径才补 ×0.75。**run85 报告把 42 点高估摊给两个遗物，实际只有 PERMAFROST 是缺口。**
- **P7 根因**：狱火 `INFERNO`「回合开始时失去 1 点生命」没算进回合末血量 → 入伤里 12 次固定 +1。
- **改动**
  - `sim.py:542-545`：`from_state` 见到 `PERMAFROST` 置 `permafrost = 7`；`play()` 里**第一次打出能力牌**
    时 +7 格挡（`_pf_used` 只给一次）。
  - `sim.py:333 _turn_start_hp_loss()` + `sim.py:727`：`end_turn()` 的 `taken`/`hp_after` 都含 `tick`，
    返回值里单独给 `"tick"`。
  - `tools/ctx.py:457`：`net = 入伤 + 状态税 - 格挡 + 状态税掉血 + tick`，并输出 `tick` 字段。
  - `tools/act.py:183-184`：`forecast` 里带 `tick`。
- **回归**：R11（3）、R12（4）；另加 **R19（2）** 钉死「红面具不能二次乘虚弱」：
  `total_damage` 存在时按它算（9），只有回落到 `damage×hits` 才补一次 ×0.75（也是 9）——
  两条路径必须给出同一个数，否则就是重复打折。

### P4 敌人机制缺三项（假斩杀的直接来源）

| 机制 | DB 原文 | 旧行为 | 现在 |
|---|---|---|---|
| `FLUTTER_POWER` 振翅 | 攻击伤害 -50% | 满伤 | `sim.py:645` 攻击减半（能力/药水/势不可当按非攻击处理） |
| `ILLUSION_POWER` 幻象 | 下回合满血复活 | 当斩杀，白扔回合 | `sim.py:678` 打死标 `revive`，`act.forecast` 不算 `kill`/`clear`，单独给 `revive:[i]` |
| `SHRIEK_POWER` 尖叫 | 首次 ≤50% 生命时眩晕 | 照算入伤 | `_stun_threshold` 认 `amount`（骇鳗 75/150），`incoming()` 跳过眩晕怪 |

- 另补字段：`enemy_state` 输出 `flutter/illusion/escape`，模型能看见机制而不是靠记忆。
- **回归**：R13（4，尖叫跨线当回合不挨打）、R14（2，振翅减半）、R15（5，幻象不算斩杀/不算清场）。

### P5 领奖绕过牌组上限

- **根因**：`deck_cap_guard` 只挂在 `choose_reward_card` 上；`collect_rewards_and_proceed`/`resolve_rewards`
  会把待领的卡牌奖励一起收走 → 牌组 16→20，第二幕被稀释。
- **改动**：`sts2.py:2591 pending_card_reward()`；`cmd_do` 里对 `collect_rewards_and_proceed`/`resolve_rewards`
  在「奖励界面还有未领卡牌 + 牌组已满」时拦下，并提示先 `skip_reward_cards`（`sts2.py:2622-2632`）。
- **回归**：R16（3）。

### P6 药水位满 → 领奖静默 pending（4 次）

- **改动**：`sts2.py:785 free_potion_slots()` / `sts2.py:790 pending_potion_rewards()`；
  `claim_reward`/`claim_all_rewards` 在空格不够时直接报 `POTION_SLOTS_FULL` 并给出「先丢一瓶/跳过」的动作，
  不再让引擎回一个假的 `pending`。
- **回归**：R18（4）。

### P8 过渡快照护栏误报（2 次）

- **根因**：`_pre_draw` 只看「手牌空 + 牌堆非空」，run85 f11 t2 手牌确实打空了、能量还剩 1，也被判过渡快照。
- **改动**：`tools/act.py:284` 增加「新回合应是满能量」判据（`energy >= max_energy`，缺省 3）。**护栏没有拆**，
  只是把「打空手牌但还有费」这种情况放出来。
- **回归**：R17（2，能量 1 → 放行；能量 3 → 仍然拦）。

### P9 营养汤把打击变「永恒」→ 删牌锁死（策略，不是 bug）

- **改动**：`tools/ctx.py:919`：持有 `NUTRITIOUS_SOUP` 时输出
  `deck_lock: "营养汤：牌组里的『打击』带『永恒』，不能被移除（删牌要挑别的牌）"`。
- **没做**：没有替模型决定「拿不拿营养汤」，也没有阻断弃牌/删牌动作（那不是执行层的权力）。

### 1.10 未建模机制必须显式标记（不是 run85 的编号项，是本次验收要求）

- **要求**：`斩杀预测正确考虑已建模的减伤、复活等机制；尚未支持的机制必须明确标记，不能假装精确。`
- **改动**
  - `tools/sim.py UNMODELED_POWERS / UNMODELED_RELICS` + `unmodeled_parts()`：收集「这一局存在、
    但 sim 没建模」的机制，`from_state` 存进 `s.unmodeled`。
    当前表：`ESCAPE_ARTIST_POWER`（4 回合后离场）、`PLATING_POWER`（敌人回合末给自己覆甲）、
    `GORGET`（战斗开始 +4 覆甲）、`CANDELABRA` / `PAELS_FLESH`（额外费用）、
    `MUMMIFIED_HAND`（能力牌后随机手牌免费）、`ORNAMENTAL_FAN`（第 3 张攻击 +4 甲）。
  - `tools/ctx.py`：有未建模项时输出 `unmodeled: [{id, why}]` + `unmodeled_why`。
  - `tools/act.py forecast()`：有未建模项时输出 `approx: [id]`，**账本里能查到这一手是估的**。
- **边界（重要）**：`approx` 只表示「这一手的数字不精确」，**不改变任何校验、不降低门槛**；
  fx 校验、护栏、斩杀判断照旧。它是给模型的告示，不是给程序的后门。
- **回归**：R20（7）—— 覆甲敌人 + 护喉甲 → `unmodeled` 两项都在、`ctx` 也标、`forecast.approx` 对得上；
  干净局面**不乱标**（防「天天报警 → 被忽略」）。

---

## 2. 测试与门禁（逐项结果）

### 2.1 新增用例

| 文件 | 用例 | 断言 | 内容 |
|---|---|---|---|
| `tools/test_free_attack.py` | 5 组 | 14 | 部分免费 / 打空能量 / 顺序敏感 / 只免一次 / 2 层免费 |
| `tools/test_regressions.py` | R7–R20 | 52 | 见 §1 每项的「回归」 |

`test_regressions.py` 现在共 **73 条断言**（原 21 条 + 新 52 条），全部来自 run85 的真实局面（文件头注明了
run/楼层/回合），不是手搓的理想场景。

### 2.2 全量执行结果

```
python -X utf8 tools/test_all.py      → TOTAL 27 | FAILED 0 | 110.25s | ALL 27 TEST FILES PASS
python -X utf8 tools/preflight.py     → PASS 8 个测试文件 + 局面检查 → PREFLIGHT OK
```

`preflight` 的 8 项（`test_imports / test_sim / test_act_guards / test_gates / test_combat_math /
test_ctx_act / test_regressions / test_free_attack`）全部 `ALL OK`。

### 2.3 历史重放（同一份账本、同一命令、唯一变量是代码）

| 范围 | 改前 | 改后 |
|---|---|---|
| 全库 replay（`logstats.py replay`） | 135 可重放 / **114 完全吻合** / 21 不符 | 135 / **115** / 20 |
| run85 子集（`--run FNVZ5NS2U5`） | 12 可重放 / **9 吻合** | 12 / **10** |

0 回退。这条只证明「修完不会把已经算对的局面算错」，**不证明新模型更准**。

### 2.4 精度口径（run86 起唯一口径）

新增 `tools/accuracy.py`。run85 复盘里「入伤 13/35」「血量 33/51」「假斩杀 2」是三个不同分母，
从 run86 起只认这一个脚本的输出，口径写死在文件头：

- 总体 = `outcome == executed`（被拒/报错的记录不算，它们没有真实结算）；
- 入伤 = `ended=1` 且 `forecast.take` 非空、`before.me.hp`/`after.me.hp` 都有值、**排除 `clear`**
  （清场会触发燃烧之血回血，血量不可比）；实际入伤 = `before.me.hp − after.me.hp`；
- 斩杀 = 按**下标**计：预测/确认（怪物从 after 列表消失）/假（仍在且 hp>0）；清场单列；
- 命中率一律先给分母。

**版本可追溯（run86 前补的）**：`tools/decision_log.py` 现在给每条决策记录写 `rev`（git 短 sha），
`accuracy.py` 头部同时打印「账本里的 rev」和「当前工作区 HEAD」。run85 及更早的账本没有这个字段，
脚本会直接说「无此字段，跨版本统计无效」—— 这正是 run85 复盘时「两套代码混在一局里」没法切开的坑。

**用这个口径重算 run85（旧代码打的，作为基线）**：

```
N=38 入伤：完全命中 12/38 = 31.6% · ±1 24/38 = 63.2% · 平均|误差| 2.45 · 平均偏差 +1.18
N=38 回合末血量：完全命中 12/38 · ±1 26/38 = 68.4%
斩杀：预测 19 次 · 确认 12 · 假斩杀 7 · 未预测死亡 12（清场 12 条单列）
```

两处必须说清楚的差异：

1. 入伤分母从 35 变成 38、±1 从 64.7% 变 63.2% —— 旧数字是另写的脚本，**分母不同**，不可沿用；
2. 假斩杀从「2」变「7」：旧统计把**幻象复活**单列成 C5，新口径按「预测斩杀没落地」一律算假斩杀
   （f21 t2/t3/t5/t6）。**run86 的起点是 7，不是 2。**

---

## 3. 过程中发现的其他问题

### 3.1 run85 报告的归因需要修正

「红面具没建模 → 入伤高估」不成立（虚弱已在引擎 `total_damage` 里）。真正的高估缺口是 `PERMAFROST` 的 7 点。

### 3.2 `test_decision_log.py` 本来就红（不是本阶段改坏的）

`test_all.py` 之前一直 FAILED 在 `test_decision_log.py`。根因是**测试 fixture 与漏费护栏冲突**：
用例拿「手上有可打的打击、还剩 3 费」的局面去测 GAME_OVER 写总结，被 `护栏·漏费` 正当地拦下。
改的是 fixture（换成手牌空的局面，`test_decision_log.py:175-180`），**没有动护栏**。改完 27/27 全绿。

### 3.3 `--dry` 没被本阶段破坏

`test_act_guards.py` 里 dry 相关 4 条仍全过（dry 不落地、给最佳线、照样拦没费的牌）。

### 3.4 口径提醒（给写 run86 报告的人）

`accuracy.py` 的「未预测死亡」包含势不可当/荆棘/毒等**非攻击死因**，它不是「放跑了能斩的机会」。
真正的「保守漏斩」需要离线搜斩杀线，**本阶段没有做这项统计**（明确记为未执行）。

---

## 4. 未解决 / 未验证（不许当成已修）

### 4.1 免费攻击的引擎语义仍有歧义（P1 的残留风险）

卡面写「下一张攻击 0 费」，引擎却把整手攻击的 `energy_cost` 报 0，run85 的中断数据支持「只免一张」。
**但这是间接证据，不是直接读数。** 现在的实现按卡面走；如果 run86 出现「模型明明还有费却打不出攻击」，
就说明引擎真的是整手免费 —— 届时改的是模型（往宽），不是护栏。
run86 第一次遇到 `FREE_ATTACK` 时要顺手记下：手牌各区费用 + 每打一张之后真实 `energy`。

### 4.2 只建模了两个遗物，其余仍会偏

已建模：`PERMAFROST`（首张能力 +7 甲）。已确认不需要建模：`RED_MASK`（引擎意图已含）。
**没建模**（run85 账本/遗物列表里出现过或常见）：
`GORGET`（战斗开始 +4 覆甲）、`CANDELABRA`（第 2 回合 +2 费）、`PAELS_FLESH`（第 3 回合起 +1 费）、
`MUMMIFIED_HAND`（每打一张能力牌，随机一张手牌本回合免费 —— 与 §4.1 同源，会让"免费"更复杂）、
`ORNAMENTAL_FAN`（同回合第 3 张攻击 +4 甲）。

**后果**：如果 run86 拿到这些遗物，`入伤 ±1 ≥ 80%` 这条**可能不达标，而且不一定是模型的错**。
报告里必须按遗物分列，不能拿一个总数糊过去。
**缓解（不是修好）**：这些遗物现在会被 `unmodeled`/`approx` 标出来（§1.10），所以 run86 的账本能
直接筛出「这一手本来就不准」，不用事后猜。

### 4.3 敌人机制只补了三个

`ESCAPE_ARTIST`（4 回合后离场）只暴露了 `escape` 字段，没算「它跑了就不用挡」；
`PLATING` 只建模了现有口径，覆甲在「回合末给甲 / 回合初 -1」的顺序仍未逐条验证；
`MINION`（爪牙：首领死后逃跑）没建模。run85 的 f21 三连空输出就是幻象+爪牙，幻象已修，爪牙未修。

### 4.4 两个未解释的偏差

- `f22 t3`：预测入伤 19、实际 8（差 +11）；
- `f20 t4`：预测 0、实际血量**反涨 3**（数据上像是战后回血/药水，账本没记）。
  这两条留给 run86 的现场记录，不再猜。

### 4.5 本阶段**没有**验证的项（别当成通过）

- 势不可当（`JUGGERNAUT`）、延迟给费（`DRUM_OF_BATTLE`/`PYRE`）、滑溜（`SLIPPERY`）：run85 未再遇到，
  只有历史回归用例（R2/R3/R4）在跑，**不等于本局验证过**；
- 漏斩 / 换血线阈值：本阶段**没碰**（用户要求先做 P0/P1）；
- 真机执行链路：`--dry` 通过，但**没有一次真实 batch 在引擎里跑过**（游戏停在 GAME_OVER）。

---

## 5. run86 准入结论

**结论：准入，但只作为"测量局"，不宣称任何一条验收标准已达成。**

理由：P0/P1 的每一项都有「根因 + 实现改动 + 来自 run85 真实局面的回归用例」，全量 27 个测试文件与
preflight 全绿，历史重放 0 回退，且没有放宽任何护栏。这足以支撑「再打一局去测量」，
但**不足以**支撑「已经修好了」—— 所有修复都只在离线局面里验证过。

### 5.1 五条验收口径（run86 按这个测，全部未达标/未测量）

| # | 标准 | 怎么测（唯一口径） | 现在的已知风险 |
|---|---|---|---|
| 1 | batch 中断 ≤1 | 账本 `outcome=batch_error` 计数 / executed | 免费攻击语义若真是整手免费，仍会中断；未建模遗物也会加费/减费 |
| 2 | 假斩杀 = 0 | `accuracy.py` 斩杀段：预测/确认/假 + 未预测死亡 | run85 基线是 **7**（不是 2）；幻象已修，爪牙未修 |
| 3 | 入伤 ±1 ≥80% | `accuracy.py` 入伤段，**分母 N 必须写出来** | run85 基线 24/38 = 63.2%；未建模遗物会直接吃掉这条 |
| 4 | 免费攻击不再超支 | 每次遇到 `FREE_ATTACK` 记录「手牌费用 + 每张之后的能量」 | 语义歧义（§4.1） |
| 5 | 遗物战斗开始效果回归全过 | R11 已在离线跑；真机需在拿到该类遗物时逐一记录 | 只覆盖 PERMAFROST |

### 5.2 判定规则（先写好，避免事后找理由）

- 5 条**全部达标** → run86 算通过，可以进 run87 正常打；
- 任一条不达标 → **不许**把这条记录成「已修」；按 `accuracy.py` 的输出定位到具体楼层/回合，
  run87 之前必须再补一次修复 + 回归，**不能靠再打一局碰运气**；
- 若第 3 条因为 §4.2 的未建模遗物而不达标，报告里必须按遗物分列，先把遗物补完再谈命中率。

### 5.3 run86 开局前必须做的一件事

```
python -X utf8 tools/preflight.py        # 必须 PREFLIGHT OK（现已含 test_free_attack）
python -X utf8 tools/accuracy.py         # 先看一眼上一局基线，别拿两套分母对比
```

---

## 6. 改动文件

| 文件 | 改动 |
|---|---|
| `tools/sim.py` | 免费攻击 / 永冻冰晶 / 狱火 tick / 振翅 / 幻象 / 尖叫自晕；`c_base`、`eternal`；未建模机制清单 |
| `sts2.py` | 状态牌判定、`_attack_profile`/`_hand_value_rank` 过滤、`pending_card_reward`、药水位检查 |
| `tools/ctx.py` | `free_atk`、`tick` 进 `calc`、`deck_lock`、`unmodeled` 标注 |
| `tools/act.py` | `forecast` 的 `revive`/`tick`/`approx`、清场判定排除复活、`_pre_draw` 满能量判据 |
| `tools/decision_log.py` | **新增** `code_rev()`，每条记录带 `rev`（代码版本可追溯） |
| `tools/preflight.py` | 测试清单加入 `test_free_attack.py` |
| `tools/logstats.py` | `replay --run <run_id>` |
| `tools/accuracy.py` | **新增**：入伤/血量/斩杀的唯一定义口径 + 版本行 |
| `tools/test_free_attack.py` | **新增**：14 条断言 |
| `tools/test_regressions.py` | 新增 R7–R20（52 条断言） |
| `tools/test_decision_log.py` | 只修 fixture（§3.2）+ 2 条 `rev` 断言，未动护栏 |
