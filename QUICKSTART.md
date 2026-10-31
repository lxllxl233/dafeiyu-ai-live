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
start.bat                    # 一键启动桌宠（双击也行）
python main.py               # 或直接跑；右键鱼身紧贴弹出设置齿轮
python main.py setup         # 建 db/、tools/data/ 兼容链接
python main.py doctor        # 自检：人设卡 / 两种语音 / overlay / 语料库 / 录像
python main.py demo          # 离线跑「游戏 → 性格 → 语音 → UI」
python main.py agent run --demo  # 可选：离线跑 Agent 图（observe → plan → validate → execute）
python main.py studio        # 控制台 http://127.0.0.1:8730/studio
```

控制台左侧导航：基础配置 / 游戏 / 人设配置 / 互动 / Agent 流程 / 决策与日志。能发台词（先「只算 lint」再「送到直播」）、
看决策/日志/Token、切语音/模型/游戏、配置 API Key、试听曼波/动物语。曼波离线时点「启动」或「试听」
会自动拉起本地 MamboTTS；目录不同在「基础配置 → MamboTTS 目录」填，或设 `DAFEIYU_MAMBO_DIR`。

> API Key 只写本机 `config.json`（`.gitignore` 已忽略），控制台只显示「已配置」，不回显。

只想要 OBS 桌宠弹幕：`python main.py studio` 起服务（不用打开 `/studio` 即可），
OBS 浏览器源指向 `http://127.0.0.1:8730/`，宽 560 高 470，背景透明（气泡 + 大肥鱼 + 名字）。
B 站云观影换成 `http://127.0.0.1:8730/bili`（1920×1080，透明，自带场景侧栏和 TTS），
底层放 Chrome/副屏捕获；不要再同时挂 `/` 源，避免同一句配音响两次。

### 2.1 看 Agent 流程（可选）

```powershell
python -m pip install -e .[agent]   # 只装一次 LangGraph
python main.py agent run --demo     # 不碰游戏，验证图
python main.py agent trace          # 节点耗时/重试
python main.py agent graph          # Mermaid
```

控制台「Agent 流程」会显示最近运行；真实局面默认只 dry-run，确认后再加 `--execute`。
外部 DSH Agent 可用 `--plan plan.json` 注入计划，继续复用 `games/slay-the-spire-2/decision/knowledge` 的决策。

桌面桌宠：

```powershell
start.bat                    # 双击也行；pythonw 启动，无控制台
python main.py               # 默认桌宠；加 --panel 回旧版横幅
# 左键拖（左右/上下换朝向）· 滚轮缩放 · 右键设置 · Ctrl+Alt+D 穿透 · Ctrl+Alt+S 声音
```

右键鱼身会紧贴右侧弹出**同心圆齿轮按钮**（外层浅蓝、内层蓝、中央一枚更大的设置图标，层层居中，大小跟随桌宠，弹出/淡出动画，5 秒不点自动收起），点它打开
分组设置页：桌宠 / 语音 / 性格 / 模型 / 服务端口 / 直播 / KOOK / 运行 / 关于。
页面和桌宠读写同一份 `config.json`：保存后 1~2 秒内生效，端口这类在启动时绑定过的值
（控制台 8730 / 曼波 9880）要点页面右下角的「重启桌宠」。空闲气泡「吃饭ing」，决策「思考ing」，
说话显示台词。
「桌宠 → 启动与常驻」可以开 Windows 托盘图标（左键唤起/收起，右键设置）和开机自启动
（HKCU Run，不需要管理员）；「语音」页的「关闭引擎」可以停掉曼波进程释放内存。
「运行」页可以让她玩（本地算账 / LLM 规划）、停手、只启动游戏、开关 LLM 直播模式；
游戏切换在「桌宠 → 当前游戏」（写入 `config.game`），只切配置，不重启游戏。
「LLM 直播模式」让导演在 `games/` 的直播模块（刷B站 / 杀戮尖塔2 / 吸血鬼幸存者）之间自己切：
模型决定下一段播哪个、播多久、过渡说什么；没有 API Key 时会返回错误提示。
吸血鬼幸存者需要先按 [games/vampire-survivors/README.md](games/vampire-survivors/README.md)
装一次 BepInEx 6 IL2CPP 桥；装好后走本地 HTTP，不切前台、不抢键盘。

