---
name: sts2-ironclad-ironclad
description: 《杀戮尖塔2》铁甲战士A10决策OS v22：每回合读tools/ctx.py局面包（实费/牌堆/遗物/药水/buff/意图/机制/斩杀），输出say+seq给tools/act.py；杂怪auto。路线references/playbook.md，卡表cards.md、敌人表enemies.md。
---

# 铁甲战士A10 · 决策OS v22（结构化输入/输出/药水牌序/回执）

> 常驻只本文件；references触发时read。

## 0. 传输协议：一回合=1次 `ctx` +1次 `act`

```
python tools/ctx.py    # 进战斗/换界面/auto停/拿不准时读
python tools/act.py --file tmp_plan.json    # 执行seq，回下回合ctx
```

- 一回合1请求：`act` 返回的 `ctx` =下回合局面包，直接用。

```json
{"say":["它16，痛击带走。","另一只吃尸体，没人打我。"],"seq":[{"c":"BASH","t":0},{"c":"STRIKE_IRONCLAD","t":0},{"end":1}],"fx":{"e":1,"hp":59}}
```

- `c`：`card_id`，不写下标；升级写STRIKE_IRONCLAD+；同名多张默认最便宜/最强。`t`：敌下标，可省（默认最威胁活怪）。`p`：药水槽，targeted必须给t。
- `end`：最后一步；不写 `end` =打到一半就停，回mid:1，抽牌后读ctx。
- `say`：1–3句，第一句随本回合播、其余排队，写法见sts2-caster。`fx`：可选自报账，e/hp/take硬校验，不符整手不打；报错时err给实际值，照抄重发。
- 省请求：杂怪（非精英/Boss、血安全）加{"auto":{"turns":3,"stop_hp":40}}本地连打，血线到/战斗结束/进选牌停；一场1请求。
- {"ok":false,...}：一张没打或打一半STOP；用返回 `ctx` 改 `seq` 重发，绝不自补 `end_turn`。
- 护栏（`act` 硬拦）：①漏斩=有可校验全斩线却没斩；②漏费=结束还剩能量且手牌能打。按 `err` 改 `seq`；确认换血/留费，`plan` 顶层写{"allow_take":1}/{"allow_leftover":1}/{"allow_no_lethal":1}并说理由。
- 换血线（提示不拦）：净挨≥6、有甲牌可顶但费已花光=攻击换防御，非漏甲；`logstats.py cases --kind miss_block` 可查。只报一行「净挨X、能顶Y、少打一张」；换血在say说理由，不换重排；真「有费不起甲」归漏费拦。
- `hand_n`=真实手牌数（`hand[]` 同名牌合并，只看条数以为牌少）；`stale_ctx`=新回合未抽完牌，必须重读 `ctx.py`。
- 遗言告警：敌人带「蒸汽喷发/爆炸/亡语」时斩杀≠安全——死后按层数结算一发（可格挡/缓冲）。`act` 在「清场且0甲」给 `warn:遗言警告`，看到就查有无甲或缓冲。
- SL不改抽牌顺序（`save_and_quit`→continue后手牌一样），只救操作失误，每幕≤2次；商店每店只删1张；药水栏满时 `claim_reward` 静默成功不给药水（先看 `potions` 槽，满则 `discard_potion`）。
- 「从牌组移除N张」多选点不动（blocker，docs/TODO_mod_deck_remove.md）：max_select=0、select_deck_card只回pending→卡死；别选，卡住就save_and_quit→continue回滚。
- 诅咒两种：进阶之灾（虚无）每回合手牌−1；贪婪（永恒）删不掉，商店删牌列表不出现；别把「删诅咒」写进计划。
- 只堆甲死于消耗战：甲牌≥45%必须配「非基础高伤攻击≥2张」，python tools/gates.py同查。

## 0.4 算账口径（1104条账本）

python tools/logstats.py replay暴露的四类算错已进代码+回归（tools/test_regressions.py）：

|机制|正确口径|曾错|
|---|---|---|
|状态牌（毒素等）|T/C类型牌不造成攻击伤害；毒素5点=回合末还在手才掉自己5血（可格挡）|把引擎Damage当攻击伤害，多算5–10点|
|势不可当|每获一次格挡→随机敌人补N点；按当前目标/首只活怪模拟=下界|没算，少6–8点/回合|
|延迟给费|「回合开始/被消耗时」给的能量本回合不算（薪火之源、战鼓）|当场到账→多算2费|
|滑溜SLIPPERY|下次掉血只掉1，之后正常|一刀算满伤→误判能斩|

