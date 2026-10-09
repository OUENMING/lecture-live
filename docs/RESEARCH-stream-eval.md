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

**2026-10-09 用 5 片真课录音复核**（同一份模拟，但派生量已收口到 `draft_policy.append_only` —— 见下）：

| 语料 | 请求数 legacy → clause | 边界命中 legacy → clause | 句末未覆盖尾巴 legacy → clause |
|---|---|---|---|
| ECON10770（4 分钟） | 38 → 37 | 0.79 → 0.81 | 0.17 → **0.19** |
| ECON10740.001 | 54 → 50 | 0.59 → 0.68 | 0.11 → **0.13** |
| ECON10740.003 | 30 → 28 | 0.57 → 0.64 | 0.26 → **0.27** |
| SOC10020.001 | 42 → 42 | 0.69 → 0.76 | 0.20 → 0.20 |
| SOC10020.003 | 22 → 21 | 0.59 → 0.62 | 0.24 → **0.26** |
| 合计 | 186 → **178（−4%）** | ≈0.65 → ≈0.70 | 变差 |

真课上请求只少 4%，**而且"句末未覆盖的尾巴"在 5 片里有 4 片变差**
（等到从句边界再送 → 最后那截更晚才翻到）。**结论不变：不切。**
⚠️ 顺带收了一个重复：`append_only`（"这一次的假设是不是原样接长"）原来在**主循环**手搓一份、
`draft_policy_sim.py` 又用 `stream_metrics.normalize` 写了一份 —— 两份不等价，所以那份模拟
**预测的不是生产真跑的那条路**。现在判据在 `draft_policy.append_only` 一处，两边都调它。

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

---

# 9. 现场采集：让**真课**自己出数（2026-10-09 加）

上面 §2 那张表里的「真实课堂」一栏只有 **3 分钟 / 17 句 / 149 份草稿** —— 而它恰恰
是唯一能回答"这套改动有没有用"的数据。§1 的回放量具只能**回放录音**：走脚本自己搭的
流水线、虚拟时钟、不排队，所以它量到的**不是生产真值**。

所以加了 `livemetrics.py`：把 `main.drain()` 那个缝（**用户实际看见了什么**）记成
`<会话名>.live.jsonl`，就在会话 md 旁边。

## 9.1 采到了什么 / 怎么读

```sh
ClassLive.app/Contents/MacOS/python scripts/class_report.py sessions/<会话>.live.jsonl
ClassLive.app/Contents/MacOS/python scripts/class_report.py sessions/<会话>.live.jsonl --json out.json
```

输出的每一行事件与 `stream_eval.py --jsonl` **同款字段**，于是：

| 问题 | 由什么回答 |
|---|---|
| 真课的草稿回改率是多少 | `class_report` 的 `baseline(raw)` 那一行 |
| 前缀冻结在真课上有多少用 | 同一个文件里 `holdback=1/2/3` 那几行 —— **同一次运行出的** |
| 腰斩半句被臆造的地方在哪 | `腰斩臆造候选`（⚠️ 机械判据，**要人耳听**）· 每条给出定稿、最后一份草稿、站不住的尾巴 |
| 本地小模型多久回显一次上下文 | `.live.jsonl` 最后那行 `summary` 里的 `echo_retries` |
| 译文在草稿阶段抖不抖 | `kind=draft_zh` 那些行（**没人算过**，数据先留着） |

⚠️⚠️ **分组键是 `seg`（一段缓冲 = 一次草稿会话），不是"一句定稿"。** 一段 VAD 会被
`split_sentences` 切成**多句**定稿，所以「草稿 : 定稿」在生产里**不是 1:1**。
2026-10-09 第一次真跑就栽在这上面：按定稿分组时 32 句里 14 句只分到 0–1 份草稿，
`草稿→定稿` 从 10.7% 跳到 **93.3%** —— 那是**分组错了**，不是模型退化了。
**段号从哪来**：`livemetrics.LiveLog.segment_end()` 挂在 `Segmenter` 的
`on_utterance_end` 回调上（**段落真正结束那一刻**），段号随 `finalq` 一路带到定稿、
随 `drafts` 队列带到 `drain()`。
⚠️ **不能**用 `main.final_gen["n"]`：它要到**定稿上屏**时才自增，比段落真正结束晚
2–4 秒（Whisper + LLM）→ 新段前几份草稿带着上一段的号，`草稿→定稿` 虚高到 **65%**（也实测踩过）。
判据：`tests/test_class_report.py::test_segment_grouping_when_seg_is_present` +
`tests/test_livemetrics.py::Segments`（4 条）。

