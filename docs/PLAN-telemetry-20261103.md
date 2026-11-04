# 遥测与诊断回传：现状审查 + 新方案（2026-11-03）

状态：审查完成，未改代码。目标不是「多采一点」，而是**让没预想到的故障也能从事后数据里被描述出来**。

## 0 结论（先看六条）

1. **正常模式零回传**：`tester` 只在 `--test-mode` 下创建，`start_upload` 只被测试模式收尾调用。普通 `cl` 只有本机 `app.log` 与会话 md，没有上传路径。
2. **测试模式传的是「课后聚合 + 全量深包」**：`report.json` 是收尾时内存累加器的快照；崩溃、合盖、强杀时收尾不执行，能补的只有每 10 分钟一次的临时快照。
3. **数据传上来了，看板没看**：`scripts/cl_admin_ui.py` 的 `_report_summary` 没有取 `reconnects / down_s / skipped_s / dropped_s / msg_gap_max_s / backlog_max_s / model / endpoints_n`，也没有 `jev` 块。上海用户 6 次重连在面板上等于没发生。
4. **几个字段语义会误导**：`reconnects` 只数「成功建立的新会话」，整节 66 次连接全失败时它仍是 0；`fallback_reason` 在没退回时也可能带一段重连死因；`capture.deficit_s` 含收尾/睡眠时间；`asr.source_pct` 按段数而不是音频秒数算。
5. **未知问题需要事件脊柱**：课后聚合只能回答已经想到的问题。要描述「用户那边正在发生、我们还没想到」的故障，必须在边界上记**带因果 id 的宽事件**，并保留崩溃前最后一口气（心跳 + 下次启动补记）。两个现成例子：11-03 复习层 0/8，报告里连 `chunk_stats` 都没有（全失败时 `note_v2` 不挂这个键）；都柏林那节 66 次连接全失败，`reconnects` 仍是 0（只数成功会话）。
6. **中国区掉线不能修 GFW，但能少自己制造断线**：自适应 `SEND_STUCK_S`、连续 send stuck 后跨区故障转移、探测所有区域并记录失败、控制面预检、修掉空缓存导致的 `default_missing`。

## 1 现状

### 1.1 两条路径

| | 正常 `cl` | `cl test` |
|---|---|---|
| 本机会话 md | 写 | 写 |
| `report.json` | **不写** | 写（收尾；课中每 10 分钟临时快照） |
| `.live.jsonl` | 默认关（`CLASSLIVE_LIVE_LOG`） | 默认开 |
| 音频 | 不录 | 默认录（Opus 分段） |
| 诊断包 | 无 | `extra.tar.gz`：日志、崩溃、git 状态、note.md、旁路文件 |
| 上传 | **无** | 课后进 `UploadQueue`，失败退避 1→120 min，14 天过期 |
| 崩溃补记 | 无 | 有 `.pending.json` + `recover_pending`（但只补已落盘的东西） |

### 1.2 上传链路

`finish()` 写报告 → `start_upload()` 入队 → 后台 `pump()` 试一次 → `make_extras()` 打诊断包 → 下次启动 `recover_pending()` 续传。队列落盘、tmp→rename、幂等 key、14 天过期，这套是成熟的（照 Glean / Firefox）。

问题不在传输，在**采什么、什么时候采、采到的东西能不能重建故障**。

### 1.3 已知失真 / 缺口

