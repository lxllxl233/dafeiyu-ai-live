# 性格层（Persona）

一个人设 = 一个目录：

```
persona/<id>/
├── card.json     # 机器读：长度/禁语/招牌/口癖/开场收尾
└── style.md      # 人读：说话系统说明，给写台词的人看
```

`card.json` 的字段：

| 字段 | 作用 |
|---|---|
| `traits` | 人设四条（UI 展示） |
| `style.max_len` / `soft_len` | lint 的 WARN / FAIL 阈值 |
| `style.max_thought` / `max_meme` / `max_total` | 拼句时的截断上限 |
| `style.min_gap` | 两条台词最小间隔（秒） |
| `banned_prefixes` / `banned_labels` | 不许出现的自称、报告体标签 |
| `repeat_words` | 一条里出现两次就 FAIL 的空梗 |
| `quips` | 招牌稿（一句最多一句，带冷却） |
| `openers` / `closers` | 开局、收尾备选 |

新增人设：复制 `dafeiyu/` 改名，改 `id`/`name`/`voice`，再在 `config.json` 里指定
`{"persona": "<id>"}` 或设置 `DAFEIYU_PERSONA=<id>`。

代码入口：`src/dafeiyu/persona/`（`Persona.compose` / `.lint` / `.render` / `.note_sent`）。
