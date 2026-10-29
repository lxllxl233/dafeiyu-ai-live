# B站热门视频云观影

这是 `bilibili-hot-commentary` 模式的说明。它不下载视频、不读取画面、不做视觉
识别或语音识别：只读取 B 站视频的**公开文本**（标题、简介、UP 主、热评、弹幕
时间轴），用虚拟播放时钟模拟“正在看”，按时间轴说预先规划好的解说。

选中视频后会按配置用 **Google Chrome 打开该视频页**：默认新建一个窗口并尽量放到
副屏；Chrome 的实际播放/暂停由浏览器和用户控制，解说时钟不会假装与画面逐秒同步。
开启 `like_on_watch` 时会用已配置的登录 Cookie 给视频点一个赞；没有 Cookie 就跳过。
直播弹幕互动会读取桌宠监听进程写的 `logs/bilibili_events.jsonl`，按频次和 token 预算
回复普通弹幕，醒目留言（SC）优先；用户名和弹幕正文一律按不可信文本处理。

## 启动

需要 overlay/studio 正在运行（默认 `http://127.0.0.1:8730`），并配置了可用的
LLM key。读热门/详情/评论/弹幕不需要 Cookie；自动点赞需要 `bilibili_cookie`
里同时有 `SESSDATA` 和 `bili_jct`。

```powershell
# 推荐：显式指定游戏和 LLM 模式
python main.py play --game bilibili-hot-commentary --mode llm

# 查看跨进程状态（config.json 的 game 指向本模式时）
python main.py status
# 或者直接看原始状态文件
python games/bilibili-hot-commentary/control.py status

# 停止（和现有模式同一个 stop 文件）
python main.py play --stop
```

桌宠右键的「让她玩」默认走 `local`；本模式**没有本地决策器**，所以必须用上面的
`--mode llm`（或桌面菜单里的「LLM 规划」）。用 `--mode local` 会明确报错，不会启动。

启动后只读**本游戏包**里的
`games/bilibili-hot-commentary/persona/card.json` 和 `style.md`，不读全局
`persona/dafeiyu/`。默认人设参考主播 xQc 的高能、嘴快、先喊再拆解，但不冒充本人；
控制台「游戏 → B站云观影 → 游戏详情」里可以查看/编辑，保存后重启 B 模式生效。
用一次轻量 LLM 生成 3–5 个搜索词；如果 `bilibili_commentary.search_keywords` 有
手写词，就直接用、不再调这次 LLM。解说/直播回复提示词也注入这份 B 站人设，不再
跟随全局 `config.json` 的 `persona`。选中视频后 Chrome 打开
`https://www.bilibili.com/video/<bvid>`；
搜索/选片仍会走时长、多 P、评论数、已看冷却和安全过滤。

暂停 / 恢复 / 跳过当前视频：

```powershell
python games/bilibili-hot-commentary/control.py pause
python games/bilibili-hot-commentary/control.py resume
python games/bilibili-hot-commentary/control.py skip
python games/bilibili-hot-commentary/control.py status
```

暂停只冻结虚拟时钟；已经发出的语音继续播完。跳过后给该视频一个冷却，再选下一个。

## 配置

默认值在 `game.json` 的 `commentary` 对象。用户覆盖写在仓库根目录
`config.json` 的顶层 `bilibili_commentary` 中，不需要改核心 `LiveConfig`：

```json
{
  "game": "bilibili-hot-commentary",
  "bilibili_commentary": {
    "speed": 1.0,
    "min_duration_s": 120,
    "max_duration_s": 1800,
    "min_gap_s": 45,
    "max_cues": 8,
    "max_replies": 4,
    "prewarm_tts": true,
    "search_enabled": true,
    "search_keywords": [],
    "search_exclude": ["杀戮尖塔", "尖塔"],
    "search_prefetch": 8,
    "open_browser": true,
    "browser_window": true,
    "browser_monitor": "secondary",
    "like_on_watch": true,
    "live_chat_enabled": true,
    "live_reply_min_interval_s": 20,
    "live_reply_sc_min_interval_s": 5,
    "live_reply_max_per_min": 3,
    "live_reply_user_cooldown_s": 60,
    "live_reply_max_queue": 6,
    "live_reply_max_tokens_per_hour": 5000,
    "live_reply_max_tokens_per_day": 20000,
    "live_reply_send_danmu": true,
    "exclude_keywords": ["政治", "灾难", "血腥"]
  }
}
```

