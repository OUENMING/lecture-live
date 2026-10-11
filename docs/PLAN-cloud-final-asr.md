# 计划：翻译空窗 + 定稿上云 + 草稿流式（2026-10-11，**未动代码**）

证据全在 `docs/RESEARCH-cloud-asr.md`（§ 号下文直接引用）。本文只写**决定**与**怎么改**。

## 0. 已拍板的决定（Owen 2026-10-11）

| # | 决定 | 依据 |
|---|---|---|
| D1 | 用户**零操作**：所有 key 放 Owen 的 Cloudflare Worker，客户端只认一个 URL | 用户注册 API 不是即开即用 |
| D2 | 定稿兜底链：**Groq whisper-large-v3 → 千问国内 qwen-audio-3.1-asr-flash → 本地** | §12.1：Groq 10.4%/0.42 s/免费约 6 节/天；千问国内 9.7%/1.6 s/≈$0.011 每节 |
| D3 | 默认开云端，**首次启动弹窗告知、面板一个开关可切回纯本地** | 上云打破 README「音频不出本机」 |
| D4 | 本地 Whisper **降为可选**，不再默认下载；已下载的老用户照用 | OCW 上本地兜底漏句与 Whisper 相当（§2） |
| D5 | DeepInfra **不进实时路径**（冷门模型排队几秒到几十秒，§12.2）；$5 留给课后精修/实验 | |
| D6 | Claude Code 的 $100 云会话额度**不用于运行时**；可在大改完成后跑一次云端代码评审 | 运行时和评测都依赖本机音频/模型/AppKit |

## 1. 分三版发，先修最痛的

| 版本 | 内容 | 预计改动 | 风险 |
|---|---|---|---|
| **R1（3.8.11）** | 翻译空窗：改草稿译文触发 + 草稿译文独立线程 | ~120 行代码 + ~80 行测试 | 低：不碰 ASR、不碰网络 |
| **R2（3.9.0）** | 定稿上云（Worker + 客户端 + 告知/开关 + Whisper 可选） | 客户端 ~350 行、Worker ~250 行、测试 ~250 行、文档 ~60 行 | 中：新网络依赖、隐私承诺变化 |
| **R3（3.10.0）** | 草稿换 Nemotron 流式（按 `PLAN-streaming-asr.md`）+ 句子级提前定稿 + 灰色未确认尾巴 | 见该计划 + ~150 行 | 中：+1 GB 内存 |

R1 与 R2 互不依赖，R1 可以先发。R3 的「句子级提前定稿」**依赖** Nemotron（Parakeet 草稿逐字一致率不够，§11.2 第 3 条）。

---

## 2. R1：翻译空窗

**根因**（§10.3）：① `draft_policy` 的「比上次多 ≥6 词」遇到 Parakeet 词数忽多忽少 + 10 s 窗口上限会饿死；② 草稿译文挤在 `final_worker` 里，定稿优先，约 27% 被挤掉。

### 2.1 `draft_policy.py`：新增 `novel` 策略并设为默认
- 判据：**相对上一次送出的文本**，新出现的词（按 `_tokens` 归一化后的多重集差）≥4 → 送；或距上次 ≥5 s 且新词 ≥1 → 送。仍保留 `MIN_GAP_S=2.5` 的下限，防刷。
- 接口变化：`should_translate(..., sent_text=...)`（上一次**送出**的原文，不是上一次草稿）。`legacy` / `clause` 保留，`CLASSLIVE_DRAFT_TRIGGER=legacy` 可退回。
- 为什么是「新词」不是「词数差」：与 LiveCaptions-Translator 的「变化次数 / 空闲时间」同思路（§11.1），对重转写的词数波动免疫。
- `main.partial_worker`（`draft_policy.should_translate` 调用处）：记 `last_sent_text`，在 `final_gen` 变化时清空（与现在重置 `last_len` 同处）。

