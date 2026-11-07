# PLAN · LLM 路由重做（CommandCode 长调用通道）· 2026-11-07

状态：**已落地（2026-11-07）** —— 提交 `ad566cc` + `e726a55`，Worker 版本 `680e2a65-bbf9-4bf2-926a-635ab6d71159`

### 落地状态（与下文设计的差异，读设计前先看这段）

| 设计 | 实际 | 备注 |
|---|---|---|
| 按 `X-ClassLive-Call` 路由 | ✅ 代码就绪，**但客户端还没发这个头** | 生产实际按"`max_tokens >= 4000` 当 note"运行；atom 900 / chapter 1400 因此回 DeepSeek |
| atom / chapter 回 DeepSeek | ✅ | 已线上核验：`atom` 类 5 次全部 `deepseek:200` |
| 笔记类留 CC | ⚠️ 部分 | 只有 ≥4000 的走 CC；1500–3000 的笔记子调用现在也回 DeepSeek（更快、且 402 有救援兜底） |
| DeepSeek 4xx → CC 救援、预算 ×2 | ✅ | `chat()` 的 `DS_RESCUE_STATUS` 分支 |
| Worker 内容级校验 | ✅ | `cmdContentTag`：空 / length / 非 JSON 一律回退且不记成功 |
| 冷却补 timeout / 5xx | ✅ | 60 s 短冷却；`cmdCoolDown` 取 max，不再互相覆盖 |
| 观测三列 | ✅ | `blob9` finish、`blob10` 调用类、`double12` 可见字数、`double13` 推理 token |
| 客户端 `length` 一律当失败 | ✅ | 只在"没传 effort 且预算 <4000"时翻倍重试（不跨 4000、不与块级重试相乘） |

线上核验（AE 聚合，`test1`）：`atom` 900 → `deepseek:200`；`legacy` 700 → `deepseek:200`；`note` 4000 → `cmd:200`。
⚠️ AE 是抽样采集，**明细 SELECT 会漏行**，对账要用 `GROUP BY ... SUM(_sample_interval)`。
触发：11-06 美国用户一节 atom 提取 59 次调用失败 28 次（47.5%），25 次是内容坏，不是断网。

## 0. 一句话

**CommandCode 的 Provider API 不认 `thinking:{"type":"disabled"}`（其 issue #697，2026-09-09 复测仍未修），DeepSeek V4.1-Flash 默认开思考，推理 token 计入 `completion_tokens` 并吃掉 `max_tokens=700` 的预算 —— content 要么被切半、要么直接被 JSON mode 洗成空串。**
所以不是"CC 不好"，是**我们把必须严格 JSON、预算只有 700 的调用，送去了一条会偷偷开思考的通道**。

## 1. 证据链

### 1.1 生产数据（AE + 客户端日志）

| | 11-04 DeepSeek 直连 | 11-06 经 CommandCode |
|---|---|---|
| 调用数（max_tokens=700） | 52 | 44 |
| completion tokens 最大值 | **564（从未撞顶）** | **700（约 17 分钟撞顶）** |
| 平均耗时 | 2314 ms | 7263 ms |
| 内容坏 | 0 | 25（13 次 `char 0`、11 次 `Unterminated string`、1 次列 680） |

输入 token 两边一致（max 1199 vs 1200）—— prompt 没变。
账对得上：客户端 59 次 = 56 次到达 Worker（44 CC + 12 回退）+ 3 次断网；失败 28 = 3 断网 + 25 CC。

### 1.2 外部事实

- CommandCode Provider API 文档 / changelog（访问 2026-11-07）：`/provider/v1/*` 是它自建的 OpenAI 兼容网关，不是 OpenRouter；多上游、可挑 serving provider。
- Issue #697（2026-08-18，2026-09-09 复测）：`thinking:{"type":"disabled"}` 返回 200 但**被忽略**，`deepseek-v4-flash` 仍产生 reasoning tokens。`reasoning_effort:"none"` 直接 400，只收 low/medium/high/xhigh/max。
- Issue #685（2026-08-16，仍 open）：`response_format` 曾被 400 拒绝；#1089（2026-10-29）有 200 记录，但无文档保证透传并强制执行。
- DeepSeek 官方：reasoning tokens 计入 `completion_tokens` 且占用 `max_tokens`；JSON mode 遇到非 JSON 会替换成空格 → **"空 content + finish_reason=stop"**。

