# 交接 · 2026-10-27 12:5x（用户要求开新窗口，run84 已存盘停在 f1）

> **⚠️ 本文件已过期（内容停在 run84 存档）。当前状态见：**
> - run85（`FNVZ5NS2U5`）已打完并死在第二幕 f22，游戏现在 `GAME_OVER`；
> - 复盘：`docs/RUN85_VERIFICATION.md`；修复+回归：`docs/RUN86_FIXES.md`（**run86 开工前必读 §5**）；
> - run86 精度只认 `python -X utf8 tools/accuracy.py`（口径写死在文件头，别再用别的分母）。


## 1. 现在的位置

- 游戏：`MAIN_MENU`，**run84 已存档**（`continue_run` 可用）。
  - 存盘点：**第一幕 f1，刚踏进第一个怪物房**（三敌人，战斗第 1 回合，这是 SL 起点，手牌会重抽）。
  - 存档时：A10，HP 64/80，金 99，牌组 11（打击×5/防御×4/痛击/进阶之灾）。
- Neow 选了 **白银熔炉 SILVER_CRUCIBLE**（前 3 次卡牌奖励都是升级版；第一个宝箱是空的）。
  理由：本阶段最大毛病是「牌组质量/稀释」，升级版奖励 = 不额外占牌位的强度。
- 已定路线（81 条路里唯一「1 精英 + 早商店」的一条，全部路都至少 1 精英）：
  `(1,4) M → (2,4) M → (3,4) ? → (4,5) $商店 → (5,6) M → (6,6) M → (7,5) R营火 → (8,4) ? → (9,3) 宝箱 → (10,3) 精英 → (11,2) ? → (12,2) 营火 → (13,2) M → (14,1) M → (15,0) 营火 → Boss`
  第一脚已经迈到 (1,4)。
- OBS：**仍在录制 run84**（`recordings/.current` 记着 run=84）。
  收尾时用 `python tools/rec_episode.py finish --result LOSS_xxx_fNN`（`--run` 可省略，会自动对号）。

## 2. 这轮已经做完的事（已 commit + push 到 main）

- 复盘：`docs/RETRO_runs78-83.md`（runs78–83 六局账 + 死因分类 + 牌组算术 + 代码改动清单）。
- 新规则写进 `docs/RUN_DOCTRINE.md` **§9**：拿牌预算 / 血量硬线 / 四个数值口径 / DPS 门槛 / 固定工具动作。
- run83 现场笔记：`logs/run83_notes.md`。
- 代码（全部离线测试绿：`test_sim` / `test_act_guards` / `test_gates` / `test_combat_math` / `test_ctx_act`）：
  1. `sim`/`ctx` 支持 **柔嫩**（每打一张牌 -1 力-1 敏 → 先打贵的）、**缩小**（-30%，牌面已含，别二次乘）、
     **缠结**（攻击 +1 费，`hand[].c` 是实价）、**失衡**（全挡即眩晕，ctx 会提示 `IMBA_STUN`）。
  2. **固化药水**（格挡 ×3）建模：`blk_mul`。
  3. `act.py --dry`：只验算不执行（`fx`/`steps`/`best`），用来在花掉一回合前核对算术。
  4. `act.py` 在 batch 被 `CARD_SELECTION` 打断时回传 `selection` + `next`（头槌类牌不用再多跑一趟）。
  5. `rec_episode.py start --run N [--note]`，`finish --run` 可省略。
  6. `sts2.deck_cap_guard` 新增 **`DECK_PACE`**：一幕前半段拿牌过快就拦。

## 3. 新窗口直接照这个顺序开工

```powershell
python tools/live.py check                    # overlay/TTS/OBS 五项
python tools/ctx.py                           # 确认 MAIN_MENU + continue_run
python -X utf8 sts2.py do continue_run        # 回到 f1 那场战斗
python -X utf8 tools/ctx.py                   # 读局面
```

自检（全部离线，30 秒）：`python tools/test_imports.py` / `test_sim.py` / `test_act_guards.py` /
`test_gates.py` / `test_combat_math.py` / `test_ctx_act.py` —— 六个都应打印 `ALL OK`。

- 战斗用 `tools/ctx.py` → `tools/act.py`（一手一个 batch，别拼大计划）。
- 拿牌前先跑 §9.1 预算：第一幕 **+4 张** 上限、f8 前 ≤14 张。
- 每幕进 Boss 前跑 `python tools/dps.py`，净输出 <40 别进。
- 台词密度：本局台词统计只有 26%，**目标 ≥45%**，`act.py` 的 `say` 里每次至少埋一句。

## 4. 别忘的两条今天刚学的

- 有**失衡**怪：优先把它那一击**完全格挡**，白赚一轮。
- 有**柔嫩**：贵牌先打，越往后的牌越小；数值别自己乘，`hand[].dmg` 已是实价。
