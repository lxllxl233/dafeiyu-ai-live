# 我的世界（minecraft）

第四个游戏包，也是第一个「先定协议、再等真机模组」的包：**没有 Minecraft 也能
完整验收** observe / validate / execute / autoplay，真机只要按同一份协议实现桥。

- 只通过本地桥 `mc-bridge/v1` 玩：`GET /state` 读状态、`POST /act` 发动作；
- **不找窗口、不切前台、不发键盘**：游戏可以最小化在后台跑；
- 没装桥时用内置仿真世界（`source=sim`）跑通链路，不会假装成真游戏；
- 想连真机就装游戏侧桥，并把 `MINECRAFT_BRIDGE` 设成 `http`。

## 跑一段

```powershell
# 1) 不需要 Minecraft：直接跑仿真世界（默认 auto，连不上 HTTP 就用 sim）
python main.py play --game minecraft --mode local
python main.py play --game minecraft --mode llm     # 需要 API Key

# 2) 想确认「HTTP 桥」这条路：先起一个仿真 HTTP 壳
python games/minecraft/bridge/server.py --port 8092
# 另开一个终端：
$env:MINECRAFT_BRIDGE = "http"
python main.py game state --game minecraft
python main.py play --game minecraft --mode local
```

`--mode llm` 想要的是**本机真游戏**：默认 `auto` 连不上桥时会先尝试拉起 Minecraft，
等不到就停手并提示启动 Fabric；不会再静默跑仿真。确实要拿仿真验收 LLM 时显式设
`$env:MINECRAFT_BRIDGE = "sim"`。

桌宠右键把「游戏」切到「我的世界」，再点「让她玩」也是一样的；控制台「当前游戏 →
开局设置」可以改难度（下一局生效）。

## 目录

| 文件 | 职责 |
|---|---|
| `game.json` | 清单：能力、入口、桥、`live`、开局设置、录像策略 |
| `plugin.py` | `McPlugin`：adapter / tools / runner / prompt |
| `adapter.py` | 真机桥状态 → `GAME_STATE`（只认 `source=http`） |
| `engine.py` | `MinecraftTools`：observe / validate / execute（只走桥） |
| `rules.py` | 生存规则参考实现：配方、工具等级、昼夜/饥饿/僵尸、`apply_step` |
| `compat/mc.py` | 桥客户端：`http` / `sim` / `auto` 三种来源 |
| `bridge/sim.py` | 离线仿真世界（持久化到 `logs/minecraft_state.json`） |
| `bridge/server.py` | 把仿真世界包成 HTTP 桥，手工验收用 |
| `runner.py` | 自动玩循环：`local` 开荒策略，`llm` 定期问模型 |
| `prompts.py` | 给模型的驾驶提示词（动作协议 + 关键配方 + 硬规则） |
| `knowledge/OS.md` | 完整开荒手册（第一天时间轴、配方、常见错误） |
| `persona/layer.md` | 游戏性格层 |
| `game-skill/obs.json` | OBS 场景/采集源命名 |

## 桥协议（`mc-bridge/v1`）

默认地址 `http://127.0.0.1:8092`，可用 `MINECRAFT_API` 覆盖。

### `GET /state`

```json
{
  "ok": true, "ready": true, "api": 1, "bridge": "mc-bridge/v1",
  "screen": "playing",
  "world": {"name": "生存·第 1 天", "time": 96.0, "day": 1, "phase": "day",
            "weather": "clear", "dimension": "overworld", "difficulty": "normal"},
  "player": {"name": "阿肥", "x": 3.0, "y": 64.0, "z": 2.0,
             "hp": 20, "max_hp": 20, "food": 18, "level": 0, "xp": 0,
             "armor": 0, "held": "wooden_pickaxe",
             "inventory": {"oak_log": 3, "oak_planks": 4, "bread": 2},
             "shelter": 0.0, "dead": false},
  "blocks": [{"id": "b7", "name": "oak_log", "kind": "tree",
              "x": 4, "y": 64, "z": -6}],
  "mobs": [{"id": "m1", "name": "cow", "hostile": false, "hp": 10,
            "max_hp": 10, "attack": 0, "x": 5.0, "y": 64.0, "z": -4.0}],
  "flags": {"crafting_table": true, "furnace": false},
  "stats": {"mined": 3, "placed": 1, "crafted": 2, "kills": 0,
            "deaths": 0, "actions": 6},
  "last": "合成 4×橡木木板"
}
```

- `screen`：`playing` / `paused` / `dead` / `menu`；
- `phase`：`day` / `dusk` / `night`，`clock` 由 `time` 换算（0 秒 = 06:00）；
- `blocks` / `mobs` 只需要给玩家附近的一批，Python 端会再按距离裁剪；
- `source` 由 Python 端按实际来源补上（`http` / `sim`），桥自己不用写。

### `POST /act`

