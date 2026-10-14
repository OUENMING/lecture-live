// 一人一码判据 —— 直接 import src 真源码；DeepSeek / Groq 换成假上游，两个 DO 用真 core + 内存 storage。
//
//   node --test server/asr-worker/test-passes.mjs
//
// ⚠️ 不验证 Cloudflare 平台真实行为（DO 分布式语义、tee 在边缘节点上的背压、真 DeepSeek 流格式）。
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "./src/handler.js";
import { PassCore, CODE_RE } from "./src/passes-core.js";
import { QuotaCore } from "./src/quota-core.js";

const INSTALL = "0123456789abcdef0123456789abcdef";
const INSTALL2 = "fedcba9876543210fedcba9876543210";
const INSTALL3 = "00000000000000000000000000000003";
const DAY = 86400000;
// harness 的假时钟起点。`stats` 要查「当天」时用 TODAY，两者同源，不会各自漂。
const T0 = Date.UTC(2026, 9, 12, 9);
const TODAY = "2026-10-12";

function memStorage() {
  const m = new Map();
  return {
    m,
    get: async (k) => structuredClone(m.get(k)),
    put: async (k, v) => { m.set(k, structuredClone(v)); },
    // 真 DO 的 storage.delete 收单个键或键数组。替身**必须一起补** ——
    // 少了它，用 delete 的路由会在这里炸成 TypeError，看起来像产品 bug（2026-10-14 撞过一次）。
    delete: async (keys) => { for (const k of [].concat(keys)) m.delete(k); },
    list: async ({ prefix }) => new Map([...m].filter(([k]) => k.startsWith(prefix)).sort()),
  };
}

const J = (status, obj) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const doStub = (core) => ({
  idFromName: (n) => n,
  get: () => ({ fetch: async (u, init) => core.handle(new Request(u, init)) }),
});

function harness({ deepseek } = {}) {
  let now = T0;
  const clock = () => now;
  const pass = new PassCore(memStorage(), clock);
  const quota = new QuotaCore(memStorage(), clock);
  const calls = { deepseek: [], groq: 0, jev: [] };
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.startsWith("https://api.deepseek.com/")) {
      calls.deepseek.push({ headers: init.headers, body: JSON.parse(init.body) });
      return deepseek ? deepseek(JSON.parse(init.body)) : J(200, {
        choices: [{ message: { content: "{}" } }],
        usage: { prompt_tokens: 100, completion_tokens: 20, prompt_cache_hit_tokens: 64 },
      });
    }
    if (u.startsWith("https://api.typesafe.ai/")) {
      calls.jev.push({ headers: init.headers, body: JSON.parse(init.body) });
      return J(200, { answers: { S1: { noul: 0.7 } } });
    }
    if (u.startsWith("https://api.groq.com/")) {
      calls.groq += 1;
      return J(200, { text: "hi", segments: [{ no_speech_prob: 0.01, compression_ratio: 1 }] });
    }
    throw new Error("unexpected upstream " + u);
  };
  const env = {
    CLIENT_TOKEN: "tok-public", GROQ_KEY: "gk", DEEPSEEK_KEY: "sk-owen", ADMIN_TOKEN: "adm", JEV_KEY: "apikey_owen",
    PER_INSTALL: { limit: async () => ({ success: true }) },
    PER_IP: { limit: async () => ({ success: true }) },
    QUOTA: doStub(quota), PASSES: doStub(pass),
  };
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p) };
  const call = async (req) => {
    const res = await worker.fetch(req, env, ctx);
    return res;
  };
  const settle = () => Promise.all(pending.splice(0));
  const adminReq = (path, body) => new Request(`https://asr.bldcam.page/v1/admin/${path}`, {
    method: "POST", headers: { authorization: "Bearer adm" }, body: JSON.stringify(body ?? {}),
  });
  const chatReq = (code, body, install = INSTALL, ver = null) => new Request("https://asr.bldcam.page/v1/chat/completions", {
    method: "POST",
    headers: { authorization: `Bearer ${code}`, "x-classlive-install": install, "content-type": "application/json",
               ...(ver ? { "x-classlive-version": ver } : {}) },
    body: JSON.stringify(body),
  });
  const asrReq = (auth, install = INSTALL, seconds = "12") => new Request("https://asr.bldcam.page/v1/transcribe", {
    method: "POST",
    headers: { authorization: auth, "x-classlive-install": install, "x-audio-seconds": seconds },
    body: new Uint8Array(100),
  });
  const jevReq = (auth, body, install = INSTALL, ver = null) => new Request("https://asr.bldcam.page/v1/jev", {
    method: "POST",
    headers: { authorization: auth, "x-classlive-install": install, "content-type": "application/json",
               ...(ver ? { "x-classlive-version": ver } : {}) },
    body: JSON.stringify(body),
  });
  const restore = () => { globalThis.fetch = origFetch; };
  return { env, call, settle, adminReq, chatReq, asrReq, jevReq, calls, pass, restore,
           tick: (ms) => { now += ms; } };
}