| # | 现象 | 影响 | 判定 |
|---|---|---|---|
| 1 | `reconnects` 只数 WS 握手次数 | 握手成功但 config 帧失败、从未 alive 的会话也会 +1；它不是「成功恢复次数」；整节连不上时仍是 0 | 采集语义错 |
| 2 | `fallback_reason` 与 `fallback_detail` 混用 | 把「重连死因」读成「已退回本地」 | 字段语义错 |
| 3 | `_detail` 不随新会话清空 | 后续重连的 detail 被上一次污染（如两个 `close 1006`） | 采集 bug |
| 4 | `down_s` 含退避 + 领 key + 握手 + config send | 不是纯握手时间；连续失败会覆盖 `_down_since` 丢掉上一段；收尾/放弃后还会继续涨 | 字段语义错 + 采集 bug |
| 5 | `capture.deficit_s` 含收尾与系统睡眠 | 11-03 = 79.5s 收尾 + 408.3s 睡眠（拆法是恒等式，不是两个独立来源）；实际收音缺口只有 1.2s | 字段口径错 |
| 6 | `asr.source_pct` 按段数算 | `asr` 块只统计**本地兜底定稿段**；11-03 是 1 段 qwen / 4 词，整节主 ASR 是 Soniox 524 句 | 作用域错 |
| 7 | `region_probe` 只记成功写入的地区 | us/eu 领 key 失败时无声消失，只剩 jp，无法区分「没配」和「连不上」 | 采集缺口 |
| 8 | `region_probe.reason` 用缓存决定，`regions` 是本次探测 | 出现「jp 有完整数却报 default_missing」的自相矛盾 | 口径错 |
| 9 | 复习层失败只在笔记 front matter | 报告里没有 `review ok/total`，0/8 无法从报告直接看出 | 采集缺口 |
| 10 | `jev` 块只记最后一次失败 | 没有调用数、按用途分组、451/429 分类 | 采集缺口 |
| 11 | 看板丢字段 | `reconnects` 等流健康字段根本没进摘要 | 展示缺口 |
| 12 | 报告含 `segments[].text` | 测试模式默认上传逐句原文；与「只传指标」的表述不一致 | 隐私面 |
| 13 | 诊断包只抹凭据形状 | 绝对路径（含用户名）、主机名、git 状态原样上传 | 隐私面 |
| 14 | 正常模式无数据 | 绝大多数使用不可见 | 覆盖缺口 |
| 15 | 崩溃 / 强杀 / 断电 | 收尾不执行，最多丢最后 10 分钟；正常模式什么都不留 | 覆盖缺口 |
| 16 | `sessions[]` 只记失败会话 | `_stopped` / `_fatal` 早退时不入表；最后一节不是「还在跑」，是已经返回但被跳过；11-03 是 6 条 vs 7 节 | 采集缺口 |
| 17 | `translation_latency.n` 只数成功等到首字的句子 | 失败多的一节会显得「更快」；`by_model` 又按全部行计数，两个分母不一致 | 字段语义错 |
| 18 | `blocks.live` 的 `chapters/failed_calls` 是中间态 | `stats()` 其实已在 `finish()` 之后；真因是 `finish()` 超时后线程还在跑，`chapters` 在合成前 +1、失败后 `failed` 才 +1；`chapters=5` 是**尝试数**，笔记只落 4 章 | 采集语义错 |
| 19 | `vad.cuts` 是静音断句 | 总段数 = `cuts + hard_cuts`（11-03 175+165=340）；`dropped=0` 也不等于没丢，`blind_dropped=1` | 命名错 |
| 20 | `msg_gap_max_s` 实际是「客户端排空间隔」 | 不是服务端消息间隔；只有走看门狗掐 socket 的路径会真漏掉；send 卡 5.01–5.49s 后自行恢复时反而能记进去 | 命名 + 采集口径错 |
| 21 | `backlog_max_s` 是服务端处理落后 | 不是客户端发送队列积压；`skipped_s=36.7` 不在这里 | 命名错 |
| 22 | `dropped_s=0` 只表示队列溢出为 0 | `skipped_s=36.7` 是主动跳过的音频，且 11-03 `backfill.need=0`，这部分没有补译记录 | 口径错 |

### 1.4 静默丢失（比字段错更危险）