一个请求一个动作；成功 `{"ok": true, "message": "..."}`，失败
`{"ok": false, "error": "..."}`（HTTP 400 也可以，客户端两种都认）。

| 动作 | 字段 | 说明 |
|---|---|---|
| `goto` | `target` 或 `x/y/z` | 走到树/石头/煤/铁/家/生物旁边 |
| `mine` | `block`、`count` 1–8 | 破坏方块，按规则掉落 |
| `craft` | `item`、`count` 1–4 | 合成；要工作台的配方必须先放下工作台 |
| `smelt` | `item`、`count` 1–4 | 熔炼；要熔炉和煤 |
| `place` | `block` | 放置方块；圆石/木板等实心方块加 `shelter` |
| `attack` | `target` = `nearest` / 生物名 | 打 6 格内的目标 |
| `eat` | `item` | 吃东西，饱食度没满才允许 |
| `equip` | `item` | 换手持（工具/武器） |
| `wait` | `seconds` 0.2–10 | 原地等 |
| `start` | `difficulty` | 开始/继续/重生 |
| `stop` | — | 暂停（仿真里等价于暂停世界） |
| `reset` | `difficulty` | 重新生成一个世界（仅仿真） |

Python 端在发动作前会用 `rules.apply_step` 在一份状态副本上验算：
配方、材料、工具等级、距离、饱食度都对得上才发；发出去后被桥拒绝会记
`minecraft_action_error`，不会静默重试。

## 动作协议（LLM 输出）

```json
{"seq": [
  {"type": "mine", "block": "oak_log", "count": 3},
  {"type": "craft", "item": "oak_planks", "count": 1},
  {"type": "craft", "item": "crafting_table", "count": 1},
  {"type": "place", "block": "crafting_table"}
]}
```

- 一次最多 8 步，等待总时长不超过 20 秒；
- 模型只认 `prompts.py` 里列出的字段；越界动作（`sleep`、命令行、聊天）会被拒绝；
- 模型超时/输出不可执行时记 `llm_fallback`，当步降级成本地开荒策略。

## 真机桥：Fabric 客户端模组 `McBridge`

游戏侧现在有真正的实现：`compat/mod/McBridge/`（Java，Fabric Loader 0.20.1 + 26.2，
Mojang 官方映射）。它只在本机 `127.0.0.1:8092` 暴露同一份协议，直接读你**当前角色**的
坐标/血量/背包/附近方块，并把 `goto/mine/attack/place/eat/equip` 翻译成游戏自己的
按键状态和交互调用——不找窗口、不注入键鼠。

```powershell
# 构建（JDK 25 用商店版启动器自带的即可；仓库自带 Gradle wrapper）
cd games/minecraft/compat/mod/McBridge
$env:JAVA_HOME = "$env:APPDATA\.minecraft\runtime\java-runtime-epsilon"
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
.\gradlew.bat --no-daemon build

# 装 Fabric Loader 0.20.1（26.2）+ 把 build/libs/mcbridge-0.1.0.jar 放进 mods/
# 启动游戏、进世界，然后：
$env:MINECRAFT_BRIDGE = "http"
python main.py game state --game minecraft   # source=http, ready=true
```

`craft` 已经接入真机（打开背包/工作台，按配方书放置材料并取产物）；`smelt` 还需要
驱动熔炉界面，暂时明确返回 `ok:false`，`reset` 同理（真机不能替你重置世界）。细节见
`compat/mod/McBridge/README.md`。

### 模组接口原则

1. 只监听 `127.0.0.1`，默认 `8092`，不开放公网；
2. 不读存档、不改存档，动作等价于玩家自己按键；
3. 做不到的动作返回 `ok:false` + 原因，不要静默成功；
4. 寻路（`goto`）是模组里的直线走+卡住跳，不是完整 A*；复杂地形会失败并返回原因。

## 已知边界

- 仿真世界是「参考实现」，不是 Minecraft 本体：没有地形生成、没有红石、没有联机；
- `python main.py games` / `game state` 只认真机桥（`source=http`）；仿真世界只在
  自动玩和工具里用，不会把包标成「在线」。想强制只看仿真：
  `$env:MINECRAFT_BRIDGE = "sim"`；
- 真机模组 `McBridge` 已实现移动/挖掘/攻击/放置/吃/切物品/合成；熔炼未实现，
  真机跑 `local` 会跳过熔炼（生肉直接吃、铁先不烧），合成会走工作台/配方书；
- 仿真世界每天会补 1–2 头牛，保证 7 天长局有食物；真机没有这个补牛逻辑；
- 模组是 26.2 + Fabric，换版本要改 `compat/mod/McBridge/gradle.properties` 重编；
- 自动玩是高层动作（砍树/合成/放方块），不做逐帧操作、不做 PVP；
- `game launch` 只会拉起官方启动器（`minecraft://`），不会替你选世界；
- 录像按 `recording.split=run` 一局一段，`dead` 时收尾。
