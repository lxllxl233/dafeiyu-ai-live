# KOOK 聊天接入

大肥鱼通过 KOOK 机器人的 **WebSocket 长连接**收消息，再用 HTTP 发回复。不需要公网 IP、
端口映射或回调地址。支持文字、图片（视觉模型）、语音音频卡片和大肥鱼表情包。

## 1. 创建机器人

1. 打开 [KOOK 开发者中心](https://developer.kookapp.cn/app)，用 KOOK 账号登录；
2. 首次使用需要完成**实名认证**（手机支付宝扫码）。未认证时不能创建应用；
3. 点「新建应用」，填应用名「大肥鱼」和简介；
4. 进入应用 → 左侧「机器人」：
   - 打开 **WebSocket 连接模式**；
   - 复制 **Token**（只复制一次也要保存好）；
5. 在同一页点「邀请链接」，勾选需要的权限（建议先给文字消息、发送消息、查看频道），
   复制邀请链接；
6. 在浏览器打开邀请链接，把机器人邀请进你的 KOOK 服务器。

## 2. 填到桌宠

设置页 → 「KOOK」：

- **启用 KOOK 聊天**：打开；
- **Bot Token**：粘贴上一步的 Token；
- **聊天人设**：默认 `companion`；
- 保存后 1~2 秒，桌宠会自动拉起 `python main.py kook`。

也可以手动跑：

```powershell
python main.py kook --check   # 只校验 Token 和网关
python main.py kook           # 常驻；Ctrl+C 停止
```

`--check` 会显示 Token/LLM/记忆库状态，并用 `/gateway/index` 验证 Token。不会打印 Token。

## 3. 使用

- 在频道里 @ 机器人，或直接私聊机器人；
- 文本和 KMarkdown 都会处理，`(met)...(met)` 提及会被清掉；
- **图片**：直接发图片，或用网页端粘贴/拖拽（会变成卡片消息）；服务会把图片下载给
  `llm_vision_model` 看，再回复看到的内容；
- **多条回复**：模型可以一次给 1–3 条短消息（像真人连发），服务按顺序逐条发；
  文字只引用原消息的第一条，不会把三条拼成一大段；
- **主动消息**：安静一阵后，偶尔主动找最近聊过的频道/私聊说一句；默认开，
  `kook_proactive_minutes` 控制最短安静时间，实际还会再随机等几分钟。发过一次后
  要等对方回话才会重新计时，不会连续轰炸；设置页改开关/间隔最多 1 分钟生效；
- **语音**：模型判断适合时，回复会带一条「大肥鱼语音 · Ns」音频卡片；
  KOOK 机器人不能直接发 `type=8` 音频消息，所以用的是官方卡片里的 `audio` 模块。
  手机端内嵌播放器对 WAV/重播有兼容问题，现在优先把 TTS 的 WAV 转成 **MP3** 再上传；
  卡片同时带一个「重听」按钮，内嵌播放器第二次没声音时点它用浏览器播放；
- **表情包**：模型会从 `persona/stickers/` 里挑一张，用图片消息发出来；
- 回复发回原频道 / 原私聊；文字会引用原消息，图片/语音/表情包跟在文字后；
- 聊天记忆默认开启：最近 N 条原文 + 摘要进上下文，事实存本机
  `logs/kook/memory.db`；
- 机器人自己发的消息、系统消息、视频/文件不会回复。

## 4. 配置项

| 字段 | 默认 | 说明 |
|---|---|---|
| `kook_enabled` | false | 桌宠是否自动拉起 KOOK 服务 |
| `kook_bot_token` | 空 | 机器人 Token；只存本机，不回显 |
| `kook_persona` | companion | 聊天人设（只加载个人层） |
| `kook_memory` | true | 本地 SQLite 长期记忆 |
| `kook_context_turns` | 12 | 每次带给模型的最近原文条数 |
| `kook_summary_trigger` | 24 | 未压缩对话超过多少条时压摘要 |
| `kook_pet_say` | false | 回复时是否让桌宠也复述 |
| `kook_allow_voice` | true | 允许模型发语音音频卡片 |
| `kook_stickers` | true | 允许模型主动配大肥鱼表情包 |
| `kook_proactive` | true | 安静一阵后偶尔主动发消息 |
| `kook_proactive_minutes` | 60 | 至少安静多少分钟才可能主动开口（10–720）；对方回话后才重新计时 |
| `kook_allowed_users` | `[]` | 只允许这些 KOOK 用户 id；空 = 不限制 |

## 5. 日志与排错

- 事件：`logs/kook/events.jsonl`（只记类型/字数/耗时，不记正文；多条回复记
  `parts`，主动消息记 `proactive`）；
- 用量：`logs/kook/usage.jsonl`（每次回复/记忆抽取/压缩/主动消息的 input/output/total、
  model、`msg_id`）；控制台「Agent 流程 → Token 花费」会和自动玩账单合并，
  并显示来源拆分；
- 服务输出：`logs/kook/service.log`；
- 锁：`logs/kook/service.lock`（防止桌宠和手动启动各开一条连接）。

常见问题：

| 现象 | 检查 |
|---|---|
| 启动提示缺少 Token | 开发者中心 → 机器人 → WebSocket → 复制 Token，填设置页 |
| `--check` 网关失败 | Token 是否复制完整；机器人是否开启 WebSocket 模式；是否需要重新生成 Token |
| 机器人不在服务器 | 重新打开「邀请链接」并确认权限，把机器人邀请进服务器 |
| 发消息没反应 | 看 `logs/kook/events.jsonl` 是否有 `event_received`；没有就是没收到事件（邀请/权限/频道） |
| 有 `event_received` 但没有 `reply` | 看 `llm_error` / `send_error`；检查模型 Key 或机器人发送权限 |
| 私信没反应 / 报“不能是自己” | 私聊事件的 `target_id` 是机器人自己；服务已改为回发送者 `author_id`。确认已重启 KOOK 服务，并看 `send_error` 是否还有 `40000` |
| 发图片没反应 | 看 `events.jsonl` 是否有 `msg_type: 2` 或 `10`；网页端粘贴图片是卡片，服务会从卡片里取图 |
| 有 `image_error` | 图片下载失败（外链过期/格式不支持）；重新发一张 PNG/JPG |
| 没有语音 | 设置页「KOOK → 允许语音回复」是否打开；看 `voice_sent` / `voice_error`，TTS 引擎没起会自动回退动物语；`voice_sent.codec` 应为 `mp3`（没装 `lameenc` 时回退 `wav`） |
| 手机语音只能听一次 | 这是 KOOK 手机端内嵌音频播放器的重播兼容问题；现在语音已转 MP3 并带「重听」按钮，点按钮用浏览器播放可重复听 |
| 没有表情包 | 设置页「KOOK → 主动发表情包」是否打开；看 `sticker_sent` / `sticker_error` |
| 不会主动发消息 | 设置页「KOOK → 偶尔主动发消息」是否打开；要先在这个频道/私聊聊过一句，服务重启后还没聊过不会主动发 |
| 桌面复述不响 | 设置页「KOOK → 桌宠复述」是否打开，桌宠/控制台是否在跑 |
