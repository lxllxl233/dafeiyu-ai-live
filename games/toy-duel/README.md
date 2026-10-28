# 玩具决斗（toy-duel）

一个**完全离线、可控制**的第二游戏插件，用来验收「大肥鱼不是《杀戮尖塔 2》专属」：

- 不启动 Steam，不连 mod，不读 STS2 状态；
- 不调用 LLM，不需要 API Key；
- 不强制 OBS 录像或语音，只通过核心 `AutoplayHost` 写状态/日志/停止；
- 和 STS2 共用 `game.json` + `plugin.py` + `GameTools` + `AutoplayRunner` 契约。

## 跑一局

```powershell
python main.py play --game toy-duel --mode local --pace normal
# 另开一个终端停手（或等它自己跑完 12 步）
python main.py play --stop
```

也可以只观察：

```powershell
python main.py game state --game toy-duel
python main.py games
```

## 文件

| 文件 | 职责 |
|---|---|
| `game.json` | 清单：`observe/control/autoplay` 能力、入口、动作/状态 schema |
| `plugin.py` | `ToyPlugin`：把 adapter/tools/runner/prompt 暴露给核心 |
| `adapter.py` | 只读 `logs/toy_duel_state.json` → `GAME_STATE` |
| `engine.py` | `ToyTools`：observe / validate / execute；规则和状态都在这里 |
| `runner.py` | 12 步演示循环；每步都走同一套 `ToyTools` |
| `README.md` | 本文件 |

状态文件默认是 `logs/toy_duel_state.json`；测试/隔离运行可以设：

```powershell
$env:TOY_DUEL_STATE = "$PWD\tmp_toy_state.json"
```

## 动作协议

```json
{"seq": [{"play": "STRIKE"}, {"play": "GUARD"}, {"end": true}]}
```

- `STRIKE`：1 费，6 伤害；
- `GUARD`：1 费，5 格挡；
- `end`：结束回合，木桩反击 6 点；
- 手牌固定为两张打击 + 一张防御，每回合补满 3 能量。

`GameTools` 的返回值是 `dafeiyu.games.contracts.ActionResult`，不是旧式
`{ok, err}`；旧调用方仍可通过 `to_legacy()` 读取。

## 为什么需要它

STS2 的完整链路依赖 Steam、mod、OBS、语音和大量游戏语料。想验证「核心是否真的
游戏无关」时，这条链路太重，失败原因也难分离。`toy-duel` 把变量压到最少：

1. `python main.py games` 能看到第二个游戏；
2. `GameRegistry.load_plugin("toy-duel")` 返回完整插件；
3. `ToyTools.observe/validate/execute` 在同一状态上工作；
4. `AutoplayRunner.start/stop/status` 使用核心 host；
5. STS2 的代码一行都不用改。

它是**验收工具**，不是商业游戏接入示例；真实游戏仍按 README 的「加一个新游戏」
流程放 `adapter.py` / `compat/` / `corpus/`。
