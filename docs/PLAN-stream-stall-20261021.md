# PLAN · Soniox 卡死与持续滞后（2026-10-21）

> 事故：2026-10-20 ECON10740（15:05–16:45）Soniox 静默 **2m41s**，恢复后整节课稳定慢 **8.6s**。
> 本文记录：代码复核 → 行业/Soniox 官方口径 → 修复设计 → 判据与验收。
> 当前状态：**P0 已实现，未推送**（分支 `fix/stream-stall-20261021`）。

---

## 1. 事故时间线（音频复听对过）

| 时刻 | 事件 |
|---|---|
| 15:07:12 | “half an hour's job” 音频 ~15:07:05 → 差 ~1s（正常） |
| 15:09:30 | 系统 `system_sleep` 7s；采集缺 11.9s 音频 |
| 15:09:23–15:12:04 | **无 final、无 draft**（161s） |
| 15:12:04 | 恢复，吐积压；`seg_to_zh` 101s → 25s |
| 15:15 左右 | 降到 ~4s，但没有回 1s |
| 之后 | 稳定 **8.6s**（p90 8.8）；15:20 那句差 ~19s，16:31 那句差 ~10s |
| 16:31:42 | 音频结束（之后是休眠 + 麦克风测试句） |

上午 ECON10770 正常：封口 p50 0.8s、翻译首字 412ms、0 失败。

---

## 2. 代码复核（确认是真问题，不是量具误报）

### 2.1 判死条件

`stream_asr.py:918`（修复前）：

```python
if now - max(got, last_audio, last_send) > self._response_timeout_s:
```

- `last_audio` 在每次从音频队列取到一块时刷新（`:937`）；
- `last_send` 在每次发音频/keepalive 时刷新（`:935`/`:949`）；
- 所以**只要还在推音频或发 keepalive，`max(...)` 恒 ≈ now** → 15s 判死永不触发。

用仓库自带 `FakeSoniox` 复现（2026-10-21）：

| 场景 | 结果 |
|---|---|
| 持续喂音频 3s、服务器一声不吭（`response_timeout=1s`） | `reason() == ''`、`alive() == True` ❌ |
| 停止喂音频 3s | `reason() == 'timeout'` ✅（旧判据只覆盖这一种） |

现有 `tests/test_stream_asr.py::t_transport_stall_times_out` 只喂 0.6s 就停，然后等超时
—— 它测的是「停止喂之后的静音」，**从没测过「一直在喂但服务端不响」**。

### 2.2 为什么没有「跳到实时」

积压从 101s 降到 ~8.6s 后停住：发送端按实时速率喂，剩余积压排不干净。
`stream_asr.py` 没有读取 Soniox 的进度字段，也没有「落后就重连」的逻辑。

### 2.3 量具

- `seg_to_zh_ms` 只有 179/1191 句有值：`seg_ended_at` 只在本地 VAD 段尾写，Soniox 段号会跳；
  这 179 条的起点还包含整段音频时长，p50 15.2s 不是纯等待。
- 新增 `sentence_wall_t0()`：用句尾音频 + 首块音频墙钟，给 Soniox 句一个可靠起点。

---

## 3. Soniox 官方口径（2026-10-21 回源）

来源：`soniox.com/docs/api-reference/stt/websocket-api`、`/stt/rt/connection-keepalive`、
`/stt/rt/real-time-transcription`、`/stt/rt/manual-finalization`、`/docs/faq`、`/docs/data-residency`。

- **keepalive**：不推音频时至少每 **20s** 一条 `{"type":"keepalive"}`；20s 内没有音频/keepalive
  → **408 request_timeout** 并关闭。现有 5s 间隔安全。
- **提前建连**：start request 之前可以提前建连并持有，每 **40s** 一条 keepalive；没有 deadline，
  但占连接数。**没有 warmup API / 模型预热**。
- **预热要不要做**：不需要。FAQ 的建议是「建连前先在本地缓冲音频，建连后立刻推缓冲」——
  本项目已经在做（`_audio` 队列 + 建连中也 `feed`）。提前建连只省 TLS/WS 握手（EU 实测
  connect 171ms / RTT 120ms），不省模型处理；**不把它当修复手段**。
- **进度字段**：每条响应都带 `final_audio_proc_ms` / `total_audio_proc_ms`；
  官方文档明确叫 “Audio progress tracking”。这是判断「服务端落后多少」的官方口径。
- **manual finalize**：`{"type":"finalize"}` → `<fin>`；本次未用（收尾仍用空文本帧）。
- **单流上限**：300 分钟；超了 413。110 分钟课没问题。
- **数据驻留**：EU 用 `api.eu.soniox.com` / `stt-rt.eu.soniox.com`。

### 行业对照

- Deepgram：音频最多 1.25× 实时；**缓冲大量音频重放会让流显著滞后**；断线要新建连接；
  keepalive 3–5s（10s 超时）。→ 不能把 100s 积压当「续传」。
