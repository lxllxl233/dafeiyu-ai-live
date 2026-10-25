# 杀戮尖塔 2 · 铁甲战士 A10 实况 AI · 开箱即用

让 AI agent 用**本地 mod HTTP API**（不靠截图识别）自己打《杀戮尖塔 2》铁甲战士 A10，同时 **OBS 录屏 + 弹幕解说**，每局自动出复盘。

这个仓库把「跑起来」需要的东西都装进来了：游戏侧 mod、客户端脚本、弹幕服务、卡/敌/遗物数据库、战术文档、两个 agent 技能（决策 + 说话）。

> 默认配置：Windows + 游戏 Steam 版 v0.107.1 + OBS Studio + Python 3.11+。
> 上手步骤见 **[QUICKSTART.md](QUICKSTART.md)**（10 分钟）。

---

## 它能干什么

- **结构化读状态**：`sts2.py state` 把游戏 JSON 压成决策用的行——`ANCHOR`（敌人数/入伤/能量/手牌）、`EFF`（每张牌对每个敌人的实际伤害，含易伤/格挡/胆小/荆棘/段数）、`BLOCK_POOL`（手牌能挡多少）、`INTENT_MEM`（意图循环记忆）。
- **按技能决策**：`.dsh/skills/sts2-ironclad-ironclad/`（决策 OS v10：三条铁律、每回合 8 步、斩杀公式、血预算、药水四问、路线与构筑红线、201 条规则库）。
- **说话**：`.dsh/skills/sts2-caster/`（憨鱼人设、三拍、三档梗库、台词 lint），通过 `--thought/--meme` 推到弹幕。
- **录屏**：`obsctl.py` 控制 OBS（场景/源/开始/停止/自检），录游戏画面 + 游戏音频。
- **复盘**：`sts2.py report` 把 JSONL 动作日志转 Markdown 战报；`docs/LESSONS.md` 累积每局教训。

## 目录结构

```text
sts2.py                 # mod API 客户端 / 状态压缩 / 动作 / 路线规划 / 战报
obsctl.py               # OBS WebSocket 控制（setup / start / stop / health / free）
overlay_server.py       # 弹幕服务 :8730（/comment /reset /state /health）
overlay/                # 弹幕页面（头像、气泡、动森式音效）
tools/                  # DB 构建、卡表/敌人表生成、梗库、skill/台词检查
db/sts2.db              # 626 张卡 / 127 敌人 / 311 遗物 / 65 药水 / 201 条策略规则
docs/                   # 战术手册（Act1/Act2-3、商店、营火、Neow、复盘模板…）
.dsh/skills/            # 两个 agent 技能
mod/                    # 游戏侧 STS2 AI Agent mod（已打 v0.107.1 补丁）
logs/                   # 战报与复盘（运行时生成）
recordings/             # 录像输出（运行时生成）
```

## 快速开始

见 **[QUICKSTART.md](QUICKSTART.md)**。最短路径：

```powershell
pip install -r requirements.txt
Copy-Item .\mod\* "<游戏目录>\mods\" -Force     # 装 mod
# 游戏用 --windowed 启动，进主菜单
python overlay_server.py                         # 弹幕服务
python obsctl.py setup                           # OBS 建场景/源
python obsctl.py start                           # 开录
```

然后把 [NEXT_PROMPT.txt](NEXT_PROMPT.txt) 交给 agent。

## 常用命令

```powershell
python sts2.py state                 # 压缩状态（每回合决策前必读）
python sts2.py acts                  # 当前合法动作
python sts2.py play BASH --target 0  # 按卡牌 ID 出牌
python sts2.py do play_card --card 2 --target 0 --thought "…" --meme "…"
python sts2.py do end_turn
python sts2.py route                 # 路线打分（含 HP/防御数/AOE/药水）
python sts2.py route --commit 2 --note "三营火线"
python sts2.py plan                  # 对账：PLAN_OK / REPLAN
python sts2.py report                # JSONL → Markdown 战报

python obsctl.py status|setup|start|stop|health|free|nomute
python tools/gamedb.py card 痛击     # 查卡/敌/遗物/药水/规则
python tools/gamedb.py strategy
python tools/memedb.py suggest --situation boss
python tools/check_skill.py          # 技能自检（体积/路径/旧卡名）
python tools/check_caster.py --say "它 21，我最多挡 15。差 6，嗨呀。"
```

环境变量：`STS2_API`（默认 `http://127.0.0.1:8080`）、`STS2_OVERLAY`（默认 `http://127.0.0.1:8730`）、`STS2_OVERLAY_PORT`（默认 8730）。

## 技能（skills）

| 技能 | 管什么 |
|---|---|
| `sts2-ironclad-ironclad` | **怎么打**：决策操作系统 v10。开局/每回合决策前加载 |
| `sts2-caster` | **怎么说**：实况说话系统 v2，三拍、五武器、三档梗库、禁语 |

技能放在仓库的 `.dsh/skills/` 下，**克隆下来就在位**。DSH 会自动发现；其它 agent 平台把对应目录复制到它的 skills 目录即可（目录名与 frontmatter `name` 必须一致，且只能是**小写字母/数字/短横线**）。

- 决策技能：`.dsh/skills/sts2-ironclad-ironclad/SKILL.md` + `references/{enemies,cards,potions,archetypes,builds_tier}.md`
- 说话技能：`.dsh/skills/sts2-caster/SKILL.md` + `references/{lines,quips}.md`

## 架构

```text
游戏（窗口化）
  └─ STS2 AI Agent mod（:8080）
        ↑ /state /actions/available /action
sts2.py（压缩状态 + 动作 + JSONL 战报）
overlay_server.py :8730 + overlay/index.html
        ↑ 浏览器源（透明弹幕；OBS 接管音频 → 动森音效进录像）
OBS Studio（场景 StS2：game_capture + 浏览器源 + 进程音频）
        └─ recordings/*.mp4
```

## 已知限制

- **必须窗口化**：全屏独占下 OBS 采集黑屏。
- **mod 版本绑定游戏版本**：`mod/STS2AIAgent.dll` 只对 v0.107.1 打过补丁；游戏升到 v0.111+ 需换 mod 并重测。
- **药水**：不同版本 `use_potion` 可能返回 `pending`；按技能里的四问与「重读 `is_queued`、最多重试 1 次」处理。
- **难度只到 A10**；A6 起删牌更贵，A10 是双 Boss。
- 数据库以 v0.107.1 为准，卡牌改版（2026-04-16 / 06-18）后部分数值会过期，以游戏内文本为准。

## 致谢与许可

- mod：**STS2 AI Agent**（作者 CharTyr）——`mod/` 内为其 Windows 构建（v0.107.1 兼容补丁版），版权归原作者。
- 游戏内容与卡牌文本版权归 **Mega Crit**；`db/` 数据来自公开 Wiki（slaythespire2.gg）与 mod 自带英文数据，仅作个人学习/实况用。
- 本仓库其余代码与文档：自用项目，未指定开源许可证；转载请注明来源。
