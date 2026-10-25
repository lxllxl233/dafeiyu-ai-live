# STS2 AI Agent mod · 安装与说明

这是第三方 mod **STS2 AI Agent v0.8.0**（作者 CharTyr，`mod_id.json` 里标 0.5.4）的 Windows 构建，`STS2AIAgent.dll` **已为游戏 v0.107.1 打补丁**。

它的作用：在游戏里起一个本地 HTTP 服务 `http://127.0.0.1:8080`，把游戏状态输出成 JSON，并接受动作指令（出牌、结束回合、选节点……）。本仓库的 `sts2.py` 就是它的客户端。

## 文件

| 文件 | 说明 |
|---|---|
| `STS2AIAgent.dll` | mod 本体（v0.107.1 兼容补丁版） |
| `STS2AIAgent.pck` | mod 资源包（必需，别漏） |
| `mod_id.json` | mod 元数据（游戏用它在 mod 列表里显示） |

## 安装

1. 找到游戏目录的 `mods/`：

   ```text
   <Steam 库>\steamapps\common\Slay the Spire 2\mods\
   ```

2. 把本目录的三个文件复制进去（不要多套一层文件夹）：

   ```powershell
   Copy-Item .\mod\STS2AIAgent.dll,.\mod\STS2AIAgent.pck,.\mod\mod_id.json "<游戏目录>\mods\" -Force
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

v0.8.0 在 v0.107.1 上会调用已不存在的 `Player.CanUseOrRemovePotions`，导致 `/state` 直接 **500**。

补丁把 `IsPotionDiscardable` 里对它的调用改成直接返回 `true`（IL：`pop; ldc.i4.1`），补丁前的原文件在项目的 `backups/` 里（仓库不附带）。

**升级注意**：游戏更新到 v0.111+ 后这个 DLL 会不兼容，需要换 v0.16.x，并重新验证 `/state`、`/actions/available`、`/action` 三个端点。

## 排错

| 现象 | 原因 |
|---|---|
| `curl :8080/state` 拒绝连接 | 游戏没启动 / mod 没启用 / DLL 被 Steam 校验覆盖 |
| `/state` 返回 500 | DLL 没打补丁（v0.107.1 上就是这个症状） |
| 游戏崩溃 | mod 与游戏版本不匹配 |
