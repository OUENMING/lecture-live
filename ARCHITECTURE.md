# ClassLive 架构审查

> 审查日期 **2026-09-18** · 只读审查, 未改动任何代码 · 基线 = **工作区(未提交)状态**, 不是 HEAD
> 词汇表取自 `codebase-design`: **module / interface / depth / seam / adapter / leverage / locality**
> 所有文件路径与行号均在本仓库实际核对过; 凡是没核实的一律写「未确认」。

---

## ⚠️⚠️ 读之前先看这里：这是一份**带日期的历史快照**（2026-09-25 加）

**这座文档记录的是 2026-09-18 的状态，不是现在。** 已经有人把它当成当前状态读，
并据此得出**错误结论**（"可缩放的 Titled 版本没进 main" —— 实测在 main 上、
且在 v3.4.0/v3.5.0/v3.6.0/v3.6.3 **每一个 tag** 上）。

### 逐条现状（2026-09-25 实测核对）

| 风险 | 09-18 的状态 | **现在** |
|---|---|---|
| **A 未提交状态** | 13 条未提交，`polish.py`/`tests/` **不在 HEAD** | ✅ **已解决** —— 两者都已入库（`git ls-files` 可验） |
| **B 环境不可复现** | 无 `requirements.txt`/`pyproject.toml`/`uv.lock` | ✅ **已解决** —— `requirements.txt` 已存在（v3.4.0 加的） |
| **C GPU 驱动崩溃** | `IOGPUFamily` 断言 panic 历史 | ⚠️ **仍成立** —— 历史未消，`translator._configure_mlx` 仍是缓解措施 |
| **D `_use_cloud` 无锁** | 未声明的不变量 | ✅ **已解决** —— 已加 `_state_lock`，把"判断+翻转"并成一步 |
| **E 错误吞噬面大** | 大量 `except Exception` | ⚠️ **仍成立，且是刻意的**（上课不能崩）。后来加了 `CLASSLIVE_DEBUG=1` 开关 |
| **F 测试的形状** | R1/R4 **复刻**实现逻辑 | ⚠️ **仍成立** —— `CLAUDE.md` 的完成判据里也记着这条 |

### ⚠️ 正文里的 `file:line` 引用**全部不可信**

行号是 09-18 那版工作区的。抽查两条已漂移：

- 正文说 `main.py:145` 是 `EngineRouter._use_cloud` → **实测现在是别的注释**
- 正文说 `overlay.py:191-198` → **实测现在是面板 scrim 的注释**

**要引用具体代码，请重新 grep，别抄这里的行号。**

### 这份文档**仍然有用**的部分

第 1-2 节（这是什么、模块地图/深度表）和第 3 节之后那些**判断性**内容
（哪些风险优先级高、模块深度的评价、seam 的位置）——
**判断比快照耐用**。上面这张表只说明"哪几条已经处理掉了"，不代表其余内容过期。

---

## 1. 这是什么

ClassLive 是一个**跑在作者自己 Mac 上的实时英译中课堂字幕工具**。它在课堂现场采集音频(笔记本麦克风, 或 BlackHole 抓的系统声), 用本地模型实时把语音转成英文、逐句修正 ASR 听错、翻成中文, 以卡片式悬浮窗叠加在 PPT 上; 每句定稿**立刻**追加写入 `sessions/`, 下课后做一次整课精修, 生成一份「英文知识点详解 + 自测 + 术语表 + 逐字转录」的双层 Obsidian 笔记。使用者只有一个人(线下课用麦克风、线上课用系统声), 运行方式是**零参数一条命令 `cl`**; 单节课成本约 0.1 元(只发文本, 音频不出本机), 断网时自动退回本地模型, 保证课上不哑。它是 2026-09 在大学上课期间边用边写出来的工具, 已 MIT 开源并脱敏。

---

## 2. 技术栈与框架

| 层 | 选型 | 说明 |
|---|---|---|
| 语言/运行时 | Python 3.12(`uv venv .venv`) | ⚠️ **2026-09-25 更正**：`requirements.txt` **已有**（v3.4.0 起）；`pyproject.toml` / `setup.py` / `uv.lock` 仍无 |
| ASR | `sherpa-onnx` + NVIDIA **Parakeet-TDT-0.6B-v3 int8** | CPU, 实测 ~28× 实时; 模型在 `~/models/parakeet-tdt-0.6b-v3-int8` |
| VAD | `sherpa-onnx` + **Silero VAD** | 缺失时回退 `_EnergyVad`(能量阈值) |
| 本地翻译 | **`mlx-lm`** + `Qwen3-1.7B-4bit`(GPU) | 断网兜底; 已实测 3B/4B 必 OOM |
| 云端翻译 | **DeepSeek** OpenAI 兼容接口(`httpx`) | 默认 `deepseek-flash`; 必须显式 `thinking:{type:disabled}` |
| 音频采集 | `sounddevice`(回调式) + `ffmpeg`(子进程解码文件) + `numpy` | 16kHz mono float32 |
| UI | **PyObjC / AppKit / Quartz**(`NSPanel` + `NSVisualEffectView` + `NSScrollView`) | 无 GUI 会话时回退 `TerminalUI` |
| 存储 | 纯文件(Markdown + JSON), 无数据库 | `sessions/`、`<vault>/Lectures/`、`term_notes*.json` |
| 测试 | `unittest`(`tests/test_audit_regressions.py`, 无网络无模型) + `test_pipeline.py`(需真实模型) | 无 CI / lint / 类型检查配置 |

