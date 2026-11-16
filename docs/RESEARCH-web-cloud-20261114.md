# 调研：重构 + 网页版（全云端）的事实底稿

> **口径**：2026-11-14 抓取 / 实测，代码基线 commit `3fef625`（3.11.3）。按 `research-sop` 两轮做：第一轮广撒网，第二轮对关键结论回源。
> 置信度：**高** = ≥2 个独立源一致，或 1 个官方原文直接写明；**中** = 单个非一手源、无冲突；**低** = 有冲突或只有低信任源。**低置信的不进决策**。
> 这份只放事实。怎么做、按什么顺序，在 `PLAN-web-cloud-20261114.md`。

## 1. 代码基线（实测）

量法：一次性 AST 脚本统计根目录 `*.py`；死代码用 `vulture`；改动频率用 `git log --since="30 days ago"`。数字会旧，引用前重量。

| 项 | 数 |
|---|---|
| 根目录模块 / 总行数 | 95 个 / 70,430 行（2026-10-17 是 36,937 行） |
| 其中可执行代码 / 注释 / docstring | 39,461（56%）/ 8,715（12%）/ 13,935（19%） |
| 测试 | 125 个文件 / 56,676 行 |
| `main.run()` | 3,116 行；45 个直接嵌套闭包；18 处 `x = {"k": …}` 当可变单元 |
| `main.py` 的 import 扇出 | 其余 94 个模块里的 53 个 |
| `entry_panel._build` | 单函数 2,773 行 |
| `Overlay` 类 | 5,705 行；249 个方法，公开 46 个；`__init__` 577 行 |
| `ObsidianWriter.close` / `_review_v2` | 386 行 / 269 行 |
| `getattr(ui, …)` / `hasattr(ui, …)` 守卫 | 40 处 |
| `streamq` 的 `item[N]` 下标读 | 93 处（tag 至少 13 种） |
| `except Exception` | 全仓 979（`main` 149、`overlay` 110、`entry_panel` 79） |
| 直接 import AppKit / objc / Quartz 等的模块 | 21 个 / 28,000 行（40%） |
| import 环（强连通分量） | 4 组：笔记 6 个 · 翻译/术语/课程 5 个 · doctor/draft_engine/ready/update · testmode/upload |
| 近 30 天提交次数前三 | `main.py` 51 · `obsidian_writer.py` 47 · `overlay.py` 40 |
| `vulture` 80% 置信 | 15 条，全是未用变量 / import —— **没有成块死代码** |
| import `main` 的测试 / 读 `main`·`overlay`·`entry_panel` 源码文本的测试 | 34 个 / 19 个 |

**只 import 标准库（或彼此）的实时侧模块**（网页版可直接复用的候选，AST 核过）：`caption_flow`、`fragment_merge`、`linebreak`、`text_diff`、`draft_policy`、`draft_stable`。笔记侧同样只吃标准库的：`note_math`。
**笔记侧其余模块**（`note_html`、`note_render`、`note_guards`、`note_v2`）自身不起线程以外的平台调用，但互相 import 并经 `obsidian_writer` / `keypoints` 连到本地路径与网络 —— 能在 Linux 的 runner 上跑，不是「纯」的。`extract.py` 的 PDF 文字层用 PDFKit（macOS 专有）。
**核心是纯的、但和线程/网络住同一个文件的**：`stream_asr.py`（`StreamCore` 纯，`StreamASR` 8 处线程/队列，文件顶部 import numpy）、`semantic_seal.py`（`choose_cut` / `cap_cut` 纯，`SemanticSealer` 带线程）、`draft_translate.py`（`DraftView` 纯，`DraftTranslator` 带线程）。

## 2. 上云容量（实测）