### 1.3 代码事实（`文件:行号` 已核）

- `server/asr-worker/src/handler.js:152-157` — `useCmdChat` 只用 `max_tokens > 400` 判定"长调用"，不看 `response_format`、不看调用方能否容忍失败。
- `handler.js:203-208` — `cmdChatAttempt` 只校验外层信封（`choices` 非空），**不校验 `message.content`**，坏响应按 200 记账、扣额度、面板不可见。
- `handler.js:193` — 冷却集只有 401/402/403/429，**timeout / 5xx 永不冷却**（章节 10/10 每次白等 10 s）。
- `handler.js:163-181` — 定时器只覆盖到响应头，`r.text()` 可被慢滴流无限拖住。
- `llm_client.py:136-141` — `finish_reason=length` **只在传了 `effort` 时**才处理；atom 不传 effort → 截断静默变成 `JSONDecodeError`。
- `live_summary.py:599` + `atom.py:55` — atom 预算 700，失败吞成 `None`，30 s 重试 3 次后跳窗（`:555-570`），丢的是要点。

## 2. 设计原则

1. **按"输出契约"路由，不按 token 上限路由。** 需要严格 JSON 的短调用和生成式长文是两种物种。
2. **兜底通道必须能力等价。** 换通道不能改变输出契约；不保证的字段（thinking / response_format）要当成"没有"。
3. **校验放在能回退的地方。** content 级的合格判定必须在 Worker（有回退能力），不能只靠客户端（只能报错）。
4. **坏响应不许记成成功。** AE 的 `up` 必须能区分 `cmd:200` 和 `cmd:200:content`。
5. **兜底是兜底，不是主通道。** CC 慢 3 倍、章节连头都不回，没有证据表明它该承担日常流量。

## 3. 路由表（目标态）

| 调用类 | 调用点 | 特征 | 主通道 | 兜底 |
|---|---|---|---|---|
| `translate` / `draft` / `explain` | `cloud_translator.py:431/452/480/499/535/547` | stream，≤1400 | OpenRouter 快路（非 CN）/ DeepSeek（CN） | DeepSeek |
| `atom` | `live_summary.py:599` | 非流，700，**必须严格 JSON** | **DeepSeek** | CC（仅 4xx，且预算 ×2） |
| `chapter` | `live_summary.py:780` | 非流，1400，严格 JSON | **DeepSeek** | CC（仅 4xx） |
| `recap` / `classify` / `keypoints` / `merge` | `recap.py:111`、`classify.py:219`、`note_merge.py:125` 等 | 非流，≤1200，严格 JSON | **DeepSeek** | CC（仅 4xx） |
| `note` / `polish` / `prep` | `note_v2.py`、`polish.py:140/217`、`prep.py:949` | 非流，**≥4000**，生成式 | **CC**（thinking 本来就要） | DeepSeek |

判定输入：客户端显式发 `X-ClassLive-Call: <class>`（Worker 只认白名单，不认的当 `legacy` 走 DeepSeek）。
**不再用 max_tokens 猜。**

## 4. 改动清单

### A. Worker 路由（`server/asr-worker/src/handler.js`）

1. `useCmdChat`（`:152-157`）改成读 class：
   ```js
   const CMD_CLASSES_DEFAULT = "note,polish,prep";
   export function useCmdChat(env, body, callClass) {
     if (!(env.CMD_CHAT_KEY || env.JEV_CC_KEY)) return false;
     if (body?.stream) return false;
     if (Date.now() < cmdChatDownUntil) return false;
     const ok = String(env.CMD_CHAT_CLASSES ?? CMD_CLASSES_DEFAULT).split(",").map(s=>s.trim());
     return ok.includes(callClass || "legacy");
   }
   ```
2. 新增"DeepSeek 挂了才用 CC"的救援开关：`useCmdChatRescue(env, body, deepseekStatus)` —— 4xx∈{401,402,403,429} 时返回 true，且 `max_tokens` 至少 ×2（`:404` 之前调）。
3. 冷却集加 `0`（网络异常）和 `>=500`，冷却 60 s；content 连续 2 次坏 → 10 分钟。

### B. Worker 内容级校验（`:203-208` 之后）