| # | 路径 | 结果 |
|---|---|---|
| 1 | `.pending.json` 只在 `record_audio=True` 且 `stem` 非空时创建 | `--no-record-audio` 的测试课硬崩溃后不会被补传（即使盘上有 provisional 报告） |
| 2 | provisional 每 10 分钟一次 | **硬崩溃/断电**时前 10 分钟没有报告；普通未捕获异常仍会走 `finally` 写报告；快照构建超 50ms 会永久停采 |
| 3 | `make_http_send` 对 4xx/5xx/断网都返回 `False` | 401 / 413 / 429 / 断网在客户端看起来一样 |
| 4 | 队列 14 天过期后从内存移除 | `pump()` 有 `expired` 计数但没人消费；全过期时磁盘队列不重写，下次还会读回来 |
| 5 | `make_extras` 失败或超 24 MB 返回 `None` | 诊断包缺失没有提示 |
| 6 | `journal_upload_done()` 在 extras 之前撤 journal | 只有「撤 journal 后、`add_files` 前」被杀才永久缺失；入队后队列持久化，下次会补传 |
| 7 | `audio_saved` = 请求录音的布尔 | 录音打开失败/转码全失败仍为 true |
| 8 | `_report_summary` 不读 `collector_errors` | **摘要层**看不出采集异常；raw `report.json` 视图仍能看到 |
| 9 | `class_report` 只读 `.live.jsonl` / 离线 eval JSONL | 不读 `report.json`，所以 `report.json` 独有的块在课堂报告里看不到 |
| 10 | `R2_REPORT_MAX=30`，且只列有 `report.json` 的组 | 崩溃课/无报告课/较早的课在面板消失 |

### 1.5 隐私面（现有口径不一致）

| # | 问题 |
|---|---|
| 1 | `_scrub_one` 只去目录、保留文件名；文件名是「课号 + 精确时间戳」= 课表 |
| 2 | `segments[].probe.float_saved` 是路径，但不在 `_SCRUB_KEYS` 里 |
| 3 | `events[].detail` 的错误串/文件名不 scrub |
| 4 | `extra.tar.gz` 只抹凭据形状，绝对路径（含用户名）、主机名、git 状态原样保留 |
| 5 | `note.html` 直接上传，未脱敏 |
| 6 | 报告含 `segments[].text`、`probe.final_text`、`probe.draft_text`：逐句原文 |

### 1.6 测试覆盖缺口

| 场景 | 现有测试 | 缺口 |
|---|---|---|
| 正常模式不产生报告 | 无 | 没有「absence」判据 |
| 66 次连接全失败 → `reconnects=0` | 无 | 只测过成功重连 |
| 复习层全失败 → `chunk_stats` 缺失 | 无 | 只测过部分失败 |
| 上传 401/413/429/断网 | 只测 bool | 没有错误分类判据 |
| 崩溃后 `session.recovered` | 只测 journal 读写 | 没有「心跳存在但无 end」判据 |
| Jev 451 → 回退 | 无 | 没有区域错误判据 |
| 报告 schema 与 `BLOCKS` 一致 | 有 | `REPORT_BLOCKS` 与 `BLOCKS` 不一致没测 |

### 1.7 报告里没有、日志里有的关键事实（11-03）

| 事实 | 日志证据 | 报告现状 |
|---|---|---|
| 复习层 0/8 未生成 | 进度到 7/8，但成品层 all-or-nothing 全丢；note.md 写「复习层这次没生成」 | 无 review 字段 |
| Jev 451 | 20 条（9 次打分 + 11 次术语）；总结行自报「26/26 批」，逐条无法复算 | 无 `jev` 块 |
| 第 5 章合成失败 | 两次 ReadTimeout/503，17:00:22 才回来 | `chapters=5`，笔记只 4 章 |
| 收尾丢 1 句 | 「队列里约 1 句没处理完」 | `counts` 无 lost 字段 |
| `semantic_seal` 块被拒 | `bad_block` event | `collector_errors={}` |
| 4 次翻译 ReadTimeout | events t=3004/3018/3040/3053 | `translation_latency` 无 timeout_n |
| 13 条 `seg_to_zh` >10s | 全部落在最后 100 秒（utt 514、523–530），与 ReadTimeout 重合；max 23.5s | 报告只给 p50/p90，无 max/p99 |
| 162 条 stale 草稿 | `draft_stats` | 无字段 |
| 课前回顾未生成 | ConnectError | `feature_usage` 只记 UI |
| 实时总结收尾被截断 | 「超过 25 秒，剩余步骤已放弃」 | `blocks.live` 无 truncated |
| 11-03 上传日志缺失 | `upload.log` 最后一条是 10-29 | 本地无法证明上传完成（R2 有报告） |

## 2 新方案：三层

