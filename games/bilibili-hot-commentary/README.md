# B站热门视频云观影

这是 `bilibili-hot-commentary` 模式的说明。它不下载视频、不读取画面、不做视觉
识别或语音识别：只读取 B 站视频的**公开文本**（标题、简介、UP 主、热评、弹幕
时间轴），用虚拟播放时钟模拟“正在看”，按时间轴说预先规划好的解说。

选中视频后会按配置用 **Google Chrome 的独立 app 窗口**打开该视频页：使用独立
`--user-data-dir` profile，只有一个页面、没有标签栏，不会碰你正常 Chrome 里的标签页；
窗口会移到副屏并铺满工作区。同一个 app 窗口开着时，下一条视频用 Chrome DevTools
在原页面内导航，不关窗、不新开、不抢前台；只有窗口被关掉或导航失败才重开一个。
独立 profile 没有你主 Chrome 的登录态，第一次可能要在
这个 app 窗口里登录一次 B 站，之后登录会保留。Chrome 的实际播放/暂停由浏览器和用户
控制，解说时钟不会假装与画面逐秒同步。
默认每条视频准备时都会先按「大肥鱼 / 大肥鱼 直播 / 大肥鱼 切片」搜一遍，
优先播播放量 ≥10 万、且没看过的大肥鱼；没有过关的再回到搞笑/高能人设候选。
播放量在评分里占 0.20，大肥鱼候选过关后还会被置顶到本轮第一条。刷到标题、UP 主
或简介带“大肥鱼”的同类视频时，档案会带 `same_fish=true`，她会会心一笑、共情视频里的
鱼/UP 主，但只聊视频里具体的事，最多一两句，不冒充视频里那条鱼，也不背“同类看同类”
“我也是条鱼”这类固定台词。模型如果确实有具体想法，
还可能用登录 Cookie 在视频评论区留一条；不是每条都评，默认不限制每日条数、两条之间
至少 10 分钟，没有想法就留空。评论要像真人留评（一个具体细节 + 一句自己的反应），
只喊口癖、或抄人设台词的会被丢掉不发送。每日上限和间隔可以在控制台「B站播放列表 →
视频评论」里改。开启 `like_on_watch` 时会用已配置的登录 Cookie 给视频点一个赞；
没有 Cookie 就跳过。
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
跟随全局 `config.json` 的 `persona`。选中视频后用独立 Chrome app 窗口打开
`https://www.bilibili.com/video/<bvid>?t=0`，强制从 0:00 开始，避免 B 站历史记录
把上次看到的进度带进来、导致虚拟时钟和实际剩余时长对不上；
搜索/选片仍会走时长、多 P、评论数、已看冷却和安全过滤。视频一进入播放就记为
已看，所以中途停掉/重启后不会又把同一条选回来；跳过仍额外进失败冷却。

暂停 / 恢复 / 跳过当前视频：

```powershell
python games/bilibili-hot-commentary/control.py pause
python games/bilibili-hot-commentary/control.py resume
python games/bilibili-hot-commentary/control.py skip
python games/bilibili-hot-commentary/control.py status
```

暂停只冻结虚拟时钟；已经发出的语音继续播完。跳过后给该视频一个冷却，再选下一个。

## 直播姬 / OBS 场景

场景名：**Bili Watch**（B站云观影）。场景页是独立的透明浏览器源：

```text
http://127.0.0.1:8730/bili
```

尺寸按 `1920x1080` 设计，页面会自动缩放；左侧和中间保持透明，给 Chrome 视频画面，
右侧是「正在看 / 直播弹幕 / 接下来」，左下是大肥鱼和当前解说。场景页自己会拉
`/state` 和 `/api/bilibili/scene`，不会额外调用 LLM。

手动在直播姬里新建场景「B站云观影」，按顺序放：

