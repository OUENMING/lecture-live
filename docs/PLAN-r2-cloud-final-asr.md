# R2 实施细则：定稿上云（3.9.0）

给**执行者**（Sonnet 子代理）看的逐文件改法。决定与证据在 `docs/PLAN-cloud-final-asr.md` §0、`docs/RESEARCH-cloud-asr.md`。
行号是 2026-10-11 的快照，**以函数名为准**，动手前用 grep 重新定位。

## 0. 已拍板（Owen 2026-10-11，第二轮）

| # | 决定 |
|---|---|
| E1 | **只做定稿 ASR 免 key**。翻译不变：有 DeepSeek key 走云端，没有就走本地 |
| E2 | 千问**只用免费额度、绝不付费**：按 `qwen3-asr-flash → qwen-audio-3.1-asr-flash → qwen-audio-3.0-asr-flash` 轮换；哪个模型回 `403 AllocationQuota.FreeTierOnly` 就永久跳过它；全用完 → Worker 回 503 → 客户端本地转写 |
| E3 | Owen 自己在百炼「免费额度」页**一键开启所有模型的「免费额度用完即停」**（账户设置，代理不碰）。⚠️ 没开的模型一旦扣费、账户欠费，**其他模型的免费额度也调不动**（官方 FAQ 原文） |
| E4 | Worker 由代理部署到 `asr.bldcam.page`（本机 wrangler 已登录）；key 从 `~/.classlive/{groq,qwen-cn}-token` 管道灌进 `wrangler secret put`，不回显；**部署前把配置和测试结果给 Owen 看，点头才执行** |
| E5 | 沿用 D2–D4：Groq 优先；默认开云端 + 首次告知 + 可关；本地 Whisper 降为可选 |

### 0.1 本轮现查的事实（2026-10-11，作为前提写进代码注释）

- **千问国内可调的英文同步 ASR**（用 Owen 的 key 逐个实测）：`qwen3-asr-flash`、`qwen-audio-3.1-asr-flash`、`qwen-audio-3.0-asr-flash` 能调通；`qwen3-asr-flash-2025-09-08` 等快照 404；`fun-asr-flash-2026-06-15` 要 `format` 参数，未调通 → **不进链**。
- **免费额度规则**（help.aliyun.com `new-free-quota` + `error-code`）：每个模型（含不同快照）额度独立；开「用完即停」后耗尽返回 **HTTP 403 + `code: AllocationQuota.FreeTierOnly`**，不扣费；限流是 **429**，与额度无关。
- **延迟**（爱尔兰实测，本轮）：千问吃 **WAV base64 时 11–23 s**，吃 **Opus 时 1.2–1.4 s**（偶发 5 s）→ **必须压缩后再发**。Groq WAV/Opus 都是 0.4–0.5 s。
- **编码**：`.app` 里**没有 soundfile、没有打包 ffmpeg**（ffmpeg 只在装了 Homebrew 的机器上有）。`soundfile 0.14`（wheel 自带 libsndfile 1.2.2）能写 `OGG/OPUS`，12 s 段编码 0.20–0.35 s、约 42 KB；Groq 与千问都接受（实测 200）。
- **数据留存**（写告知文案用）：Groq 推理请求**默认不留存**客户数据，仅「系统可靠性与滥用监控」最长 30 天，可开 ZDR 关掉（console.groq.com/docs/your-data）；阿里云百炼**不用于训练**，但「根据法规**会存储**调用数据」（help.aliyun.com/zh/model-studio/privacy-notice）。
- Cloudflare：Workers 免费档只有 **SQLite 后端的 Durable Objects**；免费 10 万 DO 请求/天。

## R. 审查后修订（2026-10-11 独立审查，**与下文正文冲突时以本节为准**）

审查原文要点与证据见本节每条括号里的 file:line。执行者先读本节，再读正文。