| 项 | 数 | 量法 |
|---|---|---|
| 笔记流水线峰值内存 | 145 MB（RSS） | `/usr/bin/time -l … scripts/notes_bench.py econ10790-1315 --limit 1`（test key，5 分钟切片、3 节） |
| 同上 CPU / 墙钟 | 5.2 s / 65.7 s | 同上；其余时间在等模型 API |
| 只 import 笔记模块 | 27 MB；不加载 numpy / mlx / sherpa / AppKit | `python -c "import note_v2, note_math, …"` |
| 整课笔记墙钟 | 约 6.6 分钟（1,371 句、20 个复习单元） | 2026-11-10 事故记录 |
| 整课峰值内存 | **没量**（估 150–300 MB） | — |
| 桌面端整机 RSS 峰值 | 885–1,490 MB | `sessions/*.report.json` 的 `resource.rss_peak_mb`；含本地模型与 AppKit，上云后不跑 |
| 每节课逐字稿 `.md` | 中位 91 KB · p90 178 KB · 最大 277 KB | 本机 `sessions/` 38 节（>20 KB 的） |
| 每节课 `.live.jsonl` | 中位 0.45 MB · 最大 2.58 MB | 同上 |
| 音频 | opus 24.5 kbps ≈ 11 MB / 小时 | `ffprobe` 一个 600 s 分段 = 1.84 MB |
| 上传包 `.zip` | 中位 0.64 MB | 46 个 |
| 作者 VPS | 2 vCPU / 1,967 MB（available 820）/ 磁盘剩 14 G / 法兰克福 / Python 3.12.3 | 11-14 `ssh` 实测 |

## 3. 结论表

### A. 浏览器能力

| # | 结论 | 来源 | 源日期 | 置信 |
|---|---|---|---|---|
| A1 | Document Picture-in-Picture（网页开置顶小窗，放任意 HTML）：Chrome / Edge 116+、Firefox 151+ 桌面可用；Safari 与所有移动端不支持 | https://caniuse.com/mdn-api_documentpictureinpicture · https://developer.chrome.com/docs/web-platform/document-picture-in-picture | caniuse 2026-10 数据 | 高 |
| A2 | PiP 小窗必须由用户手势打开；一个标签页只能开一个；随打开它的标签页关闭；网页不能设位置；尺寸只是提示（浏览器会记住用户上次拖的大小） | https://openpwa.net/reference/capabilities/document-picture-in-picture/ · https://issues.chromium.org/issues/332592715 | 2026-09 | 高 |
| A3 | Chrome 的 PiP 小窗**总有标题栏**（显示来源，防仿冒），不能做成无边框 | https://github.com/WICG/document-picture-in-picture/issues/32 （Chrome 团队成员回复） | 2023-04 | 中 |
| A4 | 新小窗没有样式表，要自己把样式复制进去 | https://getstream.io/video/docs/react/ui-cookbook/document-pip | — | 中 |
| A5 | 浏览器抓声音：Chromium 在各平台都能抓「标签页声音」；Windows / ChromeOS 能抓整机系统声；Firefox、Safari 的 `getDisplayMedia` 不给音轨 | https://developer.mozilla.org/docs/Web/API/MediaDevices/getDisplayMedia · https://caniuse.com/mdn-api_mediadevices_getdisplaymedia_audio_capture_support | 2026 | 高 |
| A6 | **macOS 上 Chrome 也能抓系统声**（macOS 14.2+，约 Chrome 141 起）。Chromium 2026-09-01 的提交删掉了 `MacCatapLoopbackAudioForScreenShare` 开关，提交说明写「已上线近一年」 | https://chromium.googlesource.com/chromium/src/media/+/1c3d731128ccb8cf522d3b262db874b6eb2e29ae · https://blog.addpipe.com/getdisplaymedia-allows-capturing-the-screen-with-system-sounds-on-chrome-on-macos | 2026-09-01 / 2026-02-19 | 高 |
| A7 | 采音要用 AudioWorklet 自己重采样到 16 kHz：Safari 无视 `AudioContext` 的 `sampleRate`，Firefox 在非默认采样率下会绕过回声消除 | https://www.assemblyai.com/blog/build-a-voice-assistant-app-with-voice-agent-api | — | 中 |
| A8 | 浏览器默认开着降噪 / 自动增益 / 回声消除；有实例显示它们会吃掉轻声和句首，关掉三项后识别完整 | https://github.com/open-webui/open-webui/issues/28680 · https://github.com/QuentinFuxa/WhisperLiveKit/discussions/431 | 2026-08 / 2026-11 | 中（个例，需自己 A/B） |
| A9 | Chrome：正在用麦克风 / 屏幕采集的后台标签页**不会被省电模式冻结**；WebSocket 连接免于预算式计时器节流 | https://developer.chrome.com/blog/freezing-on-energy-saver · https://developer.chrome.com/blog/background_tabs | 2025-01-20 | 高 |
| A10 | 后台标签页的计时器会被压到 1 次 / 秒，隐藏 5 分钟后可到 1 次 / 分钟（除非在放声音或用 WebRTC）—— **逻辑不能靠 `setInterval` 推时钟** | https://developer.chrome.com/blog/timer-throttling-in-chrome-88 | 2021 | 高 |
| A11 | iOS：页面进后台或锁屏，麦克风采集被系统静音；必须保持前台 | https://github.com/get-bb/bb/issues/5698 · https://bugs.webkit.org/show_bug.cgi?id=233419 | 2026-10 | 中 |
| A12 | Screen Wake Lock（防熄屏）：2025-03 起各主流浏览器都支持（Safari 16.4+、Firefox 126+）；切走标签页会自动释放，回来要重新申请 | https://developer.mozilla.org/en-US/docs/Web/API/Screen_Wake_Lock_API · https://caniuse.com/wake-lock | 2026 | 高 |
| A13 | Safari 对「7 天内没有用户交互的站点」会删光脚本写入的存储（IndexedDB、Cache、Service Worker）；装成主屏幕应用的不受影响 | https://webkit.org/tracking-prevention · https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria | 2020 / 2026 | 高 |
| A14 | Firefox 不允许后台标签页**开始**采集；PiP 小窗有焦点时主标签页算后台 | https://getstream.io/video/docs/react/ui-cookbook/document-pip | — | 中 |