### 2.2 `main.py`：草稿译文独立线程
- 新闭包线程 `draft_tr_worker`：只消费 `draftq`（仍 `maxsize=1`、只留最新），调 `translator.translate_draft`，结果进 `drafts_zh`。
- 从 `final_worker` 的空闲分支删掉草稿译文那段；`final_worker` 只剩「定稿 → carry 超时」。
- **过期丢弃**（LiveCaptions-Translator 的「新结果赢」）：每个草稿带单调序号 + 所属 `seg`；译文回来时若该 `seg` 已有定稿上屏（`final_gen` 已前进），丢弃并计数（不静默：进 `livemetrics` 一个 `draft_zh_stale` 计数）。
- 本地翻译引擎（MLX）有全局锁：两个线程会排队，不会并发 —— 行为与现在等价，不新增风险。云端 httpx Client 线程安全（`cloud_translator.py` 已共享一个）。
- 顺带修：`partial_worker` 里「它要抢同一把 ASR 锁」那段注释已过时（定稿走 Whisper 时两把锁不同），改成实话。

### 2.3 R1 追加（2026-10-11 实测后加入，见 RESEARCH §13）
- **carry 只看最后一句**（`final_worker`）：先 `split_sentences(text)`，`sents[:-1]` 立刻 `_emit`；只有 `sents[-1]` 无句末标点（不只看 `DANGLING_TAILS`）且 <25 词时才 carry。10-09 真课 43 处被劈开的句子是靶子。⚠️ 碰了收尾判据 `all_settled` 依赖的 `carry`，必跑 `probe_testmode_report.py`。
- **草稿送翻前剥掉结尾悬挂词**（复用 `DANGLING_TAILS`，零延迟）。
- **草稿翻译带上下文**：`translate_draft` 加领域 + 课程术语 + 上一句定稿（英/中），稳定内容放前面（前缀缓存）。⚠️ 会改译文 → 回放对比给 Owen 看过再定。

### 2.4 测试与验收
- `tests/test_draft_policy.py`：加 `novel` 的用例 —— 词数回落后仍能送（复刻 seg 91：19→13→17→22→23 词）；≥5 s 兜底；不足 2.5 s 不送。先写用例跑红，再实现。
- `scripts/draft_policy_sim.py`：加 `novel`，回放 10-09 真课，判据 = 「讲话中 ≥6 s 无新中文」总秒数（现 398 s → 目标 <60 s），以及送翻译次数（预计 +42%）。
- `scripts/probe_testmode_report.py`（碰了收尾相关线程，按 CLAUDE.md 要跑）。
- 默认闸门 `tests/test_audit_regressions.py` 全绿。
- **上线前人眼看一次**：用真课录音 `cl file` 回放 5 分钟，看中文是否闪得难受（「推送前先给看效果」）。

---

## 3. R2：定稿上云

> ⭐ **执行细则与 2026-10-11 第二轮决定（E1–E5：只做 ASR 免 key、千问只用免费额度轮换、Worker 由代理部署）在 `docs/PLAN-r2-cloud-final-asr.md`** —— 下面是旧稿，与细则冲突时以细则为准。

### 3.1 新模块 `final_asr.py`（深模块，定稿 ASR 的唯一入口）
接口只有一个：

```python
class FinalASR:
    def transcribe(self, samples: np.ndarray) -> FinalResult  # 永不抛
@dataclass
class FinalResult: text: str; source: str  # "groq"/"qwen"/"local-whisper"/"local-parakeet"
                   logprob: float | None; ms: float; fell_back: bool; note: str
def load_final(args, draft_asr) -> FinalASR
```
内部（调用方看不到）：
- `CloudClient`：PCM → **Opus 24k**（`ffmpeg` 已是依赖；实测上传 384→36 KB，§11.3）→ POST Worker；**整段截止时间 4 s**（Groq p95 0.6 s、千问 p95 2.2 s，留余量）；不在客户端重试 —— **重试只在一层做，就是 Worker**（子代理调研：AWS / Google SRE 都要求只在一层重试）。
- 熔断：连续 3 次失败或超时 → 本节课剩下的段走本地，60 s 后放 1 段试探（半开）；成功则恢复。沿用 `EngineRouter` 的「降级 + 后台探活」写法（`main.py` 里已有，翻译在用）。
- 本地适配器：有 Whisper 就用 Whisper，没有就用草稿模型（Parakeet，R3 后是 Nemotron）。
- 退化检测 `is_degenerate` 对云端结果同样适用；退化 → 本地重跑（与现在 `main.py` 的逻辑一致，挪进模块）。
- **两个适配器（云端 / 本地）都真实存在** → 这是一条真 seam，测试用假的 HTTP 服务器替掉云端。

