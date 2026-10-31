# 吸血鬼幸存者（vampire-survivors）

第三个游戏插件，也是「LLM 直播模式」的第一个实时动作类模块。

## 这个包能做什么

- 通过 Steam 启动游戏（`game.json` 里 `launch.app_id = 1794680`）；
- **纯本地 API 操作**：游戏侧 BepInEx 6 IL2CPP 桥在 `127.0.0.1:8091`
  暴露 `GET /state` 和 `POST /act`，Python 只走 HTTP；
- **不找窗口、不切前台、不发键盘**：游戏可以最小化/在后台跑，不会占用你的窗口；
- 桥会自己过警告页、主菜单、角色/关卡选择、升级、宝箱和结算，Python 只负责
  走位和（可选的）升级选择；
- 桥没连上时按离线处理，不会退化成键盘模拟。

## 跑一段

```powershell
python main.py game launch --game vampire-survivors   # 只启动
python main.py play --game vampire-survivors --mode local
python main.py play --game vampire-survivors --mode llm    # 需要 API Key
```

或者在桌宠右键菜单里把「游戏」切到「吸血鬼幸存者」，再点「让她玩」。

## OBS 录像

默认策略由 `game.json` 的 `recording` 块声明：**开启 + 一局一集**。控制台
「当前游戏 → OBS 录像」可以改成本机覆盖（`config.json` 的
`recording.games.vampire-survivors`），也可以关掉自动录像改用手动「开始录像」。

- 开启前会检查 `game-skill/obs.json`；没有就按游戏名创建，再补齐 OBS 场景/采集源；
- 一局一集：每次进入 `PLAYING` 开一段，`GAME_OVER` 收尾到
  `recordings/vampire-survivors/run-NNNN/run.mp4`；
- 不分集：连续录到 `recordings/vampire-survivors/continuous/`，停手才收尾；
- 右键「OBS 录像」与自动玩走同一套策略；关闭时会先停录并收尾。

## 目录

| 文件 | 职责 |
|---|---|
| `game.json` | 清单：能力、入口、Steam 启动、`live` 直播模块声明 |
| `plugin.py` | `VampireSurvivorsPlugin`：adapter/tools/runner/prompt |
| `adapter.py` | API 状态 → `GAME_STATE` |
| `engine.py` | `VampireSurvivorsTools`：observe / validate / execute（HTTP） |
| `compat/vs.py` | 本地 API 客户端（`GET /state`、`POST /act`） |
| `runner.py` | 自动玩循环；`local` 躲怪走位，`llm` 定期问模型 |
| `compat/mod/VsBridge/` | 游戏侧 BepInEx 6 IL2CPP 插件源码（C#） |
| `prompts.py` | 给模型的驾驶提示词 |

## 游戏侧桥（必须安装）

游戏是 Unity 6 / IL2CPP，需要 **BepInEx 6 bleeding-edge IL2CPP**：

1. 下载 `BepInEx-Unity.IL2CPP-win-x64`（be.788 或更新），解压到游戏根目录
   （`VampireSurvivors.exe` 旁边，出现 `BepInEx/`、`winhttp.dll`、
   `doorstop_config.ini`）；
2. 启动一次游戏，等 BepInEx 生成 `BepInEx/interop/`（第一次可能几分钟）；
3. 把 `BepInEx/core/*.dll` 和 `BepInEx/interop/*.dll` 复制到
   `games/vampire-survivors/compat/mod/refs/vs-refs/`（core 平铺，interop 放进
   `interop/`）；
4. 装 .NET 8 SDK，编译：
   ```powershell
   dotnet build games/vampire-survivors/compat/mod/VsBridge -c Release
   ```
5. 把 `compat/mod/VsBridge/bin/VsBridge.dll` 复制到
   `<游戏>/BepInEx/plugins/`，重启游戏。

`BepInEx` 是可逆的：删掉游戏根目录的 `BepInEx/`、`winhttp.dll`、
`doorstop_config.ini`、`.doorstop_version` 和 `dotnet/` 就恢复原样。
`refs/` 和 `bin/` 已在 `.gitignore` 里，不入库。

启动后 `BepInEx/LogOutput.log` 应出现：

```
[Info   : VS Bridge] VS bridge API listening on http://127.0.0.1:8091
```

## API 契约

`GET /state`：

```json
{
  "ok": true, "ready": true, "api": 1,
  "state": "playing",
  "automation": true,
  "player": {"x": 1.2, "y": -0.4, "hp": 90, "max_hp": 100, "level": 7,
             "xp": 12, "xp_to_next": 20, "minute": 3, "seconds": 181.2,
             "character": "ANTONIO", "stage": "FOREST", "kills": 123,
             "weapons": [{"id": "WHIP", "level": 3, "max": false}]},
  "enemies": [{"x": 0.5, "y": 1.0, "hp": 20, "type": "BAT3", "boss": false}],
  "gems": [], "pickups": [],
  "screen": {"half_w": 8.0, "half_h": 4.5},
  "menu": {"kind": "level_up", "title": "LevelUpPage",
           "options": [{"key": "1", "id": "FIREBALL", "name": "Fire Wand"}]},
  "run": {"active": true, "started": true}
}
```

`state` 取值：`playing` / `level_up` / `menu` / `game_over` / `paused` /
`unknown`。`menu.options` 同时用于升级、角色、关卡选择。

`POST /act`：

```json
{"type": "move", "dx": 1.0, "dy": 0.0, "seconds": 0.8, "choice": "right"}
{"type": "choose", "index": 0}
{"type": "control", "automation": true}
{"type": "start"}
{"type": "stop"}
{"type": "press", "key": "enter"}
```

- `move`：方向向量 + 持续秒数（0.05–3.0），桥在 `ProcessRawDirection` 里替换
  主玩家的输入；
- `choose`：升级/角色/关卡选项下标（0 起）；
- `start`：打开自动化，从当前页开始推进到一局；
- `stop`：关自动化、清零移动；
- `press`：仅 `enter/space` 和数字键有实际映射。

Python 客户端默认连 `http://127.0.0.1:8091`，可用环境变量覆盖：

```powershell
$env:VAMPIRE_SURVIVORS_API = "http://127.0.0.1:8091"
```

## 动作协议（LLM 输出）

```json
{"seq": [
  {"move": "right", "seconds": 0.9},
  {"move": "up", "seconds": 0.6},
  {"press": "1"}
]}
```

- 最多 8 步、总时长最多 12 秒；
- `move` 方向只认 up/down/left/right，`seconds` 0.05–3.0；
- `press` 1–5 映射到 `choose`，`enter/space/escape` 映射到 `press`；
- `wait` 0.05–2.0 秒，尽量少用。

## 已知边界

- 桥目前只支持单人；在线联机不驱动；
- 升级选择由 Python（本地第一项或模型）决定，桥只负责把选项列出来；
- 游戏更新后需要重新生成 `BepInEx/interop` 并重新编译插件；
- 桥的菜单流程参考了 MIT 的
  [jev_vampire_survivors](https://github.com/oldmoldycake/jev_vampire_survivors)
  和 [Off the Clock 的 BepInEx 6 IL2CPP 模板](https://github.com/OffTheClockMods/vampiresurvivors-mods)；
  游戏内不做画面识别、不改存档。
