// ClassLive 定稿 ASR 代理：POST /v1/transcribe（audio/ogg Opus）-> {text, provider, model, ms, tried}
//
// 链：Groq 主力（v3 有额度用 v3，没有用 turbo —— 两个独立的每日桶，由 DO /plan 选；2.5 s）；
// 1.2 s 还没回就对冲启动千问（一次性免费额度，三个模型轮换，每个 3.5 s）；
// v3 当主力且两路都没拿到结果、预算还有剩 -> turbo 最后一道。
// 整条链总预算 5 s，客户端 6 s 截止前一定有回包。
// 日志只记供应商 / 模型 / 状态码 / 毫秒 —— **不记文本、不记音频**。
// 设计与出处：docs/PLAN-r2-cloud-final-asr.md §W、docs/RESEARCH-cloud-asr.md。
//
// 2026-10-17 另加两条独立的路（都不碰音频）：
//   POST /v1/soniox/key   实时转写：只发一把 Soniox 临时 key（查码 kind="rt"），客户端自己连 WebSocket。
//                         可选 body `{region:"us|jp|eu", purpose:"probe"}`：region 只在配了那个区的 key 时才听
//                         （没配 = 忽略回默认）；purpose=probe 发 15 s 会话上限的探测 key（事件里带 :probe，
//                         便于把探测与真上课的花费分开看）。
//   scheduled()           Cron（每小时）：把 Soniox 用量日志的音频秒按码名回收进 Passes 的 rt_s

// ⚠️ **CHAIN 只在这里定义一处**；DO 从 /plan 请求里收，避免两处列表漂移。
// 前提（2026-10-11 用 Owen 的 key 实测）：这三个能调通；带日期的快照 404；fun-asr-flash 要 format 参数没调通 -> 不进链。
const CHAIN = ["qwen3-asr-flash", "qwen-audio-3.1-asr-flash", "qwen-audio-3.0-asr-flash"];
const DO_NAME = "quota-v1";   // 要重置额度状态就改这个名字再部署

const MAX_BYTES = 512 * 1024;                 // 12 s 段约 42 KB
const INSTALL_OK = /^[0-9a-f]{32}$/;
// 每次上游调用的超时 = min(供应商上限, 剩余总预算)；总预算从进入 handler 那一刻算（含读 body、问 DO）。
const T = { groq: 2500, qwen: 3500, hedge: 1200, total: 5000 };   // ms
const SECONDS_DEFAULT = 12, SECONDS_MAX = 60;   // X-Audio-Seconds 缺失 / 非法时按 12 s 段计

const GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions";
// 新加坡（国际站）而非北京：上课时段北京网关 2.8–5.9 s，新加坡同模型网关 0.34 s、总 p50 1.5 s（RESEARCH §15.2）。
// 三个模型在国际站同样能调通、请求体不变、错误码同一份（官方 error-code 页）。
const QWEN_URL = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation";

const NO_SPEECH = 0.6;        // 全段高于它 -> 静音，Groq 会幻觉 "Thank you."
const DEGENERATE_CR = 2.4;    // 压缩比高于它 -> 重复退化
const FREE_TIER_ONLY = "AllocationQuota.FreeTierOnly";   // 唯一算「额度用完」的 code（官方 error-code 页）
const NO_WORDS = "ASR_RESPONSE_HAVE_NO_WORDS";

class Skip extends Error {}

// ---- 本 isolate 的短租（2026-10-15）----
// 两个 DO 都在欧洲：中国用户落 HKG，问一次约 210 ms（70 条 trace 的 p50）。查码与选路已经并行，
// 但上游仍要等两者里慢的那个。这里让「本 isolate 60 s 内问过」的请求不等：
//   选路：拿上次的 plan 直接起跑，同时照常发 /plan（照常预扣额度、刷新租约）—— 记账不变，决定最多旧 60 s。
//         任何会改选路的 report（冷却 / 额度用完 / 国家被拦）当场作废；作废之后才回来的旧 /plan 不写回（gen）。
//   查码：本 isolate 60 s 内放行过的（码, 设备）不等查码就起上游，但**回包前**仍等查码结果，被拒照拒、不记用量。
//         代价：刚吊销的码最多再花掉一次上游调用。没放行过的码照旧先查后调 —— 随机码刷不动上游。
// isolate 回收就全丢，最坏退回原来的「并行两个来回」。
const lease = { plan: new Map(), pass: new Map(), ms: 60_000, gen: 0 };
// 测试用：清空并设租期（0 = 关掉，退回逐次问 DO）。
export function _leaseForTests(ms) { lease.plan.clear(); lease.pass.clear(); lease.ms = ms; lease.gen += 1; }
const fresh = (at) => at !== undefined && Date.now() - at < lease.ms;

function planFor(env, ctx, seconds, country) {
  const gen = lease.gen;
  const asked = getPlan(env, seconds, country).then((p) => {
    if (!p.fallback && gen === lease.gen) lease.plan.set(country, { plan: p, at: Date.now() });
    return p;
  });
  const hit = lease.plan.get(country);
  if (hit && fresh(hit.at)) { ctx?.waitUntil?.(asked); return Promise.resolve(hit.plan); }
  return asked;
}
   // 这条路没拿到可用结果，交给另一条

const json = (status, obj, headers = {}) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json", ...headers } });

