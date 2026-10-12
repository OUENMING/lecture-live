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

function memStorage() {
  const m = new Map();
  return {
    m,
    get: async (k) => structuredClone(m.get(k)),
    put: async (k, v) => { m.set(k, structuredClone(v)); },
    list: async ({ prefix }) => new Map([...m].filter(([k]) => k.startsWith(prefix)).sort()),
  };
}

const J = (status, obj) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const doStub = (core) => ({
  idFromName: (n) => n,
  get: () => ({ fetch: async (u, init) => core.handle(new Request(u, init)) }),
});

function harness({ deepseek } = {}) {
  let now = Date.UTC(2026, 9, 12, 9);
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
  const chatReq = (code, body, install = INSTALL) => new Request("https://asr.bldcam.page/v1/chat/completions", {
    method: "POST",
    headers: { authorization: `Bearer ${code}`, "x-classlive-install": install, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const asrReq = (auth, install = INSTALL, seconds = "12") => new Request("https://asr.bldcam.page/v1/transcribe", {
    method: "POST",
    headers: { authorization: auth, "x-classlive-install": install, "x-audio-seconds": seconds },
    body: new Uint8Array(100),
  });
  const jevReq = (auth, body, install = INSTALL) => new Request("https://asr.bldcam.page/v1/jev", {
    method: "POST",
    headers: { authorization: auth, "x-classlive-install": install, "content-type": "application/json" },
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
    const st = await (await h.call(h.adminReq("stats"))).json();
    assert.equal(st.rows.length, 1);
    assert.deepEqual([st.rows[0].name, st.rows[0].llm_reqs, st.rows[0].tokens_in, st.rows[0].tokens_out, st.rows[0].cache_hit],
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
    const st = await (await h.call(h.adminReq("stats"))).json();
    assert.deepEqual([st.rows[0].tokens_in, st.rows[0].tokens_out, st.rows[0].cache_hit], [7, 2, 5]);
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
    let st = await (await h.call(h.adminReq("stats"))).json();
    assert.equal(st.rows.length, 0);
    const { code } = await (await h.call(h.adminReq("add", { name: "t" }))).json();
    assert.equal((await h.call(h.asrReq(`Bearer ${code}`, INSTALL, "8.5"))).status, 200);
    await h.settle();
    st = await (await h.call(h.adminReq("stats"))).json();
    assert.deepEqual([st.rows[0].name, st.rows[0].asr_s, st.rows[0].asr_reqs], ["t", 8.5, 1]);
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
    const st = await (await h.call(h.adminReq("stats"))).json();
    assert.deepEqual([st.rows[0].jev_reqs, st.rows[0].jev_q, st.rows[0].llm_reqs], [1, 3, 0]);
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
