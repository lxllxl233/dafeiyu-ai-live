# 桌宠精灵来源

- 仓库：https://github.com/1190fasheqi/dafeiyu-pet
- 目录：`sprites/`（正面 / 侧面 / 背面，原图 + 187 / 238 / 306 三档高度）
- 协议：MIT（见同目录 `LICENSE`，版权归上游作者）
- 用途：本项目的桌面透明弹幕窗（`ui/desktop/streamer.py`）用它画「大肥鱼」，
  并按拖动方向切换正面 / 侧面 / 背面；OBS 弹幕页用 `ui/overlay/assets/pet_front.png`。

精灵没有逐帧动画，动态效果由代码实时生成：呼吸缩放、摇摆、拖拽倾斜 / 拉伸、
速度线、松手回弹、气泡弹出。没有改动原图。