// 带超时的 fetch：超时 / 父信号 abort 都会中断；响应体也在超时覆盖范围内读完。
async function timedFetch(url, init, cap, parent, deadline) {
  const ms = Math.min(cap, deadline - Date.now());
  if (ms <= 0) throw new Error("budget exhausted");
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), ms);
  if (parent.aborted) ac.abort();
  const onParent = () => ac.abort();
  parent.addEventListener("abort", onParent, { once: true });
  try {
    const res = await fetch(url, { ...init, signal: ac.signal });
    const text = await res.text();
    let body = null;
    try { body = JSON.parse(text); } catch { /* 非 JSON */ }
    return { status: res.status, headers: res.headers, body };
  } finally {
    clearTimeout(timer);
    parent.removeEventListener("abort", onParent);
  }
}

function toBase64(buf) {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

import { TURBO, V3 } from "./quota-core.js";
import { CODE_RE } from "./passes-core.js";
import { KEY_TTL_S, PROBE_MAX_SESSION_S, REGION_NAMES, STT_RT_MODEL, configuredRegions, collectUsage,
         maxSessionS, mintTempKey, pickRegion, regionKey, wsUrl } from "./soniox-core.js";

// ---- 一人一码（docs/PLAN-passkey.md）----
const PASSES_NAME = "passes-v1";
const DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions";

// ---- 翻译上游：OpenRouter 快速通道（2026-10-17 实测）----
// 同一模型直连 DeepSeek 官方，都柏林首字 p50 0.97 s；经 OpenRouter 落到 InferenceNet / Crusoe / Together
// 只要 0.40–0.46 s。只把「实时翻译 / 草稿」（stream + max_tokens ≤ 400）送过去：课后精修一次几千 token，
// 快这几百毫秒没意义，还多一个可能挂的中间商。
// 中国用户离 DeepSeek 近（HKG），绕美国反而慢，所以 FAST_LLM_SKIP 默认 CN,HK,MO。
// 快速上游不配合（抛错 / 响应头超时 / 非 2xx）就静默用**原 body** 走 DeepSeek；已经开始往客户端流的不再退。
// key 错 / 额度用完（401/402/403）在本 isolate 冷却 10 分钟，免得每句都白试一次。
const OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions";
const FAST_LLM_MODEL_DEFAULT = "deepseek/deepseek-v4.1-flash";
const FAST_LLM_ORDER_DEFAULT = "inference-net/fp8,crusoe/fp8,together";
const FAST_LLM_SKIP_DEFAULT = "CN,HK,MO";
const FAST_LLM_MAX_TOKENS = 400;             // 实时翻译 / 草稿；精修 4000 不绕道
const FAST_LLM_HEADER_MS = 1500;             // 只等响应头（OpenRouter 等供应商首字节才回头）；实测 max 0.78–2.8 s
const FAST_LLM_COOLDOWN_MS = 10 * 60_000;
const FAST_LLM_COOLDOWN_STATUS = new Set([401, 402, 403]);
let fastLlmDownUntil = 0;                    // 本 isolate 内存：跟 lease 一样，回收就丢
// 测试用：清掉冷却。
export function _resetFastLlm() { fastLlmDownUntil = 0; }
// 只放行客户端真用的模型（`main.py --cloud-model` / `keyentry` 的默认值），别让码变成任意模型的代付。
const LLM_MODELS = new Set(["deepseek-flash", "deepseek-chat"]);
const LLM_MAX_TOKENS = 16384;  // 笔记开思考（low）：实测讲解块 正文≈3964 + 推理 4483 = 7943，客户端预算 11000；留到 16384 余量（DeepSeek 上限 384K，按实用量计费，封顶不加钱）（2026-10-18）
const LLM_MAX_BYTES = 256 * 1024;
// Jev（TypeSafe System One）：通行码代付「重点句」「课务」两个打分功能。
// ⚠️ 模型钉死 —— `live_summary.DEADLINE_MIN_P` 的阈值是按这一版标定的（官方：调过阈值就钉版本）。
const JEV_URL = "https://api.typesafe.ai/v1/systemone";
const JEV_MODEL = "jev-1.13.0";
const JEV_MAX_Q = 20;            // 客户端 `keypoints.CHUNK`；多了是滥用，不是正常调用
const JEV_MAX_BYTES = 64 * 1024; // 一批 20 句的 state 实测几 KB
const DENY = {   // 码不能用 -> HTTP 状态；客户端按「云端不可用」走现有降级
  unknown: 403, revoked: 403, expired: 403, devices: 403, quota: 429,
};

async function passes(env, path, payload) {
  const stub = env.PASSES.get(env.PASSES.idFromName(PASSES_NAME));
  return stub.fetch(`https://passes${path}`, { method: "POST", body: JSON.stringify(payload) });
}

// 码能不能用 -> null（能）或拒绝的 Response。DO 不可用 -> 503（不放行：码模式下钱是 Owen 付的）。
async function checkPass(env, code, install, kind) {
  let r;
  try { r = await (await passes(env, "/check", { code, install, kind })).json(); } catch { r = null; }
  if (!r) return json(503, { error: "passes_unavailable" });
  if (r.ok) return null;
  console.log(JSON.stringify({ pass: r.name ?? "?", denied: r.reason, kind }));
  return json(DENY[r.reason] ?? 403, { error: r.reason === "quota" ? "quota_exceeded" : r.reason });
}

// 查码闸：没放行过的码先查后调（deny 非空就直接回）；租期内放行过的不等，`late` 是还在路上的查码结果，
// 调用方回包前必须 await 它（见文件头「短租」）。租约键带 kind：翻译 / 转写 / 打分的额度是分开判的。
async function passGate(env, code, install, kind) {
  const key = `${code}|${install}|${kind}`;
  const checkP = checkPass(env, code, install, kind).then((deny) => {
    if (deny) lease.pass.delete(key); else lease.pass.set(key, Date.now());
    return deny;
  });
  if (fresh(lease.pass.get(key))) return { deny: null, late: checkP };
  return { deny: await checkP, late: null };
}
// 晚到的拒绝：上游已经花了，但结果不给（流也掐掉）。用量由各自的 `ok` 闸挡住不记。
async function settleGate(gate, res) {
  if (!gate.late) return res;
  const deny = await gate.late;
  if (!deny) return res;
  try { await res.body?.cancel(); } catch { /* 已读完 / 已关 */ }
  return deny;
}

function reportUsage(env, ctx, payload) {
  const p = passes(env, "/usage", payload).catch(() => {});
  ctx?.waitUntil?.(p);
}

// 客户端版本（3.9.4.1 起发）。⚠️ 这个头**由客户端控制**：这里先砍长度，
// 形状合法性由 passes-core.usage() 自己判 —— 存储不变量归那个模块管。
const clientVer = (request) => (request.headers.get("x-classlive-version") || "").slice(0, 16);

// ---- 逐请求事件（Analytics Engine，绑定名 AE；docs/PLAN-passkey.md §4「只记数字」）----
// 每个非管理请求写一条，**写在请求结束时**（流式翻译等 usage 到了再写）。只放：
//   index  = 码的名字段（`cl-<名字>-…` 的 <名字>；公开令牌 "public"；没认证 "-"）—— **不写完整码**
//   blobs  = [路径, 码名, 模型, 上游尝试（"groq:200,qwen3-asr-flash:timeout"）, 客户端版本, 国家两字母, 节点三字母]
//   doubles= [状态码, 毫秒, 音频秒, 输入 token, 输出 token, 缓存命中 token, Jev 题数,
//             转写词数, 首字毫秒, 整段完成毫秒, 请求的 max_tokens]（后四列 2026-10-15 加：速度面板用；首字 / 完成只对翻译有）
// ⚠️ 不写文本、音频、IP、完整码。列的顺序是 SQL 的 blob1… / double1…，改了要连 scripts/cl_admin_ui.py 一起改。
const AE_PATHS = new Set(["/v1/transcribe", "/v1/chat/completions", "/v1/jev", "/v1/soniox/key"]);
function newEvent(request, path, t0 = Date.now()) {
  return {
    path: AE_PATHS.has(path) ? path : "other", name: "-", model: "", up: "", ver: clientVer(request),
    country: String(request.cf?.country || "").slice(0, 2), colo: String(request.cf?.colo || "").slice(0, 4),
    status: 0, ms: 0, audio_s: 0, tokens_in: 0, tokens_out: 0, cache_hit: 0, jev_q: 0, pending: null,
    words: 0, first_ms: 0, done_ms: 0, max_tok: 0, t0,
  };
}
function track(env, ev) {
  try {
    env.AE?.writeDataPoint({
      indexes: [ev.name],
      blobs: [ev.path, ev.name, ev.model, ev.up.slice(0, 200), ev.ver, ev.country, ev.colo],
      doubles: [ev.status, ev.ms, ev.audio_s, ev.tokens_in, ev.tokens_out, ev.cache_hit, ev.jev_q,
                ev.words, ev.first_ms, ev.done_ms, ev.max_tok],
    });
  } catch { /* 记账失败不影响请求 */ }
}

// 从 SSE 流里捞最后那块的 usage（`stream_options.include_usage` 时 DeepSeek 在末尾单独发一块）。
// onFirst：第一块带正文的 delta 到达时调一次（翻译首字）。
async function usageFromStream(stream, onFirst = () => {}) {
  const reader = stream.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "", usage = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (!line.startsWith("data:")) continue;
      try {
        const o = JSON.parse(line.slice(5));
        if (o.usage) usage = o.usage;
        if (onFirst && o.choices?.[0]?.delta?.content) { onFirst(); onFirst = null; }
      } catch { /* [DONE] 等 */ }
    }
  }
  return usage;
}