### B. Soniox

| # | 结论 | 来源 | 置信 |
|---|---|---|---|
| B1 | 官方支持浏览器直连：后端铸临时 key（`usage_type: transcribe_websocket`），浏览器直接连 WebSocket；音频不经自家服务器 | https://docs.soniox.com/guides/direct-stream · https://docs.soniox.com/authentication/temporary-api-keys | 高 |
| B2 | 浏览器不能设 WebSocket 请求头，改用 `new WebSocket(url, ["soniox-api-key", key])`；只认 `snx_temp_` 开头的临时 key，老的 `temp:` 格式不行。坏 key 不会握手失败，而是连上后收到错误帧 | https://docs.soniox.com/authentication/websocket-authentication | 高 |
| B3 | 2027-01-15 起不再接受「把 `api_key` 写在 start 消息里」；请求头与 protocols 两种都继续可用 | 同上 | 高 |
| B4 | 单条流最长 300 分钟（不可提）；**并发流默认 10 条（全组织合计）**，可在 Console 申请提高；每分钟请求 100 | https://docs.soniox.com/stt/rt/limits-and-quotas | 高 |
| B5 | 实时价约 $0.12 / 小时；实时翻译含在同一路调用里不另收费（`translation: {type: "one_way", target_language}`） | https://soniox.com/pricing · https://docs.soniox.com/translation/stt-translation/rt-translation | 高 |
| B6 | `stt-rt-v5` 于 2026-12-15 下线。本仓默认已是 v6（`stream_asr.MODEL`），v5 只剩兜底常量 `MODEL_FALLBACK` | https://docs.soniox.com/stt/models | 高 |
| B7 | 封口有三个可调参数：`endpoint_latency_adjustment_level` 0–3（默认 0；越高越早封口，也切得越碎）、`endpoint_sensitivity` −1.0–1.0（默认 0）、`max_endpoint_delay_ms` 500–3000（默认 2000）；v5、v6 都支持。官方给的低延迟起步值是 2 / 0.3 / 1500。本仓 `stream_asr._config()` 只设了 `enable_endpoint_detection: True`，三项都是默认值 | https://docs.soniox.com/stt/rt/endpoint-detection · https://docs.soniox.com/api-reference/stt/websocket-api | 高 |

### C. Cloudflare / InstaBox