执行纪律：一手≥3张牌、或有势不可当/覆甲/免费牌遗物→先python tools/act.py --dry看每步账（不耗回合）；账本24条「口算超支，第N步没费」都这么丢的。

## 0.5 输入：ctx v3字段

|字段|内容|
|---|---|
|me|hp/max/blk/e/em/stars/buffs[]（力量/敏捷/覆甲等）|
|relics/potions|遗物[{i,id,n,fx,st}]；药水i/id/n/d/t/use/st+nxt|
|hand|实费c+张数q+t+dmg/blk/draw/vuln/weak/aoe/x/exh/cm/add/pw/play；kw词条、tx机制原文、nxt下一轮|
|enemies|i/id/n/hp/max/blk/b/bd/it/nx/nx_tot/hist/mech|
|piles|draw/dis/exh：张数+组成|
|calc|inc/blk/net/full/lethal/chain/aoe/tax + dmg/ttk/ttk_all/nx_inc/nxdraw|
|can/plans/warn|详见references/ctx_fields.md|

- mech=DB机制原文｜bd=当前buff机制｜it.tot含力量/易伤。
- calc.dmg本回合上限，ttk还几回合杀，nx_inc下回合入伤，nxdraw下一手期望甲/伤；意图码、cm变费、plans五类/risk→references/ctx_fields.md。

## 0.6 输出：牌序即资源顺序

```
0费/回能/抽牌/变费牌 → 易伤/力量 → 高伤攻击 → 药水补费/补刀/保命 → end
```

1. 变费：UNRELENTING后最大攻击0费；STOMP最后；CORRUPTION技能全0费；BLOODLETTING/OFFERING先打再付大牌。
2. 药水：补能在攻击前，斩杀药最后补刀，格挡/回血在end前；targeted写t，否则invalid_target。
3. 抽牌/烧牌：POMMEL_STRIKE/BATTLE_TRANCE/OFFERING先打，斩杀线别建在随机抽牌上；POWER_THROUGH先算2张伤口税；FEEL_NO_PAIN/DARK_EMBRACE烧牌前打。
4. 能量纪律：结束e=0或写明「不可打/零收益」；0费攻击必打；同id打最便宜/最强。
5. 铺下一轮：hand[].nxt/can.buff是「这回合铺、下回合收」入口；斩杀回合不铺。

## 0.7 每回合七步

```
1 读warn+me.hp/blk/e+potions（有药先过四问）
2 读enemies：mech判机制；it.d×h（×力量×虚弱）=每怪本轮入伤，加总核对calc.inc；nx预判下一手，hist找周期
3 读calc：inc/blk/net/full/lethal/chain/aoe/tax；算「杀→晕」与两回合账（看hand[].nxt/can.buff）
4 定类型：斩杀｜全挡/喝药｜≤10%最大生命挡满｜换血（净掉≤5）｜铺场
5 选线：kill→stun>AOE清场>全挡>换血>铺场；plans全看，maxdmg的risk=1只作上限
6 写seq（牌+药水按资源顺序），能自报就写fx
7 收尾三查：e=0/N｜无能打的格挡牌｜无0费/回合末扣血牌
8 空费诊断：e>0且只剩状态牌/不可打牌=牌组攻击牌<6（记deck_issue），下店先补每费≥8的牌再删牌
```

## 0.8 记忆分层

- L1 sts2.py memory --write：开局/换幕/新session/每4–5层。
- L2 每回合ctx.py（`rules`=5条硬闸，源 `games/slay-the-spire-2/decision/hard_rules.py`）；下回合用act.py返回值，不state。
- L3 journal --floor N --last 20复盘用；logs/run_*.jsonl/战报不进上下文；每幕memory --write后开新会话。

## 1. 三条铁律

