# ClassLive 网页版（web/）

免安装入门版：打开网址 → 允许麦克风 → 输通行码 → 双语字幕 → 下课出笔记。Zoom 全屏网课用桌面版（Document PiP 上不了 macOS 全屏 Space）。
方向与闸门：`docs/PLAN-web-cloud-20261114.md`；任务单：`docs/TASKS-web-cloud-20261114.md` P5。

```sh
npm install
npm run dev        # 开发（/api 经 vite 代理到线上 Worker，见 .env.development；线上 Worker 部署 CORS 之前只能这样跑）
npm run vendor     # 自托管 Pyodide / pdf.js → static/vendor/（prebuild 自动跑，已存在跳过）
node scripts/e2e-live.mjs --wav <16k wav> --secs 120   # 真链路端到端（Chrome 假麦，test key，会花钱）
npm run check      # svelte-check，须零错误
npm run build      # 纯静态产物 → build/
npx wrangler dev   # 本地按静态 Worker 跑 build/
```

技术：SvelteKit 3（`adapter-static`，`ssr = false`，开课页 / 说明页预渲染）+ Svelte 5 runes + Tailwind v4 + shadcn-svelte（vega / neutral）。部署是只有 `assets` 的 Worker（`wrangler.jsonc`），与桌面版接口 Worker 分开。

## 模块边界（界面只认 `state`）

