# 网页版上线计划：功能对照、缺口、先后顺序

2026-11-17 · 依据：三份只读盘点（`~/classlive-web-spike/parity/P1-before-class.md`、`P2-in-class.md`、`P3-after-and-server.md`，共 76 条功能，每条带 `文件:行号`），Claude 回源抽查 5 处全对（caption_flow 漂移、Worker 零 CORS、`LLM_MAX_TOKENS=16384`、四条 POST 白名单、其余 6 个拷贝逐字相同）。
上位文档：`PLAN-web-cloud-20261114.md`（阶段 S→P6）、`API-web-v0-20261117.md`（接口草案）。本文不推翻它们，只按代码现状重排先后。

## 0. 结论

1. **不先做大重构。** 网页版要复用的桌面代码大多已经是深模块（`StreamCore`、`caption_flow`、`build_context`、`select_terms`、`note_v2 / note_math / note_guards / note_render / note_html / polish` 零本地依赖），可以原样搬。真正挡路的重构只有两刀：
   - **翻译提示词拼装提成纯模块**（`cloud_translator._user_content` + `translator.select_term_parts / term_zh_map`，逐字搬，冻结测试不改）。网页 M2 要它。
   - **`ObsidianWriter.close()`（386 行）拆出 `build_note(输入) → (md, html, 台账)`**，本地路径全推到注入的存储端口后面。VPS 笔记要它。
   PLAN 里 P1（事件 dataclass / ui_port）和 P2 其余各刀（拆 `run()`）对网页版没有直接收益，留给桌面线按原计划慢慢做。
2. **按「竖切片」推进**：先让作者自己用网页上完一节课（有字幕、有翻译、刷新不丢），再接笔记，再放给朋友。每片都能单独演示。
3. **服务端先开门再接前端**：CORS 只放一个来源、网页设备单独计数，必须先于任何浏览器请求上线（否则公开令牌和通行码会被任意网页借用）。

## 1. 对照总览（76 条按去向分组）

| 去向 | 条数 | 代表 |
|---|---|---|
| 网页已有 / 大半有 | 9 | Soniox 流 + 重连 + 心跳、采音、封口核心（Pyodide，22/22）、改字 diff、字幕排版（新设计）、浏览器兼容提示、about 页 |
| 原样搬（纯函数，进 Pyodide 或 runner） | 约 22 | `build_context`、`select_terms`、`courses.resolve`、`classify.suggest`、pptx/docx 抽取、`fragment_merge`、`lang_gate`、笔记六模块、`backfill.missing` |
| 改写 TS / 新写 | 约 15 | 通行码输入与校验、安装号与三个请求头、`getKey`、IndexedDB 落盘、❓ 落盘、首次告知、开课前检查条、PDF（pdf.js） |
| 放服务端（新接口或 runner） | 约 17 | 翻译代理（已有，缺 CORS）、会话账本、笔记 runner、prep、术语只追加、实时总结、一些事儿、讲一下、点拨、课前回顾、多地区 |
| 不迁 | 约 13 | 本地 VAD / 草稿引擎 / 定稿 ASR、更新机制（2477 行）、测试模式 / 上传 / 遥测整块、note_worker、就绪条五项、暗号、tour、提醒事项 |

不迁的理由都一样：Soniox 活着时桌面版这些本来就不跑（`main.py:4008-4011`），或浏览器有现成等价物（刷新 = 更新，Wake Lock = 防睡）。

## 2. 上线缺口（按「不做会出事」排序）