**接口与客户端（覆盖 C1/C2/C4）**
- R1. 接口改深：`FinalASR(local, draft, allow_cloud, *, post=None, encode=None, clock=time.monotonic)`；`load_final(final_model_dir: str, draft_asr, allow_cloud, *, preload_local: bool)` 不再收整个 argparse。`FinalResult` 只留 `text / source / logprob / ms / fell_back / steps: tuple[str, ...]`，测试模式要的「主模型原文 / 退化判决 / 兜底原文」收进一个 `trace: dict`。
- R2. `note` 改成 **`steps` 列表**，按发生顺序记每一步（如 `("cloud:timeout", "local:parakeet")`、`("cloud:groq",)`、`("off", "local:whisper", "degenerate", "local:parakeet")`），多步回退不丢信息。
- R3. **总截止时间**：httpx 的超时是按阶段的、不是总时长（connect+write+read 最坏约 14 s）。用单线程 `ThreadPoolExecutor` 提交请求，`future.result(timeout=剩余)` 卡总时长；超时就当失败走本地（请求在后台自然结束，httpx 自身 timeout 兜底）。平时截止 **6 s**；**收尾阶段**（`stopping` 已置位）截止 **2 s**。
- R4. **本地兜底的模型**：云端开着时**不预加载 Whisper**（省约 1 GB 内存），兜底用 Parakeet；云端关着（配置关 / `--engine local` / `CLASSLIVE_CLOUD_ASR=0`）且 Whisper 在 → 启动时加载 Whisper。⚠️ 代价：整节课断网时用的是 Parakeet 而不是 Whisper —— 交付时写进汇报给 Owen。
- R5. **云端回空串**：段长 ≥2 s 时补跑一次 Parakeet（约 0.36 s）；Parakeet 有字就用它（`steps` 记 `cloud-empty`）。防 Groq no_speech 规则 / 千问 NO_WORDS 误判静音时静默丢句。
- R6. `allow_cloud()` 再加一条：`--source file` 且 `--speed > 1` 时**默认关**（回放几秒就打满 Groq 全局 RPM 并烧千问免费额度）。
- R7. `CLASSLIVE_CLOUD_ASR=0` 时：不弹告知、**不写** `cloud-asr.json` 与 `install-id`、不发任何请求。所有探针 / 回放脚本默认设它为 0。
- R8. `transcribe_lp`：竞态只在 Parakeet 那条路（`main.py:1703` 锁外读 `last_logprob`，`partial_worker` 同时在用）。全仓读 `last_logprob` 的只有 `main.py:1693/1704` → 改完后**删掉** `last_logprob` 属性（不留死代码）。

**接线（覆盖 M1/M2/M3/M5、U1）**
- R9. `testmode.note_segment` **加 `@_safe`**（`testmode.py:438` 现在没有；它在 `main.py:1706` 的 try 内、排在 `_emit` 之前 —— 抛任何异常这一段都会被吞成「定稿失败」丢掉）。参数加 `steps`、`trace`。
- R10. 存 float 探针的条件改成：「最后一个本地主模型的结果空或退化」（不再是 `fell_back`）。**`tests/test_testmode.py:489-505` 那条用例要跟着改**（它传 `fell_back=True` 并断言存 float）。
- R11. `ready._local_path()`（`ready.py:165-170`）取的是「第一个可选模型」→ Whisper 改可选后它会指向 Whisper → 改成直接取 `models.QWEN.path`。`tests/test_ready.py:43-44` 的 `OPT` 按 key 取；加判据「Whisper 可选时本地引擎状态只看 Qwen」（审查者模拟：不改的话 21/21 → 19/21）。
- R12. `testmode.py:151` 按 `models.REQUIRED` 统计体积 → 改成统计「实际在盘上的模型」；`testmode.py:171` 的包列表与 `doctor.OPTIONAL`（`doctor.py:34`）加 `soundfile`；`testmode.py:666`「logprob 只有 Parakeet 会填」的说明改实话。
- R13. 告知文案的底线是「**云端转写开关 / `CLASSLIVE_CLOUD_ASR=0`** 才能保证音频不出本机」，**不要**说「用纯转录档就不出本机」—— 每节课都从 `both` 开始（`main.py:1247`），切到纯转录之前的几段已经发出去了。纯转录档照样不上云（R6 之外的 `allow_cloud` 判据不变）。
- R14. 终端里（`notice.alert` 走打印 fallback，`notice.py:127-132`）**不写 `acknowledged`** —— 否则终端启动一次就等于替 `.app` 用户同意了。终端每次启动只打一行提示；`.app` 弹窗点过才写。
- R15. M5 补全隐私表述位置：`README.md` 第 3、23 行；`docs/DESIGN.md` 第 12 行；`docs/HANDBOOK.md` 第 5、326 行；`ARCHITECTURE.md` 第 45、293 行（加上正文已列的）。动手前 grep「音频不出」「出本机」「不出机器」「全本地」再核一遍。
- R16. R30 的开关表要逐个写全名：`CLASSLIVE_CLOUD_ASR`、`CLASSLIVE_ASR_ENDPOINT`、`CLASSLIVE_ASR_TOKEN`（`test_audit_regressions.py:2540` 的简写只认 UPLOAD 一组）。`CLIENT_TOKEN` **不要**存成 `~/.classlive/*-token` 文件（R29 第二层会拿它比对仓库，报 `asr_endpoint.py`）。