| 层 | 内容 | 默认 | 体积 | 用途 |
|---|---|---|---|---|
| **Spine** | 会话开始/心跳/结束、层状态迁移、连接尝试、ASR/翻译/笔记/上传的计数事件、异常 flag | 开（P0，首次运行告知） | 几十 KB–几百 KB/节 | 重建故障、算分母、发现未知问题 |
| **Summary** | 现有 `report.json`，课后生成 | 开 | 几十 KB/节 | 人看、归档、版本对比 |
| **Deep** | 日志尾部、崩溃 `.ips`、堆栈、转录样本、音频 | 关，按次授权 | 几 MB–几十 MB | 只有具体故障才取 |

关键：Spine 不是「更多日志」，是**每个工作单元一条宽事件**。工作单元 = 一次连接尝试 / 一个 ASR 段 / 一批翻译 / 一次笔记阶段 / 一次上传。

### 2.1 公共信封

每条事件都有：

```
schema_version, event, event_id, session_id, install_id, seq,
t_wall, t_mono_ms, t_awake_ms, boot_id,
app_version, os_version, arch, region, component, severity,
caused_by?, attrs
```

- `t_wall` 与 `t_mono` 双记录：墙钟会被 NTP/时区/合盖改；单调钟算时长。
- `t_awake_ms`：睡眠时停走的时钟；与墙钟差值可判「机器睡了多久」。
- `install_id`：可轮换伪匿名 id；不用主机名/硬件 id。
- `seq`：每 session 单调递增；服务端用 gap 判丢事件。

### 2.2 事件清单（P0）

| 事件 | 关键字段 | 触发 |
|---|---|---|
| `session.start` | consent_level, config_hash, region | 点开始 |
| `session.heartbeat` | layer_states, counters, queue_depths | 每 15 s；睡眠/唤醒立即补 |
| `session.end` | outcome, duration_s, end_reason, data_loss_estimate | 正常收尾/手动停 |
| `session.recovered` | prev_session_id, last_seq, heartbeat_age_s, boot_changed | 下次启动发现上节没 end |
| `layer.transition` | layer, from, to, reason, duration_ms, evidence | 任一层状态变化 |
| `net.connect` | attempt_id, host, region, dns_ms, tcp_ms, tls_ms, ws_ms, http_status, result | 每次连接尝试 |
| `ws.close` | code, reason_class, last_recv_age_ms, sent_msgs, recv_msgs, reconnect_n, closed_by | 关闭/错误 |
| `asr.segment` | seg_id, audio_ms, first_partial_ms, final_ms, finalize_lag_ms, empty, char_bucket | 每个 final；**不传文本** |
| `translate.batch` | n, latency_ms, p95_ms, engine, fallback, errors | 每批翻译 |
| `note.stage` | stage, status, duration_ms, review_total, review_ok, verify_total, verify_ok | 每个笔记阶段 |
| `upload.batch` | n_files, bytes, attempt, oldest_age_s, result, error_class | 每次 pump |
| `error` | component, error_class, fingerprint, recoverable, count | 异常；同指纹限流 |
| `anomaly.flag` | rule_id, severity, evidence, window_s | 规则命中；有冷却 |

`asr.segment` 只记字符数桶（0 / 1–20 / 21–100 / >100），不记文本。`host` 只记域名，不记完整 URL/查询串/token。

⚠️ 上面是**元数据事件**。Owen 2026-11-03 确认全量授权后，另开一份 `content.jsonl`：

| 事件 | 字段 |
|---|---|
| `asr.final` | ts, en, asr_raw, seg, latency_ms |
| `translate.pair` | seg, en, zh, model, prompt_version, ms, retry, error |
| `note.chunk` | stage, idx, total, input_hash, output_json, ms, error |
| `jev.call` | purpose, provider, state, questions, answers, ms, status |
| `note.output` | 最终 md/html 的 sha256 + 全文（或全文单独文件） |

两份文件分开：元数据小、可长期留；内容大、按会话删。上传时同一个会话前缀，删一个前缀整节消失。

### 2.3 心跳与「最后一口气」

