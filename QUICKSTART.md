# 快速开始（10 分钟）

目标：克隆下来后，让 AI agent 自己开一局 A10 铁甲战士、录屏、带弹幕解说。

## 0. 前提

- Windows 10/11
- 《杀戮尖塔 2》Steam 版 **v0.107.1**（mod 按这个版本编译/打补丁；升级到 v0.111+ 需换新版 mod）
- Python 3.11+
- OBS Studio 30+（要 obs-websocket，默认自带）

## 1. 装依赖

```powershell
cd <克隆目录>
pip install -r requirements.txt
```

## 2. 装 mod（游戏侧）

把 `mod/` 里的三个文件复制到游戏的 mods 目录：

```powershell
$mods = "C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2\mods"  # 换成你的实际路径
Copy-Item .\mod\* $mods -Force
```

然后在游戏里确认 mod 已启用（设置 → mods，或 `settings.save` 里 `"mods_enabled": true`）。
细节和踩坑见 [mod/INSTALL.md](mod/INSTALL.md)。

## 3. 启动游戏

Steam → 库 → 右键游戏 → 属性 → 启动选项填 `--windowed`（**必须窗口化**，全屏独占 OBS 会黑屏）。

进到主菜单后验证 API：

```powershell
python sts2.py state
# SCREEN MAIN_MENU | IRONCLAD A? | act ? floor ? | HP ?/? gold ? deck 0
```

## 4. 起弹幕 overlay + OBS

```powershell
# 1) 弹幕服务（http://127.0.0.1:8730）
python overlay_server.py

# 2) 让 OBS 建好场景和源（OBS 需先开，obs-websocket 默认 127.0.0.1:4455）
python obsctl.py setup

# 3) 自检：出帧 / 游戏窗口 / 静音 / 音频
python obsctl.py health

# 4) 主播链路一体体检（弹幕窗心跳 / 曼波合成 / OBS 源 / 游戏 API）
python tools/live.py check
# 有 BAD 就修：坏的是弹幕窗时用这个重启，其它按提示处理
python tools/live.py restart-streamer
# 长时间直播建议挂着看护：心跳卡死自动重启主播窗
python tools/live.py watch --interval 30
```

`setup` 会建场景 `StS2`，包含：`StS2 Game`（game_capture）、`AI Commentary`（浏览器源 → 8730）、`StS2 Audio`（进程音频）。手搓 OBS 的话照着这三个名字建，`obsctl.py` 认名字。

## 5. 开录 + 打

```powershell
python obsctl.py start
```

然后让 agent 读技能：

- 决策：`sts2-ironclad-ironclad`
- 说话：`sts2-caster`

把 [NEXT_PROMPT.txt](NEXT_PROMPT.txt) 丢给 agent 就能开局。想在自己屏幕上盯着那条弹幕看，
另开一个终端跑 **主播模式**（可拖动的透明弹幕窗，和 OBS 里同款、有声音）：

```powershell
python streamer.py --demo     # 先看效果；左键拖位置，右键菜单，Ctrl+Alt+D 点击穿透，Ctrl+Alt+S 声音
```

这个窗是纯本地预览：OBS 的录像仍走原来的 `StS2 Game` + `AI Commentary`，窗口默认带采集排除，
**不会被录进视频**（要录它得加 `--capture`）。

收尾：

```powershell
python obsctl.py stop        # 录像在 recordings/
python sts2.py report        # JSONL 战报 → logs/run_*.md
```

## 常见问题

| 现象 | 处理 |
|---|---|
| `python sts2.py state` 连不上 8080 | 游戏没开 / mod 没启用 / 版本不匹配，见 [mod/INSTALL.md](mod/INSTALL.md) |
| `/state` 500 | v0.8.0 mod 在 v0.107.1 上的旧 bug，本仓库 `mod/STS2AIAgent.dll` 已打补丁 |
| OBS 录出来黑屏 | 游戏不是窗口化；用 `python obsctl.py free` 切窗口采集 |
| 录像没声音 | `python obsctl.py health` 看「后台静音」；`python obsctl.py nomute` 后重启游戏 |
| 药水用了没反应 | 旧版行为：`use_potion` 返回 `pending` 时重读 state 看 `is_queued`，最多重试 1 次 |
| 前几句有声、后面全哑 | MamboTTS 假活：`python tools/tts_guard.py --check`，坏了 `--restart`（overlay 连续合成失败会自动拉起 guard） |
| 同一句说两次（回音） | 看 `curl 127.0.0.1:8730/health` 的 `roles`：只允许一个 `live` 页面出声、主播窗在页面出声时让位；`audio_ts` 能看出是谁在响 |
| 屏幕上没有弹窗 | 先 `python tools/live.py check`（含「弹窗位图像素」）；进程没了就 `restart-streamer`，想一劳永逸开播前挂着 `python tools/live.py watch`。**截图/录屏永远拍不到这个窗**（`WDA_EXCLUDEFROMCAPTURE`），别用截图判「有没有画」；要看真图 `python tools/shot_popup.py` |

更多命令见 [README.md](README.md)。