**Worker（覆盖 W2–W5）**
- R17. 防滥用：令牌公开、`install_id` 客户端随便造 → 按 install 限流挡不住。加**按 IP 限流**（`cf-connecting-ip`，同上传 Worker `src/index.js:35`，每 60 s 60 次）；DO 里加**千问每日总量上限 3000 段**（约 9 节；超了千问整组当天不试 → 503 → 客户端本地），保护不可再生的免费额度。
- R18. Groq 额度还按**音频秒**算（2026-10-11 回源 console.groq.com/docs/rate-limits：`whisper-large-v3` 免费档 **20 RPM / 2K RPD / ASH 7.2K / ASD 28.8K**；单次不足 10 s 按 10 s 计 —— RESEARCH §2）：客户端加头 `X-Audio-Seconds`，DO 按 `max(10, 秒数)` 累计，到 95% 就不批 Groq。
- R19. 对冲：Groq 返回「退化」要当失败（reject）；对冲的那一路是**整条千问链**；每次上游调用的超时取 `min(该供应商上限, 剩余总预算)`；总预算从**进入 handler** 那一刻算起（含读 body、问 DO）。
- R20. 先看 `Content-Length` 超 512 KB 直接 413，再 `arrayBuffer()`。
- R21a. DO 免费档（developers.cloudflare.com/durable-objects/platform/pricing，2026-10-11）：10 万请求/天、**10 万行写入/天**、500 万行读取/天，超了该类操作直接报错 → 每段 1 次 `/plan` 写 1 行，约 300 节/天，够用；但 DO 报错时 Worker 要放行（已按「DO 挂了当 `{groq:true, qwen:CHAIN}`」实现）。
- R21. 部署后用 `wrangler tail` 实测一次 CPU 时间（免费档每请求 10 ms CPU；42 KB base64 编码）。

**验收（覆盖 V）**
- R22. `scripts/test_pipeline.py` 只用 Parakeet、不经过 `final_worker` → **不能**用来验 WER。新写 `scripts/final_asr_eval.py`：读 OCW 切段，逐段调 `final_asr.FinalASR.transcribe`，出 hyp 文件给 `ocw_eval.py` 打分，并统计 `source` / `steps` 分布与延迟。
- R23. `scripts/probe_testmode_report.py` 跑的是真 `main.main()`、没隔离 HOME → 加 `CLASSLIVE_CLOUD_ASR=0`（R7 保证不写配置），并把 HOME 隔离补上（同 `check.py` 的做法：临时 HOME + `UCD` 软链）。
- R24. 断网验收再加一条**慢失败**：`CLASSLIVE_ASR_ENDPOINT=http://10.255.255.1/`（不可达，连接挂住）→ 每段 ≤6 s 回本地、3 段后熔断、收尾 15 s 内冲刷完。
- R25. 低优先（旧问题、云端会放大）：收尾 15 s 超时后 `final_worker` 可能还卡在请求里，回来后对已关闭的 writer `_emit` → 核实并在 `_emit` 前判 `running`。

## 1. 执行规矩（每条都来自 CLAUDE.md / 事故史）

1. **开工前**加载 skill：`verification-before-completion`、`codebase-design`（建 `final_asr.py` 的接口时）；出现失败加载 `diagnosing-bugs`；改完跑 `simplify`。
2. **测试先红后绿**，贴出红、绿两次输出；每条新判据做一次变异（删掉对应分支，看它会不会红）。
3. **不跑 `main.py` / `cl`**（hook 拦着）；需要整链验证的交回主会话。
4. `sessions/`、`~/.classlive/` 只读不删；测试写端一律指向临时目录（`HOME` 隔离 + 参数注入），**同时**隔离读端和写端。
5. **不 commit、不 push、不 deploy、不改 VERSION / CHANGELOG**；做完交回。
6. 不回显任何 key；不把 key 写进仓库（R29 钉着）。
7. 运行环境用 `ClassLive.app/Contents/MacOS/python`；装包 `uv pip install --python ClassLive.app/Contents/MacOS/python …`。
8. 完成判据：`ClassLive.app/Contents/MacOS/python scripts/check.py --changed` 全绿，贴输出。

## 2. 改动总览与分工

| 包 | 内容 | 文件 | 依赖 |
|---|---|---|---|
| **W** | ASR Worker + 测试 | `server/asr-worker/`（新） | 无 |
| **C** | 客户端深模块 `final_asr.py` + 编码 + 熔断 + 测试 | `final_asr.py`（新）、`asr.py`、`requirements.txt`、`asr_endpoint.py`（新）、`tests/test_final_asr.py`（新） | 无（假服务器联调） |
| **M** | 接线：`main.py`、测试模式字段、Whisper 可选、开关登记、文档 | `main.py`、`testmode.py`、`models.py`、`ready.py`、`doctor.py`、`paths.py`、`README.md`、`docs/HANDBOOK.md`、`docs/DESIGN.md`、`CLAUDE.md`、`cloud_translator.py` 文件头 | C |
| **U** | 告知弹窗 + 面板开关 + 拍图探针 | `notice.py`（如需）、`entry_panel.py`、`entry_launch.py`、`scripts/probe_cloud_asr_consent.py`（新） | M 的 `paths.cloud_asr_config` |

