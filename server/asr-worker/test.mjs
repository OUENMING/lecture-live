// ASR Worker 判据 —— 直接 import src 真源码；fetch 换成假上游，DO 用真 QuotaCore + 内存 storage。
//
//   node --test server/asr-worker/test.mjs
//
// ⚠️ 不验证 Cloudflare 平台真实行为（路由 / 绑定 / DO 的分布式语义 / 真实 Groq、千问响应），
//    那些只能部署后手验。这里钉的是 Worker 自己的逻辑。
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "./src/handler.js";
import { QuotaCore } from "./src/quota-core.js";

const TOKEN = "tok-public";
const INSTALL = "0123456789abcdef0123456789abcdef";
const CHAIN3 = ["qwen3-asr-flash", "qwen-audio-3.1-asr-flash", "qwen-audio-3.0-asr-flash"];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 返回在 ms 后兑现的响应；signal abort 时像真 fetch 一样 reject。
function later(ms, make, signal) {
  return new Promise((resolve, reject) => {
    const onAbort = () => { clearTimeout(t); reject(Object.assign(new Error("aborted"), { name: "AbortError" })); };
    const t = setTimeout(() => { signal?.removeEventListener("abort", onAbort); resolve(make()); }, ms);
    if (signal?.aborted) return onAbort();
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
const J = (status, obj, headers = {}) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json", ...headers } });
const groqOk = (text = " hello groq ", segments = [{ no_speech_prob: 0.01, compression_ratio: 1.1 }]) =>
  J(200, { text, segments });
const qwenOk = (text = "hello qwen") => J(200, { output: { choices: [{ message: { content: [{ text }] } }] } });

function harness({ groq = () => groqOk(), qwen = () => qwenOk(), groqMs = 0, qwenMs = 0, planMs = 0 } = {}) {
  const calls = { groq: [], qwen: [], qwenUrls: [] };
  const reports = [];
  const plans = [];
  const limitKeys = { ip: [], install: [] };
  const store = new Map();
  const core = new QuotaCore({
    get: async (k) => store.get(k),
    put: async (k, v) => { store.set(k, structuredClone(v)); },
  });
  const origFetch = globalThis.fetch;
  globalThis.fetch = (url, init = {}) => {
    const u = String(url);
    if (u.startsWith("https://api.groq.com/")) {
      calls.groq.push({ form: init.body, signal: init.signal, headers: init.headers });
      return later(groqMs, () => groq(calls.groq.length, init.body.get("model")), init.signal);
    }
    if (u.startsWith("https://dashscope-intl.aliyuncs.com/")) {
      const body = JSON.parse(init.body);
      calls.qwenUrls.push(u);
      calls.qwen.push({ model: body.model, body, signal: init.signal, headers: init.headers });
      return later(qwenMs, () => qwen(body.model, calls.qwen.length), init.signal);
    }
    throw new Error("unexpected upstream " + u);
  };
  const env = {
    CLIENT_TOKEN: TOKEN, GROQ_KEY: "gk", QWEN_KEY: "qk",
    PER_INSTALL: { limit: async ({ key }) => { limitKeys.install.push(key); return { success: true }; } },
    PER_IP: { limit: async ({ key }) => { limitKeys.ip.push(key); return { success: true }; } },
    QUOTA: {
      idFromName: (n) => n,
      get: () => ({
        fetch: async (u, init) => {
          const req = new Request(u, init);
          const path = new URL(req.url).pathname;
          if (path === "/report") reports.push(await req.clone().json());
          if (path === "/plan") { plans.push(await req.clone().json()); if (planMs) await sleep(planMs); }
          return core.handle(req);
        },
      }),
    },
  };
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p) };
  const call = async (req, e = env) => {
    const res = await worker.fetch(req, e, ctx);
    await Promise.all(pending.splice(0));
    return res;
  };
  return { env, calls, reports, plans, limitKeys, call, core, store, restore: () => { globalThis.fetch = origFetch; } };
}

function req({ method = "POST", path = "/v1/transcribe", token = `Bearer ${TOKEN}`, install = INSTALL, bytes = new Uint8Array(1000), seconds = null, ip = "203.0.113.9" } = {}) {
  const headers = { "content-type": "audio/ogg", "cf-connecting-ip": ip };
  if (seconds !== null) headers["x-audio-seconds"] = seconds;
  if (token !== null) headers.authorization = token;
  if (install !== null) headers["x-classlive-install"] = install;
  return new Request(`https://asr.bldcam.page${path}`, { method, headers, body: method === "GET" ? undefined : bytes });
}

async function withHarness(opts, fn) {
  const h = harness(opts);
  try { return await fn(h); } finally { h.restore(); }
}

// ---------- 1. 入口校验 ----------
test("1 入口：401 / 400 / 413 / 404 / 429，且都没碰上游", () => withHarness({}, async (h) => {
  assert.equal((await h.call(req({ token: "Bearer nope" }))).status, 401);
  assert.equal((await h.call(req({ token: null }))).status, 401);
  // fail-closed：secret 没配时 "Bearer undefined" 不许放行
  assert.equal((await h.call(req({ token: "Bearer undefined" }), { ...h.env, CLIENT_TOKEN: undefined })).status, 401);
  assert.equal((await h.call(req({ install: "XYZ" }))).status, 400);
  assert.equal((await h.call(req({ install: null }))).status, 400);
  assert.equal((await h.call(req({ install: INSTALL.toUpperCase() }))).status, 400);
  assert.equal((await h.call(req({ bytes: new Uint8Array(512 * 1024 + 1) }))).status, 413);
  assert.equal((await h.call(req({ method: "GET" }))).status, 404);
  assert.equal((await h.call(req({ path: "/v1/other" }))).status, 404);
  const limited = { ...h.env, PER_INSTALL: { limit: async () => ({ success: false }) } };
  const r429 = await h.call(req(), limited);
  assert.equal(r429.status, 429);
  assert.equal(r429.headers.get("retry-after"), "60");
  assert.equal(h.calls.groq.length + h.calls.qwen.length, 0);
}));

// ---------- 2. Groq 正常 ----------
test("2 Groq 200 -> provider=groq，千问零调用，且不传 prompt", () => withHarness({}, async (h) => {
  const res = await h.call(req());
  assert.equal(res.status, 200);
  const j = await res.json();
  assert.equal(j.text, "hello groq");
  assert.equal(j.provider, "groq");
  assert.equal(h.calls.qwen.length, 0);
  const form = h.calls.groq[0].form;
  assert.equal(form.get("model"), "whisper-large-v3");
  assert.equal(form.get("language"), "en");
  assert.equal(form.get("response_format"), "verbose_json");
  assert.equal(form.get("temperature"), "0");
  assert.equal(form.has("prompt"), false);
}));

// ---------- 3. 静音幻觉 ----------
test("3 Groq 全段 no_speech_prob 0.9 -> 空串、千问没调", () => withHarness({
  groq: () => groqOk("Thank you.", [{ no_speech_prob: 0.9, compression_ratio: 1 }, { no_speech_prob: 0.95, compression_ratio: 1 }]),
}, async (h) => {
  const res = await h.call(req());
  assert.equal(res.status, 200);
  const j = await res.json();
  assert.equal(j.text, "");
  assert.equal(j.provider, "groq");
  assert.equal(h.calls.qwen.length, 0);
}));

// ---------- 4. 退化 ----------
test("4 Groq compression_ratio 3.0 -> 走千问", () => withHarness({
  groq: () => groqOk("la la la la", [{ no_speech_prob: 0.01, compression_ratio: 3.0 }]),
}, async (h) => {
  const j = await (await h.call(req())).json();
  assert.equal(j.provider, "qwen");
  assert.equal(j.text, "hello qwen");
  assert.equal(h.calls.qwen.length, 1);
}));

// ---------- 5. Groq 429 ----------
test("5 Groq 429 retry-after 120 -> report groq_cooldown_s=120、走千问", () => withHarness({
  groq: () => J(429, { error: { message: "rate" } }, { "retry-after": "120" }),
}, async (h) => {
  const j = await (await h.call(req())).json();
  assert.equal(j.provider, "qwen");
  assert.deepEqual(h.reports.filter((r) => "groq_cooldown_s" in r), [{ groq_cooldown_s: 120 }]);
}));

// ---------- 6. 对冲 ----------
test("6a Groq 1.8 s 才回、千问 0.1 s -> 取千问，Groq 那路被 abort", () => withHarness({ groqMs: 1800, qwenMs: 100 }, async (h) => {
  const t0 = Date.now();
  const j = await (await h.call(req())).json();
  assert.equal(j.provider, "qwen");
  assert.ok(Date.now() - t0 < 1700, "应在 Groq 回来之前就返回");
  assert.equal(h.calls.groq[0].signal.aborted, true);
}));
test("6b Groq 0.5 s 回 -> 千问没被调（对冲不误触发）", () => withHarness({ groqMs: 500 }, async (h) => {
  const j = await (await h.call(req())).json();
  assert.equal(j.provider, "groq");
  await sleep(1400);   // 越过 1.2 s 对冲点，确认定时器已被清掉
  assert.equal(h.calls.qwen.length, 0);
}));
test("6c 千问请求体：两种模型各按实测格式", () => withHarness({ groq: () => J(500, {}), qwen: (m) => (m === CHAIN3[2] ? qwenOk() : J(500, {})) }, async (h) => {
  await h.call(req());
  assert.deepEqual(h.calls.qwen.map((c) => c.model), CHAIN3);
  const b0 = h.calls.qwen[0].body, b1 = h.calls.qwen[1].body;
  assert.match(b0.input.messages[0].content[0].audio, /^data:audio\/ogg;base64,/);
  assert.deepEqual(b0.parameters, { asr_options: { language: "en", enable_itn: false } });
  assert.match(b1.input.messages[0].content[0].input_audio.data, /^data:audio\/ogg;base64,/);
  assert.equal(b1.input.messages[0].content[0].type, "input_audio");
  assert.deepEqual(b1.parameters, { format: "opus", sample_rate: "16000", language_hints: ["en"] });
  assert.equal(h.calls.qwen[0].headers["x-dashscope-sse"], "disable");
  assert.equal(h.calls.qwenUrls[0], "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation");
  assert.equal(h.calls.qwen[0].headers.authorization, "Bearer qk");
}));

// ---------- 7. 额度用完永久跳过 ----------
test("7 千问第一个 403 FreeTierOnly -> report exhausted、第二个成功；下次不再试第一个", () => withHarness({
  groq: () => J(500, {}),
  qwen: (m) => (m === CHAIN3[0] ? J(403, { code: "AllocationQuota.FreeTierOnly", message: "x" }) : qwenOk("second")),
}, async (h) => {
  const j = await (await h.call(req())).json();
  assert.equal(j.text, "second");
  assert.equal(j.model, CHAIN3[1]);
  assert.deepEqual(h.reports.filter((r) => r.exhausted), [{ exhausted: CHAIN3[0] }]);
  h.calls.qwen.length = 0;
  await h.call(req());
  assert.deepEqual(h.calls.qwen.map((c) => c.model), [CHAIN3[1]]);
}));

// ---------- 8. 无词 ----------
test("8 千问 400 ASR_RESPONSE_HAVE_NO_WORDS -> 200 空文本", () => withHarness({
  groq: () => J(500, {}),
  qwen: () => J(400, { code: "ASR_RESPONSE_HAVE_NO_WORDS", message: "" }),
}, async (h) => {
  const res = await h.call(req());
  assert.equal(res.status, 200);
  assert.equal((await res.json()).text, "");
  assert.equal(h.calls.qwen.length, 1);
}));

// ---------- 9. 欠费 ----------
test("9 千问 403 Arrearage -> report qwen_cooldown_s=3600、503、整组一小时不试", () => withHarness({
  groq: () => J(500, {}),
  qwen: () => J(403, { code: "Arrearage", message: "x" }),
}, async (h) => {
  const res = await h.call(req());
  assert.equal(res.status, 503);
  assert.equal(h.calls.qwen.length, 1, "403 其他 code 不再试下一个模型");
  assert.deepEqual(h.reports.filter((r) => "qwen_cooldown_s" in r), [{ qwen_cooldown_s: 3600 }]);
  assert.equal(h.reports.some((r) => r.exhausted), false, "欠费不是额度用完，不能永久封模型");
  h.calls.qwen.length = 0;
  await h.call(req());
  assert.equal(h.calls.qwen.length, 0);
}));

// ---------- 10. 全挂 ----------
test("10 全挂 -> 503 带 tried", () => withHarness({ groq: () => J(500, {}), qwen: () => J(502, {}) }, async (h) => {
  const res = await h.call(req());
  assert.equal(res.status, 503);
  const j = await res.json();
  assert.equal(j.error, "all_failed");
  assert.deepEqual(j.tried.sort(), ["groq:500", "turbo:500", ...CHAIN3.map((m) => `${m}:502`)].sort());
}));

// ---------- T. Groq turbo：独立额度桶的最后一道 ----------
const TURBO = "whisper-large-v3-turbo";
const V3 = "whisper-large-v3";
test("T1 v3 与千问都失败 -> turbo 顶上，provider=groq model=turbo", () => withHarness({
  groq: (n, model) => (model === TURBO ? groqOk(" from turbo ") : J(500, {})), qwen: () => J(502, {}),
}, async (h) => {
  const j = await (await h.call(req())).json();
  assert.deepEqual([j.provider, j.model, j.text], ["groq", TURBO, "from turbo"]);
  assert.deepEqual(h.calls.groq.map((c) => c.form.get("model")), ["whisper-large-v3", TURBO]);
}));
test("T2 v3 赢 / 千问赢 -> turbo 一次都不调、不占它的额度", () => withHarness({}, async (h) => {
  await h.call(req());
  assert.deepEqual(h.calls.groq.map((c) => c.form.get("model")), ["whisper-large-v3"]);
  assert.equal(h.plans.filter((p) => p.model === TURBO).length, 0);
}));
test("T3 v3 当日额度满 + 千问全耗尽 -> 直接 turbo（不等对冲）", () => withHarness({
  groq: (n, model) => (model === TURBO ? groqOk(" t ") : groqOk(" v3 ")),
}, async (h) => {
  await h.store.set("state", { day: new Date().toISOString().slice(0, 10), groq_day: 1900,
    qwen_exhausted: Object.fromEntries(CHAIN3.map((m) => [m, "x"])) });
  const t0 = Date.now();
  const j = await (await h.call(req())).json();
  assert.equal(j.model, TURBO);
  assert.ok(Date.now() - t0 < 1000, "不该等 1.2 s 对冲");
  assert.equal(h.calls.qwen.length, 0);
}));
const fullDay = (extra = {}) => ({ day: new Date().toISOString().slice(0, 10), groq_day: 1900, ...extra });
test("T5 v3 当日额度满、千问有额度 -> turbo 当主力，千问零调用、不计千问用量", () => withHarness({
  groq: (n, model) => (model === TURBO ? groqOk(" t ") : groqOk(" v3 ")),
}, async (h) => {
  await h.store.set("state", fullDay());
  const j = await (await h.call(req())).json();
  assert.deepEqual([j.provider, j.model, j.text], ["groq", TURBO, "t"]);
  assert.deepEqual(h.calls.groq.map((c) => c.form.get("model")), [TURBO]);
  assert.equal(h.calls.qwen.length, 0);
  assert.equal(h.reports.filter((r) => r.qwen_used).length, 0);
}));
test("T6 v3 与 turbo 当日都满 -> 直接千问、Groq 零调用；千问也挂 -> 503，不再单独问 turbo 桶", () => withHarness({
  qwen: () => J(502, {}),
}, async (h) => {
  await h.store.set("state", fullDay({ turbo: { n: 1900 } }));
  assert.equal((await h.call(req())).status, 503);
  assert.deepEqual(h.calls.qwen.map((c) => c.model), CHAIN3);
  assert.equal(h.calls.groq.length, 0);
  assert.equal(h.plans.filter((p) => p.model === TURBO).length, 0);
}));
test("T7 turbo 当主力却 500 -> 千问接住；turbo 不被重试第二次", () => withHarness({
  groq: (n, model) => (model === TURBO ? J(500, {}) : groqOk()),
}, async (h) => {
  await h.store.set("state", fullDay());
  const j = await (await h.call(req())).json();
  assert.equal(j.provider, "qwen");
  assert.deepEqual(h.calls.groq.map((c) => c.form.get("model")), [TURBO]);
}));
test("T8 v3 现场 429 -> 这一段走千问；冷却期间下一段主力换 turbo、不再碰千问", () => withHarness({
  groq: (n, model) => (model === TURBO ? groqOk(" t ") : J(429, {}, { "retry-after": "120" })),
}, async (h) => {
  assert.equal((await (await h.call(req())).json()).provider, "qwen");
  h.calls.qwen.length = 0;
  const j = await (await h.call(req())).json();
  assert.equal(j.model, TURBO);
  assert.equal(h.calls.qwen.length, 0);
}));
test("T9 Quota：v3 与 turbo 都没额度 -> groq=null", async () => {
  const clock = { now: DAY };
  const { plan } = quotaAt(clock, { day: "2026-10-11", groq_day: 1900, turbo: { n: 1900 } });
  assert.equal((await plan()).groq, null);
});
test("T4 Quota：turbo 桶与 v3 互不占用；turbo 冷却只冷 turbo", async () => {
  const clock = { now: DAY };
  const { plan, report, store } = quotaAt(clock, { day: "2026-10-11", groq_day: 1900 });
  const q = new QuotaCore({ get: async (k) => store.get(k), put: async (k, v) => { store.set(k, structuredClone(v)); } }, () => clock.now);
  const turbo = async () => (await (await q.handle(new Request("https://quota/plan", { method: "POST",
    body: JSON.stringify({ chain: CHAIN3, seconds: 12, model: TURBO }) }))).json()).groq;
  assert.equal((await plan()).groq, TURBO, "v3 满了");
  assert.equal(await turbo(), true, "turbo 是另一个桶");
  await report({ groq_cooldown_s: 120, model: TURBO });
  assert.equal(await turbo(), false);
  store.set("state", { ...store.get("state"), groq_day: 0 });
  assert.equal((await plan()).groq, V3, "turbo 冷却不该连累 v3");
  clock.now = Date.UTC(2026, 9, 12, 0, 0, 1);
  store.set("state", { ...store.get("state"), turbo: { ...store.get("state").turbo, n: 1900, cooldown_until: 0 } });
  assert.equal(await turbo(), true, "跨日清零 turbo 次数");
});

// ---------- 11. 总预算 ----------
test("11 上游都挂 10 s -> 5.3 s 内 503", async () => {
  await withHarness({ groqMs: 10000, qwenMs: 10000 }, async (h) => {
    const t0 = Date.now();
    const res = await h.call(req());
    const dt = Date.now() - t0;
    assert.equal(res.status, 503);
    assert.ok(dt <= 5300, `耗时 ${dt} ms`);
    assert.ok(dt >= 4800, `太早放弃：${dt} ms`);
  });
});

// ---------- 12. Quota ----------
function quotaAt(clock, seed) {
  const store = new Map(seed ? [["state", seed]] : []);
  const q = new QuotaCore({ get: async (k) => store.get(k), put: async (k, v) => { store.set(k, structuredClone(v)); } }, () => clock.now);
  const plan = async (seconds = 12) => (await q.handle(new Request("https://quota/plan", { method: "POST", body: JSON.stringify({ chain: CHAIN3, seconds }) }))).json();
  const report = (b) => q.handle(new Request("https://quota/report", { method: "POST", body: JSON.stringify(b) }));
  return { plan, report, store };
}
const DAY = Date.UTC(2026, 9, 11, 12, 0, 0);

test("12a Quota：同分钟第 20 次 v3 不批 -> 主力换 turbo，下一分钟恢复 v3", async () => {
  const clock = { now: DAY };
  const { plan } = quotaAt(clock);
  for (let i = 1; i <= 19; i++) assert.equal((await plan()).groq, V3, `第 ${i} 次`);
  assert.equal((await plan()).groq, TURBO);
  clock.now += 60_000;
  assert.equal((await plan()).groq, V3);
});
test("12b Quota：第 1901 次 v3 不批 -> turbo；跨 UTC 日清零", async () => {
  const clock = { now: DAY };
  const day = "2026-10-11";
  const { plan } = quotaAt(clock, { day, groq_day: 1899 });
  assert.equal((await plan()).groq, V3);     // 第 1900 次
  assert.equal((await plan()).groq, TURBO);    // 第 1901 次
  clock.now = Date.UTC(2026, 9, 12, 0, 0, 1);
  assert.equal((await plan()).groq, V3);
});
test("12c Quota：cooldown / exhausted 生效，exhausted 跨日不清", async () => {
  const clock = { now: DAY };
  const { plan, report } = quotaAt(clock);
  await report({ groq_cooldown_s: 120 });
  assert.equal((await plan()).groq, TURBO);
  clock.now += 121_000;
  assert.equal((await plan()).groq, V3);
  await report({ exhausted: CHAIN3[0] });
  clock.now += 86_400_000 * 2;
  assert.deepEqual((await plan()).qwen, CHAIN3.slice(1));
  await report({ qwen_cooldown_s: 3600 });
  assert.deepEqual((await plan()).qwen, []);
  clock.now += 3_601_000;
  assert.deepEqual((await plan()).qwen, CHAIN3.slice(1));
});

// =====================  §R 审查后修订（R17–R20）  =====================

// ---------- R17 按 IP 限流 ----------
test("R17a 按 IP 限流：key=cf-connecting-ip，超了 429 retry-after 60，且没碰上游", () => withHarness({}, async (h) => {
  assert.equal((await h.call(req({ ip: "198.51.100.7" }))).status, 200);
  assert.deepEqual(h.limitKeys.ip, ["198.51.100.7"]);
  assert.deepEqual(h.limitKeys.install, [INSTALL]);
  h.calls.groq.length = 0;
  const limited = { ...h.env, PER_IP: { limit: async () => ({ success: false }) } };
  const res = await h.call(req(), limited);
  assert.equal(res.status, 429);
  assert.equal(res.headers.get("retry-after"), "60");
  assert.equal(h.calls.groq.length, 0);
}));

// ---------- R17 千问每日总量 ----------
test("R17b 千问每日 3000 段：满了当天 qwen=[]，UTC 跨日恢复；每段只计一次", async () => {
  const clock = { now: DAY };
  const { plan, report } = quotaAt(clock);
  for (let i = 0; i < 2999; i++) await report({ qwen_used: 1 });
  assert.deepEqual((await plan()).qwen, CHAIN3);
  await report({ qwen_used: 1 });
  assert.deepEqual((await plan()).qwen, []);
  clock.now = Date.UTC(2026, 9, 12, 0, 0, 1);
  assert.deepEqual((await plan()).qwen, CHAIN3);
});
test("R17c Worker 每段只报一次 qwen_used（整条链试三个模型也只一次）；Groq 赢时不报", async () => {
  await withHarness({ groq: () => J(500, {}), qwen: (m) => (m === CHAIN3[2] ? qwenOk() : J(502, {})) }, async (h) => {
    await h.call(req());
    assert.deepEqual(h.reports.filter((r) => r.qwen_used), [{ qwen_used: 1 }]);
  });
  await withHarness({}, async (h) => {
    await h.call(req());
    await sleep(50);
    assert.equal(h.reports.filter((r) => r.qwen_used).length, 0);
  });
});

// ---------- R18 音频秒 ----------
test("R18a X-Audio-Seconds 解析：缺失/非法 -> 12，夹到 [0,60]", () => withHarness({}, async (h) => {
  for (const s of [null, "abc", "", "Infinity", "NaN"]) await h.call(req({ seconds: s }));
  await h.call(req({ seconds: "99" }));
  await h.call(req({ seconds: "-5" }));
  await h.call(req({ seconds: "3.5" }));
  assert.deepEqual(h.plans.map((p) => p.seconds), [12, 12, 12, 12, 12, 60, 0, 3.5]);
}));
test("R18b Quota：Groq 小时音频秒累计到 95%（6840）就不批；不足 10 s 按 10 s 计；换小时恢复", async () => {
  const clock = { now: DAY };
  const { plan } = quotaAt(clock, { day: "2026-10-11", groq_hour: { hour: Math.floor(DAY / 3600000), s: 6820 } });
  assert.equal((await plan(5)).groq, V3);    // 5 s 按 10 -> 6830
  assert.equal((await plan(5)).groq, V3);    // 6840，刚好到线
  assert.equal((await plan(5)).groq, TURBO);   // 6850 > 6840
  clock.now += 3600_000;
  assert.equal((await plan(5)).groq, V3);
});
test("R18c Quota：Groq 当日音频秒到 95%（27360）就不批；UTC 跨日清零", async () => {
  const clock = { now: DAY };
  const { plan } = quotaAt(clock, { day: "2026-10-11", groq_day_s: 27350 });
  assert.equal((await plan(10)).groq, V3);   // 27360
  assert.equal((await plan(10)).groq, TURBO);
  clock.now = Date.UTC(2026, 9, 12, 0, 0, 1);
  assert.equal((await plan(10)).groq, V3);
});

// ---------- R19 ----------
test("R19a 对冲已发出后 Groq 才回退化结果 -> 不能当赢家，取千问", () => withHarness({
  groqMs: 1500, qwenMs: 600,
  groq: () => groqOk("la la la", [{ no_speech_prob: 0.01, compression_ratio: 3.0 }]),
}, async (h) => {
  const j = await (await h.call(req())).json();
  assert.equal(j.provider, "qwen");
  assert.equal(j.text, "hello qwen");
}));
test("R19b 对冲那一路是整条千问链：第一个模型失败，第二个顶上（Groq 还在途中）", () => withHarness({
  groqMs: 4000, qwenMs: 100,
  qwen: (m) => (m === CHAIN3[0] ? J(502, {}) : qwenOk("second")),
}, async (h) => {
  const t0 = Date.now();
  const j = await (await h.call(req())).json();
  assert.equal(j.model, CHAIN3[1]);
  assert.ok(Date.now() - t0 < 2000);
  assert.equal(h.calls.groq[0].signal.aborted, true);
}));
test("R19c 总预算从进入 handler 算起：问 DO 就花了 3 s，Groq 只剩 2 s，总共仍 <= 5.3 s", () => withHarness({
  planMs: 3000, groqMs: 10000, qwenMs: 10000,
}, async (h) => {
  const t0 = Date.now();
  const res = await h.call(req());
  const dt = Date.now() - t0;
  assert.equal(res.status, 503);
  assert.ok(dt <= 5300, `耗时 ${dt} ms`);
}));
test("R19d 读 body 就花了 3 s（慢上传），总预算仍从进入 handler 算：总共 <= 5.3 s", () => withHarness({
  groqMs: 10000, qwenMs: 10000,
}, async (h) => {
  const fake = {
    method: "POST", url: "https://asr.bldcam.page/v1/transcribe",
    headers: new Headers({ authorization: `Bearer ${TOKEN}`, "x-classlive-install": INSTALL, "content-length": "1000" }),
    arrayBuffer: async () => { await sleep(3000); return new ArrayBuffer(1000); },
  };
  const t0 = Date.now();
  const res = await h.call(fake);
  const dt = Date.now() - t0;
  assert.equal(res.status, 503);
  assert.ok(dt <= 5300, `耗时 ${dt} ms`);
}));

// ---------- R20 先看 Content-Length ----------
test("R20 Content-Length 超 512 KB -> 413，且根本没去读 body", () => withHarness({}, async (h) => {
  let read = false;
  const fake = {
    method: "POST", url: "https://asr.bldcam.page/v1/transcribe",
    headers: new Headers({ authorization: `Bearer ${TOKEN}`, "x-classlive-install": INSTALL, "content-length": String(512 * 1024 + 1) }),
    arrayBuffer: async () => { read = true; return new ArrayBuffer(10); },
  };
  assert.equal((await h.call(fake)).status, 413);
  assert.equal(read, false);
}));

// =====================  §H Groq 报的剩余额度（2026-10-14）  =====================
// 实测：两个桶连续回补（RPD 每 43.2 s 回 1 次，ASH 每 0.5 s 回 1 秒），不是到点清零。
const GH = (req, aud, req_lim = 2000, aud_lim = 7200) => ({
  "x-ratelimit-remaining-requests": String(req), "x-ratelimit-remaining-audio-seconds": String(aud),
  "x-ratelimit-limit-requests": String(req_lim), "x-ratelimit-limit-audio-seconds": String(aud_lim),
});
const groqOkH = (h, text = " hi ") => J(200, { text, segments: [{ no_speech_prob: 0.01, compression_ratio: 1.1 }] }, h);

test("H1 Groq 200 带剩余头 -> DO 记下（v3 记在 groq_hdr），管理面板读得到推算值", () => withHarness({
  groq: () => groqOkH(GH(1500, 6000)),
}, async (h) => {
  await h.call(req({ seconds: "12" }));
  const st = h.store.get("state");
  assert.deepEqual({ ...st.groq_hdr, at: 0 }, { req: 1500, aud: 6000, req_lim: 2000, aud_lim: 7200, at: 0 });
  assert.equal(st.turbo.hdr, null, "v3 的数不许记到 turbo 上");
  const q = await (await h.core.handle(new Request("https://quota/admin/state", { method: "POST", body: "{}" }))).json();
  assert.equal(q.v3.groq.req_lim, 2000);
  assert.ok(q.v3.groq.req_left >= 1500 && q.v3.groq.req_left <= 1501, JSON.stringify(q.v3.groq));
  assert.equal(q.turbo.groq, null);
}));
test("H2 头缺一个 -> 不报（宁可退回老算法也不信半截数）", () => withHarness({
  groq: () => groqOkH({ "x-ratelimit-remaining-requests": "5" }),
}, async (h) => {
  await h.call(req());
  assert.equal(h.reports.filter((r) => r.groq_hdr).length, 0);
  assert.equal(h.store.get("state").groq_hdr ?? null, null);
}));
test("H3 有 Groq 的数就信它：本地 UTC 日计数已到 1900，Groq 说还剩 1500 -> 照批 v3", async () => {
  const clock = { now: DAY };
  const { plan } = quotaAt(clock, { day: "2026-10-11", groq_day: 1900,
    groq_hdr: { req: 1500, aud: 6000, req_lim: 2000, aud_lim: 7200, at: DAY } });
  assert.equal((await plan()).groq, V3);
});
test("H4 Groq 剩余低于 5% 就不批；按 43.2 s / 次回补，回够了再批", async () => {
  const clock = { now: DAY };
  const { plan, store } = quotaAt(clock, { day: "2026-10-11",
    groq_hdr: { req: 99, aud: 7000, req_lim: 2000, aud_lim: 7200, at: DAY } });
  assert.equal((await plan()).groq, TURBO, "99 - 1 < 100");
  clock.now += 2 * 43_200;                    // 回 2 次 -> 101
  assert.equal((await plan()).groq, V3, "101 - 1 = 100，刚好到线");
  assert.ok(Math.abs(store.get("state").groq_hdr.req - 100) < 1e-6, "批准后本地先扣 1");
  assert.equal((await plan()).groq, TURBO, "本地扣过，下一段又不够了");
});
test("H5 音频秒按真实秒扣（不按 10 s 起步），按 2 秒/秒回补", async () => {
  const clock = { now: DAY };
  const { plan, store } = quotaAt(clock, { day: "2026-10-11",
    groq_hdr: { req: 1500, aud: 400, req_lim: 2000, aud_lim: 7200, at: DAY } });
  assert.equal((await plan(50)).groq, TURBO, "400 - 50 < 360");
  clock.now += 10_000;                       // +20 秒
  assert.equal((await plan(50)).groq, V3, "420 - 50 = 370");
  assert.ok(Math.abs(store.get("state").groq_hdr.aud - 370) < 1e-6);
  assert.equal((await plan(5)).groq, V3, "5 s 的段只扣 5 秒：370 - 5 = 365");
  const c = quotaAt(clock, { day: "2026-10-11",
    groq_hdr: { req: 1500, aud: 366, req_lim: 2000, aud_lim: 7200, at: clock.now } });
  assert.equal((await c.plan(5)).groq, V3, "366 - 5 = 361 ≥ 360（按 10 s 计费秒就会 356 被拒）");
});
test("H6 Groq 不报的两条照旧本地管：RPM 19 与当日音频秒", async () => {
  const clock = { now: DAY };
  const hdr = { req: 1900, aud: 7000, req_lim: 2000, aud_lim: 7200, at: DAY };
  const { plan } = quotaAt(clock, { day: "2026-10-11", groq_hdr: hdr });
  for (let i = 1; i <= 19; i++) assert.equal((await plan(1)).groq, V3, `第 ${i} 次`);
  assert.equal((await plan(1)).groq, TURBO, "同分钟第 20 次");
  const b = quotaAt(clock, { day: "2026-10-11", groq_day_s: 27355, groq_hdr: hdr });
  assert.equal((await b.plan(10)).groq, TURBO, "当日音频秒 27355 + 10 > 27360");
});
test("H7 429 也带头 -> 冷却照设、数也记下；turbo 的头记到 turbo 桶", () => withHarness({
  groq: (n, model) => (model === TURBO ? groqOkH(GH(1990, 7100), " t ") : J(429, {}, { "retry-after": "30", ...GH(0, 5000) })),
}, async (h) => {
  await h.call(req());                      // v3 429 -> 千问
  const st = h.store.get("state");
  assert.ok(st.groq_cooldown_until > Date.now());
  assert.equal(st.groq_hdr.req, 0);
  await h.store.set("state", { ...st, qwen_exhausted: Object.fromEntries(CHAIN3.map((m) => [m, "x"])) });
  const j = await (await h.call(req())).json();
  assert.equal(j.model, TURBO);
  assert.equal(h.store.get("state").turbo.hdr.req, 1990);
  assert.equal(h.store.get("state").groq_hdr.req, 0, "turbo 的数不许盖到 v3 上");
}));
test("H8 推算封顶上限：很久没报 -> 回满但不超过 limit", () => {
  const e = QuotaCore.estimate({ req: 10, aud: 10, req_lim: 2000, aud_lim: 7200, at: 0 }, 10 * 86_400_000);
  assert.deepEqual(e, { req: 2000, aud: 7200 });
  assert.equal(QuotaCore.estimate(null, 1), null);
  assert.equal(QuotaCore.parseHdr({ req: 1, aud: 1, req_lim: 0, aud_lim: 7200 }), null, "上限 0 不收");
  assert.equal(QuotaCore.parseHdr({ req: -1, aud: 1, req_lim: 2000, aud_lim: 7200 }), null);
});
