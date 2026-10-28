# 通用 meme 梗

和具体游戏无关的短梗，放在这里；游戏专属梗放
`games/<game-id>/game-skill/`，避免把「杀戮尖塔」的梗写进通用性格层。

- `general.json`：通用鱼味梗（20 条），字段 `id / text / tags / when`。
- 代码读取：`dafeiyu.persona.memes.load_memes("general")`。
- 新增语言/风格：加 `persona/memes/<name>.json`，不用改代码。