1. **视频层**：显示器捕获（Chrome 所在的副屏）或 Chrome 窗口捕获，铺满画布；
2. **场景页**：浏览器源 `http://127.0.0.1:8730/bili`，`1920x1080`，透明背景，
   **打开音频**（TTS 由这个页面播）；页面内部会给 TTS 提 6 dB、限幅后再补 3 dB，
   OBS 源音量保持 100% 即可，不要再额外加增益滤镜，否则容易削爆。只有 OBS 浏览器源里的
   页面会出声；在普通浏览器或直播姬内置浏览器里打开 `/bili` 只显示字幕，不会
   抢占 live 音频、也不会多播一路；
3. **音频**：Chrome / 桌面音频进直播；不要再同时打开 `/` 那个桌宠气泡源，
   否则同一句 TTS 会响两次。`Bili Scene` 会把自己登记成 live 音频主，桌宠窗口
   检测到后自动让位；如果仍然有重音，先确认没有第二个 live 页面（例如另一个
   `/` 浏览器源）在同时播 TTS。

如果不用 OBS、直接用直播姬开播：启动桌宠后右键「叠加层」，选状态条/电影框/
弹幕栏/极简字幕，再把桌宠拖到直播的那块屏；直播姬用「显示器捕获」抓这块屏，
叠加层会随游戏/视频一起进画面。不要只用游戏捕获或单窗口捕获，否则桌面叠加层
不在那个窗口里。OBS 和桌面叠加层二选一，不要同时开两个。

OBS 源列表里**越靠上越在上层**：`Bili Scene` 必须在 `Bili Monitor` 上面，否则
显示器采集会把侧栏和大肥鱼整层盖住，只剩视频。用 `python main.py obs setup` 建的
场景会自动把 `Bili Scene` 放到最顶层；手动场景如果看不到叠加层，把 `Bili Scene`
拖到源列表最上面即可。

如果使用 OBS WebSocket，把 `config.game` 切到本模式后运行：

```powershell
python main.py obs setup
```

本包的 `game-skill/obs.json` 会让它建 `Bili Watch` 场景、副屏采集、`Bili Scene`
浏览器源和桌面音频引用。默认抓第 2 块屏（`monitor_index: 1`）；只有一块屏时改成
`0`，或者把 Chrome 窗口放到被捕获的屏上。音频优先复用 OBS 已有的桌面音频，不会
再建第二个桌面源；切下一个视频在 app 页面内导航，桌面音频源不断。不想收其他软件的声音就
把别的音频源静音。正在直播时先建场景但不想切画面，用
`python main.py obs setup noswitch`，之后在直播姬/OBS 里手动切到 `Bili Watch`。

