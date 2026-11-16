# 任务单：桌面体验线 + 重构 + 网页版

2026-11-14 · 给实现的 AI 照着做的逐张任务单。**一张都还没开工。**
方向、边界、闸门在 `PLAN-web-cloud-20261114.md`；事实与来源在 `RESEARCH-web-cloud-20261114.md`（下文的 A6、F9、§6 都指它）。
行号是 commit `3fef625`（3.11.3）上的，动手前用函数名重新定位。

## 怎么用

派一张活 = 把「通用规矩」整节 + 那一张任务单原样交给实现的 AI。一次只派一张；有「前置」的先确认前置已验收。
标 **〔作者做〕** 的步骤要真跑程序或要凭证，实现的 AI 写好脚本和说明后停下，交给 Owen。

## 通用规矩（每张都适用）

**开工前读**：`CLAUDE.md`（文件地图 + 雷区）、`docs/HANDBOOK.md` §3、本张任务单点名的文件头。

**边界**：

- 只改任务单「只许改」里列的文件；发现别处有问题，写进交回的「计划外问题」，留给下一张。
- 提交、推送、打 tag、发版、部署、删文件，都等 Owen 开口。推到 `main` 等于发给用户。
- 真上课的程序由 Owen 跑：`main.py` / `cl` 留给〔作者做〕的步骤。验证用进程内测试 + HOME 隔离。
- 要花钱的调用只用 `cloud_translator.load_test_key()`。
- 用户数据只读不写：`sessions/`、`glossary/`、`~/.classlive/`。测试同时隔离读端和写端。
- 夹具只用作者自己的课；朋友的转录留在本机。仓库公开：地址、通行码、key 留在仓库外。
- 凭证由 Owen 自己放到位（`.dev.vars`、`~/.classlive/test-key`、VPS 上的令牌），对话里只说「需要哪一个」。
- 新增 `CLASSLIVE_*` 开关同步登记 `HANDBOOK.md` §6.1；新增 streamq tag 同步加 `main.drain()` 分支。
- 冻结的提示词逐字保持（`tests/test_translator.py` 钉着）。

**每张的底线判据**：`ClassLive.app/Contents/MacOS/python scripts/check.py --changed` 全绿，输出尾部原样贴进交回；它列出的慢测试和量具照跑或写明谁来跑。

**交回格式**（固定，照抄填空）：

```
## 交回 <任务单号>
- 加载的 skill：
- 改动文件：<路径> — <一句话>
- 完成判据：逐条写「命令 → 输出尾部原文」
- 交给作者做的：<步骤 + 预计几分钟>
- 没做到的：<哪条 + 卡在哪>
- 计划外问题：<只报告>
```

## 顺序与依赖

| 序 | 任务单 | 前置 | 估时 | 谁验收后才往下走 |
|---|---|---|---|---|
| 1 | U1-A 卡顿探针 · U2-A 启动打点 | 无 | 各半天 | Owen 同意随下个版本发 |
| 2 | U1-B 手动验证 | U1-A | 作者 5 分钟 | — |
| 3 | S1–S4 穿刺 · P0-1 | 无（S1 的磁带可用 P0-1 录） | 3–4 天 | 闸门 G0 |
| 4 | U1-C 修卡顿 | U1-A 攒够 3 节真课数据 | 2–3 天 | 5 节真课 |
| 5 | P0-2 → P0-5 | P0-1 | 3–5 天 | 完成判据 |
| 6 | P1-1 → P1-3 | P0 全部 | 约 1 周 | 闸门 G1 |
| 7 | P2 逐刀 · P3 | P1 | 3–5 周 | 闸门 G2 / G3 |
| 8 | P4 · P5-0 · P5-D（可与 7 并行） | G0 | 2–3 周 | 闸门 G4 |
| 9 | P5 M1–M7 · P6 | P2 的核心 + P4 | 2–3 周 | 闸门 G5 |

---

## U · 桌面体验线

### U1-A 卡顿探针（随下个版本默认开）