- Smallest.ai（2026-04/10 生产实践）：**gap > 2s 应当作 session termination，重新开 session，
  不要在同一 session 里继续**；否则上下文/时间戳都会坏。→ 支持「重连 + 跳实时」。
- 结论：**用官方 `total_audio_proc_ms` 做 backlog 判据 + 超阈值重连跳到实时**，比通用
  「定时 ping」更贴近 Soniox；方向正确。

---

## 4. 修复设计（P0，已实现）

### 4.1 判死：只看服务端消息

```python
if now - got > self._response_timeout_s:
    self._pending = "timeout"
    self._skip_next = True
    return "timeout"
```

- `got` = 最近一条服务端消息（msg / end / err）到达时刻。
- 实测 Soniox 在静音/只发 keepalive 时每 ~1–1.5s 回一条空进度消息（35s 实测 23 条），
  所以 15s 阈值不会误杀安静课堂。

### 4.2 落后：官方进度字段

- 解析 `total_audio_proc_ms`（会话相对）。
- 记录收到消息那一刻已发送的样本数 `_proc_sent_ms`；`lag = _proc_sent_ms - total_audio_proc_ms`。
  **不拿现在的 `_sent_samples` 去减旧消息**（消息间隔 1.5s 会被算成落后）。
- `lag > 3s` 持续 `3s`，且期间**来了新的进度消息仍落后** → `stale`，重连并
  `_skip_next=True`（3s 给普通断线重连后的积压一点追平时间；实测服务端 1–2.5× 实时，
  3s 内追平就不折腾）。
- 守卫 `total > 0`：开流/大 context 就绪中 `total=0` 是合法的，不能误判。
- **两个量都按会话相对算**（OCR 2026-10-21 抓到的边界：绝对量在重连后 `_sess_off_ms>0`
  会让 `total=0` 骗过守卫）。
- **`total=0` 卡死兜底**：消息在来、但 `total_audio_proc_ms` 持续 0 达 15s → `stale`
  （否则 `got` 被消息刷新、落后判据又被 `>0` 跳过，两端都判不出来）。
- **hold 必须等新读数**：`_proc_seq` 记录进度消息序号；只有起计后收到新消息、仍落后，
  才允许判 stale（OCR 第 3 条：不能拿一条冻结读数等时间过去）。

### 4.3 跳实时

- `timeout` / `stale` → `_skip_next=True`。
- 新 `_session` 连接成功后：
  1. 把 `_audio` 队列里积压的块全部丢掉，计入 `skipped_s`；
  2. `_core.flush()` 把旧会话挂着的半句封出去（不跟新会话的词混）；
  3. 从当前实时位置继续推流。
- 普通 `disconnected` **先续传**：10s 队列内的短缺口按原样续传（不丢内容）。
  如果重连本身较慢、续传后仍 `stale`，允许再跳一次（第三次连接）；第三次还失败才退回本地
  —— 不让一次慢重连把整节课降级成本地识别。

### 4.4 量具

- `metrics()` 新增：`reconnects` / `skipped_s` / `backlog_max_s` / `msg_gap_max_s` /
  `dropped_chunks` / `dropped_s`。
- `sentence_wall_t0(end_s)` 修正 Soniox 模式的 `seg_to_zh` 起点。
- `main._emit(..., seg_t0=...)` 把该起点传给 `tr_latency.done()`；本地路径行为不变。

### 4.5 不改的东西

- 不改 `StreamCore` 的封口判据（P_stable 0.3s）。
- 不改本地 VAD / 本地定稿 / 草稿路径。
- 不改 keepalive 5s、不改队列 10s 上限。
- 不改 `main` 的收尾流程、上传、笔记。

---

## 5. 判据（新增/修改）

| 判据 | 说明 |
|---|---|
| `t_transport_stall_with_continuous_audio_times_out` | **持续喂 1.2s**（>0.5s 超时）必须判死；修复前红、修复后绿 |
| `t_transport_progress_messages_keep_alive` | 静音期有进度消息时不许判死 |
| `t_transport_backlog_reconnects_to_live` | 落后 1.5s / 门槛 0.5s → 重连，`reconnects>=1`；第二条新读数仍落后才动作 |
| `t_transport_backlog_caught_up_no_stale` | 落后读数后服务端追平 → 不许因「时间过去」判 stale（OCR 第 3 条） |
| `t_transport_zero_proc_stuck_reconnects` | 消息在来但 `total=0` 卡死 → 15s 内判 stale（OCR 第 2 条） |
| `t_transport_zero_proc_frozen_reading_no_stale` | `total=0` 后消息缺字段 → 冻结读数不许误判（OCR 第 3 轮第 1 条） |
| `t_transport_normal_reconnect_stale_retries_live` | 普通断线续传后 stale → 再跳一次，不直接退回本地（OCR 第 1 条） |
| `t_transport_network_then_stale_retries_live` | 建连失败（network）后续传 stale → 同样再跳一次（OCR 第 3 轮第 3 条） |
| `t_transport_zero_progress_after_reconnect_not_stale` | 重连后 `total=0` 不许误判 stale（OCR 第 1 轮边界） |
| `t_transport_queue_full_drops_oldest` | 新增 `dropped_s` 按样本数算，不按块数×`_chunk_s` 估（OCR 第 4 条） |
| `test_stream_gate::封口句不按 25 词劈开` | 改为 AST 钉 `max_words=STREAM_MAX_WORDS` + `seg_t0`，不再被格式变化误红 |

