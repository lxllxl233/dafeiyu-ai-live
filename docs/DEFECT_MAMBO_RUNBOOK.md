# 故障机器人 A0→A10 · 曼波主播模式 Runbook

> 用户只要说一句：**「以主播模式曼波配音机器人启动A0-A10」**
> 就按本文件从头跑到 A10 通关。别读 docs/ 全量，别翻历史 run 日志。

## 0. 前置检查（按顺序，缺什么起什么）

```powershell
cd D:\aigame\sljt\sljt
# 1) 曼波引擎（127.0.0.1:9880）没起就起：
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:9880/control   # 200 = 好
cd D:\MamboTTS\app\MamboTTS-v1.2.1-full; python run_engine.py          # 后台常驻
# 2) 弹幕服务 8730（后台）
cd D:\aigame\sljt\sljt; python main.py studio
# 3) 主播窗（后台，曼波配音默认；--capture 让直播姬的显示器捕获能抓到）
python main.py --capture
# 4) OBS：切游戏采集 + 自检
python main.py obs free
python main.py obs health
# 5) 游戏（Steam appid 2868840）
python tools/sts2.py state      # 连不上 8080 说明游戏没开
```

## 1. 一局的完整流程

```powershell
python main.py obs start                       # 从选人开始录
python tools/sts2.py do open_character_select
python tools/sts2.py do select_character --option 4 # 4 = DEFECT（故障机器人）
# 进阶等级：A<n> 用 decrease_ascension 调到 n（Defect 首次是 A0）
python tools/sts2.py do embark
python tools/sts2.py route                         # 选路 + --commit
```

- 战斗（v20 协议，一回合两次调用）：
  ```powershell
  python tools/ctx.py --out tmp_ctx.json      # 局面包
  # 写 tmp_plan.json：{"say":["..."],"seq":[{"c":"ZAP"},{"c":"DUALCAST","t":0},{"end":1}]}
  python tools/act.py --file tmp_plan.json    # 执行 + 回下一回合局面包
  ```
- 事件/奖励：`do choose_event_option --option i`、`do claim_reward --option i`、`do choose_reward_card --option i`
- 每幕结束：`python tools/sts2.py memory --write`，然后**开新会话**继续（最大的一笔省 token）

## 2. 升降阶规则（本模式的硬规则）

- **赢了 A<n> → 下一局 A<n+1>**（在选人界面用 `do decrease_ascension` 往上调，或 `increase_ascension` 若有）
- 输了：**同阶重开**，不降阶（不 SL、不读档）
- 目标：A0 → A10 全通；每局独立录像 + 战报
- 每局录像名：`recordings/sts2_defect_a<N>_run<M>_<win|lose>_floor<F>.mp4`

## 3. 配音（曼波）

- 每句 `thought + meme` 都会合成曼波语音：
  - 本地主播窗：`streamer.py`（winsound 播）
  - **OBS 浏览器源 `AI Commentary`：页面自己调 `/tts` 播 → 录像自带曼波配音**（源未静音，已验证）
- 引擎离线 → 静音，**不回落动物语**
- 可调：`STS2_TTS_URL` / `STS2_TTS_REF` / `STS2_TTS_SPEED`；窗口 `--vol`；`--voice animalese` 才用旧音效

## 4. 每局收尾

```powershell
python main.py obs stop        # 拿录像路径
# 改名成上面第 2 节的格式
python tools/sts2.py report        # JSONL → logs/run_*.md
python tools/sts2.py memory --write
```

复盘只写三件事：死因链、这局的新教训、下一局改什么（新教训写进 `sts2-defect-defect` 技能，不改铁甲 skill）。

## 5. 省 token 约定

- 只读本文件 + `sts2-defect-defect` + `sts2-caster`
- 不读 docs/ 全量、不读 `logs/run_*.jsonl`、不读战报
- 每幕结束 `memory --write` + 新会话
