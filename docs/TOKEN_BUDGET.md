# Token 账本：钱到底花在哪（2026-10-26 实测）

> 结论先说：**skill 文本从来不是主因**（一次加载 ~3.6k token，占一局消耗的 0.002%）。
> 账单 = **请求数 × 每次请求的上下文**。实测平均每次请求 37.6 万 token，其中 99.6% 是
> 「把同一段越来越长的 transcript 重新读一遍」。所以省 token 的唯一有效动作是**减少请求数**。

## 1. 怎么算的（可复现）

DSH 的 session 日志里，每条 `assistant/message` 都带 `usage`（inputTokens / cacheReadTokens /
outputTokens），每条 `tool/result` 带完整回显文本。`tools/audit_tokens.py` 直接读这些日志：

```powershell
python tools/audit_tokens.py                  # 所有 sts2 会话汇总
python tools/audit_tokens.py --per-command    # 各命令被调多少次、回显多少字符
python tools/audit_tokens.py --top 10         # 单条最大回显（找整篇读文档的）
python tools/audit_tokens.py --session 9365   # 只看某一局
```

日志位置：`%DSH_HOME%\sessions\--*-sljt-*--\session-*`（zstd）。不联网、不改游戏。

## 2. 实测账本（14 个 sts2 会话，含研究/改代码的会话）

| 指标 | 数值 |
|---|---|
| 模型请求 | **8,334 次** |
| 计费合计 | **31.4 亿 token** |
| 平均每次请求 | **37.6 万 token**（输入 1,568 + 缓存重读 374,331 + 输出 502） |
| 单会话峰值上下文 | **72.7 万 token** |
| 缓存重读占比 | **99.6%** |

代表性一局（session-9365，793 次请求 / 2.68 亿 token）：

| 命令 | 调用次数 | 回显字符 | 说明 |
|---|---|---|---|
| `sts2.py do` | 334 | 308,057 | 出牌/end_turn/地图/事件/奖励，一个动作一次请求 |
| `sts2.py play` | 225 | 195,885 | 每刀一次请求 |
| `sts2.py state` | 62 | 43,992 | 每回合一次 |
| `pwsh` 等其它 | 84 | 29,913 | shell 包装 |

换算：**每次工具调用 = 一次模型请求 = 一遍 30 万+ token 的上下文重读**。
省下 100 次调用 ≈ 省 3,000 万 token；把 SKILL.md 砍掉一半（~1.8k token）≈ 每局省 0.5 万。

## 3. 所以改了什么

### 3.1 协议：一回合一条 `batch`（主项）

旧：`state` → `play`×N → `end_turn` = 每回合 5–7 次请求。
新：`batch` 一条打完整个回合，**末尾直接给下一回合快照** = 每回合 1 次请求。

同回合对比（`python tools/compare_turn_protocol.py`，3 刀 + end_turn）：

```
旧协议 请求 5 次  回显 2,279 字符
v18    请求 1 次  回显   610 字符
回显 3.7× 省，请求 5× 省
```

安全性没有交给「模型多读几次」，而是搬进 `sts2.py batch`：

| 旧协议靠什么 | v18 靠什么 |
|---|---|
| 每刀前重读 state 拿新下标 | 每步按 `card_id` 在当前手牌重解析 |
| 出牌失败就停手，别接 end_turn | 缺牌 `BATCH STOP`、超费 `PLAN_OVER_BUDGET`、目标失效自动重定向 |
| 手动等抽牌动画 | 撞过渡快照自动重读；`end_turn` 后等新回合手牌抽稳 |
| 战斗打完还要补 end_turn | 最后一刀打完自动跳过 |

### 3.2 上下文：换幕换会话（第二项）

即使一回合一条，长会话的上下文仍会涨到 60–70 万，之后每次请求都在为它付钱。
`memory --write` + **每幕开新会话** 把均值从 37 万压到每幕体量（≈ 1/3）。
skill §0.5 已写成硬规则。

### 3.3 读文件：grep 先行（第三项）

实测一轮里有 10 万+ 字符来自「整篇读 docs/*.md、LESSONS.md（97KB）、sts2.py」。
skill 与 `NEXT_PROMPT.txt` 现在要求：先 `grep` 拿行号，再 `read --offset/--limit` 取 20–40 行；
references 同样只取需要的段。

### 3.4 skill 文本：顺手瘦身（最小项）

`SKILL.md` 13,006 → 11,632 字节（~4.0k → ~3.6k token）。**规则一条没删**：
`tools/check_skill.py` 现在逐条断言 40+ 个阈值、60+ 个关键词、25 个反模式编号、
6 个协议标记还在，丢一个就 FAIL。

## 4. 预期与验收

按「请求数 5→1、每回合增量 2,279→610 字符」估算，下一局应当是：

- 请求数：~800 → **~250–300**（战斗回合按 1 次算，读档/选牌/事件仍各 1 次）
- 单局 token：**3–6× 下降**（上下文涨得慢，后半局尤其明显）

跑完一局后验收：

```powershell
python tools/audit_tokens.py --session <新会话 id 前缀>
```

看三项就够：`requests`、`total`、`mean_ctx`。三项同时降才算生效；
只有 `total` 降而 `requests` 没降，说明模型还在按老习惯每刀一条命令。

## 5. 回滚

```powershell
# 本机备份（backups/ 不入库）
Copy-Item backups\SKILL.v17.1.md .dsh\skills\sts2-ironclad-ironclad\SKILL.md -Force
# 或者直接退回上一版
git checkout <本次提交>^ -- .dsh/skills/sts2-ironclad-ironclad/SKILL.md
```

`sts2.py batch` 的改动是向后兼容的：不带 `end_turn` 的旧用法照常工作。
