# 无句号时按语意封口（2026-10-29）

**问题**：Soniox 标点崩塌（10-28 CHE002A 开课 ~50 s 后整节课没有逗号句号）→ 封口只认句末标点 → 字幕堆几分钟、翻译 / 笔记 / 章节时间戳全晚（最长 252 s）。
**做法**：`semantic_seal.py`（文件头有取舍）+ `StreamCore.seal_through_word`。不按秒数、不看 VAD 停顿、不上本地句界模型。

## 数据依据（离线，2026-10-29；脚本与结果在作者 `~/Desktop/classlive-review/20261028/indep/`）
- 崩塌由音频决定且可复现：新会话重放 0–600 s，封口时刻与原会话逐点吻合；同窗口重复跑完全一致。
- 重建会话**有时**恢复（真 252 s 洞段：原会话 11 句 final / 2 句句号，新会话 35 句全句号），连珠炮段也会再崩 → 重建不是保证，只能做影子模式（本计划 P2，未做）。
- VAD 停顿对句界无区分力（与随机候选几乎无差）；Soniox `<end>` 精度高但召回随场次变。
- 句界检测器（以健康段 Soniox 句号为真值，±2 词容差，去标点后）：规则 F1 0.19–0.32；SaT-1l（400 MB ONNX，4 ms）0.59 / 0.45；LLM（deepseek-flash，~1 s）0.65 / 0.49。
- 回放 10-28 全程草稿（离线模拟）：LLM 路径封 395 次、词数 p50 11 / p90 20 / 最大 35、**最老未封词龄 p50 7.4 / p90 11.8 / 最大 17.8 s**（现状最长 252 s）。
- 门槛依据：健康课里 25 词内无标点的句子只占 0–1.2%（ECS170 498 句、10-26 89 句）。

## 已实现
- `stream_asr.StreamCore.unsealed_words / seal_through_word`、`StreamASR` 同名包装（`record_lat=False`：语意封口不进 `seal_latency_s`）。
- `semantic_seal.py`：`choose_cut` / `cap_cut` / `SemanticSealer` / `llm_decide`。
- `main.py` 接线（`CLASSLIVE_SEMANTIC_SEAL`，默认开）、`live.jsonl` 事件 `semantic_seal`、测试报告块 `semantic_seal`。
- `tests/test_semantic_seal.py`（21 条；两个变异已验证会红）。

## 端到端实测（真 `StreamASR` + `SemanticSealer` + 真 LLM，重放 10-28 音频前 600 s，2026-10-29）
- 封口句数 12 → **108**；最大封口间隔 124 s → **26 s**（p90 12 s）；LLM 219 次、0 错误、1 次 stale、0 次硬切。
- 封口词数 1310 → 1393（没丢词，文字相似度 0.957），近距 6-gram 重复 0（没有重复封）。
- 复现：`~/Desktop/classlive-review/20261028/indep/rep3.py`（花 Soniox 额度，LLM 用 test key）。

## 还没做 / 未验证
- 真课验证（离线模拟 ≠ 真链路）：误切率（一个意群被拆到两行）没有人工标注数。
- 课堂 UI 实机观感；翻译对被切开的句子的影响。
- P2：标点崩塌检测 + 重建会话（影子模式）。
- 本地小模型（SaT 400 MB）不进产品；若以后要离线兜底再议。