**入口与启动链**: `cl`(bash, 被软链到 `~/.local/bin/cl`; 解析符号链接后 `cd` 到仓库)→ 把课程号从 `.course` 读出来 → `exec .venv/bin/python main.py --source {mic|blackhole|file} --ui {overlay|terminal} --engine {auto|cloud|local} --vault ... [--course ...]` → `main.main()` 解析 argparse → `main.run(args)`。另有两条旁路入口: `cl last`(bash 直接 grep 最新 `sessions/*.md`)与 `python build_notes.py [course] [--rebuild]`(术语库构建期 CLI)。

---

## 3. 数据流与代码逻辑

运行时是**一条主循环 + 4 个后台线程**的推拉结构, 核心约束是「音频循环永不阻塞, 所有 UI 调用都在主线程」。

```
cl → main.run()
  ├ load_source()            capture.py      音源(最先打开, 失败就友好退出)
  │     └ _NormalizedSource  capture.py      PeakNormalizer 滑动峰值补电平
  ├ load_asr()               asr.py          Parakeet(内部带锁)
  ├ load_translator() ×2     translator.py / cloud_translator.py   本地 + 云端
  ├ ObsidianWriter()         obsidian_writer.py   立刻建 sessions/<日期>_<时>_<课>.md
  └ Segmenter()              vad.py          回调: on_partial / on_utterance_end
        │
        │  主线程 while running:  src.poll() → seg.accept(chunk) → drain() → ui.pump()
        │
        ├─ on_partial  → latest.put(buf)   [_Latest: latest-wins 槽]
        │     └ 草稿线程 partial_worker: asr.transcribe(最近 10s) → drafts
        │           └ 节流(≥2.5s 且新增 ≥6 词)→ draftq → 定稿线程 → drafts_zh
        │
        └─ on_utterance_end → finalq
              └ 定稿线程 final_worker:
                   asr.transcribe(整句)
                   → is_incomplete()? 挂 carry(上限 5s / 25 词)或拼接下一句
                   → split_sentences()(>25 词按逗号切)
                   → translator.fix_and_translate_stream() → _StreamParser 逐 token
                        ├ on_zh → ("zh", …) → streamq
                        └ on_en → ("en", …) → streamq
                   → ("final", en, zh, asr_raw) → streamq
                   → TermNotes.match() → ("terms", …) → streamq
                   → detect_proper_nouns() ≥2 次 → properq
                         └ 专有名词线程 proper_worker: lookup_term() → notes.add() → ("terms", …)
                   → 输入框回车 / 「讲一下」→ answerq
                         └ 问答线程 answer_worker: 冻结转录快照(list(finals), 只追加)
                               → EngineRouter.answer → CloudTranslator.answer_stream
                               → ("answer", delta) → streamq          ← 流式, 非紧急
                               → ("answer_done", q, text) → streamq
        │
        主线程 drain()  ← 唯一的 tag 分发点(无 else 分支, 漏注册即静默丢弃)
              "zh"/"en" → ui.stream_*          "terms" → ui.terms()
              "notice"  → ui.notice()          "final" → ui.finalize() + writer.append()
              "answer"/"answer_done" → ui.answer_delta() / ui.answer_done()(getattr 守卫)
        │
        退出(✕ / Ctrl+C / 文件播完): stopping.set() → seg.flush() → finalq.put(_QUIT)
              ⚠️ ✕ 只置 stopping, **不**直接 running.clear(): 收尾要先把 _QUIT 交给
                 final_worker 收摊, 而它的循环判据正是 running —— 提前清掉 = 没人取
                 哨兵, finalq 永远非空, all_settled() 永不成立, 每次退出卡满 15s
                 (实测 15.03s → 0.30s; 且修复前还会丢掉正在转写的那一句)
              → 等 all_settled()(队列 + busy + carry 四项)→ writer.close():
                    polish_entries()  整课二次精修(30 句/批, 以直播 EN 为基准)
                    → _review()        按 70 句分块生成知识点+自测 JSON
                    → _render_note()   双层笔记 → <vault>/Lectures/<日期>_<课>.md
```