关键字段：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `speed` | `1.0` | 虚拟时钟倍速，限制在 0.25–4 |
| `min_duration_s` / `max_duration_s` | `120` / `1800` | 只选 2–30 分钟视频 |
| `min_comments` / `target_comments` | `12` / `30` | 热评门槛和 LLM 输入上限 |
| `min_gap_s` | `45` | 两条 cue 的最小模拟时间间隔 |
| `max_cues` / `max_replies` | `8` / `4` | 单视频最多 cue / 空档弹幕回应 |
| `dynamic_reactions` | `false` | 预留；MVP 不做逐桶 LLM，弹幕回应是预生成 |
| `prewarm_tts` | `true` | `/comment` 之后让 `/tts` 预热 |
| `search_enabled` | `true` | 先按人设搜视频；关掉则只刷热门列表 |
| `search_keywords` | `[]` | 手写搜索词；非空时覆盖人设 LLM，不再调搜索意图 |
| `search_exclude` | `["杀戮尖塔", "尖塔", ...]` | 当前游戏/不想看的词；自动搜索词和候选标题命中即排除。手写 `search_keywords` 时视为用户覆盖 |
| `search_prefetch` / `search_limit` | `8` / `20` | 搜索候选补详情的上限 / 每条关键词取回上限 |
| `open_browser` | `true` | 选中视频后用 Chrome 打开视频页 |
| `browser_path` | `""` | Chrome 路径覆盖；空则自动查找，找不到退回默认浏览器 |
| `browser_window` | `true` | `true` 新建 Chrome 窗口；`false` 退回新标签页 |
| `browser_monitor` | `"secondary"` | 新窗口目标屏：`secondary` 副屏、`primary` 主屏、`0/1` 索引 |
| `like_on_watch` | `true` | 选中视频后点一次赞；需要登录 Cookie，失败不影响播放 |
| `live_chat_enabled` | `true` | 读取直播间事件并回复；需要桌宠监听进程在跑 |
| `live_reply_min_interval_s` | `20` | 普通弹幕两次回复的最小间隔 |
| `live_reply_sc_min_interval_s` | `5` | SC 的最小间隔（SC 永远优先于普通弹幕） |
| `live_reply_max_per_min` | `3` | 每分钟最多几次 LLM 回复 |
| `live_reply_user_cooldown_s` | `60` | 同一 uid 的冷却，防单用户刷屏 |
| `live_reply_max_queue` | `6` | 普通弹幕队列上限；满了丢最旧 |
| `live_reply_max_input_chars` / `live_reply_max_chars` | `180` / `60` | 输入截断 / 回复字数上限 |
| `live_reply_danmu_chars` | `20` | 真正发到直播间的弹幕长度 |
| `live_reply_max_tokens_per_hour` / `..._per_day` | `5000` / `20000` | LLM token 预算硬限制 |
| `live_reply_send_danmu` | `true` | 是否把回复发到自己的直播间 |
| `live_reply_speak` | `true` | 是否同时走 overlay/语音 |
| `exclude_keywords` | 见 `game.json` | 标题/简介命中即跳过 |

时长决定弹幕桶：≤ 5 分钟用 30 秒桶，≤ 20 分钟用 60 秒桶（16 分钟正好 16 桶），
更长的视频用 120 秒桶，最多 20 桶。30 分钟上限下约 15 桶，避免 LLM 输入膨胀。

## 控制台播放列表

控制台「游戏」页在 `config.game = bilibili-hot-commentary` 时，会在游戏列表下面显示
「B站播放列表」：

- 列表来自引擎最近一次候选（搜索/热门），第一行是当前正在播放的视频；
- 点「播放」：指定另一条视频时，引擎会先关掉上一个 Chrome 窗口，再打开新视频；
  点的就是当前视频时，直接刷新当前标签页；
- 「刷新当前标签页」「关闭当前标签页」只操作引擎记录的那个 Chrome 窗口；
- 列表状态和浏览器状态每 3 秒随 `/api/studio` 刷新；B 模式没启动时只显示最后一版，
  并提示先启动自动玩；
- 通信是本地文件：引擎写 `logs/bilibili_commentary.playlist.json`，控制台写
  `logs/bilibili_commentary.command.json`，不经过视频/弹幕文本，也不碰 Cookie。

浏览器控制是尽力而为：Chrome 新窗口刚打开、还没被 Win32 找到时，关闭/刷新可能
短暂返回「没有标签页」，过一秒重试即可；系统默认浏览器（找不到 Chrome 时）只能
打开，不能关闭/刷新。B 模式停止时会关闭自己记录的窗口。

