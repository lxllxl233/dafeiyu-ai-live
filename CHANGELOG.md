# Changelog

## [Unreleased]

- **KOOK 私信修复**：私聊事件的 `target_id` 是机器人自己，原来直接拿来回发会报
  `40000: 不能是自己`；现在频道仍回 `target_id`，私聊改回发送者 `author_id` 并走
  `direct-message/create`，主动私信也记住用户 id。补了私聊回归测试。

### Added

- **性格三层**：个人性格（常驻）→ 直播性格（直播时叠加）→ 游戏性格（玩该游戏时叠加），
  由 `src/dafeiyu/persona/layers.py::PersonaStack` 按固定顺序拼进系统提示词。
  新增 `persona/live/card.json` + `style.md`、`persona/companion/`（KOOK 个人层）和
  `games/<id>/persona/layer.md`（杀戮尖塔 2 / 吸血鬼幸存者 / B站）；设置页新增「性格」，
  可看三层来源/字数、关直播层、预览最终提示词。KOOK 只用个人层（`kook_persona`）。
- **KOOK 聊天记忆压缩**：`memory.db` 新增 `summaries` + `meta.summary_upto`；每次请求只带
  最近 `kook_context_turns` 条原文 + 最近几段摘要，未压缩原文超过
  `kook_summary_trigger` 时保留最后 8 条、把更老的压成摘要（原文不删）。设置页
  「KOOK → 聊天记忆」可看原文/摘要/待压缩条数并改阈值。
- **KOOK 私聊/频道机器人**：桌宠启动时按 `kook_enabled` 自动拉起无窗口的 KOOK
  监控子进程，退出/关开关时收掉；`logs/kook/service.lock` 防止手动和桌宠各起一份。
  Bot 走 WebSocket 长连接，不需要公网 IP，频道和私聊都回复；对话进本地 SQLite
  记忆，`logs/kook/events.jsonl` 只记事件类型/字数/耗时，不记正文。
- **KOOK 图片 / 语音 / 表情包**：能接收 `type=2` 图片和网页端卡片图片，走
  `llm_vision_model` 看图；回复可带大肥鱼表情包（`asset/create` 上传后发 `type=2`）
  和语音音频卡片（TTS WAV 上传后发卡片 `audio` 模块；KOOK 不允许机器人直发
  `type=8`）。新增 `kook_allow_voice` / `kook_stickers` 开关，设置页可关。
- **KOOK 聊天 Token 账单**：每次回复/记忆抽取/记忆压缩的真实 usage 写
  `logs/kook/usage.jsonl`（带 `msg_id`），控制台「Agent 流程 → Token 花费」把
  它和自动玩账单合并，新增「来源」拆分（自动玩 / KOOK 聊天）和每步明细。
- **KOOK 连发与主动消息**：回复协议改为 `messages` 数组（1–3 条短消息），服务逐条
  发成独立气泡，旧 `reply` 单字符串仍兼容；安静 `kook_proactive_minutes` 后按概率
  主动找最近聊过的频道/私聊说一句（`kook_proactive` 开关，默认开；发过一次后要等
  对方回话才重新计时）。设置页改主动消息开关/间隔最多 1 分钟生效；主动消息的 usage
  也计入 Token 账单。
- **KOOK 语音手机端可重听**：TTS 的 WAV 先用 `lameenc` 转成 MP3 再上传（编码失败才
  回退 WAV），音频卡片追加「重听」link 按钮；解决手机端内嵌播放器第二次播放没声音的
  兼容问题。新增 `lameenc>=1.8` 依赖。
- **KOOK 接入**：配置、设置页、常驻进程、测试和文档都改走 `kook_*` 字段。
- **曼波引擎可关闭**：设置页「语音 → 关闭引擎」和 `POST /api/voice/stop` 调
  `voice/startup.py::stop_mambo`，只杀 python/pythonw 进程树，等待端口释放；不碰其他程序。
