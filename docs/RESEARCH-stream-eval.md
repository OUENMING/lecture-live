# 流式字幕量具与 2026-10-08 夜间改动：数字与取舍

> 日期 **2026-10-08**。分支 `overnight-20261008`（未合并、未推送）。所有数字是本机一次实测，
> 语料与命令在下面，**可复跑**。TTS 语料干净，数字是乐观上界；真实课堂只看稳定度与延迟（没有参考稿）。

## 1. 量具怎么跑

```
# 语料（一次）：macOS say 合成 2 个声音 × ~2 分钟，参考稿 = scripts/eval_corpus/econ_lecture.txt
python3 scripts/eval_corpus/make_tts_corpus.py            # 写到 ~/.classlive/eval/

# 回放（用 App 自带 python；约 40–70 秒/段）
ClassLive.app/Contents/MacOS/python scripts/stream_eval.py ~/.classlive/eval/econ_daniel.wav \
    --ref ~/.classlive/eval/econ_daniel.txt [--stabilize 2] --json out.json --jsonl out.jsonl
```

- 复用生产的 `Segmenter`（VAD 切句 + 每 1s 一次草稿）和 `PeakNormalizer`；草稿 = Parakeet 最近 10s，定稿 = Whisper 整句。
- **用回放时钟替掉 `vad.time.monotonic`**：Segmenter 的 1s 草稿节拍读墙钟，回放快于实时，不换的话 12 句只出 6 份草稿（第一版就栽在这）。
- **同步回放、不排队** → 延迟是下限（生产里草稿/定稿线程抢同一把 ASR 锁）。翻译不在回放里。
- 指标（`scripts/stream_metrics.py` 文件头有定义与出处）：WER（先归一化，数字读成英文单词，`10%`=`ten percent`）、
  **草稿回改率**（相邻草稿间，上一份里"没有作为前缀保留"的已显示词数 ÷ 已显示词数；缓冲超 10s 后窗口滑动的草稿不计，单独数）、
  `beyond_k`（把最后 k 个词当灰尾吸收后仍被改写的）、草稿→定稿的编辑量（半全局对齐，草稿只覆盖定稿后半段）、延迟 p50/p95、RTF。
- 判据：`tests/test_stream_metrics.py`（23 条，WER 值与 jiwer 500 组随机对拍 0 不一致；改坏 `lcp_len`/回改累计都会红）。

## 2. 基线（改动前的生产行为）