| # | 结论 | 来源 | 置信 |
|---|---|---|---|
| C1 | R2 免费：10 GB-月 存储、100 万次 A 类、1,000 万次 B 类操作；出网不收费 | https://developers.cloudflare.com/r2/pricing （2026-10-30 更新） | 高 |
| C2 | Workers 免费档：每天 10 万请求、每次 10 ms CPU、50 个外部子请求、128 MB；HTTP 请求没有墙钟上限（客户端连着就能一直流） | https://developers.cloudflare.com/workers/platform/limits | 高 |
| C3 | Cloudflare Queues 2026-02-04 起进免费档：每天 1 万次操作、支持外部 HTTP 拉取；**免费档消息只留 24 小时** | https://developers.cloudflare.com/changelog/post/2026-02-04-queues-free-plan | 高 |
| C4 | Python Workers 跑在 Pyodide 上：没有线程、没有子进程；内存上限同为 128 MB | https://developers.cloudflare.com/workers/languages/python/how-python-workers-work · https://talkpython.fm/episodes/show/568/cloudflare-python-workers | 高 |
| C5 | InstaBox 免费档：1 台 4 vCPU / 8 GiB / 20 GB；空闲 30 分钟暂停（固定），下次被用到（exec / SSH / 公网端口请求）自动恢复，内存与进程保留 | https://instabox.sh/docs/idle-suspend · https://instabox.sh/docs/limits | 高 |
| C6 | InstaBox 免费档连续运行上限 480 分钟，忙着也停，活动租约不延长，且「服务可能更早暂停」；撞上限后冷却 5–20 分钟，连撞第 2 次 60–75、第 3 次起 120–135；空闲暂停不触发冷却并清零计数 | https://instabox.sh/docs/machines/auto-pause | 高 |
| C7 | InstaBox：静默等 API 的任务要持有活动租约（`PUT /v1/machines/{id}/activity-leases/{taskID}`，ttl 30–3600 s）才不会被当成空闲；出网 100 GiB / 月、约 50 Mbit/s；没有独立入站 IP | https://instabox.sh/docs/limits | 高 |
| C8 | `*.workers.dev` 在中国大陆基本不可达，`*.pages.dev` 时通时断；绑自有域名后可达但走海外线路（实测延迟 155–240 ms）。Turnstile 在大陆不可用 | https://www.v2ex.com/t/1250202 · https://whatworksinchina.com/self-hosting/cloudflare-china · https://developers.cloudflare.com/china-network/faq | 中（社区 + 第三方实测） |
| C9 | Cloudflare 建议新项目用 Workers 的静态资源（Static Assets）而不是 Pages（Pages 文档首页：「Start new projects with Workers」；Pages 继续维护）。静态文件请求免费、不限量、不触发 Worker 脚本；单页应用用 `not_found_handling = "single-page-application"`；同一个 Worker 可以既带静态文件又带接口，用 `run_worker_first` 指定哪些路径先进脚本 | https://developers.cloudflare.com/pages/ · https://developers.cloudflare.com/workers/static-assets/ · https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/ （2026-11-12 更新） | 高 |

### D. 架构与方法

| # | 结论 | 来源 | 置信 |
|---|---|---|---|
| D1 | Pyodide（CPython 编译成 WebAssembly）：传输约 6 MB、冷启动约 2 s、缓存后约 0.6 s；纯 Python 模块能直接跑；**没有线程、没有 socket**；必须放 Web Worker 里 | https://pyodide.org/en/stable/usage/wasm-constraints.html · https://sagebrush.space/articles/performance/01-sagebrush-and-pyodide · https://www.webassembly-wasm.com/production-wasm-workloads-and-deployment/other-languages-in-the-browser/running-python-in-the-browser-with-pyodide/ | 高（数字为他人机器实测） |
| D2 | 遗留系统替换的通行做法：先定要达成的结果，再按能力切片、逐片交付；「功能对等」（把旧系统做过的全搬一遍）是被点名的反模式 | https://martinfowler.com/articles/patterns-legacy-displacement/ · https://martinfowler.com/articles/patterns-legacy-displacement/feature-parity.html | 高 |
| D3 | 「迁移前先重构」成立的条件：先选迁移边界再重构；只重构穿过边界的部分；这块能力「搬得动」了就停 | https://www.freecodecamp.org/news/refactor-legacy-application-before-migration （2026-09-07） | 中 |
| D4 | 给没有测试的老代码做重构，先录现有行为当判据（characterization / golden master），并用变异证明判据会红 | https://developertoolkit.ai/en/workflows/test/characterization-tests · https://sourcegraph.com/blog/legacy-code-modernization | 中 |
| D5 | Tauri v2 官方有 sidecar 模式打包 Python 可执行文件（若将来要原生 Windows 壳） | https://v2.tauri.app/develop/sidecar | 高 |
| D6 | `python-sounddevice` 官方包不支持 WASAPI loopback（维护者 2025-10-05：等 PortAudio 发版）；替代是 PyAudioWPatch、soundcard | https://github.com/spatialaudio/python-sounddevice/issues/510 · https://github.com/s0d3s/PyAudioWPatch | 高（截至 2025-10） |
| D7 | AppKit 里按住按钮、拖动、打开菜单都会进入模态的鼠标跟踪循环（`NSEventTrackingRunLoopMode`）。Apple 原文：「the application's main thread is unable to process any other requests during an event-tracking loop and timers might not fire as expected」。手写事件循环的项目（winit）记录过同一现象：窗口缩放期间 `nextEventMatchingMask` 不返回。通行解法两种：在跟踪模式里挂定时器继续干活；或把不能停的活挪出主线程 | https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/EventOverview/HandlingMouseEvents/HandlingMouseEvents.html · https://github.com/rust-windowing/winit/issues/219 | 高（机制存在）。它是不是 ClassLive 停顿的原因：未证实，见 §6 |

