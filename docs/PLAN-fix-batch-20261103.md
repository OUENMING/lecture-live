# 修复批次计划（2026-11-03）：C5 安全化 · P0 退出 · 遥测字段 · 区域探测

状态：已审查，按批次执行。每个批次独立可回滚；每批先加红-绿测试，再改代码，最后跑 `check.py`。

## 0 总原则

- **不改数据格式的批次先上**；改格式的字段新旧并存一个版本。
- **C5 在修好之前不许合**：它会把算对的式子改成算错的。
- 每批只动列出的文件；跨批共享的函数先抽 helper，不复制逻辑。
- 任何「声称完成」之前跑 `ClassLive.app/Contents/MacOS/python scripts/check.py`，贴输出。

## 1 批次与顺序

| 批次 | 内容 | 预计 | 回滚 |
|---|---|---|---|
| **A** | C5 安全化 + 计数一致性 + 回改提前 | 1–2 天 | revert `note_guards/note_v2/note_render/note_html/obsidian_writer/notes_replay` |
| **B** | P0 线程退出 + Jev 重置 + v6 回退 | 半天 | revert `note_v2/keypoints/main/stream_asr` 对应段 |
| **C** | 遥测字段 additive + 看板 + `report_version` | 1 天 | 保留旧键，删新键即可 |
| **D** | 区域 attempts + 缓存/replan（含原子写、n 幂等、plan 解耦） | 1 天 | revert `region_route/entry_launch` |
| **E** | 全量 content spine（默认全量，音频单独开关） | 2–3 天 | 开关关闭 |
| **F** | 后台 note_worker（所有权/锁/电源/恢复守卫齐了再拆） | 2–3 天 | `CLASSLIVE_NOTE_WORKER=0` |

---

## 2 批次 A：C5 安全化

### A1 目标

1. 只改**确实算错**的那一条；算不准就退回 ⚠，不改。
2. 改完的正文、页头、终端、guard 计数、修正记录**同一口径**。
3. 回改发生在 `_with_overview` / `flag_conflicts` 之前，速览和跨节矛盾照改后的正文写。
4. 同一 claim 出现在多节时，每节都处理，不只第一节。

### A2 代码改动

| 文件 | 改什么 |
|---|---|
| `note_guards.py` | 新增 `_result_text(recomputed)`：只取最后一个 `≈/=/→` 后的纯数字；`答案值/取点/程序值/support p=` 直接返回 None。重写 `_correct_mismatch`：收集全部候选，要求恰好一个「当前 c 错、重算值对」的候选；`_EQ` 前后邻字符不许是运算符/括号；`_DIV` 有 `d` 组跳过；调用 `note_math.is_sum_term`；改后值按原 c 的小数位必须真不同。新增 `correct_mismatches(review)`（早跑，只改正文、记 `corrected/original/replacement`）；`annotate_mismatches` 保留最后一步，只处理未更正的 mismatch，加 ⚠。 |
| `note_v2.py` | `checks_summary` 增 `n_fixed`；`n_bad` 排除 `corrected`；`checks_headline` 改成「N 处已核（F 处已更正 · M 处对不上）」。更新「正文不改」的过期 docstring。 |
| `note_render.py` | 页头继续用 `checks_headline`；附录「三道核另有发现」改用 `n_bad`，不含 `n_fixed`。 |
| `note_html.py` | 页头继续用 `checks_headline`；核对表 `corrected` 行保留「✅ 已更正（原写 X）」；修正记录写「已更正：原写 X → Y」。 |
| `obsidian_writer.py` | 流水线在 `verify_numbers` 之后、`flag_conflicts`/`_with_overview` 之前插入 `correct_mismatches`；`annotate_mismatches` 仍在最后。终端 🥉 行与 `_guard_counts["flagged"]` 用 `n_bad`。 |
| `scripts/notes_replay.py` | 镜像顺序同步。 |
| `scripts/note_eval.py` | `correction_audit` 认 `corrected`，不再把已更正算成「未修正」。 |

### A3 测试（先红后绿）

`tests/test_guards.py` 新增：