W 与 C 并行；M 在 C 之后；U 在 M 之后（或与 M 同一个执行者）。

---

## W. `server/asr-worker/`（新 Worker，不动上传 Worker）

上传 Worker 是「只写、PUT-only」的安全立场，转写放进去会改变它的性质 → 单独一个 Worker。

### W1. 文件
- `wrangler.toml`：`name = "classlive-asr"`，`main = "src/index.js"`，`compatibility_date = "2025-10-01"`；**`routes` 写在任何 `[table]` 之前**（上传 Worker 踩过：写在 `[ratelimits.simple]` 后面会被解析成那张表的键，路由根本没建）：`{ pattern = "asr.bldcam.page", custom_domain = true }`。
  - Durable Object：`[[durable_objects.bindings]] name = "QUOTA" class_name = "Quota"`；`[[migrations]] tag = "v1" new_sqlite_classes = ["Quota"]`（免费档只能 SQLite 后端）。
  - Rate limiting：`[[ratelimits]] name = "PER_INSTALL" namespace_id = "2001"`，`limit = 30`，`period = 60`（需 wrangler ≥ 4.36）。
  - 文件头注释写部署三步：`wrangler secret put GROQ_KEY`、`wrangler secret put QWEN_CN_KEY`、`wrangler secret put CLIENT_TOKEN`、`wrangler deploy`。
- `src/index.js`（入口 + 供应商调用）、`src/quota.js`（DO）、`test.mjs`（`node --test`，假上游）。

### W2. 请求与鉴权
- `POST /v1/transcribe`；头 `Authorization: Bearer <CLIENT_TOKEN>`（**公开**令牌，同 `upload_endpoint.TOKEN` 的定位：只证明「是 ClassLive 在发」）；头 `X-ClassLive-Install: <install_id>`（客户端随机生成的 32 位 hex，见 C3）。
- body：`audio/ogg`（Opus），**上限 512 KB**（12 s 段约 42 KB；超了回 413）。其他方法/路径 404。
- 顺序：路径/方法 → 令牌（不对 401）→ `install_id` 形状（`/^[0-9a-f]{32}$/`，不对 400）→ `PER_INSTALL.limit({key: install_id})`（超了 429，带 `retry-after: 60`）→ 体积 → 转写。
- `const audio = await request.arrayBuffer()` 先读进内存（要发多次）。

### W3. 供应商链
1. 向 DO 要计划：`POST https://quota/plan` → `{groq: bool, qwen: [可用模型…]}`。DO 原子地判断 Groq 额度并**在批准时计数**。
2. **Groq**（`groq` 为 true 时）：`https://api.groq.com/openai/v1/audio/transcriptions`，multipart：`file=audio.ogg`（`audio/ogg`）、`model=whisper-large-v3`、`language=en`、`response_format=verbose_json`、`temperature=0`；**不传 `prompt`**（RESEARCH §13.3：喂术语 WER 10.1→15.2% 并编造整句）。超时 **2.5 s**（`AbortSignal.timeout`）。
   - 200：若 `segments` 非空且**全部** `no_speech_prob > 0.6` → 文本当空串返回（静音段的 "Thank you." 幻觉；**不升级到千问**，不白花额度）；若任一 `compression_ratio > 2.4` → 视为退化，进千问。否则返回 `text.trim()`。
   - 429：读 `retry-after`（秒，缺省 60），`POST /report {groq_cooldown_s}`，进千问。
   - 其他错误/超时：进千问（不报 DO）。
3. **对冲**：Groq 发出 **1.2 s** 还没回 → 并发启动千问第一顺位；`Promise.any` 取先成功的那个，另一路 abort。（平时 Groq p99 0.77 s，几乎不触发。）
4. **千问**（按 `qwen` 列表顺序，逐个试）：`POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`，头 `Authorization: Bearer QWEN_CN_KEY`、`X-DashScope-SSE: disable`。body 按模型分两种（照 `docs/experiments/cloud_asr_20261010/qcn.py` 的 `call()`，那份已实测）：
   - `qwen3-asr-flash`：`{"model", "input": {"messages": [{"role": "user", "content": [{"audio": "data:audio/ogg;base64,…"}]}]}, "parameters": {"asr_options": {"language": "en", "enable_itn": false}}}`
   - `qwen-audio-3.x-asr-flash`：`content: [{"type": "input_audio", "input_audio": {"data": "data:audio/ogg;base64,…"}}]`，`parameters: {"format": "opus", "sample_rate": "16000", "language_hints": ["en"]}`
   - 取文本：`output.choices[0].message.content[0].text`（兼容 `output.text`）。
   - 每个模型超时 **3.5 s**；整条链总预算 **5 s**（超出直接 503，客户端 6 s 截止前一定有回包）。
   - `403` 且 `code == "AllocationQuota.FreeTierOnly"` → `POST /report {exhausted: model}`（**永久**跳过），试下一个。
   - `400` 且 `code == "ASR_RESPONSE_HAVE_NO_WORDS"` → 空文本，成功。
   - `403` 其他 code（如欠费 `Arrearage`、权限）→ `POST /report {qwen_cooldown_s: 3600}`（一小时内整组不试），503。
   - 429 / 5xx / 超时 → 试下一个模型（不报 DO）。