### E. 同类项目的教训（社区）

| # | 教训 | 来源 | 置信 |
|---|---|---|---|
| E1 | 「识别不准」多数其实是输入问题：选错设备、静音、回声消除。要在界面上检测并说人话（6 s 没声音提示选麦克风） | https://dev.to/anders_lee_28dbcc2fd8e034/building-real-time-speech-translation-in-the-browser-what-actually-mattered-5acc （2026-10-22） | 中 |
| E2 | 延迟主要由**断句**决定，不是翻译模型；要有「标点 / 静音 / 硬上限」三重封口，否则不停顿的讲者永远不出译文 | 同上 | 中 |
| E3 | 翻译模型会去**回答**被翻译的问句；用标签包住原文并明令「只翻译不回答」后消失 | 同上 | 中 |
| E4 | 重开识别会话时，旧 socket 迟到的 `close` 可能关掉新会话 —— 两个真实 bug | 同上 | 中 |
| E5 | 浏览器差异要在界面上**前置说明**（Firefox 抓不到标签页声音），不要等报错 | https://juejin.cn/post/7700822035408683062 （2026-10-26） | 中 |
| E6 | 公开的大模型代理端点会被人找到并刷爆：一例 6 周几百美元，根因是无鉴权 + `Access-Control-Allow-Origin: *` + 可列模型 | https://sean.horgan.net/Notes/The-Gemini-bill-I-did-not-run-up | 中 |
| E7 | 防刷的通行分层：先鉴权 → 按用户限速 → 封顶输入与 `max_tokens` → 按用户的日 token 预算（原子计数）→ 供应商侧硬上限兜底 | https://genai.owasp.org/llmrisk/llm102025-unbounded-consumption · https://cryptoagilelabs.com/blog/cost-and-abuse-controls-for-llm-apis | 高 |
| E8 | 实时字幕要「稳」：当前这句固定在一个位置，只在句尾生长；已经显示的字不重排、不跳行 | https://www.reddit.com/r/SideProject/comments/1x0y80i/subkun_live_transcribe_free_live_captions_that/ | 中（单个同类项目的自述） |

### F. 前端与视觉