1. `recomputed="5.3/3 ≈ 1.767（保留 2 位）"` → 不改，退回 ⚠。
2. `15+225/15=30` + `recomputed="225/15 ≈ 15"` → 不改。
3. `2+3×4=14` + `recomputed="3×4 = 12"` → 不改。
4. `500/60=30/3` + `recomputed="500/60 ≈ 8.3333"` → 不改。
5. `5.3÷3≈1.77。而 5.3÷3=2.89 是错的。` → 只改 2.89，不改 1.77；或候选不唯一 → 退回 ⚠。
6. 同一 claim 在两个 section → 两处都改（或两处都不改），不许只改一处还标 `corrected`。
7. `checks_summary`：1 条 corrected + 1 条 mismatch → `n_fixed=1, n_bad=1`；`checks_headline` 含「1 处已更正」「1 处对不上」。
8. 端到端：`correct_mismatches` 后 `overview_long` 不保留旧值（用一个假 `_with_overview` 顺序测试，或断言流水线顺序）。
9. `algebra` 字段只改数字、不缀中文括注。

### A4 验收

- 上面 9 条全绿；原有 `tests/test_guards.py`、`tests/test_note_v2.py`、`tests/test_layout_a.py` 全绿。
- 拿 11-03 的 `note.md` 对应 review 数据跑一遍：`0.160−0.667=0.33` 改成 `-0.507`；正文、页头、终端一致；速览无旧值。
- `check.py` 全绿。

---

## 3 批次 B：P0 退出 + Jev 重置 + v6 回退

### B1 线程退出

- `note_v2.review` 增加 `cancel` 事件；`one()` 每次重试前检查。
- 预算到点后 `fut.cancel()`；仍未完成的线程用 daemon executor（自定义 `ThreadPoolExecutor` 子类，线程 `daemon=True`），避免解释器退出 join 卡 90 秒。
- 保留 `send_stuck_s`/`deadline` 的注入接缝；测试用假 `chat` 睡 2 秒，断言 `review` 在预算内返回且进程不挂。

### B2 Jev 重置

- `keypoints.reset_failure()`；`main.run` 开课时调一次；`last_failure()` 只返回本课记录。
- 测试：上一课有失败、这一课没有 Jev 调用 → `blocks.jev == {}`。

### B3 v6 回退

- `stream_asr` 增加 `MODEL_FALLBACK="stt-rt-v5"`；收到 `model_not_available` 时置 `_force_v5`，下一次重连用 v5，并在 `metrics()` 记 `model_fallback=True`。
- `region_route` 探测 v6 失败时记录 `model_error`，不当作「区域不可用」。
- 测试：假 ws 第一次返回 `model_not_available`，断言第二次 config 的 `model == stt-rt-v5`。

---

## 4 批次 C：遥测字段

- **新旧并存**：`reconnects` 增 `connect_attempts/connections/failed_sessions/final_session`；`down_s` 增 `reconnect_downtime_s`；`msg_gap_max_s` 增 `drain_gap_max_s`；`sessions` 增 `failed_sessions`；`vad` 增 `silence_cuts/total_cuts/blind_dropped_blocks`；`blocks.live` 增 `chapters_attempted/written/failed_atom_calls/failed_chapter_calls/finish_truncated`。
- `fallback_detail` 改名 `last_close_detail`；摘要 `last_close_detail or fallback_detail` 双读。
- `region_probe.attempts` 独立字段；摘要白名单放行。
- `report_version` 顶层键。
- 补采集：`review_ok/total`、`jev_calls/fail`。
- `_report_summary` 补流健康字段；`REPORT_BLOCKS` 补 `counts/semantic_seal/jev`；`feature_usage` 保持顶层。
- 测试：旧报告缺新字段 → 摘要 `None`；新报告字段齐全；`fallback_detail` 双读。

---

## 5 批次 D：区域

- `attempts` 独立于 `regions`；default 失败也写；default+us/jp/eu 并行。
- `_persist` tmp+replace 原子写；`n` 按课/`probe_id` 幂等。
- `plan` 的「选地区」与 `should_run` 解耦；`n≥3` 仍可 `replan`。
- 面板 `on_start` join ≤1 秒，或写 `probe_done/probe_id` 让 main 轮询。
- 测试：领 key 全失败 → `attempts` 有记录、`regions` 不变；`n` 每节只 +1；首节 `cached=true` 或 `late=false`。

---

## 6 批次 E/F（后续）

