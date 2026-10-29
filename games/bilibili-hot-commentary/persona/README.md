# B站刷片人设（隔离）

这里只属于 `bilibili-hot-commentary`，不读也不改 `persona/dafeiyu/`。B 模式加载的
是 `games/bilibili-hot-commentary/persona/card.json` + `style.md`：

- `card.json`：名字、tagline、性格、口癖、看片关键词/避雷和偏好；
- `style.md`：刷片时的说话方式与边界；
- 当前默认参考主播 **xQc** 的高能、嘴快、先喊再拆解，但不冒充本人，也不复制原话。

控制台「游戏 → B站云观影 → 游戏详情」里可以直接查看和编辑这两个文件。保存后
重启 B 站云观影（或重启桌宠）才会加载新人设；正在跑的引擎不会热替换系统提示。

修改 `watch` 会影响搜索关键词和选片，`traits/quips/style` 会影响视频档案、解说、
直播弹幕回复；不需要清全局人设缓存。
