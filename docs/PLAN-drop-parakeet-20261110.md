# 计划：去掉 Parakeet，把流式模型（Nemotron）当成必需 + 断网定稿

2026-11-10 · Owen 在课上提出，**未实施**（课上不动运行路径）

## 结论先说

「删掉 Parakeet」不是删一个模型目录那么简单：它现在同时占着**两个位置**。

| 位置 | 现在的样子 | Parakeet 没了会怎样 |
|---|---|---|
| 草稿兜底 | `draft_engine.choose()`：Nemotron 缺/内存不足/熔断 → Parakeet | 没有草稿可退（可接受：就少几条实时草稿） |
| **本地定稿兜底** | `final_asr.load_final(..., draft_asr)` → `FinalASR._local_chain()` 调 `ParakeetASR.transcribe_lp()` | 云端失败/断网时**整段没有定稿**（不可接受） |
| 启动自检 | `main.load_asr()` 要求 Parakeet 文件在 | **直接起不来**（就是 11-10 这次事故的形态） |

## 目标形态（按 Owen 的方向）

1. `Nemotron` → `required=True`（`models.NEMOTRON`）。
2. `Parakeet` → 不再是必需：`required=False`，可从 `~/models/` 删除。
3. 断网时的本地定稿 = **流式模型对整段重新解码**（不是拿直播草稿凑）：
   新建 `NemotronFinal`（或给 `NemotronDrafter` 加一个只读 `transcribe_lp` 适配器），
   每段新开一条 stream → feed 整段 + `TAIL_PAD_S` 静音 → `input_finished` → 取结果。
4. 草稿引擎在 Nemotron 熔断/缺失时：**不退回 Parakeet**，直接「本段无草稿」；
   定稿不受影响（它不走草稿引擎）。

## 要动的文件

- `models.py`：`NEMOTRON.required=True`、`PARAKEET.required=False`；
  `REQUIRED_FILES` 保留 parakeet（仍在时校验），加 nemotron（已有）。
- `main.py`：启动时 Parakeet 缺失不再 `return`；按「Parakeet 在 → 旧行为，
  不在 → NemotronFinal」装配 `load_asr` / `load_final`。
- `final_asr.load_final()`：`local` 为空时用 NemotronFinal（现在会退成 `draft`）。
- `draft_engine.py`：`choose()` 在没有 Parakeet 时不返回 `parakeet`；
  `DraftEngine` 熔断后退到「无草稿」而不是 Parakeet。
- `doctor.py`：Parakeet 从 ❌ 硬缺失降为 ⚪ 可选；Nemotron 升为必需。
- 测试：`test_draft_engine`（缺 parakeet 路径）、`test_final_asr`（NemotronFinal
  当 local）、`test_doctor_model`、`test_update`（required 集合变化）。

## 验证（课后做，不在课上跑）

1. `check.py --changed` 全绿。
2. 离线量具：拿 `scripts/stream_eval.py` 的语料，对比
   「Parakeet 定稿」vs「Nemotron 整段解码」的 WER / 漏句 —— **这是概率性链路，
   必须先量再切**（项目硬规矩）。若 Nemotron 明显更差，先别删 Parakeet。
3. 真课一次 `--engine local` 或 `CLASSLIVE_CLOUD_ASR=0` 回放，确认断网定稿有字。

## 现状（2026-11-10 15:xx）

- Parakeet encoder 已重下、可跑；Nemotron 的 encoder 仍在 `~/.Trash`
  （`encoder.int8.onnx 12.34.16`）。**没有删任何模型文件**：正在上课，
  删 Parakeet 可能让课上草稿兜底失败。
- 本计划未改任何运行代码。

---

## 2026-11-20 实施（桌面 D1，比原计划更彻底）

⚠️ **Owen 当天改口一次，最终口径以「第二步」为准**：

1. 早上拍板「Nemotron 升为**必需**」；
2. 下午改口 —— **Nemotron 仍是可选**（没装也能正常上课，全走云端）；装了才有
   「断网 / 云端识别失败时仍有英文转录」。必下的只剩 **VAD**。

所以原计划里那几条「`NEMOTRON.required=True`」**没有执行**。

### 删掉了什么

