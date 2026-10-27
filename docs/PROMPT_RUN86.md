# run86 启动提示词（新窗口复制粘贴即用）

```
先加载 skill：`sts2-ironclad-ironclad`（决策 OS）+ `sts2-caster`（说话/overlay 台词），再按它们执行。

你来玩《杀戮尖塔 2》铁甲战士 A10，工作目录 D:\aigame\sljt\sljt。
这一局是 run86，**定位是"测量局"，不是"打出好成绩"的局**：上一轮刚修完 P1–P9 并加了回归，
离线证据齐全，但一条实战指标都还没测过。你的首要产出是**三个可复现的数**，不是通到第几层。

## 0. 开工顺序（照做，别跳）

1. 读 `docs/RUN86_FIXES.md`（全篇，尤其 §4 未解决 / §5 准入与判定规则）
2. 读 `docs/RUN85_VERIFICATION.md`（注意开头「勘误」：红面具归因、斩杀口径都以 RUN86_FIXES 为准）
3. 读 `docs/RUN_DOCTRINE.md` §9（拿牌预算 / 血量硬线 / 数值口径 / DPS 门槛）
4. `python -X utf8 tools/preflight.py` → **必须 PREFLIGHT OK**；不 OK 就停下来报告，不许绕过
5. `python -X utf8 tools/accuracy.py` → 抄下上一局固定口径基线：
   入伤 ±1 **24/38 = 63.2%**、平均|误差| 2.45、偏差 +1.18；斩杀 19 预测 / 12 确认 / **假 7**
6. `python -X utf8 sts2.py state` → 确认 MAIN_MENU、无存档；`python obsctl.py status` → 确认没在录

## 1. 本局唯一目标：测三个数（口径写死，不许自创）

| 指标 | 口径 | 起点（run85，旧代码） | 达标线 |
|---|---|---|---|
| batch 中断 | 账本 `outcome=batch_error` 条数 / executed | 4 / 61 | **≤ 1** |
| 假斩杀 | `accuracy.py` 斩杀段：预测/确认/假（按**下标**计，幻象复活算假） | 19 / 12 / **7** | **= 0** |
| 入伤 ±1 | `accuracy.py` 入伤段：`ended=1` 且非 clear，实际 = `before.me.hp − after.me.hp` | **24/38 = 63.2%** | **≥ 80%（分母必须写出来）** |

硬性要求：
- 只认 `python -X utf8 tools/accuracy.py --run <RUN_ID>` 的输出。**不许另写脚本算百分比**，
  不许换分母，不许跨版本混统（老账本没有 `rev` 字段，脚本会自己提示）。
- 「假斩杀 = 0」不等于模型可靠：报告里必须同时给**预测次数/确认次数/假斩杀清单**，
  以及「未预测死亡」条数（注意它含势不可当/荆棘/毒等非攻击死因，不等于放跑了能斩的机会）。
- 「保守放跑能斩的机会」需要离线搜斩杀线，本局不做，报告里明确写"未统计"。

## 2. 局中必须顺手记的三件事（不做等于白打）

1. **第一次遇到「免费攻击 / FREE_ATTACK」**：把当时手牌每张的 `c`、`energy`，以及**每打出一张之后的
   真实 energy** 记进 `logs/run86_notes.md` 的固定小节。这是唯一还没裁决的语义歧义：
   卡面写「下一张 0 费」，引擎会把整手报 0 费。若出现「还有能量却打不出攻击」，说明引擎是整手免费，
   记录下来（**改模型，不改护栏**）。
2. **账本里 `forecast.approx` 非空的记录**：在 notes 里逐条记（楼层/回合/哪几个机制）。
   这些回合的入伤/斩杀本来就是估的 —— 统计时要能从"模型算错"里摘出去。
3. **每次 batch_error / rejected_guard / rejected_fx**：记楼层、回合、原始错误行、
   当时的能量/手牌/入伤。这三个数就是 run87 的输入。

## 3. 执行纪律（一条都不许破）

- 只用 `tools/ctx.py` 读局面、`tools/act.py` 出牌；**不手改战斗决策**，不直接 `play` 跳过 batch
- 一回合一条 batch；不确定先 `--dry`
- **不许**新增 `allow_*`、不许放宽任何护栏/阈值、不许改 fx 校验、不许删断言来让这局/测试通过
- 不预称指标达成；报告只写 `accuracy.py` 量到的数
- 控制台一律 `python -X utf8`；不写 PowerShell heredoc（要临时脚本就写 `tmp_*.py`，已在 .gitignore）
- 游戏内动作顺序按 `docs/HANDOFF_PROMPT.md` 的「每局固定流程」：overlay reset → `obsctl.py start`
  → `do open_character_select --thought "<开场白>"` → 确认 IRONCLAD / A10 / can_embark → `do embark`
  → 涅奥选上限 → `sts2.py route` 定路线贴 overlay

## 4. 收尾（每局必做，顺序别换）

1. `python obsctl.py stop`；录像改名 `recordings/sts2_ironclad_a10_run86_<win|lose>_floor<M>.mp4`
2. `python -X utf8 tools/accuracy.py --run <RUN_ID>` 输出**原样**贴进报告
3. 写 `docs/RUN86_REPORT.md`：三指标实测（含分母）+ 假斩杀逐条 + `approx` 清单 +
   未解释偏差 + 与 run85 基线对比 + 本局死因
4. 更新 `logs/run86_notes.md`、`recordings/INDEX.md`
5. **commit 分开**：run 记录一个 commit，代码修复另一个 commit（不许混在一起）
6. 按 `docs/RUN86_FIXES.md` §5 的判定规则下结论：
   5 条全达标 → 可进 run87 正常打；任一条不达标 → 写明是哪条、在哪个楼层回合，
   run87 前必须再补修复 + 回归，**不许靠再打一局碰运气**

## 5. 已知的"不算你错"清单（但必须写进报告）

- 未建模遗物（护喉甲/烛台/佩尔之肉/干瘪之手/精致折扇）→ 会标 `approx`，命中率可能因此不达标
- 敌人机制只建模了：振翅/幻象/尖叫/爪牙/饥饿/滑溜/尖刺；`ESCAPE_ARTIST`、敌人 `PLATING` 只标记未建模
- `f22 t3 +11`、`f20 t4 血量反涨 3` 是 run85 遗留的未解释偏差

开始吧：先跑第 0 节那 6 步，把 preflight 与基线数报出来，再开新局。
```
