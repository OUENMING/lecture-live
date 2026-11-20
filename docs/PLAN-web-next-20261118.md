# 网页版下一步（2026-11-18 夜，Owen 睡前定：写计划 → 落细节 → 交给 pi 推进；体验对齐桌面 3.12）

上一份：`PLAN-web-launch-20261117.md`（A/B/C 三片）。本文件只管「今晚到 Owen 起床」这一段。

## 0. 现状（11-18 05:00 实测）

- **A 片主链路已通**：Chrome 假麦（wav）→ 网页上课页 → Soniox → Pyodide 核心 → 翻译 → IndexedDB → 课后页。
  `node web/scripts/e2e-live.mjs --wav …/hall_mid_snr10.wav --secs 120`：38 句全有中文、首句 10 s、课后页能下载 md/txt。
- 本机 dev 经 vite `/api` 代理到线上 Worker（线上还没部署 W3 的 CORS）。
- **已知问题**：草稿译文没节流，2 分钟 373 次请求（桌面约每句 1–2 次）。判据已写好（`lib/translate/draft-gate.ts`，与 CPython 逐条对照绿），还没接。
- 3.12.0 与网页拷贝的核心无漂移（`sync-core.py --check --desktop ~/lecture-live`：9/9 + 切片一致）；翻译相关文件 3.12 没改。

## 1. 今晚的任务（pi 写逻辑，Claude 写界面 + 验收）

| 号 | 内容 | 谁 | 依赖 | 只许改 |
|---|---|---|---|---|
| W11 | 草稿限频接线（**W10 已顺手接在 `LiveSession.#onView`**，不再单独派）；Claude 在 W12/W13 交回后跑干净的 e2e 核请求/句 ≤ 2.5 | Claude | W10 交回 ✅ | `web/src/lib/translate/translator.ts`、`draft-gate.ts`、`tests/translate/**`、`web/src/lib/session/live.svelte.ts`（只加计数） |
| W12 | Worker 会话账本：`/v1/web/session` 开课 / `PUT lines` / `end` / `GET` / `GET sessions` / `DELETE /v1/web/data`；Durable Object 存账本，R2 存逐句；**不部署** | pi | 无 | `server/asr-worker/src/web-*.js`（新）、`handler.js` 只加路由、`wrangler.toml` 只加绑定、`test-web-session.mjs`（新）、`package.json` test |
| W13 | 网页 `lib/api` 六个函数落地 + `lib/live` 接 `createSyncer`（每 10 句或 30 s 推一次、下课 flush、离线排队） | pi | 契约 = `docs/API-web-v0-20261117.md` §3（与 W12 同一份） | `web/src/lib/api/web-session.ts`（新）、`api/index.ts`、`web/src/lib/live/sync.ts`（新）、`tests/api/web-session.test.ts`（新） |
| W14 | 防睡 + 换麦克风 + 后台标签保活：`navigator.wakeLock`（上课期间持有、可见时重取）；`listMics()` / `switchMic(deviceId)` 不断识别；标签页隐藏 ≥ 5 s 记一次 `hidden` 事件给界面 | pi | 无 | `web/src/lib/audio/devices.ts`（新）、`mic.ts`（加 deviceId 选项）、`web/src/lib/live/wake.ts`（新）、`tests/audio/**` |
| U1 | 界面：换麦克风菜单（工具条 + 「没听到声音」空态里的按钮）、同步状态小字（侧栏）、课后页「删除这台电脑上的全部记录」、about 页把还没做的承诺改成实话 | Claude | W13 / W14 | `.svelte` |
| V | 验收：每个 W 看 diff + 复跑测试 + 抽查 2 处；整链 e2e 复跑一次 | Claude | 全部 | — |

并行：W11 等 W10；W12 / W13 / W14 可同时开（文件不重叠）。

## 1b. W10 交回（05:10，Claude 已复跑 translate 29/29、core+session 30/30）

- 磁带 10 分钟、150 句、0 失败：说完→中文首字 中位 1.88 s / p90 2.42 s；拆分 段尾→封口 1.50 s + 封口→首字 0.36 s。
- 翻译段比桌面快（0.36 vs 0.45 s）；多出来的全在封口，而封口是与桌面**同一份** `stream_core.py`（纯 Python 离线量同磁带 1.39 s）。桌面 1.55 s 基线是另一节课 → 要判公平得在同一盘磁带上量桌面，见 §3。
- 浏览器 e2e（05:13，被 HMR 重挂污染，仅参考）：首字段 zhP50 0.43 s。

## 1c. 05:20 交回与验收（Claude 复跑）