- E：全量 `events.jsonl` + `content.jsonl`，正常模式也建会话；默认传内容，音频单独开关。
- F：先补 journal owner/pid、per-output-path 锁、job spec、恢复守卫、power 断言，再拆 worker。

---

## 7 总验收与回滚

- 每批 `check.py` 全绿；A/B 批另跑 `tests/test_guards.py`、`tests/test_note_v2.py`、`tests/test_stream_asr.py`。
- A 批回滚：revert 五个文件；不改数据格式。
- B 批回滚：`CLASSLIVE_V6_FALLBACK=0`；Jev 重置可单独 revert。
- C 批回滚：保留旧键，删新键；看板双读。
- D 批回滚：revert `region_route/entry_launch`；状态文件向后兼容。

---

## 8 执行记录（2026-11-03 晚）

### 批次 A —— 已落地（未提交）

- `note_guards.py`
  - `_EQ` 收窄成符号四则 `+ − × *`；除法只走 `note_math._DIV`（少一套判据）。
  - 新增 `_result_text`（只认最后一个 `≈/=/→` 后的纯数字；`答案值/取点/程序值/support p=/最值`
    与尾巴缀字一律拒）、`_wrong_at`（按断言自己的小数位判错，`1.77` vs `1.767` 不算错）、
    `_locate_fields`（ctx 前缀 / 剥-CJK 前缀 / 原文逐字三把钥匙；命中长度并列的**全部**返回）。
  - `_correction`（返回修正点 span，不再直接改字）：收集全部候选，**恰好一个**「c 真错、重算值对得上」才改；
    邻字符、`is_sum_term`、`_DIV` 的 `d` 组三道闸。
  - 拆成 `correct_mismatches`（早跑；`algebra` 只改数字不缀括注；多字段全改或全不改；
    幂等）+ `annotate_mismatches`（链尾，只标没改掉的 ⚠）。
- `note_v2.py`：`checks_summary` 增 `n_fixed`（按 claim 去重）、`n_bad` 排除 `corrected`；
  `checks_headline` →「N 处已核（F 处已更正 · M 处对不上）」；`corrections_from_mismatch`
  带 `corrected/original/replacement`；过期 docstring 更新。
- `note_render.py`：附录分三档（修正 / 三道核回改 / 另有发现）。
- `note_html.py`：修正记录里已更正行写「✅ 三道核已更正：原写 X → Y」。
- `obsidian_writer.py`：`verify_numbers` 之后立刻 `correct_mismatches`（清理链之前）；
  🥉 行加「已更正」；`flagged` 只数未处理。
- `scripts/notes_replay.py` 镜像顺序；`scripts/note_eval.py` 的 `correction_audit`
  单列 `corrected`。
- 测试：`tests/test_guards.py` 新增 `C5Safety`（5 种改错形状 + 跨节同改 + algebra）；
  原有 C5 用例改走两步流水线。红证据：旧版在五种形状上分别改了 `2.89→2`、
  `30→15`、`14→12`、`30→8.3333`（留 `/3` 残渣）、把对的 `1.77` 改掉而留下 `2.89`。
- **验收**：`scripts/check.py` 全绿（115 文件）；`0.160−0.667=0.33 → −0.507` 端到端过；
  机制核+模型核同句 → `n_fixed=1`、`n_bad=0`、页头「1 处已更正」；二次运行幂等。
- **未跑**：11-03 那节真笔记的 review 数据不在本机（发件箱里），A4 的「真数据回放」
  只能等下一次有可复现会话时补。

### 批次 B —— 进行中（2026-11-03 深夜）

- **B2 Jev 重置**：`keypoints.reset_failure()`；`main.run` 抢到单实例锁后调一次。
  判据：`test_keypoints.t_reset_failure_clears`。
- **B3 v6→v5 回退**：`stream_asr.MODEL_FALLBACK="stt-rt-v5"`；`_drain` 见
  `model_not_available` → `_force_v5`；`_attempts` 见到 `_fatal + _force_v5` 立刻换 v5
  重连一次（`_v5_tried` 只试一次）；`metrics()` 增 `model_fallback`，`model` 报实际用的那个。
  `region_route._ws_probe` 把 `model_not_available` 记成 `model_error`（不是地区不可用）。
  判据：`test_stream_asr.t_transport_model_not_available_retries_v5`、
  `test_region_route.t_probe_model_error_not_region_down / t_ws_probe_marks_model_error`。