1. 先算死：calc.inc vs me.hp+calc.blk+药水；inc≥hp→全挡/斩杀/喝药，禁半挡半打。
2. 小伤害必挡：≤8挡满；9–14吃满calc.blk，差额是换血额度；≥15全挡/斩杀/喝药。
3. 怪不打伤害就少起防：it.t无A（沉睡/纯buff/debuff/塞牌）→0甲全输出；calc.inc==0时warn提示可少起防。甲下回合清零，只在本回合被打时值钱。
4. 回合账：calc.dmg本回合上限，calc.ttk还几回合死；nx_inc下回合入伤；ttk_all>1且nx_inc≥hp→全挡或灌伤害，别折中。

## 2. 准入闸

- 血闸：<50%只接营火/事件｜<60%不进精英（召唤型70%）｜<70%不进Boss；唯一出口=精英先养血到60%。
- 倒推闸（每层算）：出幕血=HP−剩余必经战×8+营火×0.3×max；到Boss<70%→下节点只走营火/商店，拒精英/加牌。出度1指向精英=必打，按到那层的血预判，不够第1层避开。
- 多怪闸：can.aoe=0且敌≥3→不进精英。
- 换血闸：花血换relic/金币先答「Act1内回本吗」：+1最大生命/场最多回5–8，不值14点现血。

## 3. 数值与斩杀

```
单卡伤害=hand[].dmg（已含力量/虚弱/遗物）
修正：敌易伤×1.5｜我虚弱×0.75｜我脆弱 格挡×0.75｜敌格挡先扣｜胆小首击+6/7｜荆棘每段反伤
敌减伤/封顶：振翅×0.5｜无实体=每段1｜难以杀灭=每段封顶9｜人工制品先吃1次debuff
多段：每段独立吃格挡/胆小/荆棘（飞剑3×3、双发5×2乘段数）
AOE 清算：总量A vs 全场血H；A≥H清场；A<H收最危险1–2只；N=1不用AOE
```

- 每费价值：飞剑+ 12｜耸肩+ 11｜飞剑 9｜余烬 9｜上勾拳 6.5｜打击 6｜防御 5。
- 状态牌：感染 3 伤、燃烧 2 伤、剧毒 5 伤、呼唤 6 伤（无视格挡）进calc.tax；可格挡部分与incoming共用池；黏液1费抽1，Wound无回合末伤害。
- 自伤四问（放血/祭品/血墙/突破）：2–6血换的能量本回合能斩杀/挡≥自伤吗？HP≤40%不交；有别的回能不交。跨费先看plans[].d/calc.lethal，别用旧斩杀线。

## 4. 多怪/机制

目标优先级：召唤者/仪式>高攻>可斩的低血>小怪；能斩召唤者忽略小怪。

杀→晕：calc.chain已算「plans[].s杀谁→谁晕→省多少（save）」。饥饿怪：杀同伴才晕；多只饥饿杀任意同伴全晕，taken已扣。爪牙：主人死→撤；2回合内能杀本体直杀。

其余机制：眩晕窗口留爆发；荆棘先叠甲再多段；滑溜用多段；无实体先易伤；人工制品先用低价值debuff破；懒惰/缠结/限费（蛇行扼杀者）按最坏重算；死亡召唤（寄生蛙/活雾/雾魔）压到15–25等AOE。

聚焦vs分散：总血>本回合伤害→聚焦下回合能杀且威胁最高那只；AOE只补刀或破滑溜/无实体/人工制品。两回合账：打完plans[].hp、下回合入伤I′；H′≤I′→挡满或斩杀；否则选「本回合净掉+下回合预期」最小线；别为等晕空过。

## 5. 血预算/药水/SL

- 道中预算：Act1前6层≤5 血/场、整段≤20；Boss前≥60%；燃烧之血战后+6。
- 药水四问：解什么｜槽满留不留｜会被打穿吗｜留给下场值不值。交：挡不住且净掉≥10%最大生命｜斩杀差5–15｜Boss大攻击；单体/群体先核对；Boss不省药，剩≥1瓶=失误。
- SL：只在「全挡/斩杀/喝药」全不成立时用；常规战 ≤3 次，Boss不占额度；流程save_and_quit→continue_run→重读ctx。

## 6. 路线/非战斗

- 地图：ctx.choice.opts→route --commit <i> --note "…"→每节点plan对账（PLAN_OK/REPLAN）。低血优先级：营火>问号>商店>怪>精英。
- 非战斗：读choice.eval的need/best/skip/buy/bar/why（提示非胜率）；冲突按牌面/手册再do --option i；事件读原文，地图走route；--full复盘。
- REPLAN：血线<70%｜掉血≥20%最大｜拿首张AOE｜金币100+｜牌组+3｜多诅咒。金币只删牌；诅咒局拒全金币换牌。

