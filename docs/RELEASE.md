# 发布检查（v2.2）

这份清单只做两件事：**防止版本漂移**、**防止把本机实机状态带进发布**。它不调用
真实游戏、不调用模型、不上传日志。

## 1. 先跑本地门禁

```powershell
python tools/release_check.py
```

默认会跑：

- `tests/` 单元测试（跨游戏契约、host、插件、录制）
- `tools/test_imports.py` / `test_ctx_act.py` / `test_docs_consistency.py`
- `tools/check_skill.py`
- `compileall`（`src/dafeiyu` / `games` / `tools` / `tests`）
- 版本串同步检查（`pyproject.toml` / `__init__.py` / `README.md` / `game.json` / changelog）
- `git diff --check`（有 git 时）

需要跑完整的 v1 工具回归时：

```powershell
python tools/release_check.py --full
```

CI 用较轻的组合：

```powershell
python tools/release_check.py --ci
```

## 2. 版本串

发布前这五处必须是同一个产品版本：

| 位置 | 字段 |
|---|---|
| `pyproject.toml` | `[project].version` |
| `src/dafeiyu/__init__.py` | `__version__` |
| `README.md` | 首屏 `` `vX.Y` `` |
| `games/slay-the-spire-2/game.json` | `version` |
| `CHANGELOG.md` | `## [X.Y.Z]` |

`tools/release_check.py --metadata-only` 可以只查这一组，不跑测试。

> 注意：`.dsh/skills/sts2-ironclad-ironclad` 的 `v22` 是**决策技能版本**，不是产品
> 版本；两者不要互相覆盖。

## 3. 实机验收（发布前手动）

```powershell
python main.py doctor
python main.py studio
python main.py play --mode llm --pace voice
# 看一局：路线 / 无开场问候 / 语音节奏 / token / 录像时间轴
python main.py play --stop
```

确认：

- `logs/autoplay.json`：`running=false`、`recording=false`、`last` 正常收尾；
- `recordings/<game>/run-NNNN/`：`run.mp4`、`events.jsonl`、`chapters.md`、`timeline.json`；
- 停止后 `logs/autoplay.stop` 不存在；
- OBS `outputActive=false`；
- B 站礼物需要真实直播间；没有房间号时只能验证单测，不能声称实机通过。

## 4. 发布边界

- 不提交 `config.json`、`logs/`、`recordings/*`（README/INDEX 除外）、`backups/`；
- 不提交 API Key、Cookie、观众隐私、完整游戏局面；
- `tools/` 的 shim 仍在退役观察期，见 `tools/README.md`，本版本不删除。