const usageFields = (u) => ({
  tokens_in: u?.prompt_tokens ?? 0, tokens_out: u?.completion_tokens ?? 0,
  // DeepSeek 用 prompt_cache_hit_tokens；OpenRouter 用 prompt_tokens_details.cached_tokens。
  cache_hit: u?.prompt_cache_hit_tokens ?? u?.prompt_tokens_details?.cached_tokens ?? 0,
});

// 快速通道只服务「实时翻译 / 草稿」：非流式 / 精修 / 中国时区都不绕美国。
function useFastLlm(env, body, country) {
  if (!env.OPENROUTER_KEY) return false;
  if (body.stream !== true || body.max_tokens > FAST_LLM_MAX_TOKENS) return false;
  if (Date.now() < fastLlmDownUntil) return false;
  const skip = String(env.FAST_LLM_SKIP ?? FAST_LLM_SKIP_DEFAULT)
    .split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
  return !skip.includes(String(country || "").toUpperCase());
}

// 在副本上改：退回 DeepSeek 时还要用原 body（thinking / model 都是 DeepSeek 的原样）。
function fastLlmBody(env, body) {
  const out = { ...body };
  out.model = env.FAST_LLM_MODEL || FAST_LLM_MODEL_DEFAULT;   // LLM_MODELS 只有 deepseek-flash / chat，都映到这一个
  delete out.thinking;
  out.reasoning = { enabled: false };
  out.provider = {
    order: String(env.FAST_LLM_ORDER || FAST_LLM_ORDER_DEFAULT)
      .split(",").map((s) => s.trim()).filter(Boolean),
    allow_fallbacks: true,
  };
  return out;
}