5. 全部失败 → `503 {"error": "all_failed", "tried": [...]}`。成功 → `200 {"text", "provider": "groq"|"qwen", "model", "ms", "tried"}`。
6. 日志：只 `console.log` 供应商、模型、状态码、毫秒；**不记文本、不记音频**。

### W4. `Quota`（Durable Object，单实例 `idFromName("quota-v1")`）
- SQLite 存一行 JSON 状态：`{day: "YYYY-MM-DD"(UTC), groq_day: n, groq_min: {minute, n}, groq_cooldown_until: ms, qwen_exhausted: {model: iso}, qwen_cooldown_until: ms}`。
- `/plan`：跨 UTC 日自动清零 `groq_day`；`groq = now >= groq_cooldown_until && groq_day < 1900 && groq_min.n < 19`（Groq 免费 2000 RPD / 20 RPM，留 5% 余量，主动降级不等 429）；批准就 `groq_day++`、`groq_min.n++`。`qwen = now < qwen_cooldown_until ? [] : CHAIN.filter(m => !qwen_exhausted[m])`。
- `/report`：合并字段；`exhausted` 一旦写入不再清（换模型名或改 DO 名才会重置 —— 注释写明）。
- 不做查询/管理路由（YAGNI）：排查用 `wrangler tail`；要重置额度状态就改 DO 名（`quota-v2`）重新部署。
- `CHAIN` 常量只在 `src/index.js` 定义一处，DO 从请求里收（`/plan` body 带 `chain`），避免两处列表漂移。

### W5. 测试 `server/asr-worker/test.mjs`
用 `node --test`；把 `fetch` 换成假上游（按 URL 分派），DO 用内存假实现（同接口 `plan/report`）。用例（**每条先红后绿**）：
1. 令牌错 401；install_id 形状错 400；超 512 KB 413；GET 404。
2. Groq 200 → `provider=groq`，千问一次都没调。
3. Groq 全段 `no_speech_prob 0.9` → 空文本、千问没调。
4. Groq `compression_ratio 3.0` → 走千问。
5. Groq 429 `retry-after: 120` → report 收到 `groq_cooldown_s=120`、走千问。
6. Groq 1.5 s 才回、千问 0.3 s 回 → 取千问（对冲生效）；Groq 0.5 s 回 → 千问没被调。
7. 千问第一个 403 FreeTierOnly → report `exhausted`、第二个成功；下一次 plan 不再含第一个。
8. 千问 400 `ASR_RESPONSE_HAVE_NO_WORDS` → 200 空文本。
9. 千问 403 `Arrearage` → report `qwen_cooldown_s`、503。
10. 全挂 → 503 带 `tried`。
11. 总预算：上游都挂 10 s → Worker 在 ≤5.3 s 内回 503。
12. Quota：跨 UTC 日清零；第 1901 次 plan 的 `groq=false`；同分钟第 20 次 `groq=false`。

### W6. 部署（主会话做，Owen 点头后）
`cd server/asr-worker && npx wrangler@4 deploy --dry-run`（给 Owen 看）→ 三个 secret（`< ~/.classlive/groq-token` 管道，`CLIENT_TOKEN` 用 `secrets.token_hex(16)` 新生成并写进 `asr_endpoint.py`）→ `deploy` → 用 OCW 一段真音频 `curl` 验 200 → 用错令牌验 401。

---

## C. 客户端深模块 `final_asr.py`

### C1. 接口（调用方只需要知道这些）

