# STS2 AI Agent mod · 安装与说明

这是第三方 mod **STS2 AI Agent v0.8.0**（作者 CharTyr，`mod_id.json` 里标 0.5.4），
用 .NET 9 SDK 从源码**重新编译**，并打了游戏 v0.107.1 兼容补丁与牌组多选补丁。

它的作用：在游戏里起一个本地 HTTP 服务 `http://127.0.0.1:8080`，把游戏状态输出成
JSON，并接受动作指令（出牌、结束回合、选节点、选牌……）。本仓库的 `sts2.py` 就是它的客户端。

## 文件

| 文件 | 说明 |
|---|---|
| `STS2AIAgent.dll` | mod 本体（v0.8.0 源码 + `compat/v0.107.1` 分支补丁重编） |
| `STS2AIAgent.pck` | mod 资源包（必需，别漏；没改资源，沿用旧包） |
| `mod_id.json` | mod 元数据（游戏用它在 mod 列表里显示） |

## 源码与重编

- 源码：`https://github.com/lxllxl233/STS2-Agent`，分支 `compat/v0.107.1`
  （基于 tag/commit `6957e27`，即 v0.8.0；上游 main 是给游戏 v0.111+ 的，别用）。
- 重编：

  ```powershell
  git worktree add ..\STS2-Agent-v080 6957e27
  cd ..\STS2-Agent-v080
  git checkout compat/v0.107.1
  $env:STS2_DATA_DIR = "D:\SteamLibrary\steamapps\common\Slay the Spire 2\data_sts2_windows_x86_64"
  dotnet build STS2AIAgent\STS2AIAgent.csproj -c Release
  # 产物：STS2AIAgent\bin\Release\net9.0\STS2AIAgent.dll
  ```

- 只有 DLL 变了，`STS2AIAgent.pck` 不用重打（它只装 `mod_manifest.json`）。

## 安装

1. 找到游戏目录的 `mods/`：

   ```text
   <Steam 库>\steamapps\common\Slay the Spire 2\mods\
   ```

2. 把本目录的三个文件复制进去（不要多套一层文件夹）：

   ```powershell
   Copy-Item .\games\slay-the-spire-2\compat\mod\STS2AIAgent.dll,`
             .\games\slay-the-spire-2\compat\mod\STS2AIAgent.pck,`
             .\games\slay-the-spire-2\compat\mod\mod_id.json "<游戏目录>\mods\" -Force
   ```

3. 启动游戏，确认 mod 已加载：
   - 游戏设置里 `mod_settings.mods_enabled = true`（`%APPDATA%\SlayTheSpire2\default\<id>\settings.save`）
   - 也可以在游戏内的 mod 界面确认 `STS2 AI Agent` 已勾选

4. 验证：

   ```powershell
   curl http://127.0.0.1:8080/state
   # 返回 {"ok":true,...}
   ```

## 这个补丁改了什么

1. **v0.107.1 兼容**：v0.8.0 会调用游戏里已删除的 `Player.CanUseOrRemovePotions`，
   导致 `/state` 直接 500。改为 `IsPotionDiscardable` 返回 `true`（以前是在 DLL 上打
   IL 补丁，现在是源码层）。
2. **牌组多选（「从牌组移除/升级 N 张」）能确认了**：
   - `NDeckCardSelectScreen` 的确认按钮是私有字段 `_confirmButton` /
     `_previewConfirmButton`，不是 `%Confirm` 节点；现在反射取字段（预览打开时优先
     预览确认），并把牌组屏接进 `CanConfirmSelection` / `ExecuteConfirmSelectionAsync`，
     暴露 `confirm_selection`；
   - `BuildSelectionPayload` 从提示文本解析 `min/max`（先去掉 `[blue]` 这类 BBCode），
     已选张数读 `_previewCards`，不再一律 0/0/0；
   - 点确认前用游戏自己的 `CheckIfSelectionComplete` 把关（实测选 1/2 就点确认，
     游戏会只移除 1 张并结束事件）；
   - `select_deck_card` 只点牌、不自动确认，`confirm_selection` 单独驱动。

**升级注意**：游戏更新到 v0.111+ 后这个 DLL 会不兼容，需要换上游 v0.16.x，并重新验证
`/state`、`/actions/available`、`/action` 三个端点。

## 排错

| 现象 | 原因 |
|---|---|
| `curl :8080/state` 拒绝连接 | 游戏没启动 / mod 没启用 / DLL 被 Steam 校验覆盖 |
| `curl` 报 SSL/代理错误 | PowerShell 的 `Invoke-RestMethod` 走了系统代理，用 `curl.exe --noproxy "*"` |
| `/state` 返回 500 | DLL 没打补丁（v0.107.1 上就是这个症状） |
| 直接运行 `SlayTheSpire2.exe` 报「Steam 初始化失败」 | 游戏目录缺 `steam_appid.txt`（内容 `2868840`） |
| 游戏崩溃 | mod 与游戏版本不匹配 |
