# 任务单：自动语种（中文只转录）

2026-11-16 · Owen 定：场景只有纯英或纯中；中文课**只做转录**，不翻译。
依据：`RESEARCH-speaker-multilang-20261116.md` §8.4（含 11-17 用生产 context 复核的两行）。
派活方式、通用规矩、交回格式：照 `TASKS-web-cloud-20261114.md`「怎么用」「通用规矩」两节，本文不重复。

## 做法

判出这节是中文后，切到**新的翻译档 `zh`**：转录照常走 Soniox，不翻译、不出云端笔记。

**不能复用纯转录档 `raw`**（11-17 核过）：`raw` 会关掉云端识别 —— `main.cloud_asr_off_reason` 对 `mode == "raw"` 返回「纯转录档」（`main.py:2122`），`stream_asr.wanted()` / `stream_gate` 也把 raw 当成不建流的条件。关了 Soniox 就退回本地英文模型，中文全乱。

`zh` 档的规则只有一条：**凡是按 `raw` 判「别发模型请求」的地方，`zh` 同 `raw`；凡是按 `raw` 判「别上云识别」的地方，`zh` 不同于 `raw`。** 判 `== "both"` 的地方（草稿译文等）自动把 `zh` 排除在外，不用改。

## L-1 识别层带出语种，中文能封口

**只许改**：`stream_asr.py`、`tests/test_stream_asr.py`

1. `_config()` 加 `"enable_language_identification": True`；`language_hints` 与 context 不动（11-17 实测：英文课错误率 8.9% 开关前后相同；中文段在生产 context 下 8.5%、语种标签错 0.8%）。
2. `Tok` 加 `lang: str = ""`；`Sealed` 加 `lang: str = ""`（句内 token 的多数语种，且文本里确有汉字才记 `"zh"`）。都是带默认值的追加。
3. 句末标点加全角 `。？！`：`END_PUNCT`、`at_boundary()`。先 `grep -n '\.?!' *.py` 列出全部按半角判句末的位置，交回里逐条写「改 / 不改 + 理由」。

完成判据：

- 新测试：合成中文 token（含 `。`）经 `StreamCore` 封出句子且 `lang == "zh"`；英文 token 封口结果与改前逐字相同。
- 变异验证：从 `END_PUNCT` 拿掉全角标点，新测试变红。
- 〔作者做〕回放 `~/.classlive/eval/diar_spike/out5/prod_ctx.jsonl`（纯中文、生产 context）与 `out7/prod_ctx.jsonl`（纯英文课）：中文段封口句 > 100 且无一句超过 60 字；英文段封口句与改前逐字相同。公开语料，**留在仓库外**。

## L-2 判出中文就切 `zh` 档

**前置**：L-1 验收、调试通道 D-2 验收（用 `clctl` 验）
**只许改**：`main.py`、`overlay.py`（只动模式显示）、`caption_flow.py`、新文件 `lang_gate.py`（纯函数 + 状态，无 I/O）、对应测试

1. 先交一张表：`grep -n '"raw"' main.py overlay.py stream_asr.py stream_gate.py final_asr.py backfill.py obsidian_writer.py testmode.py`，每一处写「`zh` 同 raw / 不同 / 无关 + 一句理由」。表经 Claude 看过再动代码。
2. `lang_gate`：吃封口句的 `lang`，**连续 3 句是 `zh`** → 回一次「切 zh」；切过就不再判，也不切回。用户手动换过档（`trans["ever_off"]`）后不再自动切。
3. `main`：在 `sealedq` 的消费处喂 `lang_gate`；回「切 zh」时把 `trans["mode"]` 设成 `"zh"`（与 `on_translate` 同样写 `ever_off`），通知悬浮窗显示「只转录」，并发一条现有的 `notice`：「识别到中文，已切到只转录」。切换前已经送翻的几句照常完成。
4. `overlay.py`：`_trans_mode` 接受 `"zh"`，排版按 `raw` 处理，模式胶囊显示「只转录」；用户点胶囊轮换时从 `zh` 进 `both`。
5. `caption_flow.n_words`：汉字占非空白字符 ≥ 50% 时按汉字数计。否则整句中文 `split()` 只得 1 个词，被 `is_tail` 当成「Okay?」并进上一行（`caption_flow.py:107-114`，`TAIL_WORDS = 3`、`GAP_S = 3.0`）。
6. `main.split_sentences` 只按半角 `.!?` + 空格切（`main.py:140-160`）：中文句由 Soniox 逐句封口，这里不改，在交回里写明核过。

完成判据：

- `lang_gate` 单测：zh/zh/zh → 切一次；zh/en/zh/zh → 不切；切后再来 en 不切回；`ever_off` 时不切。
- `cloud_asr_off_reason(mode="zh", …)` 返回 None；`backfill` 不把 `zh` 档的句子当漏译。
- `caption_flow` 单测：一句 12 个汉字、离上句 1 s，`is_tail` 为 False；`"Okay?"` 仍为 True。
- 用调试通道验：`run_isolated.py` 以**实时速度**放 3 分钟中文音频（`~/.classlive/eval/diar_spike/data/zh_only.wav` 截前 3 分钟，test key），`clctl --wait 'trans.mode == zh' --timeout 120` 退出 0，之后翻译请求计数不再增加；同样放 3 分钟英文课，`trans.mode` 始终是 `both`。
- 〔作者做〕悬浮窗模式各看一遍。约 10 分钟。

估时：L-1 1 天，L-2 3–4 天（无实测依据）。

## 已知缺口（写进交回的「没做到的」，不在本单解决）

- Soniox 掉线退回本地识别时，本地模型只会英文，中文段会乱。
- 悬浮窗行高、截断按「英文在上、中文在下」排，纯中文一行的观感没调。
- 头 3 句仍会送翻译（多花几次请求，译文是中译中）。
- 中文课的笔记：`zh` 档不出云端笔记，只留 `sessions/` 里的逐字稿。