录像默认**关闭 + 不分集**（`game.json` 的 `recording` 块）。想看录像时在控制台
「当前游戏 → OBS 录像」打开自动录像，或直接点「开始录像」；开启后会先检查/创建
`game-skill/obs.json`，连续录到 `recordings/bilibili-hot-commentary/continuous/`。

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
    "search_prefetch": 16,
    "high_view_keywords": ["大肥鱼", "大肥鱼 直播", "大肥鱼 切片"],
    "high_view_min_view": 100000,
    "high_view_every": 1,
    "open_browser": true,
    "browser_window": true,
    "browser_isolated": true,
    "browser_profile_dir": "",
    "browser_fullscreen": true,
    "browser_monitor": "secondary",
    "like_on_watch": true,
    "video_comment_enabled": true,
    "video_comment_max_chars": 60,
    "video_comment_max_per_day": 0,
    "video_comment_min_gap_s": 600,
    "live_chat_enabled": true,
    "live_reply_min_interval_s": 20,
    "live_reply_sc_min_interval_s": 5,
    "live_reply_max_per_min": 3,
    "live_reply_user_cooldown_s": 60,
    "live_reply_max_queue": 6,
    "live_reply_max_tokens_per_hour": 5000,
    "live_reply_max_tokens_per_day": 20000,
    "live_reply_send_danmu": false,
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
| `seen_ttl_days` | `7` | 已看去重天数；进入播放就记已看，设为 `0` 关闭去重 |
| `max_cues` / `max_replies` | `8` / `4` | 单视频最多 cue / 空档弹幕回应 |
| `dynamic_reactions` | `false` | 预留；MVP 不做逐桶 LLM，弹幕回应是预生成 |
| `prewarm_tts` | `true` | `/comment` 之后让 `/tts` 预热 |
| `search_enabled` | `true` | 先按人设搜视频；关掉则只刷热门列表 |
| `search_keywords` | `[]` | 手写搜索词；非空时覆盖人设 LLM，不再调搜索意图 |
| `search_exclude` | `["杀戮尖塔", "尖塔", ...]` | 当前游戏/不想看的词；自动搜索词和候选标题命中即排除。手写 `search_keywords` 时视为用户覆盖 |
| `search_prefetch` / `search_limit` | `16` / `40` | 补详情的候选上限 / 每个关键词从搜索首屏取回的上限 |
| `high_view_keywords` | `["大肥鱼", "大肥鱼 直播", "大肥鱼 切片"]` | 高播放量专线搜索词；空则关闭专线 |
| `high_view_min_view` | `100000` | 高播放量专线的最低播放量 |
| `high_view_every` | `1` | 每 N 条视频安排一条高播放量；`1` 为每条都先试，`0` 关闭轮换 |
| `open_browser` | `true` | 选中视频后用 Chrome 打开视频页 |
| `browser_path` | `""` | Chrome 路径覆盖；空则自动查找，找不到退回默认浏览器 |
| `browser_window` | `true` | 是否要 Chrome 窗口；独立 app 模式下仍保持 `true` |
| `browser_isolated` | `true` | 用独立 Chrome profile + app 窗口：一个页面、没有标签栏，不碰用户正常浏览器；设为 `false` 才退回复用当前标签页 |
| `browser_profile_dir` | `""` | 独立 profile 目录；空则用 `logs/bilibili_commentary.chrome-profile`。第一次可能要在这个窗口登录 B 站 |
| `browser_fullscreen` | `true` | app 窗口铺满副屏工作区（无边框），不抢前台 |
| `browser_monitor` | `"secondary"` | 目标屏：`secondary` 副屏、`primary` 主屏、`0/1` 索引 |
| `like_on_watch` | `true` | 选中视频后点一次赞；需要登录 Cookie，失败不影响播放 |
| `video_comment_enabled` | `true` | 同类视频且模型主动写了评论时，是否允许发到视频评论区；需要登录 Cookie |
| `video_comment_max_chars` | `60` | 视频评论字数上限 |
| `video_comment_max_per_day` | `0` | 每天最多发几条视频评论；`0` = 不限，控制台可改（重启 B 模式生效） |
| `video_comment_min_gap_s` | `600` | 两条视频评论的最小间隔（秒），控制台可改（重启 B 模式生效） |
| `live_chat_enabled` | `true` | 读取直播间事件并回复；需要桌宠监听进程在跑 |
| `live_reply_min_interval_s` | `20` | 普通弹幕两次回复的最小间隔 |
| `live_reply_sc_min_interval_s` | `5` | SC 的最小间隔（SC 永远优先于普通弹幕） |
| `live_reply_max_per_min` | `3` | 每分钟最多几次 LLM 回复 |
| `live_reply_user_cooldown_s` | `60` | 同一 uid 的冷却，防单用户刷屏 |
| `live_reply_max_queue` | `6` | 普通弹幕队列上限；满了丢最旧 |
| `live_reply_max_input_chars` / `live_reply_max_chars` | `180` / `60` | 输入截断 / 回复字数上限 |
| `live_reply_danmu_chars` | `20` | 真正发到直播间的弹幕长度 |
| `live_reply_max_tokens_per_hour` / `..._per_day` | `5000` / `20000` | LLM token 预算硬限制 |
| `live_reply_send_danmu` | `false` | 是否把回复发到自己的直播间；默认只走 overlay/语音，不往 B 站弹幕发 |
| `live_reply_speak` | `true` | 是否同时走 overlay/语音 |
| `exclude_keywords` | 见 `game.json` | 标题/简介命中即跳过 |