```python
@dataclass(frozen=True)
class FinalResult:
    text: str
    source: str          # "groq" / "qwen" / "local-whisper" / "local-parakeet"
    logprob: float | None
    ms: float
    fell_back: bool      # 想上云但没用上云（网络/熔断/退化）
    note: str            # "" / "off" / "breaker" / "timeout" / "http-503" / "encode" / "degenerate" / "empty-local"…
    primary_text: str | None       # 第一个被尝试的来源的原文（给测试模式「主模型」那格）
    primary_degenerate: bool
    fallback_text: str | None      # 真回退时兜底模型的原文（给测试模式「兜底模型」那格）；没回退 = None

class FinalASR:
    def transcribe(self, samples: np.ndarray) -> FinalResult: ...   # 永不抛

def load_final(args, draft_asr, allow_cloud: Callable[[], bool]) -> FinalASR
```

- `allow_cloud()` **每段调用一次**（不是启动时定死）：它由 `main.py` 给，判据是「配置开 且 `--engine != local` 且 `trans["mode"] != "raw"` 且 `CLASSLIVE_CLOUD_ASR != "0"`」。上课中途切到「纯转录」→ 下一段就不上云（README「纯转录才是真离线」的契约不变）。
- 本地适配器：`--final-model-dir` 下有 Whisper → 用 Whisper；没有（或加载失败）→ **不再退出**，用草稿模型（Parakeet）。启动时 echo 一行说明用的是哪个。

### C2. 内部（调用方看不到）
- **编码** `_encode_opus(samples) -> bytes | None`：`soundfile.write(BytesIO, samples, 16000, format="OGG", subtype="OPUS")`。`import soundfile` 失败或写失败 → `None` → 本段走本地、`note="encode"`；`soundfile` 缺失时**只在第一次** echo 一行「缺 soundfile，定稿走本地；`cl update` 会补」。
  - ⚠️ 先做 **C0 量具**再定码率参数（见下）。
- **HTTP**：`httpx.Client`（模块内一个，线程安全），`POST asr_endpoint.endpoint()`，头见 W2；**截止 6 s**（`timeout=httpx.Timeout(6.0, connect=2.0)`）；**客户端不重试**（重试只在 Worker 一层）。
  - 200 → 取 `text`、`provider`；`is_degenerate(text)` 为真 → 本地重跑，`note="degenerate"`。
  - 429 → 熔断记一次失败、本地；其他非 200 / 异常 / 超时 → 同上，`note` 写状态码或 `timeout`。
- **熔断**：连续 3 次失败 → 打开 60 s（这期间直接本地，`note="breaker"`）→ 半开放 1 段试探；成功关闭，失败再开 60 s。时钟可注入（测试用）。状态变化时 echo 一行（「☁ 云端转写暂不可用，改用本地」/「☁ 云端转写恢复」），**不刷屏**。
- **本地**：`local_whisper.transcribe_lp(buf)`；空或退化 → `draft_asr.transcribe_lp(buf)`。云端拿到的空串（静音）**直接接受**，不再本地重跑。
- **`asr.py` 修竞态**：给 `ParakeetASR`、`WhisperASR` 加 `transcribe_lp(samples) -> tuple[str, float | None]`，在锁内算出 logprob 一起返回；`transcribe()` 改为 `return self.transcribe_lp(s)[0]` 并保留 `last_logprob`（别的调用方还在读）。`final_asr` 只用 `transcribe_lp`。（原来定稿线程读 `asr.last_logprob` 时，草稿线程可能刚覆盖它。）

### C3. 其他新文件
- `asr_endpoint.py`：仿 `upload_endpoint.py`：`ENDPOINT = "https://asr.bldcam.page/v1/transcribe"`、`TOKEN`（公开，部署时生成）；`endpoint()` / `token()` 允许 `CLASSLIVE_ASR_ENDPOINT` / `CLASSLIVE_ASR_TOKEN` 覆盖（测试用）。
- `paths.py`：`cloud_asr_config(*, root=None)` → `~/.classlive/cloud-asr.json`；`install_id(*, root=None)` → `~/.classlive/install-id`。
- `final_asr.install_id()`：读文件；没有就 `secrets.token_hex(16)` 写入（权限 600）；写失败用进程内随机值（不阻塞开课）。
- `final_asr.read_config()` / `write_config(enabled: bool)`：`{"enabled": true, "acknowledged": "<ISO 日期>"}`；文件缺失 = 未告知过、默认开。坏 JSON = 默认开 + 视为未告知。
- `requirements.txt`：加 `soundfile>=0.13    # 定稿上云前把段落压成 Opus（千问吃 WAV 慢 10 倍）`。

### C0. 量具（C 包第一步，花 Groq 免费额度约 15 分钟音频）
用 `~/.classlive/eval/ocw/`（`segs.json` + `segs.json.norm.npy` + `ref.json`）前 80 段，Groq `whisper-large-v3`，对比三种上传：WAV（基线）、soundfile 默认参数、soundfile `compression_level=0.6`（或使体积落在 30–45 KB/12 s 的值）。打分用 `docs/experiments/cloud_asr_20261010/ocw_eval.py` 的口径。**判据**：选「WER 与 WAV 差 ≤0.3 pp 里体积最小」的那个；把三行结果和选择写进 `docs/RESEARCH-cloud-asr.md` 新的 §15。脚本放 `docs/experiments/cloud_asr_20261010/sf_opus_ab.py`。