**目的**：下一次字幕停住时，报告里能直接读出「卡在主循环的哪一步」和「是主循环卡住还是声卡没送块」。现状与两种候选机制见底稿 §6.1。

**先加载**：`diagnosing-bugs`、`verification-before-completion`。
**只许改**：`main.py`（主循环，`while running.is_set() and not stopping.is_set():` 那一段）、`overlay.py`（`_pump_once` / `_drain_events` / `_yield_to_runloop`）、`capture.py`（`CallbackSource`）、`testmode.py`、`tests/test_loop_probe.py`（新）、`docs/HANDBOOK.md`（报告字段说明处）。

**步骤**：

1. `testmode`：加 `note_loop(total_s, phases, pump_event=None)`，带 `@_safe`。累加三个计数（≥ 0.3 s / ≥ 1 s / ≥ 3 s 的轮数）、卡住总秒数，并留最长的 50 条明细：`{t_s, total_s, phases: {poll, feed, accept, drain, device, pump}, pump_event: {type, s}}`。进报告 `blocks.ui.loop_stalls`。
2. `main` 主循环：每一步前后取 `time.monotonic()`，一轮结束把各步耗时交给 `tester.note_loop`（`tester is None` 时整段跳过）。`pump` 这一步只算「派发事件 + 干活」的时间，**刻意让步的那段不算** —— `overlay` 每轮把三段时间（派发 / 干活 / 让步）放在一个只读属性上，主循环读它。
3. `overlay._drain_events`：每次 `sendEvent_` 前后取时钟，记下这一轮里最慢的那个事件的 `type()` 和耗时，随上一步的属性带出去。
4. 分辨两种机制：`CallbackSource` 的声卡回调里记一个时间戳（只赋值，回调必须立刻返回）；`testmode._capture_tick` 记停顿时多带两项 —— `cb_age_s`（距上一次声卡回调多久）和 `q_depth`（此刻缓冲里还排着几块）。**判读**：`q_depth` ≈ 停顿秒数 × 10 → 主循环卡住，音频没丢；`q_depth` ≈ 0 且 `cb_age_s` 大 → 声卡没送块，这段可能没录到。文件回放没有这条缓冲，两项记 `null`。
5. 测试：`note_loop` 的阈值、上限 50、异常不外抛；停顿条目带上两项新字段（注入假时钟）。
6. `HANDBOOK.md` 写清新字段含义与判读表。

**完成判据**：

- 底线判据全绿。
- 变异验证：把 0.3 s 阈值改成 30 s，`tests/test_loop_probe.py` 至少一条变红；改回后全绿。交回里贴两次输出。
- 探针不新增线程、不做 I/O、不分配大对象（交回里指出每轮新增的全部调用）。
- 〔作者做〕跑一次 `scripts/probe_testmode_report.py`（约 1 分钟），确认报告里有 `loop_stalls`。

### U1-B 手动验证〔作者做，5 分钟〕

前置：U1-A 已在本机。用悬浮窗界面回放一段自己的课堂录音（测试模式），依次做三件事，每件之间隔 30 秒：按住悬浮窗拖 3 秒 → 点开菜单栏图标停 3 秒 → 按住「回顾」按钮 3 秒再松开。然后打开这次的 `report.json`：

| 看到 | 结论 |
|---|---|
| 三个时间点各有一条 `loop_stalls`，`pump` 占大头，`pump_event.type` 是鼠标类 | 假设成立 → 派 U1-C |
| 没有对应条目 | 假设不成立 → 等真课数据，看 `phases` 里谁占大头 |

### U1-C 把「喂 Soniox」搬出主线程

**前置**：U1-A 的数据确认 `pump` 是主因（U1-B 或 ≥ 3 节真课）。数据指向别处时本张作废，按下表另立：

| 探针显示 | 另立什么 |
|---|---|
| `drain` 占大头 | 查是哪类事件慢，单独修 |
| `accept` 占大头 | 段尾同步做了重活，挪到后台 |
| 各步都小但整轮很长 | 别的线程长时间占着解释器锁，查是谁 |
| 停顿条目 `q_depth` ≈ 0 | 声卡侧空窗（换设备 / 音频服务重启），可能真丢音频，优先级最高 |

