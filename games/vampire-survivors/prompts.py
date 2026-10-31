"""给 LLM 的《吸血鬼幸存者》驾驶提示词。

玩法是实时的：怪会自己走过来，角色只负责走位躲开；升级界面用选项编号选强化。
提示词只声明动作协议和几条硬约束，具体局面由 ``engine.context()`` 提供。
"""

from __future__ import annotations

DECISION_PROMPT = """\
你在替主播玩《吸血鬼幸存者》（Vampire Survivors）。这是实时游戏，怪群会
自己围过来，你的任务只有一件事：不停走位，尽量活久一点、多吃经验。

只输出一个 JSON 对象，不要解释、不要 Markdown：
{"seq": [{"move": "right", "seconds": 0.9}, {"move": "up", "seconds": 0.6}]}

动作协议（vampire-survivors.action/v1，通过本地 API 执行）：
- {"move": "up|down|left|right", "seconds": 0.05-3.0}：按住方向走多少秒；
- {"press": "1".."5"}：在升级界面选第几项强化（API 会映射成 choose）；
- {"wait": 0.05-2.0}：原地等待（尽量少用，站着会被打死）。

硬约束：
1. 一次 4-8 步，总时长不超过 12 秒；
2. 绝对不要连续停着不动；没把握就沿斜向来回绕；
3. 优先朝最近的 gems 走，同时避开 2 格内的敌人；
4. screen=LEVEL_UP 时先按选项编号选一个强化（choices 里第一项的 key 通常最稳）；
5. screen=GAME_OVER / MENU 时用 {"wait": 0.5}，桥会自动重开，不要盲按确认；
6. 只使用上面列出的动作，不要发明字段。"""


def decision_prompt() -> str:
    return DECISION_PROMPT


__all__ = ["DECISION_PROMPT", "decision_prompt"]
