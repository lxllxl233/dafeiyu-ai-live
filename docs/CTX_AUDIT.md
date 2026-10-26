# ctx v3 战斗输入审计（决策前我看到的唯一结构化输入）

生成：`python tools/ctx.py --out tmp_ctx.json` → 我读这份 JSON → 写 `tmp_plan.json` → `tools/act.py` 执行并回下一份 ctx。
体积：战斗 1.2–2.2KB，非战斗 0.7–1.7KB。

## 1 战斗字段表

| 字段 | 含义 | 例 |
|---|---|---|
| `v / turn / screen` | 版本、回合、界面 | `3 / 5 / COMBAT` |
| `run{f,act,asc,gold,deck}` | 层、幕、进阶、金币、**牌组张数（无明细）** | `6,1,10,48,18` |
| `me{hp,max,blk,e,em,stars,buffs}` | 血/上限/甲/能量/上限/星/增益（**字符串**"力量7"） | `17,80,0,3,3` |
| `relics[{i,id,n,fx,st}]` | 遗物 + 效果 + 状态 | `st:"on"` / `{charges:2}` |
| `potions[{i,id,n,d,t,use,st,nxt}]` | 药水效果/目标/能不能用/跨回合 buff | `nxt:{dex:5}` |
| `hand[{id,n,c,t,dmg,hits,blk,vuln,nxt,cm,q,play}]` | 手牌**实费 + 全部效果**，同名合并 `q` | `STOMP c3 cm{per:1}` |
| `piles{draw,dis,exh}` | 三堆的 id 计数 | `draw{n:9,c:[[STRIKE,4]]}` |
| `enemies[{i,id,n,hp,max,blk,b,it,hist,nx,mech}]` | 每怪血/甲/buff/当前意图/历史/下一手/机制 | 见下 |
| `it{t,d,h,tot,sc}` | `t`=意图类型，`d`=单击伤，`h`=段数，`tot`=**本回合总入伤**，`sc`=塞牌数 | `{t:"A",d:19,tot:19}` |
| `calc{n,inc,blk,net,full,lethal,chain,aoe,tax}` | 合算：总入伤/最大可挡/漏伤/能否全挡/可斩目标/斩杀连锁/AOE 总和/状态牌税 | `{inc:19,blk:12,net:7,full:false}` |
| `can{vuln,weak,draw,en,cost,exh,add,buff,aoe}` | **能力清单**：能上易伤/虚弱、能抽牌/回能、变费牌、可消耗、可加牌、跨回合 buff、AOE | `draw{n:1,by:[POMMEL_STRIKE]}` |
| `plans[{k,s,d,take,hp,e,kill}]` | 本地搜索的前 3 套连招与结果 | `d:10,take:0,hp:17,kill:[0]` |
| `warn[]` | 规则告警 | `cannot_full_block(-7)`、`incoming>=hp` |

## 2 非战斗输入

统一塞在 `choice`：`map{opts[{i,t,r,c,to}]}` / `event{id,title,desc,opts[{n,d,locked,go}]}` / `reward{items,cards}` / `select{prompt,min,max,cards}` / `shop{gold,items[{sec,i,n,price,ok}],removal}` / `rest{opts}` / `chest{opts}` / `over{win,floor}`。另外 `deck[{id,n,c,t,q,up}]` 只在非战斗给全量。

## 3 真实样本（f6 小啃兽 T5，未删减）

```json
{"v":3,"turn":5,"screen":"COMBAT","run":{"f":6,"act":1,"asc":10,"gold":34,"deck":17},
 "me":{"hp":17,"max":80,"blk":0,"e":3,"em":3,"stars":0,"buffs":["力量","敏捷"]},
 "potions":[{"i":0,"id":"CURE_ALL","n":"痊愈药水","use":true,"st":"usable"}],
 "hand":[{"id":"DEFEND_IRONCLAD","c":1,"t":"S","blk":6,"q":2},
         {"id":"POMMEL_STRIKE","c":1,"t":"A","dmg":10,"hits":1,"draw":1}],
 "piles":{"draw":{"n":9,"c":[["STRIKE_IRONCLAD",4]]},"dis":{"n":3},"exh":{"n":2}},
 "enemies":[{"i":0,"id":"NIBBIT","n":"小啃兽","hp":9,"max":44,"blk":6,"b":["力量6"],
             "it":{"t":"A","d":19,"tot":19},"hist":["B","A16","B","A19"]}],
 "calc":{"n":1,"inc":19,"blk":12,"net":7,"full":false,"lethal":[0]},
 "can":{"draw":{"n":1,"by":[{"id":"POMMEL_STRIKE","n":1}]}},
 "plans":[{"k":"best","s":[["DEFEND_IRONCLAD",0],["POMMEL_STRIKE",0]],"d":10,"take":0,"hp":17,"e":0,"kill":[0]}],
 "warn":["hp<50%(17/80)","no_potion","cannot_full_block(-7)","incoming>=hp：必须全挡/斩杀/喝药"]}
```

## 4 缺失清单

