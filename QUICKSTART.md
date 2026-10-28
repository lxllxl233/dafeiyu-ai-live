# 快速开始（10 分钟）

两条路，任选：

- **只看直播姬**：不需要游戏，`main.py` 出鱼，`demo` 跑通四层。
- **要打杀戮尖塔 2**：再装 mod、起 OBS，走 v1 实况流程（老命令在 `tools/` 仍可用）。

## 0. 前提

- Windows 10/11（桌宠透明窗 / OBS 音频路由）
- Python 3.10+
- 可选：《杀戮尖塔 2》Steam 版 v0.107.1 + OBS Studio 30+（要 obs-websocket）

## 1. 装依赖

```powershell
cd <克隆目录>
python -m pip install -r requirements.txt
```

## 2. 跑直播姬（不需要游戏）

```powershell
python main.py               # 大肥鱼桌宠；右键鱼身看菜单
python main.py setup         # 建 db/、tools/data/ 兼容链接
python main.py doctor        # 自检：人设卡 / 两种语音 / overlay / 语料库 / 录像
python main.py demo          # 离线跑「游戏 → 性格 → 语音 → UI」
python main.py studio        # 控制台 http://127.0.0.1:8730/studio
```

控制台里能：发台词（先「只算 lint」再「送到直播」）、试听曼波/动物语、看四层状态。

只想起 OBS 桌宠弹幕（不要控制台）：`python main.py studio`，OBS 浏览器源指向
`http://127.0.0.1:8730/`，宽 560 高 470，背景透明（气泡 + 大肥鱼 + 名字）。

桌面桌宠：

```powershell
python main.py               # 默认桌宠；加 --panel 回旧版横幅
# 左键拖（左右/上下换朝向）· 滚轮缩放 · 右键菜单 · Ctrl+Alt+D 穿透 · Ctrl+Alt+S 声音
```

`python main.py` 默认在后台顺带起 overlay 服务；端口被占用会复用，不想起服务加
`--no-server`。

## 3. 打杀戮尖塔 2（v1 流程）

### 3.1 装 mod

```powershell
$mods = "C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2\mods"  # 换成实际路径
Copy-Item .\games\slay-the-spire-2\compat\mod\* $mods -Force
```

在游戏里确认 mod 已启用；细节见
[`games/slay-the-spire-2/compat/mod/INSTALL.md`](games/slay-the-spire-2/compat/mod/INSTALL.md)。

### 3.2 启动游戏并验证

Steam 启动选项加 `--windowed`（全屏独占 OBS 会黑屏）。进主菜单后：

```powershell
python tools/sts2.py state  # 兼容老命令
python main.py game state   # 走游戏注册表读状态
# SCREEN MAIN_MENU | IRONCLAD A? | act ? floor ? | HP ?/? gold ? deck 0
python main.py games        # 应该看到「杀戮尖塔 2 ● 在线」
```

### 3.3 起 OBS + 录屏

```powershell
python main.py obs setup     # 建场景 StS2：StS2 Game / AI Commentary / StS2 Audio
python main.py obs health    # 出帧 / 游戏窗口 / 后台静音 / 音频
python tools/live.py check   # 弹幕窗心跳 / 曼波合成 / OBS 源 / 游戏 API
python main.py replay start --character ironclad --ascension 10
# 打完：停录并收进 recordings/slay-the-spire-2/run-NNNN/run.mp4
python main.py replay stop --result win_a10
```

### 3.4 让 Agent 开局

- 决策技能：`sts2-ironclad-ironclad`
- 说话技能：`sts2-caster`
- 把 [`games/slay-the-spire-2/game-skill/NEXT_PROMPT.txt`](games/slay-the-spire-2/game-skill/NEXT_PROMPT.txt)
  丢给 Agent。

收尾：

```powershell
python tools/sts2.py report
```

录像默认一局一个 `run.mp4`，索引在 `recordings/INDEX.md`；旧的散放录像已迁进
`recordings/slay-the-spire-2/run-NNNN/`，用法见 [`recordings/README.md`](recordings/README.md)。

## 4. 常见问题

| 现象 | 处理 |
|---|---|
| `python tools/sts2.py state` 连不上 8080 | 游戏没开 / mod 没启用 / 版本不匹配 |
| `/state` 500 | v0.8.0 在 v0.107.1 的旧 bug；本仓库 DLL 已打补丁 |
| 曼波不说话 | `python main.py voice test "测试" --voice mambo`；再看 `tools/tts_guard.py --check` |
| 控制台打不开 | `python main.py studio` 是否在跑；端口被占用换 `--port 8731` |
| OBS 录出来黑屏 | 游戏不是窗口化；`python main.py obs free` |
| 录像没声音 | `python main.py obs health` 看「后台静音」；`python main.py obs nomute` 后重启游戏 |
| 同一句说两次 | `curl 127.0.0.1:8730/health` 的 `roles`：只允许一个 live 页面出声 |
| 老脚本找不到 `db/sts2.db` | 跑一次 `python main.py setup` |
| 屏幕上没有弹窗 | `python tools/shot_popup.py` 看真窗口；`tools/live.py restart-streamer` |
| 录像文件在哪 | `python main.py replay list`；一局一个 `recordings/<游戏>/run-NNNN/run.mp4` |

更多命令见 [README.md](README.md)、[ARCHITECTURE.md](ARCHITECTURE.md)。