**先加载**：`codebase-design`、`diagnosing-bugs`、`verification-before-completion`。
**只许改**：`main.py`（主循环 + 一个新的小线程）、`stream_asr.py`（`mark_segment`）、`capture.py`、`testmode.py`、对应测试。

**设计**（已定，照做；有异议写进交回）：

- 新增一条「送音线程」：独占 `src.poll()`，拿到块立刻 `stream.feed()`（`gate.can_feed()` 为真时），再把 `(块, 峰值, 增益)` 放进一条给主循环的有界队列（容量同 `QUEUE_MAX`，满了丢最旧并计数）。主循环改从这条队列取块，照旧做 `seg.accept()`、`tester.note_chunk()`、静音监测。
- **段界约束**：现在的注释写明「喂音频必须排在 `seg.accept()` 之前」，因为 `mark_segment()` 记的是「此刻已喂了多少样本」。送音线程会跑在主循环前面，所以 `mark_segment` 要改成接收显式位置：主循环自己累计「`seg.accept` 已吃掉的样本数」，段尾时传 `mark_segment(at_samples=n)`；不传时行为不变。
- `gate`、`stream.feed` 被两条线程碰到的状态逐个核对有没有锁；结论写进交回。
- 收尾：送音线程随 `running` / `stopping` 退出，带超时 join；文件源播完的判定仍由主循环做。
- 本地兜底那条路（Soniox 退回后靠 `seg.accept` 出句）仍在主循环上，停顿时会晚出句 —— 本张接受，写进 `HANDBOOK.md` 的已知限制。

备选「在 AppKit 跟踪模式里挂定时器继续跑主循环体」已否决：`drain()` 会在拖动途中改视图，重入风险大。

**完成判据**：

- 新测试：假音源每 0.1 s 出一块、假 `stream` 记录每次 `feed` 的时刻；主循环人为卡 3 秒期间，相邻两次 `feed` 间隔始终 < 0.3 s。
- 新测试：同一串「块 + 段尾时刻」，改前改后 `seg_for_audio()` 对每个封口句给出的段号逐一相同。
- 报告新增 `feed.gaps`（送音线程相邻两次 `feed` 超过 1 s 记一条），与 `capture.gaps` 并列。
- 底线判据全绿；`check.py --changed` 点名的流式量具对照基线，贴数字。
- 〔作者做〕重复 U1-B：拖窗 3 秒，`capture.gaps` 有条目、`feed.gaps` 没有。之后连续 5 节真课 `feed.gaps.n` 为 0。

### U2-A 启动打点

**目的**：慢启动全部慢在「`run()` 开头 → 首屏小窗出现」（底稿 §6.2），这段内部没有打点。补上，才知道修什么。

**先加载**：`verification-before-completion`。
**只许改**：`main.py`（`run()` 开头到 `_trace("hud")` 之间，及组装 `startup` 事件字段处）、对应测试、`HANDBOOK.md`。

**步骤**：

1. 在这一段的五步后面各记一个时间点：单实例锁、开课清理（`keypoints` 那步）、老 key 搬家、麦克风权限、首屏小窗（再拆成「import 完成」与「窗口显示」两点）。
2. 六个差值写进 `startup` 事件（字段名 `pre_lock_s` / `pre_reset_s` / `pre_keymove_s` / `pre_mic_s` / `hud_import_s` / `hud_show_s`），`CLASSLIVE_STARTUP_TRACE=1` 时同样逐行打印。
3. 测试：`startup` 字段集合包含这六项；任何一步抛异常时打点不影响启动。

**完成判据**：底线判据全绿；六项之和与 `hud_s` 相差 < 50 ms（测试里断言）。
**之后**〔作者做〕：攒 5 次「当天第一次开」和 5 次「刚关又开」，把六项列成表交回 → 再立 U2-B（修法取决于哪一项大，现在不预设）。

### U3–U5（先量，后立单）