**关键文件**: `main.py`(主循环 + 队列 + 线程)、`capture.py`(音源 + 电平)、`vad.py`(断句)、`asr.py`(转写)、`translator.py` / `cloud_translator.py`(修正+翻译, 同一套 interface)、`obsidian_writer.py`(落盘)、`polish.py`(二级精修)、`build_notes.py`(术语查表)、`overlay.py` + `transcript_view.py`(显示)。`transcript_view.py` 的核心是**固定行高**——它让"滚动偏移→行索引"变成 O(1) 除法, 回收池全靠这条; Phase 3 的答案接管也复用它(答案先按测量预折成固定行)。

---

## 4. 架构: 模块 · 接口 · 深度

| module(文件) | interface(调用方必须知道的事) | 深度 |
|---|---|---|
| `capture.py` | `load_source(source, path, speed) -> src`; src 必须提供 `poll() -> chunk｜None` / `is_done()` / `close()`; 块 = 1600 采样 float32 单声道; poll 非阻塞; **内部已套电平归一化**; 另 `load_file(path)`, `PeakNormalizer.process(chunk)` | **深**。3 个 adapter(mic 的 `CallbackSource`、系统声的 `CallbackSource`、`FileSource`)加一层 `_NormalizedSource` decorator, 全部藏在 3 个方法后面 |
| `vad.py` | `Segmenter(on_partial, on_utterance_end)`; `accept(chunk)` / `flush()` / `dur`; **回调必须立刻返回**; 隐含语义「什么时候算说完一句」 | **深**。梯级静音阈值 + 12s 硬上限 + 300ms pre-roll + 2 个 VAD adapter 全在身后 |
| `asr.py` | `load_asr(dir) -> asr`; `asr.transcribe(samples) -> str`; 线程安全; <0.1s 返回 `""` | **深而窄**。只有 1 个 adapter(Parakeet)→ 按定义这是**假想 seam**(docstring 自己说「便于替换成 whisper」) |
| `translator.py` | 实例: `warmup()` / `fix_and_translate_stream(en, ctx, on_zh, on_en) -> Result` / `fix_stream(en, ctx, on_en) -> str` / `translate_draft(en, on_zh) -> str`。**另有 7 个模块级自由函数**: `load_terms` / `course_term_list` / `course_title` / `core_terms` / `course_terms_path` / `select_terms` / `domain_block` | **混合**。实例 interface 深;术语部分是一组扁平自由函数, 大 interface、薄实现 → **浅** |
| `cloud_translator.py` | 与上表**逐方法同名**(鸭子类型, 无 `Protocol`/ABC), 外加 `probe() -> bool`; 模块级 `load_api_key()` | **深**(240 行实现藏在 4 个方法后), 但**契约靠约定维护**, 没有任何地方写下这个 interface |
| `main.py` | 对外的 interface 只有「命令行参数 + stdout」。内部: `EngineRouter`、`TerminalUI`、`_Latest` 三个 class + `is_incomplete` / `split_sentences` / `all_settled` 三个纯函数 | **run() 是一个 ~380 行、8 个 queue、4 个线程的闭包**。实现极深, interface 却不可调用——测不进流水线 |
| `overlay.py` | 构造参数 = 3 个回调(`on_quit`/`on_flag`/`on_translate`);方法 13 个(`show`/`close`/`add_draft`/`draft_zh`/`terms`/`stream_zh`/`stream_en`/`finalize`/`notice`/`pump`/`set_translating` + 按钮回调) | **浅**。interface 大, 且 AppKit 细节(panel/scrim/弱引用 `_targets`/菜单栏)与 interface 处在同一层 |
| `transcript_view.py` | 13 个方法(`set_content`/`set_items`/`set_live`/`set_frame`/`set_collapsed`/`is_at_bottom`/`scroll_to_bottom`/`following`/`mark_dirty`/`flush_if_due`/`tick`/…), 池化槽位靠构造参数注入尺寸 | **浅**。跟随状态机 + 视图回收确有价值, 但 interface 与实现**等比**, 测试无法从 interface 驱动 |
| `obsidian_writer.py` | `ObsidianWriter(vault, course, mode, api_key, model, glossary_path, polish, polish_model)`;`append(en, zh, flagged, raw)`;`count`;`close(ask) -> str`。**附带一个隐式文件格式契约**: `> [!abstract] HH:MM:SS[ ⭐ Exam Focus]` + `> **EN/ZH/ASR**: …` | **深**。7 参构造 + 3 个成员, 藏起: 实时落盘、解析自己的文件、二级精修、分块生成复习层、渲染双层笔记、同名冲突改名 |
| `polish.py` | `polish_entries(entries, api_key, model, course_terms, domain, batch, timeout, on_progress) -> list[dict]` | **深**(批处理切分 + 序号越界校验 + fail-soft 保原文), 但**未入库**;1 个 adapter(DeepSeek)→ 假想 seam |
| `build_notes.py` | 构建期 CLI(`build_notes.py [course] [--rebuild]`);运行期 `TermNotes()` → `match(sentence) / known() / add(term, note, type)`;`detect_proper_nouns(text, known, max_n)`;`lookup_term(term, key, model)`;`format_gloss(hits)` | **深**。`TermNotes` 把「三档筛选 + 词边界正则 + curated/auto 双文件合并 + 长词优先」藏在 3 个方法后 |
| `probe_scroll.py` | 无 interface, 独立脚本(import `overlay` / `transcript_view`) | 开发工具, **不在运行路径上**;README 记为「滚动行为验收探针」 |