// 只等响应头：FAST_LLM_HEADER_MS 内没到就 abort。头部一到就清计时器，后面的流不受影响。
async function fetchFastLlm(env, body) {
  const ms = Number(env.FAST_LLM_HEADER_MS) || FAST_LLM_HEADER_MS;
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), ms);
  try {
    return await fetch(OPENROUTER_URL, {
      method: "POST",
      headers: { authorization: `Bearer ${env.OPENROUTER_KEY}`, "content-type": "application/json" },
      body: JSON.stringify(fastLlmBody(env, body)),
      signal: ac.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

async function chat(request, env, ctx, code, ev, ok = Promise.resolve(true)) {
  if (!env.DEEPSEEK_KEY) return json(503, { error: "llm_not_configured" });
  if (Number(request.headers.get("content-length")) > LLM_MAX_BYTES) return json(413, { error: "too_large" });
  const raw = await request.text();
  if (raw.length > LLM_MAX_BYTES) return json(413, { error: "too_large" });
  let body;
  try { body = JSON.parse(raw); } catch { return json(400, { error: "bad_json" }); }
  if (!body || !LLM_MODELS.has(body.model)) return json(400, { error: "model_not_allowed" });
  ev.model = body.model;
  body.max_tokens = Math.min(Number(body.max_tokens) || LLM_MAX_TOKENS, LLM_MAX_TOKENS);
  ev.max_tok = body.max_tokens;   // 面板据此分「实时翻译」（几十）和「课后笔记 / 精修」（上千）
  if (body.stream) body.stream_options = { ...(body.stream_options || {}), include_usage: true };
  let up = null, fast = "";
  if (useFastLlm(env, body, ev.country)) {
    try {
      const r = await fetchFastLlm(env, body);
      fast = `openrouter:${r.status}`;
      if (r.ok && r.body) up = r;
      else {
        if (FAST_LLM_COOLDOWN_STATUS.has(r.status)) fastLlmDownUntil = Date.now() + FAST_LLM_COOLDOWN_MS;
        try { await r.body?.cancel(); } catch { /* 错误响应体不要了 */ }
      }
    } catch (e) {
      fast = `openrouter:${e?.name === "AbortError" ? "timeout" : "err"}`;
    }
  }
  if (!up) {
    up = await fetch(DEEPSEEK_URL, {
      method: "POST",
      headers: { authorization: `Bearer ${env.DEEPSEEK_KEY}`, "content-type": "application/json" },
      body: JSON.stringify(body),   // 原 body：退回必须与不配 OPENROUTER_KEY 时逐字一致
    });
    ev.up = fast ? `${fast}>deepseek:${up.status}` : `deepseek:${up.status}`;
  } else {
    ev.up = fast;   // openrouter:<status>
  }
  const headers = { "content-type": up.headers.get("content-type") || "application/json" };
  if (!up.ok || !up.body) {
    return new Response(up.body, { status: up.status, headers });   // 上游错误原样回（不记用量）
  }
  if (body.stream) {
    const [toClient, toMeter] = up.body.tee();
    const p = usageFromStream(toMeter, () => { ev.first_ms = Date.now() - ev.t0; })
      .then(async (u) => {
        Object.assign(ev, usageFields(u));
        ev.done_ms = Date.now() - ev.t0;
        if (await ok) return passes(env, "/usage", { code, ver: clientVer(request), ...usageFields(u) });
      })
      .catch(() => {});
    ctx?.waitUntil?.(p);
    ev.pending = p;   // 事件等 usage 到了再写（见 fetch 末尾）
    return new Response(toClient, { status: up.status, headers });
  }
  const text = await up.text();
  ev.first_ms = ev.done_ms = Date.now() - ev.t0;   // 非流式：整段一起到
  let u = null;
  try { u = JSON.parse(text).usage; } catch { /* 非 JSON 照样回 */ }
  Object.assign(ev, usageFields(u));
  ctx?.waitUntil?.(ok.then((y) => { if (y) reportUsage(env, ctx, { code, ver: clientVer(request), ...usageFields(u) }); }));
  return new Response(text, { status: up.status, headers });
}

async function jev(request, env, ctx, code, ev, ok = Promise.resolve(true)) {
  if (!env.JEV_KEY) return json(503, { error: "jev_not_configured" });
  if (Number(request.headers.get("content-length")) > JEV_MAX_BYTES) return json(413, { error: "too_large" });
  const raw = await request.text();
  if (raw.length > JEV_MAX_BYTES) return json(413, { error: "too_large" });
  let body;
  try { body = JSON.parse(raw); } catch { return json(400, { error: "bad_json" }); }
  const q = body && typeof body.questions === "object" && body.questions ? Object.keys(body.questions).length : 0;
  if (!body || typeof body.state !== "string" || q < 1 || q > JEV_MAX_Q) return json(400, { error: "bad_request" });
  const up = await fetch(JEV_URL, {
    method: "POST",
    headers: { authorization: `Bearer ${env.JEV_KEY}`, "content-type": "application/json" },
    body: JSON.stringify({ model: JEV_MODEL, state: body.state, questions: body.questions }),
  });
  const text = await up.text();
  ev.model = JEV_MODEL;
  ev.up = `jev:${up.status}`;
  ev.jev_q = q;
  if (up.ok) ctx?.waitUntil?.(ok.then((y) => { if (y) reportUsage(env, ctx, { code, jev_q: q, ver: clientVer(request) }); }));   // 上游错误不记用量（同 chat）
  return new Response(text, { status: up.status, headers: { "content-type": up.headers.get("content-type") || "application/json" } });
}

// ---- 实时转写（Soniox WebSocket）：只发一把临时 key，客户端拿了自己连，**Worker 不碰音频** ----
// 认证与转写同路：只认通行码（公开令牌在 route 里就被 401 掉了）；查码走 passGate（kind="rt"，
// 额度按已回收的 rt_s 判）；晚到的拒绝照 settleGate 的语义 —— key 已经发出来了但**不给**，上游最多白花一把。
// 上游不配合（非 2xx / 超时 / 回包没 key）一律 502，客户端按「云端不可用」降级。出处见 soniox-core.js 文件头。
async function sonioxKey(env, code, install, ev, request) {
  const gate = await passGate(env, code, install, "rt");
  if (gate.deny) return gate.deny;
  if (!env.SONIOX_KEY) return json(503, { error: "soniox_not_configured" });   // fail-closed：没配就谁都不给
  // 可选参数（客户端探测 / 联调用；老客户端不发 body，逐字退回改前行为）：
  //   region  只在**那个区配了 key** 时才生效（没配 / 拼错 -> 忽略回默认 —— 与 pickRegion 同一条 fail-safe）；
  //   purpose=probe -> 15 s 会话上限的探测 key；事件里带 `:probe`，便于把探测与真上课的花费分开看。
  const body = await request.json().catch(() => ({}));
  const b = body && typeof body === "object" ? body : {};
  const probe = b.purpose === "probe";
  const want = typeof b.region === "string" ? b.region.trim().toLowerCase() : "";
  const explicit = REGION_NAMES.includes(want) && regionKey(env, want) ? want : "";
  const region = explicit || pickRegion(ev.country, env, request.cf?.continent);
  const max_session_s = probe ? PROBE_MAX_SESSION_S : maxSessionS(env);
  ev.model = STT_RT_MODEL;
  const mint = await mintTempKey({
    region, name: code.split("-")[1], maxSessionS: max_session_s, apiKey: regionKey(env, region),
  });
  ev.up = `soniox-${region}:${mint.status || "timeout"}${probe ? ":probe" : ""}`;   // ⚠️ 只记状态码，**key 本身绝不进日志 / 事件**
  // 上游挂了要留一行（不然 502 风暴在日志里看不见）；仍然只记状态码。
  if (!mint.api_key) console.log(JSON.stringify({ soniox: "mint_failed", region, probe, status: mint.status || "timeout" }));
  const res = mint.api_key
    ? json(200, { api_key: mint.api_key, ws_url: wsUrl(region), region,
                  expires_in_seconds: KEY_TTL_S, max_session_s })
    : json(502, { error: "soniox_upstream" });
  return settleGate(gate, res);
}

// ---- 用量回收（Cron，每小时）----
// Worker 看不到音频流（客户端直连 Soniox），用量的**唯一**记录是 Soniox 的用量日志：每条带
// client_reference_id = 码名。这里按名字把音频秒补进 Passes 的 rt_s（键与 usage() 同一格）。
// ⚠️ 去重与游标都在 Passes 里（passes-core 的 /admin/rt/*）：一个 DO 调用里完成「去重 + 找码 + 落账 + 推游标」，
//    所以同一条日志不会记两次、也不会记一半；窗口起点由 DO 按 OVERLAP 回退，重叠段靠 uuid 去重。
// ⚠️ 每个配置了 key 的区各拉各的（`configuredRegions`，加区不改这里）；没配 key 的区整个跳过。
async function reclaimUsage(env, region, nowMs) {
  const apiKey = regionKey(env, region);
  if (!apiKey) return;
  const cur = await passes(env, "/admin/rt/cursor", { region }).then((r) => r.json()).catch(() => null);
  if (!cur || cur.error) {
    console.log(JSON.stringify({ soniox: "cursor_unavailable", region, err: cur?.error ?? null }));
    return;
  }
  if (!(nowMs > cur.from_ms)) return;   // 窗口是空的（同一分钟跑了两次 / 时钟倒着走）：什么都不做
  const { records, bad, error, more } = await collectUsage({ region, apiKey, sinceMs: cur.from_ms, untilMs: nowMs });
  if (error !== null) {   // 上游不配合：游标**不动**，下轮整个窗口重来（真重复了由 uuid 去重兜住）
    console.log(JSON.stringify({ soniox: "usage_failed", region, status: error }));
    return;
  }
  // 拉满页数还有下一页：游标只推到**已拉到的最后一条**，别把没拉到的那些甩在窗口外。
  const until = more && records.length ? records.reduce((m, r) => Math.max(m, r.end_ms), 0) : nowMs;
  const out = await passes(env, "/admin/rt/import", { region, until_ms: until, records })
    .then((r) => r.json()).catch(() => null);
  console.log(JSON.stringify({ soniox: "usage", region, got: records.length, bad, more, out }));
}

// 管理接口：只认 ADMIN_TOKEN（fail-closed：没配就谁都不认）。路径 /v1/admin/x -> DO 的 /admin/x。
async function admin(request, env, path) {
  const expected = env.ADMIN_TOKEN ? `Bearer ${env.ADMIN_TOKEN}` : null;
  if (!expected || (request.headers.get("authorization") || "") !== expected) {
    return new Response("unauthorized", { status: 401 });
  }
  const body = await request.json().catch(() => ({}));
  // 额度快照住在另一个 DO 上（Passes 那边没有这些计数），所以这一条单独分流。
  const res = path === "/v1/admin/quota"
    ? await quota(env, "/admin/state", body)
    : await passes(env, path.slice(3), body);
  return new Response(res.body, { status: res.status, headers: { "content-type": "application/json" } });
}

// ---- Quota DO ----
async function quota(env, path, payload) {
  const stub = env.QUOTA.get(env.QUOTA.idFromName(DO_NAME));
  return stub.fetch(`https://quota${path}`, { method: "POST", body: JSON.stringify(payload) });
}
async function getPlan(env, seconds, country = "") {
  try {
    const res = await quota(env, "/plan", { chain: CHAIN, seconds, country });
    if (res.ok) return await res.json();
  } catch { /* 落到下面 */ }
  // DO 不可用：开放而不是挂掉（Groq 429 / 千问 403 自己会兜住），只是少了主动降级。
  // ⚠️ 此时 R17 的千问每日 3000 段上限、R18 的 Groq 音频秒上限也一并失效 —— 接受（DO 免费档超限才会走到这）。
  console.log(JSON.stringify({ quota: "unavailable" }));
  return { groq: V3, qwen: CHAIN, fallback: true };   // fallback：不进租约
}
async function takeTurbo(env, seconds) {
  try {
    const res = await quota(env, "/plan", { chain: CHAIN, seconds, model: TURBO });
    if (res.ok) return (await res.json()).groq === true;
  } catch { /* 落到下面 */ }
  return true;   // DO 不可用：同 getPlan，开放（Groq 自己 429 会兜住）
}

// ---- Groq ----
// Groq 每次回包带的剩余额度（2026-10-14 实测头名）；缺任何一个就当没有。
function groqHdr(h) {
  const g = (k) => h?.get?.(`x-ratelimit-${k}`);
  const v = [g("remaining-requests"), g("remaining-audio-seconds"), g("limit-requests"), g("limit-audio-seconds")];
  if (v.some((x) => x == null || x === "")) return null;
  const [req, aud, req_lim, aud_lim] = v.map(Number);
  return [req, aud, req_lim, aud_lim].every(Number.isFinite) ? { req, aud, req_lim, aud_lim } : null;
}

// run = {env, audio, sig, deadline, tried, report}：一次请求内各供应商共用的上下文。
async function tryGroq(run, model = V3) {
  const { env, audio, sig, deadline, tried, report, country } = run;
  const tag = model === V3 ? "groq" : "turbo";
  const form = new FormData();
  form.append("file", new Blob([audio], { type: "audio/ogg" }), "audio.ogg");
  form.append("model", model);
  form.append("language", "en");
  form.append("response_format", "verbose_json");
  form.append("temperature", "0");
  // ⚠️ 故意不传 prompt：喂术语 WER 10.1 -> 15.2% 并编造整句（RESEARCH §13.3）。
  let r;
  try {
    r = await timedFetch(GROQ_URL, { method: "POST", headers: { authorization: `Bearer ${env.GROQ_KEY}` }, body: form }, T.groq, sig, deadline);
  } catch {
    tried.push(`${tag}:` + (sig.aborted ? "cancelled" : "timeout"));
    throw new Skip();
  }
  tried.push(`${tag}:${r.status}`);
  // Groq 按**访客国家**拦（Cloudflare 把原访客国家带进子请求）：2026-10-15 CN 访客 739/739 groq:403；
  // 探针 Worker 放在 HKG / SIN 跑、访客 GB 时都是 200 —— 跟机房无关，换 placement 也绕不开。
  // 报给 DO：这个国家 6 h 内不再试 Groq、不预扣 Groq 额度（quota-core 的 groq_blocked）。
  if (r.status === 403) {
    run.blocked = true;
    console.log(JSON.stringify({ groq403: country, msg: String(r.body?.error?.message ?? "").slice(0, 120) }));
    if (country) report({ groq_blocked: country });
  }
  const hdr = groqHdr(r.headers);   // 200 与 429 都带；有就报给 DO（见 quota-core.js 的 hdr）
  if (hdr && r.status !== 429) report(model === V3 ? { groq_hdr: hdr } : { groq_hdr: hdr, model });
  if (r.status === 429) {
    const cd = parseInt(r.headers.get("retry-after"), 10) || 60;
    report({ groq_cooldown_s: cd, ...(hdr ? { groq_hdr: hdr } : {}), ...(model === V3 ? {} : { model }) });
    throw new Skip();
  }
  const b = r.body;
  if (r.status !== 200 || !b || typeof b.text !== "string") throw new Skip();
  const segs = Array.isArray(b.segments) ? b.segments : [];
  if (segs.length && segs.every((s) => s.no_speech_prob > NO_SPEECH)) {
    return { text: "", model };   // 静音：当空返回，不升级千问
  }
  if (segs.some((s) => s.compression_ratio > DEGENERATE_CR)) throw new Skip();   // 退化 -> 千问
  return { text: b.text.trim(), model };
}

// ---- 千问 ----（请求体照 docs/experiments/cloud_asr_20261010/qcn.py 的 call()，已实测）
function qwenBody(model, b64) {
  const uri = `data:audio/ogg;base64,${b64}`;
  if (model.startsWith("qwen-audio")) {
    return {
      model,
      input: { messages: [{ role: "user", content: [{ type: "input_audio", input_audio: { data: uri } }] }] },
      parameters: { format: "opus", sample_rate: "16000", language_hints: ["en"] },
    };
  }
  return {
    model,
    input: { messages: [{ role: "user", content: [{ audio: uri }] }] },
    parameters: { asr_options: { language: "en", enable_itn: false } },
  };
}
const qwenText = (b) => b?.output?.choices?.[0]?.message?.content?.[0]?.text ?? b?.output?.text;

async function tryQwen({ env, audio, sig, deadline, tried, report }, models) {
  const b64 = toBase64(audio);
  report({ qwen_used: 1 });   // R17：每段只计一次（不是每个模型一次）
  for (const model of models) {
    if (sig.aborted) throw new Skip();
    let r;
    try {
      r = await timedFetch(QWEN_URL, {
        method: "POST",
        headers: { authorization: `Bearer ${env.QWEN_KEY}`, "content-type": "application/json", "x-dashscope-sse": "disable" },
        body: JSON.stringify(qwenBody(model, b64)),
      }, T.qwen, sig, deadline);
    } catch {
      tried.push(`${model}:${sig.aborted ? "cancelled" : "timeout"}`);
      continue;
    }
    tried.push(`${model}:${r.status}`);
    const code = r.body?.code;
    if (r.status === 200) {
      const text = qwenText(r.body);
      if (typeof text === "string") return { text: text.trim(), model };
    } else if (r.status === 400 && code === NO_WORDS) {
      return { text: "", model };
    } else if (r.status === 403) {
      if (code === FREE_TIER_ONLY) { report({ exhausted: model }); continue; }   // 额度用完：永久跳过
      report({ qwen_cooldown_s: 3600 });   // 欠费 / 权限：整组歇一小时，别再烧
      throw new Skip();
    }
    // 429 / 5xx / 其他：试下一个模型，不报 DO
  }
  throw new Skip();
}

async function transcribe(env, ctx, audio, seconds, t0, planP = null, country = "") {
  const deadline = t0 + T.total;
  const tried = [];
  const budget = new AbortController();
  const budgetTimer = setTimeout(() => budget.abort(), Math.max(0, deadline - Date.now()));
  let hedgeTimer;
  const report = (r) => {
    if (r.groq_cooldown_s || r.qwen_cooldown_s || r.exhausted || r.groq_blocked) { lease.plan.clear(); lease.gen += 1; }
    const p = quota(env, "/report", r).catch(() => {});
    ctx?.waitUntil?.(p);
  };
  try {
    const run = { env, audio, sig: budget.signal, deadline, tried, report, country };
    const plan = await (planP ?? getPlan(env, seconds, country));
    const models = env.QWEN_KEY ? plan.qwen : [];
    const primary = env.GROQ_KEY ? plan.groq : null;   // V3 | TURBO | null
    const groqP = primary ? tryGroq(run, primary) : Promise.reject(new Skip());
    // 千问何时起跑：Groq 失败的当下，或 Groq 发出 1.2 s 还没回（对冲），取早者。
    const qwenP = models.length
      ? (async () => {
          const groqFailed = groqP.then(() => new Promise(() => {}), () => {});
          const hedge = new Promise((res) => { hedgeTimer = setTimeout(res, T.hedge); });
          await Promise.race([groqFailed, hedge]);
          return tryQwen(run, models);
        })()
      : Promise.reject(new Skip());
    let won = null;
    try { won = await Promise.any([groqP, qwenP]); } catch { /* 全部失败 */ }
    // 最后一道：主力是 v3、它与千问都没拿到（额度满 / 冷却 / 都挂了）且预算还够一次 Groq -> turbo。
    // 主力已经是 turbo / null（turbo 桶已问过）就不再问。
    // Groq 刚因国家回 403 -> turbo 同样会 403，不问。
    if (!won && primary === V3 && !run.blocked && deadline - Date.now() > 500 && await takeTurbo(env, seconds)) {
      try { won = await tryGroq(run, TURBO); } catch { /* 也没拿到 */ }
    }
    const ms = Date.now() - t0;
    if (!won) {
      console.log(JSON.stringify({ result: "all_failed", ms, tried }));
      return { res: json(503, { error: "all_failed", tried: [...tried] }), model: null, tried };
    }
    const provider = won.model.startsWith("whisper") ? "groq" : "qwen";
    console.log(JSON.stringify({ provider, model: won.model, ms, tried }));
    const words = won.text ? won.text.split(/\s+/).filter(Boolean).length : 0;   // 只记个数，不记文本
    return { res: json(200, { text: won.text, provider, model: won.model, ms, tried: [...tried] }), model: won.model, tried, words };
  } finally {
    clearTimeout(budgetTimer);
    clearTimeout(hedgeTimer);
    budget.abort();   // 落败的一路（Groq 或千问）在这里被掐掉
  }
}

export default {
  async fetch(request, env, ctx) {
    const t0 = Date.now();
    const path = new URL(request.url).pathname;
    if (request.method === "POST" && path.startsWith("/v1/admin/")) return admin(request, env, path);   // 管理调用不进事件
    const ev = newEvent(request, path, t0);
    const res = await route(request, env, ctx, ev, path, t0);
    ev.status = res.status;
    ev.ms = Date.now() - t0;   // 流式翻译：到响应头为止（首包），不是整段流
    if (ev.pending) ctx?.waitUntil?.(ev.pending.then(() => track(env, ev)));
    else track(env, ev);
    return res;
  },

  // Cron（wrangler.toml 的 [triggers]）：每个配置了 key 的区各拉各的用量日志，回收进 Passes。
  // 用 event.scheduledTime 当窗口末尾 —— 定时的语义就是「拉到这一刻」，顺带给测试一个确定的时钟。
  // 一个区挂了不影响另一个；这里自己吞掉异常并**记一行**（cron 没有重试，抛出去也只是多一条 invocation 报错）。
  async scheduled(event, env) {
    const nowMs = Number(event?.scheduledTime) || Date.now();
    await Promise.all(configuredRegions(env).map((region) =>
      reclaimUsage(env, region, nowMs).catch((e) =>
        console.log(JSON.stringify({ soniox: "reclaim_threw", region, msg: String(e).slice(0, 120) })))));
  },
};

async function route(request, env, ctx, ev, path, t0) {
  if (request.method !== "POST"
      || !(path === "/v1/transcribe" || path === "/v1/chat/completions" || path === "/v1/jev"
           || path === "/v1/soniox/key")) {
    return new Response("not found", { status: 404 });
  }
  // 认证：公开令牌（只能转写）或通行码 `cl-<名字>-<随机>`（转写 + 翻译，记到人名下）。
  // ⚠️ fail-closed：secret 没配时 "Bearer undefined" 不能放行（同上传 Worker）。
  const auth = request.headers.get("authorization") || "";
  const code = auth.startsWith("Bearer cl-") ? auth.slice(7) : null;
  if (code !== null && (!CODE_RE.test(code) || !env.PASSES)) {
    return json(403, { error: "unknown" });
  }
  const expected = env.CLIENT_TOKEN ? `Bearer ${env.CLIENT_TOKEN}` : null;
  if (code === null && (!expected || auth !== expected)) {
    return new Response("unauthorized", { status: 401 });
  }
  ev.name = code === null ? "public" : code.split("-")[1];   // 码已过 CODE_RE：中间段就是名字
  if ((path === "/v1/chat/completions" || path === "/v1/jev" || path === "/v1/soniox/key") && code === null) {
    return new Response("unauthorized", { status: 401 });   // 公开令牌不代付翻译 / 打分 / 实时转写的 key
  }
  const install = request.headers.get("x-classlive-install") || "";
  if (!INSTALL_OK.test(install)) return new Response("bad install id", { status: 400 });

  // 翻译：只有通行码能走到这；不过下面那两道限流 —— 码是个人的、能吊销、能限额，
  // 而一节课的翻译请求（定稿 + 草稿译文）每分钟会超过转写那套 30 / 60 的限。
  // 翻译首字：中国用户落 HKG，查码那一趟 DO 约 210 ms，原来全压在首字前面（10-15：CN p50 395 ms vs IE 343 ms）。
  if (path === "/v1/chat/completions" || path === "/v1/jev") {
    const kind = path === "/v1/jev" ? "jev" : "llm";
    const gate = await passGate(env, code, install, kind);
    if (gate.deny) return gate.deny;
    const ok = gate.late ? gate.late.then((d) => !d) : Promise.resolve(true);
    const res = kind === "llm" ? await chat(request, env, ctx, code, ev, ok) : await jev(request, env, ctx, code, ev, ok);
    return settleGate(gate, res);
  }

  // 令牌公开、install_id 客户端随便造 -> 按 install 限流挡不住，所以再按 IP 限一道（R17，同上传 Worker）。
  const ip = request.headers.get("cf-connecting-ip") || "0.0.0.0";
  if (!(await env.PER_IP.limit({ key: ip })).success) {
    return new Response("rate limited", { status: 429, headers: { "retry-after": "60" } });
  }
  const { success } = await env.PER_INSTALL.limit({ key: install });
  if (!success) return new Response("rate limited", { status: 429, headers: { "retry-after": "60" } });

  // 实时转写：只发一把临时 key（客户端拿了自己连 Soniox），所以这里没有音频 body 要读
  // （`sonioxKey` 自己读那个可选的 `{region, purpose}` —— 别的路一个字节都不读）。
  if (path === "/v1/soniox/key") return sonioxKey(env, code, install, ev, request);

  // R20：先看 Content-Length，超了不读；但它可以谎报，所以读完仍按**实际字节数**再判一次。
  if (Number(request.headers.get("content-length")) > MAX_BYTES) return new Response("too large", { status: 413 });
  const audio = await request.arrayBuffer();
  if (audio.byteLength === 0) return new Response("empty body", { status: 400 });
  if (audio.byteLength > MAX_BYTES) return new Response("too large", { status: 413 });

  const raw = parseFloat(request.headers.get("x-audio-seconds"));
  const seconds = Number.isFinite(raw) ? Math.min(Math.max(raw, 0), SECONDS_MAX) : SECONDS_DEFAULT;
  // 查码（Passes DO）与选路（Quota DO）一起问：两个 DO 都在欧洲，从香港节点串着问是两个来回。
  // 2026-10-15 实测中国用户（HKG）70 条 trace：查码 p50 210 ms、选路 213 ms，占 Worker 耗时的 38%。
  // 代价：被拒的码也会在 Quota 里占一次 Groq 额度（getPlan 会预扣）—— 拒绝很少见，接受。
  // 两者在租期内还能连这一个来回也不等（见文件头「短租」）。
  const planP = planFor(env, ctx, seconds, ev.country);   // 永不 reject（DO 不可用时自己退回开放）
  let gate = { deny: null, late: null };
  if (code !== null) {
    gate = await passGate(env, code, install, "asr");
    if (gate.deny) return gate.deny;
  }
  ev.audio_s = seconds;
  const { res, model, tried, words = 0 } = await transcribe(env, ctx, audio, seconds, t0, planP, ev.country);
  ev.words = words;
  ev.model = model ?? "";
  ev.up = tried.join(",");
  if (gate.late && await gate.late) return settleGate(gate, res);   // 上游已花掉，但结果不给、不记用量
  if (code !== null && res.status === 200) {
    reportUsage(env, ctx, { code, asr_s: seconds, model, ver: clientVer(request) });
  }
  return res;
}
