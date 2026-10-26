# ctx v3 字段详解（tools/ctx.py 输出）

| 字段 | 内容 |
|---|---|
| `me` | hp/max/blk/e/em/stars/`buffs[]`（力量/敏捷/覆甲等跨回合 buff 在这） |
| `relics`/`potions` | 遗物 `[{i,id,n,fx,st}]`（fx=效果，st=on/x{n}/melted）；药水 `i/id/n/d/t/use/st`+`nxt` |
| `hand` | **实费** `c` + 张数 `q` + 类型 `t` + `dmg/hits/cond/blk/draw/en/hp/vuln/weak/aoe/x/exh/cm/add/pw/play`；`kw`=词条（EXH 消耗／ETH 虚无／INN 固有／RET 保留／UNP 不可打／ETR 永恒／SLY）；`tx`=带机制的原文（≤42 字，如踩踏「每打一张攻击 -1 费」）；`nxt`=留给下一轮什么（`vuln/weak/frail` 挂敌、`str/dex/plating/power` 挂己、`retain/draw/block_keep`） |
| `enemies` | `i/id/n/hp/max/blk/b/bd/it/nx/nx_tot/hist/mech` |
| `piles` | `draw`/`dis`/`exh`：张数 + 组成（同名合并，`STRIKE+` 带加号） |
| `calc` | `inc/blk/net/full/lethal/chain/aoe/tax` + `dmg`（本回合最大伤害）/`ttk`（每怪还几回合死）/`ttk_all`/`nx_inc`（下回合预计总入伤）/`nxdraw`（下一手期望 `{n,blk,dmg,uncertain}`） |
| `can` | `vuln/weak/draw/en/cost/exh/add/aoe/tf/buff`；`buff=[{id,nxt}]`=本回合能给下一轮上 buff 的牌/药水 |
| `plans` | 五类各一：`k/s/d/take/hp/e/kill/stun/risk`；`s` 是含药水的牌序；`k=maxdmg` 是纯伤害上限线，`risk=1` 时 `hp<=0`，只作上限不要照打；`k=potion` 是单独的药水线 |
| `warn`/`choice` | 红灯；非战斗 map/event/reward/shop/rest/select/chest/modal/over |
| `choice.eval` | 非战斗建议层：`deck` 结构、`need` 缺口、`opts[{i|sec,sc,tag,why}]`、`best`、`skip`/`buy`、`bar`、`heal`/`up`。是排序提示，不是胜率；地图仍走 `route`，事件仍读原文 |

## 意图与机制

- `it`：`{t:"A",d:11,h:1,tot:11}`；`A` 攻击、`D` 防御、`B` buff、`W` debuff、`S` 塞状态牌；`tot` 已含敌方力量与玩家易伤。
- `nx`/`nx_tot`：意图历史+周期推的下一手（`A9x2`）与其伤害（18）；首次遇怪没 `nx` 就看 `hist` 与 `mech`，按最坏留甲。
- `mech`：**DB 驱动**（`tools/mech.py` 从 `db/sts2.db`+`tools/data/eng/monsters.json` 生成 `db/mech.json`）：手写攻略 + `招：紧绕藤蔓9A+W｜挥击7A｜大啃18A`（招式名+A10 伤害+意图）。没攻略的怪也至少给招式表。
- `bd`：怪物身上非基础 buff 的一句话机制（`荆棘3=多段反伤`、`缓慢=…`）；`b` 只给名字。
- `cm`：`na`=下一张攻击 0 费、`ns`=下一张技能 0 费、`sk0`=技能全 0 费、`per`=每打一张攻击 -1 费。

## 数字口径

- `calc.dmg`：`sim.search(max_cards=8, max_plans=5)` 的 `maxdmg` 线，含易伤/力量/变费，**不含随机抽牌收益**；覆盖最多 10 张手牌里的第 8 张。
- `calc.ttk`/`ttk_all`：`ceil((hp+blk)/dmg)`，是「按这回合上限每回合都这么打」的乐观值。
- `calc.nxdraw`：`{n,blk,dmg,pool,mix,uncertain,why[,short]}`；抽牌堆 ≥5 按整堆组成估 5 张，<5 时先抽光再按弃牌堆平均质量补；`uncertain:1` 表示**期望值、不是牌堆顶**。
- `plans` 不假设抽牌收益；抽牌后未知牌用 `mid` 分两步打（见 `docs/DRAW_BOUNDARY.md`）。
- `plans` 五类都看：`best` 综合、`kill` 斩杀、`guard` 全挡、`potion` 药水线、`maxdmg` 纯伤害上限；`maxdmg` 的 `risk=1` 是自杀线，只能当上限。

## 维护

改 `tools/ctx.py` 后跑 `python tools/ctx.py --out tmp_ctx.json` 在真实战斗里验一遍；
`db/mech.json` 由 `python tools/mech.py --rebuild` 重建（db 更新后必须重建）。