| 号 | 结果 |
|---|---|
| W12 | Worker 账本 + 删除：`npm test` 221/221（原 210 + 新 11）。Claude 补：`/v1/web/*` 预检放行 GET/PUT/DELETE（W12 因 W1 用例逐字钉死没敢改），W1 用例同步改。待 Owen：course 放宽、分桶 `classlive-web`、账本接口走 `kind=rt` 设备闸。**未部署** |
| W13 | 网页同步：api 33 + live 17 + store，合计 79/79；`http.ts` 的 `RequestOptions` 加了 method/call/headers（越界但有理，老路径测试兜着） |
| W14 | 防睡 / 换麦 / 隐藏记录：18/18；换麦 seq 连续 |
| W11 | 干净 e2e（05:17，150 s）：46 句 / 107 次请求 = **2.33 次/句**（改前 9.8）；46/46 有中文；浏览器内近似延迟 p50 1.36 s · p90 2.47 s（封口近似 1.05 + 首字 0.41；段尾用「草稿最后变化」近似，权威数字看 W10 量具） |
| U1 | Claude：换麦菜单（工具条 + 没声音空态）、拔麦提示、上课持屏幕常亮、侧栏「已存本机 / 已同步 N 句」、翻译请求头与桌面同形（不带调用类） |

## 1d. 第二批（05:22 派）

| 号 | 内容 | 产出 |
|---|---|---|
| W15 | 课件 → 候选术语（浏览器版 prep：PPTX/DOCX 走 Pyodide 切片、PDF 走 pdf.js、LLM 走 Worker 白名单加 prep 一条） | `web/src/lib/prep/`；界面 Claude 接 |
| W16 | 调研：3.12 右栏实时总结怎么搬（JSPI / 两段式 / 服务端），带小原型 | `~/classlive-web-spike/web-pi3/W16-live-summary.md` |

## 1e. 09:00 第二批验收（Claude 复跑）

| 号 | 结果 |
|---|---|
| W15 | `test:prep` 18/18；`sync-core.py --check` 无漂移（stream_core / extract / prep 三份切片）；Worker 224/224。越界：`handler.js` 加 `callClassOf` 传给提示词闸（不加则 prep 白名单是死代码，认可）。Claude 补：网页 `call=prep` 的 max_tokens 上限 400 → 4000（不然候选 JSON 被截断），P8 加断言并反证过（改回 400 → 红）。Claude 接界面：首页术语框「从课件生成」（`lib/prep/browser.ts` 主线程按需加载 Pyodide）。**真机验收**：合成经济学 docx → test key → 一次 LLM、约 25 s（含首次下载 Python 运行时）→ 15 个候选全部合理、并入术语框。PDF 走 pdf.js 还没真机跑过 |
| W16 | 调研交回 `web-pi3/W16-live-summary.md`：Pyodide 没有线程 → (a) JSPI 单独不成立，等于 (b) 两段式；pi 推荐 (c) 服务端跑（保留首段、关逐段刷新，每章 2 次调用）。前提：Worker 网页 max_tokens、白名单加原子 / 章节两条、谁付钱 —— **等 Owen 定**，今晚不动 |

已知：显示器睡着时 headless Chrome 的点击 / 截图会卡死（不出帧），所以首页新按钮的截图自查留到 Owen 在屏幕前时做。

## 2. 各任务的判据（写进任务单）

- **W11**：① 单元：同一段草稿 10 次变化、间隔 0.35 s → 送翻 ≤ 3 次；换段立即可送。② 浏览器 e2e 120 s：`chat 请求数 / 句数 ≤ 2.5`，中文覆盖率不降（38/38 那次为基线）。③ 草稿中文不会消失超过 1 次刷新（看 `draftZh` 在被限频时保留上一条，不清空）。
- **W12**：① `npm test` 原有全绿 + 新测试：幂等（同 `(session_id, seq)` 重放不重复）、跨码隔离（A 码读不到 B 码的会话）、`DELETE` 后 `GET` 404、缺通行码 401、桌面请求路径行为不变。② 不写死桶名：绑定名 `WEB_DATA`（Owen 未定分桶 → 默认分桶，计划 §4 的建议），`wrangler.toml` 里写注释「待 Owen 确认」。③ 生命周期 30 天写进注释与 README，不在代码里删。
- **W13**：① 假 fetch：断网时 `pending` 累积、恢复后按序补齐、`acked_seq` 只增不减；服务端回 409/5xx 时退避不死循环。② 刷新页面重开同一 `session_id` 能续推（从 IndexedDB 的 `acked_seq` 往后）。③ 不连真 Worker（W12 没部署）。
- **W14**：① `wakeLock` 不支持时静默降级、不报错。② 换麦克风：`AudioSource` 不重建 Soniox 连接，帧序号 `seq` 连续不归零（核心的时钟挂在帧上，归零会把封口判乱）。③ 单测用假 `MediaDevices`。

## 3. 不在今晚做（要 Owen 定或要动桌面 main）

- **B3 `build_note()` 抽出**：要动桌面 main，另一个窗口在做说话人分离 → 等不冲突时段。
- **B4 笔记 runner / 实时总结（3.12 右栏）**：都要服务端 + 新的提示词白名单（W9 只放行翻译三条 system）。先写调研不写代码。
- **部署**：W3/W8/W9/W12 的 Worker、网页静态站 —— 都等 Owen 点头。
- **提示词分支** `~/lecture-live-prompt`（`web-prompt-core`，A7）合 main —— 等 Owen。

## 4. 起床给 Owen 看的

`~/classlive-web-spike/HANDOFF-web.md` 末尾「11-18 夜」一节：做了什么（带实测数）、要拍板的 4 件（部署 / 分桶 / 提示词分支合并 / 网页日额度数值确认）、怎么自己试（`npm run dev` + 真麦，5 分钟）。