| 东西 | 处置 |
|---|---|
| `asr.ParakeetASR` / `LazyASR` / `WhisperASR` / `load_asr` / `load_final_asr` | 删。`asr.py` 现在只剩 `_find_file` 与 `is_degenerate` 两个共用件 |
| `models.PARAKEET` / `WHISPER` / `QWEN` | 删。`MODELS = (VAD, NEMOTRON)`，`REQUIRED_FILES` 只剩 `nemotron` |
| `fetch_model.py` 的 `parakeet` / `whisper` / `llm` 三个 key 与 `fetch_hf` 那一整条 HF 路径 | 删。`KEYS = ("vad", "nemotron")` |
| `translator.Translator` / `load_translator` / `_configure_mlx` / `local_model_present` / `clean_local_zh` / `zh_echoes_previous` / `DRAFT_SYSTEM` / `FIX_SYSTEM` / `_apply_chat_generic` | 删。`translator.py` 只剩纯函数（`Result` / `_StreamParser` / `_clean_fix` / `guard_zh_result` + 术语那一组），因为 `cloud_translator` 与判据还在 import 它们 |
| `whisper_card.py` + `tests/test_whisper_card.py` + `entry_panel` 里那张卡的接线 | 删（整个功能就是「清掉旧 Whisper」） |
| `paths.whisper_card()` | 删 |
| `ready.engine_item(local_state=)` / `offer_local` / `_local_path()` | 删（没有本地翻译引擎可下，别给按了没反应的按钮） |
| `draft_engine.MIN_RAM_BYTES` / 内存门 / `parakeet` 分支 / `window_samples` | 删。`choose()` 现在只答 `nemotron` / `none`；`CLASSLIVE_DRAFT_ENGINE` 只留 `auto` / `off` |
| `main.EngineRouter` 的 `_local` / `_local_usable` / `--engine local` | 删。连败照旧计数与告警，但提示语改成「翻译暂时失败，字幕照常（英文）」，不再提「已降级本地引擎」 |
| `tests/test_lazy_asr.py`、`tests/test_local_postprocess.py` | 删（被测对象没了） |

### 加了什么

- **`nemotron_final.py`** —— `NemotronFinal`：流式模型拿**整段**重新解码当定稿兜底
  （喂整段 + `TAIL_PAD_S` 静音 + `input_finished`）。**懒加载**：只有云端定稿失败才建，
  且它有一条**自己的** `OnlineRecognizer`（不与草稿线程共用 —— 并发 decode 的安全性
  没有文档保证，见它的模块头）。
  理由与实测：`TAIL_PAD_S` 不补会吞掉段尾的词（WER 16.2% → 10.3%，
  `docs/PLAN-streaming-asr.md` §0.1）。
- **`scripts/nemotron_final_eval.py`** —— 切换前的量具。**量了才切**（项目硬规矩）：

  | 引擎 | 段数 | WER | 空段率 | p50 / p95 |
  |---|---|---|---|---|
  | Parakeet 整段（旧） | 322 | **14.3%** | 1.2% | 371 / 456 ms |
  | Nemotron 整段（新） | 322 | **14.7%** | 2.5% | 1262 / 1652 ms |

  相对差 **+2.5%**，远在「差 >30% 就不切」的门内。语料 `~/.classlive/eval/ocw`（OCW 322 段
  + 人工字幕），只本地跑、不调云。⚠️ 慢 3.4 倍 —— 但它只在**云端定稿失败**时才跑，
  那时候慢一点也比没字强。
- **`tests/test_nemotron_final.py`** —— 懒加载 / 补尾 / 每段新开流 / 同实例并发不串 /
  两个实例不共享 / 缺文件在构造时报 / 加载失败原样抛。
- **`update.py` 的推荐语气**：`DRAFT_MODEL` 那一步的文案改成
  「推荐下载：识别模型（464 MB）」+「断网时也能有英文转录」；判据只剩
  `draft_engine.want_model()`（= 开关不是 `off`），**内存门已删**。
- **`fetch_model.VPS_MIRROR`**：GitHub 资产在第三方前缀代理之后多兜一站
  `https://git.bldcam.page/models/<原名>`，`mirror=True` → 强制过 `PINS` 的 sha256。
- **`doctor.py` 的旧模型提示**：`~/models/` 里还有 `parakeet-tdt-0.6b-v3-int8` /
  `sherpa-onnx-whisper-turbo` 时多打一行「旧模型不再使用 —— 可手动删，约 X GB」。
  ⚠️ **只提示、不删**（不删用户数据是硬规矩）。

### 升级路径（老用户）

`models.py` 动过 → `_models_changed` 为真 → 面板 / 日志会说「模型清单有变化」，
且 `_quiet_ok` 返回 `False`（退回「更新」那一格让用户点，不在背后换模型）。
`run_step("models")` 只下**必下**的 → 现在只剩 VAD，**不会再下 Parakeet**。
`tests/test_update.py` 的 B16 组钉着这三条。

### 没做的

- `docs/HANDBOOK.md` §6.1 那张开关表没更新（`CLASSLIVE_DRAFT_ENGINE` 的取值从
  `auto|parakeet|nemotron` 变成 `auto|off`）—— 不在这次允许改的清单里。
- `~/.classlive/eval/` 里那些**历史报告**留着（它们记的是当年的 Parakeet/Whisper 数字，
  是历史数据，不是现役值）。