| 语料 | 句数 | 草稿数 | WER | 回改率 | 回改率(beyond k=2) | 草稿→定稿编辑量 | 首字可见 p50/p95 | 定稿计算 p50/p95 | 定稿 RTF p50 |
|---|---|---|---|---|---|---|---|---|---|
| TTS Daniel | 12 | 107 | **5.4%**（2 替/1 漏/**14 插**） | 10.4% | 6.7% | 10.7% | 0.97 / 3.1 s | 2.77 / 3.06 s | 0.25 |
| TTS Samantha | 12 | 100 | **1.3%**（3 替/0 漏/1 插） | 13.9% | 10.0% | 8.2% | 0.95 / 3.1 s | 2.43 / 3.08 s | 0.25 |
| 真实课堂 `rec_0908_1033_n5` 60–240s | 17 | 149 | 无参考稿 | **39.6%** | **29.6%** | **38.4%** | 1.06 / 4.14 s | 2.64 / 2.91 s | 0.23 |

读法：
- **真实课堂的草稿稳定度比 TTS 差 3–4 倍**（回改率 40% vs 10–14%）。TTS 上看不出的问题，真实课堂上是主问题。
- 草稿与定稿的分歧 41%（TTS 17–19%）：这是 Parakeet 与 Whisper 两个模型的差，不是回改。
- **发现一个定稿缺陷**：Daniel 的 14 个插入词不是随机的——VAD 在一个停顿处把一句话腰斩，Whisper 给前半句**臆造了续写**
  （`…the quantity` → 定稿成 `…the quantity is less.`，而下一句才是真正的 `quantity falls by more than 10%`），
  另一处整段 `percentage change in quantity…` 被重复。Samantha 同一稿没有切在那里，所以只有 1 个插入。
  → 真实课堂里"被腰斩的半句"是常态（12s 硬切 + 换气停顿），这条值得单独立项量（见 §6）。

## 3. 草稿前缀冻结（`draft_stable.py`，默认关）

`CLASSLIVE_DRAFT_HOLDBACK=2` 打开（数字 = 灰尾词数）。只改上屏的草稿，**送去翻译的仍是原始假设**。

| 语料 | 回改率 基线 → K=2 → K=1 | beyond k=2 基线 → K=2 → K=1 |
|---|---|---|
| TTS Daniel | 10.4% → 8.9% → 8.9% | 6.7% → 5.2% → 5.2% |
| TTS Samantha | 13.9% → 13.0% → 12.9% | 10.0% → 9.2% → 9.1% |
| 真实课堂 | 39.6% → 33.1% → **31.2%** | 29.6% → 23.4% → **21.7%** |

**结论：有用但不是根治。** 真实课堂回改率降了约 1/5（相对），草稿→定稿的编辑量没变（38%→40%，因为那是两个模型之差）。
剩下的回改主要发生在**还没被冻结的前缀**上——真实课堂里 Parakeet 对一句话的开头要连续改好几轮才稳。
想再降要么加大灰尾（把最后 3–4 个词不上屏/画灰），要么换草稿模型/加音频上下文，不是这层文本能解决的。
没做 UI 的"灰尾"画法（`DraftStabilizer.committed` 暴露了冻结词数，UI 想画灰就读它）。
判据：`tests/test_draft_stable.py`（12 条，含"下一句的草稿不被上一句的冻结前缀拖着"，改坏阈值/越界保护都会红）。

## 4. 草稿译文触发策略（`draft_policy.py`，默认 `legacy` = 原行为逐字等价）

`CLASSLIVE_DRAFT_TRIGGER=clause` 才换（落在从句边界或前缀稳定才送，防饿死）。离线回放 `scripts/draft_policy_sim.py`：

| 语料 | 请求数 legacy → clause | 落在从句边界 legacy → clause | 首发延迟 p50 |
|---|---|---|---|
| TTS Daniel | 30 → 29 | 73% → 76% | 不变 |
| TTS Samantha | 28 → 27 | 54% → 63% | 不变 |
| 真实课堂 | 35 → 34 | 49% → 59% | 不变 |

**结论：差异很小，不建议切。** 边界命中率 +3~10 个百分点、少 1 个请求，量不出译文质量。要切必须拿真译文肉眼比一次。
`legacy` 与原内联条件的等价性有 5000 组随机对拍钉着。

## 5. 本地小模型输出后处理（`translator.py`，**默认就开**——只作用于本地引擎）

- 剥「以下是译文：」之类的元话前缀（只认"前缀+冒号"，`翻译是一门艺术` 这类正文不动，测试钉着）。
- 译文与**上一句译文**的字符二元组重合 ≥0.45 → 判为"翻成了上下文"，不带上下文重试**一次**；仍回显就保留（宁可有译文）。
  阈值 0.45 来自 livecaption；**用本项目真实数据复核过**：39 节课 4780 对相邻句（只读），0.45 误伤 0.54%（26 对，多是讲师真的重复），0.35 为 1.1%，0.25 为 3.5%。
- 没在真模型上跑过（mlx 有内核崩溃史，夜里不碰）；用假 `mlx_lm` 钉住了流程。**不知道 Qwen3-1.7B 实际多久回显一次**——`Translator.echo_retries` 是只读计数，下次本地引擎真跑时看它。
- 判据：`tests/test_local_postprocess.py`（12 条）。

## 6. 系统声音源 `--source system`（`system_tap.py`，**未经真机验证**）

- 链路：`CATapDescription(mono, 全局, 不排除进程)` → `AudioHardwareCreateProcessTap` → 私有聚合设备 → `AudioDeviceCreateIOProcIDWithBlock`。
  思路来自 WhisperCaption（MIT）；macOS ≥14.4，只要「音频录制」授权，不要「屏幕录制」。
- 已验证（不需要授权）：PyObjC 里这些符号都在；`initMonoGlobalTapButExcludeProcesses_` 存在（直接出单声道）；
  用 ctypes 读设备采样率的调用约定（默认输出设备 → 48000.0）；重采样（numpy 加窗 sinc：1kHz 保幅度、12kHz 衰减 >40dB、分块与一次性一致）；
  分块/丢旧保新/静音看门狗/接口形状；`AudioBufferList` 解码的两种指针形态（假对象）。共 20 条测试，改坏截止频率/丢旧逻辑都会红。
- **没验证**：创建 tap 本身（会弹授权框，夜里没人）、IOProc 回调里 PyObjC 实际给的 `mData` 类型、权限被拒时是报错还是给全零。
- **LecSync 的做法不同**：Electron 的 `getDisplayMedia` loopback（ScreenCaptureKit 音频，要「屏幕录制」权限，macOS 13.2+），
  说明 SCK 音频在 macOS 15 上是能工作的（和 WhisperCaption 作者"SCK 在 15+ 回调不触发"的说法矛盾）；但它要更重的权限，Process Tap 仍是更优先的路。
- **上机步骤**：放点声音，`ClassLive.app/Contents/MacOS/python scripts/probe_system_tap.py 10`，会弹一次授权框，成功会写 `~/.classlive/eval/tap_probe.wav` 供耳听。

## 7. 从竞品/开源项目里看到、但**没做**的

- LecSync（Electron，云端转录/翻译）：客户端是薄层，音频全上传；"本地 AI 翻译"代码里已下架（官网宣传过时）；
  值得借的：①**触发式精修门**（只在数字缺失 / 术语未命中 / 长句≥10 词时才调 LLM 精修，省钱省延迟）
  ②翻译只带最近 2 句且字数封顶 ③"用户手改 → 候选术语"闭环（严过滤，宁漏勿脏）
  ④悬浮窗细节（`showInactive`、光标轮询显隐控件、三档字号）⑤RMS<0.001 持续 10s 告警。
- 词级时间戳 / AlignAtt / CIF / DRY：依赖 Whisper 解码器内部，Parakeet 用不上。
- Apple Translation 当断网兜底：macOS 15 上只有 SwiftUI 入口，PyObjC 调不到，不划算。

## 8. 复现与回退

- 测试：`tests/test_stream_metrics.py` `test_system_tap.py` `test_draft_stable.py` `test_draft_policy.py` `test_local_postprocess.py`，全是毫秒级、无网络无模型。
  默认闸门 `tests/test_audit_regressions.py` 全绿（116 条，1 条既有 skip）。
- 默认行为：除 §5（本地引擎后处理）外，其余都**默认关**。`git diff 44810e1..overnight-20261008 --stat` 看全部改动。
- 没做：真机跑一节课、`probe_testmode_report.py`（会写 sessions/）、`main.py` 的端到端（只做了语法/ruff/默认闸门；`partial_worker` 是嵌套闭包，没法单测，
  默认路径与改前逐字等价靠 `legacy` 的随机对拍保证）。