const body = (extra = {}) => ({ model: "deepseek-flash", messages: [{ role: "user", content: "x" }], max_tokens: 50, ...extra });

test("admin：没配 / 配错 ADMIN_TOKEN 一律 401（fail-closed）", async () => {
  const h = harness();
  try {
    const bad = new Request("https://asr.bldcam.page/v1/admin/list", {
      method: "POST", headers: { authorization: "Bearer nope" }, body: "{}" });
    assert.equal((await h.call(bad)).status, 401);
    h.env.ADMIN_TOKEN = undefined;
    assert.equal((await h.call(h.adminReq("list"))).status, 401);
  } finally { h.restore(); }
});

test("发码：格式 cl-<名字>-<16 位 hex>，默认 120 天 / 2 台 / 不限额", async () => {
  const h = harness();
  try {
    const r = await (await h.call(h.adminReq("add", { name: "LiMing" }))).json();
    assert.match(r.code, CODE_RE);
    assert.ok(r.code.startsWith("cl-liming-"));
    assert.equal(r.expires - r.created, 120 * DAY);
    assert.equal(r.max_devices, 2);
    assert.equal(r.daily_asr_s, null);
    assert.equal(r.daily_tokens, null);
    const bad = await (await h.call(h.adminReq("add", { name: "has space" }))).json();
    assert.ok(bad.error);
  } finally { h.restore(); }
});

test("翻译：通行码转发给 DeepSeek，换成 Owen 的 key，用量记到人名下", async () => {
  const h = harness();
  try {
    const { code } = await (await h.call(h.adminReq("add", { name: "ying" }))).json();
    const res = await h.call(h.chatReq(code, body()));
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.deepseek.length, 1);
    assert.equal(h.calls.deepseek[0].headers.authorization, "Bearer sk-owen");
    const st = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    assert.equal(st.days[0].rows.length, 1);
    assert.deepEqual([st.days[0].rows[0].name, st.days[0].rows[0].llm_reqs, st.days[0].rows[0].tokens_in, st.days[0].rows[0].tokens_out, st.days[0].rows[0].cache_hit],
                     ["ying", 1, 100, 20, 64]);
  } finally { h.restore(); }
});

test("翻译：公开令牌不代付（401），未知码 403，模型白名单，max_tokens 封顶", async () => {
  const h = harness();
  try {
    assert.equal((await h.call(h.chatReq("tok-public", body()))).status, 401);
    assert.equal((await h.call(h.chatReq("cl-nobody-0123456789abcdef", body()))).status, 403);
    const { code } = await (await h.call(h.adminReq("add", { name: "a" }))).json();
    assert.equal((await h.call(h.chatReq(code, body({ model: "deepseek-reasoner" })))).status, 400);
    await (await h.call(h.chatReq(code, body({ max_tokens: 999999 })))).text();
    assert.equal(h.calls.deepseek.at(-1).body.max_tokens, 4096);
    assert.equal(h.calls.deepseek.length, 1, "被拒的请求不许打到上游");
  } finally { h.restore(); }
});