**deletion test 结果**(逐个想象删掉会怎样):

- 删 `asr.py` → 模型文件发现 + 线程锁会散回复现 → **通过, 它挣到了自己的位置**
- 删 `capture._NormalizedSource` → 每个消费方都要记得自己调 `PeakNormalizer` → 通过
- 删 `EngineRouter` → 降级 + 探针逻辑在 3 个调用点(`_emit` 用到它的 2 个方法)复现 → 通过
- 删 `obsidian_writer._parse` → 解析逻辑会散进渲染与 `cl last` → 通过
- 删 `_Latest` → 只有 20 行, 内联即可, 复杂度不散 → **边界案例**(浅, 但无害)
- 删 `probe_scroll.py` → 没有任何运行路径断裂; 但它是唯一能验证滚动行为的手段 → 不删, 应标注得更清楚
- 删 `translator.select_terms` → 4 个调用方都要自己拼术语 prompt → 通过

---

## 5. Seam 与职责边界

| # | seam | 位置 | 真实 / 假想 |
|---|---|---|---|
| A | **音源** | `capture.load_source()` → `poll()/is_done()/close()` | **真实**(3 个 adapter: mic / 系统声 / 文件) |
| B | **VAD** | `vad._make_vad()` → `is_speech(chunk)` | **真实**(`SileroVad` + `_EnergyVad`), 但它是**私有** seam, 由 try/except 兜底选出来, 不是设计出来的 |
| C | **ASR** | `asr.load_asr()` → `transcribe(samples)` | **假想**(只有 Parakeet 一个 adapter) |
| D | **翻译引擎** | `fix_and_translate_stream` / `fix_stream` / `translate_draft` / `warmup` | **真实**(mlx 本地 + DeepSeek 云端)。证据: `tests/test_audit_regressions.py` 里已经出现 `FakeLocal` / `FakeCloud` —— 测试已经在用这个 seam 了, 却没有一个地方写下它的形状 |
| E | **UI** | `main.drain()` 调 `ui.*` | **真实**(`TerminalUI` + `Overlay`), 但契约不成文: `TerminalUI` 没有 `close`(靠 `getattr` 兜), `notice` 是本次 diff 才给 `TerminalUI` 补上的 |
| F | **LLM / HTTP 调用** | —— | **不存在**。见下 |
| G | **持久化** | `ObsidianWriter.append()` 写的 Markdown ↔ `_parse()` 读的同一份文本 | **真实的隐式 seam**: 文件格式同时是崩溃日志、解析输入、`cl last` 的显示格式(`cl` 里 grep `^> \[!abstract\]`) |
| H | **术语表载入** | `translator.py` 的 7 个自由函数 | **不存在独立 seam**, 逻辑散在 4 个模块里 |

**职责漏过 seam 的地方**(每条都在源码里核实过):

1. **`main.drain()` 既做 UI 路由又做落盘**。它按 tag 分发到 `ui.*`, 同时在 `"final"` 分支里调 `writer.append(...)` 并复位 `flagged`。一个本该与 UI 无关的主循环, 持有持久化职责。
2. **同一套 prompt 规则手抄了 4 份**。`translator.SYSTEM_PROMPT` / `FIX_SYSTEM`(中文)+ `cloud_translator.SYSTEM_PROMPT_CLOUD` / `FIX_SYSTEM_CLOUD`(英文)内容等价、例子完全相同(`max chocolate` → `macroscopic` 等)。改一条规则要动 4 处, 且天然会漂移。
3. **术语注入策略写在 docstring 里, 调用点却有两个**。`select_terms(..., always=...)` 的「课程术语全量注入」策略, 必须由 `translator._terms_context` 与 `cloud_translator._terms_block` 各自记得传 `always=self._course_terms` —— 本次 diff 正是改这件事, 于是两处都要改。
4. **`obsidian_writer` 是后处理流水线的实际协调者**。`close()` 里晚导入 `translator` / `polish` / `build_notes`(为绕开循环依赖), 依次跑精修 → 复习层 → 渲染。名字叫 writer, 干的是 pipeline。
5. **同一个概念的默认值有两个**: `main` 的 `--context` 默认 **5**, 而 `translator.load_translator` / `CloudTranslator` 的 `max_context` 默认 **2**。
6. **`load_api_key` 住在 `cloud_translator.py`, 却被 `build_notes.py` 导入** —— key 的读取策略跨了两个不相关的模块。