红-绿验证（都在**新进程**里跑，避免模块已 import 的假绿）：
- 旧判据：连续喂 1.2s，`reason()` 仍 `''` → 新判据红。
- `_proc_rel_ms >= 0` 变异：`t_transport_zero_progress...` 报 `stale` → 新判据红。
- 去掉 `disconnected→stale` 的额外重试：`t_transport_normal_reconnect...` 连接数停在 2 → 红。
- 去掉 zero-proc 兜底：`t_transport_zero_proc_stuck...` 报 `''` → 红。
- 去掉 `_proc_seq > _backlog_seq`：`t_transport_backlog_caught_up...` 的 `reconnects` 变 1 → 红。
- `dropped_s` 改回 `dropped_chunks * _chunk_s`：队列测试报 28.8s vs 24.0s → 红。

---

## 6. 验收

- `scripts/check.py --all`：117 个测试文件全通过；ruff 干净；pyright 基线内；
  写端（sessions / ~/.classlive / Lectures / git status）无变化（OCR 第二轮修复后重跑）。
- 真实 Soniox 直连（EU key，25s 语音，最终代码）：
  `sealed_n=4`、`reason=''`、`reconnects=0`、`backlog_max_s=0.1`、`msg_gap_max_s=1.11`、
  `dropped=0`；`sentence_wall_t0` 实测延迟 p50 1.39s。
- 隔离 e2e（60s 文件回放，`run_isolated.py`，`--test-mode`）：进程跑完、播完到 settled 0.33s、
  报告写出、新增 metrics 字段可见；test1 通行码当前被 Worker 403（环境问题，非本次改动），
  该轮 stream 走 `key_rejected → 本地 Parakeet`，本地路径正常。
- 未做：Soniox 真实课 e2e（需要有效的 `cl-` 通行码；test1 当前 403）。

### OCR 第二轮（2026-10-21）

第一轮 1 条（绝对量守卫）已修。第二轮 6 条，逐条处置：

1. **普通断线续传后 stale 直接退回本地** → `_attempts` 允许 `disconnected → stale` 再跳一次
   （最多三次连接）；新增回归判据。
2. **消息在来但 `total=0` 卡死，两端都判不出** → 新增 `ZERO_PROC_TIMEOUT_S=15s` 兜底；
   新增回归判据。
3. **hold 用冻结读数** → 新增 `_proc_seq`，hold 必须等到新进度消息仍落后；新增「追平不判」判据。
4. **`dropped_s` 按块数×0.12 高估** → 改按 `_dropped_samples/SR`；队列测试钉住。
5. **2 元组兼容分支不可达** → 保留兼容分支（防外部注入炸掉），注释改为准确描述。
6. **docstring 缺 `backlog_s`/`backlog_hold_s`** → 连同 `zero_proc_timeout_s` 补进「小值接缝」清单。

### OCR 第三轮（2026-10-21）

3 条，全部已修：

1. **`total=0` 冻结读数**：消息缺 `total_audio_proc_ms` 时 `_proc_rel_ms` 不更新 → 15s 后误判。
   加 `_zero_proc_seq`，必须等到新读数仍为 0；新增 `t_transport_zero_proc_frozen_reading_no_stale`。
2. **队列满记账漏一块**：`get_nowait()` 被消费者取空时新块没进队，但 `_dropped_samples` 没加。
   改成「先腾一格，再试着放新块，按真正没进队的那块记账」。
3. **`network → stale` 漏了额外重试**：首因是建连失败时第二次 stale 会直接退回本地。
   条件扩成 `last_err in ("disconnected", "network")`；新增
   `t_transport_network_then_stale_retries_live`。

---

## 7. 风险 / 未做

- `ws.send` 若在内核里长时间阻塞，控制线程卡在 send 上，`got` 判死也检查不到；
  现有代码同样如此，本次不引入新风险。后续可把 watchdog 放独立线程。
- `BACKLOG_S=3s` / `HOLD=3s` 是工程取舍；真实课若出现 3–4s 的短暂抖动，可能触发一次跳实时
  （丢几秒）。已有 `skipped_s` / `reconnects` 可观测；hold 还要求新读数确认，降低误判。
- 普通断线 → 续传 → 仍 stale 时会多一次跳实时（第 3 次连接）；如果第 3 次也失败才退回本地。
  这是为了避免「一次慢重连 = 整节课本地」，代价是极端情况下多领一次 key、丢掉积压几秒。
- 跳实时丢掉的几秒目前**没有自动补译**；音频仍在本地，后续可接课后补译/回填。
- Soniox 路径的 ASR 质量统计（words/empty/punct）仍未接入 `asr` 块（P1）。
- HTML 详细版仍含课后麦克风测试句（P2）。