### P0（直接影响胜负）
1. **`mech` 只覆盖 17/127 只怪**（13%）。`_mech()` 只认白名单 power（RAVENOUS/MINION/ARTIFACT/THORNS/SLIPPERY/INTANGIBLE/RITUAL/PLATING/STOCK），其余靠 `db.sts2.enemies.notes` 手写，17 条。小啃兽、史莱姆、雾菇本体等在输入里**没有任何机制信息**。
2. **`nx`（下一手）只有签名串**（`"A16"`），没有数值换算，也没有"下一回合我要吃多少"。所以"这回合不挡，下回合会不会死"只能我人肉推。
3. **`it.tot` 口径不一致**：游戏给了 `total_damage` 就用它（含力量/虚弱/玩家易伤）；没给时回落成 `d*h`，**不含力量**。同一字段两种口径 → 偶尔低估入伤。
4. `plans` 只 3 条、`max_cards=5`，且不含"先交药水"的完整计划；斩杀/AOE 冲突时要以 `calc` 为准，我经常要自己重算。

### P1（拖慢决策 / 容易错）
5. 没有 `dmg`（本回合我方最大伤害）与 `ttk`（每只怪还需几回合），要靠 `plans[0].d` 间接推。
6. `me.buffs` 是字符串（"力量7"），没有 `me.str/me.dex` 数值字段。
7. 战斗中 `run.deck` 只有张数，没有明细（`piles` 有 id 计数，够用但不含升级标记）。
8. 药水 `d` 字段残留 `res://...png`（如痊愈药水显示"获得res://…energy_icon.png。抽2张牌"）——`parse_potion` 没过 `_clean`。
9. 药水 `use:false` 不给原因（战斗中不可用 / 需要目标 / 已排队）。

### P2（体验）
10. 地图节点只有类型，没有"该节点之后能到营地/商店的距离"。
11. `hist` 只留 4 条、周期检测 p≤3，长战（>8 回合）的循环对手会丢。
12. 无"当前坐标"，要靠 `route` 缓存判断自己在第几列。

## 5 建议补丁（ctx.py）

1. `mech` 全量化：读 `tools/data/eng/monsters.json`（111 只，含 `moves` + `damage_values{normal,ascension}`），按 `asc` 生成
   `mech:"Swipe 9 / Headbutt 16（A10）；召唤利齿之眼"`；db 那 17 条手写 notes 作为前缀优先。
2. `nx` 数值化：新增 `nx_tot`（按当前力量、玩家易伤折算的下一回合入伤）+ `nx_t`（意图类型）。
3. 修 `it.tot` 回落：`tot = (d + str) * h`，再乘玩家易伤；同时给 `str` 数值。
4. `calc` 增 `dmg`（本回合最大）与 `ttk:{i:回合数}`。
5. `parse_potion.d` 过 `_clean`；`use:false` 补 `why`。
6. `me.str / me.dex` 数值化（仍保留 `buffs` 文本）。
7. 地图 `opts` 增 `after`（该节点子节点类型）与 `rest_dist / shop_dist`。

## 6 验证

- `python tools/ctx.py --pretty` 看字段齐全；
- 进一场杂怪，确认每只怪都有 `mech`，`nx_tot` 与 `calc.inc` 关系合理；
- `python tools/check_skill.py` 保持 `OK 0 warning(s)`。

## 7 落地状态（P0+P1 已改完，run69 实战验证）

| 项 | 状态 | 实现 |
|---|---|---|
| P0-1 `mech` 全量化 | ✅ | 新增 `tools/mech.py` → `db/mech.json`（enemies 127 / cards 626）：db `notes` + `powers_json` + `tools/data/eng/monsters.json` 的 `damage_values` 合成 `招：紧绕藤蔓9A+W｜挥击7A｜大啃18A`，只注入场上怪，≤130 字 |
| P0-2 `nx` 数值化 | ✅ | `nx_tot`（从 `nx` 签名解析，`A9x2`=18），汇总成 `calc.nx_inc`，未知怪标 `nx_part` |
| P0-3 `it.tot` 口径 | ✅ | 回落后 `tot=(d+敌力量)*h`；`it.d` 也加力量 |
| P0-4 plans 扩容 | ✅ | `max_cards=6, max_plans=6`，新增 `maxdmg` 线（纯伤害上限） |
| P1-5 `calc.dmg / ttk` | ✅ | `dmg=maxdmg`；`ttk={i:ceil((hp+blk)/dmg)}` + `ttk_all` |
| P1-8 药水 `d` 残渣 | ✅ | 过 `_clean`，`use:false` 补 `why` |
| 新增 6 `hand.kw/tx` | ✅ | `kw`=db 词条（EXH/ETH/UNP/ETR…）；`tx`=含机制关键词的原文 ≤42 字（踩踏「每打一张攻击 -1 费」） |
| 新增 5 遗物状态 | ✅ | 原 `relics[{fx,st}]` 已够，未改 |
| 新增 6 怪 buff 机制 | ✅ | `bd`=非基础 buff 一句话（`缓慢=每打一张牌受伤+10%`） |
| 新增 2 下回合抽牌 | ✅ | `calc.nxdraw{n,blk,dmg,pool,mix}`：抽牌堆组成算期望，<5 张时按洗牌比例 |
| 规则「怪不打伤害就少起防」 | ✅ | `warn` 增「本回合无入伤：可少起防」，SKILL 铁律 3 |

验证样本：VINE_SHAMBLER（mech 招式表 + `nx_tot` 周期）、BYGONE_EFFIGY（`bd` 缓慢）、STOMP（`tx` + `cm.per` → 两刀后 1 费）、PERFECTED_STRIKE（`tx` 说明 22 伤来源）。
仍未做：P2-10 地节点距离、P2-11 长战 `hist`、`me.str/dex` 数值字段。