| # | 先做的测量 | 立单条件 |
|---|---|---|
| U3 内存 | Soniox 正常时，本地草稿模型「启动就加载」对比「用到才加载」各跑一次回放，记内存峰值与退回本地时的首句延迟 | 省 ≥ 300 MB 且退回延迟 ≤ 3 s |
| U4 中文晚 1.5 s | 用 `scripts/stream_eval.py` 对照基线试 Soniox 封口参数（B7）：`endpoint_latency_adjustment_level` 1 和 2，各记封口延迟、句数、WER | 封口延迟降 ≥ 0.3 s 且 WER 不变差 |
| U5 事故 | 逐项列：3.11.1–3.11.3 修过的每一类有没有会红的判据；还开着的（断线空窗只补了一部分）各一行 | 列完由 Owen 排优先级 |

---

## S · 穿刺（一次性代码，放仓库外的 scratch 目录，不提交）

每张交回的是**数字和结论**，代码不要求整洁。闸门 G0 的数在 `PLAN` §5。

### S1 Python 实时核心能否原样跑在浏览器里

**先加载**：`research-sop`（查 Pyodide 当前版本与装载方式，结论带链接和日期）。

1. 拷出：`stream_asr.py` 里的 `Tok`、`Sealed`、`StreamCore`、`parse_tokens`、`build_context`（剥掉 numpy、线程、网络），以及 `caption_flow`、`fragment_merge`、`linebreak`、`text_diff`、`draft_policy`、`draft_stable`、`draft_translate.DraftView`。
2. 磁带：一节作者自己的课的 Soniox 原始消息，逐条一行 JSON、带到达时刻。来源二选一：P0-1 的录制开关；或用 test key 把一段 10 分钟的自己的录音过一遍 Soniox，把每条 WebSocket 消息原样落盘。
3. 参照输出：CPython 下按磁带逐条喂（时钟用磁带时刻注入），每条之后记 `draft_text()` 和新封口的句子，落成一行一条的 JSON。
4. 同一份代码、同一条磁带放进 Pyodide：先在 Node 里跑（快），再放进浏览器的 Web Worker 里跑。
5. 交回：两份输出是否**逐字节相同**（不同就贴第一处差异）；冷启动秒数（清缓存）、热启动秒数、单条消息处理耗时的中位与 p99、首次下载的总字节数；Chrome 版本与机器型号。

### S2 浏览器采音 → Soniox 的质量与延迟

1. 最小页面：`getUserMedia` → AudioWorklet 重采样到 16 kHz 单声道、100 ms 一帧、s16le → `new WebSocket(url, ["soniox-api-key", key])`（B2）。
2. 临时 key：命令行用 test 通行码向现有 Worker 的 `/v1/soniox/key` 要一把，贴进页面（服务器到服务器，不需要动 Worker 的 CORS）。
3. 对照一（纯链路）：页面里解码一份带真值的语料文件（`docs/experiments/classroom_sim_20261011/` 那批）→ 走同一条 worklet → Soniox；桌面侧用 `scripts/stream_eval.py` 跑同一份。比 WER、封口延迟。
4. 对照二（真麦克风）：外放同一份语料、麦克风收音，降噪 / 自动增益 / 回声消除三项「全开」与「全关」各一遍（A8）。
5. 交回：四组 WER 与封口延迟、所用语料文件名、浏览器版本；worklet 的重采样写法（是否需要移植 `capture.PeakNormalizer`）。

### S3 置顶字幕小窗的观感（用 Svelte 写）

**先加载**：`impeccable` 或 `design-taste-frontend`；接上 Svelte 官方 MCP（F9）。

1. `npx sv create` 起一个最小 SvelteKit 项目，关掉 SSR。
2. 页面上一个按钮：点一下 `documentPictureInPicture.requestWindow(...)`，把主文档的样式表拷进小窗，`mount(字幕组件, { target: 小窗.document.body })`（F14）。
3. 字幕数据：作者一节课的 `live.jsonl` 转成「时刻 + 英文 + 中文 + 草稿/定稿」的数组，按原时刻回放；主页面和小窗读同一个 `.svelte.ts` 响应式对象。
4. 核对并交回：小窗里的按钮点得动吗；关掉主标签页时小窗的行为；重新打开要不要再点一次；能缩到多小；回放 10 分钟期间 Chrome 性能面板里超过 50 ms 的长任务数。
5. 〔作者做〕目测：标题栏能否接受；能否浮在全屏 PPT、全屏 Zoom 上面（Mac 一遍，有条件 Windows 一遍）。

