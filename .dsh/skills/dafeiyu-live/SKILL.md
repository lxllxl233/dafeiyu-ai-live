---
name: dafeiyu-live
description: 操作《大肥鱼直播姬》这套 Python 直播工具：起桌宠/控制台、切语音与游戏、只启动游戏、让她自动玩、跑 LangGraph Agent 流程、看本机轨迹。游戏内决策仍加载 sts2-ironclad-ironclad，说话仍加载 sts2-caster。
---

# 大肥鱼直播姬 · Harness 操作卡

这个仓库是 **DeepSeek Harness 驱动的 Python 应用**：外层 Agent 负责决策/说话，
内核只做规则、节奏、TTS、overlay 和游戏适配。不要在内核里再写一个隐藏 LLM 循环。

## 起服务

```powershell
python main.py                 # 桌宠
python main.py studio          # overlay + 控制台 http://127.0.0.1:8730/studio
python main.py game launch     # 只按 config.game 启动（STS2 = Steam 2868840）
python main.py play            # 启动 + 让她自动跑；Ctrl+C 停
python main.py play --mode llm # 战斗回合交给 LLM（需 API Key）
python main.py play --stop     # 跨进程停手
python main.py doctor          # 环境自检
```

## Agent 流程

```powershell
python main.py agent run --demo    # 离线图，不碰游戏
python main.py agent run           # 真实 ctx + LLM，只 dry-run
python main.py agent run --plan p.json  # 外部计划（保留技能决策）
python main.py agent trace --limit 5
```

图：`observe → plan → validate → execute → reflect`；验算失败回 `plan`。
轨迹在 `logs/agent_trace.jsonl`，控制台「Agent 流程」可看；不存 API Key。

## 让她玩

右键「让她玩」二级菜单：本地算账 / LLM 规划 / 慢速播报 / 普通节奏；「让她停手」单独一项。
LLM 模式只把战斗回合交给 LangGraph `run_turn`，默认模型 `deepseek-chat`；地图/奖励仍本地。
规划/验算失败本回合自动用本地兜底，执行失败才停手；GAME_OVER 会回主菜单再开一局。默认慢速播报按台词长度等语音，至少 2 秒一步；普通节奏是旧版 0.7 秒。整局循环在
`src/dafeiyu/agent/play.py`，状态 `logs/autoplay.json`，步骤 `logs/autoplay.jsonl`，
停手文件 `logs/autoplay.stop`。只处理已知界面；未知界面会停下，不猜按钮。

## 边界

- 游戏内决策：加载 `sts2-ironclad-ironclad`；说话：`sts2-caster`。
- 出牌走 `tools/act.py` 同款 batch 协议（自动玩法用 `sts2.cmd_batch`）；读局面只走 `tools/ctx.py`。
- API Key 只在本机 `config.json`，绝不回显/提交/写日志。
- 右键菜单是 Win32 弹窗，新增项要保留已有 ID。