test("流式：客户端拿到原样的流；末尾 usage 被捞出来记账（强制 include_usage）", async () => {
  const sse = [
    'data: {"choices":[{"delta":{"content":"你"}}]}',
    'data: {"choices":[{"delta":{"content":"好"}}]}',
    'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2,"prompt_cache_hit_tokens":5}}',
    "data: [DONE]", "",
  ].join("\n\n");
  const h = harness({ deepseek: () => new Response(sse, { status: 200, headers: { "content-type": "text/event-stream" } }) });
  try {
    const { code } = await (await h.call(h.adminReq("add", { name: "s" }))).json();
    const res = await h.call(h.chatReq(code, body({ stream: true })));
    assert.equal(await res.text(), sse);
    await h.settle();
    assert.deepEqual(h.calls.deepseek[0].body.stream_options, { include_usage: true });
    const st = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    assert.deepEqual([st.days[0].rows[0].tokens_in, st.days[0].rows[0].tokens_out, st.days[0].rows[0].cache_hit], [7, 2, 5]);
  } finally { h.restore(); }
});

test("吊销 / 过期 / 续期 / 设备上限", async () => {
  const h = harness();
  try {
    const { code } = await (await h.call(h.adminReq("add", { name: "d", max_devices: 2 }))).json();
    assert.equal((await h.call(h.chatReq(code, body(), INSTALL))).status, 200);
    assert.equal((await h.call(h.chatReq(code, body(), INSTALL2))).status, 200);
    const third = await h.call(h.chatReq(code, body(), INSTALL3));
    assert.equal(third.status, 403);
    assert.equal((await third.json()).error, "devices");
    assert.equal((await h.call(h.chatReq(code, body(), INSTALL))).status, 200, "老设备照用");

    await h.call(h.adminReq("revoke", { name: "d" }));
    const rv = await h.call(h.chatReq(code, body()));
    assert.equal(rv.status, 403);
    assert.equal((await rv.json()).error, "revoked");
    await h.call(h.adminReq("unrevoke", { code }));
    assert.equal((await h.call(h.chatReq(code, body()))).status, 200);

    h.tick(121 * DAY);
    const ex = await h.call(h.chatReq(code, body()));
    assert.equal((await ex.json()).error, "expired");
    await h.call(h.adminReq("renew", { code, days: 120 }));
    assert.equal((await h.call(h.chatReq(code, body()))).status, 200);
  } finally { h.restore(); }
});

test("额度：设了 daily_tokens / daily_asr_s 才拦（429 quota_exceeded），跨天清零", async () => {
  const h = harness();
  try {
    const { code } = await (await h.call(h.adminReq("add", { name: "q" }))).json();
    await h.call(h.adminReq("limit", { code, daily_tokens: 100, daily_asr_s: 20 }));
    await (await h.call(h.chatReq(code, body()))).text();   // 用掉 120 token
    await h.settle();
    const over = await h.call(h.chatReq(code, body()));
    assert.equal(over.status, 429);
    assert.equal((await over.json()).error, "quota_exceeded");

    assert.equal((await h.call(h.asrReq(`Bearer ${code}`, INSTALL, "12"))).status, 200);
    await h.settle();
    assert.equal((await h.call(h.asrReq(`Bearer ${code}`, INSTALL, "12"))).status, 200);   // 12 < 20，还能过
    await h.settle();
    assert.equal((await h.call(h.asrReq(`Bearer ${code}`, INSTALL, "12"))).status, 429);   // 24 ≥ 20

    h.tick(DAY);
    assert.equal((await h.call(h.chatReq(code, body()))).status, 200, "跨天清零");
    await h.call(h.adminReq("limit", { code, daily_tokens: null }));
    const l = await (await h.call(h.adminReq("list"))).json();
    assert.equal(l.codes[0].daily_tokens, null, "null = 不限");
    const bad = await (await h.call(h.adminReq("limit", { code, daily_tokens: -1 }))).json();
    assert.ok(bad.error);
  } finally { h.restore(); }
});

