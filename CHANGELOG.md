# Changelog

## [2.2.0] - 2026-10-28

### Added

- **第二游戏插件 `toy-duel`**：完全离线的观察/控制/自动玩验收包，证明核心不依赖
  《杀戮尖塔 2》；`GameRegistry`、facade、`AutoplayHost`、`GameTools` 共用同一套契约。
- **跨游戏一致性门禁**：`tests/test_game_conformance.py` 至少检查两个完整插件包；
  `tests/test_architecture_boundary.py` 把 `toy-duel` / `玩具决斗` 也加入核心禁用 token。
- **发布门禁**：`tools/release_check.py`、GitHub Actions CI、`docs/RELEASE.md`、本 changelog。
- **`tools/README.md`**：说明 shim、运维脚本和回归测试的边界，以及 shim 退役条件。

### Changed

- STS2 决策引擎整体位于 `games/slay-the-spire-2/decision/`；`tools/` 下同名文件只是
  兼容 shim，旧命令和旧 skill 继续可用。
- 自动玩停止收尾会清理 `logs/autoplay.stop`，并补了同进程停手回归断言。
- `AutoplayHost` 构造时把 `game/mode/pace` 写入初始状态，跨进程停手不再写出空游戏。
- `AutoplayRunner` 现在是 `runtime_checkable`，插件一致性测试可直接检查。

### Verified

- 实机 STS2 LLM 自动玩：录制 `run-0108`（`run.mp4` 约 92 MB，15 事件 / 10 章节），
  3 次 LLM、7,166 tokens，开局路线锁定，无开场问候，语音节奏生效，停手后 OBS 收尾。
- `python -m pytest -q tests`：115 项通过。
- `python tools/test_all.py`：39/39 通过。
- `python main.py doctor`：9 项，0 BAD（overlay 服务开启时）。

## [2.1.0]

- 跨游戏插件契约、`AutoplayHost`、STS2 插件/自动玩拆分、`ActionResult` 统一返回值；
  旧的 v1 工作流和 `tools/` 命令保留兼容。