```js
const c = j?.choices?.[0];
if (typeof c?.message?.content !== "string") return { r: null, tag: `cmd:${r.status}:nocontent` };
if (c.finish_reason === "length")            return { r: null, tag: `cmd:${r.status}:length` };
if (body.response_format?.type === "json_object") {
  const s = c.message.content.trim();
  if (!s.startsWith("{"))                    return { r: null, tag: `cmd:${r.status}:notjson` };
  try { JSON.parse(s); } catch {             return { r: null, tag: `cmd:${r.status}:content` }; }
}
```
命中任一 → `r=null` → `chat()` 自动回退 DeepSeek，客户端无感。

### C. Worker 正文超时（`:163-181`）

`fetchCmdChat` 的定时器不要 `clearTimeout` 在响应头之后；再加一个 `CMD_CHAT_BODY_MS`（默认 30 s）覆盖 `r.text()`。

### D. 客户端（`llm_client.py:130-146`）

- `finish_reason == "length"` **一律**当失败（不只 effort 路径）；若 `max_tokens < 16384`，用 1.5× 预算重试一次，仍 length 才抛。
- 记录 `usage.completion_tokens_details.reasoning_tokens`，`>0` 且 `finish_reason==length` 时在异常消息里点名"preasoning 吃了预算"。
- atom 预算 700 → **900**（DeepSeek 实测最大 564，留 60% 余量）。

### E. 发版卫生（不是可选项）

`git status` 显示 `handler.js` 未提交，而 AE 证明它已在服务。**先提交、打 tag、再改**，否则这次改动本身也没法回滚。

## 5. 观测（改完必须能看到）

`handler.js:317-320` 的 doubles / blobs 补三个字段：

| 字段 | 用途 |
|---|---|
| `finish_reason` 枚举 | 区分 length / stop / null |
| `visible_chars` | content 长度，0 = 空 |
| `reasoning_tokens` | 判断是不是思考吃预算 |

`ev.up` 的标签体系扩成：`cmd:200` / `cmd:200:content` / `cmd:200:length` / `cmd:200:notjson` / `cmd:200:nocontent` / `cmd:timeout>` / `cmd:5xx>`。
面板「要注意」加一行：**近 10 分钟 `cmd:*:content|length|notjson` > 0 就亮红**。

## 6. 验收判据

| # | 判据 | 怎么量 |
|---|---|---|
| 1 | atom 失败率 ≤5% | 一节 50+ 次调用，`failed_atom_calls / atom_calls` |
| 2 | 章节零 `cmd:timeout>` | AE 按 up 聚合 |
| 3 | 坏响应可分账 | AE 里能查 `cmd:200:content` 的计数 |
| 4 | 客户端能识别截断 | 构造 `finish_reason=length` 的桩，单测必须抛带 "reasoning" 字样的错 |
| 5 | 回退无感 | 把 CC key 改成错的，一整节 atom 成功率不掉 |
| 6 | 有回滚点 | `git tag` + `wrangler deployments list` 对得上 |

## 7. 上线顺序

1. 提交工作区（`handler.js` / `wrangler.toml` / `package.json` / `test-cmdchat.mjs`），打 tag。
2. 补 `test-cmdchat.mjs` 的 6 个新用例（content 空 / 非 JSON / length / 5xx / 正文超时 / response_format 透传）。
3. 改 A→B→C（Worker）+ D（客户端），一次发。
4. `wrangler deploy` → 用测试码跑一节模拟课，看 AE 的 `cmd:` 标签分布。
5. 过了 §6 前 3 条再放用户。

## 8. 未定项

- CC 到底认不认 `response_format`：issue #685 仍是 open，需要一次真实探针（拿 `CMD_CHAT_KEY` 打一发 700 token 的 JSON 请求，看 `finish_reason` 和 `reasoning_tokens`）。这次改动**不依赖**这个答案 —— 内容级校验对两种情况都成立。
- 笔记走 CC 后的截断风险：笔记预算 4000–14000，thinking 本来就要；但 CC 的 thinking 档位不受我们控制，仍需 §5 的 `reasoning_tokens` 观测兜着。
- `handler.js:409/414` 两条通道互斥、`stream` 真值口径不一致（`useCmdChat` 用真值、`useFastLlm` 用 `=== true`）—— 顺手统一，别在这次之外单开一次。

## 9. 相关

- `ClassLive-11-06全量上传复盘-20261107.md` §3.3b
- `docs/PLAN-note-background-20261103.md`（note_worker 那条链路）
