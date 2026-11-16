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