1. `telemetry/<session_id>.events.jsonl`：append-only；有界队列 + 后台线程；绝不阻塞音频回调。
2. `telemetry/heartbeat.json`：每 15 s 原子替换，记 `session_id / seq / t_wall / t_mono / layer_states / 关键计数`。
3. 内存 ring buffer：最近 N 条 breadcrumb（参考 Sentry 默认 100）。
4. 下次启动：读 heartbeat；有 `session_id` 但没有 `session.end`，或 `boot_id` 变了，或心跳年龄 >60 s → 补发 `session.recovered`，`outcome=abnormal`，带最后 seq 和丢失窗口估计。
5. 崩溃：macOS `.ips` 已由系统写盘；Python 侧加 `faulthandler`；下次启动扫描。
6. 合盖：`t_wall - t_awake` 差值大而 `t_mono` 小 → `sleep_gap` flag。

不引入 Crashpad：它是原生进程外 handler，接它要改主进程架构；`.ips + faulthandler + 心跳` 已经能回答「上节课死在哪一步」。

### 2.4 分层健康（不要只报重连次数）

| 层 | live 证据 | 失败证据 |
|---|---|---|
| capture | 回调持续、RMS 非零、设备稳定 | 回调间隔 >2 s、RMS 长时间 0、设备切换 |
| control | WS 握手成功、key 领取成功 | DNS/TCP/TLS 失败、401/403、5xx |
| asr_stream | 收到 partial/final、first_partial_ms 有值 | 连上但 `last_recv_age` >15 s、0 final |
| asr_final | 每段 finalize 完成 | 空转写比例、超时、熔断 |
| translate | 批次返回、延迟在阈值内 | 连续失败、fallback、p95 爆表 |
| note | 各阶段完成、review/verify 有结果 | `review 0/N`、阶段缺失 |
| upload | 对象上传成功 | 最老条目 >24 h、连续失败 ≥3 |

会话结果拆两个：**core success**（有字幕、有笔记落盘）与 **delivery success**（core + 24 h 内上传完成）。

三个字段拆开「重连 0 但整节失败」：
- `control.never_connected`
- `asr.never_live`
- `captions_shown_s / duration_s`

## 3 数据范围（2026-11-03 Owen 确认：测试用户已授权，无隐私顾虑）

**默认全量回传，不再区分「正常模式只本地」。** 之前那套 P0/P1/P2 最小采集作废。

| 类别 | 内容 | 默认 | 说明 |
|---|---|---|---|
| 事件 | 会话/心跳/层状态/连接/ASR/翻译/笔记/上传/异常 | **传** | 元数据，几十 KB–几 MB |
| 逐句内容 | ASR 原文、草稿、译文、时间戳、延迟、回改 | **传** | 单独 `content.jsonl`，约 50–200 KB/节 |
| 笔记内容 | 每阶段输入/输出、块状态、错误、最终 md/html | **传** | 含复习层/讲解/例题 |
| Jev | 每次 state / questions / answers / provider / 错误 | **传** | 便于复现打分与术语闸门 |
| 日志/崩溃 | app.log 尾部、`.ips`、faulthandler | **传** | 崩溃现场 |
| 设备/环境 | 型号、OS、版本、region、网络类型、课程号 | **传** | 不加主机名/用户名也可；授权后保留也无妨 |
| 原始音频 | Opus 分段 | **单独开关，默认不传** | 体积大且含同学声音；要开再开 |

工程约束：所有采集走有界队列 + 后台线程，绝不阻塞音频回调；单节上传上限 50 MB（超了先传事件+内容，音频单独）；gzip/zstd 压缩；断点续传；保留 90 天；按会话可删。

「多」不等于「准」：字段语义错（`down_s`/`reconnects`/`asr` 作用域）采再多也是错的，先修语义再扩量。

## 4 看板与服务端聚合