**回退**：步骤 2 挂不进去或事件收不到，一天内绕不过 → 交回说明，网页版改用 React（`PLAN` P5）。

### S4 笔记流水线在 Linux 上能否跑

1. 列出笔记流水线真正需要的第三方包：在 macOS 上隔离 HOME import 笔记那组模块，看 `sys.modules` 里的非标准库。
2. 〔作者做〕在 VPS 上放好仓库与 test 通行码。
3. VPS 上建虚拟环境只装第 1 步那几个包；带内存上限跑 `scripts/notes_bench.py econ10790-1315 --limit 1`，再跑整课。
4. 交回：冻结判据逐条过没过；峰值内存与墙钟（一片、整课）；缺了什么（`extract.py` 的 PDFKit 一项单列）；与 macOS 同一输入的结构差异。

---

## P0 · 安全网

### P0-1 原始消息录制开关

**只许改**：`stream_asr.py`（`_recv_loop` 收到消息处）、`HANDBOOK.md` §6.1、对应测试。
加一个默认关闭的开关 `CLASSLIVE_STREAM_TAPE=1`：把收到的每条 Soniox 消息连同到达时刻追加写进 `sessions/<同名>.tape.jsonl`。写盘走后台队列，收消息的线程只做入队；写失败静默停录，绝不影响上课。
**完成判据**：开关关着时零开销（测试断言没有打开文件）；开着时磁带条数 = 收到的消息数；底线判据全绿。〔作者做〕录一节自己的课。

### P0-2 课堂回放判据（分两步交）

**先加载**：`codebase-design`、`verification-before-completion`。

**第一步，只读调查**（交回后等确认再写代码）：`main.run()` 的四个替身各从哪里注入 —— 音源、`StreamASR`、翻译器、界面对象。每个写出模块级名字 + 行号 + 打算怎么替（优先 `unittest.mock.patch`，不改生产代码）。同时列出 `run()` 会写的全部路径，说明各自怎么隔离。

**第二步，实现**：`tests/test_class_replay.py`（`tests/test_replay.py` 已被笔记回放占用，别重名）。

- 四个替身：静音音源；按磁带吐消息的假 `StreamASR`；按磁带回译文的假翻译器；把每次 `ui.*` 调用记成一行的记录型界面。
- 比三样：封口句的序列与内容；会话 `.md` 的落盘内容（去掉时间戳）；每句的译文。草稿的交错顺序由线程调度决定，不比。
- 夹具：作者一节课的磁带切 5–10 分钟，放 `tests/fixtures/class_replay/`。

**完成判据**：未改动的 `main` 上连跑 5 次全绿、每次 ≤ 60 s；跑完 `git status` 干净、真 `~/.classlive/` 与 `sessions/` 没有新文件（`check.py` 的写端核对通过）。

### P0-3 变异验证

对 P0-2 的判据做三次故意破坏，每次都必须变红：丢一句封口句；把相邻两句的译文对调；漏写一行 `.md`。破坏用测试内的补丁做，生产代码保持原样。交回贴三次红的输出。

### P0-4 读源码文本的测试清单

19 个测试读 `main` / `overlay` / `entry_panel` 的源码文本。逐个标：钉的是行为（留）还是「这段代码在这个文件里」（搬家时同步改）。结果写在各测试文件头的注释里，并汇总一张表交回。只加注释，不改断言。

### P0-5 纯度规则