`main.py` 的变化：
- 启动：草稿模型照旧必须；`asr_final = load_final_asr(...)` 换成 `final = final_asr.load_final(args, asr)`，**定稿加载失败不再退出**（D4）。
- `final_worker`：`text = asr_final.transcribe(buf)` 一段 + 退化兜底，换成 `r = final.transcribe(buf)`；`tester.note_segment` 用 `r.source / r.fell_back`。
- 顺带修掉 `last_logprob` 跨线程共享可变状态的竞态（现在草稿线程可能覆盖定稿线程要读的值）：改成返回值。

### 3.2 Worker：新建 `server/asr-worker/`（不改上传 Worker）
上传 Worker 是「只写、PUT-only」的安全立场，不往里塞转写。新 Worker：
- `POST /v1/transcribe`：请求体是 Opus 24k 音频（实测不伤准确度，RESEARCH §13.1）；头带安装令牌。入口先 `await request.arrayBuffer()`，音频留在内存可发多次。
- **不给 ASR 喂课程术语**（Groq 实测 WER 10.1→15.2% 且编造整句，RESEARCH §13.3）。
- Groq 用 `verbose_json`，`no_speech_prob`/`compression_ratio` 超阈值视为幻觉 → 换千问。
- 对冲：Groq 1.2 s 未返回就并发发千问，`Promise.any` 取先成功的（平时不触发，RESEARCH §13.4）。
- 可选第三顺位：Cloudflare Workers AI（binding，免 key），在千问之后、本地之前。
- 链：Groq（multipart，`whisper-large-v3`，`language=en`）→ 429 / 5xx / 2.5 s 超时 → 千问国内 `qwen-audio-3.1-asr-flash`（base64 data URI，`language_hints=["en"]`）→ 都失败回 503（客户端走本地）。
- 千问对静音段回 400 `ASR_RESPONSE_HAVE_NO_WORDS` → 当空文本返回，不算失败（§12）。
- Groq 额度：一个 Durable Object 记当日请求数与当前分钟数；RPD ≥1900 或本分钟 ≥19 → 直接跳到千问（主动降级，不等 429）。KV 是最终一致、不适合计数（子代理核 Cloudflare 文档）。
- 防滥用：沿用上传那套「公开安装令牌」+ Rate Limiting binding 按安装 ID 限流（每分钟 30）；月度付费上限由千问控制台预算告警兜底。
- 回包：`{text, provider, ms}`。
- Secrets：`GROQ_KEY`、`QWEN_CN_KEY`（`wrangler secret put`）。
- 免费档上限：10 万请求/天、每请求 10 ms CPU（等上游的时间不计）—— 一节课 322 次，远低于上限。
- 测试：`server/asr-worker/test.mjs`，假上游覆盖 Groq 200 / 429 / 超时 / 千问静音 400 / 双失败 503。

### 3.3 告知与开关（D3）
- `paths.py`：新增 `cloud_asr_consent()`（`~/.classlive/cloud-asr.json`：`{"enabled": true, "acknowledged": "2026-…"}`）。
- 首次启动：`notice.py` 弹一次说明（音频片段经 Owen 的服务器发往 Groq（美国）与阿里云（中国），不留存，可在面板关闭）。
- 课程卡片面板：一个「云端转写」开关（与「测试模式」开关同一套 sidecar 机制，`entry_launch.py` 写、`cl` 读）。
- `cl local`：同时强制本地定稿（现在只管翻译）。
- **措辞先渲染给 Owen 看再推。**

