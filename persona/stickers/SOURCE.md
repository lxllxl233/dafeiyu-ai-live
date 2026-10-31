# 大肥鱼表情包来源

- 上游：**梗鲸 GengJing · DeepSeek 娘表情包合集**
  <https://github.com/the-beating-light-of-the-nail/deepseek-chan-meme-pack>
- 在线图库：<https://aigengtu.com/>（每张图有台词与梗科普）
- 本目录只取上游 `previews/` 里的 19 张精选图，转成 512×512 PNG 供聊天发送。
- 图片里的台词就是表情文案；`stickers.json` 的 `text` / `when` 是模型选图时看的
  文案和适用场景，`source_url` 指回原图详情页。

## 版权

角色形象「蓝色大肥鱼 / 鲸鱼娘 / DeepSeek 娘」与原始画作版权归原作者及社区
创作者所有；上游仓库与图库均为**非营利粉丝向整理**。这里按上游说明用于个人、
非商用聊天；商用请先联系原作者。

重新导入：

```powershell
python tools/import_stickers.py
# 或使用本地已下载的 previews：
python tools/import_stickers.py --src <下载目录>
```