test("转写：公开令牌照旧能用、不记到任何人；通行码转写记秒数", async () => {
  const h = harness();
  try {
    assert.equal((await h.call(h.asrReq("Bearer tok-public"))).status, 200);
    await h.settle();
    let st = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    assert.equal(st.days[0].rows.length, 0);
    const { code } = await (await h.call(h.adminReq("add", { name: "t" }))).json();
    assert.equal((await h.call(h.asrReq(`Bearer ${code}`, INSTALL, "8.5"))).status, 200);
    await h.settle();
    st = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    assert.deepEqual([st.days[0].rows[0].name, st.days[0].rows[0].asr_s, st.days[0].rows[0].asr_reqs], ["t", 8.5, 1]);
    assert.equal((await h.call(h.asrReq("Bearer cl-x-zz"))).status, 403, "格式不对的码不许落到公开令牌那条");
  } finally { h.restore(); }
});

test("通行码模式下 DO 不可用 -> 503（不放行：钱是 Owen 付的）", async () => {
  const h = harness();
  try {
    const { code } = await (await h.call(h.adminReq("add", { name: "u" }))).json();
    h.env.PASSES = { idFromName: (n) => n, get: () => ({ fetch: async () => { throw new Error("down"); } }) };
    assert.equal((await h.call(h.chatReq(code, body()))).status, 503);
    assert.equal(h.calls.deepseek.length, 0);
  } finally { h.restore(); }
});

test("⭐ 被额度拒掉的请求**不登记设备**、也不刷新 last_seen", async () => {
  // ⚠️ 根因（2026-10-12 全仓审查）：`check()` 原来把设备登记排在额度校验**之前** ——
  //    被额度拒掉的请求照样占一个设备名额、并刷新 `last`。
  //    改坏：把额度那两段挪回 `INSTALL_RE` 那一段之后 -> 这条红。
  const h = harness();
  try {
    const { code } = await (await h.call(h.adminReq("add", { name: "dq", max_devices: 2 }))).json();
    await h.call(h.adminReq("limit", { code, daily_asr_s: 0 }));     // 一上来就超额度
    const over = await h.call(h.asrReq(`Bearer ${code}`, INSTALL, "12"));
    assert.equal(over.status, 429);
    assert.equal((await over.json()).error, "quota_exceeded");
    const { codes } = await (await h.call(h.adminReq("list"))).json();
    const row = codes.find((c) => c.code === code);
    assert.equal(row.devices, 0, "⚠️ 被额度拒掉的请求占用了设备名额");
    assert.equal(row.last_seen, 0, "⚠️ 被额度拒掉的请求刷新了 last_seen");
    // 对照：额度放开之后，同一个请求**照常**登记设备（别把登记整个弄没了）
    await h.call(h.adminReq("limit", { code, daily_asr_s: null }));
    assert.equal((await h.call(h.asrReq(`Bearer ${code}`, INSTALL, "12"))).status, 200);
    const { codes: after } = await (await h.call(h.adminReq("list"))).json();
    assert.equal(after.find((c) => c.code === code).devices, 1);
  } finally { h.restore(); }
});

test("同名多条时按名字改会被拒，要给码", async () => {
  const h = harness();
  try {
    await h.call(h.adminReq("add", { name: "dup" }));
    await h.call(h.adminReq("add", { name: "dup" }));
    const r = await (await h.call(h.adminReq("revoke", { name: "dup" }))).json();
    assert.ok(r.error);
  } finally { h.restore(); }
});

const jq = (n) => Object.fromEntries(Array.from({ length: n }, (_, i) => [`S${i + 1}`, { type: "noul", instructions: "x" }]));

test("Jev：通行码转发给 TypeSafe，换成 Owen 的 key、模型钉 jev-1.13.0，按问题数记账", async () => {
  const h = harness();
  try {
    const { code } = await (await h.call(h.adminReq("add", { name: "ying" }))).json();
    const res = await h.call(h.jevReq(`Bearer ${code}`, { model: "jev-latest", state: "S1. hi", questions: jq(3) }));
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { answers: { S1: { noul: 0.7 } } });
    await h.settle();
    assert.equal(h.calls.jev.length, 1);
    assert.equal(h.calls.jev[0].headers.authorization, "Bearer apikey_owen");
    assert.equal(h.calls.jev[0].body.model, "jev-1.13.0");          // 客户端说 latest 也不跟
    const st = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    assert.deepEqual([st.days[0].rows[0].jev_reqs, st.days[0].rows[0].jev_q, st.days[0].rows[0].llm_reqs], [1, 3, 0]);
  } finally { h.restore(); }
});