### C4. 测试 `tests/test_final_asr.py`（本机 `http.server` 当 Worker，**不联网**）
每条先红后绿 + 变异：
1. 200 正常 → `source="groq"`，本地适配器没被调。
2. 服务器睡 7 s → 6 s 内返回本地结果、`note="timeout"`、`fell_back=True`。
3. 503 → 本地。
4. 连续 3 次失败 → 第 4 段不发请求（服务器计数不变）、`note="breaker"`；时钟推进 61 s → 发 1 次试探；成功后恢复。
5. 云端回退化文本（`"... ... ..."`）→ 本地，`note="degenerate"`。
6. 云端回空串 → 接受空串，本地**没**被调。
7. `allow_cloud()` 返回 False → 不发请求、`note="off"`、`fell_back=False`。
8. 编码失败（注入 `encode=lambda s: None`）→ 本地、`note="encode"`。
9. 没 Whisper → 本地用草稿模型，`source="local-parakeet"`。
10. 请求头带 `Authorization` 与 `X-ClassLive-Install`，body 以 `OggS` 开头。
11. `install_id()` 在临时 HOME 下生成一次、第二次读到同一个值、权限 600。
12. `transcribe_lp` 返回的 logprob 与同一次调用对应（用桩 recognizer：两线程交替调，各自拿到自己的值）。

---

## M. 接线

### M1. `main.py`
- 启动（`load_asr` / `load_final_asr` 那段）：`asr = load_asr(...)` 照旧必需（失败照旧弹「模型没装好」退出）；**删掉** `asr_final = load_final_asr(...)`。`final = final_asr.load_final(args, asr, allow_cloud=lambda: _cloud_asr_ok())` 放到 `trans` 定义**之后**（`allow_cloud` 要读 `trans["mode"]`）。`_cloud_asr_ok()` 判据见 C1。
- 告知：`final_asr.read_config()` 显示「未告知过」→ 调 U1 的告知（终端里打印一段同样的文字）→ `write_config(enabled=<用户选择>, acknowledged=今天)`。**在开始采音之前**。
- `final_worker`：把 `_t_asr … _draft_text` 那段替换为：
  ```python
  r = final.transcribe(buf)
  text = r.text
  if tester is not None:
      tester.note_segment(buf, text, r.ms, r.source, logprob=r.logprob,
                          fell_back=r.fell_back, n_sentences=len(split_sentences(text)),
                          final_text=r.primary_text, final_degenerate=r.primary_degenerate,
                          draft_text=r.fallback_text, note=r.note)
  ```
  后面的 carry / `_emit` 逻辑**一字不动**。
- 状态行 / `☁ 引擎:` 那行旁边加一行 `☁ 定稿: 云端（Groq → 千问 → 本地）` 或 `💻 定稿: 本地 Whisper / Parakeet（原因）`。
- `import` 行去掉 `load_final_asr`（若无他用）；`is_degenerate` 若只剩 `final_asr` 在用也挪过去。
- `--final-model-dir` 的 help 改为「本地定稿模型目录（可选；云端不可用时才用）」。

### M2. `testmode.py`
- `note_segment(..., note: str = "")`：段记录加 `"note"`；`model` 现在是 `source` 的值。
- ⚠️ `_save_probe_float` 现在由 `fell_back` 触发：网络整节课挂掉时会**每段都存一份 float** → 改成只在 `note in ("degenerate", "empty-local")` 时存（原意是「主模型没出东西」的样本）。
- 报告 `asr` 段加 `source_pct: {source: 百分比}` 与 `note_counts: {note: 次数}`；`fell_back_pct` 保留（语义改为「想上云没上成」，在 docstring 写明）。
- `tests/test_testmode.py` 里传 `"whisper"` / `"parakeet"` 的用例照样能跑（`model` 只是字符串）；加一条 `note` 落盘 + 「网络回退不存 float」的用例。

### M3. Whisper 可选（D4）
- `models.py`：`WHISPER` 的 `required=False`，label 改「本地定稿 Whisper（可选，断网时更准）」。
- 连带逐个核：`ready.required_left` / `ready.py:168` 附近、`doctor.py`（可选显示 ⚪）、`entry_panel._download_required`（只下必需 → 新用户不再下 Whisper）、`update.py:1047`（**已经**按 `required` 过滤 —— 计划旧稿说要修的 bug 不存在，别重复改）、`testmode.py` 的模型尺寸统计、`tests/test_ready.py` 的 `REQ`、`tests/test_update.py` B 组、`tests/test_entry_panel.py`、`scripts/stream_eval.py`（`--final whisper` 时 `load_final_asr` 照用，不受影响）。
- `asr.load_final_asr` 的 docstring「必需模型」改成实话；它仍在抛（`final_asr.load_final` 负责接住）。