---

## 6. 摩擦与深化机会(按 收益/成本 排序)

### ① 抽出唯一的 LLM 调用 module —— 目前 **4 处手写 httpx**, seam F 不存在 【Strong】

- **module**: 现在没有;散布在 `cloud_translator._stream_chat`(SSE 流式)、`obsidian_writer._call_review`、`obsidian_writer._overview`、`polish._chat`。
- **为什么浅**: 这 4 处各自重复了同一组事实——base URL、Bearer 头、`thinking:{type:disabled}`、timeout、`response_format:json_object`、JSON 解析与容错。每处实现都薄(十几行), 但 interface(调用方必须知道"要关思考模式""要自己 raise_for_status")却和实现一样厚。
- **更深的 interface**: `LLMClient(base_url, api_key, model)` 提供两个方法——`stream_chat(messages, max_tokens, temperature, on_delta) -> Iterator[str]` 与 `chat_json(messages, schema_hint, max_tokens) -> dict`;把重试、超时、关思考、JSON 容错、未来可能的缓存都藏在身后。
- **payoff**: locality —— 换 provider、加超时策略、加重试只改一处;testability —— 现在这 4 条路径**一条都测不到**(测试刻意绕开网络),有了 seam 就能注入 fake adapter 跑通精修与复习层的解析;leverage —— `polish` 与 `_review` 免费获得重试与错误分类。这个 seam 已经有第二个 adapter 的雏形(本地 MLX), 不是凭空的抽象。

### ② 把 `run()` 的流水线收成一个可测 module 【Strong】

- **module**: `main.run()`。
- **为什么浅(准确说: 深实现 + 不可用 interface)**: 380 行闭包, 8 个 queue、4 个线程、5 份在途状态(`busy`/`carry`/`final_gen`/`flagged`/`translating`), 对外只有 argparse 与 stdout。三个纯函数(`all_settled`/`is_incomplete`/`split_sentences`)是**为了可测才抽出来的**, 但真正的缺陷都在**它们被怎么调用**上 —— 这是典型的 locality 缺失。
- **证据**: `tests/test_audit_regressions.py` 的 R1 测试**用假线程重新实现了一遍收尾循环**(注释写着「与生产一致」「逻辑级验证」), 而不是驱动真实的那一个;R4 的一条测试同样**复刻**了 `close()` 里的重名判断。测试与实现同源 → 实现改了测试依然绿。
- **更深的 interface**: `Session(source, asr, translator, ui, writer, glossary, ...)`, 依赖全部从构造参数传入;方法 `pump_once() -> None`、`stop_and_flush(deadline) -> FlushReport`、`state() -> SessionState`(busy/carry/pending 计数)。
- **payoff**: 15s 冲刷截止、carry/busy 接棒竞态、✕ 与 Ctrl+C 的等价性, 都能用 `FileSource` + fake ASR/translator 在**毫秒级**无模型环境下断言 —— 这三样正是最近五次回归的主角。这是本仓库测试性价比提升最大的一步。

### ③ 把 glossary / 术语检索收成一个 module 【Worth exploring】

- **module**: `translator.py` 的 7 个自由函数 +(部分)`build_notes.collect_terms`。
- **为什么浅**: interface 大(`load_terms` / `course_term_list` / `course_title` / `core_terms` / `course_terms_path` / `select_terms` / `domain_block` 七个名字), 而每个实现都只有几行;调用方 4 个(main、两个 translator、polish/obsidian)。更糟的是 interface 里漏了**约定**: 文件必须叫 `glossary/<课号>.txt`、允许后缀匹配、不许放单字母词。
- **更深的 interface**: `Glossary.load(path, course) -> Glossary`, 暴露 `title`、`for_prompt(sentence) -> str`、`terms`;把后缀匹配、去重、单字母排除、全量 vs Top-3 策略全部收进实现。`translator` / `cloud_translator` / `polish` / `obsidian_writer` 各拿一个实例, 而不是各拼一次字符串。
- **payoff**: 文档里记过的「`cl course 10202` 静默落空」类 bug 只可能在一处发生;`select_terms` 的策略变更(本次 diff 的 Top-3 → 全量)从"改两个调用点"变成"改一个方法"。