1. `_report_summary` 补：`reconnects / down_s / skipped_s / dropped_s / msg_gap_max_s / backlog_max_s / model / endpoints_n`；**先补采集再加展示**——`review_ok/review_total`、`jev_calls/jev_fail` 现在报告里根本不存在，只改摘要会恒为 `None`。
2. `REPORT_BLOCKS` 补 `semantic_seal / jev / counts`；`feature_usage` 是 `report.json` **顶层键**，不要塞进 `REPORT_BLOCKS`。
3. **字段新旧并存一个版本**：`reconnects`、`down_s`、`msg_gap_max_s`、`sessions`、`vad.cuts`、`blocks.live` 的旧键继续发，新键（`connect_attempts`、`reconnect_downtime_s`、`drain_gap_max_s`、`failed_sessions`、`silence_cuts/total_cuts`、`chapters_attempted/written`）同时发；看板读新键、旧报告兜底读旧键。`fallback_detail` 直接改名 `last_close_detail`，摘要做 `last_close_detail or fallback_detail` 双读。
4. 报告加 `report_version`（当前没有 schema 版本），下一次改名不用靠字段嗅探。
5. `region_probe.attempts` 必须是**独立字段**，不要塞进 `regions`（`decide()`/`_total_ms()` 只认完整测量，测试还断言领 key 全失败时 `regions=={}`）；摘要白名单要放行并限长/枚举化。
6. AE 事件加 `session_id / region / country / ver / layer / outcome`；SQL 按国家×区域×版本算分母。
   ⚠️ 现在 Worker 的 AE 只覆盖 HTTP 路径（`/v1/transcribe`、`/v1/chat/completions`、`/v1/jev`、`/v1/soniox/key`），**看不到直连 Soniox 的 WebSocket**。中国区掉流在服务端没有分母，必须加一个 `POST /v1/telemetry` 接事件批（带通行码认证），或在下一次领 key 的请求里带一个压缩后的上节摘要。
7. 客户端只发证据 + 简单 flag；阈值、cohort、告警在服务端。客户端规则改阈值要发版，服务端当天可改。
8. 规则先上 7 条：`send_stalled`、`recv_gap`、`never_live`、`region_failover`、`note_degraded`、`upload_backlog`、`abnormal_end`。

## 5 中国区掉线

### 5.1 四层归因

DNS → TCP → TLS → WS 握手 → 上游 ASR → 翻译 → 客户端。每层都有独立的耗时与错误类，别再混成一个 `network`。

⚠️ **不要把区域当成可控变量**：现有 jp/us/eu 三组同时对应不同设备、系统、课程——
jp 全是 M2 / 8 GB / macOS 13.4.1 / BUSI3222；us 全是 M5 / 16 GB / macOS 26.4.1；
eu 全是 M1 Pro / 16 GB / macOS 15.7.5。每区只有 1 个网络指纹，n_eff=1，
不能把差异归因于「中国区」。us 组也有 1 次重连（`32b6d636`）和整节实时流失败（`ba1fb923`），
所以正确的说法是「这台中国网络+设备上的 3 节课出现重连」，不是「中国区问题最大」。

### 5.2 修复项（按优先级）

1. **自适应 `SEND_STUCK_S`**：在 `_timed_send` 的 `ws.send` 前后取 `monotonic`，**只记成功返回的样本**（异常样本丢弃，否则 watchdog 自己掐断的样本会把 p95 越推越高）。n≥50、按 region 分开、只在会话边界更新；封顶 6–8 s（`QUEUE_MAX_S=10`，超过就是保证丢最旧）。
   ⚠️ `ws.send` 阻塞是内核发送缓冲满，不是 RTT；高 RTT 健康链路 p95 仍可能接近 0，`max(5,3×p95+2)` 不会自动放宽。要按 RTT 放宽就用 probe 的 `connect_ms/rtt_ms`，先影子采数据再定公式。
   ⚠️ 阈值变大会和本地兜底冲突：`_alive` 在 config send 后置真，send 卡住时 `allow_local()` 仍为 False，本地 ASR 被闸住，队列 10 s 溢出丢最旧。需要拆 `connected()` / `producing()`，阻塞约 2 s 就放开本地，重连阈值另算。
2. **跨区故障转移**：只能在 `_attempts` 控制线程做。`_key` 在 `__init__` 固定 region，`request_key` 注入签名没有 region——要加 region-aware seam，并在控制线程 rebind。读 `key["region"]` 与请求比对；不一致/缺失不算成功。`_detail` 的 `startswith("send stuck")` 语义不能被 region 污染；`sessions[]` 记 `region_req/applied` 快照；`_fatal` 只置不清，区级 fatal 要单独决定是否可重试；`ws_url` 缺失时不能连默认区却标新区。
   固定候选顺序 + `used` 集合 + 单节 ≤2 次 + 5 min 冷却 + 整节固定；key 502 现在不触发 send-stuck，只在同区退避（死守 jp），若扩展触发条件要防止 jp↔us 打转。
