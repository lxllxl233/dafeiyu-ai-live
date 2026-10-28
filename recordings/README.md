# 录像回放

一局一个文件夹，默认一个 `run.mp4`。视频永远不进 Git（本目录只提交说明和索引）。

```text
recordings/
├── README.md                  ← 本文件（入库）
├── INDEX.md                   ← 自动索引（入库，replay index 重写）
├── slay-the-spire-2/          ← 游戏 id，和 games/registry.json 对齐
│   ├── run-0094/
│   │   ├── run.mp4            ← 这一局唯一的录像
│   │   └── meta.json          ← 局号/角色/进阶/结果/大小/时间
│   └── run-0093/
└── demo/                      ← 第二个游戏自动多一个目录
```

## 命令

```bash
# 开一局：建目录 + 把 OBS 录像目录指过去 + 开始录
python main.py replay start --character ironclad --ascension 10

# 打完：停录，把 OBS 输出收成 <局>/run.mp4，补 meta，重写 INDEX
python main.py replay stop --result win_a10

# 只建目录、不录（先排好再手动开 OBS）
python main.py replay new --run 97 --character ironclad --ascension 10 --note "实验卡组"

# 看 / 重写索引
python main.py replay list
python main.py replay index

# 把根目录散放的旧 mp4 收进分游戏目录（一次性）
python main.py replay migrate
```

- 局号默认自动 +1；也可以 `--run 98` 指定。
- 目标 `run.mp4` 已存在时默认报错，不悄悄覆盖；确认要换加 `--force`。
- OBS 不支持 `SetRecordDirectory` 时，`stop` 会读 `StopRecord.outputPath` 再把文件挪进来。
- 老的 `tools/rec_episode.py start/finish/list` 仍然可用，内部走同一套目录。

## 旧文件

历史上的 `sts2_ironclad_a10_runNN_<result>.mp4` 已按局号迁到
`slay-the-spire-2/run-00NN/run.mp4`，`result` 写进 `meta.json`。
对不上局号的文件放进 `slay-the-spire-2/_legacy/`，不参与索引。

## 为什么一局一个文件

- 一集 = 一局，剪辑/上传/回放都按目录找，不用在一堆时间戳里猜。
- `meta.json` 让「第几局、什么角色、什么进阶、怎么死的」跟视频绑在一起。
- 默认只留一个 `run.mp4`；同一局多录的段会被挪进 `_extra/`，不参与主回放。
