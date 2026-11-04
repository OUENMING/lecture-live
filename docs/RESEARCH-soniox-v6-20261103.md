# Soniox STT v6 调研与 ClassLive 适配审查

日期：2026-11-03 · 触发：v6 当天发布 · 状态：代码已切 v6，等真课数据

## 0 结论（先看这五条）

1. **价格没变**：实时 $0.12/小时、异步 $0.10/小时，token 单价与 v5 相同。
2. **v6 = `stt-rt-v6`（实时）/ `stt-async-v6`（异步）**，2026-11-03 发布；API 完全兼容，只换 model 名。
3. **v5 于 2026-12-15 下线**；之后旧 model 名自动路由到 v6，不会断服。ClassLive 已提前切。
4. **断句碎片不能靠 v6 调参解决**：`endpoint_sensitivity` / `endpoint_latency_adjustment_level`
   在官方 API 参考里标着「Supported only by the Soniox v5 model」；v6 只剩
   `max_endpoint_delay_ms`（上限，默认 2000ms，范围 500–3000），而它是**最晚**多久出端点，
   不是「至少等多久」——挡不住模型认为一句话已经说完。仍走 B1 后处理合并。
5. **v6 没有独立基准**：官方 benchmarks 页仍是 v5 的 1.34% semantic WER / 260ms
   （2026-08 数据）；发布当天没有社区实测可引用。

## 1 版本与价格（2026-11-03 读取官方页）

| 项目 | v6 |
|---|---|
| 实时 `stt-rt-v6` | $0.12 / 小时 |
| 异步 `stt-async-v6` | $0.10 / 小时 |
| 音频输入 token（实时 / 异步） | $2.00 / $1.50 每 1M |
| 文本输入 / 输出 token（实时） | $4.00 / $4.00 每 1M |
| 文本输入 / 输出 token（异步） | $3.50 / $3.50 每 1M |