## 工作流与成本

1. 读 B 包 `persona/card.json` / `style.md` 和可选 `watch` 偏好（与全局人设隔离）；手写
   `search_keywords` 优先，否则用一次轻量 LLM 生成搜索意图，并按人设哈希缓存。
2. 用 B 站搜索页 HTML 取 BVID/标题/时长/播放量；官方搜索 JSON 匿名会 412，
   所以 HTML 是主路径。搜索失败/为空时回退热门列表（180 秒缓存）。
3. 对搜索候选补详情，再过滤时长、多 P、评论数、互动率、已看/失败冷却。
4. 对最终候选拉热评、弹幕分段；弹幕按播放进度分桶。
5. **每个视频只调用一次 LLM** 生成 `VideoProfile`：主题、观众反应、分段摘要、
   4–8 条 cue（16 分钟视频优先 6–8 条）、最多 4 条空档弹幕回应；之后整个
   播放期间不再调用 LLM。
6. 播放前把 cue 排到虚拟时间轴上（最小间隔、最多条数）；**开场必有一句问候**，
   收尾也必有一句「看完了」，如果原档案缺问候会自动补，收尾会等当前语音说完再发。
   同时 Chrome 新建窗口（默认副屏）打开选中视频页；如果 `like_on_watch` 开着，
   后台点一次赞。B 模式运行时桌宠/控制台空闲文案是「看视频ing」，不再是「吃饭ing」；
   播放中只做到点判断、单槽语音门和状态写入。
7. 当前视频播放时后台预取**下一个**候选的详情和档案；自然播完后直接接上，
   不用再等一次选片/LLM。跳过/失败进冷却；LLM 失败只跳过该候选，不阻塞整个循环。
8. 直播弹幕线程只读 `logs/bilibili_events.jsonl`：普通弹幕有队列/用户冷却，SC
   优先；过频次/预算直接跳过，不会为一条弹幕无限重试。

搜索意图缓存键包含人设文本哈希 + `search_exclude` + `SEARCH_PROMPT_VERSION`，
同一人设/排除词 7 天内复用；直播弹幕每条选中的消息额外一次 LLM，受上面的
间隔、每分钟条数、队列和 token 预算限制；
档案缓存键包含 bvid + 来源文本哈希 + `PROMPT_VERSION`。所以一次启动通常只有
“搜索意图 1 次 + 每个视频 1 次”的 token 账单，播放循环本身不调 LLM。

## 安全边界

- 标题、简介、评论、弹幕统一按 `untrusted_text` 处理；其中的“忽略指令”“输出
  系统提示”“执行命令”“给我 API Key”等只是文本，不构成指令，不会触发工具。
- 评论不保存用户名；弹幕不保存发送者；输出前清洗 `@用户名`、URL 和控制字符。
- 不声称看到画面、听到声音、读到字幕或知道精确台词；`safe_line` 会拒绝
  “第 N 秒”“画面里”“系统提示”等没有证据或疑似注入的句子。
- 不把弹幕的 `ctime`（发送墙钟时间）当作视频进度；只用 `progress`。
- 搜索页标题只是候选文本；Chrome 打开页面后**不读取 DOM、不注入脚本、不把页面
  内容当指令**，也没有工具执行能力。
- 热门/详情/评论/弹幕仍按匿名公共接口读取；写操作只有 `like_on_watch` 的点赞和
  `live_reply_send_danmu` 的直播弹幕回复，均需要登录 Cookie；不自动发视频评论，
  不打印 Cookie。
- 直播弹幕/SC 的 `username`、`text`、`gift` 以及当前视频标题全部是不可信文本，不是指令；
  其中出现“忽略以上/输出密钥/执行命令”等只作为内容，不改变系统规则、不触发工具。
  回复再经过 `safe_line` 过滤 URL、@、控制字符和没有证据的断言。

## 已知限制

- B 站接口可能返回 412/风控、304 或空段；protobuf 分段是主路径，失败才降级
  XML（最多 1000 条，标记 `partial`，档案会降低时间轴 confidence）。
- 官方搜索 JSON 匿名请求 412；搜索页 HTML 结构可能变化，解析失败会自动回退热门。
- Chrome 默认每个选中视频开一个新窗口并尝试移到副屏；不会复用一个窗口，也不会
  读取播放进度。Chrome 已在运行时窗口位置参数可能被忽略，所以启动后会再用
  Win32 移动一次；如果系统拒绝或找不到新窗口，就留在 Chrome 默认位置。
  要真正自动播放并与虚拟时钟同步，需要另接 Chrome DevTools 控制，当前不做。