### ④ `ObsidianWriter` 的「文件即介质」改成显式 interface 【Worth exploring】

- **module**: `ObsidianWriter` 的 `append()` ↔ `_parse()` 往返。
- **为什么浅**: 模块**把自己的输出文件当作下一次调用的输入**(append 写 Markdown → close 读回来 parse), 而这份格式同时被 `cl last` 用 grep 依赖。三种角色共用一份文本, 没有任何一个 interface 声明它, 但任何一处改格式都会同时打断另外两处。
- **更深的 interface**: 内存里持有 `entries: list[dict]`, 文件只是它的一种**渲染**(而不是介质);或者反过来, 把 `SessionLog.parse(path) -> entries` 提成正式 interface, 让 writer 与 `cl last` 都走它。
- **payoff**: 格式漂移这一类 bug 消失;`_parse` 变成可单测的纯函数;`cl last` 不必再 grep。

### ⑤ UI seam 正式化, 并把落盘职责交还给流水线 【Worth exploring】

- **module**: `main.drain()` + `TerminalUI` / `Overlay`。
- **为什么浅**: 两个 UI adapter 靠 7~9 个同名方法维持鸭子类型, 但契约不成文——`close` 缺失要 `getattr` 兜, `notice` 是补出来的;`drain()` 还顺手落盘。
- **更深的 interface**: 用 `Protocol` 写下 UI adapter 的契约(含 `close()` 可选)、把 `writer.append` 与 `flagged` 复位移出 `drain()`。
- **payoff**: `overlay.py`(34KB, 无法无头测试)之外的一切都能用 fake UI 驱动;`main` 不再同时知道 UI 与存储。

### ⑥ 收窄 `overlay.py` / `transcript_view.py` 的 interface 【Speculative】

- 两个模块各 13 个方法, interface 与实现等比;`probe_scroll.py` 这套自制探针之所以存在, 正是因为**没有可以从 interface 驱动的入口**。更深的形状是 `render(state: ViewState)`, 把布局与滚动状态机关进实现内部。列为 Speculative: AppKit 的约束(主线程、弱引用 target、run loop 让让)使收益不确定, 且这是全仓最不愿意动的一块。

---

## 7. 风险与债务

> ⚠️ **本节是 2026-09-18 的快照，不是现状。** 里面的 `git status` 输出、行号、文件
> 计数都已经过期 —— 例如下面提到的 `?? polish.py` / `?? tests/` **早已提交进 HEAD**
> （`git ls-tree -r HEAD` 可验），别照着它判断"仓库缺文件"。
> **要看现状请现查**：`git status --porcelain` / `git log --oneline -15`。
> 本节保留的是**判断**（哪类风险值得优先），判断比快照耐用。

### A. 未提交状态是本项目**最大的单个风险** —— ✅ **已解决（2026-09-25 核实）**

> **这一节说的是 2026-09-18 的状态。现在不成立了。**
> `polish.py` 与 `tests/test_audit_regressions.py` **都已入库**
> （`git ls-files --error-unmatch` 可验），下面是历史记录，保留它是为了说明
> "这个风险当时有多具体"。**别据此判断当前状态** —— 见文首的横幅。

审查起始时 12 条未提交;审查进行中新出现第 13 条。`git status --porcelain` 实测:

```
 M README.md          M main.py             ?? docs/PLAN-ai-explain-qa.md   ← 审查期间新增
 M build_notes.py     M obsidian_writer.py  ?? polish.py
 M capture.py         M overlay.py          ?? tests/
 M cl                 M translator.py
 M cloud_translator.py M docs/DESIGN.md
```

