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
```

`setup` 会建场景 `StS2`，包含：`StS2 Game`（game_capture）、`AI Commentary`（浏览器源 → 8730）、`StS2 Audio`（进程音频）。手搓 OBS 的话照着这三个名字建，`obsctl.py` 认名字。

## 5. 开录 + 打

```powershell
python obsctl.py start
```

然后让 agent 读技能：

- 决策：`sts2-ironclad-ironclad`
- 说话：`sts2-caster`

把 [NEXT_PROMPT.txt](NEXT_PROMPT.txt) 丢给 agent 就能开局。收尾：

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

更多命令见 [README.md](README.md)。
