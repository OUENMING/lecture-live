# 计划：草稿层换成真流式 ASR（Nemotron Streaming）

> 2026-10-09 写 · 基于 main `96bf33b` · **还没动代码**
> 读者：照着做改动的 AI。**每一节都是要执行的**，§8「雷区」是这个仓库已经付过学费的地方。
> 依据的数字都是实测的（脚本见 §9），引用的外部说法都标了来源与日期。

---

## 0. 一句话

现在的草稿是「Parakeet（离线模型）每秒把最近 10 s 重新转写一遍」→ 前面已经显示的字会被改
（真课回改率 **36.6%**）。换成**真流式**模型 Nemotron Speech Streaming EN 0.6B（**同一个
sherpa-onnx**，零新依赖）后，同一节课前 240 s 实测回改率 **0.5%**，计算量 RTF 0.126。
**第一阶段只换草稿层**；定稿（Whisper）、断句（Silero + `Segmenter`）、翻译、UI 一概不动；
任何一步失败都退回今天的 Parakeet 草稿，**课照常上**。

## 1. 调研结论：选哪个模型

| 候选 | 形态 | 能不能直接用 | 结论 |
|---|---|---|---|
| ⭐ **Nemotron Speech Streaming EN 0.6B**（NVIDIA，2026-01） | cache-aware FastConformer-RNNT，原生流式，带标点大小写；chunk 80/160/560/1120 ms | **sherpa-onnx 官方支持**（[文档](https://k2-fsa.github.io/sherpa/onnx/nemo/nemotron-streaming.html)，release 资产 int8 约 464 MB）；App 里的 sherpa-onnx 1.13.8 **实测能跑** | **选它** |
| Nemotron 3.5 ASR Streaming 0.6B（2026-06，40 语种） | 同上，多语种 | sherpa-onnx 也支持 | 课是英文 → 用英文专版；多语种留作以后 |
| Voxtral Realtime（Mistral，4B，Apache-2.0，[arXiv 2602.11298](https://arxiv.org/abs/2602.11298)） | 原生流式，480 ms 延迟下接近 Whisper | 4B 参数，sherpa-onnx 不支持 | 太重，CPU 实时没把握 |
| Kyutai STT 1B / 2.6B（[kyutai.org/stt](https://kyutai.org/stt/)） | 流式，延迟 0.5 s / 2.5 s | 要 PyTorch/MLX 栈 | 引入新运行时，不划算 |
| Moonshine v2（[2602.12241](https://www.alphaxiv.org/abs/2602.12241)） | 滑窗流式编码器，M3 上 50–258 ms | 自带运行时，sherpa-onnx 未见 | 备选 |
| 云端（Soniox $0.12/h、AssemblyAI $0.15/h，2026-09 价） | 托管流式 | 要网 + 音频出机 | 违背「音频不出本机」；本地方案已够好，不做 |

旁证：开源听写工具 OpenWhispr 1.7.6（2026-07）也是用 sherpa-onnx 跑这个模型做流式（[博文](https://openwhispr.com/blog/local-streaming-speech-to-text)）。
⚠️ 反面证据：Mistral 的 Voxtral 论文里 Nemotron Streaming 在长音频集上的 WER 明显高于 Whisper ——
**所以第一阶段不拿它做定稿**（§7）。

### 1.1 本机实测（M1 Pro 16 GB，2 线程，chunk 560 ms int8）

| 量 | Nemotron | 现状 Parakeet 草稿 | 出处 |
|---|---|---|---|
| 回改率（10-09 真课前 240 s，**按本计划的接法**） | **0.5%**（26 段 / 180 份草稿） | 36.7%（25 段 / 196 份） | `drafter_design_check.py` |
| 每次更新耗时 | p50 146 ms / p95 156 ms | Parakeet 每次转写 10 s 窗口 | 同上 |
| RTF | 0.126 | — | 同上 |
| **加载后常驻内存** | **+1011 MB** | — | 同上（`ru_maxrss`） |
| WER，TTS 语料对参考稿 | Daniel **1.6%** / Samantha **1.3%**（整段流式） | Whisper 定稿 5.4% / 1.3% | `RESEARCH-stream-eval.md §2` |
| 与 Whisper 定稿的分歧（真课，非真值） | 25% | Parakeet 末份草稿 40%（受 10 s 窗口截头，不公平） | `nemotron_eval.py` |

### 1.2 VPS 能不能做到零成本

作者的 VPS（2026-10-09 实测）：腾讯云法兰克福，**2 vCPU AMD EPYC 7K62（只有 AVX2，没有 VNNI）**、
**1.9 GB 内存（可用 1.1 GB）**，本机到它 TCP 建连 28 ms。

- 模型常驻要 **~1 GB** → 和机器上现有的 nginx/AList/Next/SearXNG 抢内存，**有把整台机器 OOM 掉的风险**。
- 2 个 Zen2 vCPU、int8 没有 VNNI 加速 → 能撑的并发流**大概 1–2 路**（**没测**，推断）。
- 每个用户的 Mac 本来就比这台 VPS 快、内存大 → **零成本的那条路是「每台 Mac 自己跑」**，
  VPS 不在这条路上。VPS 只在将来「用户机器太弱」时才有意义，那时也得先升级配置（不再是零成本）。

## 2. 阶段与闸门

| 阶段 | 做什么 | 进入下一阶段的闸门 |
|---|---|---|
| **P0** | 把评估脚本落进仓库（§6.1），在真课录音 + TTS 语料上定 chunk（560 vs 1120 ms） | 两份数字写进 `RESEARCH-stream-eval.md` |
| **P1** | 草稿层接入（§3–§5），**默认 `auto` = 模型在且内存够才启用** | §6 全部通过 + **一节真课**：回改率 ≤ 5%、定稿延迟不变差、无回退事件 |
| P2 | 用流式结果改断句（84% 的段是 12 s 硬切） | P1 真课过闸 |
| P3 | 评估能否让流式文本替代 Whisper 定稿 | 要有人工参考稿的真课片段（≥ 5 分钟） |

**本文件只把 P0/P1 写到可执行的粒度。** P2/P3 见 §7，只给方向与闸门。

## 3. P1 设计（为什么这么接）

### 3.1 接在哪：沿用 `Segmenter.on_partial` 的节拍，换掉的只有「转写那一下」

```
main 线程:  chunk → Segmenter.accept → on_partial(buf)（每 1 s，buf = 本段从头到现在）
                                         └→ latest.put(buf, tag=当前段号)
partial_worker:  buf, tag = latest.take
                 ├ 现在：asr.transcribe(buf[-10 s:])            ← 每次重算 10 s
                 └ 之后：draft.transcribe(buf, tag)             ← 每段一条流，只喂新增样本
```

- **每段一条流**：段号变了（或 `buf` 比已喂的短）→ 新建 stream；否则只喂 `buf[已喂:]`。
  `buf` 是**本段累积的**（`Segmenter._buf()` 从段首拼起，见 `vad.py`），所以 latest-wins 丢掉
  中间几份也不丢样本 —— 下一份 `buf` 里全都有。
- **段号用 put 那一刻的**：`on_partial` 与 `on_utterance_end` 都在 main 线程里跑，
  在 put 时读 `seg_gen["n"]` 就是这份 `buf` 真正所属的段（**race-free**）。
  ⚠️ 不能在 `partial_worker` 里读 `seg_gen["n"]` —— 那时下一段可能已经开始了
  （这正是 `HANDOFF-20261009 §6.1` 那类「分组键取自晚到信号」的坑）。
- **定稿路径不碰**：`final_worker` 仍用 Whisper，退化时仍回退 `asr`（Parakeet）——
  所以 **Parakeet 照样加载**（它是定稿的兜底，`main.py` `final_worker` 里 `asr.transcribe(buf)`）。

### 3.2 新逻辑全部放进**一个可测的模块** `draft_engine.py`

⚠️ **这是本计划最重要的结构决定。** `partial_worker` / `final_worker` 是 `run()` 里的嵌套闭包，
**默认闸门一条都够不到**（`HANDOFF-20261009 §6.3`：116 条全绿而整节课一份草稿都不出）。
所以：**选择、喂流、熔断、回退全在 `draft_engine.py`**，用假识别器做毫秒级判据；
`main.py` 只改 4 处接线。

## 4. P1 改动清单（文件 · 位置 · 改成什么）

行号是 `96bf33b` 的，**会漂**；以「锚点」那一列的字符串 `grep` 为准。

| # | 文件 | 锚点 | 改动 |
|---|---|---|---|
| 1 | `draft_engine.py`（**新**） | — | §4.1 全文 |
| 2 | `models.py` | `MODELS: tuple[Model, ...] = (PARAKEET, VAD, WHISPER, QWEN)` | 加 `NEMOTRON` 一条（§4.2） |
| 3 | `fetch_model.py` | `def fetch_whisper(` · `KEYS = (` · `def fetch(key` · `PINS = {` | 泛化 tar 下载 + 加 `nemotron` 分支与 sha（§4.3） |
| 4 | `update.py` | `missing = [m for m in doctor.MODELS if not doctor.model_present(m.path)]`（:934） | **只列 `m.required`**（§4.4 —— 不改的话每个用户的更新卡片都会多一条假警告） |
| 5 | `main.py` | `class _Latest:`（:531） | `put(buf, tag=None)` + `taken_tag`（§4.5a） |
| 6 | `main.py` | `asr = load_asr(args.model_dir)`（:1125） | 紧随其后建 `draft`（§4.5b） |
| 7 | `main.py` | `on_partial=lambda buf: latest.put(buf),`（:1672） | 带上段号（§4.5c） |
| 8 | `main.py` | `t = asr.transcribe(buf[-PARTIAL_MAX_S * SR:])`（:1451，在 `def partial_worker` 里） | 换成 `draft.transcribe`；`drafts` 元组加 `engine`（§4.5d） |
| 9 | `main.py` | `def _pair(item)`（:1875）· `drain()` 里 `live.draft(` · `live.close(`（:2359） | 元组 7 项；`engine` 传给现场采集；收尾 summary 记引擎（§4.5e） |
| 10 | `livemetrics.py` | `def draft(self, raw`（:171） | 加 `engine: str \| None = None` 字段（同 `lag_s` 的写法：给了才写） |
| 11 | `scripts/stream_eval.py` | `sliding = sum(`（:98）与下一行 `rewrite_stats([...] if d["buf_s"] <= PARTIAL_MAX_S]` | 「滑窗剔除」只对**窗口型**引擎生效（§4.6） |
| 12 | `scripts/class_report.py` | `build_utts` 里 `"buf_s": float(rec.get("buf_s")` | 把 `engine` 带进 draft dict（§4.6） |
| 13 | `cl` | `TMCFG=".test-mode"` 那一段之后 | 读 `.draft-engine` 文件 → `export CLASSLIVE_DRAFT_ENGINE`（§4.7，App 启动没有环境变量，这是 App 用户唯一的开关） |
| 14 | `.gitignore` · `scripts/link-runtime.sh` | `.test-mode` 出现的地方 | 加 `.draft-engine` |
| 15 | `tests/test_draft_engine.py`（**新**） | — | §6.2 |
| 16 | 文档 | `CLAUDE.md` 文件地图 · `docs/README.md` · `RESEARCH-stream-eval.md` | 各加一行指针 / P0 数字 |

### 4.1 `draft_engine.py`（骨架 —— 名字与契约照抄，实现可以更好）

```python
"""草稿引擎：选哪个模型出草稿、怎么喂、坏了怎么退。

## 契约（main.partial_worker 只依赖这一个方法）
    eng = load_draft_engine(parakeet_asr, window_samples=PARTIAL_MAX_S * SR)   # 永不抛
    text, used = eng.transcribe(buf, tag)     # used ∈ {"nemotron", "parakeet"}；永不抛
    eng.name / eng.reason / eng.fallback_reason                               # 只读

⚠️ 线程：**只能在一个线程里用**（partial_worker）。sherpa 的 OnlineStream 非线程安全；
   本类不加锁 —— 加了也挡不住第二个线程造成的「流状态错乱」，所以干脆规定单线程。
⚠️ 熔断后**本节课不再试**（不来回抖）：退回 Parakeet，直到进程结束。
"""
ENV = "CLASSLIVE_DRAFT_ENGINE"            # auto | parakeet | nemotron（默认 auto）
ENV_MODEL_DIR = "CLASSLIVE_DRAFT_MODEL_DIR"   # 只给测试/排查用：指向别的目录
MIN_RAM_BYTES = 12 * 2**30                # auto 模式下的内存门（模型常驻 +1 GB，实测）
SLOW_S, SLOW_STRIKES = 0.9, 3             # 连续 3 次单次更新 > 0.9 s 就熔断（实测 p95 0.16 s）
NUM_THREADS = 2

def choose(mode: str, model_present: bool, ram_bytes: int | None) -> tuple[str, str]:
    """纯函数：(引擎名, 理由)。
    - mode 非法 → 当 auto
    - parakeet → ("parakeet", "手动指定")
    - nemotron → 模型在就用（**不看内存**，用户明说要）；不在 → ("parakeet", "模型未下载")
    - auto → 模型不在 → parakeet；ram_bytes 已知且 < MIN_RAM_BYTES → parakeet；否则 nemotron
      （ram 未知 → 放行，理由里写「内存未知」）"""

class NemotronDrafter:
    def __init__(self, model_dir: str, num_threads: int = NUM_THREADS):
        # 文件名**精确匹配**：encoder.int8.onnx / decoder.int8.onnx / joiner.int8.onnx / tokens.txt
        # ⚠️ 不许退到 `*.txt` 猜词表（asr.py ParakeetASR 那段注释讲过为什么）
        # sherpa_onnx.OnlineRecognizer.from_transducer(..., sample_rate=16000, feature_dim=80,
        #     decoding_method="greedy_search", enable_endpoint_detection=False, num_threads=…)
        # ⚠️ 关掉 endpoint：分段归 Segmenter 管，两套分段同时开会互相打架
        self._stream = None; self._tag = object(); self._fed = 0

    def update(self, buf, tag) -> str:
        """喂到 buf 末尾，返回本段当前假设。会抛（由 DraftEngine 兜）。"""
        if self._stream is None or tag != self._tag or len(buf) < self._fed:
            self._stream = self._rec.create_stream(); self._tag = tag; self._fed = 0
        if len(buf) > self._fed:
            self._stream.accept_waveform(16000, buf[self._fed:]); self._fed = len(buf)
        while self._rec.is_ready(self._stream):
            self._rec.decode_stream(self._stream)
        return self._rec.get_result(self._stream).strip()

class DraftEngine:
    def __init__(self, parakeet, streaming=None, *, window_samples: int, reason: str = ""): ...
    @property
    def name(self) -> str: ...   # 当前生效的："nemotron" 或 "parakeet"
    def transcribe(self, buf, tag) -> tuple[str, str]:
        if self._streaming is not None:
            try:
                t0 = time.monotonic(); text = self._streaming.update(buf, tag)
                dt = time.monotonic() - t0
                self._strikes = self._strikes + 1 if dt > SLOW_S else 0
                if self._strikes >= SLOW_STRIKES:
                    self._trip(f"太慢：连续 {SLOW_STRIKES} 次 > {SLOW_S}s")
                return text, "nemotron"          # 这一份照常用（已经算出来了）
            except Exception as e:               # noqa: BLE001
                self._trip(f"出错：{e!r}")        # 落到下面的 Parakeet，**这一份不丢**
        return self._parakeet.transcribe(buf[-self._window:]), "parakeet"
    def _trip(self, why): self._streaming = None; self.fallback_reason = why

def load_draft_engine(parakeet, *, window_samples, env=os.environ, ram_bytes=_UNSET) -> DraftEngine:
    """**永不抛**。模型目录取 `models.path_of("nemotron")`（ENV_MODEL_DIR 可覆盖）；
    「在不在」用 doctor 同一判据（目录非空）——别在这里再写一份；
    内存用 `os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")`（失败 → None）；
    choose() 说 nemotron 就 try 构造 NemotronDrafter，任何异常 → Parakeet，reason 写清楚。"""
```

### 4.2 `models.py`

```python
NEMOTRON_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                "sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25.tar.bz2")
NEMOTRON = Model(
    "nemotron", f"{MODELS_ROOT}/sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25",
    "流式草稿模型 Nemotron(可选)", False, _fetch_cmd("nemotron"),
    464.0, src=NEMOTRON_URL, extra="（解开后约 660 MB）")
MODELS = (PARAKEET, VAD, WHISPER, QWEN, NEMOTRON)
```
- ⚠️ 目录名必须**等于 tar 包里的顶层目录名**（`fetch_whisper` 是「整体挪进 models_root」）。
- P0 若选 1120 ms：换 URL / 目录名 / sha 三处（都在这里和 `fetch_model.PINS`），别处不许写死。

### 4.3 `fetch_model.py`

- `fetch_whisper(models_root, url, *, say)` → 加参数 `pin: str = PINS["whisper"]`，内部用 `pin`；
  **保持原签名可用**（`tests/test_fetch_model.py` 在调它）。
- `PINS["nemotron"] = "78e2b79fcf7271553a74402a76b771b09ea40117a39566a79f52235b23db6358"`
  （GitHub release API 的 `digest` 字段，2026-10-09 取；1120 ms 版是 `840c48deed02d4a5975716e7b12dc0a8b1ba620776c6366f7e5677d8907edd73`）。
- `KEYS` 加 `"nemotron"`；`fetch()` 加分支：`return fetch_whisper(path.parent, models.NEMOTRON_URL, pin=PINS["nemotron"], say=say)`。
- 文件头用法行 `<vad | whisper | parakeet | llm>` 补上 `nemotron`（`main()` 打印的是它）。

### 4.4 `update.py`（顺手修一个现成的错）

`:934` 的 `missing` 现在包含**可选**模型 → 卡片会写「下载模型（1 个）… 缺模型的话课上会直接起不来」，
而 `run_step` 那边（`:1047` `if not m.required: continue`）**根本不下可选模型**。今天 Qwen 缺失时就已经这样；
加了 Nemotron 后**每个用户**都会看到这条假警告。改成 `if m.required and not doctor.model_present(m.path)`，
并在 `tests/test_update.py` 加一条：注册表里有可选且缺失的模型时，卡片不出「下载模型」。
⚠️ `update._models_changed()` 会因为 `models.py` 变了在下次 `cl update` 时提示一次 —— 预期行为，不用改。

### 4.5 `main.py`（只接线）

**a. `_Latest`** —— 与上一轮加 `taken_t` 同一个模式：
```python
def put(self, buf, tag=None) -> None:
    with self._lock:
        self._buf = buf; self._t = time.monotonic(); self._tag = tag
    self._ev.set()
# take() 里与 taken_t 同一把锁：self.taken_tag = self._tag
```
**b.** 紧跟 `asr_final = load_final_asr(...)` 那个 `try` 块**之后**（不要放进去 —— 它失败不该 `return`）：
```python
draft = draft_engine.load_draft_engine(asr, window_samples=PARTIAL_MAX_S * SR)
echo(f"✍️ 草稿引擎：{draft.name}" + (f"（{draft.reason}）" if draft.reason else ""))
```
并在 `live = livemetrics.open_for(...)` 之后：`if live is not None: live.event("draft_engine", name=draft.name, reason=draft.reason)`。

**c.** `on_partial=lambda buf: latest.put(buf, seg_gen["n"]),`

**d.** `partial_worker` 里：
```python
_tag = latest.taken_tag                         # 紧跟 _t_put = latest.taken_t
...
t, _eng = draft.transcribe(buf, _tag)           # 替换 asr.transcribe(buf[-PARTIAL_MAX_S * SR:])
if _eng != _last_eng:                           # _last_eng 初值 = draft.name（循环外）
    if _last_eng is not None and draft.fallback_reason:
        echo(f"⚠ 流式草稿已停用，本节课改回 Parakeet：{draft.fallback_reason}")
        if live is not None:
            live.event("draft_engine_fallback", reason=draft.fallback_reason)
    _last_eng = _eng
...
drafts.put((t, _shown, len(buf) / SR, _dt_draft, _tag if _tag is not None else seg_gen["n"], _t_put, _eng))
```
- ⚠️ **`seg` 改用 `_tag`** 是**有意的**：它是 put 那一刻的段号（真身份），比 worker 里读 `seg_gen["n"]` 准。
  在 `RESEARCH-stream-eval.md` 记一句：分组从这一版起更准，旧文件不受影响。
- ⚠️ `if not finalq.empty(): continue` **保留**：流式草稿跳过一轮不丢样本（下一份 `buf` 里有）。
- ⚠️ 冻结（`stab`）与触发策略原样保留，它们吃的是 `t`。

**e.** `_pair`：返回 7 项，`except` 分支的默认值同步成 7 项（**这里漏改 = drain 打死主循环**）；
`drain()` 里 `live.draft(..., engine=_eng)`；`live.close(...)` 加 `draft_engine=draft.name, draft_fallback=draft.fallback_reason or ""`。

### 4.6 量具：滑窗剔除只对窗口型引擎

`stream_eval.evaluate` 里两处 `d["buf_s"] > PARTIAL_MAX_S` 的判据换成一个函数：
```python
def _slid(d) -> bool:
    """草稿窗口滑了没有。只有**窗口型**（parakeet：只看最近 10 s）会滑；流式引擎看整段，不滑。"""
    return d.get("engine", "parakeet") == "parakeet" and d["buf_s"] > PARTIAL_MAX_S
```
`class_report.build_utts` 把 `rec.get("engine")` 带进 draft dict（缺省不写 → 视为 parakeet，旧文件数字逐位不变）。
⚠️ 不改的后果：Nemotron 的长段草稿会被当成「滑窗」**整批剔除**，回改率会被**低估**（看起来更好）。

### 4.7 `cl`：App 用户的开关

紧跟 `.test-mode` 那段（`TMCFG=".test-mode"` 下面的读取逻辑），照它的写法：
```bash
DECFG=".draft-engine"     # 内容：auto | parakeet | nemotron；不存在 = 不设（默认 auto）
_de="$(cat "$DECFG" 2>/dev/null || true)"
case "$_de" in auto|parakeet|nemotron) export CLASSLIVE_DRAFT_ENGINE="$_de" ;; esac
```
⚠️ 已经设了环境变量时**不覆盖**（命令行优先）：外面包一层 `if [ -z "${CLASSLIVE_DRAFT_ENGINE:-}" ]`。
⚠️ 注意 §8 的 `$VAR` 紧跟中文问题。

## 5. 失败模式 → 程序怎么表现

**总原则：任何一条都不许让课停下；凡是回退都要「出声一次 + 记一笔」。**

| # | 失败 | 落点 | 表现 | 判据 |
|---|---|---|---|---|
| F1 | 模型没下载 | `choose` | Parakeet；启动行写「模型未下载」 | 单测 |
| F2 | 内存 < 12 GB（auto） | `choose` | Parakeet；写「内存不足」 | 单测 |
| F3 | sherpa-onnx 太旧 / 文件损坏 / 缺 tokens.txt | `load_draft_engine` 构造 | Parakeet；reason 带异常 | 单测（假模块抛）+ e2e（`CLASSLIVE_DRAFT_MODEL_DIR=/不存在`） |
| F4 | 上课中途 `update` 抛 | `DraftEngine.transcribe` | **同一次**改走 Parakeet（这份草稿不丢）；echo 一次；`draft_engine_fallback` 事件；本节不再试 | 单测 |
| F5 | 变慢（连续 3 次 > 0.9 s） | 同上 | 这份照用，之后 Parakeet | 单测（假时钟） |
| F6 | 段边界判错 | `NemotronDrafter.update` | tag 变 → 新流；`buf` 变短 → 新流 | 单测 |
| F7 | 静音段输出空串 | `partial_worker` 现有 `if not t: continue` | 不上屏 | 已有 |
| F8 | 内存被系统杀进程 | 无法捕获 | 靠 F2 的内存门预防；e2e 报告里看内存峰值 | e2e |
| F9 | Nemotron 抢 CPU 拖慢定稿 | — | 2 线程上限；**验收看「段结束→定稿上屏」** | §6.3 |
| F10 | `drafts` 元组形状改了漏改 `_pair` | `drain` | 主循环死 | **只有真跑抓得到** → §6.3 必跑 |
| F11 | 下载中断 / sha 不符 | `fetch_model` | 现有逻辑：临时目录、不留半截 | `tests/test_fetch_model.py` |

## 6. 验收

### 6.1 P0：评估脚本（先做，决定 chunk）

把 scratchpad 里验证过的原型落成 `scripts/draft_engine_eval.py`：用生产的 `Segmenter`（虚拟时钟，
同 `stream_eval.replay`）+ `NemotronDrafter`（从 `draft_engine` import，**同一份实现**），
输出：段数、草稿数、回改率（`stream_metrics.rewrite_stats`）、每次更新耗时 p50/p95、RTF、
段尾全文；`--ref` 时给 WER。跑：
- 真课：`sessions/2026-10-09_120723_ECON10770.001.opus` 前 240 s（**只读**，ffmpeg 解到内存）
- TTS：`~/.classlive/eval/econ_daniel.wav`、`econ_samantha.wav` + `scripts/eval_corpus/econ_lecture.txt`
- 560 ms 与 1120 ms 各一遍。**选 1120 的条件**：WER 明显更低（≥ 1 个百分点）且回改率仍 ≤ 2%；否则 560。
⚠️ 回放有偏（`HANDOFF-20261009 §3.2`：回放把回改率高估 55%）→ **只拿来比两个 chunk，不拿来定"要不要上"**。

### 6.2 单测 `tests/test_draft_engine.py`（全离线、毫秒级、不需要模型）

用**假识别器**（实现 `create_stream / is_ready / decode_stream / get_result`，假 stream 记下被喂的样本）。必须有：
1. `choose` 的全表（3 种 mode × 模型在/不在 × 内存够/不够/未知；非法 mode 当 auto）
2. **只喂增量**：同一 tag 连续 3 次 `update`（buf 逐次变长）→ 假 stream 收到的样本拼起来**逐字等于**最后的 buf
3. tag 变 → 新 stream；buf 变短（同 tag）→ 新 stream
4. 假识别器第 2 次抛 → 返回 `(parakeet 的结果, "parakeet")`，`name` 变 parakeet，第 3 次**不再碰**流式
5. 假时钟让 3 次都超 `SLOW_S` → 第 3 次之后熔断；中间夹一次快的 → 计数清零
6. `load_draft_engine` 在「模块 import 失败 / 构造抛」时返回 Parakeet 引擎**而不是抛**
7. ⚠️ 每条写完**改坏实现看它会不会红**（这个仓库被抓出过一批恒真断言，见 `CLAUDE.md` 面板那条）

另：`tests/test_livemetrics.py` 加 `engine` 字段那条；`tests/test_class_report.py` 加「`engine=nemotron` 的长段草稿不被剔除」与「无 engine 字段的旧文件数字不变」两条；`tests/test_update.py` 加 §4.4 那条。

### 6.3 必跑的端到端（默认闸门够不到 `partial_worker`）

**隔离写端**（读端写端一起换 —— `HANDOFF §6.7`；`sessions` 是软链时先确认没别的 ClassLive 进程在跑：`ps -axo command | grep main.py`）：

⚠️ 会话目录**写死**在 `obsidian_writer.SESSIONS = Path(__file__).with_name("sessions")`，没有参数能改。
所以 **P1 在新分支的 worktree 里做**（作者的规矩：先分支、不直接动 main、不 push）：
`git worktree add ../lecture-live-stream -b stream-asr && cd ../lecture-live-stream && ./scripts/link-runtime.sh`
（它给 worktree 补运行态软链，`sessions` 指向主目录那份）→ 跑 e2e 前把 `sessions` 软链**临时**指到 `$S/sessions`，跑完指回：

```sh
S=$(mktemp -d)/e2e && mkdir -p $S/sessions $S/vault
ln -sfn $S/sessions sessions && readlink sessions        # 确认指到了临时目录
for E in parakeet nemotron; do
  CLASSLIVE_DRAFT_ENGINE=$E CLASSLIVE_UPLOAD=0 CLASSLIVE_NO_AUTO_UPDATE=1 \
  ClassLive.app/Contents/MacOS/python main.py --source file --path ~/.classlive/eval/econ_daniel.wav \
     --ui terminal --save-notes yes --vault $S/vault --test-mode --record-audio </dev/null > $S/$E.log 2>&1
done
# 再跑一次故障注入：
CLASSLIVE_DRAFT_ENGINE=nemotron CLASSLIVE_DRAFT_MODEL_DIR=/nonexistent … （同上）
ln -sfn ~/lecture-live/sessions sessions && readlink sessions   # ⚠️ 指回去
```
（macOS 没有 `timeout` 命令，别包它 —— 上一轮就因此退出码 127 白跑一次。音频 2 分钟，实时回放。）
通过条件（**全部**）：
- 三次都退出码 0、日志无 `Traceback`；故障那次启动行写明回退原因、草稿照常出
- `nemotron` 那次：`.live.jsonl` 有 `draft_engine` 事件且 `name=nemotron`；draft 行带 `engine`
- `class_report`：nemotron 回改率 ≤ 3%；**「段结束→定稿上屏」p50 不比 parakeet 那次差 15% 以上**（F9）
- `summary` 行 `dropped: 0`；`report.json` 的内存峰值比 parakeet 那次多 ≤ 1.3 GB
- 默认闸门 `tests/test_audit_regressions.py` 116 条 + 全部 `tests/test_*.py` 退出码 0；`uvx ruff@0.6.9 check <改过的文件>` 干净

### 6.4 真课闸门（P1 → P2）

装模型（`ClassLive.app/Contents/MacOS/python fetch_model.py nemotron`），App 里开测试模式上一节课，
`class_report` 读那份 `.live.jsonl`：回改率 ≤ 5%、无 `draft_engine_fallback`、定稿延迟不比
10-09 那节（p50 3.8 s / p95 5.0 s）差 15% 以上。**拿数字给作者看，作者点头再进 P2。**

## 7. P2 / P3（只给方向）

- **P2 断句**：真课 158 次断句里 132 次是 12 s 硬切（`HANDOFF §3.4`）。流式结果带每个 token 的时间戳
  （`rec.get_result_all(stream)` 的 `.tokens` / `.timestamps`，1.13.8 实测存在，时间戳单位秒）→ 撞 12 s 时改在「最后一个句末标点 token 的时刻」切，
  而不是硬切。动的是 `vad.Segmenter` 的切点（**不改 `on_partial`/`on_utterance_end` 的契约**）。
  量：硬切占比、`no_end_punct_pct`、草稿→定稿。
- **P3 定稿**：TTS 上流式 WER 1.6%/1.3% ≈ Whisper 5.4%/1.3%，但 Voxtral 论文说它长音频明显差。
  **先做人工参考稿**（真课 5 分钟以上）再谈；要是能去掉 Whisper，CPU 与「段结束→定稿」都会大降。

## 8. 雷区（这个仓库付过学费的）

1. **嵌套闭包够不到** → 新逻辑进 `draft_engine.py`；改了 `drafts` 元组 / `_pair` / `drain` **必须真跑 §6.3**。
2. **`_pair` 的 `except` 默认值要和正常分支同形**，否则形状一错整条 drain 停。
3. **拆包放在哨兵判断之后**（`final_worker` 那次：`buf, _fseg = buf` 提前 → 线程当场死）。本计划不碰 `final_worker`，但如果你碰了，照这条。
4. **测试要同时隔离读端和写端**；`sessions/` 只追加、绝不删；`rm` 碰 `sessions` 路径会被作者的 hook 拦。
5. **shell 里 `$VAR` 紧跟中文要写 `${VAR}`**（`cl` 那段新代码）。
6. **sha 钉在 `fetch_model.PINS`，目录名钉在 `models.py`**，别在第三处写。
7. **判据先问「改坏实现它会不会红」**；`assert` 别用（`-O` 会剥掉），用 unittest 的 `assertX`。
8. **改代码前先加载 skill**（`codebase-design` → 写 `draft_engine.py` 的接口；`verification-before-completion` → 收尾）。
9. **默认闸门用 App 自带的 python 跑**（`~/.venvs/cl-dev` 没 AppKit，会有 22 条假失败）。
10. **别 push、别发版**：作者要先看真课数字；发版默认 manual、CHANGELOG 文案先给作者看。

## 9. 复现本文数字

都在作者本机跑过（2026-10-09，App 里的 python + sherpa-onnx 1.13.8）：
- 模型：`curl -L -o m.tar.bz2 <§4.2 的 URL> && tar xjf m.tar.bz2`（放 scratchpad，**别放 `~/models`** 直到 P1 接好）
- 「按本计划的接法」那组（0.5% / 146 ms / +1011 MB）：`Segmenter` 虚拟时钟 + 每段一条流 + 只喂增量，
  喂 10-09 真课 `.001.opus` 前 240 s（P0 的 `scripts/draft_engine_eval.py` 就是它的正式版）
- TTS WER：整段流式（endpoint 开、20 s 强制断）对 `scripts/eval_corpus/econ_lecture.txt`
- VPS：`ssh -o Port=22 bldcam 'nproc; lscpu; free -h'`（2222 端口当天超时）