### 3.4 Whisper 可选（D4）
- `models.py`：`WHISPER.required=False`。
- 连带：`ready.required_left`、`tests/test_ready.py` 的 `REQ`、`testmode.py` 的尺寸统计、`doctor.py` 的「缺失」判定。
- 顺带修 bug：`update.py` 里检查缺失模型时没按 `required` 过滤 → 可选模型会冒出假的「下载模型」提示。
- README 里 `--final-model-dir ""` 能关定稿的说法现在就是错的（`load_final_asr("")` 会抛）→ R2 后变成真的，文案一起改。

### 3.5 文档（隐私承诺）
README 第 3、13、23、520、662–666 行及相关表格；`CLAUDE.md` 第 5 行；`docs/HANDBOOK.md`、`docs/DESIGN.md`；`cloud_translator.py` 文件头「音频仍全本地」。

### 3.6 验收
- 新 `tests/test_final_asr.py`：本机 HTTP 假服务器当 Worker —— 正常 / 超时 → 本地 / 503 → 本地 / 熔断开合 / 退化 → 本地 / Opus 编码失败 → 本地。**先红后绿**，并对每条做一次变异（删掉对应分支看它会不会红）。
- `scripts/test_pipeline.py` 拿 OCW 录音跑通；与 §2 表对比 WER/漏段（应接近 Groq 的 10.4% / 0.7%）。
- 断网实测：关 Wi-Fi 上 2 分钟课 → 本地接手、字幕不断。
- `probe_testmode_report.py`（收尾路径）。

---

## 4. R3：草稿流式 + 句子级提前定稿（R2 之后）
- 草稿换 Nemotron：完全按 `docs/PLAN-streaming-asr.md`（它的改动清单与 R2 有重叠：`models.py` 的 `MODELS` 元组、`fetch_model.py` 的通用化、`update.py` 的 `required` 过滤 —— **在 R2 里做一次，R3 复用**）。
- 句子级提前定稿：草稿里的完整句（句末标点）连续两次不变 → 当定稿翻译上屏；段尾真定稿到达后，只在与提前版**明显不同**（词级相似度 <0.85）时替换。换模型后先在真课上量「提前多久 / 被替换比例」再定阈值。
- 灰色未确认尾巴（LecSync / SimulStreaming 同款）。
- 「投机复用草稿译文」目前证伪（§12.4，相似度 ≥0.9 的段只有 3%），换 Nemotron 后再量。

## 5. 顺带的重构清单（只做与上面改动同一处的）
1. `final_worker` 是 `run()` 里的闭包、没法测 → R2 把定稿逻辑挪进 `final_asr.py` 后可测。
2. `last_logprob` 竞态 → R2 修。
3. `partial_worker` 过时注释 → R1 修。
4. `update.py` 不按 `required` 过滤 → R2 修。
5. `PARTIAL_MAX_S` 在 `scripts/stream_eval.py` 里有一份拷贝 → R3 一起收口。

## 6. 学到的写法（来源见 RESEARCH §11 与两份子代理报告）
- 重试只在一层（Worker）；客户端只有「截止时间 + 熔断 + 本地兜底」。
- 有界队列 + 只留最新 + **丢弃要计数**（WhisperLive 静默丢段是反例）。
- 新结果赢：慢的旧译文不许覆盖新的（LiveCaptions-Translator `TranslationTaskQueue`）。
- 已确认 / 未确认分开显示，而不是等稳定才显示（SimulStreaming、LecSync）。
- 评测用「回放 + 记发射时刻」，并加一条「定稿故意变慢」的回归（RealtimeSTT 的 `final_transcription_gap_regression`）。