- 已跟踪文件: **636 insertions / 157 deletions**(10 个文件)。未跟踪: `polish.py`(123 行)、`tests/test_audit_regressions.py`(269 行)、`docs/PLAN-ai-explain-qa.md`(229 行)。
- **HEAD 还是 v3.2, 工作区已经是 v3.3。** `git ls-tree HEAD` 实测: HEAD 里**没有** `polish.py`, 也**没有** `tests/`。
- 后果(具体): 任何人 clone `OUENMING/lecture-live` 后 ——(1) 拿不到 R1–R5 回归测试;(2) `obsidian_writer.close()` 里那句 `from polish import polish_entries` 在 `try/except` 里, 所以**精修会静默失效**, 不报错、不是降级提示, 就是没有了 —— 而 README 与 DESIGN 已经把它写成一项功能。文档与代码不一致, 且不一致的方向是"看起来能用、实际不生效"。
- `docs/PLAN-ai-explain-qa.md` 生成于 **2026-09-18 01:59**(审查进行中)。它是一份写给"新会话直接开工"的执行计划, 正文带大量 `file:line` 引用。我抽查了 6 条(`main.py:301`、`main.py:521-550`、`main.py:145`、`overlay.py:191-198`、`cloud_translator.py:125`、`translator.py:373`)——**当前工作区下全部命中**。但要注意: 这些行号只对"这一版未提交的工作区"成立, 一旦这批 diff 被提交或继续编辑, 行号就会漂移(`translator.py:373` 恰恰落在本次被改动的文件里)。
- 建议(不代为执行): 分三个 commit 落地 ——(1) 引擎降级探针 + 收尾冲刷 + `tests/`(main / overlay / capture / cl / tests);(2) 二级精修与双层笔记(polish.py + obsidian_writer + translator + cloud_translator + build_notes);(3) 文档(README / DESIGN / PLAN)。先落 `polish.py` 与 `tests/` —— 它们最容易被漏掉, 而漏掉的代价最高。

### B. 运行环境不可复现 —— ✅ **已解决（2026-09-25 核实）**

> `requirements.txt` **已在仓库里**（v3.4.0 加的）。下面是历史记录。
> ⚠️ 但**它仍然没有 lock 文件**（`uv.lock` / `pyproject.toml` 都没有，实测），
> 依赖是"下界"而非锁定版本 —— 这一半的风险还在，见文件头 `requirements.txt` 自己的说明。

仓库里没有 `requirements.txt` / `pyproject.toml` / `setup.py` / `uv.lock`(实测四者皆无), 依赖只存在于 `docs/DESIGN.md` 的安装片段里。换机、重装、或一年后回来看, 装不回来。对一个要开源的仓库来说, 这是比任何模块划分都更靠前的债。

### C. GPU 驱动崩溃(可用性风险) —— ⚠️ **仍成立（2026-09-25 核实）**

`docs/DESIGN.md` 记录 2026-09-10 本程序触发过一次 `IOGPUFamily` 驱动断言**内核崩溃**(非 OOM)。缓解措施已写在 `translator._configure_mlx`(`set_cache_limit(2GB)` + 不主动 `clear_cache`), 但根因指向上游 MLX 的已知 bug 族。一个正在上课用的工具带着内核崩溃风险, 是这份债里唯一会直接打断课堂的一条。

### D. `EngineRouter._use_cloud` 无锁 —— ✅ **已解决（2026-09-25 核实）**

> 已按当时的建议**加了锁**：`self._state_lock`（`main.py:162`），
> 并把"判断 + 翻转"并成一步（`with self._state_lock:`，`main.py:172`）。
> 下面保留原判断，因为它解释了**为什么**需要这把锁。

`main.py:145` 起, 该布尔被主线程/定稿线程读、被探针线程写(plan 文档也标了这一点)。CPython 下 bool 赋值实际是原子的, 所以**未确认**它造成过真实故障;但这是未声明的不变量 —— 一旦 `_start_probe` 将来加了别的状态, 就会变成竞态。建议标注或加锁。

### E. 错误吞噬面很大(刻意的) —— ⚠️ **仍成立（2026-09-25 核实）**

全仓大量 `except Exception` + `# noqa: BLE001`。这是深思熟虑的选择(上课不能崩), 但它也把**配置错误**吞成"看起来正常": 文档里记过的「`glossary/10202.txt` 静默落空、只加载到公共术语」正是这种形状。现状是"用注释和文档记账", 而非用类型或告警记账。

### F. 测试的形状 —— ⚠️ **仍成立（2026-09-25 核实）**

`tests/test_audit_regressions.py` 的 R1–R5 全部是**逻辑级**回归, 无网络无模型, 毫秒级 —— 这点很好。但其中至少两条(R1 的收尾循环、R4 的重名判断)**复刻**了实现逻辑而不是调用它。测试与实现同源时, 实现改了测试不会红 —— 这是本仓库最具体的 locality 缺口(对应机会 ②)。

### G. 仓库卫生

- `sessions/` 共 30 个文件, 其中 **14 个是 `*_TEST.md`** 测试残留。**不建议删**(既定规矩: `sessions/` 只读, 曾因误删丢过一节真实课堂记录), 仅知悉。
- `probe_scroll.py` 在运行路径之外, README 有一行说明, 但容易被误认为死代码。
- 根目录有 `__pycache__/`(已被 `.gitignore` 覆盖)。

### H. 隐私 / 脱敏(现状良好)