test("Jev：公开令牌不代付（401）；超过 20 题 / 没有 state → 400 且不打上游", async () => {
  const h = harness();
  try {
    assert.equal((await h.call(h.jevReq("Bearer tok-public", { state: "x", questions: jq(1) }))).status, 401);
    const { code } = await (await h.call(h.adminReq("add", { name: "ying" }))).json();
    assert.equal((await h.call(h.jevReq(`Bearer ${code}`, { state: "x", questions: jq(21) }))).status, 400);
    assert.equal((await h.call(h.jevReq(`Bearer ${code}`, { questions: jq(1) }))).status, 400);
    assert.equal((await h.call(h.jevReq(`Bearer ${code}`, { state: "x", questions: {} }))).status, 400);
    assert.equal(h.calls.jev.length, 0);
  } finally { h.restore(); }
});

test("Jev：Worker 没配 JEV_KEY → 503（fail-closed，不打上游）；吊销的码 → 403", async () => {
  const h = harness();
  try {
    const { code } = await (await h.call(h.adminReq("add", { name: "ying" }))).json();
    const k = h.env.JEV_KEY;
    h.env.JEV_KEY = undefined;
    assert.equal((await h.call(h.jevReq(`Bearer ${code}`, { state: "x", questions: jq(1) }))).status, 503);
    h.env.JEV_KEY = k;
    await h.call(h.adminReq("revoke", { code }));
    assert.equal((await h.call(h.jevReq(`Bearer ${code}`, { state: "x", questions: jq(1) }))).status, 403);
    assert.equal(h.calls.jev.length, 0);
  } finally { h.restore(); }
});

// ---- 2026-10-14：记账补 model/ver、stats 跨天、/v1/admin/quota ----

test("记账：模型归桶（v3/turbo/qwen/other），版本按形状收，不合的不记", async () => {
  const h = harness();
  try {
    const { code } = await h.pass.add({ name: "t" });
    await h.pass.usage({ code, asr_s: 12, model: "whisper-large-v3" });
    await h.pass.usage({ code, asr_s: 12, model: "whisper-large-v3-turbo" });
    await h.pass.usage({ code, asr_s: 12, model: "qwen3-asr-flash" });
    await h.pass.usage({ code, asr_s: 12, model: "some-future-model" });   // 新模型上线不许炸
    await h.pass.usage({ code, ver: "3.9.4.1", tokens_in: 5, tokens_out: 1 });
    await h.pass.usage({ code, ver: "3.9.4.1", tokens_in: 5, tokens_out: 1 });
    await h.pass.usage({ code, ver: "带中文/斜杠", tokens_in: 5, tokens_out: 1 });  // 形状不合
    const st = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    const r = st.days[0].rows[0];
    assert.deepEqual(r.models, { v3: 1, turbo: 1, qwen: 1, other: 1 });
    assert.deepEqual(r.vers, { "3.9.4.1": 2 });
  } finally { h.restore(); }
});

test("模型与版本从真请求走到记账里（跑一遍 handler，不只是 core）", async () => {
  const h = harness();
  try {
    const { code } = await h.pass.add({ name: "t" });
    const req = new Request("https://asr.bldcam.page/v1/transcribe", {
      method: "POST",
      headers: { authorization: `Bearer ${code}`, "x-classlive-install": INSTALL,
                 "x-audio-seconds": "12", "x-classlive-version": "3.9.4.1" },
      body: new Uint8Array(100),
    });
    await h.call(req);
    await h.settle();
    const st = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    const r = st.days[0].rows[0];
    assert.deepEqual([r.models, r.vers], [{ v3: 1 }, { "3.9.4.1": 1 }]);
  } finally { h.restore(); }
});

