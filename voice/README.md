# 语音层（Voice）

直播间的声音有两种口音，共用一个路由：

| 语音 | 目录 | 引擎 | 依赖 | 什么时候用 |
|---|---|---|---|---|
| **动物语** | `voice/animal/` | 五声音阶短音节（纯 Python 合成） | 无 | 默认回退；OBS 页面也用它做「动森式」音效 |
| **曼波语** | `voice/mambo/` | 本地 MamboTTS / GPT-SoVITS（`:9880`） | 引擎 + 参考音频 | 主配音，音色像主播本人 |

```
台词 ──► VoiceRouter.speak()
             ├─ 首选 mambo（在线才用）
             └─ 回退 animal（永远可用）
```

## 动物语

`voice/animal/scale.json` 可调：音阶、基频、音节时长、音量。
合成规则和 `ui/overlay/index.html` 的 JS 版一致（每字一个短音节，随机上下行，12% 概率翻八度）。

试听（生成 WAV 到 `logs/voice_test.wav`）：

```powershell
python dafeiyu.py voice test "它 21，我最多挡 15。差 6，嗨呀。" --voice animal
```

## 曼波语

引擎不在仓库里（体积大、要模型），直播姬只负责调用：

1. 起 MamboTTS（默认 `127.0.0.1:9880`，见其 `engine_contract.py`）；
2. 把参考音频路径填到 `voice/mambo/ref.json` 的 `ref_wav`，或设环境变量 `STS2_TTS_REF`；
3. `python dafeiyu.py voice test "..." --voice mambo`。

合成结果按文本 sha1 缓存在 `logs/tts_cache/`，页面 `/tts` 和桌面弹幕窗共用这份缓存。
连续失败会由 `tools/tts_guard.py --auto` 拉起看护（overlay_server 里触发）。

## 回退策略

- 曼波离线 / 超时 / 返回坏 WAV → 自动回退动物语，`/api/studio` 里能看到 `counts.fallbacks`。
- `DAFEIYU_VOICE=off` 全局静音（字幕照常）。
- 同一个进程里曼波是**串行**合成的（锁 + 缓存），不会并发压引擎。

代码入口：`src/dafeiyu/voice/`（`build_router()` 装配，`VoiceRouter.speak()` 出口）。