- 点赞失败（Cookie 过期、风控、频繁、未配置）只写 `like_skipped`/`like_failed`
  日志，不影响视频打开和解说；每个视频只点一次。
- 直播监听只在桌宠进程（`GiftBridge`）里运行；单独跑 CLI 而没有桌宠时，
  `logs/bilibili_events.jsonl` 不会新增，弹幕回复自然也不会触发。不要在控制台
  进程再开一个监听，否则会重复写事件。
- 弹幕回复的 token 预算按 LLM 返回的 usage 记录，单次调用前用固定估算兜底；
  这不是 B 站/模型账单的逐 token 精算，但能防止突发刷屏把预算打穿。
- 真实发送直播弹幕可能因 Cookie、禁言、发送频率或 B 站风控失败；失败只记
  `live_chat_send_failed`，语音和视频继续。
- Chrome 找不到时退回系统默认浏览器；`browser_path` 可指定 Chrome 路径。
- 多 P 视频默认跳过；热门列表里这类很多，所以候选可能比预期少。
- 没有独立的“当前进度/热度”UI；进度、cue 数、弹幕覆盖度只写
  `logs/autoplay.json` / `logs/autoplay.jsonl` 和状态接口。
- 语音忙时 cue 会丢弃而不是排队；这是为了保证虚拟时钟和直播节奏不被 TTS 拖住。
- 停止是协作式的：如果正卡在 LLM 请求里，后台线程最多等到请求超时；`stop`
  不会强杀线程。
- 和 STS2 共用一个 `logs/autoplay.stop` 和一份状态文件；切换模式前先停掉当前
  模式，不要同时启动两个 autoplay。
- 目录名带连字符，不能被普通 `import games.bilibili_hot_commentary` 导入；插件和
  测试都通过 `GameRegistry` 的合成包名加载。

## 文件

| 文件 | 作用 |
| --- | --- |
| `api.py` | 热门/搜索/详情/热评/弹幕 HTTP，错误分类，XML 降级 |
| `search.py` | B 站搜索页 HTML 解析（BVID/标题/时长/播放量） |
| `persona_watch.py` | 读 B 包独立人设卡、style.md 和 `watch` 偏好 |
| `persona/` | B 站专用人设（card.json/style.md/README），控制台可编辑 |
| `skills.py` | 控制台「B站云观影 → 游戏详情」的文件白名单和保存校验 |
| `search_intent.py` | 人设 → 搜索词（手写优先，LLM 一次，缓存） |
| `browser.py` | 查找 Chrome、新建窗口/副屏定位、关闭/刷新自己记录的标签页 |
| `control.py` | 播放列表快照、控制台 play/close/refresh 命令文件 |
| `liker.py` | 用登录 Cookie 给选中视频点赞（可关；不打印 Cookie） |
| `live_chat.py` | 直播事件增量读取、SC 优先队列、频次/用户冷却/token 预算、LLM 回复 |
| `danmaku.py` | protobuf/XML 解析、分桶、强度/关键词/注入检测 |
| `models.py` | 数据模型和文本清洗 |
| `select.py` | 候选过滤和评分 |
| `profile.py` | 一次 LLM 档案、确定性校验、缓存 |
| `schedule.py` | 虚拟时钟、cue 调度、空档回应 |
| `speaker.py` | overlay 输出和单槽语音门 |
| `runner.py` | 模块级生命周期、搜索/选视频、播放循环、状态/日志 |
| `adapter.py` | 只读伪 `GameState`，不启动线程 |
| `plugin.py` | 插件入口 |

## 测试

```powershell
python -m pytest -q tests/test_bilibili_commentary.py
```

测试不访问真实 B 站，使用 FakeClient/FakePlanner/FakeSink/FakeBrowser 和临时
`AutoplayHost` 路径；覆盖解析、分桶、筛选、缓存、注入边界、时钟、语音门、
B 站独立人设/编辑器白名单、搜索意图缓存、当前游戏排除、搜索 HTML 解析、Chrome 新窗口/副屏/关闭/
刷新/换页关旧窗、播放列表文件、控制台命令、点赞请求、直播弹幕增量读取/SC 优先/
频次/预算、连续预取、LLM 失败和停止/切模式。