test("老记录没有 models / vers 键：读得出来，再记一笔也只是补上新键", async () => {
  const h = harness();
  try {
    const { code } = await h.pass.add({ name: "old" });
    // 手工摆一条 2026-10-14 之前的形状
    h.pass.storage.m.set(`use:${TODAY}:${code}`, {
      asr_s: 30, asr_reqs: 2, llm_reqs: 0, tokens_in: 0, tokens_out: 0, cache_hit: 0,
      jev_reqs: 0, jev_q: 0,
    });
    const st = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    assert.equal(st.days[0].rows[0].asr_s, 30);
    assert.equal(st.days[0].rows[0].models, undefined);   // 老记录就是没这一键，面板得自己兜
    await h.pass.usage({ code, asr_s: 12, model: "whisper-large-v3" });
    const st2 = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    const r = st2.days[0].rows[0];
    assert.deepEqual([r.asr_s, r.asr_reqs, r.models], [42, 3, { v3: 1 }]);   // 老字段不丢
  } finally { h.restore(); }
});

test("stats 跨天：区间里每一天都出现（没记录的给空数组），from/to 回显", async () => {
  const h = harness();
  try {
    const { code } = await h.pass.add({ name: "t" });
    await h.pass.usage({ code, asr_s: 12 });
    h.tick(DAY);                                          // 10-12 -> 10-13
    await h.pass.usage({ code, asr_s: 30 });
    const st = await (await h.call(h.adminReq("stats", { days: 3 }))).json();
    assert.deepEqual([st.from, st.to], ["2026-10-11", "2026-10-13"]);
    assert.deepEqual(st.days.map((x) => x.day), ["2026-10-11", "2026-10-12", "2026-10-13"]);
    assert.deepEqual(st.days.map((x) => x.rows.length), [0, 1, 1]);
    assert.deepEqual([st.days[1].rows[0].asr_s, st.days[2].rows[0].asr_s], [12, 30]);
    const st2 = await (await h.call(h.adminReq("stats", { from: "2026-10-13", to: "2026-10-13" }))).json();
    assert.deepEqual(st2.days.map((x) => x.rows[0].asr_s), [30]);
  } finally { h.restore(); }
});

test("额度快照：/v1/admin/quota 走 Quota DO，只回计数与上限", async () => {
  const h = harness();
  try {
    assert.equal((await h.call(new Request("https://asr.bldcam.page/v1/admin/quota", {
      method: "POST", headers: { authorization: "Bearer wrong" }, body: "{}",
    }))).status, 401);                                   // 新暴露面也得先过鉴权
    const asr = await h.call(h.asrReq("Bearer tok-public", INSTALL, "50"));
    assert.equal(asr.status, 200, "先确认这一笔真的走通了（否则下面全是假绿）");
    await h.settle();
    const q = await (await h.call(h.adminReq("quota"))).json();
    assert.deepEqual(Object.keys(q).sort(), ["caps", "day", "now", "qwen", "turbo", "v3"]);
    assert.deepEqual([q.caps.reqs_day, q.caps.qwen_day], [1900, 3000]);
    assert.deepEqual([q.v3.used_reqs, q.v3.used_s], [1, 50]);
    assert.deepEqual([q.turbo.used_reqs, q.qwen.used, q.qwen.exhausted], [0, 0, []]);
  } finally { h.restore(); }
});

// ---- 2026-10-14 全仓审查的回归判据 ----

test("版本头在 chat / 流式 / JEV 三条路径上都记账（漏一条就不算做完）", async () => {
  const sse = ['data: {"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1,"prompt_cache_hit_tokens":0}}',
               "data: [DONE]", ""].join("\n\n");
  const h = harness({ deepseek: () => new Response(sse, { status: 200, headers: { "content-type": "text/event-stream" } }) });
  try {
    const { code } = await h.pass.add({ name: "t" });
    await h.call(h.chatReq(code, body({ stream: true }), INSTALL, "3.9.4.1"));   // 流式
    await h.settle();
    const st = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    assert.deepEqual(st.days[0].rows[0].vers, { "3.9.4.1": 1 }, "流式那条没记版本");
    await h.call(h.jevReq(`Bearer ${code}`, { state: "s", questions: { S1: { q: "x" } } }, INSTALL, "3.9.5"));
    await h.settle();
    const st2 = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    assert.deepEqual(st2.days[0].rows[0].vers, { "3.9.4.1": 1, "3.9.5": 1 }, "JEV 那条没记版本");
  } finally { h.restore(); }
});