**出报告要多久**（2026-10-09 实测，合成 4200 词的事件表、`--ks 0,1,2,3`）：
**0.10 秒**（`--json` 时 5.4 秒）；8500 词（≈一节 50 分钟真课的词量）**0.33 秒**。
差别全在 `draft_vs_final_disagreement` 那一项上 —— 它是**整课词表的 O(n²) 编辑距离**
（实测 2.4k 词 0.9s、4.9k 词 4.4s、10k 词 17.8s；独立复核跑到 2.5 万词时**单遍 111 秒、
一次报告 461 秒**），而文本表根本不打它、`pool_sweeps` 也不汇总它。
原来 `sweep()` **每个 K 都算一遍**（默认 4 遍）→ 一节真课的报告要等 **1–8 分钟**，
看起来就像卡死了（报告工具 99% 以上的时间花在这个没人读的标量上）。
现在它**只在要 `--json` 时算、且只算基线那一遍**。

**它给主循环加了多少活**（2026-10-09 实测，`LiveLog` 直接压测 2 万次）：
`draft()` **1.8 微秒/次**、`final()` **4.0 微秒/次**（主线程只做一次 `put_nowait`，
写盘在后台线程）。一节课约 2000 份草稿 → 合计约 **4 毫秒**。⛔ 卡住的地方也不反压：
队列满就丢并计数（那 2 万次的压测里丢了 11800 条 —— 真实课堂是 1 秒一份草稿，撑不满）。

⭐⭐ **同场 A/B 的关键**：`draft` 事件同时记 `raw`（模型原始假设）与 `text`（上屏的
那份）。所以**冻结开不开都能事后算** —— 一次真课拿到两条曲线，不用跑两节课。

⚠️ 判据：`tests/test_livemetrics.py`（19 条）· `tests/test_class_report.py`（22 条）。
其中一条钉住的是**「离线套冻结 == 回放那条路」**：拿 2026-10-08 那三份记录逐位复核过
（baseline 10.4% / K=1 8.9% / K=2 8.9%，与 `~/.classlive/eval/out/daniel_*.json` 一致）。

## 9.2 上课当天怎么跑（分支 `overnight-20261008`）

```sh
cd ~/lecture-live-overnight
./scripts/link-runtime.sh            # 一次性：把主目录的 sessions/glossary/.course/ClassLive.app 软链过来
                                     #   （代码是这个 worktree 的，数据是你原来那份）
./cl-x test                          # 测试模式 + 现场采集（会录音频 → 开课前要跟同学说一声）
# 或者：./cl-x --freeze 1 test        # 额外把草稿前缀冻结拨上去
```

⚠️ 跑之前的三件事：① **电池**（长跑 66%→20% 那次教训）· ② 网络（云端引擎）·
③ 若要试系统声源，**先**在没上课时跑 `ClassLive.app/Contents/MacOS/python scripts/probe_system_tap.py 10`
（会弹一次授权框，**别在课上弹**）。

## 9.3 课后把样本做大（不用等下一节课）

现有 19 份真课录音就在 `sessions/*.wav|*.opus`。挑几分钟切片各跑一次回放，
把样本从 17 句堆到上百句：

```sh
ClassLive.app/Contents/MacOS/python scripts/stream_eval.py sessions/<录音>.wav \
    --start 300 --dur 240 --jsonl /tmp/slice1.jsonl --json /tmp/slice1.json
ClassLive.app/Contents/MacOS/python scripts/class_report.py /tmp/slice1.jsonl   # K 曲线
```

⚠️ 一份 `--jsonl` 就够跑整条 K 曲线（`raw` 字段在，不必对每个 K 重跑 ASR）。
⚠️ 回放不排队 → **延迟是下限**，别拿去和现场那份比延迟；文本类指标（回改率）可比。

**2026-10-09 实跑过一遍**（5 片 × 4 分钟，ECON10740 ×2 / SOC10020 ×2 / ECON10770 ×1；
`scripts/class_report.py <每片 jsonl> --json` 汇总，**按计数相加**）：

| 设置 | 回改率 | beyond-k |
|---|---|---|
| 基线(raw) | **40.9%** | 29.2% |
| hold-back K=1 | **35.4%** | 24.3% |
| hold-back K=2 | 36.0% | 24.7% |
| hold-back K=3 | 37.1% | 25.7% |

样本 **98 句 / 846 份草稿 / 6195 个已显示词**（原来那一栏是 17 句 / 149 份 —— 大了 5.8 倍）。
读法：① 真课的草稿回改率就是 **40% 上下**，比 TTS（10–14%）高 3–4 倍，**这才是主问题**；
② 前缀冻结在真课上砍掉约 **13% 相对**（40.9% → 35.4%），比 3 分钟那次的 21% 小 ——
**样本大了之后效应变小了**，所以早先那个 −21% 是乐观的；③ 曲线**不单调**，K 越大反而略差
（K=1 35.4% < K=2 36.0% < K=3 37.1%）→ 选 K 只能实测，不能外推。
⚠️ 这是**回放**的数字（喂的是归一化后的 16-bit wav），不是现场真值；现场真值得等
`<会话>.live.jsonl`。两者口径可比，但别混着写进同一句话。