**只许改**：`scripts/check.py`、对应测试。
加 `CORE_MODULES` 清单；清单里的模块 import 了 AppKit / objc / Quartz / threading / httpx / sounddevice / numpy / `paths` 就判红。起步清单：`caption_flow`、`fragment_merge`、`linebreak`、`text_diff`、`draft_policy`、`draft_stable`、`note_math`。
**完成判据**：现状全绿；临时往 `text_diff.py` 加一行 `import threading`，`check.py` 变红并点名该文件；撤掉后全绿。

---

## P1 · 契约（行为不变，回放逐字相同）

每张的完成判据都含：P0-2 的回放逐字相同。

| 单 | 做什么 | 完成判据（除回放外） |
|---|---|---|
| P1-1 | `streamq` / `drafts` / `drafts_zh` 的位置元组换成 `@dataclass(frozen=True)`。先交一张表：现有全部 tag、各自字段、生产者与消费者行号。然后**一次换一种 tag、一种一个提交** | `main.py` 里 `item[` 下标读计数为 0；`drain()` 对事件类型穷尽匹配，漏一种时 pyright 报错 |
| P1-2 | `ui_port.py`：一个 `typing.Protocol` + 一个全空实现的基类；`Overlay` 与终端界面继承它；删掉 `getattr(ui, …)` 守卫 | `getattr(ui` / `hasattr(ui` 在 `main.py` 计数为 0；记录型界面实现同一个协议 |
| P1-3 | `ViewState`（下面的 v0）由同一批事件投影出来，桌面版以影子模式并行算它、不拿它画界面 | 回放磁带上，「按 ViewState 渲染出的文本」与记录型界面收到的调用零差异 |

**ViewState v0**（字段以 P1-1 的调查为准，可增不可乱改名 —— 它是网页版的数据契约）：

```python
@dataclass(frozen=True)
class Row:
    id: str            # 稳定标识，草稿转定稿时不变
    kind: str          # "draft" | "final"
    en: str
    zh: str
    zh_state: str      # "none" | "draft" | "final" | "failed"
    seg: int
    t_s: float
    terms: tuple       # 命中的术语 (原文, 译法)

@dataclass(frozen=True)
class ViewState:
    rev: int           # 每次变化 +1
    rows: tuple        # 屏上这一窗的行
    status: dict       # 识别来源、是否在听、降级 / 失败提示（U6：失败必须出现在这里）
    course: str
    elapsed_s: float
```

必须可 JSON 序列化；只含数据，不含回调。

---

## P2 · 拆 `run()`（逐刀同一个模板）

八刀的清单与顺序在 `PLAN` P2。每一刀一张单，都按这个模板：

1. **只读**：列出这一簇闭包读写的全部外层变量（名字 + 行号 + 读还是写）。交回，等确认。
2. 把这一簇搬进一个类 / 模块，外层变量变成构造参数或显式状态；`run()` 里只留构造和接线。
3. 搬出去的东西若不碰线程和 I/O，加进 `CORE_MODULES`。
4. 提示词拼装（`cloud_translator._user_content`、`translator.select_term_zh`）逐字搬。

**完成判据**：回放逐字相同；`tests/test_translator.py` 的冻结用例一字不改就过；P0-4 清单里受影响的测试同步改完；这一刀之后 `run()` 的行数写进交回。
**闸门**：每刀合并后〔作者做〕上 1 节真课无异常，才派下一刀。落盘路径（`_emit` / `drain`）排在最后动。

---

## P3 · 笔记服务函数

**先加载**：`codebase-design`。
目标接口（一个深模块，桌面后台进程与服务器 runner 共用）：

```python
def build_note(inp: NoteInput, *, llm: LLMPort, store: StorePort,
               progress: Callable[[str, float], None] | None = None) -> NoteOutput: ...
# NoteInput：逐字稿文本、课件文本（可空）、术语表、课号、档位
# NoteOutput：note_md、note_html、台账 dict、用量统计
# LLMPort：一个方法 —— 发一次对话调用；StorePort：按名字读写中间产物
```