| # | 缺口 | 现状证据 | 去哪片 |
|---|---|---|---|
| 1 | Worker 没有 CORS，网页一个请求都发不出去 | `grep -ci access-control server/asr-worker/src/*.js` 全 0 | A（W3 进行中） |
| 2 | 通行码从未校验，`getKey` 没实现，`api.*` 7 个函数全 throw | `lib/api/index.ts:8-27`、`env.ts:24-48` | A |
| 3 | 上课页还在放示例回放，真链路没接 | `class/+page.svelte` 只 import `demo/player` | A（W2 进行中） |
| 4 | 没有翻译 | `core/project.ts` 里 `zh` 恒空 | A |
| 5 | 刷新就丢、❓ 标记只在内存 | `grep -c indexedDB web/src` = 0 | A（W1 进行中） |
| 6 | about 页承诺了「30 天自动删 / 删除我的全部数据 / 下载笔记」，三样都没有 | `about/+page.svelte:96,129,133` | B（做不到前先改文案） |
| 7 | 桌面 + 网页共用 2 台设备名额，朋友第一天就会 403 | `passes-core.js:142-172` | A（W3 进行中） |
| 8 | 网页日额度默认不限 | `passes-core.js:213-215` 全 `null` | C 之前由 Owen 定数 |
| 9 | 没有笔记（runner 不存在，`close()` 绑本地路径） | `ls server` 只有两个 Worker；`paths.py:43` 等 12 处依赖 | B |
| 10 | 门面回放用的是作者真课 | `web/.gitignore:26` | C 之前 |
| 11 | Pyodide 拷贝已漂移，「原样拷贝」不成立 | `caption_flow.py:121` 两版不同 | A（W4 进行中） |

## 3. 先后顺序

### A 片：作者自己能用网页上一节课（约 1.5–2 周）