## 7. 构筑

|指标|目标|红线|
|---|---|---|
|张数|Act1 14–18，Act2≤22|>24抽不到组合；Act1第10层起18 张封顶|
|打击|≤3|4张进Act2=稀释|
|防御|Act2前≥6；精英前8+甲≥2张|单回合格挡<精英入伤=必输|
|AOE|1–2 张|0AOE=多怪磨死|
|收尾|1张（重锤/契约终结）|无收尾=打不死Boss|
|运转|Act2前4 + 2回费|只有一套启动=死|

只补缺口：AOE>主轴>防御>运转>收尾；≥14张只拿A级；奖励看cards的dmg/blk/draw/aoe。

## 8. 精英 / Boss

```
T≈敌血÷实际DPS        N≈敌入伤−稳定可挡
T×N>当前血量×0.6→不打
```

- Act1 Boss：199血÷15–18伤=11–13回合，Boss19–33伤/回合；不够别进。
- 斩杀排程：定第T回合杀；该回合HP+甲≥爆炸/DeathBlow，不够就留1血、先叠甲再杀。
- Act2 Boss：399÷25–35=12–16回合+每3回合一枚Debuff→要≥100血+20+甲/回合+≤22张。
- Boss前：HP≥70%、2张8+甲、牌组≤18（Act2≤22）、金币≤20、药水先验过。精英闸：60%血+2张8+甲+1张AOE+1瓶药（召唤型70%+AOE能量预算）。

## 9. 工具坑

|坑|做法|
|---|---|
|act手牌只剩1–2张|抽牌过渡态：ctx.py复核再决策|
|use_potion pending|药水先进CARD_SELECTION，select_deck_card后生效|
|费用误算|回能/变费牌先打；act逐步验算，不整手预扣|
|mid:1且e=0|回合已结束：读下回合ctx；补end会被拒|
|卡牌奖励|`sts2.py rewards`（默认跳卡）或`--take i`＝choose_reward_card --option i；一条命令收完金币+卡+collect，别分4回合|
|台词lint|python tools/check_caster.py --say "…"|

## 10. 反模式（见到就停）

㉟不读ctx凭记忆出牌 ㊱seq写下标 ㊲ok:false后自己补end ㊳无视calc.chain的免费晕
①多怪只看一只 ②半挡半打 ③差1–2点没收 ④按旧斩杀线打 ⑤H′≤I′还换血
⑥非斩杀回合全输出 ⑦有格挡牌却弃盾 ⑧对荆棘/高甲硬砍 ㉔7–10入伤硬吃 ㉕药水带满不喝
⑩奖励全拿 ⑪删错牌 ⑫营火升打击/防御 ⑬三条轴混抓 ㉙大店只删1张又加2张
⑭不算总账进精英/Boss ⑯凭记忆点下标 ⑱有费空过 ㉓play失败直接end_turn ㉖use_potion漏--target ㉜0费攻击留手里 ㉝无收益自伤 ㉞血换relic不回本

## 11. 收尾

1. obsctl stop→改名recordings/sts2_ironclad_a10_run<N>_<result>_floor<M>.mp4
2. sts2.py report→logs/run<N>_review.md；memory --write落终局
3. docs/LESSONS.md追加+教训压回本文件+python tools/check_skill.py

## 12. 本季教训（全文见references/lessons.md）

1. 金币先补牌再删牌：攻击牌<6张时每回合空1–2费，先买每费≥8的牌再删打击。
2. 召唤/塞牌怪先算5回合能否杀，杀不了早交药/早撤；auto只给单只纯攻击杂怪。
3. 覆甲药水第2–3回合交>留到40%血；SL重开抽牌序列不变。
4. 牌组Act1≤16；狂宴+4最大、黑暗之血+12；Boss不省药，留1瓶格挡给爆炸回合。
5. 召唤型精英要70%血+AOE（寄生蛙 79 血）；A10二幕杂怪30–40/场，Act1出口≥60%。
6. 无引擎（力量/恶魔/大甲/DoT）必输A1 Boss 233血；Neow石板优先恶魔形态；清场步 pick:skip 否则自动加牌撑牌组。