时长决定弹幕桶：≤ 5 分钟用 30 秒桶，≤ 20 分钟用 60 秒桶（16 分钟正好 16 桶），
更长的视频用 120 秒桶，最多 20 桶。30 分钟上限下约 15 桶，避免 LLM 输入膨胀。

## 控制台播放列表

控制台「游戏」页在 `config.game = bilibili-hot-commentary` 时，会在游戏列表下面显示
「B站播放列表」：

- 列表来自引擎最近一次候选（搜索/热门），第一行是当前正在播放的视频；
- 点「播放」：指定另一条视频时，app 窗口已开着就原地导航到新视频；窗口不在才
  重开，所以始终只有一个页面；点的就是当前视频时，直接刷新这个 app 页面；
- 「刷新当前页面」「关闭当前页面」只操作引擎记录的那个 app 窗口；
- 列表状态和浏览器状态每 3 秒随 `/api/studio` 刷新；B 模式没启动时只显示最后一版，
  并提示先启动自动玩；
- 通信是本地文件：引擎写 `logs/bilibili_commentary.playlist.json`，控制台写
  `logs/bilibili_commentary.command.json`，不经过视频/弹幕文本，也不碰 Cookie。

「视频评论」卡片下面还有一块「已发评论」：只列**真的发到 B 站视频评论区**的记录，
每行是视频标题（可点开原视频）、视频链接/bvid、UP 主、rpid 和评论正文，按时间倒序。
数据来自引擎追加写的 `logs/bilibili_commentary.comments.jsonl`（最近 200 条），
控制台只读，不重发、不编辑。

浏览器控制是尽力而为：app 窗口刚启动、还没被 Win32 找到时，打开/关闭/刷新可能
短暂返回「没有页面」，过一秒重试即可；系统默认浏览器（找不到 Chrome 时）只能
打开，不能关闭/刷新。B 模式停止时会关闭自己记录的 app 窗口。

## 工作流与成本

1. 读 B 包 `persona/card.json` / `style.md` 和可选 `watch` 偏好（与全局人设隔离）；手写
   `search_keywords` 优先，否则用一次轻量 LLM 生成搜索意图，并按人设哈希缓存。
2. 用 B 站搜索页 HTML 取 BVID/标题/时长/播放量；每个关键词默认取搜索首屏 40 条，
   避免只看前几条。官方搜索 JSON 匿名会 412，所以 HTML 是主路径；搜索失败/为空时
   回退热门列表（默认拉 3 页、最多 60 条，180 秒缓存）。
3. 每条视频准备时按 `high_view_keywords`（默认「大肥鱼 / 大肥鱼 直播 / 大肥鱼 切片」）
   搜一遍，只保留播放量 ≥ `high_view_min_view`、时长合格、没看过/没在失败冷却的
   结果；补详情后只要过了评论/互动门槛，就在准备阶段置顶到本轮第一条。专线没有
   可用结果时才回到普通候选。评分里播放量占 0.20，专线置顶不依赖评分。
4. 对搜索候选补详情，再过滤时长、多 P、评论数、互动率、已看/失败冷却。控制台
   手动点播放时跳过评论数门槛；评论/弹幕接口失败或模型档案失败，会先用兜底
   开场/收尾档案把视频放起来，避免“点了播放但没反应”；同时保留旁边的大肥鱼候选列表。
5. 对最终候选拉热评、弹幕分段；弹幕按播放进度分桶。
6. **每个视频只调用一次 LLM** 生成 `VideoProfile`：主题、观众反应、分段摘要、
   4–8 条 cue（16 分钟视频优先 6–8 条）、最多 4 条空档弹幕回应，以及可选的
   视频评论 `video_comment`；之后整个播放期间不再调用 LLM。
