"""大肥鱼直播姬 · Dafeiyu AI Live。

一个面向「AI 主播」的直播工具：把游戏信号、性格层、语音层和 UI 拆成可替换的层，
游戏只是其中一层。当前内置《杀戮尖塔 2》游戏包，换成别的游戏只要加一个适配器。

分层（对应仓库目录）：

    games/            游戏列表（每个游戏 = 兼容层 + 语料库）
    persona/          性格层（人设卡 + 台词规则）
    voice/            语音层（动物语 / 曼波语）
    src/dafeiyu/      源码（事件总线、会话编排、CLI、UI 服务）
    ui/               UI 层（OBS 浏览器源、桌面透明弹幕窗、直播姬控制台）
"""

from __future__ import annotations

__version__ = "2.0.0"
__product__ = "大肥鱼直播姬"
__codename__ = "dafeiyu-ai-live"

__all__ = ["__version__", "__product__", "__codename__"]