## 9.4 ⭐ 交叉验证：现场采集的数字与离线回放对得上（2026-10-09 实测）

同一份音频（`~/.classlive/eval/econ_daniel.wav`，TTS、有参考稿）两条路各跑一遍：
`main.py --source file`（**生产真值**，`CLASSLIVE_LIVE_LOG=1`）对 `stream_eval.py`（离线回放）。

| 指标 | 现场采集（生产） | 离线回放 |
|---|---|---|
| 段 / 句 | 12 | 12 |
| 草稿数 | 104 | 107 |
| 基线回改率 | **10.1%** | 10.4% |
| hold-back K=1 | **8.6%** | 8.9% |
| hold-back K=2 | **8.6%** | 8.9% |
| beyond-k（k=2）基线 | **6.6%** | 6.7% |
| 草稿→定稿编辑率 | **10.7%** | 10.7% |
| 腰斩臆造候选 | **1 条（`the quantity is less`）** | 1 条（同一条） |

**读法**：生产那份的数字可以直接和 §2/§3 那些离线基线放在一张表里比 —— 这正是
"同一把尺子"要的证据。臆造候选两边**都只命中那一条真的**（§2 记的那个 Daniel 案例），
说明这个机械判据不是噪声发生器。

⚠️ **走到这一步踩了两个坑，都值得记**（都是"分组错了"伪装成"模型退化"）：
1. 按"一句定稿"分组 → 32 组里 14 组只有 0–1 份草稿 → 草稿→定稿 虚高到 **93.3%**。
2. 段号取自 `main.final_gen["n"]`（**定稿上屏时**才自增，比段落真正结束晚 2–4 秒：
   Whisper + LLM）→ 新段前几份草稿带着上一段的号 → 草稿→定稿虚高到 **65%**。
   修法：`live.segment_end()` 挂在 `Segmenter` 的 `on_utterance_end` 回调上，段号随
   `finalq` 一路带到定稿。

## 9.4.1 ⚠️ 改完这一层**必须真跑一次**（默认闸门够不到）

`final_worker` / `partial_worker` 都是 `main.run()` 里的**嵌套闭包** ——
`tests/test_audit_regressions.py`（默认闸门）**一条都碰不到它们**。2026-10-09 就栽在这上面：

> 简化那一轮我把 `finalq` 元素的拆包从"两个哨兵之后"提到了**之前**（还写了句
> "走到这里一定是元组"，**那句是错的**）。而"空转"那条路**每轮都会走**（`finalq` 空时
> `buf = EMPTY`）→ `buf, _fseg = EMPTY` 抛 `TypeError` → **`final_worker` 线程当场死掉**
> → 没人消费 `finalq` → `partial_worker` 的"有定稿在排队就让开"永远成立
> → **整节课一份草稿都不出**。三份毫秒级判据 + 116 条闸门**全绿**，只有真跑才抓到。

所以：**动了 `finalq` / `streamq` / `drafts` 任何一个的元素形状、或动 `drain()` 的分发，
就必须跑一次下面这条**（`--source file` 回放一段音频，喂的是 `~/.classlive/eval/` 里的 TTS 语料）：

```sh
# ⚠️ 先把 sessions 软链指到临时目录，别让这次验证往真 sessions/ 里写（吃过一次亏）
mkdir -p /tmp/cl-probe/sessions && ln -sfn /tmp/cl-probe/sessions sessions
CLASSLIVE_LIVE_LOG=1 ClassLive.app/Contents/MacOS/python main.py \
    --source file --path ~/.classlive/eval/econ_daniel.wav --ui terminal \
    --save-notes yes --vault /tmp/cl-probe/vault 2>&1 | tail -40
ln -sfn ~/lecture-live/sessions sessions          # 还原
# 判据：没有 Traceback；`.live.jsonl` 里 draft/final/summary **三种都有**；
#       `class_report.py` 跑得出回改率（见 §9.4 那张对照表）
```

⚠️ **没有自动化的端到端判据**（这是个已知缺口）—— 要么把 `final_worker` 的分发抽成
可测的纯函数（评审建议过，代价是动 `drain()` 那段"绝不能抛"的代码），要么就靠上面这条手跑。

## 9.5 还没做 / 别声称做了

- **`--source system`（Process Tap）在真机上仍未跑过** —— §6 那一段的结论一个字没变。
- `draft_zh` 采了但**没有分析器**（要回答什么还没想清楚，先留数据）。
- 臆造候选的判据是机械的：Parakeet 与 Whisper 是两个模型，正常措辞差异也会命中。