- **Windows 托盘图标 / 开机自启动**：`pet_status_bar` 开一个系统托盘图标
  （左键唤起/收起桌宠，右键打开设置），`pet_autostart` 写
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`（pythonw + `main.py`，无需管理员）。
  两项都在设置页「桌宠 → 启动与常驻」。
- **桌宠右侧设置齿轮**：右键鱼身会紧贴鱼身右缘弹出双层同心圆按钮
  （外圈浅蓝、内圈蓝、中央一枚更大的设置图标，层层居中；大小跟桌宠倍率同步），
  弹出时轻微过冲、
  收起时缩小淡出；点它才打开设置页，5 秒不点自动收起。定位按桌宠所在显示器的
  工作区计算，副屏右键不会再飞到主屏。不再弹 Win32 系统菜单。

- **杀戮尖塔 2 开局设置（角色 / 进阶）**：`game.json` 新增 `start` 块声明开局选项
  （铁甲战士 / 静默猎手 / 故障机器人 / 死灵缚者 / 星之王储 + 进阶 0–20，默认铁甲 A10）。
  控制台「当前游戏 → 开局设置」按声明渲染，保存写 `config.json` 的
  `start_options.<game-id>`（核心只按声明清洗，不认识具体游戏）。`runner.py::_ensure_run`
  每次开新局前按 mtime 重读：角色按 `character_id` 选、进阶上下两个方向都调、
  超过游戏 `max_ascension` 停在上限；角色没解锁只记 `start_character_unavailable`，
  不卡在选人界面。`tools/run_start.py` 也读这份设置（`--character/--ascension` 可覆盖），
  决策提示词改成「角色/难度以 ctx 为准」。
- **B站视频评论留档**：`bilibili-hot-commentary` 真发出一条视频评论后，引擎把
  视频链接、标题、UP 主、评论内容和 rpid 追加到
  `logs/bilibili_commentary.comments.jsonl`（最近 200 条）；控制台「视频评论」
  卡片新增「已发评论」列表，按时间倒序展示，点标题直接打开原视频。没发成功
  （Cookie 失效、风控、限流、空评论）的不记，避免把草稿当已发。
  `GET /api/bilibili/comment` 同时返回 `posted` 账本。

- **OBS 录像策略**：新增 `src/dafeiyu/recording/policy.py`。每个游戏在 `game.json`
  的 `recording` 块声明默认（`enabled` + `split=run|none`），控制台「当前游戏 → OBS 录像」
  按当前游戏修改并写 `config.json` 的 `recording.games.<id>`；`recording_enabled`
  是总开关。开启录像时先检查/创建 `games/<id>/game-skill/obs.json` 场景脚本，再按
  一局一集（`run-NNNN`）或不分集（`continuous/`）开录；关闭按会话类型收尾。
  默认杀戮尖塔 2 / 吸血鬼幸存者开启一局一集，刷B站关闭不分集。右键「OBS 录像」
  走同一套 `manual.toggle_game`，OBS 没开时尝试拉起。控制台游戏页重做为当前游戏
  workspace，只展示本局数据、当前游戏录像策略和操作，其他游戏收进折叠列表。
- **吸血鬼幸存者（第三个游戏包）**：新增 `games/vampire-survivors/`。游戏是
  Unity 6 / IL2CPP，配套 `compat/mod/VsBridge`（BepInEx 6 IL2CPP 插件）在
  `127.0.0.1:8091` 暴露 `GET /state` / `POST /act`；`compat/vs.py`、`engine.py`、
  `runner.py` 全部走 HTTP，**不找窗口、不切前台、不发键盘**，游戏可以在后台跑。
  桥读取玩家/敌人/升级选项，并驱动警告页、角色/关卡选择、升级、宝箱、结算和
  重开；`runner.py` 支持 `local` 躲怪走位和 `llm` 定期问模型，实时游戏不等待
  语音。`game.json` 用 Steam `1794680` 启动，并声明 `live` 直播模块。
- **LLM 直播模式（导演）**：新增 `src/dafeiyu/agent/director.py`。导演扫 `games/`
  下声明了 `live` 的模块（刷B站 / 杀戮尖塔2 / 吸血鬼幸存者），把候选、最近历史
  和已播时长交给模型，模型只回 `{module, minutes, say, reason}`；导演停掉当前模块、
  切 `config.game`、按模块声明的模式重新开播，并把过渡台词走 `/comment`。
  模型失败按顺序轮换并记 `director_fallback`；状态写 `logs/live_director.json`，
  停止标志 `logs/live_director.stop`，Token 仍走 `AutoplayHost.add_usage`。
  桌宠右键新增「LLM 直播模式」（运行中显示当前模块，再点是停），控制台「Agent
  流程」新增按钮和当前模块/下次切换显示，CLI 新增 `python main.py live`
  （`--stop` / `--status` / `--module`）。手动「让她玩」/「让她停手」/`play --stop`
  会先停导演。
- **直播模块声明**：`games/slay-the-spire-2/game.json`、
  `games/bilibili-hot-commentary/game.json` 增加 `live` 块（label/role/weight）；
  核心只认字段，不写死游戏 id。`games/registry.json` 注册 `vampire-survivors`。

### Changed

- **B站视频评论更像真人**：`video_comment` 提示词改成「一个具体细节 + 一句自己的反应」，
  明确禁止抄人设台词（同类看同类 / 我也是条鱼 / 先别急着笑 / 我们鱼界）和空话
  （求赞 / 已三连 / 沙发），并给了两条写法参考；人设卡去掉了这两句招牌稿，
  `style.md` 的同类时刻改成“说视频里的事，不背台词”。档案层新增
  `models.comment_drop_reason()`：命中同类台词、或整句只有一句口癖的评论直接丢掉
  （meta 记 `video_comment_dropped`），引擎发之前再兜底查一次，宁可不发也不发台本。
  `PROMPT_VERSION` 升到 `bili-commentary-v6`（解说提示词末尾追加该包
  `persona/layer.md` 的游戏层），旧档案缓存会自动重建。

### Fixed

- **跨域 POST 偶发 ConnectionAborted**：拒绝 `Origin` 不匹配的请求时，先把请求体读掉
  再关连接，避免 Windows 带未读数据 close 回 RST，客户端能看到正常的 403 而不是
  连接被中止（设置页集成测试因此偶发失败）。
- **overlay 断线不再刷 traceback**：OBS 切场景、直播姬刷新浏览器源时，还在等 `/tts`
  的连接会被掐断（WinError 10053/10054），`socketserver` 会给每条断线打一整页
  traceback，看着像服务崩了。现在 `Handler.handle` / `_send` 把连接层的错按「响应
  没人要了」处理，服务继续跑；TTS 合成失败会在 `[tts]` 行尾带上真实原因
  （`ConnectionRefusedError` / 超时 / 非 WAV 响应），并写进 `/health` 的 `tts.last_error`。
- `python main.py game state --game vs` 现在会先按 `game.json` 的 aliases 解析，
  不再把别名当游戏 id 直接查注册表。

## [2.5.0] - 2026-10-29

### Added

- **B站热门视频云观影（B 模式）**：新增 `games/bilibili-hot-commentary/` 插件，按 B 站
  专属人设（默认参考 xQc，保留大肥鱼身份）搜片/选片；只读标题、简介、UP 主、热评和
  弹幕时间轴，不下载、不读页面、不同步播放。每条视频一次轻量 LLM 生成档案和时间轴
  cue，播放中按虚拟时钟说话，不每 tick 调模型；开场/收尾各一句问候，直播弹幕按频次/
  预算回复并优先 SC。控制台「游戏 → B站云观影」可看播放列表、指定播放/关闭/刷新
  Chrome 标签页，并编辑 B 站专属人设；运行中桌宠/控制台空闲文案是「看视频ing」。
  直播姬/OBS 新增 `Bili Watch` 场景页 `/bili`（1920×1080 透明）：右侧显示当前视频、
  虚拟进度、直播弹幕和下一条，左下是大肥鱼与解说，带 AI 生成标识；场景页自己播 TTS。
  OBS 下 `python main.py obs setup` 会按 B 包 `game-skill/obs.json` 自动建副屏采集、
  `Bili Scene` 浏览器源和桌面音频引用（切 Chrome 窗口不断，且不重复建桌面源）。
- **B 模式去重与续播修正**：视频一进入播放就记“已看”，中途停掉/重启不会再把同一条
  选回来；搜索每个关键词首屏取 40 条、候选池补详情上限提到 16，热门回退最多拉
  60 条，不再总在前 8 条里循环。打开视频统一加 `?t=0`，压过 B 站历史记录续播，避免视频从
  上次位置开始导致实际剩余时长变短；跨进程加运行锁，防止两个云观影进程同时选片。
  播放中增加外部结束检测：Chrome 窗口关闭立即、页面关掉后约 8 秒进入下一条；
  每 10 秒读一次自己账号的历史 `progress/duration`，拖到结尾也会自动接下一个。
- **B 模式高播放量「大肥鱼」专线**：除人设的搞笑/高能搜索外，默认每条视频准备时
  都先按「大肥鱼 / 大肥鱼 直播 / 大肥鱼 切片」搜一遍，只保留播放量 ≥10 万、时长合格、
  没看过/没在失败冷却的结果；补详情后只要过了评论/互动门槛，就置顶到本轮第一条。
  `high_view_keywords` / `high_view_min_view` / `high_view_every` 可在
  `bilibili_commentary` 覆盖，`high_view_every=0` 关闭专线。
- **B 模式同类自嘲/共情**：标题、UP 主或简介带“大肥鱼”时，视频档案会收到
  `same_fish=true`，提示词允许她自然自嘲“我自己也是条鱼”、开同类玩笑、共情视频里的
  鱼/UP 主；同一段最多一两句，不冒充视频里的鱼。人设 `card.json` / `style.md` 和
  直播回复也同步加了“同类时刻”，`PROMPT_VERSION` 升到 v4，`LIVE_PROMPT_VERSION` 升到 v3。
- **B 模式同类视频可选评论**：模型在 `same_fish=true` 且有具体想法时，可以在档案里写
  一条 `video_comment`；引擎用登录 Cookie 发到 B 站视频评论区。不是每条都评，默认
  不限制每日条数、间隔至少 10 分钟、同视频只发一次；没想法/没 Cookie/间隔太近都只记日志。
  控制台「B站播放列表 → 视频评论」可改启用、每日上限（0 = 不限）、间隔和字数；
  保存后重启 B 模式生效。
  `video_comment_enabled` / `video_comment_max_chars` / `video_comment_max_per_day` /
  `video_comment_min_gap_s` 也可直接覆盖。
- **B 模式点播与列表修正**：控制台点播放不再因为视频冷热评少于 12 条被丢弃；
  点播准备时保留原有候选列表，不再把列表清成只剩这一条，旁边的大肥鱼还在。
  点播时评论/弹幕接口失败或模型档案失败，会先用兜底开场/收尾档案把视频放起来；
  只有详情都拿不到才报错，不会偷偷改播别的视频。
- **B 模式防卡死/防标签堆积**：默认改用独立 Chrome profile 的 `--app` 窗口播放，
  只有一个页面、没有标签栏，启动后移到副屏并铺满工作区；不碰用户正常 Chrome 的
  标签页，也不再依赖“抢前台后往地址栏打字”（之前前台被 DeepSeek 占用时会导航失败、
  空窗口 + 兜底 URL 窗口叠成两个）。同一个 app 窗口开着时，下一条通过本机 DevTools
  的 `Page.navigate` 原地换页，不关窗/不新开/不抢前台；只有窗口被关或导航失败才重开。
  启动参数带禁用后台节流、遮挡暂停和恢复上次标签页的开关；开始独立模式时会按启动
  开关识别本模式以前启动的 Chrome 进程，清掉它遗留的 B 站/空窗口，不影响普通用户
  Chrome。历史进度连续 30 秒不动就判定视频没有在播放，先把 app 页面切到
  `about:blank`、标记失败并强制下一条，避免直播画面静帧被平台判违规。播放器里手动
  暂停也会触发强制下一条。`browser_isolated=false` 可退回旧版当前标签页复用，但会
  受前台焦点影响。
- **B 模式直播弹幕回复默认不回流到直播间**：`live_reply_send_danmu` 默认改为
  `false`，读到的弹幕/SC 仍然会由大肥鱼用 overlay + 语音回应，但不会再用登录账号
  往 B 站直播间发一条文字弹幕；需要时可在 `bilibili_commentary` 里显式改回 `true`。
  这样既不会让回复刷屏，也少一层“自动发弹幕”的机器特征。直播回复和视频
  `danmaku_replies` 同时加了开头称呼清洗：提示词要求直接接内容，输出再去掉
  “兄弟们/家人们/各位”等前缀，避免每句都带同一句口头禅。
- **回复里不再提“弹幕”两个字**：直播弹幕回复和视频 `danmaku_replies` 的提示词
  要求直接接内容，不描述消息来源；输出再加一层 `strip_danmaku_word` 清洗，
  把“弹幕说/这条弹幕/弹幕里”改成“评论说/这条评论/评论里”。已缓存的旧档案
  在播放时也会清洗，无需等缓存过期。`PROMPT_VERSION` / `LIVE_PROMPT_VERSION`
  已升到 v2，新生成的档案不会再把“弹幕”写进回复。
- **OBS 叠加层被视频盖住修复**：`obs setup` 创建 `Bili Scene` 时把
  `SetSceneItemIndex` 写成了 0（OBS 里 0 是最底层），后加的 `Bili Monitor` 会盖住
  侧栏和大肥鱼；现在改为放到源列表最顶层。当前场景已用 WebSocket 重排为
  `Bili Monitor` 在下、`Bili Scene` 在上，截图确认叠加层可见。
- **B 模式直播重音修复**：`Bili Scene` 页面现在通过 `/events?role=live` 登记为
  live 音频主，并在 TTS 起播时用 `/mark?ev=audio&role=live` 刷新音频时间；桌宠
  进程原有的 `Speaker` 会让位，避免“页面 TTS + 桌宠 TTS”同时进直播。当前 OBS
  页面已刷新，`/health` 确认 `live=1`。
- **B 模式直播音量偏小修复**：曼波 TTS 的峰值接近满刻度、平均响度只有约
  `-20 dBFS`，之前 `Bili Scene` 直接用 0.9 的音量播放，听感比游戏声音小一截。
  场景页现在走一条 TTS 母线：先提 6 dB，经过 `-12 dB / 8:1` 的限幅器后再补
  3 dB，平均音量提上来但峰值不削爆；OBS 浏览器源默认音量也从 0.9 提到 1.0。只影响 `/bili`
  页面，桌宠窗口和别的浏览器源不受影响。页面同时只允许 OBS 浏览器源出声：
  普通浏览器或直播姬内置浏览器打开 `/bili` 只显示字幕，不再登记 live、不再播
  第二路 TTS。
- **直播姬桌面叠加层（不用 OBS）**：桌宠右键新增「叠加层 ▸ 关闭 / 状态条 /
  电影框 / 弹幕栏 / 极简字幕」。叠加层是 Win32 真透明、置顶、点击穿透的全屏窗口，
  跟随桌宠所在显示器；直播姬用「显示器捕获」即可把游戏/视频和叠加层一起进画面，
  不需要 OBS 浏览器源。选择写 `logs/streamer_overlay.json`，下次启动自动恢复。
  数据只读 `/state` 和 `logs/bilibili_events.jsonl`，不发弹幕、不读页面，空闲时
  只在状态变化时重画。
- **桌宠右键「OBS 录像」**：一个入口既开也关——勾上表示正在录，再点一下停。
  点击时先问 OBS 的 `GetRecordStatus` 再决定开还是停，所以缓存晚一拍也点不错方向；
  这一局是自动玩开的，停的时候顺手收尾成 `recordings/<游戏>/run-NNNN/run.mp4`，
  否则只停 OBS（文件留在 OBS 自己的输出目录）。OBS 没开、WebSocket 没开、密码不对
  都只打印一句能照着做的提示，连接失败不上抛到窗口线程，右键菜单不会卡。
  实现是 `src/dafeiyu/recording/manual.py`（`probe` / `toggle`），菜单项 id 70。
- **游戏详情里编辑策略、查看资料**：控制台「游戏」页的游戏列表点进去，就是该游戏的
  详情面板——上半部分是可编辑的策略文档（`decision/knowledge/ironclad/OS.md` 决策手册、
  `references/*.md` 资料笔记、`defect/OS.md`、`persona/*.md` 主播稿），保存不限字数、
  立即写盘（旧的 15000 字节 / 215 行 / 4600 token 只作建议提示，`check_skill.py` 里
  也降级为 warning）；下半部分是「游戏资料」：
  `corpus/data/eng/*.json` 的条数统计（卡牌 / 遗物 / 敌人 / 药水 / 事件…）和只读 JSON
  预览（超过 20KB 截断）。路径白名单（不能写 `prompts.py` 或包外文件），写入按字节 + LF，
  写坏自动回滚。接口 `GET/POST /api/skill?game=<id>`，实现放在
  `games/slay-the-spire-2/skills.py`（核心不硬编码知识库/资料路径）。

### Fixed

- **A10 路线规划：开局不规划 / 逐格走一步看一步**：`--mode llm` 下 MAP 直接交给模型，
  `_choose_map` 里 commit 好的整幕计划从没被刷新/读取——日志一直刷 `ROUTE_PLAN_STALE`，
  开局还可能照着上一局/上一幕的计划走。现在：① 每幕第一张图（以及计划 stale 时）先
  `route --commit` 生成整幕计划；② Act1 硬禁精英，规划器直接过滤带精英的路径，
  Act2+ 只有防御≥6/AOE≥1/药水≥1 且血≥60% 才允许「考虑」精英；③ 候选精英必须评估
  「精英之后到 Boss」有没有营火/商店，没有就降权；④ MAP 的 ctx 给出 `route.next`，
  菜单标 `plan`，精英项在禁令下标 `ban`，`check` 直接拦（模型要硬闯得写 `allow_off_route`）；
  ⑤ 偏航（选了计划外的格子）也会被拦，下一格不在可选点里则立刻重排；可选点全是精英时
  自动解禁并标 `forced`，避免单线地图把整屏封死。
- **拿了奖励却说没拿（卡牌奖励 / 涅奥选牌）**：`71f1acb` 把「屏幕里有
  `select_deck_card`」一律当牌组多选、跳过模型走本地——可 mod 的卡牌奖励屏和
  「三选一」的涅奥祝福也带这个动作，于是模型再也看不到自己拿了哪张牌；紧接着的
  奖励屏 `reward.card_options` 又是空的，ctx 还写着「没有卡牌选项」，台词就变成
  「奖励没牌可拿 / 涅奥没给好处」。现在只有「从牌组挑 N 张」（移除/升级/变化/附魔，
  或 `min/max=0` 的盲选）才走本地，卡牌奖励和单张选牌交回模型。
- **`collect_rewards_and_proceed` 静默塞牌**：奖励屏上还有没打开的 Card 奖励时
  （`pending_card_choice=false`），直接 collect 会让 mod 把**第一张**牌塞进牌组，
  而台词说的是「卡牌就不贪了」。现在 `actions.check` 会拦下来，提示先
  `claim_reward` 打开、再 `choose_reward_card` / `skip_reward_cards`；CLI 路径
  （`sts2.unclaimed_card_reward`）早就这么拦了，LLM 菜单路径补上。
- **奖励屏 ctx 不再误报「没有卡牌选项」**：奖励列表里的 `Card` 只是一个没打开的
  卡牌奖励，`reward_eval` 现在给它标 `claim_first` 并提示「先 claim_reward 再看牌」，
  不再返回 `skip:true / 没有卡牌选项`；奖励菜单的名称也退到 mod 真正发送的
  `description`（之前只有 "Card"/"Gold"）。
- **斩杀后写 end 被整手拒绝（看起来像敲牌卡住）**：模型排「斩杀牌 + end」时，
  `sim.validate` 把尾部的 `end` 也算成「清场后还有动作」，整手拒绝 → 重试两次 →
  本地兜底，一步要拖 20–30 秒（f11 t2 连拒两次、f9 t2 一次）。batch 本来就会
  跳过清场后的 end（`act.clear_overflow_error` 就是这么放行的），现在
  `sim.validate` 对齐：清场后只允许尾部的 end，别的动作仍然拒绝。
- **营火锻造/删牌「选完不确认」卡死**：mod 的 `select_deck_card` 只选不确认，
  而且 `selection.selected_count` 经常滞后为 0；模型看到 0/1 会再点同一张，可这个
  动作是**切换**——第二下把选择取消，界面原地不动（run-0049 f7 实测，最后被 stalled
  收尾）。现在「选 1 张 + 确认」屏在点牌后自动补一次 `confirm_selection`：先等
  0.35 秒让选择生效（run-0049 f21 商店删牌时确认抢跑，点了等于没点），确认后再
  最多等 5 秒让界面离开 `CARD_SELECTION`（否则下一回合读到旧界面，又点一次把
  选择取消；其实牌已经删掉了）。菜单在已选够时只留「确认（已选 N/M）」，不再列牌。
- **奖励屏「跳过选牌」死循环**：mod 的 `skip_reward_cards` 只关掉选牌，Card 奖励
  仍留在奖励列表里 `claimable`，下一步又从奖励屏点 `claim_reward` 打开同一张牌，
  REWARD→CARD→REWARD 无限循环（run-0005 f1 实测卡了 300+ 步）。现在跳过选牌后
  自动 `collect_rewards_and_proceed` 收掉奖励屏；LLM 直接选「跳过选牌」时也补这一步。
- **Boss 战 / 重大损失还是不用药水**：Boss 战经常「两瓶药水带进棺材」——run
  `75S0RYFKEH` f17 打到 18 血才交迅捷、5 血才交赌徒，已经救不回来。三层修复：
  1. `act._guard_potion` 执行层硬拦：这一手会致死，或 Boss 战净掉 ≥18% / 普通战
     ≥28% 最大生命（或 ≥40%/50% 当前生命）且手里还有**能减伤**的药水（格挡/回血/
     抽牌/虚弱/易伤；缚魂药水实测无效、灰水只消耗手牌，都不算）时，拒绝「空手 end」，
     回执里点名槽位和数值；确认要留药可写 `{"allow_take":1}` 并在 say 说明；
  2. `ctx` 观察侧加 `POTION` 警告（全挡也只能压到净 X 时，直接列出槽位）；
  3. `sim.search` 的 `useful` 补上 `draw`（迅捷药水这类抽牌药之前根本进不了候选线），
     ctx 在有药水线时加一句图例：`@N` = 药水槽 N，要写 `{"p": N}`。
  `sim.is_boss_fight` 用「每幕 17 层的 Boss 层」+ 怪 id 词根（THE_KIN_BOSS /
  KIN_PRIEST）识别 Boss 战，不能只看整幕都有的 `run.boss_id`。
- **添柴随机换手牌后重新读局面**：`STOKE` 这类「消耗所有手牌 + 随机生成」的牌，
  以前打完还按旧手牌续排，随机给的牌等于没看。现在 sim 把它记成手牌未知，
  `validate` 在生成边界拒绝后续预排（连 `end` 也不让接）：只打这张、不写 end，
  读新 ctx 看完新手牌再决定；提示词和 OS.md 也补了这条规则。
- **台词别再刷「卡组还是 X 级」**：非战斗台词老拿卡组等级/卡组状态开场（商店、选牌、
  路线、营火连着刷）。提示词明令别报这类状态；`decision/speech.py` 播出去前整句摘掉
  「卡组还是 C 级」「卡组状态不错」这类小句（只留真正的取舍），同一开场 2 分钟内不重复说。
- **台词每回合只说一句**：`say` 数组只允许 1 个元素——提示词写死「say 只放 1 句」，
  `decision/act.py` 的 `normalize` 只保留第一条（战斗回合），界面决策也只播第一条，
  多余的记 `say_truncated` 日志。
- **「选择任意张牌」不再卡死**：mod 对这类多选给 `max_select=999999999` 当哨兵，
  `_selection_need` 以前直接拿它当目标张数，点满不了就 `CARD wait` 到 stalled
  （run-0040 f9「选择任意张牌进行替换」只选 3 张就停了）。现在按实际牌数封顶，
  选满即可 `confirm_selection`。
- **宝箱遗物必须拿走**：宝箱里 `has_relic_been_claimed=false` 时，`proceed` /
  `collect_rewards_and_proceed` / 领金币这类动作会被 `actions.check` 拒绝并提示
  「先 choose_treasure_relic」；拿完才放行。奖励没领完同样不许走（遗物有时挂在
  `reward.rewards` 里）。CHEST 屏会记 `chest_state`（acts/opened/claimed/relics/
  rewards），下次再漏就能直接定位是没动作还是标记提前为 true。
- **消耗（Exhaust）牌序**：契约终结消耗堆 <3 张时不可打（不再当 0 费空过）；
  邪眼/被遗忘的仪式按「本回合是否消耗过」结算（先消耗再打）；午夜按消耗堆逐张减费；
  跃跃欲试不再当成防御牌——2 费（+1 费），手里每张攻击回 1 能，所以要先打。
  `prompts.py` 和 `OS.md` 都补了「消耗序」；`references/cards.md` 里跃跃欲试、
  彼岸咆哮、契约终结、午夜、无惧疼痛、被遗忘的仪式 6 张按引擎数据更正；`compat/sts2.py`
  给这几张加了 `CARD_DB` 覆盖（站点抓的旧版中文描述会被奖励/商店读到，跃跃欲试以前
  就被当成「按力量加格挡」的防御牌）。
- **OBS 残留录制导致新一局开录失败**：上一段录制没停（手工开的、或 current 指针已清），
  `StartRecord` 一直报 `code=500`，状态显示没在录、文件却越录越长。现在开新一局前先
  `stop_recording_now()` 收掉残留段并记 `record_preempt`，再建本局目录开录。

## [2.4.1] - 2026-10-29

### Changed

- **自动玩录像按局切段（每局一个视频）**：以前只在 `play` 启动时开一次 OBS 录制，
  GAME_OVER 自动重开下一局也接着录，一晚上十几局会全在一个 mp4 里。
  现在死亡收尾旧录像，等新一局第一屏出现再开新的一段（`recordings/<game>/run-XXXX/`）；
  SL 回档属于同一局，不切段。OBS 万一开录失败会在 6 秒后重试一次，不阻断自动玩。
  日志新增 `record_split` / `record_new_run` / `record_retry` / `record_split_failed`。

### Fixed

- **强杀进程后 OBS 还在录**：`play --stop` 在 CLI 进程里没有录像 session，
  以前只回「已停」而不去停真正的 OBS，结果旧视频一直变长、下次开录报
  `StartRecord failed: code=500`。现在按 OBS 实际状态 + `.current.json` 收尾上一段；
  新会话启动前也会自动收掉上一进程留下的 `status=recording` 残留。

## [2.4.0] - 2026-10-29

### Added

- **控制台直接编辑人设 / 梗**：「人设配置」页新增编辑器，可选并保存
  `persona/dafeiyu/card.json`（名字 / 性格 / 招牌 / 禁语）、`style.md`（说话风格）、
  `persona/memes/*.json`（通用梗）、`db/memes.json`（118 条梗列表）和主播台词文档
  （`games/<game>/persona/speech.md`、`lines.md`、`quips.md`）。
  - 服务端只认白名单文件名（不传路径），JSON 先校验，写坏自动回滚；
  - 梗列表保存后自动重建 `db/sts2.db` 的 `memes` 表（和 `tools/memedb.py` 同一张表）；
  - 人设卡保存后立即重载 `Persona`，下一条台词生效；页面提供「还原 / 重载人设」。
- **自动玩恢复 SL（时间回溯）**：当本回合穷举所有牌序都会死、且不是第 1 回合时，
  runner 自动 `save_and_quit` → 主菜单 `continue_run`，把整场战斗还原到第 1 回合重打。
  一局最多 6 次、同一场战斗只回档一次，回档时播报「时间回溯」。
  （以前只有人工在对话里跑 `tools/sts2.py do save_and_quit` 才能用。）

### Fixed

- 战斗 / 宝箱结算后的地图节点晚加载不再被卡住守卫当成无进展收尾：
  `MAP_NO_AVAILABLE` 会先重读最多 4 秒，必要时点 `proceed`，仍无节点才 `WAIT_MAP`。

## [2.3.0] - 2026-10-29

### Added

- **桌宠侧面的直播间互动气泡**：B 站监听现在把每条互动推给桌宠窗口
  （`StreamerWindow.room_event`），渲染在精灵侧面，和头顶的说话气泡分开：
  点赞 = 爱心、弹幕 = 内容、进直播间 = 「欢迎 名字」、送礼物 / 醒目留言 / 上舰 = 大米饭表情。
  - 频控：点赞和同款礼物 2 秒内合并成 `×N`（连点 30 个赞只出一个气泡），
    同时最多 4 个，自动淡入淡出；观众内容去控制字符/换行、限长，不会把窗口刷爆。
  - 图标用 PIL 画（PIL 没有彩色 emoji 字体）：爱心是双圆+三角，大米饭是碗+米饭+热气，
    浅色气泡上也看得清。
  - 只画在桌面桌宠窗口；OBS 网页源继续走主播气泡，避免两边重复。

## [2.2.2] - 2026-10-29

### Changed

- **说话不再念血条**：`combat_goal` / `noncombat_goal` 不再把 `HP x/y`、金币、牌组张数写进目标文本
  （模型会顺手抄进 say），`SYSTEM_PROMPT` / `NONCOMBAT_PROMPT` 的台词规则改成「1–2 句人话，
  别报 HP/金币流水账，数字只给伤害/价格/斩杀线」，界面台词也要求带一点口癖或自嘲。
  本地兜底战斗台词 `_combat_bark` 按局面给情绪（残血叹气、斩杀收工、翻车自嘲），
  不再每回合念「它多少、这手打多少」。
- **台词过滤兜底**：新增 `decision/speech.py::tidy_say`，战斗（`act.py`）和界面（runner）
  在播报前统一摘掉「HP 64/80」「我 61 血」这类血条片段；整句只有血条就这一句不说，
  价格/金币保留。模型偶尔不听话也不会再念血条。
- **叹气能读出来了**：`persona/speech.md` 增加「情绪」一节；台词 lint（`persona/lint.py`、
  `tools/check_caster.py`）允许叹气/拖音替代数字，并新增「别念血条」「叹气太多」两条提醒。
  `MamboVoice` 对叹气句按 `sigh_speed`（默认 0.94）降速合成，缓存按语气分盐，
  曼波会把「唉……」读得更软——观众听到的叹气是真实情绪，不是每句都有的口癖。

## [2.2.1] - 2026-10-29

### Changed

- **直播合规**：overlay 增加「AI 生成 · 虚拟形象」角标（2025-09-01 起《人工智能
  生成合成内容标识办法》要求主动标识）；同一句台词 4 分钟内不再重复播
  （`_say_screen` 去重），减少「长时间重复同一内容/挂机」的机器特征。
  `docs/LIVE_OPS.md` 增加「B 站合规」清单：挂机/定格画面、AI 标识、标题用词、时长。
- **LLM 决策提速：默认关思维链**。实测 `deepseek-flash` 开着 thinking 时一次
  战斗决策要 24s、`completion_tokens=4096` 全是 reasoning、`content` 为空，
  正好撞上 4096 预算和 25s 超时；界面决策 10–12s/步也主要是它在耗。新增
  `autoplay_llm_thinking`（默认 `off`）→ 请求带 `thinking:{"type":"disabled"}`，
  同一局面 1.7s 出计划；`on` 恢复思维链，`auto` 只让战斗跟模型默认。provider
  不认这个字段时自动去掉重试。`llm_usage` 行和 Token 面板每一步都多一个耗时 `ms`。
- **界面决策改为「先说话再操作」**：`_say_screen` 从 `actions.execute` 之后移到之前，
  和战斗回合一致（`cmd_batch` 本来就是先推字幕再出牌）；观众先听到理由再看到画面变化。
- **牌组多选（移除/升级 N 张）改为逐张点不同牌**：不再依赖 mod 的 `selected_count`
  （旧 mod 恒 0，点第二次会把第一张取消），本地按 0、1、2… 顺序点，点满 `need` 张
  后调 `confirm_selection`；`--mode llm` 下这一屏直接本地处理（模型看不到已选张数，
  只会重复点同一张，run-0113 一晚烧了 530 万 token）。删掉不适用于 12+ 张牌组的
  `ui_pick_cards` 鼠标坐标兜底。
- **选牌会挑牌了**：牌组多选不再按 0、1、2… 瞎点，复用 `decision/noncombat.select_eval`
  （和奖励/商店同一套牌面评估）：**移除/变化**先挑打击（+20）、再防御（+12）、诅咒（+35），
  核心牌 −45 保护起来；**升级**打击只给 5 分、防御 12 分，BASH／耸肩／恶魔之焰这类
  核心牌 +36 优先。评估失败才退回原顺序。

### Fixed

- **战斗规划/费用统一到 `sim`（P0）**：`advisor.build_plan` 以前自己维护一份静态费用模型
  （只读当前 `energy_cost`、只看伤害/格挡/易伤/虚弱），和 `sim.validate`、执行层各算一套：
  回能牌不进候选、引擎把整手攻击报 0 费时当成全免费、抽牌/变费后的手牌不参与排序。
  现在本地计划直接取 `sim.search` 的候选与逐步费用（`cost_of`/`play`），
  `spec_from_plan` 带 `skip_budget`，不再让 `cmd_batch` 按初始手牌预扣费误拒合法序列。
- **战斗执行结果必须核对（P0）**：`_combat_local` 以前只要 `cmd_batch` 返回就记
  `COMBAT Ndmg`，`BATCH STOP`/`PLAN_OVER_BUDGET`/动画没落地都会被当成打赢，卡住检测
  也看不出来。现在解析回执里的失败标记并对比前后局面指纹（回合/能量/手牌/双方血量/
  格挡/敌 HP），没有推进就落 `combat_no_progress` 并记 `COMBAT no-progress`；
  `SystemExit`/`RuntimeError` 只记 `combat_exec_failed`，不再把整局判死。
- **等待指纹补全（P1）**：`sts2._fingerprint` 增加玩家血量/格挡/关键 Buff、敌方格挡与
  Buff、药水槽；0 费防御只加格挡、吃 Buff 药水这类「能量不变」的动作以前被当成没变化，
  一直等到 timeout。
- **狂怒格挡不再虚报（P1）**：`sim.max_block` 只按「剩下能量真的打得出去」的攻击牌算
  狂怒补甲（2 张 1 费攻击只剩 1 费时以前会 +6）。
- **清场后多写的动作前置拒绝（P1）**：`sim.validate` 在斩杀那一步后还有动作时直接报
  「已经清场，后面还有 N 步」，不再打到一半才失败。
- **锻造/升级选牌撞键卡死（run-0009 f16）**：`_deck_clicks` 的键只有 run+提示+牌表，
  同一局两次「锻造选 1 张」且牌表没变时会以为已经选满，整屏 `CARD wait 1/1`；
  键补上楼层，且发现计数残留（`selected_count=0`、屏上不需要确认）时清零重按，
  不再 `save_and_quit` 丢掉正在打的整局。

- **BOSS 眩晕卡死（run-0114）**：眩晕回合手牌全部 `playable=false`，模型还在排
  「COLOSSUS」「STRIKE」等牌，执行层 `sts2.py` 用 `sys.exit(3)` 拒绝，`SystemExit`
  绕过主循环的 `except Exception` → 记成 `ERROR SystemExit: 3`、三次后判卡住停播。
  ① 战斗里 `play_card` 不在可用动作、`end_turn` 在时直接结束回合（`COMBAT pass`，
  不进模型、不烧 token）；② `_call` 把 CLI 的 `SystemExit` 转成 `RuntimeError`，
  不再逃出主循环；③ 校验文案从「要 999 费，只剩 2」改成「本回合不能打（眩晕/不可用）」，
  并提示「整手都不能打就只写 end」；④ `combat_goal` 补一句同样提示。
- **商店删牌服务进菜单**：`remove_card_at_shop` 之前不在 `actions.available()` 的
  菜单里，模型想删牌时只能反复开/关货架，实机在一次商店里空转 18 分钟（run-0116）。
  现在货架打开且服务可用时菜单会给出「删牌服务 100金」（模组接口不收参数，
  所以 `i` 必须是 None），选中后进入选牌界面，脚本按价值自动挑牌、确认；
  另外「开/关货架」两种返回并成同一个卡住键，来回切换也会被防呆收掉。
- **商店原地开/关的循环**：货架关着时菜单无条件给「看货架」，模型遇到样样买不起
  （实机 90 金、祭品 75 金但药水槽满）就会「开→关→开→关」，而且卡住检测只认本地兜底的
  SHOP open / SHOP close，认不出 LLM 的 SHOP open_shop_inventory 标签。现在：
  ① 买得起任何东西或付得起删牌才给「看货架」；② 两种标签都归一成 SHOP toggle 防呆。
- **营火锻造**：它和休息共用 choose_rest_option（靠 i 区分），菜单一直有；
  「锻造→选牌→确认」这条链路也验证过（select_deck_card + confirm_selection）。
  实机出现过「营火里没有锻造」的说法，现在每次营火会记一条
est_menu
  （菜单项 + 游戏给的原生选项/是否禁用），下次能直接判断是游戏禁了还是菜单漏了。
- **白名单菜单审计**：逐屏对了一遍模组 `GameActionService` 真正支持的动作，补上
  漏掉的出口——① 开局「选牌包」`BUNDLE_SELECTION` 之前根本没有菜单，整局在第 1 层
  以 `UNKNOWN BUNDLE_SELECTION` 收尾（run-0116 之前那次就是这么停的）；② 确认框
  （`confirm_modal`/`dismiss_modal`）现在优先于底层屏幕，否则放弃/存档退出后会卡在
  `WAIT_MAIN_MENU`；③ 地图改用模组的 `available_nodes` 下标（之前靠
  `nodes.is_available` 的过滤顺序碰巧一致）；④ 选牌界面补 `close_cards_view`；
  ⑤ 奖励只列 `claimable` 的、宝箱不列已领走的遗物；⑥ 删掉模组根本不发的死项
  `claim_all_rewards` / `close_chest` / `leave_shop`。另加一条守卫测试：菜单里出现的
  动作名必须都在模组动作表里。已知仍不支持：`CAPSTONE_SELECTION`（模组没暴露选项
  数量，进不到这个屏幕）。
- **CLI 托管不再被外部状态覆盖弄停**：`python main.py play` 的等待循环只看
  `logs/autoplay.json` 的 `running`，别的进程（跑测试、toy-duel 验收）把它写成
  `game=toy-duel, running=false` 时，正在直播的这一局会被静默停掉（实机 15:54）。
  现在 pid 不是本进程时继续等，不跟着退出。
- **商店的遗物/药水也带上效果**：商店 payload 只给名字和价格，ctx.shop.items
  里的遗物/药水没有说明，模型只能凭名字猜，历史上一件遗物/药水都没买过。
  现在从本地 RELIC_DB/POTION_DB 补效果文本（菜单和 ctx 都补），药水标
  use: 仅战斗，提示词也点名「遗物/药水按效果买」。
- **血量以战斗中的真实值为准**：`_run_meta` 在 COMBAT 下优先用
  `combat.player.current_hp`（`run.current_hp` 偶尔慢一拍，overlay/字幕会显示旧血量），
  两者不一致时记 `hp_mismatch`；战斗目标文本也改用这份 HP。
  另外 `calc.line` 直接给出「回合末血」（`calc.hp_after`），提示词要求 `fx.hp` 照抄它，
  别自己减——run-0114 里模型写 74、账本 69 被反复打回。
- **「从牌组移除 N 张」终于能确认（mod 重编）**：根因在 mod，不在客户端。
  `NDeckCardSelectScreen` 的确认按钮是私有字段 `_confirmButton` /
  `_previewConfirmButton`，v0.8.0 的 `ConfirmDeckSelectionAsync` 只找
  `%PreviewConfirm` / `%Confirm` 节点 → 永远点不到；`select_deck_card` 只回
  `pending`，`min/max/selected_count` 也一律 0。现在用 .NET 9 SDK 从
  `lxllxl233/STS2-Agent` v0.8.0 源码重编（分支 `compat/v0.107.1`），补丁：
  ① `Player.CanUseOrRemovePotions` 在 v0.107.1 已删除，改回 `true`（原先靠 IL 补丁）；
  ② 反射私有确认按钮，并把牌组屏接进 `CanConfirmSelection` /
  `ExecuteConfirmSelectionAsync`；
  ③ 从提示文本解析 min/max（先去掉 BBCode），已选张数读 `_previewCards`；
  ④ 点确认前用游戏自己的 `CheckIfSelectionComplete` 把关（选不满就点确认，游戏会
  只移除已选张数并直接结束事件）；
  ⑤ `select_deck_card` 不再自动确认，交给 `confirm_selection`。
  实测（2026-10-29，游戏 v0.107.1）：涅奥「松动羊毛剪」（移除 2 张）`min/max=2`，
  点 1 张时 `confirm_selection` 不出现、点满 2 张才出现，确认后回到事件、牌组 11→9。
- **日志一天能写十几 GB（mod 每张牌每帧都报 Localization 错误）**：`GetCardRulesText`
  直接读 `Description`/`RulesText` 再调 `GetFormattedText()`，缺动态变量时游戏会给
  **每张牌每条 /state** 打一条 `Localization formatting error` 堆栈；实测一次会话
  `godot.log` 涨到 14 GB（磁盘会被写满）。现在先走游戏自己的
  `GetDescriptionForPile`（带正确数值，`打击` → `造成6点伤害。`），原始文本只作兜底。
- **删牌多选屏空转一晚上（run-0113）**：涅奥「移除 2 张牌」把游戏带进
  `CARD_SELECTION`，mod 的 `select_deck_card` 只回 `pending`、`selected_count`
  永远 0；LLM 每 13 秒回一次「选牌 select_deck_card 0」，从 03:51 卡到 09:58，
  **1332 步、烧掉 530 万 token**（占整局 82%）。三处修：
  ① `_stall_key` 现在把重复的 `CARD ...` 返回串算作没进展（真正选上牌时串会变）；
  ② 识别「min/max 都是 0 的牌组多选」后不再问 LLM，直接本地处理，点一次没进展
  就交给守卫；
  ③ 守卫判定卡住时**自动 `save_and_quit` 一次**并从主菜单 `continue_run` 回滚
  （文档里的临时绕过），仍卡住才收手。`NONCOMBAT_PROMPT` 同时写明这类选项别选。
- **无人值守连播的加固**（睡觉前检查）：
  ① `LLMPlanner` 对超时/连接重置/429/5xx 退避重试 3 次（1.2s、2.4s），401/403
  不重试并提示 Key 失效；坏 JSON 重试时把第一次的 token 也并进账单；
  ② 战斗侧任何 `LLM_FAILED`（包括网络异常）都本地兜底，不再整局停手；界面侧
  只有「模型回了但不可执行」才重问一次，超时/限流不再叠加第 2 层重试；
  ③ 主循环加游戏看门狗：约 60 秒读不到局面就重启游戏（3 次配额，稳定 10 分钟
  重置），启动超时也会重启一次；GAME_OVER 回主菜单失败重试 3 次；每步状态刷新
  异常不再打成 fatal；
  ④ `autoplay.jsonl` 超 6MB 自动压缩，`llm_usage` 行全部保留（token 总额不变）；
  ⑤ 录像盘剩余 < 2GB 自动跳过录像（游戏照打）。
- **提示词里还有三处「说错」**（同一类：说给模型听的和执行器实际做的相反）：
  ①`seq 最后必须是 end` 与 `sim.validate` 允许半手（返回 `mid:1`）冲突，模型抽完牌
  会硬结束回合；②非战斗 `i 用 choice.*[].i`，宝箱/选牌的 `choice` 没有 `i` 列表，
  改成「照抄 `context.acts` 里那一项的 `i`」；③`fx.p 不会被执行` 在兼容层加上后
  已不成立（会被挪进 seq），改成「药水别写进 fx」。`combat_goal` 同步，OS.md 同步，
  加回归测试钉住这三类断言。
- **直播间一直「连接失败」但发弹幕正常**：带 Cookie 时 `getDanmuInfo` 的 token
  是绑账号的，而监听 auth 里 uid 硬编码 `0`，B 站 0.1 秒内直接断开（连 close
  frame 都没有），状态只剩「连接断开，正在重连」。现在从 `nav` 取 Cookie 对应的
  uid（10 分钟缓存），握手带 `Cookie/Origin/UA`，auth 用真实 uid；Cookie 失效时
  状态明确写「登录态不可用」。实测监听 12 秒保持 connected。
- **不会用药水（run-0112）**：`SYSTEM_PROMPT` 把药水写成「fx 的 p」，但执行器
  只认 `seq` 里的 `{"p": 槽位, "t": 目标}`，`fx` 只校验 e/hp/take——模型以为喝了，
  实际一滴没喝。提示词改成 seq 写法并注明 fx 限制，决策手册同步；
  `act.normalize` 兼容旧习惯：`fx` 里带 `p/potion` 时自动挪成一步药水放到最前面。

### Added

- **界面决策也走 LLM**：新增 `games/slay-the-spire-2/decision/actions.py`——把
  地图/事件/营火/商店/宝箱/奖励/选牌的可点项做成白名单菜单，模型只回
  `{"say": [...], "act": "...", "i": N}`；禁用项、越界下标、买不起的货、
  `save_and_quit` / `use_potion` 这类越界动作全部拒绝。`runner.py::_screen_llm`
  在 `--mode llm` 下先问模型，带原因重问一次，仍不行才落本地兜底并记 `llm_fallback`。
- **非战斗提示词**：`decision/prompts.py` 新增 `NONCOMBAT_PROMPT` / `noncombat_goal`，
  写清事件不可逆代价、血闸路线、营火/商店/奖励取舍和 `say` 的数字纪律。
- **决策手册与人设落位**：DSH 技能目录删除，内容拆进项目：
  `decision/knowledge/ironclad/OS.md` + `references/`、`decision/knowledge/defect/OS.md`、
  `persona/speech.md` / `persona/lines.md` / `persona/quips.md`、`docs/LIVE_OPS.md`；
  新增两份目录说明和 `tests/test_knowledge_layout.py`（无 `.dsh` 路径、文件齐全、提示词限额）。
- 测试：`tests/test_actions.py`（菜单/校验/执行）、`tests/test_autoplay.py` 增补
  「llm 先问模型 / 拒绝越界动作 / 重问一次 / 规划失败兜底 / 过场不过模型」5 条。
- **B 站直播间互动**：监听从「只收礼物」扩到进入 / 关注 / 分享 / 点赞 / 弹幕 /
  礼物 / 醒目留言 / 上舰（含 `INTERACT_WORD_V2` 的 base64 protobuf 解码），
  写 `logs/bilibili_events.jsonl`，**只保留最近 120 条**（超 200KB 自动压缩）。
  控制台新增「直播间」页：类型筛选、最近一条、分类计数；右侧输入框可直接发弹幕
  （`POST /api/bili/send` → `live.bilibili.send_danmu`，需要带 `bili_jct` 的 Cookie）。
- **Token 账单**：`AutoplayHost.add_usage` 增加 `note`/`step`，`token_usage()` 汇总
  `logs/autoplay.jsonl` 的 `llm_usage`；控制台「Agent 流程 → Token 花费」显示
  合计 / 输入输出 / 调用次数 / 按类型 / 最近 20 步每步 token（含跨进程累计）。
  战斗回合记 `战斗 f7 t3`，界面决策记 `地图 choose_map_node 2` 这类说明。
- **跨局上下文隔离**：新增 `tests/test_context_isolation.py`，证明每次规划只发
  system + 当前 `goal/feedback/context`，换局后不带上一局的 run_id、楼层、牌组、
  台词或聊天历史。

### Fixed

- **营火卡死（run-0110）**：`_rest` 以前只按血量挑标题、不看 `is_enabled`，满血 1/1 且牌
  全升级时每轮都点被禁用的「锻造」，被 409 打回后原样重放 1628 次。现在只在可用项里选，
  全禁用时走 `proceed` 或报 `REST all disabled`。
- **主循环原地打转**：新增 `_StallGuard`（同画面同失败连续 3 次即收手），失败键用
  `_error_code()` 归一异常串；旧的 `("CHEST","REST no options",…)` 字面量判断不再生效。
- `tools/check_skill.py` / `check_caster.py` / `test_rules_in_ctx.py` /
  `test_docs_consistency.py` 改指新位置，并去掉已不存在的 YAML frontmatter 检查。
- **「让她玩」启动上一局的游戏**：`agent/play.py::_game_id` 以前先读
  `logs/autoplay.json` 的 `game`，而 toy-duel 验收跑完后这份状态文件一直留着
  `game: toy-duel`，于是一点「让她玩 ▸ LLM 规划」就去启动玩具决斗并提示
  「只支持 local 模式」，用户以为 LLM 拉起的是《杀戮尖塔 2》。现在启动以
  `config.game` 为准，只有状态文件确实 `running` 时才按它停手/看状态；
  `AutoplayHost.status()` 也不再让上一局的 `game/mode/pace` 盖住当前宿主。
- **营火/宝箱/选牌/事件不播报**：界面决策只调了 `_remember_speech`（它只设
  「等语音」的计时窗口），没有调 `overlay_say`，所以 llm 模式下这些屏虽然记了
  台词，桌面宠物和 OBS 一句都听不到。现在点完动作后走 `_say_screen`：
  `sts2.overlay_say(tag_for(screen), text, "", state)` 真正推送，tag 按屏幕取
  （营火/商店/宝箱/选牌…）。补回归测试断言快照的 `overlay_say` 收到台词与 tag。
- **同类卡死审计（营火 / 商店之后）**：顺着这两个直接原因把「一步没推进就整局停」
  的同源问题一起收了：
  ① `UNKNOWN`/`* unknown` 原来第一次出现就收尾 → 现在同画面连续 3 次才停，
  动画/过场/兜底缺口多给两步；② `CARD wait N/N`、`CARD ui wait`、
  `WAIT_MAIN_MENU`/`WAIT_CHARACTER_SELECT` 这类「没推进」原来不计入原地打转，
  会一直转到 5000 步上限 → 现在计入 `_StallGuard`；③ 宝箱/选牌的白名单菜单缺
  `claim_all_rewards`、`choose_reward_card`、`skip_reward_cards` 等真实动作，
  llm 模式下这两屏其实一直在悄悄走本地启发式 → 菜单补齐；本地兜底也补了
  `claim_all_rewards`/`close_chest`/`dismiss_modal`。战斗侧确认 `_combat` 早就会在
  `LLM_FAILED_*` 时落到本地算账，不用改。
- **商店卡死（run-0111）**：货架打开时游戏只给 `close_shop_inventory`，不给
  `leave_shop`；`decision/actions.py` 的商店菜单和 `_shop` 兜底都没列这一项，于是
  模型在买完牌后一次 `Read timed out`，兜底返回 `SHOP unknown`，整局停在商店。
  现在菜单有「关闭货架」，兜底先关货架再离开；`_screen_llm` 对超时/网络错也重试
  一次（界面决策超时 25s→40s），两次都失败才落本地。补 4 条回归测试。
- **B 站连接每 20 秒刷一屏 traceback**：B 站弹幕服务器不回 WebSocket 层 ping，
  客户端默认 keepalive 每 20s 报 `sent 1011 keepalive ping timeout` 并打印三条
  链式栈；重连又被指数退避拖到 60s。现在 `connect(..., ping_interval=None)`
  只发 30s 一次的 JSON 心跳，兜底过滤器收掉 `keepalive ping failed`，连上后掉线
  3s 重连（连不上才退避），并把「连接断开，正在重连」写进 `logs/bilibili.json`。

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
