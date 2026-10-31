# 性格层（Persona）

性格按「个人 → 直播 → 游戏」三层叠：

| 层 | 目录 | 什么时候加载 |
|---|---|---|
| 个人（常驻） | `persona/<id>/` | 永远；KOOK 聊天用 `persona/companion/`（`config.kook_persona`） |
| 直播 | `persona/live/` | 直播/上台且 `config.persona_live` 打开；KOOK 不加载 |
| 游戏 | `games/<id>/persona/layer.md` | 只在该游戏的直播/自动玩提示词里 |

装配代码是 `src/dafeiyu/persona/layers.py`：`PersonaStack.load(persona_id, game_id, live)`
把三层按固定顺序拼成前缀；设置页「性格」能看到三层和最终提示词。下层只补「这个
时候多出来的一点」，不要把个人层的人设再抄一遍。

一个人设 = 一个目录：

```
persona/<id>/
├── card.json     # 机器读：长度/禁语/招牌/口癖/开场收尾
└── style.md      # 人读：说话系统说明，给写台词的人看
```

`persona/live/` 也是同样结构（`card.json` + `style.md`）；游戏层只需要一个
`games/<id>/persona/layer.md`，没有卡也能加载。

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
| `watch` | 可选：旧版看片偏好（`keywords` / `avoid` / `prefer`）；B 站模式已不用这一份 |
| `openers` / `closers` | 开局、收尾备选 |

新增人设：复制 `dafeiyu/` 改名，改 `id`/`name`/`voice`，再在 `config.json` 里指定
`{"persona": "<id>"}` 或设置 `DAFEIYU_PERSONA=<id>`。

代码入口：`src/dafeiyu/persona/`（`Persona.compose` / `.lint` / `.render` / `.note_sent`）。

B 站云观影是隔离人设：读 `games/bilibili-hot-commentary/persona/{card.json,style.md}`，
不跟随这里的 `config.persona`；它的直播层是 `games/bilibili-hot-commentary/persona/layer.md`。

表情包在 `persona/stickers/`（`stickers.json` + `sticker_<id>.png`），
素材来自梗鲸 DeepSeek 娘表情包（粉丝二创，非商用，见 `persona/stickers/SOURCE.md`）；
`python tools/import_stickers.py` 可重新导入，聊天回复的 `sticker` 字段引用这里的 id。