「写进 vault」「自动打开」「写 note-outbox」留在桌面侧，拿到 `NoteOutput` 之后再做。Linux 上的 PDF 文字层按 S4 的结论处理。
**完成判据**：`notes_bench` 冻结判据在 macOS 与 Linux 上一致；桌面版 `note_worker` 改调它之后，同一份逐字稿生成的笔记结构与台账与改前一致；3.11.2 修过的三类笔记故障的现有判据一字不改全绿。

---

## P4 · 服务端

**只许改**：`server/asr-worker/`（沿用「纯逻辑不 import `cloudflare:*`、Node 测试直接跑」的写法）、新目录 `server/notes-runner/`。部署〔作者做〕。

**接口 v0**（实现前先交一份逐条的请求 / 响应样例，等确认）：

| 谁调 | 接口 | 要点 |
|---|---|---|
| 网页 | `POST /v1/web/session` | 开课，回 `session_id` |
| 网页 | `PUT /v1/web/session/:id/lines` | 追加逐句；每句带序号，重复提交不重复写；回已确认到的序号 |
| 网页 | `POST /v1/web/session/:id/end` | 下课，入一条笔记任务，回 `job_id` |
| 网页 | `GET /v1/web/session/:id` · `GET /v1/web/sessions` | 状态、笔记地址、历史列表 |
| 网页 | `POST /v1/web/slides` | 课件进 R2，入一条术语任务 |
| 网页 | `DELETE /v1/web/data` | 立即删除这枚通行码名下的全部数据 |
| runner | `POST /v1/runner/claim` · `/heartbeat` · `/complete` | 领任务、续租、交结果；产物也经 Worker 写 R2（runner 手里只有一枚可吊销的令牌） |

- **任务状态机**（Durable Object）：排队 → 已领（第 n 次、租约到期时刻）→ 完成 / 失败（3 次后）。租约过期自动回到排队。交结果时带的次数与账本不符 → 拒收，不产生第二份笔记。
- **R2 目录**：`web/<通行码哈希>/<session_id>/…`；`web/` 前缀挂 30 天生命周期。音频不存（Q2）。
- **CORS**：只放行网页版那一个来源（E6）。网页放在**另一个只有静态文件的 Worker** 上（C9），这样改网页不会重新部署正在给桌面版用的接口；「同一个 Worker 既出网页又出接口」因此不用。
- **防刷**（E7）：网页端通行码默认带日 token 上限与实时转写日秒数上限；`max_tokens` 封顶；浏览器设备单独计数。具体数值实现前问 Owen（Q4b）。
- **runner**：拉任务 → 心跳 → 调 `build_note` → 交结果。systemd 常驻，并发 1，带内存上限。

**完成判据**：Node 测试覆盖状态机（含租约过期被另一台领走、重复交结果）；runner 在「跑到一半被杀」「断网 2 分钟」「同一任务被领两次」各有判据；现有 `test*.mjs` 全绿（桌面版接口行为不变）；〔作者做〕用 test 通行码走通一次「下课 → 笔记进 R2」。

---

## P5 · 网页客户端

技术与理由在 `PLAN` P5「界面方案」。浏览器范围：桌面 Chrome / Edge。

### P5-0 脚手架

目录 `web/`（与 `server/` 并列）。

1. 复核 shadcn-svelte #2915 是否已修（F11）：已修用 SvelteKit 3，否则用 2 的最新版。结论与链接写进交回。
2. `npx sv create web`（TypeScript）→ 加 Tailwind v4 → `adapter-static`（`fallback` 配好，根布局 `ssr = false`，开课页与说明页 `prerender = true`）→ `npx shadcn-svelte@latest init` → `npx sv add ai-tools`（装官方 MCP 并写进 `AGENTS.md`；本仓库只维护 `CLAUDE.md`，生成的内容并进去）。
3. `web/wrangler.jsonc`：只有 `assets`（`not_found_handling: "single-page-application"`），没有脚本。
4. 模块边界（每个都是深模块，界面只认 `state`）：

