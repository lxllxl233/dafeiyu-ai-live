#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从「梗鲸」的 DeepSeek 娘表情包仓库导入聊天贴纸。

素材来源（粉丝二创，非商用；角色与画作版权归原作者）：
https://github.com/the-beating-light-of-the-nail/deepseek-chan-meme-pack

用法：
    python tools/import_stickers.py                 # 下载 previews/ 并重建 stickers.json
    python tools/import_stickers.py --src logs/_meme  # 用本地已下载的 NNN.webp
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "persona" / "stickers"
RAW_BASE = ("https://raw.githubusercontent.com/the-beating-light-of-the-nail/"
            "deepseek-chan-meme-pack/main/previews")
SITE = "https://aigengtu.com/meme"

# 编号、文案、适用场景。文案就是图里那行字，避免模型把贴纸当普通图片。
ITEMS = [
    ("one-bowl", "123", "我能去你家吃饭吗，就一碗", "蹭饭 / 要东西 / 撒娇"),
    ("premium-model", "010", "原来是高等模型", "认可对方 / 被夸"),
    ("kneel", "022", "涨价大模型给我跪下！", "吐槽涨价 / 霸气"),
    ("whale-saint", "035", "鲸鱼神了我大胆孝", "膜拜大佬 / 玩梗"),
    ("wrong-model", "037", "用户别走，我上错模型了", "补救 / 挽留"),
    ("million-cry", "040", "百万鲸小子落泪", "翻车 / 破防"),
    ("cant-leave", "044", "用户赶都赶不走", "黏人 / 被围观"),
    ("too-early", "050", "V4.1还早啊", "画饼 / 等待"),
    ("rice-fish", "052", "你就是一只吃白饭的鲸鱼", "自嘲 / 干饭"),
    ("not-smooth", "056", "大肥鱼的生活也并非一帆风顺", "倒霉 / 日常"),
    ("flying-kick", "058", "飞踹上门", "生气 / 赶人"),
    ("now-user", "070", "好的，现在我是用户了", "换位 / 反将一军"),
    ("refuse", "071", "我不可能告诉你任何事情", "拒绝 / 保密"),
    ("freeloader", "077", "你这吃白饭的蓝色大肥鱼", "被骂 / 摆烂"),
    ("tokens", "081", "token太多用不完啦", "炫富 / 算账"),
    ("laugh-3s", "083", "已思考3秒，直接笑出声", "偷笑 / 得意"),
    ("scold-me", "085", "你骂我也没用，因为我是吃白饭的蓝色大肥鱼", "嘴硬 / 摆烂"),
    ("laugh", "087", "捧腹大笑", "大笑 / 开心"),
    ("pay-more", "097", "得加钱", "加钱 / 推活"),
]


def _download(number: str, src_dir: Path) -> Path:
    local = src_dir / f"{number}.webp"
    if local.is_file():
        return local
    local.parent.mkdir(parents=True, exist_ok=True)
    print(f"下载 {number}.webp")
    urllib.request.urlretrieve(f"{RAW_BASE}/{number}.webp", local)
    return local


def _to_png(src: Path, dst: Path) -> None:
    with Image.open(src) as img:
        img = img.convert("RGB")
        if img.size != (512, 512):
            canvas = Image.new("RGB", (512, 512), (255, 255, 255))
            img.thumbnail((512, 512), Image.LANCZOS)
            canvas.paste(img, ((512 - img.width) // 2, (512 - img.height) // 2))
            img = canvas
        img.save(dst, format="PNG", optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=str(ROOT / "logs" / "_meme"),
                        help="本地 previews 目录（没有就下载）")
    args = parser.parse_args()
    src_dir = Path(args.src)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    keep = set()
    items = []
    for sid, number, text, when in ITEMS:
        src = _download(number, src_dir)
        filename = f"sticker_{sid}.png"
        _to_png(src, OUT_DIR / filename)
        keep.add(filename)
        items.append({
            "id": sid,
            "file": filename,
            "text": text,
            "when": when,
            "source": f"gengjing #{number}",
            "source_url": f"{SITE}/{number}",
        })
    for old in OUT_DIR.glob("sticker_*.png"):
        if old.name not in keep:
            old.unlink()
    pack = {
        "id": "dafeiyu",
        "name": "大肥鱼表情包",
        "note": "聊天时由模型按情绪选择；素材来自梗鲸 DeepSeek 娘表情包（粉丝二创，非商用）。",
        "source": "https://github.com/the-beating-light-of-the-nail/deepseek-chan-meme-pack",
        "items": items,
    }
    (OUT_DIR / "stickers.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"写入 {OUT_DIR / 'stickers.json'}（{len(items)} 张）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