| 目录 | 管什么 | 对外接口 |
|---|---|---|
| `lib/audio/` | 麦克风、worklet 重采样、电平；设备清单 / 插拔通知，上课中途换设备只换流不重连 | `AudioSource.start(onFrame)`、`level`、可选 `switchDevice(deviceId)` / `onDeviceLost(cb)`；`listMics()`、`onDevicesChanged(cb)` |
| `lib/live/` | 上课页唯一的真链路装配：先领一把 Soniox key（同时查码/额度、拿 ws_url）→ 麦克风 + Soniox + Pyodide 核心 + 翻译 + IndexedDB 落盘（一句译完才写，下课补写）；术语表进 Soniox context 与翻译 user 段；防睡 / 后台标签页记录（`wake.ts`）；忘关网页的自动下课判据（`autoend.ts`，纯状态机，靠已有的音频帧喂） | `connectLive(course)` → `{ session, store, session_id, end() }`；`holdWakeLock()` → `{ active, release() }`、`watchHidden(cb)`；`createAutoEnd({ now, quietWarnS, hiddenQuietWarnS, noSpeechWarnS, countdownS, quotaWarnS })` → `{ tick({ text, hidden, quotaLeftS }), cancel(), state }` |
| `lib/asr/` | Soniox 连接、重连、临时 key | `send(pcm)`、`onMessage(raw)`、`state` |
| `lib/core/` | 实时核心（Pyodide Worker）；Pyodide 本体同源优先（`static/vendor/`，`npm run vendor` 下）、jsDelivr 兜底 | `feed(raw)`、`tick(seq)` → `onView(ViewState)`；装载面 `PYODIDE_VERSION` / `PYODIDE_CDN` / `PYODIDE_VENDOR` / `originBase()` / `importPyodide()` |
| `lib/translate/` | 翻译接线：提示词由 Pyodide 里的 `prompt_core.py` 拼（`build_request`），流式 POST `/v1/chat/completions` 增量解析 SSE，定稿句串行、草稿句只译最新（旧的 abort），失败重试一次后标「没译出」 | `Translator`（`final` / `draft` / `noteFinal` / `close`）、`createPrompt` / `pyPromptFromPyodide`、`streamChat`、`latencyOf` |
| `lib/session/` | 把采音 / 识别 / 核心接成一次真会话，投影成界面形状（与 `demo` 同形）；磁带源可脱网回放 | `LiveSession.start/end`，只读 `rows/draftEn/draftZh/playing/status/level/message`；`tapeReplay` |
| `lib/live/` | 上课页唯一的真链路装配：先领一把 Soniox key（同时查码/额度、拿 ws_url）→ 麦克风 + Soniox + Pyodide 核心 + 翻译 + IndexedDB 落盘（一句译完才写，下课补写）→ 顺带把句子同步到 Worker；术语表进 Soniox context 与翻译 user 段 | `connectLive(course)` → `{ session, store, session_id, sync, end() }`；`attachSync({ store, local_session_id, course, api, clock })` → `state()` / `push` / `flush` / `end` / `stop` |
| `lib/state/` | 唯一的界面状态 | `view.current`（只读） |
| `lib/store/` | IndexedDB 逐句落盘（打不开退回内存，`persistent` 告知）、同步队列、本机 30 天清理；课名可课后改（`renameSession`，空课名开课合法） | `openStore()` → `append` / `pending` / `ack` / `listSessions` / `linesOf` / `renameSession` / `deleteAll` / `purgeOlderThan`；`createSyncer({ store, putLines, session_id, clock })` → `push` / `flush` / `state` |
| `lib/api/` | 到 Worker 的接口层：安装号 / 通行码 / 请求头（`identity`）、唯一网络出口与错误归一（`http`）、领 Soniox 临时 key 与查码（`soniox-key`）、网页会话账本七个接口（`web-session`：开课 / 追加逐句 / 下课 / 查 / 列表 / 改课名 / 删光）、术语三个（`web-terms`：拉个人术语 / 写一门课 / 共享池） | `installId()`、`passcode.get/set`、`isPasscodeShape()`、`headers(call?)`、`request(path, body, {deadlineMs, method, call, headers})`、`getSonioxKey(region?)`、`createKeySource()`、`checkPasscode(code)`、`startSession/putLines/endSession/getSession/listSessions/renameSession/deleteAll/getTerms/putTerms/sharedTerms`（`api.*` 同形）、`ApiError{kind,reason,message,status}` |
| `lib/after/` | 下课后的收尾逻辑（不碰界面）：给这节课起名——本机改名优先、术语快照并入那门课并推上云、服务端改名失败就排队 | `renameCourse({ store, api, local_session_id, course })` → `{ ok, addedTerms, serverQueued }`；`retryPendingRenames(api)` → 本轮补发成功数；`normalizeCourseName(raw)` |
| `lib/prep/` | 开课前的准备（浏览器版）：课件 → 候选术语。PPTX/DOCX 抽块与全部判断跑 Pyodide 里的`static/core-py/extract.py` / `prep.py`（桌面同源切片，`sync-core.py --check` 钉着），PDF 走 pdf.js（固定版本、不进主包、同源优先 jsDelivr 兜底；扫描件不支持，明说）。⚠️ 不写术语表、不碰 localStorage —— 由界面接 `lib/env` | `prepareTerms(files, course, { py, fetch?, pdf?, onProgress?, limit? })` → `{ terms: string[], stats: { files, blocks, chars, chunks }, failed: [{ name, reason }] }`；`stagePrep({load, read, manifest})` → `PrepPython`（`pptxFile` / `docxFile` / `prompt` / `chunks` / `user` / `score`）；`pdfBlocks(data)`；`PDFJS_VERSION` / `PDFJS_CDN` / `PDFJS_VENDOR`、`pdfModuleUrl()` / `pdfWorkerUrl()` |
| `lib/terms/` | 术语上云与同课共享（客户端唯一有网络的一处）：拉个人术语并进本机（每门课比 `updated_at`，本机有没推上去的改动时本机赢再补推）、推送失败排队 `cl.terms.pending.<课名>`、共享开关 `cl.shareTerms`、开课要用的个人 + 共享术语（共享池拉不到 / 超时就当没有；个人在前，共享按服务端顺序补位，≤300） | `pullTerms(api)` → `{ updated }`；`pushTerms(api, course, text)`、`saveTermsSynced(api, course, text)`、`retryPendingTerms(api)` → 补发成功数；`shareEnabled()` / `setShareEnabled(v)`；`termsForClass(api, course, { timeoutMs })` → `{ personal, shared }`；`mergeForContext(personal, shared)` |
| `lib/components/ui/` | shadcn-svelte 源码 + 自家 token | — |

## 规矩
- 每个 `.svelte` 文件过 `svelte-autofixer` 到零问题；只用 runes；`svelte-check` 零错误。
- 逻辑不靠 `setInterval` 推时钟：挂在音频帧节拍或 WebSocket 消息上。
- 课堂数据不进仓库；要花钱的调用只用 test key。
