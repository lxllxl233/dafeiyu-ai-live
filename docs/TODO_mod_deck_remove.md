# TODO：修 mod 的「从牌组移除 N 张」多选点击（blocker）

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

**猜测根因**
mod 里 `select_deck_card` 的点击路径大概只接了「手牌/奖励三选一」的按钮，
没有接 deck view（牌组浏览界面）里的卡片对象，所以点击落到无效区域，`selected_count` 不更新。
而且这个 selection 类型的 `min/max` 没从 UI 读出来（都是 0），说明这个 screen 根本没被识别。

**修法方向**
1. 在 mod 里识别 `CardSelectScreen`（或等价的 deck-remove 界面），读出 `min/max`；
2. 点击卡片对象时，模拟 UI 的事件（`CardReward` 那种 `OnCardClicked` / `_onCardClicked`），而不是按坐标；
3. 支持 `selected` 列表参数一次提交（`{"cards":[0,3]}`），别依赖多次点击累积；
4. 加自测：用「松动羊毛剪」跑一遍，断言 `deck` 张数 −2。

**临时绕过（已写进 LESSONS.md / SKILL.md）**
- 遇到会打开「从牌组移除 N 张」的选项，**不要选**；
- 已经卡住：`python sts2.py do save_and_quit` → `python sts2.py do continue_run`，会回滚到该选项之前。
