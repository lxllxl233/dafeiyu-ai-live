# 经验卡检索（lessons.jsonl + lesson_index.py）

目的：把 run96–run105 那些**账本核对过**的失败，变成「**什么局面 → 该做什么**」的短卡片，
每回合**只注入相关的那几条**。不做胜负预测，不做长篇攻略；长文在 `docs/LESSONS.md`，
索引在 `logs/SEASON_INDEX.md`，这里只解释检索层怎么工作、怎么加卡。

## 1. 一张卡长什么样

`logs/lessons.jsonl` 一行一张（JSONL，UTF-8）：

```json
{"id": "L105_eel", "kind": "case",
 "when": {"enemy_any": ["TERROR_EEL"], "hp_pct_max": 60},
 "t": "骇鳗：24/80 进场先吃 18→6；只打全挡+药水，别换血（run105）",
 "why": "23:49:10 T1 出 INFLAME+SETUP_STRIKE+STRIKE 没挡，24→6",
 "prio": 9, "verified": true,
 "src": {"run": "run105", "file": "logs/decisions.jsonl", "ts": "2026-10-27T23:49:10"}}
```

| 字段 | 规则 |
|---|---|
| `id` | 唯一，建议 `runNN_主题` |
| `when` | **触发条件**，键见下表；同一键的列表内是 **OR**，不同键之间是 **AND** |
| `t` | 给模型的**一句话建议动作**，≤60 字（超了 `--check` 报错） |
| `why` | 人看的证据摘要（不进 ctx） |
| `prio` | 同分时的次序（越大越前） |
| `verified` | **`false` 永不注入**；`true` 必须能被 `src` 里的账本行证实 |
| `src` | `{run, file, ts}`，`file` 必须真实存在（`--check` 校验） |

### `when` 可用条件

| 键 | 含义 | 例 |
|---|---|---|
| `screen` | 界面（小写） | `["combat"]`、`["map"]`、`["shop"]`、`["reward"]` |
| `enemy_any` | 场上任意一只的敌人 id | `["TERROR_EEL"]` |
| `enemy_type` | 敌人类别（DB 的 `enemies.type`，小写） | `["elite"]`、`["boss"]` |
| `enemy_n_min`/`enemy_n_max` | 敌人数量区间 | `2` / `4` |
| `hp_pct_max`/`hp_pct_min` | 自己是**百分比**血 | `45` |
| `incoming_min` | 本回合预计入伤下限 | `15` |
| `hand_any` | 手里有这张牌 | `["BLOOD_WALL"]` |
| `potion_max` | 药水槽 ≤N | `0` |
| `node_any` | MAP 可选节点类型（小写） | `["elite"]`、`["rest"]` |
| `act_min`/`act_max`/`floor_min`/`floor_max` | 幕/层区间 | `1` |

## 2. 怎么检索（`tools/lesson_index.py`）

1. `facts_from_ctx(ctx)` 把局面压成事实串：界面、血%、敌人 id/类别/数量、手牌、节点、入伤；
2. 逐卡 `_hit()`：所有键都满足才算命中，同时累计**具体度分**
   （`enemy_any`/`hand_any` +2，`node_any`/`enemy_type` +1）；
3. 排序：`score` 降序 → `prio` 降序 → `id`；
4. 取前 **3** 条，渲染成 `[{"id","t","s"}]`，**总字节 ≤420**（超了按条数砍）；
5. `ctx.py` 在 `_combat()` / `build()` 里把结果并进局面包：`{"lesson_v":1,"lessons":[...]}`；
   **一条都没命中就不加这个字段**（无关回合 0 字节）；
6. `STS2_LESSONS=0` 可临时关掉（回溯老局面用）。

检索成本：18 张卡时约 **0.2 ms**/次；注入样例（骇鳗 30% 两条）**240 B**，
三条最坏 360 B，上限 420 B。

## 3. 现在有哪 18 张（按触发面）

- **指定敌人**：骇鳗、异蛙寄生虫、鬼祟珊瑚群、祭典野兽、灵魂异鱼、瀑布巨兽、
  乐加维林族母、小啃兽×2、墨宝×3/×2、幽灵园丁×4、血墙+低血、SPITE 在手
- **血量档**：精英 ≤45%、Boss <70%、入伤 ≥15 且 <45%、低血商店、低血奖励
- **节点**：MAP 有精英且 <60%、MAP 有营火且 <60%

卡片全部来自 run96–run105 的**首场致命战斗**或用 `run_summary_*.json` 的终局数据，
`src.ts` 指向账本里那一行（跑测试会回放核对，见 §5）。

## 4. 加卡 / 改卡流程

1. 先在账本里找到**能证实**的那一行（`logs/decisions.jsonl` 的 `before`/`after`）；
2. 写卡：`when` 只放**这条证据支持的**条件（别把「我觉得」写成条件）；
3. `python tools/lesson_index.py --check` —— 字段/长度/`src` 文件/未知键/重复 id；
4. `python tools/lesson_index.py --enemy TERROR_EEL --hp 24` 手查命中与排序；
5. `python tools/test_lesson_index.py`（66 项断言，含账本回放）；
6. 想被文档测试覆盖，就别改 `test_docs_consistency.py` 的禁用结论检查绕过。

## 5. 测试（`tools/test_lesson_index.py`）

| 组 | 验什么 |
|---|---|
| 1 | 卡片自检：`validate()` 全过、全 `verified`、`t`≤60、`src.file` 存在 |
| 2–4 | 敌人/血量/手牌命中，且**不该命中的不命中** |
| 3 | 血量边界：45% 命中、46% 不命中 |
| 5 | 节点：精英/营火/商店/奖励，含「只有杂怪不命中」「80% 血不命中」 |
| 6 | 90% 血打普通怪 → 0 条，ctx 里**没有** `lessons` 字段 |
| 7 | 预算：≤3 条、≤420 B |
| 8 | `verified:false` 不注入、未知条件键不命中 |
| 9 | 注入链路：`ctx.build` / `act._next_ctx` 都带，且 `rules` 仍是 5 条 |
| 10–11 | 开关关掉为空、重复调用结果稳定 |
| 12 | **账本回放**：run96/97/98/100/101/102/103/104/105 的真实 `before` 快照各命中本局卡片；run105 f2 健康回合 0 条 |

## 6. 已知边界

- 卡片只覆盖**第一幕/本季**的失败；第二幕、第三幕还没卡片（没证据就不写）；
- `enemy_type` 来自 `db/sts2.db` 的 `enemies.type`，DB 没收录的怪回落到内置表，再没有就**不按类别匹配**（不会瞎命中）；
- 经验卡**不能替代**硬闸：`ctx.rules` 优先，卡片只是「这个局面历史上怎么死的」；
- 卡片里的建议若与模拟器同一回合的 `plans` 冲突，先查 `docs/LESSONS.md` 里对应的 bug 条目
  （例如 SPITE 的双段判定 `L105_spite`），**别照抄模拟器数字**。