| # | 结论 | 来源 | 源日期 | 置信 |
|---|---|---|---|---|
| F1 | shadcn/ui（React）：命令行把组件源码拷进你的仓库，之后归你改；2026-07 起默认底座从 Radix 换成 Base UI；约 12.8 万 stars | https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default · https://dalao.ai/p/shadcn-ui | 2026-07 | 高（官方更新日志）；stars 为中 |
| F2 | Base UI 已是稳定版（1.9.0） | https://base-ui.com/react/overview/releases | 2026-10 | 高 |
| F3 | 公开基准 js-framework-benchmark（Chrome 154，Svelte 5.42 对 React 19.2）：压缩后体积 9.7 KB 对 51.4 KB；首次绘制 71 ms 对 227 ms；建 1,000 行后内存 2.9 MB 对 4.4 MB；建 1,000 行 22.1 ms 对 23.6 ms（速度接近）；建 10,000 行 231 ms 对 389 ms。React Compiler 不去掉虚拟 DOM，差距没有消失 | https://epicmax.co/blog/react-vs-vue-vs-svelte · https://arc.dev/employer-blog/svelte-performance-compiler-model · https://tech-insider.org/svelte-vs-react-2026/ | 2026-10-04 的一轮基准 | 中（三篇都在转述同一个基准，没回到原始结果页） |
| F4 | 「高级感」来自交互做全而不是视觉堆料：每个控件六种状态（悬停 / 聚焦 / 按下 / 禁用 / 加载 / 空）、一套字体加等宽、4–6 档字号、一个强调色 | https://mantlr.com/blog/stripe-linear-vercel-premium-ui · https://blog.logrocket.com/ux-design/linear-design | — | 中（评论文章，观点一致） |
| F5 | 可照着执行的界面细则：键盘处处可用、聚焦环可见、尊重「减少动态效果」、只动合成层属性、嵌套圆角内 ≤ 外；交互动效 ≤ 200 ms、高频操作不加多余动效、输入框字号 ≥ 16 px | https://vercel.com/design/guidelines · https://interfaces.rauno.me/ | — | 高（原文即规则本身） |
| F6 | 用 shadcn 做出来「千篇一律」的原因是没改默认值；解法是换掉中性色、圆角、字体，再加一个默认没有的自家 token | https://freedesignmd.com/blog/shadcn-looks-generic | — | 中 |
| F7 | CSS：`text-spacing-trim`（中文标点挤压）只有 Chromium 支持；`text-autospace`（中英自动间距）支持情况各家说法不一（见 §4）；`@starting-style` 2024-08 起各主流浏览器可用；`linear()` 缓动 2023-12 起可用；同文档 View Transitions 2025-10 起各主流浏览器可用 | https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/text-spacing-trim · https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@starting-style · https://developer.mozilla.org/en-US/docs/Web/API/View_Transition_API | 2026 | 高（`text-autospace` 除外） |
| F8 | 动效库的代价：Motion 的 `motion` 组件最少约 43 KB，换 `m` + `LazyMotion` 可降到约 5 KB。原生 CSS 与 View Transitions 能覆盖淡入、滑动、形变，做不了弹簧物理与交错 | https://motion.dev/docs/react-reduce-bundle-size · https://blog.logrocket.com/react-viewtransition-vs-motion-comparing-animation-approaches | 2026 | 中 |
| F9 | **Svelte 官方 MCP**：四个工具 —— `list-sections`、`get-documentation`（取最新 Svelte 5 / SvelteKit 文档）、`svelte-autofixer`（静态分析模型写出的代码，循环调用到没有问题为止）、`playground-link`。本地 `npx -y @sveltejs/mcp`，远程 `mcp.svelte.dev/mcp`。官方另有 Claude Code、OpenCode、Codex、Cursor、Pi 的插件，以及配套 skill 和 `svelte-file-editor` 子代理；`npx sv add ai-tools` 一步装好并写进 `AGENTS.md` | https://svelte.dev/docs/mcp/overview · https://svelte.dev/docs/ai/local-setup · https://svelte.dev/docs/cli/ai-tools | 2026-11 抓取 | 高 |
| F10 | shadcn-svelte：社区移植（非官方，得到 shadcn 本人同意），底座 Bits UI；当前 1.8.1（2026-11-11），组件为 Svelte 5 runes 原生写法，支持 Tailwind v4；近 24 个月 39 次发布；约 9.1k stars（2026-09-17）。2026-10 加了流式对话组件（Message Scroller：跟随流式输出、加载历史不跳动）。与上游有对齐缺口（issue 里有 `parity` 标签） | https://github.com/huntabyte/shadcn-svelte/releases · https://www.shadcn-svelte.com/docs/changelog · https://ross.abutalabs.com/products/shadcn-svelte | 2026-11 | 高（发布与功能）；stars 为中 |
| F11 | SvelteKit 当前主线是 3.x（3.2.0，2026-11-10），2.x 仍在发补丁（2.70.4）。Kit 3 把配置挪进 `vite.config.js`。shadcn-svelte 在 2026-10 上旬挂着 Kit 3 相关 issue：#2915「CLI 加 form 组件失败」（标了有绕法）、#2917「文档示例待更新」 | https://github.com/sveltejs/kit/releases · https://github.com/huntabyte/shadcn-svelte/issues | 2026-11 | 高；两个 issue 的现状没复核 |
| F12 | SvelteKit 做纯客户端应用：`adapter-static` 加 `fallback`，产物是纯静态文件，可放 Workers 静态资源。官方提醒：纯 SPA 首屏要多几次往返，建议能预渲染的页面都预渲染 | https://svelte.dev/docs/kit/single-page-apps · https://svelte.dev/docs/kit/adapter-cloudflare | 2026-11 | 高 |
| F13 | 采用度：npm 周下载 React 约 1.75 亿、Vue 约 1,590 万、Svelte 约 610 万（2026-10）；Stack Overflow 2025 使用率 44.7% / 17.6% / 7.2%，Svelte 是「最受喜爱」的框架（62.4%） | https://epicmax.co/blog/react-vs-vue-vs-svelte · https://svar.dev/blog/react-vs-vue-vs-svelte-for-modern-web-apps | 2026-10 | 中（二手转述，数量级一致） |
| F14 | Svelte 5 的 `mount()` 目标可以是 `Document`、`Element` 或 `ShadowRoot`；事件委托的监听同时挂在 mount 目标和主 `document` 上 —— 所以把组件挂进置顶小窗的 `body`，小窗里的点击由目标上的监听接住。没找到「Svelte 5 + Document PiP」的现成实例 | https://svelte.dev/docs/svelte/imperative-component-api · https://raw.githubusercontent.com/sveltejs/svelte/svelte@5.57.2/packages/svelte/src/internal/client/render.js | 5.57.2 | 中（读源码推断，S3 穿刺实测） |

