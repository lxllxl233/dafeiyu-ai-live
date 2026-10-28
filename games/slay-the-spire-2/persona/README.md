# 人设与台词

大肥鱼「说话」的资料都在这里；原来在 DSH 技能 `sts2-caster` 里，现在就是项目文档。

| 文件 | 内容 | 谁在用 |
| --- | --- | --- |
| `speech.md` | 说话 OS：人设、两个通道、三拍节奏、五件武器、反效果清单、出口前三问、台词 lint | 写台词前的总纲；`tools/check_caster.py` 校验 |
| `lines.md` | 分节点台词库（开局 / 战斗开场 / 掉大血 / 斩杀 / 认错 / Boss / 死亡 / 连载梗） | 找例句时查 |
| `quips.md` | 招牌稿（带梗、限频） | `tools/caster_quips.py` 生成；`check_caster.py` 校验 |

## 说话怎么进直播

- 台词由决策层产出（战斗 `seq` 计划里的 `say`，界面动作里的 `say`），
  `autoplay/runner.py` 的 `_remember_speech` 排进语音窗口，`_pace` 等它播完再走下一步。
- `say` 的硬要求写在 `decision/prompts.py`：1–3 句、带数字、数字必须来自局面包、
  计划里没有的动作不许说。
- 发出去之前可以手工过一遍 lint：

```powershell
python tools/check_caster.py --say "它 21，我最多挡 15。这回合交 6 点税。"
```

## 改台词风格

1. 先改 `speech.md` 的规则，再补 `lines.md` 的例句，最后看 `quips.md` 要不要重新生成。
2. `python tools/check_caster.py` 校验小节/体积/台词条数。
3. 招牌梗的限频在 `speech.md` §4 与 `check_caster.py` 的 `QUIPS` 里，两边一起改。