来源：[Pricing](https://soniox.com/pricing)（2026-11-03）、
[Models / changelog](https://docs.soniox.com/stt/models)（2026-11-03）、
[发布博客](https://soniox.com/blog/soniox-stt-v6)（2026-11-03）。

一课 50 分钟、只做 ASR 时约 **$0.10**（音频 $0.05 + 输出 $0.05）；
ClassLive 的中文翻译走 DeepSeek，不占 Soniox 输出。

## 2 v6 新特性（官方，摘与 ClassLive 相关的）

- **准确率**：60+ 语言整体提升；口音、句内混语言更稳；`context` 利用更好。
- **格式变化（要盯）**：价格与编号更接近口语写法 ——
  `15 dollars and 20 cents` 不再变成 `$15.20`；代码里的连字符只在说的时候才写
  （不再出现 `NP-38-BX` 这种多余连字符）。
- **翻译**：动态决定攒多少语音再翻（德语/韩语这类后置信息语言受益）。
- **语音 agent 两项**（ClassLive 不用）：`enable_agent_text_input`、agent 自身回声消除。
- **端点检测 / 说话人分离**：官方称有改进，但没有给数字。

## 3 端点检测：官方参数与社区经验

官方三个旋钮（[Endpoint detection](https://soniox.com/docs/stt/rt/endpoint-detection)，2026-10-30）：

| 参数 | 范围 | 默认 | v6 支持 |
|---|---|---|---|
| `enable_endpoint_detection` | bool | false | ✅ |
| `max_endpoint_delay_ms` | 500–3000 | 2000 | ✅ |
| `endpoint_sensitivity` | -1.0–1.0 | 0.0 | ❌ 官方标 v5-only |
| `endpoint_latency_adjustment_level` | 0–3 | 0 | ❌ 官方标 v5-only |

社区里能对上号的四条（都是 GitHub issue，不是博客二手）：

- LiveKit `agents-js#2724`（2026-10-14）：Soniox 插件不发 `{"type":"finalize"}`，
  最后一段要等 Soniox 自己的端点，实测比主动 finalize 慢约 250ms。
- Sokuji `#464`（2026-09-01）：把 `max_endpoint_delay_ms` 硬编码成 500ms（最激进值），
  用户想「让它多等一会」也做不到；修法是删掉硬编码、回到 2000 默认。
  → 教训：**不要为了低延迟把端点上限压到最小**，会切出更多段。
- LiveKit `agents#7357`（2026-09-19）：半开连接（无 FIN/RST）会永远挂着；
  加 heartbeat 后约 46s 才发现。ClassLive 自己有 watchdog，10-20 踩过同类问题。
- LiveKit `agents#5248`（2026-03-27）：`max_endpoint_delay_ms` 一度没暴露，
  被加进 STTOptions；说明这个参数在实践中确实要可调。

**对 ClassLive 的判断**：现在只发 `enable_endpoint_detection: True`，等于用默认 2000ms，
没有硬编码激进值——这点是对的。碎片问题的根因是「模型认为上一句已说完」，
不是等待上限不够；加 `max_endpoint_delay_ms` 不会让它更愿意等。仍按 B1 做客户端反向合并。

⚠️ **文档自相矛盾，别盲试**：API 参考写 `endpoint_sensitivity` / `endpoint_latency_adjustment_level`
「Supported only by the Soniox v5 model」，但 Python SDK 的类型页只写「Introduced in the Soniox v5 model;
earlier models reject it」，没有说 v6 拒绝。两种读法都可能。**先不要往 v6 请求里加这两个参数** ——
若 v6 真拒绝，会返回 400 → ClassLive 整节课退回本地 ASR，代价太大。等有隔离环境（test key + 一小段音频）
再试，成功再谈用它压碎片。

## 4 ClassLive 适配审查

已做：

- `stream_asr.MODEL = "stt-rt-v6"`，`_config()` 与 `region_route.MODEL`（探测）同步；
- `stream_asr.metrics()` 增加 `"model": MODEL` → 收尾报告 / `.live.jsonl` / class_report 能看到这一节是 v5 还是 v6；
- `scripts/class_report.py` 的 stream 行显示 `model=stt-rt-v6`；
- 测试同步（`test_stream_asr`、`test_class_report`）。

核对过、不用改的：

- **鉴权**：已经用 `Authorization: Bearer` 头（`stream_asr.py:1135`），
  不受 2027-01-15「只把 key 放在 protocols 里会被拒」的迁移影响。
- **v5-only 参数**：一个都没发，v6 不会因未知参数报 `Model does not support ...`。
- **context 上限**：仍是 8000 token，v6 没放宽（官方错误码里还写着 8000）。
- **keepalive**：流式期间**每 20 s 至少一条**（40 s 只是「建连后、start 请求前」的规则）；
  ClassLive 静音时每 5 s 发一条（`KEEPALIVE_S=5.0`），余量够。
- **会话时长**：单条实时会话上限 **300 分钟**（官方 Limits 页，2026-10-21 读取）；重连会新开一条，不受影响。
- **`max_non_final_tokens_duration_ms`**：错误码里还列着，但已被官方废弃
  （Pipecat PR #2873 明确移除 `enable_non_final_tokens` 与它）——不要加。

风险与待观察：

1. **数字/货币写法变化**：`note_math` 抽的是数字 token；`15 dollars and 20 cents`
   仍有两个数，但 `$15.20` 这种整体金额不再出现。经济课的价格题可能受影响，
   需要看真课笔记有没有漏核。
2. **v6 端点行为未知**：可能切得更少，也可能切得更多；只能用量具对比。
3. **地区可用性**：`stt-rt-v6` 在 eu/jp/us 三个 Worker 路由区是否都上线，官方没写；
   首节课若 `invalid_request` / 模型不支持，先回 `stt-rt-v5`（12-15 前仍可用）。

## 5 真课要看的数据（最小集）

1. **模型**：报告里 `model=stt-rt-v6`（这次新加）。
2. **流健康**：`sealed_n`、`seal_latency_s` 的 p50/p90、`reconnects`、`down_s`、
   `dropped_chunks` / `dropped_s`、`msg_gap_max_s` —— 对照最近一节 v5 的同一组数。
3. **碎片**：课上人工记 2–3 个「介词短语被切成独立句」的例子（如 `With the stable growth rate.`），
   看 v6 是变多还是变少；这是 B1 是否还要做的判据。
4. **数字**：抽 5 个课上的价格/百分比/编号，对照笔记的核对清单有没有漏。
5. **不要声称 WER**：没有真值，只能做 v5/v6 同课的两两对比或人工抽听。

## 6 未核实 / 下一步

- [ ] 真课确认 eu 区 `stt-rt-v6` 可用（首发课就是判据）。
- [ ] 用同一段音频跑 v5 / v6 并排（`scripts/stream_eval.py` 或 `scripts/test_pipeline.py`），
      再决定要不要调 `max_endpoint_delay_ms`。
- [ ] 若数字核对漏检变多，考虑在 `note_math` 前加「货币词 → 数字」归一化。
- [ ] v6 端点行为若明显变好/变差，回写 B1 的优先级。

## 7 第二轮补充（2026-11-03 晚）

这一轮把实时 API 的边角参数、SDK 行为和社区仓库翻了一遍，结论按「可做 / 要测 / 不做」分。

### 7.1 可直接做（低风险）

1. **模型名进报告**（已做）：`metrics()["model"]` + class_report 的 `model=`。
   没有它，v5/v6 的课混在一起就没法对比。
2. **语义端点计数进报告**（已做）：`metrics()` 新增 `endpoints_n`（`<end>` 总数）与
   `endpoints_between_n`（落在两句之间的数量）；class_report 显示「端点 7 个（句间 3）」。
   后者是「本来一句被端点切开」的可测量代理 —— v5/v6 的碎片差异直接看这一对数。
   原始事件本来就在 `.live.jsonl`（`event("endpoint")`），这次只是把汇总放进报告。
3. **货币写法归一化**（建议做，等真课确认）：v6 把 `$15.20` 写成
   `15 dollars and 20 cents`；`note_math` 仍能抽到 15 和 20，但「金额」这种整体量会丢。
   如果真课笔记出现价格题漏核，再在数字抽取前加一层「X dollars and Y cents → X.YY」。
   ⚠️ 不要提前改——先拿到一节真课的样本，避免为没发生的格式变化改核对链。

### 7.2 要实测再定

4. **`max_endpoint_delay_ms`**：v6 仍支持（500–3000，默认 2000），但它是上限，
   不是「至少等多久」。真课先记碎片数量，再决定要不要动；不要学 Sokuji #464 压到 500。
5. **`TERMS_MAX`（当前 100 条）**：v6 官方说「更会用 context」，但没说更大 context 不拖延迟。
   只有并排量到延迟没变差，才考虑放宽；否则维持「小目标 + 超量记账」。
6. **会话上限**：官方现在是 300 分钟/条；ClassLive 每节课一条、重连另开，不受影响。
   key broker 返回的 `max_session_s`（4 小时）目前没被使用，也不需要。

### 7.3 明确不做

7. **`enable_agent_text_input` / agent 回声消除**：面向语音 agent，ClassLive 是单向课堂收音。
8. **`client_reference_id`**：官方写明「临时 API key 下会被忽略」，ClassLive 正是临时 key，
   加了也不进 Soniox 用量日志。
9. **`pause/resume`**：SDK 的暂停只停发音频，**仍按整条流时长计费**；ClassLive 本来连续推流，
   没有省钱的收益，也没有「暂停上屏」的产品需求。
10. **`max_non_final_tokens_duration_ms` / `enable_non_final_tokens`**：已废弃（见 §4）。
11. **`enable_speaker_diarization`**：会给每个 token 加 speaker 字段、增加输出 token；
    现有笔记链不使用说话人标签，收益不抵成本。
12. **`enable_language_identification`**：同上；英文课堂、翻译走 DeepSeek，不需要语言标签。

### 7.4 社区这一轮的新事实

- 发布当天（2026-11-03）**没有 v6 的独立实测**：Reddit / HN / GitHub 都还搜不到 v6 的准确率或延迟报告；
  唯一能引用的是官方博客的定性描述。别把「v6 更准」当已证事实。
- Pipecat `#2873`：官方合作方在清理 Soniox 参数，明确移除 `enable_non_final_tokens` 与
  `max_non_final_tokens_duration_ms` —— 错误码列表里还有它们，但不要照着加。
- LiveKit `agents-js#2724`：插件层不发 `finalize`，最后一段要等端点；这是**插件**的问题，
  ClassLive 自己解析 `<end>` + 自建封口，不依赖插件的 flush。
- LiveKit `agents#7357`：半开连接要靠 heartbeat 才能发现（约 46 s）；ClassLive 有
  `msg_gap_max_s` watchdog，10-20 已处理过同类故障，保持现状。

### 7.5 一个已修正的错误

第一轮写的「keepalive 每 40 s」不准确：40 s 是**建连后、start 请求前**的规则；
**流式期间（包括暂停推音频）是 20 s**。ClassLive 用 5 s，安全，但文档已改正（§4）。

## 8 今天的课：全量数据（2026-11-03）

### 8.1 本地 5 节

| 会话 | 模型 | 时长 | 封口 | 端点（句间/句中） | 退回 | 收尾 |
|---|---|---|---|---|---|---|
| 10:14 ECON10790 | v5 | 44 min | 0 | — | **network（DNS 全断）** | 8 min |
| 11:07 ECON10770 | v5 | 50 min | 491 | 289（282/7） | 无 | **35 min** |
| 12:33 ECON10770 | v5 | 24 min | 146 | 51（46/5） | 无 | **68 min** |
| 15:01 ECON10740 | **v6** | 18 min | 306 | 48（47/1） | 无 | 用户选「不存」→ 无笔记 |
| 15:25 ECON10740 | **v6** | 63 min | 809 | 199（195/4） | 无 | 见下 |

- 两节 v6：`dropped_chunks=0`、`reconnects=0`、`backlog_max_s≤1.4`、`msg_gap_max_s≤2.0`。
- 15:25 收尾：复习层 12 块，**1 块失败**（日志原文「已按兜底重试过；这些课段的讲解缺失」）；
  报告生成时间被系统休眠拉长，不能拿来量收尾耗时。
- 10:14 那节：DNS `nodename nor servname` 全程失败，Soniox 0 句、翻译全失败、本地 Whisper 没装
  → 整节课只有转录，笔记 `changed_pct=36.8%`。这是今天最严重的一节。

### 8.2 v5 / v6 准确率（同一段音频，test key）

`~/.classlive/eval/econ_daniel.wav`（119.4 s / 312 词，经济学独白），1× 实时喂，
同一套归一化（数字词 → 数字，去标点）：

| 模型 | WER | sub | del | ins |
|---|---|---|---|---|
| stt-rt-v5 | **6.41%** | 5 | 15 | 0 |
| stt-rt-v6 | **5.45%** | 6 | 11 | 0 |

差距 = **3 个词**（主要是少删 4 个词）。同一段文本的另一条录音（samantha）输出 token 完全一致，
所以有效样本只有 **1 条 2 分钟音频** → **不能据此声称 v6 更准**。要下结论至少再跑 5–10 条不同音频。

### 8.3 其他用户（上传桶，今天）

今天只有 1 节别人的课（另一组 10-29 的课是今天补传的，不算）：

- BUSI3222，51.7 min，中国上海，region=jp（探测 377 ms 建连 / 462 ms RTT）。
- **6 次重连**，全部 `send stuck >5s; close 1006`；`down_s=9.5`、`skipped_s=36.7`（丢约 37 秒）。
- 笔记正常生成；翻译 `zh_first p50=1042ms`、`seg_to_zh p50=2021ms`（明显慢于本机 ~470/1850）。
- `merge` 触发 4 次、失败 2 次 → 跨段半句合并仍不稳。

### 8.4 判断

- ✅ v6 切换本身没问题：两节零丢块、零重连。
- ✅ 收尾预算对新代码生效（12 块能收住），但**代价是 1 块讲解缺失**——需要「主轮之后再补跑失败块」。
- ❌ 收尾仍长：修复前两节 35 / 68 分钟；P2（拆进程）没做，仍会挡住下一节课。
- ❌ 网络全断时没有可用兜底：Soniox 全灭 + 翻译全灭 + 本地 Whisper 未安装。
- ⚠️ 其他用户的弱网重连会静默丢 37 秒；`send stuck 5s` 是否太激进，需要真机对照。
- ⚠️ v6 准确率今天**没有证据**说更好或更差。