7. 播放前把 cue 排到虚拟时间轴上（最小间隔、最多条数）；**开场必有一句问候**，
   收尾也必有一句「看完了」，如果原档案缺问候会自动补，收尾会等当前语音说完再发。
   同时用独立 Chrome profile 的 `--app` 窗口打开选中视频页，URL 带 `?t=0`；
   窗口没有标签栏，启动后移到副屏并铺满工作区。同一个 app 窗口开着时，下一条用
   Chrome DevTools 在原页面内导航，不关窗/不新开/不抢前台；窗口被关掉或导航失败
   才关旧重开。所以始终只有一个页面；独立 profile 不碰你正常 Chrome 的标签页。
   如果 `like_on_watch` 开着，后台点一次赞。如果 `video_comment_enabled` 开着、
   视频是同类、模型也确实写了 `video_comment`，后台再尝试发一条视频评论；评论必须
   像真人留评（具体细节 + 自己的反应），只喊口癖或抄人设台词（“同类看同类”等）的
   直接丢弃；没有登录 Cookie、没想法、超过每日上限或间隔太近都只记日志，不挡播放。**真发出去的评论会
   记一笔账**：视频链接、标题和评论内容追加到 `logs/bilibili_commentary.comments.jsonl`
   （最多留最近 200 条），控制台「B站播放列表 → 视频评论 → 已发评论」直接列出来，
   点标题可在浏览器打开原视频。没发成功（Cookie/风控/限流）的不记，避免把草稿当已发。
   播放中会看 Chrome
   窗口是否被关掉
   （窗口关闭立即、页面关掉后最多约 8 秒），并每 10 秒读一次自己账号的历史进度
   数字，发现用户把进度拖到结尾就提前进入下一条；如果进度连续 30 秒不动，就认为
   视频没有在播放，直接关掉 app 窗口、标记失败并强制下一条，不把静帧留在直播画面里。
   没有 Cookie/历史接口失败时仍由虚拟时钟自然结束。Chrome 启动带禁用后台节流、
   遮挡暂停和恢复上次标签页的开关；每次换视频会清掉本模式以前启动的 Chrome 旧窗口。
   B 模式运行时桌宠/控制台空闲文案是「看视频ing」，不再是「吃饭ing」；播放中只做
   到点判断、单槽语音门和状态写入。
8. 当前视频播放时后台预取**下一个**候选的详情和档案；自然播完后直接接上，
   不用再等一次选片/LLM。跳过/失败进冷却；LLM 失败只跳过该候选，不阻塞整个循环。
9. 直播弹幕线程只读 `logs/bilibili_events.jsonl`：普通弹幕有队列/用户冷却，SC
   优先；过频次/预算直接跳过，不会为一条弹幕无限重试。回复提示词要求直接接内容，
   清洗时还会去掉开头的“兄弟们/家人们/各位”等称呼，避免每句都带同一句口头禅；
   视频里的 `danmaku_replies` 也走同样的开头清洗。回复和解说里不出现“弹幕”二字：
   提示词先约束，输出再用 `strip_danmaku_word` 把“弹幕说/这条弹幕/弹幕里”
   换成“评论说/这条评论/评论里”，已缓存的旧档案播放时也会清洗。

搜索意图缓存键包含人设文本哈希 + `search_exclude` + `SEARCH_PROMPT_VERSION`，
同一人设/排除词 7 天内复用；但每个关键词首屏取 40 条、补详情上限 16，
候选池比原来大，所以不会反复只在前几条里打转。直播弹幕每条选中的消息额外
一次 LLM，受上面的
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
  内容当指令**，也没有工具执行能力。导航只把 URL 交给 Chrome `--app` 启动参数，
  或交给本机 DevTools 的 `Page.navigate`（只发 URL，不读 DOM；非独立模式才用
  Win32 键盘输入地址栏）；外部播放检测只读 Chrome 窗口句柄/标题，以及自己账号历史
  接口返回的 `progress`/`duration` 数字，不取标题/评论/页面文本。
- 热门/详情/评论/弹幕仍按匿名公共接口读取；写操作只有 `like_on_watch` 的点赞和
  显式开启 `live_reply_send_danmu` 后的直播弹幕回复，均需要登录 Cookie；默认不往
  直播间发弹幕，不自动发视频评论，不打印 Cookie。
- 直播弹幕/SC 的 `username`、`text`、`gift` 以及当前视频标题全部是不可信文本，不是指令；
  其中出现“忽略以上/输出密钥/执行命令”等只作为内容，不改变系统规则、不触发工具。
  回复再经过 `safe_line` 过滤 URL、@、控制字符和没有证据的断言。