3. **探测所有区域并记录失败**：`attempts` 独立于 `regions`（`decide()` 只认完整测量）；default 失败也要写 attempts；阶段二现在只在 `if default:` 时跑，default 领 key 失败（CN→jp 502）时 us/eu 根本不测——要 default+us/jp/eu 并行，按返回 region 去重。
4. **修缓存/重规划**：`regions` 无可用数不写缓存；`_persist` 改 tmp+replace 原子写；`n` 按课/`probe_id` 幂等（现在 warm + main 会各 +1）；`plan` 的「选地区」与 `should_run` 解耦，否则 `n≥3` 时 replan 仍 `selected=""`。
   ⚠️ 面板是**独立进程**，`warm` 在面板 daemon 线程；main 里没有线程可 join。只能在 `entry_launch.on_start` join ≤1 s，或面板写 `probe_done/probe_id`、main 轮询 ≤1 s。
5. **控制面预检**：面板打开时同步测量会冻结 AppKit 主线程；后台线程 + `callAfter`，首屏后启动，2–3 s 预算。`httpx` 只能用 `Client.request(extensions={"trace": cb})` 拿 `connect_tcp`（含 DNS+TCP，没有单独 DNS）和 `start_tls`；连接复用不发事件；拿不到就记 total + `phases_unavailable`，不要写 0。面板「预领 key」不成立：面板进程退出后 key 带不到 main，TTL 60 s，用户挑课常超时——去掉，或只在 main 一次性注入并检查 TTL。
6. **对冲重连（后续）**：send stuck 时先并行开第二条连接，2 s 内成功就切，失败再关旧连接。降低 `down_s`，但要处理重复音频与计费，先做小规模实验。

**落地顺序**：先 3+4（连同原子写、`n` 幂等、`plan` 解耦）；1 先影子只记 p95；2 用固定 5 s 触发先验证链路；5 只测量和面板提示，不做预领。

## 6 实施顺序

**P0（1–2 天）**
1. `telemetry.py`：信封、白名单、有界队列、心跳、`session.start/end/recovered`；正常模式也建会话（不建 `TestSession`，不录音）。
2. 修失真字段（**新旧并存**）：`reconnects` 增 `connect_attempts / connections / failed_sessions / final_session`；`down_s` 增 `reconnect_downtime_s` 并修「连续失败丢上一段」「收尾后继续涨」；`fallback_detail` 改 `last_close_detail`（摘要双读）；`deficit_s` 拆 `gap / tail / sleep`；`asr` 加 `scope` 与 `stream_sealed_n`；`msg_gap_max_s` 增 `drain_gap_max_s`；`vad` 增 `silence_cuts / total_cuts / blind_dropped_blocks`；`blocks.live` 增 `chapters_attempted / chapters_written / failed_atom_calls / failed_chapter_calls / finish_truncated`（不要等被放弃的线程）。
3. 看板补流健康字段 + `report_version`。
4. `region_probe` 记 attempts + 空缓存不写 + `replan()`。

**P1（2–3 天）**
5. `net.connect` / `ws.close` / `asr.segment` / `translate.batch` / `note.stage` / `upload.batch` 六类事件。
6. 7 条 anomaly flag + 证据字段。
7. 正常模式全量 spine + `content.jsonl`（逐句原文/译文/笔记/Jev）；默认开。

**P2（3–5 天）**
8. AE 服务端分母 + 区域对比 SQL。
9. 内容包与音频分开：内容默认传，音频单独开关；按会话前缀可删。
10. 对冲重连实验；同网络 jp/us/eu A/B 量具。

## 7 验收

- `check.py` 全绿。
- 新事件模块：单元测试覆盖「队列满丢最旧」「心跳原子写」「recovered 判定」「白名单丢未知字段」。
- 一节真实课：`report.json` 的 `reconnects` 与事件流重算一致；`region_probe.attempts` 有 3 个区；`note.stage` 能看出 0/8 是哪一步。
- 崩溃演练：`kill -9` 后下次启动出现 `session.recovered`，且能指出最后一条事件。
- 中国区：同网络 A/B 能产出 jp/us/eu 的 connect/RTT/send 阻塞/重连对比；故障转移演练能把 `region_from/to` 写进报告。
