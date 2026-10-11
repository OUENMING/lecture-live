// ClassLive 定稿 ASR 代理：POST /v1/transcribe（audio/ogg Opus）-> {text, provider, model, ms, tried}
//
// 链：Groq（whisper-large-v3，2.5 s）优先；1.2 s 还没回就对冲启动千问；千问免费额度
// 三个模型轮换（每个 3.5 s）；整条链总预算 5 s，客户端 6 s 截止前一定有回包。
// 日志只记供应商 / 模型 / 状态码 / 毫秒 —— **不记文本、不记音频**。
// 设计与出处：docs/PLAN-r2-cloud-final-asr.md §W、docs/RESEARCH-cloud-asr.md。

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
const QWEN_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation";

const NO_SPEECH = 0.6;        // 全段高于它 -> 静音，Groq 会幻觉 "Thank you."
const DEGENERATE_CR = 2.4;    // 压缩比高于它 -> 重复退化
const FREE_TIER_ONLY = "AllocationQuota.FreeTierOnly";   // 唯一算「额度用完」的 code（官方 error-code 页）
const NO_WORDS = "ASR_RESPONSE_HAVE_NO_WORDS";

class Skip extends Error {}   // 这条路没拿到可用结果，交给另一条

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

// ---- Quota DO ----
async function quota(env, path, payload) {
  const stub = env.QUOTA.get(env.QUOTA.idFromName(DO_NAME));
  return stub.fetch(`https://quota${path}`, { method: "POST", body: JSON.stringify(payload) });
}
async function getPlan(env, seconds) {
  try {
    const res = await quota(env, "/plan", { chain: CHAIN, seconds });
    if (res.ok) return await res.json();
  } catch { /* 落到下面 */ }
  // DO 不可用：开放而不是挂掉（Groq 429 / 千问 403 自己会兜住），只是少了主动降级。
  // ⚠️ 此时 R17 的千问每日 3000 段上限、R18 的 Groq 音频秒上限也一并失效 —— 接受（DO 免费档超限才会走到这）。
  console.log(JSON.stringify({ quota: "unavailable" }));
  return { groq: true, qwen: CHAIN };
}

// ---- Groq ----
// run = {env, audio, sig, deadline, tried, report}：一次请求内各供应商共用的上下文。
async function tryGroq({ env, audio, sig, deadline, tried, report }) {
  const form = new FormData();
  form.append("file", new Blob([audio], { type: "audio/ogg" }), "audio.ogg");
  form.append("model", "whisper-large-v3");
  form.append("language", "en");
  form.append("response_format", "verbose_json");
  form.append("temperature", "0");
  // ⚠️ 故意不传 prompt：喂术语 WER 10.1 -> 15.2% 并编造整句（RESEARCH §13.3）。
  let r;
  try {
    r = await timedFetch(GROQ_URL, { method: "POST", headers: { authorization: `Bearer ${env.GROQ_KEY}` }, body: form }, T.groq, sig, deadline);
  } catch {
    tried.push("groq:" + (sig.aborted ? "cancelled" : "timeout"));
    throw new Skip();
  }
  tried.push(`groq:${r.status}`);
  if (r.status === 429) {
    report({ groq_cooldown_s: parseInt(r.headers.get("retry-after"), 10) || 60 });
    throw new Skip();
  }
  const b = r.body;
  if (r.status !== 200 || !b || typeof b.text !== "string") throw new Skip();
  const segs = Array.isArray(b.segments) ? b.segments : [];
  if (segs.length && segs.every((s) => s.no_speech_prob > NO_SPEECH)) {
    return { text: "", model: "whisper-large-v3" };   // 静音：当空返回，不升级千问
  }
  if (segs.some((s) => s.compression_ratio > DEGENERATE_CR)) throw new Skip();   // 退化 -> 千问
  return { text: b.text.trim(), model: "whisper-large-v3" };
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
        headers: { authorization: `Bearer ${env.QWEN_CN_KEY}`, "content-type": "application/json", "x-dashscope-sse": "disable" },
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

async function transcribe(env, ctx, audio, seconds, t0) {
  const deadline = t0 + T.total;
  const tried = [];
  const budget = new AbortController();
  const budgetTimer = setTimeout(() => budget.abort(), Math.max(0, deadline - Date.now()));
  let hedgeTimer;
  const report = (r) => {
    const p = quota(env, "/report", r).catch(() => {});
    ctx?.waitUntil?.(p);
  };
  try {
    const run = { env, audio, sig: budget.signal, deadline, tried, report };
    const plan = await getPlan(env, seconds);
    const models = env.QWEN_CN_KEY ? plan.qwen : [];
    const groqP = plan.groq && env.GROQ_KEY
      ? tryGroq(run)
      : Promise.reject(new Skip());
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
    const ms = Date.now() - t0;
    if (!won) {
      console.log(JSON.stringify({ result: "all_failed", ms, tried }));
      return json(503, { error: "all_failed", tried: [...tried] });
    }
    const provider = won.model === "whisper-large-v3" ? "groq" : "qwen";
    console.log(JSON.stringify({ provider, model: won.model, ms, tried }));
    return json(200, { text: won.text, provider, model: won.model, ms, tried: [...tried] });
  } finally {
    clearTimeout(budgetTimer);
    clearTimeout(hedgeTimer);
    budget.abort();   // 落败的一路（Groq 或千问）在这里被掐掉
  }
}

export default {
  async fetch(request, env, ctx) {
    const t0 = Date.now();
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/v1/transcribe") {
      return new Response("not found", { status: 404 });
    }
    // ⚠️ fail-closed：secret 没配时 "Bearer undefined" 不能放行（同上传 Worker）。
    const expected = env.CLIENT_TOKEN ? `Bearer ${env.CLIENT_TOKEN}` : null;
    if (!expected || (request.headers.get("authorization") || "") !== expected) {
      return new Response("unauthorized", { status: 401 });
    }
    const install = request.headers.get("x-classlive-install") || "";
    if (!INSTALL_OK.test(install)) return new Response("bad install id", { status: 400 });

    // 令牌公开、install_id 客户端随便造 -> 按 install 限流挡不住，所以再按 IP 限一道（R17，同上传 Worker）。
    const ip = request.headers.get("cf-connecting-ip") || "0.0.0.0";
    if (!(await env.PER_IP.limit({ key: ip })).success) {
      return new Response("rate limited", { status: 429, headers: { "retry-after": "60" } });
    }
    const { success } = await env.PER_INSTALL.limit({ key: install });
    if (!success) return new Response("rate limited", { status: 429, headers: { "retry-after": "60" } });

    // R20：先看 Content-Length，超了不读；但它可以谎报，所以读完仍按**实际字节数**再判一次。
    if (Number(request.headers.get("content-length")) > MAX_BYTES) return new Response("too large", { status: 413 });
    const audio = await request.arrayBuffer();
    if (audio.byteLength === 0) return new Response("empty body", { status: 400 });
    if (audio.byteLength > MAX_BYTES) return new Response("too large", { status: 413 });

    const raw = parseFloat(request.headers.get("x-audio-seconds"));
    const seconds = Number.isFinite(raw) ? Math.min(Math.max(raw, 0), SECONDS_MAX) : SECONDS_DEFAULT;
    return transcribe(env, ctx, audio, seconds, t0);
  },
};