| 步 | 内容 | 谁 | 状态 |
|---|---|---|---|
| A1 | IndexedDB 逐句落盘 + 同步队列（`lib/store`） | pi W1 | ✅ 29 测绿（Claude 复跑） |
| A2 | 会话接线 `lib/session` + 核心 `flush` + 磁带端到端测试 | pi W2 | ✅ 6 测绿 |
| A3 | Worker：CORS（单来源，只给 soniox/key、chat、/v1/web/*）+ `devweb:` 分账 + 网页 max_tokens 封顶 | pi W3 | ✅ Worker 203 测绿（含 W8 额度） |
| A4 | 核心防漂移脚本 + 同步 caption_flow | pi W4/W5 | ✅ 漂移检查 0；切片按名字重生成 |
| A5 | 通行码输入 + 形状校验 + 安装号 / 三个头 + `getKey` 实现（`POST /v1/soniox/key`） | pi W6 逻辑 ✅ 23 测绿；界面 Claude 待写 |
| A6 | 首次告知（存什么、多久、全云端、Soniox 断了没兜底）+ 开课前两行检查（通行码 / 麦克风） | Claude | 待做 |
| A7 | **桌面侧小重构**：提示词拼装提成 `prompt_core.py`（纯，逐字搬，`tests/test_translator.py` 不改一字），进 `CORE_MODULES` | pi W7 | ✅ prompt_core 9/9 + test_translator 31/31（app python）；~/lecture-live-prompt 未提交 |
| A8 | M2 翻译：Pyodide 拼提示词 → Worker `/v1/chat/completions` 流式（`client=web`）→ 定稿译文进 ViewState；草稿译文复用已搬的 `draft_translate` | pi W10 | 进行中（含 test key 量延迟） |
| A9 | 术语注入最简版：术语表手动粘贴 / 上传 txt 存本机，进 Soniox context（`build_context`）和提示词 | pi | 依赖 A8 |
| A10 | 上课页换成真会话；❓ 写 IndexedDB | Claude | 依赖 A1 A2 |
| A11 | 真机验收：Owen 用真麦 + test key 上一节课（约 1 小时）；同时量 Pyodide 首下耗时、后台标签页 5 分钟是否断流 | Owen | A 片闸门 |

需要部署时（A3 上线 Worker、静态页上子域）先问 Owen。

### B 片：下课出笔记（约 2 周）

| 步 | 内容 |
|---|---|
| B1 | Worker 会话账本：`/v1/web/session`、`PUT …/lines`（`(session_id, seq)` 幂等，只存定稿）、`…/end`、`GET` 两条；网页数据**单独一个 R2 桶**（现有上传桶是只写、180 天，性质不同），桶生命周期 30 天 |
| B2 | `DELETE /v1/web/data`（先删账本再删 R2）+ 网页「删除我的全部数据」 |
| B3 | 桌面侧重构：`build_note()` 从 `close()` 抽出；判据 = `notes_bench` 冻结 + 3.11.2 的三类笔记故障判据不改全绿 |
| B4 | runner（VPS，systemd，并发 1）：领任务 → 续租 → `build_note` → 写回 R2；三种故障判据（跑一半被杀 / 断网 2 分钟 / 重复领取）；KaTeX 副本随 runner 部署 |
| B5 | 课后页：进度 → 页面内看笔记 → 下载 md / html；导出 md 用桌面会话格式，桌面 `rebuild_note.py` 能直接吃 |
| B 闸门 | VPS 上 5 节真实逐字稿出笔记，记每节耗时和峰值内存（test key） |

### C 片：放给朋友（约 2–3 周，可按需拆）

先做：课件上传（pdf.js 在浏览器抽字，pptx/docx 进 Pyodide，扫描件先不支持）→ runner 跑 `prep` → 术语表回到浏览器；课程清单（服务端存）；Owen 定日额度；换门面回放片段；中国大陆实测。
后做：实时总结（服务端状态机，`live_summary` 依赖全注入可直接搬，浏览器轮询）、❓ 一行点拨、讲一下 / 追问、课前回顾、一些事儿（ics 代替提醒事项）、多地区路由（跟临时 key 一起下发）、语意封口、网课音源 `getDisplayMedia`。

## 4. 要 Owen 定的（到对应片之前定即可）

| 问题 | 建议 | 什么时候要 |
|---|---|---|
| 提示词在哪拼 | **已定（Owen 11-17）**：浏览器拼 + Worker 校验（调研见 §6） | — |
| 网页日额度 | **已定（Owen 11-17）**：每码每天 5 小时实时转写；全局默认 `WEB_DAILY_RT_S` 可调，单码 `cl_admin limit --web-rt-s` 可调（pi W8 实现中） | — |
| 网页数据 R2 分桶 | 分桶（盘点 P3 R8：同桶会破坏上传桶「只写」纪律），草案里写的「待定」改成定论 | B1 之前 |
| 实时总结 / 讲一下放服务端 | 放服务端；网页版第一版不带 | C |

## 5. 风险

- 桌面 main 还在动（main 比本分支多 9 个提交，暂未碰 `server/`）。A7、B3 两刀在桌面仓库做，挑不上课的时段，过回放判据再合。
- Soniox 断线时网页没有本地兜底，这节课就没字幕；要写进首次告知，不能藏。
- 后台标签页定时器降频可能让 keepalive 掉线，A11 实测；不行就把心跳挂在 worklet 帧上（已是计划写法）。
- 文档里的「已完成」容易被误读：HANDOFF 里 M1-a/M1-b 是测试绿，不是页面能跑；API 草案是文档，不是代码。

## 6. 调研：翻译提示词在浏览器拼还是服务端拼（2026-11-17）

| 结论（原子） | 来源 | 源日期 / 抓取 | 置信度 |
|---|---|---|---|
| 系统提示词不应被当成秘密，也不该当安全控制；真正的风险在授权、额度这些模型外的控制 | OWASP Top 10 for LLM 2025 · LLM07（genai.owasp.org/llmrisk/llm072025-system-prompt-leakage） | 2024-11 / 11-17 | 高（canonical） |
| 设计时就假定提示词会泄露；安全控制放在应用层 | AWS Security Blog「Designing for the inevitable: System prompt leakage」 | 2026-07-08 / 11-17 | 高（与 OWASP 一致） |
| 结构性做法是服务端拼：客户端只送用户内容，网关选模板 | Belgavi.AI Lab「Confidential Prompts, in depth」 | 2026-10-02 / 11-17 | 中（单个社区源） |
| Cloudflare Python Workers 已 GA（2026-09-21），跑在 Pyodide 上 | blog.cloudflare.com/python-workers-ga | 2026-09-21 / 11-17 | 高 |
| Workers 免费档每请求 CPU 10 ms、内存 128 MB、启动 1 s | pythonresources.com、creuto.com 两篇转述 Cloudflare limits 页 | 2026-09 / 11-17 | 中（未直接回源 limits 页） |
| DeepSeek 前缀缓存要求前缀逐字节一致，与谁拼无关 | api-docs.deepseek.com/guides/kv_cache | 现行 / 11-17 | 高 |

**对本项目的含义**
- 浏览器拼不会泄露 key（key 在 Worker），泄露的只是提示词；提示词本来就在仓库里，按 OWASP 不算风险。
- 真风险是「通行码 + 透传」= 一个能发任意内容的模型接口。这个风险**桌面版今天就有**（Python 端拼好整条 messages 发给 Worker），网页版不新增，只是更容易复制。
- 服务端拼要么在 JS Worker 里再写一份提示词（与冻结测试的 Python 版漂移），要么上 Python Worker / VPS（免费档 10 ms CPU，多一个部署单元）。现在做代价大于收益。

**建议**：浏览器拼（Pyodide 跑 `prompt_core.py`，与桌面同一份）+ Worker 做确定性校验：
1. 网页请求的 `max_tokens` 封顶 400（W3 已做）；
2. 系统提示词白名单：Worker 存当前几版系统提示词的哈希，不在名单里拒；
3. 用户段长度上限（按术语表 + 上下文窗口的实际最大值留余量）；
4. 模型白名单（已有）+ 每码日额度（转写 5 小时已定；token 额度可随后定）。
出现滥用再把拼装挪到服务端：`prompt_core.py` 是纯模块，到时候放进 Python Worker 或 VPS 都只是换宿主。

## 7. 体验闸门：网页版不能比桌面差（Owen 11-17：「最起码等同 app，甚至更好」）

桌面基线取自 `RESEARCH-web-cloud-20261114.md` §6（12 节真课）。网页版每片合并前用同一口径量，**差于基线不算完成**。

| 体验 | 桌面基线 | 网页闸门 | 怎么量 |
|---|---|---|---|
| 一句说完 → 中文出现 | 中位 1.55 s · p90 2.0 s（封口 ≈1.1 s + 译文首字 ≈0.45 s） | 中位 ≤ 1.55 s · p90 ≤ 2.0 s | 磁带回放 + test key，浏览器里打点（A8） |
| 英文草稿跟手 | Soniox token 到上屏 | token 到上屏 ≤ 100 ms（同一帧内） | Pyodide feed→snapshot→渲染打点 |
| 开课到能出字 | 偶发 5–6 s 慢启动 | 热启动 ≤ 2 s；首次访问 ≤ 5 s | 门面页预取 Pyodide（输通行码时就开始下）+ 缓存 |
| 断线 | 自愈，不丢句 | 自愈不丢句 + 刷新页面不丢一句 | W2 磁带断线用例 + 真机拔网 |
| 字幕流畅度 | — | 120 Hz 下 0 掉帧（门面 / 上课页已测过） | Playwright 帧计时 |
| 内存 | 整机峰值 1.07–2.15 GB | 标签页 ≤ 400 MB | Chrome 任务管理器，一节课 |
| 专有名词 | 术语注入 Soniox + 提示词 | 同一份 `build_context` / `prompt_core`，请求体与桌面逐字相同 | A8 判据 |

网页比桌面多出来的（做到就是「更好」）：免安装、回看任意一句 + 一键回到最新、标记跳回、全屏、刷新不丢、换电脑打开就是同一节课（B 片之后）。

> 2026-11-18 Owen 定：网页转写秒数**同时计入**该码的桌面日额度（合着算，W8 现状），防止网页成为桌面额度的后门。以后要分开：`passes-core.js` check() 里桌面判据改成 `rt_s - rt_web_s >= cap`，一行。
