"""大肥鱼直播姬 · Dafeiyu AI Live。

把「游戏信号 / 性格层 / 语音层 / UI」拆成可替换的层，游戏只是输入源。
具体游戏放在 `games/<id>/`，核心只依赖 `game.json` 与插件协议。

分层（对应仓库目录）：

    games/            游戏列表（game-skill + compat + corpus）
    persona/          性格层（人设卡 + 通用 meme + 台词规则）
    voice/            语音层（动物语 / 曼波语）
    src/dafeiyu/      源码（事件总线、会话编排、CLI、UI 服务、录制）
    ui/               UI 资产（OBS 浏览器源、直播姬控制台）
"""

from __future__ import annotations

__version__ = "2.2.0"
__product__ = "大肥鱼直播姬"
__codename__ = "dafeiyu-ai-live"

__all__ = ["__version__", "__product__", "__codename__"]
