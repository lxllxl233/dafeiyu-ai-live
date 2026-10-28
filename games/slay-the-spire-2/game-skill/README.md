# 杀戮尖塔 2 · 游戏技能层

这个目录放**只属于《杀戮尖塔 2》**的东西，`src/dafeiyu` 保持游戏无关。

```text
game-skill/
├── adapter.py          # GameAdapter 实现：读 mod 本地 HTTP :8080 → GameState
├── obs.json            # OBS 场景/采集源命名（StS2 Game / StS2 Audio / …）
├── NEXT_PROMPT.txt     # 铁甲战士 A10 实况 runbook
└── README.md
```

## 为什么放这里

- 通用内核只认 `GameAdapter` 协议（`dafeiyu/games/base.py`）；
- 游戏包通过 `game.json` 的 `adapter` 指向本目录，注册表按文件路径加载，
  不需要把游戏代码塞进 `src/`：

```json
"adapter": "game-skill/adapter.py:Sts2Adapter"
```

- 兼容层本体仍在 `../compat/`（客户端 `sts2.py` + mod DLL），语料库在 `../corpus/`；
- 游戏专属梗、台词、路线规则放本目录或 `.dsh/skills/`，通用梗放
  `persona/memes/`。

## 常用命令

```powershell
python main.py game state                 # 读状态（:8080）
python main.py games                      # 游戏列表/在线状态
python tools/sts2.py state                # 兼容老命令
python main.py obs health                 # OBS 采集/音频自检
```
