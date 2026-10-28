# 游戏兼容层 · 杀戮尖塔 2

把「游戏能读能操作」和「直播姬怎么说话」隔开。这一层只做两件事：

1. **读**：mod 本地 HTTP `127.0.0.1:8080` → `/state`、`/actions/available`（只读快照）
2. **写**：`/action` 一次一个动作（出牌、结束回合、选奖励……）

## 目录

```
compat/
├── sts2.py          # 客户端 CLI：状态压缩 / 动作 / 等待 / 战报 / 路线 / batch
├── mod/
│   ├── STS2AIAgent.dll   # 已打兼容补丁的 mod（原版在 backups/）
│   ├── STS2AIAgent.pck
│   ├── mod_id.json
│   └── INSTALL.md        # 安装/启用/验证
└── README.md
```

## 安装 mod

见 `mod/INSTALL.md`。要点：把 DLL 放进游戏 `mods/`，在 `settings.save` 里启用，
启动游戏后 `python sts2.py state` 应返回 JSON（ConnectionRefused = 游戏/引擎没起，不是坏了）。

## 兼容补丁

游戏 v0.107.1 上，v0.8.0 的 `IsPotionDiscardable` 会调用不存在的
`Player.CanUseOrRemovePotions`，导致 `/state` 500。已把该调用改成返回 true
（`pop; ldc.i4.1`），原 DLL 备份在 `backups/STS2AIAgent.dll.orig`。

## 常用命令（仓库根执行，`sts2.py` 是兼容入口）

```powershell
python sts2.py state              # 压缩状态（增量）
python sts2.py state --full       # 全量状态
python sts2.py acts               # 当前合法动作
python sts2.py do play_card --card 0 --target 0 --thought "..." --meme "..."
python sts2.py batch --file spec.json
python tools/ctx.py               # 结构化局面包（决策层用）
python tools/act.py --file plan.json
python tools/live.py check        # 主播链路体检
```

CLI 的 `--thought/--meme` 会把台词推给 overlay；新链路也可以用
`python dafeiyu.py say --thought ... --meme ...`（多走一遍性格层 lint）。

## 状态结构（适配器读取的字段）

`/state` 返回的 `data` 里：

- `screen`：`MAIN_MENU` / `MAP` / `COMBAT` / `REWARD` / `SHOP` / `REST` / `EVENT` ...
- `in_combat`、`turn`
- `run.character_id` / `character_name` / `ascension` / `floor` / `current_hp` / `max_hp` /
  `gold` / `act_id` / `boss_id`

`src/dafeiyu/games/sts2/__init__.py` 只读这些字段生成 `GAME_STATE` 事件，不碰出牌逻辑。

## 边界

- 出牌/决策仍由兼容层 CLI + 决策 OS（`.dsh/skills/sts2-ironclad-ironclad`）负责。
- 适配器不会替你点「继续」，也不改游戏文件。
- mod 是第三方二进制，升级游戏后需要重新验证（见 `game.json` 的 `compat.checks`）。