- **B1 线程退出**：红证据 —— 预算 0.3 s 返回、`chat` 睡 3 s 时进程退出 3.10 s。
  已修：`note_v2._DaemonExecutor`（daemon 线程、不登记 `_threads_queues`）+ `_cancel` 闸；
  复测退出 0.35 s。判据：`test_note_v2.ReviewBudgetExit`（子进程计时）。

**A 批复核修正（对抗验证代理）**：
  1. claim 去重改按**修正点**（字段 + c 区间，`corrected_id`）—— 不再按 claim 判等，
     `除以`/`乘以` 这类剥 CJK 会撞的坑随之消失（`_claim_in` 最终删掉）；
  2. `_locate_fields` 加「原文逐字」兜底（`-2-3=-1` 归一化后太短，旧门槛够不着 → 既不回改也不标 ⚠）；
  3. 错值后跟「是错的/不对/有误…」→ 不认候选（改完会把句子变成假话）；
  4. 对应新增/更新判据：`test_纯符号短式也能定位并回改`、`test_除以与乘以不是同一句`、
     `test_同段同式两处_否定语境不改`。

**A 批第二轮回核（代码评审代理，2026-11-03 深夜）—— 四个阻断项已修**：

1. **带单位的结果回改**（`50/100=50%` → `0.5%`）：`_correction` 见结果后跟
   `%‰分/成/折` 一律不认；`note_math._DIV` 同口径（不再为这类断言产 mismatch）。
2. **排版减号/点乘**（`15－225/15=0`、`2·225/15=30`）：`_SUM_CONNECTORS` 补
   `－ – — ﹣ · ⋅ ∙ ＊ ✕ ✖`；`_correction` 匹配前把变体 1:1 换成 ASCII（span 不漂）。
3. **同一句多条断言**（`5.3÷3=2.89，再算 7.2÷2=4.1`）：改成**两阶段**——
   先在原始正文上把每条断言的**修正点**（字段+c 区间）全部规划好，再统一回写；
   台账按 `corrected_id` 去重（同一修正点被两行引用只算一处，两个不同点算两处）。
4. **半入舍入**（`5.35÷2≈2.68`）：`_round_half_up`（Decimal, ROUND_HALF_UP）替换
   `round(float)`，`note_math` 与 `note_guards` 同口径。
另修：`repair_exercises` 的 keep 保留 corrected 行；`corrections_from_mismatch` 按
`corrected_id` 去重；`note_render` 的「已更正」数改从 `checks_summary` 派生；
`note_eval.correction_audit` 对 corrected 行**重新复核**（断言算得对却被改 → 记 `wrong`）；
回放的 `flagged` 改从最终 `checks_summary().n_bad` 派生。
新增判据：`test_带单位的结果不回改`、`test_排版减号的和式不回改`、`test_半入舍入不算错`、
`test_同一句两条断言各改一处`。

### 批次 C —— 已落地（未提交）

**新旧并存（旧键继续发）**
- `stream_asr.metrics()`：`reconnects` 旁增 `connect_attempts`（每次 `_session()` 进入即 +1，
  含领 key/握手失败）、`connections`（`_sess_n`）、`failed_sessions`（带死因的会话数）、
  `final_session`（最近一条会话记录，不受 `SESS_LOG_MAX` 截断）；`down_s` 旁增
  `reconnect_downtime_s`；`msg_gap_max_s` 旁增 `drain_gap_max_s`（同一实际口径：排空调用间隔）。
- `vad.Segmenter.diag`：`silence_cuts`（= 旧 `cuts`）、`total_cuts`（silence + hard）、
  `blind_dropped_blocks`（= 旧 `blind_dropped`）。
- `live_summary.stats()`：`chapters_attempted`（= 旧 `chapters`，尝试合成）、
  `chapters_written`（`ChapterWriter.append` 真写成才算）、`failed_atom_calls`、
  `failed_chapter_calls`、`finish_truncated`（拿不到锁 / 超时 / 异常 / 文件已关）。

**修采集 bug**
- `down_s` 连续失败：新退避开始前先把上一段收口（原来直接覆盖 `_down_since`）。
- `down_s` 收尾后继续涨：`stop()` / `_finish()` 收口掉线段；`metrics()` 在 stopped/finished
  时不再加「现在 - 上次断线」。