test("公开令牌的转写不记版本（没有码就没人可记）", async () => {
  const h = harness();
  try {
    const req = new Request("https://asr.bldcam.page/v1/transcribe", {
      method: "POST",
      headers: { authorization: "Bearer tok-public", "x-classlive-install": INSTALL,
                 "x-audio-seconds": "12", "x-classlive-version": "3.9.4.1" },
      body: new Uint8Array(100),
    });
    assert.equal((await h.call(req)).status, 200);
    await h.settle();
    const st = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    assert.equal(st.days[0].rows.length, 0, "公开令牌不该产生任何按人记账");
  } finally { h.restore(); }
});

test("stats 的日期参数非法 -> 明确报错，不是 500", async () => {
  const h = harness();
  try {
    for (const bad of [{ day: "10-13" }, { day: "2026-1-1" }, { day: "garbage" }, { to: "2026/10/13" }]) {
      const r = await h.call(h.adminReq("stats", bad));
      assert.equal(r.status, 200, `DO 里抛出去就是 500：${JSON.stringify(bad)}`);
      assert.equal((await r.json()).error, "bad_day", `${JSON.stringify(bad)} 没被挡住`);
    }
  } finally { h.restore(); }
});

test("stats 的区间封顶：from 给 1990 年也只回 366 天；from > to 明确报错", async () => {
  const h = harness();
  try {
    const st = await (await h.call(h.adminReq("stats", { from: "1990-01-01" }))).json();
    assert.equal(st.days.length, 366, "没封顶（会一次吐上万条）");
    assert.equal(st.to, "2026-10-12");
    assert.equal(st.from, "2025-10-12");
    const inv = await (await h.call(h.adminReq("stats", { from: "2026-10-20", to: "2026-10-14" }))).json();
    assert.equal(inv.error, "bad_range", "倒过来的区间要明确报错，不能回一个空数组让调用方自己去猜");
  } finally { h.restore(); }
});

test("vers 的键数封顶：客户端连发 12 个版本，只留 8 个真键 + 一个「+」", async () => {
  const h = harness();
  try {
    const { code } = await h.pass.add({ name: "t" });
    for (let i = 1; i <= 12; i++) await h.pass.usage({ code, ver: `100000${i}` });
    const st = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    const v = st.days[0].rows[0].vers;
    assert.equal(Object.keys(v).length, 9, "键数没封顶（每请求一个新键会把这一格撑到写不进去）");
    assert.equal(v["+"], 4);
    assert.equal(v["1000001"], 1);
    assert.equal(v["1000012"], undefined);
  } finally { h.restore(); }
});

// ---- /admin/delete：不可逆，所以闸要厚 ----
test("delete：只给名字 / 不给 yes / yes 不是 true，三下都不动手", async () => {
  const h = harness();
  try {
    const { code } = await h.pass.add({ name: "junk" });
    const byName = await (await h.call(h.adminReq("delete", { name: "junk", yes: true }))).json();
    assert.match(byName.error, /完整码/);
    const noYes = await (await h.call(h.adminReq("delete", { code }))).json();
    assert.match(noYes.error, /yes/);
    const strYes = await (await h.call(h.adminReq("delete", { code, yes: "true" }))).json();
    assert.match(strYes.error, /yes/, "字符串 \"true\" 不算数，必须是布尔");
    const unknown = await (await h.call(h.adminReq("delete", { code: "cl-nowhere-0123456789abcdef", yes: true }))).json();
    assert.match(unknown.error, /unknown code/);
    const list = await (await h.call(h.adminReq("list", {}))).json();
    assert.equal(list.codes.length, 1, "上面四下都不该删掉东西");
  } finally { h.restore(); }
});