## 4. 冲突与订正

- **macOS 抓系统声**：第一轮有两个来源（OpenPWA 2026-09、RTMA）说 macOS 的 Chrome 只能抓标签页声音。第二轮回到 Chromium 源码提交（A6）裁定：**能抓**，那两篇是旧信息。
- **本仓 `RESEARCH-soniox-v6-20261103.md` 第 89 行**写「2027-01-15『只把 key 放在 protocols 里会被拒』」，与 Soniox 官方原文不符：被停用的是「`api_key` 写在 start 消息里」，protocols 方式是浏览器的正式做法（B2、B3）。桌面端结论不受影响（它用请求头）。**那一行没改**，动 Soniox 鉴权前以官方页为准。
- **`text-autospace` 的浏览器支持**：MDN 标「2025 起各主流浏览器可用」，caniuse 的数据不一致（https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/text-autospace · https://caniuse.com/wf-text-autospace ）。没裁定 → 网页版不依赖它，当锦上添花。
- **State of JS 2025 里 Svelte 的使用率**：两篇写约 27%（epicmax、svar），一篇写「不到 10%」（strapi，疑似把 Stack Overflow 的 7.2% 混了进来）。没回到 stateofjs 原始页核 → 这个数不进决策，采用度以 npm 下载量为准（F13）。

## 5. 没查到 / 未核

- PiP 小窗能否半透明、能否鼠标穿透：没找到原文（有标题栏已确认，见 A3）。能否浮在 macOS 全屏应用（全屏 PPT、全屏 Zoom）上面：没查到，要真机试。
- 整课笔记的峰值内存；流水线在 Linux 上跑是否与 macOS 逐项一致（`extract.py` 用了 PDFKit）。
- Worker 现在铸出来的 Soniox 临时 key 是不是 `snx_temp_` 格式。
- InstaBox 的服务条款是否允许拿它跑对外的后台任务（只查到免费档禁止搭代理，有封号先例）。
- 中国大陆浏览器直连 Soniox 的稳定性：只有桌面端的数据（2026-10-29 一位中国用户：默认走日本区，RTT 232–305 ms；美 / 欧区 3 s 内全超时。依据见 `PLAN-stream-reconnect-20261029.md`）。
- Cloudflare Queues 的空拉取算不算操作次数。
- Soniox 自带翻译的中文质量对比 DeepSeek + 术语注入：没测。
- 桌面版字幕停顿的原因（§6）：两种候选机制都没证实；朋友机器上有没有同样的停顿没统计。
- Worker 铸出来的临时 key 前缀：读了 `server/asr-worker/src/soniox-core.js`，请求体是 `usage_type: transcribe_websocket`、`single_use: true`，注释里写着前缀 `snx_temp_`；实际返回值没看（不打印 key）。
- shadcn-svelte #2915（SvelteKit 3 下 CLI 加 form 组件失败）现在是否已修：搭脚手架当天复核。
- 网页与接口同源（静态站 Worker 用 service binding 转发 `/v1/*`）时，转发算不算第二次计费请求：没查。
- Svelte 5 组件挂进 Document PiP 小窗的现成实例：没找到（F14 是读源码推断）。

## 6. 桌面版体验数据（实测，只有作者一台 M1 Pro）

量法：`sessions/*.report.json` 的 `capture.gaps`、`blocks.ui.streamq_dwell_ms`、`resource.rss_peak_mb`；`sessions/*.live.jsonl` 的 `startup` 事件。范围：2026-10-20 → 11-10 的 12 节真课（Soniox 时期；不含名字带「测试」的和两节 5 秒的试跑）。脚本是一次性的，引用前重算。

**「停顿」是怎么量的**：`testmode._capture_tick` 记的是主循环**相邻两次拿到音频块**的墙钟间隔，超过 1.0 s（`CAPTURE_GAP_S`）记一条。所以一条停顿有两种可能：主循环自己卡住了（音频在 20 秒缓冲里等着，没丢）；或者主循环在转、声卡那头没送块来（这段音频可能真没录到）。

### 6.1 停顿（U1）

