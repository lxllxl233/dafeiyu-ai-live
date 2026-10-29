# TODO：修 mod 的「从牌组移除 N 张」多选点击（已修复，2026-10-29）

**状态：已修复**。mod 已用 .NET 9 SDK 从 `lxllxl233/STS2-Agent` v0.8.0 源码重编
（分支 `compat/v0.107.1`，本仓库 `games/slay-the-spire-2/compat/mod/STS2AIAgent.dll`
就是这份构建），客户端在 `runner._card_selection` 里改为逐张点不同牌 + `confirm_selection`。

补丁（都在 mod 源码里）：
1. `Player.CanUseOrRemovePotions` 在游戏 v0.107.1 已删除 → `IsPotionDiscardable` 返回 `true`
   （以前靠 IL 补丁，现在源码层）。
2. `NDeckCardSelectScreen` 的确认按钮是私有字段 `_confirmButton` / `_previewConfirmButton`，
   不是 `%Confirm` 节点 → `TryGetDeckScreenConfirmButton` 反射取字段（预览打开时优先预览确认），
   `CanConfirmSelection` / `ExecuteConfirmSelectionAsync` 接上牌组屏，暴露 `confirm_selection`。
3. `BuildSelectionPayload`：`min/max` 从提示文本解析（先去掉 `[blue]` 这类 BBCode），
   已选张数读 `_previewCards`；不再一律 0/0/0。
4. 点确认前用游戏自己的 `CheckIfSelectionComplete` 把关：实测选 1/2 时就点确认，
   游戏会**只移除 1 张并直接结束事件**（`松动羊毛剪` 变相废掉）。
5. `select_deck_card` 只负责点牌、不再自动确认；`confirm_selection` 单独驱动。

实测（2026-10-29，游戏 v0.107.1 / mod 0.8.0 重编）：
- 涅奥「精准剪刀」（移除 1 张）：`min=max=1`，点 1 张后 `confirm_selection` 可用，
  确认后回到事件，牌组减少。
- 涅奥「松动羊毛剪」（移除 2 张）：选 1 张后确认**不可用**（门控生效），选满 2 张后才可用。

客户端侧：
- `runner._card_selection` 用 `_deck_clicks` 本地计数，按 0、1、2… 点不同牌，
  `selected_count` 只在更大时同步；点满 `need` 后调 `confirm_selection`，
  不推进再交给卡住守卫回滚。
- `--mode llm` 下牌组多选屏（有 `select_deck_card`）直接本地处理，不再问模型
  （模型看不见已选张数，只会重复点同一张；run-0113 一晚烧 530 万 token）。
- 删掉 `ui_pick_cards` 鼠标坐标兜底（12+ 张牌组的网格坐标对不上）。

## 历史记录（修复前）

**现象**（2026-10-27 06:35，run75 涅奥选项 `松动羊毛剪`）
- 选完后进 `CARD_SELECTION`，提示「选择 2 张牌来移除」。
- `selection.max_select = 0`、`min_select = 0`、`selected_count = 0`、`can_confirm = false`。
- `select_deck_card --option 0` → 只打印 `pending`，`selected_count` 不变。
- `select_deck_card --options 0,1` → 两张都 `pending`，界面依旧 0/0。
- `confirm_selection` → `invalid_action`（当前状态不可用）。
- 结果：卡死在这一屏，只能 `save_and_quit`。

**影响**
- 涅奥的「精准剪刀 / 松动羊毛剪」（移除 2 张）用不了 → 删牌这个最强开局直接损失。
- 其它走「从牌组选 N 张」的事件/遗物（如需要挑选牌组的遗物）可能同样中招。

**猜测根因（已用游戏自带 `sts2.xml` 文档核对）**
- `CardSelectCmd.FromDeckForRemoval` 打开的是 `NDeckCardSelectScreen`（继承/等价于
  `NCardGridSelectionScreen`，mod 只按后者处理）。
- 这类屏的确认按钮是 `NDeckCardSelectScreen._previewConfirmButton`，
  确认方法是 `NDeckCardSelectScreen.ConfirmSelection`；mod 的
  `ConfirmDeckSelectionAsync` 只找 `%PreviewContainer/%PreviewConfirm/%Confirm`，
  对不上 → 永远不点确认。
- 状态里的 `selection.min_select/max_select/selected_count/can_confirm` 在
  `BuildSelectionPayload` 里**只从战斗手牌选择（NPlayerHand）读取**，牌组屏一律是
  0/0/0/false → 客户端误以为一张都没选、于是反复点同一张（还被取消）。
- 正确修法（3 处，都在 mod 里）：
  1. `BuildSelectionPayload`：牌组屏从 `CardSelectorPrefs`/屏自己的 `_selectedCards`
     读 min/max/selected，并给每张卡带 `selected` 标记；
  2. `CanConfirmSelection` + `ExecuteConfirmSelectionAsync`：牌组屏改用
     `_previewConfirmButton`/`ConfirmSelection`；
  3. `select_deck_card`：点已选中的卡改为不重复点（或明确 deselect）。
- 现状：mod 是第三方 DLL + 反编译快照，仓库里没有 `Server/` 源码和工程文件，
  **没法直接重编**；要么拿完整工程重编，要么用 Cecil 打 IL 补丁（本机有 VS Build
  Tools 的 csc/MSBuild）。

**注意**：`ui_pick_cards`（真实鼠标点 5+2 布局）在牌组满 12+ 张时不适用——
`NDeckCardSelectScreen` 是 5 列多页网格，坐标对不上，所以那条兜底也点不动。

**临时绕过（已写进 LESSONS.md / SKILL.md）**
mod 里 `select_deck_card` 的点击路径大概只接了「手牌/奖励三选一」的按钮，
没有接 deck view（牌组浏览界面）里的卡片对象，所以点击落到无效区域，`selected_count` 不更新。
而且这个 selection 类型的 `min/max` 没从 UI 读出来（都是 0），说明这个 screen 根本没被识别。

**修法方向**
1. 在 mod 里识别 `CardSelectScreen`（或等价的 deck-remove 界面），读出 `min/max`；
2. 点击卡片对象时，模拟 UI 的事件（`CardReward` 那种 `OnCardClicked` / `_onCardClicked`），而不是按坐标；
3. 支持 `selected` 列表参数一次提交（`{"cards":[0,3]}`），别依赖多次点击累积；
4. 加自测：用「松动羊毛剪」跑一遍，断言 `deck` 张数 −2。

**临时绕过（已写进 LESSONS.md / SKILL.md）**
- 遇到会打开「从牌组移除 N 张」的选项，**不要选**（`NONCOMBAT_PROMPT` 已写明）；
- 已经卡住：`python tools/sts2.py do save_and_quit` → `python tools/sts2.py do continue_run`，会回滚到该选项之前。

**已自动化的部分（2026-10-29，run-0113 复盘）**
- 识别「min/max 都是 0 的多选」：不再问 LLM（模型点是空操作，一晚烧了 530 万 token），
  直接走本地；本地点一次没进展就由卡住守卫判定，**自动 `save_and_quit` 一次**
  （`runner._recover_blind_screen`），主菜单再 `continue_run` 回滚。
- `CARD ...` 返回串重复（同一张牌反复点）现在算卡住，不会再转 1000+ 步。