- `_detail` 每次 `_session()` 尝试先清空（上一课/上一会话的死因不再串味）。
- `fallback_detail` 改名 `last_close_detail`（语义：最近断线原因，不是「已退回」）；
  看板 `last_close_detail or fallback_detail` 双读。

**新采集**
- `region_probe.attempts`：独立字段（不塞 `regions`），记每次领 key（default 失败 /
  没配区 / 重复区 / 量完的错误），有界 `ATTEMPTS_MAX=8`，`snapshot()` 原样带出。
- `note_v2` 复习层：`obsidian_writer.note_stats()` → `blocks.review`
  `{review_total, review_ok, review_failed, review_generated}`；总块数从
  `on_progress("review", 0, N)` 取（全失败时 `review()` 返回 `{}`，没有 `chunk_stats`）。
- `keypoints.stats()` → `blocks.jev`：`{calls, fail}`（`ask_commandcode` 每发一次 +1，
  HTTP/网络/解析失败 +1）+ 原有 `last_failure()`；`reset_failure()` 一并清零。
- `report.json` 顶层 `report_version = 2`（`testmode.REPORT_VERSION`）。

**看板**
- `_report_summary` 补流健康：`reconnects / connect_attempts / connections /
  failed_sessions / final_why / down_s(新键优先) / skipped_s / dropped_s /
  msg_gap_max_s / drain_gap_max_s / backlog_max_s / model / model_fallback / endpoints_n`。
- `REPORT_BLOCKS` 补 `semantic_seal / jev / counts / review`；`feature_usage` 仍留顶层。
- `region.attempts` 限 8 条 + 每格限长/枚举化。

**测试**
- `test_stream_asr`：第二次掉线断言新键；`t_metrics_down_s_freezes_after_stop`（stop 后冻结）；
  `last_close_detail` 改名适配。
- `test_live_summary`：`chapters_attempted/written` + `finish_truncated` + 分层失败数。
- `test_vad_sentence`：静音断句 vs 总切段。
- `test_region_route`：default 领 key 失败写 `attempts`、重复区标记、snapshot 带出。
- `test_keypoints`：`stats()` 调用数/失败数 + reset 清零。
- `test_cl_admin_ui`：旧报告缺新字段 → `None`；`fallback_detail` 双读；新键优先；
  四块白名单 + 新计数。
- `test_testmode`：`report_version == 2`。

**C 批复核修正（对抗验证代理，2026-11-04）**

1. 🔴 致命会话不进账：`_attempts` 原来在 `_fatal` / `not err` 分支直接 return，
   `failed_sessions / final_session / sessions[]` 全是空的（报告假绿灯）。改成**先构造
   entry、记账、再早退**；`_stopped` 分支也记一条 `why=""`，`final_session` 才是
   「最后一节」而不是「最后一次失败」。
2. `_drain_events`：v5 兜底也被 `model_not_available` 拒时**无条件写 detail**
   （带模型名），不再只剩 v6 那条。
3. `live_summary` 增 `chapters_nonempty`（空壳章记录不算「有内容」）。
4. `_report_summary` 增 `endpoints_between_n`（v5/v6 对比量）。
5. `keypoints` 的 `_LAST_FAILURE` 也纳入 `_STATS_LOCK`（改 `RLock` 以便 `stats()` 重入）；
   `main` 在 `stats()=={}` 时**不建空 `blocks.jev`**。
6. 补判据：连续握手失败 `down_s` 不丢段（旧实现会 <0.35）、致命会话进 `failed_sessions`、
   `finish_truncated=True`（拿不到锁）、`note_stats()` 全失败/部分失败、
   `main.py` 真的写了 `note_block("jev"/"review")`。
7. 已改并部署（2026-11-04）：Worker 的 `STT_RT_MODEL` 改成 `stt-rt-v6`
   （`server/asr-worker/src/soniox-core.js:21`，`handler.js:393` 写进用量事件）。
   它只是**用量标签**：v5 兜底的那几节会被标成 v6（少数，可接受）。
   Worker 测试 116/116；`wrangler deploy` 版本 `3ecd22f1-a4ee-4204-8181-407cda3af863`；
   部署后 `POST /v1/soniox/key`（测试码 + probe）回 200 有 key、`asr.bldcam.page` 正常。