`.gitignore` 覆盖 `.deepseek_key` / `.course` / `glossary/` / `term_notes.json` / `term_notes_auto.json` / `term_notes*.bak` / `sessions/`;`git ls-tree HEAD` 确认 HEAD 树里**不含**这些路径。`glossary.example.txt` 用的是占位课号(`YOUR-COURSE-101`), 三次脱敏 commit 已清掉文档与注释里的真实课号。**音频从不落盘**(`capture.py` 只读不写), 只有文本出本机。

### I. 默认值分歧

`--context`: `main.py` 默认 5, `translator.load_translator` / `CloudTranslator` 默认 2。同一概念两个默认值, 直接调库的调用方(测试、脚本)会得到与 CLI 不同的行为。

---

## 8. 一页速览

**读这行就够**: 一个单人用的实时字幕工具;`main.run()` 是 380 行闭包 + 4 线程 + 8 队列的流水线, 其余模块大多**深而干净**;真正的债在「流水线不可测」「4 处手写 LLM 调用」「术语逻辑散成 7 个自由函数」,以及**整套 v3.3 改动 + 精修模块 + 测试还躺在工作区没提交**。

| 文件 | 角色 | interface(记住这句就够) | 改它之前须知 |
|---|---|---|---|
| `cl` | 一键启动器(bash) | 5 个子命令 → `main.py` 参数 | `.course` 无换行;`${ARGS[@]+…}` 是为 `set -u` 兜底 |
| `main.py` | 流水线 + 路由 + 线程 | 只有 argparse/stdout;内部 `EngineRouter`、`drain()` | **新增 streamq tag 必须同时改 `drain()`, 否则静默丢弃**;`busy`/`carry` 是冲刷判据的命脉, 新 worker 不要碰 |
| `capture.py` | 音源 + 电平 | `poll()/is_done()/close()`, 块=1600 采样, 已归一化 | 队列满时**丢最旧**, 别改成丢新的 |
| `vad.py` | 断句 | `Segmenter(on_partial, on_utterance_end).accept(chunk)` | 回调里不要做重活;静音阈值**不得低于 0.45s** |
| `asr.py` | 转写 | `transcribe(samples) -> str`, 线程安全 | 内部有锁;草稿与定稿线程会抢它 |
| `translator.py` | 本地修正+翻译 | 4 个实例方法 + 7 个模块级术语函数 | 术语函数被 4 个模块共用;`max_context` 默认 2 而 CLI 是 5 |
| `cloud_translator.py` | 云端同接口 | 与上同一套 + `probe()` | 必须关 `thinking`;有回显重试(重试**刻意不回调 UI**) |
| `obsidian_writer.py` | 落盘 + 后处理流水线 | `append()` / `count` / `close(ask)` | 它**读回自己写的文件**;格式同时被 `cl last` grep |
| `polish.py` | 二级精修(**未入库**) | `polish_entries(entries, …) -> list[dict]` | 输入必须同时给 EN 与 ASR, EN 是基准;失败保留原样 |
| `build_notes.py` | 术语库 + 运行时查表 | 构建 CLI;`TermNotes.match()/known()/add()` | 匹配用词边界不用子串;别放单字母词 |
| `overlay.py` | 悬浮窗 | 3 个入参回调 + 13 个方法 | AppKit 弱引用 target;穿透只能从菜单栏切 |
| `transcript_view.py` | 滚动 + 池化 | 13 个方法, 尺寸靠构造注入 | 收回态占 99% 使用时间, 别做两套渲染器 |
| `tests/test_audit_regressions.py` | 回归(**未入库**) | `python tests/test_audit_regressions.py` | 无网络无模型;R1/R4 复刻了实现逻辑 |
| `test_pipeline.py` | 端到端 | `python test_pipeline.py <m4a> [start] [dur] [speed]` | 需要真实模型 |
| `probe_scroll.py` | 滚动探针 | 独立脚本 | 不在运行路径上 |

**Top 3 深化机会**: ① 抽出唯一的 LLM 调用 module(消掉 4 处手写 httpx, 让精修/复习层第一次可测)→ ② 把 `run()` 收成可注入依赖的流水线 module(让 15s 冲刷、carry/busy 竞态、✕ 与 Ctrl+C 等价性变成毫秒级断言, 不再靠复刻逻辑)→ ③ glossary 收成一个 module。

**最大风险**: 13 条未提交, 其中 `polish.py` 与 `tests/` **完全不在 HEAD**, 而 README/DESIGN 已把精修写成功能 —— 全新 clone 会静默少掉一整块功能与全部回归测试。

> ⚠️ **2026-09-24 更正**: 上句**已不成立**。`polish.py` 与 `tests/test_audit_regressions.py`
> 当时确实还是未跟踪的 `??`，但随后已被提交 —— `git ls-tree -r HEAD` 两个都能查到，
> 全新 clone 拿得到。此处保留原文是为了不改写审查当天的记录，**请不要据此判断现状**。