test("delete：码 + 设备 + 当天用量一起删，另一条一个键不碰", async () => {
  const h = harness();
  try {
    const a = await h.pass.add({ name: "junk" });
    const b = await h.pass.add({ name: "keep" });
    for (const [code, install] of [[a.code, INSTALL], [b.code, INSTALL2]]) {
      await h.call(h.asrReq(`Bearer ${code}`, install, "12"));
      await h.call(h.chatReq(code, body(), install));
    }
    await h.settle();
    const before = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    assert.equal(before.days[0].rows.length, 2, "先确认两条都记上账了，否则下面全是假绿");
    const keepBefore = before.days[0].rows.find((r) => r.name === "keep");

    const del = await (await h.call(h.adminReq("delete", { code: a.code, yes: true }))).json();
    assert.deepEqual([del.ok, del.name, del.usage_days], [true, "junk", 1]);

    const list = await (await h.call(h.adminReq("list", {}))).json();
    assert.deepEqual(list.codes.map((c) => c.name), ["keep"]);
    assert.deepEqual([...h.pass.storage.m.keys()].filter((k) => k.includes(a.code)), [],
                     "码 / 设备 / 用量三种键都该没了");
    assert.deepEqual([...h.pass.storage.m.keys()].filter((k) => k.includes(b.code)).sort(),
                     [`code:${b.code}`, `dev:${b.code}`, `use:${TODAY}:${b.code}`].sort(),
                     "另一条的键少一个都不行");
    const after = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    assert.deepEqual(after.days[0].rows.map((r) => r.name), ["keep"], "stats 里不留 name 为 ? 的孤儿行");
    assert.deepEqual(after.days[0].rows[0], keepBefore, "另一条的数一个都不能变");
  } finally { h.restore(); }
});

// ---- 2026-10-14 晚：逐分钟（作者：「面板能不能精确到分钟」）+ 设备列表 ----

test("逐分钟：按 UTC 当日分钟号分格，各格各记各的；stats 不带 mins 只带活跃分钟数", async () => {
  const h = harness();
  try {
    const { code } = await h.pass.add({ name: "t" });
    await h.pass.usage({ code, asr_s: 12.34, model: "whisper-large-v3" });      // 09:00 → 540
    await h.pass.usage({ code, tokens_in: 100, tokens_out: 20, cache_hit: 64 });
    h.tick(61_000);                                                             // 09:01 → 541
    await h.pass.usage({ code, asr_s: 10 });
    await h.pass.usage({ code, jev_q: 3 });
    await h.pass.usage({ code });                                               // 空记账不占格
    const mm = await (await h.call(h.adminReq("minutes", { code, day: TODAY }))).json();
    assert.deepEqual(mm.mins, { 540: [1, 12.3, 1, 120, 0], 541: [1, 10, 0, 0, 1] });
    const st = await (await h.call(h.adminReq("stats", { day: TODAY }))).json();
    const r = st.days[0].rows[0];
    assert.equal(r.mins, undefined, "stats 不许带逐分钟（太大）");
    assert.equal(r.active_min, 2);
  } finally { h.restore(); }
});

test("逐分钟 / 设备：只认完整码；日期非法明确报错；设备只回 id 前 6 位", async () => {
  const h = harness();
  try {
    const { code } = await h.pass.add({ name: "t" });
    await h.pass.check({ code, install: INSTALL, kind: "asr" });
    for (const body of [{ name: "t" }, { code: "cl-t-xyz" }]) {
      const r = await (await h.call(h.adminReq("minutes", body))).json();
      assert.ok(r.error, JSON.stringify(body));
    }
    const bad = await (await h.call(h.adminReq("minutes", { code, day: "2026-1" }))).json();
    assert.equal(bad.error, "bad_day");
    const dv = await (await h.call(h.adminReq("devices", { code }))).json();
    assert.deepEqual(dv.devices, [{ id: INSTALL.slice(0, 6), first: T0, last: T0 }]);
  } finally { h.restore(); }
});