`python main.py` 默认在后台顺带起 overlay 服务；端口被占用会复用，不想起服务加
`--no-server`。

性格在设置页「性格」看：个人性格常驻，直播时加 `persona/live/`，玩某个游戏时再加
`games/<id>/persona/layer.md`；三层按顺序拼，预览区能直接看到最终提示词。
KOOK 聊天开启后由桌宠自动拉起监控（也可手动 `python main.py kook`），聊天记忆在本机 SQLite：
最近 N 条原文进上下文，超过阈值自动压成摘要（原文不删）。它用 WebSocket 长连接，
频道/私聊都行，不需要公网 IP；配置见 [docs/KOOK.md](docs/KOOK.md)。

## 3. 打杀戮尖塔 2（v1 流程）

### 3.1 装 mod

```powershell
$mods = "C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2\mods"  # 换成实际路径
Copy-Item .\games\slay-the-spire-2\compat\mod\* $mods -Force
```

在游戏里确认 mod 已启用；细节见
[`games/slay-the-spire-2/compat/mod/INSTALL.md`](games/slay-the-spire-2/compat/mod/INSTALL.md)。

### 3.2 启动游戏并验证

Steam 启动选项加 `--windowed`（全屏独占 OBS 会黑屏）。设置页「运行 → 只启动游戏」或：

```powershell
python main.py game launch  # 只按 config.game 启动（STS2 = Steam 2868840）
python tools/sts2.py state  # 兼容老命令
python main.py game state   # 走游戏注册表读状态
# SCREEN MAIN_MENU | IRONCLAD A? | act ? floor ? | HP ?/? gold ? deck 0
```

想让她直接打：

```powershell
python main.py play              # 启动 + 自动跑；Ctrl+C 停
python main.py play --mode llm   # 所有玩家决策交给 LLM（需 API Key）
python main.py play --pace voice # 慢速等语音（默认）
python main.py play --pace normal # 旧版快节奏
python main.py play --stop       # 另一个进程里停手
```

设置页「运行」、管理端「Agent 流程」和 CLI 是同一套控制。默认 **本地算账 +
慢速播报**：每一步至少留 2 秒，台词更长时会等估算播完再动。LLM 模式把所有玩家决策交给
模型：战斗牌序走 `decision/act.py`，地图/事件/营火/商店/宝箱/奖励/选牌走
`decision/actions.py` 的白名单菜单；模型超时或输出不可执行才本地兜底（记
`llm_fallback`），主菜单/角色选择/确认框仍是本地管道。自动玩默认自动开 OBS 录像（OBS 没开会拉起），
停止时先把录像收进 `recordings/<游戏>/run-NNNN/run.mp4`，并在同目录写
`events.jsonl` / `chapters.md` / `timeline.json`（画面、楼层、战斗、台词和对应
时间段，方便混剪）；控制台「Agent 流程」会显示本轮实际 Token（输入/输出/合计）
与 LLM 回合数。自动玩法只处理已知界面，未知界面会停下并写 `logs/autoplay.json`，不猜按钮。

B 站礼物：控制台「基础配置 → B 站直播间」填房间号，勾选「开启 B 站礼物监听」。
桌宠会在后台连 B 站直播 WebSocket，收到礼物/醒目留言/上舰就让鱼绕两圈；若提示
`-352` 风控，在同一张卡填浏览器里的 `SESSDATA` Cookie（只存本机 config.json）。
房间号/Cookie 改动会自动重连，不用重启桌宠。
python main.py games        # 应该看到「杀戮尖塔 2 ● 在线」；装了 Steam 版还会看到「吸血鬼幸存者」
python main.py live         # LLM 直播模式：导演自己切模块（需要 API Key）
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

录像默认一局一个 `run.mp4`，索引在 `recordings/INDEX.md`；自动玩还会在同目录写
`events.jsonl` / `chapters.md` / `timeline.json` 时间轴。旧的散放录像已迁进
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