### M4. 开关登记（R30 钉着）
`docs/HANDBOOK.md` §6.1 加：`CLASSLIVE_CLOUD_ASR`（`0` = 本节强制本地定稿）、`CLASSLIVE_ASR_ENDPOINT`、`CLASSLIVE_ASR_TOKEN`（测试用）。

### M5. 文档（隐私承诺改实话）
`README.md` 第 13、520、521、664 行附近（grep「音频不出」「出本机」「文本会发给谁」）；`CLAUDE.md` 第 5 行；`docs/DESIGN.md` 第 194、418 行附近；`cloud_translator.py` 文件头「音频仍全本地」。口径：
> 定稿转写默认把**每段音频**（Opus 压缩，约 40 KB/段）经作者的服务器发给 Groq（美国；默认不留存，滥用监控最长 30 天）或阿里云百炼（中国；不用于训练，按法规会保存调用记录）。面板里关掉「云端转写」或用「纯转录」档，音频就不出本机。
文件地图（CLAUDE.md）加一行 `final_asr.py`；`docs/PLAN-cloud-final-asr.md` §3 顶部加指针指向本文。

---

## U. 告知与开关（UI —— 拍图给 Owen 看过才算完）

### U1. 首次告知
- 在 `final_asr.py` 里写 `consent_text()`（唯一文案来源，终端和弹窗共用）。
- 弹窗：`notice.alert("定稿转写会用云端", consent_text(), buttons=("好的", "只用本地"), fallback="好的")`（D3：默认开；问不到人时按「好的」并在终端打印同一段）。选「只用本地」→ `write_config(False)`。
- 只弹一次（`acknowledged` 存在就不再弹）。

### U2. 面板开关
- `entry_panel.py`：在「测试模式」开关**旁边**加「云端转写」开关，同一套写法（`on_cloud_asr` 回调 + 初值参数；`None` = 不建这颗开关）。**面板不写盘**，`entry_launch.py` 的回调调 `final_asr.write_config`。
- 遵守 `.claude/rules/appkit-ui.md`：新视图自己接管点击、加行为断言「回调真的触发」。
- `scripts/probe_cloud_asr_consent.py`：隔离 HOME，拍两张图（首次告知弹窗、面板开关开/关两态），**一个字节不写真盘**。

---

## V. 验收（主会话做）

1. `scripts/check.py --changed` 全绿；`node --test server/asr-worker/test.mjs` 全绿。
2. Worker 部署后：`scripts/test_pipeline.py` 拿 OCW 录音跑通（`CLASSLIVE_UPLOAD=0`），用 `ocw_eval.py` 打分，应接近 Groq 10.4% / 漏 0.7%；对照本地 12.0% / 2.5%。
3. **断网实测**：`CLASSLIVE_ASR_ENDPOINT=http://127.0.0.1:9`（拒连）跑 2 分钟 → 本地接手、字幕不断、只打一行熔断提示。
4. `scripts/probe_testmode_report.py`（碰了 `final_worker`）。
5. 拍图给 Owen：告知弹窗 + 面板开关。
6. 3.9.0 更新日志草稿给 Owen 看（升级须知必须写：要 `cl update` 补依赖 `soundfile`；新用户不再下 Whisper；告知内容）。

## X. 顺带查 bug 的清单（审查者逐条核，结论写回本节）

1. `final_worker` 的 `except Exception` 吞掉后 `busy["on"] = False` —— 新代码 `final.transcribe` 永不抛，但 `tester.note_segment` 新增参数若签名不匹配会被吞成「定稿失败」整节课空 → 加测试钉住签名。
2. `last_logprob` 跨线程竞态（C2 修）。
3. `allow_cloud` 读 `trans["mode"]` 是跨线程读 dict 的单键 —— CPython 下原子，写明即可。
4. 收尾冲刷：云端慢（最长 6 s）会不会让 `all_settled` 的 15 s 上限提前放弃最后几段？→ 核 `main.py` 收尾那段的超时与 `finalq` 积压量。
5. `asr_endpoint.TOKEN` 是公开常量 → R29 的 key 形状扫描不能误报它（32 hex，与上传令牌同形，已有先例）。
6. Whisper 改可选后，`doctor` / `ready` / 面板首启是否还有地方把它当必需（逐个 grep `required`、`REQUIRED`、`"whisper"`）。
