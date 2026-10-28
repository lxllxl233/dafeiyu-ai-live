# 修复报告：牌面读取（run69 白挨伤害的根因）

> 症状（用户观察）：一局里多次「还有费不叠甲、白白掉血」，「能斩杀不斩杀、多挨一回合」，
> 以及「读到的牌好像少了几张」。三个独立问题叠在一起，下面是逐条根因、证据、修法和验证。

## 1. `end_turn` 之后把「过渡快照」当成新手牌（最主要）

**现象**：`act.py` 执行完一手（含 `end`）后，立刻 `sts2.get_state()` 把下一回合的 `ctx` 打印出来；
但那一刻游戏还在抽牌动画里，手牌只推过来 1 张甚至 0 张。

**证据**（run69 f17 第 7 回合）：`act.py` 返回的 `hand` 只有 `BODY_SLAM` 一张；
同一时刻再跑 `python tools/ctx.py` 是 **3 张**（`BODY_SLAM` / `DEFEND_IRONCLAD` / `RAGE`）。
照 1 张牌排牌序 → 剩下 2 张永远打不出去 → 漏费、漏甲、掉血。

**修法**：
- `tools/act.py`：`ended` 之后改用 `sts2.wait_new_turn_hand(prev_hand_len, timeout=6.0)`
  （可出牌 + 状态指纹连续 2 次不变 + 至少 0.6 秒才返回）；读不到就输出
  `stale_ctx` 警告，明确禁止拿这份 ctx 排牌序。
- `tools/ctx.py`：单独跑 ctx 时也加同样兜底（手牌 ≤1 张而抽牌/弃牌堆非空 → 先等抽完）。

## 2. 同名牌合并成一条，看「条数」会以为牌很少

`ctx.hand[]` 会把同 id 同数值的牌合并成一条并带 `q`（张数）。只看数组长度会误判手牌规模。

**修法**：`ctx` 新增 `hand_n` = **真实手牌张数**（与 `hand[]` 的合并无关）。

## 3. 没有「漏费 / 漏甲 / 漏斩」护栏，全靠模型自觉

**修法**：`tools/act.py` 在每一手（会结束回合的）执行前硬校验，顺序按亏多少排：

| 护栏 | 触发条件 | 拦截效果 |
|---|---|---|
| `护栏·漏斩` | 本地搜索找到**能通过 `sim.validate` 且真的清空活怪**的全斩线，而这一手不斩 | 拒绝，并把斩杀线贴出来 |
| `护栏·漏费` | 结束回合时还有能量，且手上还有当前能量打得出的牌 | 拒绝，列出还能打的牌 |
| `护栏·漏甲` | 这一手结束净挨 ≥6 点，起手有 ≥4 甲的防御牌且当时打得出，却一张没打 | 拒绝（<6 点或甲 <4 只 `warn` 不拦） |

清场（活怪为空）时三项都不拦。逃生口写在 plan 顶层：
`{"allow_take":1}` / `{"allow_leftover":1}` / `{"allow_no_lethal":1}`，用于明确换血、留费、拖回合。

## 4. 附带发现：牌面文字用了 DB 描述，可能过期

**证据**（run69 f17 第 2 回合）：`EXPECT_A_FIGHT`（跃跃欲试）在 DB 里写
「获得15点格挡，每点力量额外+5」、费用 3；引擎实时数据是
`energy_cost: 2`、`rules_text: 每打出一张攻击牌回复1点能量` —— **它是回能牌，不是防御牌**。
我按 DB 文字把它当 15 甲打出去，实际 0 甲：Boss 21 点入伤只被防御牌挡了 5 点，净挨 16。
（同回合 `act.py` 返回的 `fx.take=16` 已经如实写了，我没拿它对账——这条也写进 LESSONS。）

**修法**：
- `sim.parse_card` 保留引擎实时文本 `ltx`；`ctx` 的 `tx` 改为**优先实时文本**，
  与 DB 描述不一致时加 `txdiff:1`，提示"以引擎/act 账本为准"。

## 验证

### 单元/回归（全部离线）

```
python tools/test_act_guards.py      # 新增：17 项护栏用例（漏费/漏甲/漏斩/清场放行/stale 等）
python tools/test_ctx_act.py         # 12 组 ctx+act 用例（含新增 txdiff 用例）
python tools/test_execution_safety.py  test_turn_hand_wait.py  test_plan_visibility.py
python tools/test_combat_math.py       test_draw_boundary.py   test_state_compact.py  test_batch.py
```

全部 `ALL OK`。

### 历史账本重放（真实局面）

```
python tools/audit_guards.py --last 120
扫描 99 条带状态的决策，其中 83 手是「结束回合」
按新护栏会被拦下：34 手     漏甲 27 / 漏费 5 / 漏斩 2
```

也就是说：run69 里约 **4 成** 的回合会当场被拦下来改牌序，而不是继续白吃伤害。

## 改动文件

- `tools/act.py`：三道护栏 + `_taken_if_end` / `_affordable` / `_free_block_cards`、`ended` 后等抽牌、`stale_ctx`
- `tools/ctx.py`：`hand_n`、独立运行时的抽牌兜底、`tx` 优先实时文本 + `txdiff`
- `tools/sim.py`：`parse_card` 输出 `ltx`（引擎实时牌面）
- `tools/test_act_guards.py`（新）、`tools/audit_guards.py`（新）
- `tools/test_ctx_act.py` / `tools/test_draw_boundary.py`：旧用例补 `allow_*`（它们本身就是"换血/留费"场景）
- `games/slay-the-spire-2/decision/knowledge/ironclad/OS.md`：协议里写明护栏、`hand_n`、`stale_ctx`

## 已知限制

- `auto`（杂怪连打）走 sim 自己排的线，不过护栏。
- 漏甲阈值是经验值（6 点 / 4 甲）；真换血要显式 `allow_take`。
- 若某张牌 DB 有数字、引擎不暴露数字（如上面的跃跃欲试），sim 仍可能算偏：
  看到 `txdiff` 就以 `act` 返回的 `fx.take` 为准对账。