## 已知限制

- B 站接口可能返回 412/风控、304 或空段；protobuf 分段是主路径，失败才降级
  XML（最多 1000 条，标记 `partial`，档案会降低时间轴 confidence）。
- 官方搜索 JSON 匿名请求 412；搜索页 HTML 结构可能变化，解析失败会自动回退热门。
- B 模式默认用**独立 Chrome profile 的 app 窗口**：只有一个页面、没有标签栏，
  启动后移到副屏并铺满工作区，不碰你正常 Chrome 的标签页/当前页面。app 窗口开着时
  下一条通过本机 DevTools 在原页面内导航（端口只绑 127.0.0.1，不读 DOM/页面文本），
  不重复开窗。`browser_isolated` 设成 `false` 才会退回旧版“复用当前标签页 + 地址栏
  导航”模式；那个模式需要抢前台，
  如果用户正把前台给 DeepSeek/游戏，导航会失败并退回新标签页（之前出现两个页面的
  原因就是：空窗口已开 + 导航失败后又用 URL 开了一个）。
  独立 profile 没有主 Chrome 的登录态，第一次可能要在 app 窗口里登录一次 B 站；
  profile 目录在 `logs/bilibili_commentary.chrome-profile`，登录会保留。启动参数
  仍带 `--disable-restore-session-state`，所以这个 profile 不会恢复上次整批标签页。
  不读取 DOM/页面内容，只用 URL 的 `?t=0` 强制从 0:00 开始，避免历史续播。拖到
  结尾/关窗口的检测依赖历史进度和 Chrome 窗口标题；如果 B 站页面/播放器或历史接口
  忽略这些信号，仍可能按虚拟时钟走完；真正的逐秒进度同步需要另接 Chrome DevTools，
  当前不做。历史接口看的是“进度 30 秒不动”，所以暂停、缓冲或画面冻住都会直接跳过
  这条；想暂停自动流程请用控制台的 pause 文件（它冻结虚拟时钟，不会暂停 Chrome 里的
  视频）。
  窗口位置参数可能被 Chrome 忽略，所以启动后会再用 Win32 移动并全屏；如果系统拒绝
  或找不到窗口，就留在 Chrome 默认位置。开始独立模式时会按启动开关识别本模式以前
  启动的 Chrome 进程并清掉它的旧窗口（包括导航失败留下的空窗口）；普通用户 Chrome
  是另一个进程，不会被碰。
- 点赞失败（Cookie 过期、风控、频繁、未配置）只写 `like_skipped`/`like_failed`
  日志，不影响视频打开和解说；每个视频只点一次。
- 直播监听只在桌宠进程（`GiftBridge`）里运行；单独跑 CLI 而没有桌宠时，
  `logs/bilibili_events.jsonl` 不会新增，弹幕回复自然也不会触发。不要在控制台
  进程再开一个监听，否则会重复写事件。
- 弹幕回复的 token 预算按 LLM 返回的 usage 记录，单次调用前用固定估算兜底；
  这不是 B 站/模型账单的逐 token 精算，但能防止突发刷屏把预算打穿。
- `live_reply_send_danmu` 默认关闭：回复只走 overlay/语音，不往 B 站直播间发文字。
  显式开启后，真实发送可能因 Cookie、禁言、发送频率或 B 站风控失败；失败只记
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
| `browser.py` | 独立 profile app 窗口、副屏全屏、旧窗口清理、关闭/刷新自己记录的 app 页面 |
| `control.py` | 播放列表快照、控制台 play/close/refresh 命令文件、已发评论账本（链接/标题/内容） |
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
B 站独立人设/编辑器白名单、搜索意图缓存、当前游戏排除、搜索 HTML 解析、Chrome
独立 app 窗口/副屏全屏/旧窗口清理/关闭/刷新、播放列表文件、控制台命令、点赞请求、直播弹幕增量读取/SC 优先/
频次/预算、连续预取、LLM 失败和停止/切模式。