| 项 | 数 |
|---|---|
| 出现停顿的课 | 12 节里 11 节 |
| 每节条数 | 中位 4，最多 12（11-09 SOC10020） |
| 单条时长（共 53 条，另有 3 条 113–366 s 在 10-20 同一节，疑似合盖，未核） | 中位 1.42 s · p90 3.5 s · ≥ 3 s 的 8 条 · 最长 11.9 s |
| ≥ 6 s 的 | 3 条：11.9 s（10-20）、6.45 s（10-23）、6.43 s（11-03） |

**两条证据线**：

1. **主循环卡住（6 节直接对得上）**：这 6 节里，streamq 最长停留时间与最长停顿几乎相等 —— 11-09：3.84 / 3.86 s；11-10 第二节：2.84 / 3.20 s；10-20 第一节：1.98 / 2.18 s；11-03 12:33：1.72 / 1.80 s；11-03 15:25：1.67 / 1.79 s；11-05：1.50 / 1.60 s。streamq 由主循环的 `drain()` 消费，停留变长 = 主循环没在转。
2. **那 3 条 ≥ 6 s 的不一样**：所在的课 streamq 最长停留只有 0.8–1.1 s，主循环大概率没卡那么久 → 更像声卡那头没送块（候选：换输入设备时重开流、系统音频服务重启）。**这 3 条可能是真丢了音频**，没证实。

**和界面操作的关系**：把停顿和 `live.jsonl` 里的事件对时间（55 条，含 2 节测试课的 3 条）。有记录的界面操作只有「回顾」按钮：全部数据里约 100 个回顾事件，却有 3 条停顿（2 个时间点）前后 3 秒内正好在点回顾按钮 —— 按随机撞上算，期望不到 1 条。拖窗、开菜单栏菜单没有任何日志，对不了。其余事件（封口、段尾）出现的比例和随机撞上差不多，说明不了问题。

**现有假设**（未证实）：主循环是手写的（`overlay.pump()` 里 `nextEventMatchingMask` + `sendEvent_`），按住按钮 / 拖窗 / 开菜单时 AppKit 进入自己的跟踪循环（D7），`sendEvent_` 要等鼠标松开才返回；而「取音频 → 送 Soniox → 切段」（`main.py` 主循环里的 `src.poll()` / `stream.feed()` / `seg.accept()`）就在这条循环上。别的候选：`drain()` 里某一类事件处理慢、`seg.accept()` 在段尾同步做了重活、别的线程长时间占着解释器锁。分辨它们要靠探针（任务单 U1-A）。

### 6.2 启动（U2）

| 项 | 数 |
|---|---|
| 点「开始上课」到悬浮窗出现（12 节真课） | 中位 3.95 s；≥ 5 s 的 5 节；最慢 22.4 s（10-20，其中「翻译与术语」一段占 18.1 s，那之后没再出现） |
| 10-23 起的 10 节（有首屏小窗打点 `hud_s`） | ≥ 5 s 的 4 节（5.2 / 5.9 / 6.5 / 8.1 s），**这 4 节的 `hud_s` 全都 ≥ 3 s**（3.1 / 4.5 / 5.4 / 6.4 s）；其余 6 节 `hud_s` 0.5–1.9 s |
| 同期其他各段 | 收音 ≤ 0.83 s · 翻译 ≤ 0.74 s · 悬浮窗 ≤ 0.27 s · 草稿 ≤ 1.2 s —— 都稳定 |

结论：现在的慢启动全部慢在 `run()` 开头到首屏小窗出来这一段（单实例锁 → 清理 → 老 key 搬家 → 麦克风权限 → 开首屏小窗），而这一段内部没有打点（`CLASSLIVE_STARTUP_TRACE` 的第一个点就是 `hud`）。4 节慢的里有 3 节是当天第一次开，猜测与冷启动（首次加载 AppKit / PyObjC）有关，**未证实**。

### 6.3 其他

| 项 | 数 |
|---|---|
| 整机内存峰值（U3） | 1.07–2.15 GB；Soniox 在出字幕时本地草稿模型照常加载 |
| 一句说完到中文出现（U4） | 中位 1.55 s · p90 2.0 s；其中封口约 1.1 s、翻译首字约 0.45 s |
| 事故（U5） | 12 节里 3 节：11-03 一节 Soniox 不可用 821 s；11-05 一节实时总结 155 次调用失败；11-09 一节掉线 67 s |
| `zh_overflow` | 不引用：这个计数记的是更新事件不是句子，口径不适合当体验指标 |