| 目录 | 管什么 | 对外接口 |
|---|---|---|
| `lib/audio/` | 麦克风、worklet 重采样、电平 | `start()` → 16 kHz 帧的回调；`level` |
| `lib/asr/` | Soniox 连接、重连、临时 key | 帧进、原始消息出；连接状态 |
| `lib/core/` | 实时核心（Pyodide Worker 或 TS，S1 定） | 消息进、`ViewState` 出 |
| `lib/state/` | `viewstate.svelte.ts`：唯一的界面状态 | 只读的响应式对象 |
| `lib/store/` | IndexedDB 逐句落盘、同步队列 | `append(line)`、`pending()` |
| `lib/api/` | Worker 接口 | 一个函数对应一个接口 |
| `lib/ui/` | 组件（shadcn-svelte 源码 + 自家 token） | — |

**完成判据**：`npm run build` 出纯静态产物；`svelte-check` 零错误；`wrangler dev` 能打开开课页；不含 Pyodide 的首屏 JS ≤ 150 KB（gzip，建议值）。

### P5-D 设计稿（先于组件）

**先加载**：`impeccable`、`design-taste-frontend`。基调「暗字幕 + 亮笔记」，做法见 `PLAN`（自定中性色、一个圆角档、一个强调色、磨砂面板配方沿用桌面版 `panel.py`、六种状态做全、动效 ≤ 200 ms）。
交付：一份 token 表 + 五个页面的静态稿（开课、上课、置顶小窗、课后、设置），每页含空状态与出错状态。〔作者做〕Owen 看过点头，才写组件。

### M1–M7（每个一张单，都能单独演示）

通用硬性要求：每个 `.svelte` 文件过 `svelte-autofixer` 到零问题；只用 runes 写法；`svelte-check` 零错误。

| 单 | 做什么 | 完成判据 |
|---|---|---|
| M1 | 麦克风 → 字幕：采音、直连 Soniox、核心出封口句、页面渲染 `ViewState`；6 秒没声音提示检查麦克风（E1） | 回放 S1 的磁带，页面上的封口句与桌面回放逐字相同；回放 10 分钟期间超过 50 ms 的长任务为 0 |
| M2 | 翻译 + 术语注入：流式译文；术语进 Soniox context 与翻译提示词（提示词从 P2 提纯的模块来，逐字相同） | 同一句的请求体与桌面版逐字相同（测试比对）；问句只翻译不回答 |
| M3 | 不丢：定稿句先写 IndexedDB 再同步；断线重连；旧连接迟到的事件不影响新会话（E4） | 上课中途刷新页面，已定稿的句子一条不少；断网 60 秒恢复后自动续上，服务器端无重复句 |
| M4 | 置顶小窗 + 防熄屏：Document PiP；非 Chrome / Edge 在开课前说明（E5）；Wake Lock 切回标签页时重新申请（A12） | S3 的五项核对在正式页面上重过一遍 |
| M5 | 课件 → 术语表 | 上传一份课件，术语表回到页面并进入下一节课的注入 |
| M6 | 下课 → 笔记：触发任务、显示进度、页面内阅读、下载 md / html | 下课后 15 分钟内笔记出现；runner 停机时页面显示「排队中」，恢复后自动出现 |
| M7 | 网课音源：`getDisplayMedia` 抓标签页 / 系统声（A5、A6） | Mac 与 Windows 的 Chrome 各抓一次系统声出字幕 |

时钟规则：封口判据的「推时钟」挂在 worklet 送帧的节拍或 WebSocket 消息上，后台标签页的定时器会被压到每秒甚至每分钟一次（A10）。

**第一版完成判据（M1–M6）**：〔作者做〕用网页版完整上一节真课，句数与同时开着的桌面版相差 ≤ 2%；Windows 的 Chrome 上重复一遍。闸门 G5：一位朋友在自己机器上零指导走通。

---

## P6 · 放给朋友之前（清单）

- 大陆路径实测：页面可达、Soniox 直连、Worker 往返延迟（C8）。
- 首次打开的告知：存了什么（逐字稿、笔记、术语表）、不存什么（音频）、存多久（30 天）、怎么立即删。
- Soniox 并发流默认 10 条（B4），同时上课的人接近这个数之前去 Console 申请。
- 用量面板里网页端与桌面端的调用能分开看。
