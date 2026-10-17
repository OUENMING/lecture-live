// 翻译快速上游（OpenRouter）判据 —— 直接 import src 真源码；OpenRouter / DeepSeek 换成假 fetch，
// Passes 用最小替身（只认 /check 与 /usage）。
//
//   node --test server/asr-worker/test-fastllm.mjs
//
// ⚠️ 不验证 Cloudflare 平台真实行为（真实 OpenRouter 响应、都柏林到美国机房的延迟、tee 的背压），
//    这里钉的是 Worker 自己的选路 / 退回 / 冷却 / 记账。
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { _leaseForTests, _resetFastLlm } from "./src/handler.js";

const CODE = "cl-amy-0123456789abcdef";
const INSTALL = "0123456789abcdef0123456789abcdef";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 返回在 ms 后兑现的响应；signal abort 时像真 fetch 一样 reject（响应头超时那条用）。
function later(ms, make, signal) {
  return new Promise((resolve, reject) => {
    const onAbort = () => { clearTimeout(t); reject(Object.assign(new Error("aborted"), { name: "AbortError" })); };
    const t = setTimeout(() => { signal?.removeEventListener("abort", onAbort); resolve(make()); }, ms);
    if (signal?.aborted) return onAbort();
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

const J = (status, obj) => new Response(JSON.stringify(obj), {
  status, headers: { "content-type": "application/json" },
});

// 最小 SSE：一块正文 + 末尾 usage（stream_options.include_usage 的格式，两家一样）。
const sse = (usage, text = "译") => new Response([
  `data: {"choices":[{"delta":{"content":"${text}"}}]}`,
  `data: {"choices":[],"usage":${JSON.stringify(usage)}}`,
  "data: [DONE]",
  "",
].join("\n\n"), { status: 200, headers: { "content-type": "text/event-stream" } });

const BODY = (extra = {}) => ({
  model: "deepseek-flash",
  messages: [{ role: "user", content: "x" }],
  max_tokens: 220,
  stream: true,
  thinking: { type: "enabled" },   // 客户端真会带；快速上游要删掉它
  ...extra,
});

function harness({ openrouter, deepseek, env: extraEnv = {} } = {}) {
  _leaseForTests(0);   // 短租关掉：这里每次都要真问 Passes / Quota
  _resetFastLlm();     // 冷却从干净状态开始（模块级变量，跨用例会留）
  const calls = { openrouter: [], deepseek: [], usage: [], ae: [] };
  const passesFetch = async (u, init = {}) => {
    const path = new URL(String(u)).pathname;
    if (path === "/check") return J(200, { ok: true });
    if (path === "/usage") { calls.usage.push(JSON.parse(init.body)); return J(200, {}); }
    return J(200, {});
  };
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    const body = init.body && typeof init.body === "string" ? JSON.parse(init.body) : null;
    if (u.startsWith("https://openrouter.ai/")) {
      calls.openrouter.push({ headers: init.headers, body, signal: init.signal });
      return openrouter
        ? openrouter(body, calls.openrouter.length, init.signal)
        : sse({ prompt_tokens: 11, completion_tokens: 5, prompt_tokens_details: { cached_tokens: 100 } });
    }
    if (u.startsWith("https://api.deepseek.com/")) {
      calls.deepseek.push({ headers: init.headers, body, signal: init.signal });
      return deepseek
        ? deepseek(body, calls.deepseek.length, init.signal)
        : sse({ prompt_tokens: 100, completion_tokens: 20, prompt_cache_hit_tokens: 64 }, "深");
    }
    throw new Error("unexpected upstream " + u);
  };
  const env = {
    CLIENT_TOKEN: "tok-public", DEEPSEEK_KEY: "sk-owen",
    PASSES: { idFromName: (n) => n, get: () => ({ fetch: passesFetch }) },
    AE: { writeDataPoint: (dp) => calls.ae.push(structuredClone(dp)) },
    ...extraEnv,
  };
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p) };
  const call = (req) => worker.fetch(req, env, ctx);
  const settle = () => Promise.all(pending.splice(0));
  const chatReq = (body, country = "IE") => {
    const r = new Request("https://asr.bldcam.page/v1/chat/completions", {
      method: "POST",
      headers: { authorization: `Bearer ${CODE}`, "x-classlive-install": INSTALL, "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (country) Object.defineProperty(r, "cf", { value: { country, colo: "DUB" } });
    return r;
  };
  const restore = () => { globalThis.fetch = origFetch; };
  return { env, calls, call, settle, chatReq, restore };
}

// ---------- a. 没配 key：行为与现在完全一致 ----------
test("a 没配 OPENROUTER_KEY -> 只调 DeepSeek，请求体与现在一致", async () => {
  const h = harness();
  try {
    const res = await h.call(h.chatReq(BODY()));
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.openrouter.length, 0, "没配 key 不许碰 OpenRouter");
    assert.equal(h.calls.deepseek.length, 1);
    assert.deepEqual(h.calls.deepseek[0].headers,
      { authorization: "Bearer sk-owen", "content-type": "application/json" });
    assert.deepEqual(h.calls.deepseek[0].body, {
      model: "deepseek-flash",
      messages: [{ role: "user", content: "x" }],
      max_tokens: 220,
      stream: true,
      thinking: { type: "enabled" },
      stream_options: { include_usage: true },
    });
    assert.equal(h.calls.ae[0].blobs[3], "deepseek:200");
  } finally { h.restore(); }
});

// ---------- b. 命中快速通道：只调 OpenRouter，请求体按 OpenRouter 的形状 ----------
test("b 流式 + max_tokens 220 + IE -> 只调 OpenRouter 一次，body 映射正确", async () => {
  const h = harness({ env: { OPENROUTER_KEY: "or-key" } });
  try {
    const res = await h.call(h.chatReq(BODY()));
    assert.equal(res.status, 200);
    const text = await res.text();
    await h.settle();
    assert.equal(h.calls.openrouter.length, 1, "命中就只调快速上游");
    assert.equal(h.calls.deepseek.length, 0);
    const c = h.calls.openrouter[0];
    assert.equal(c.headers.authorization, "Bearer or-key");
    assert.equal(c.headers["content-type"], "application/json");
    assert.equal(c.body.model, "deepseek/deepseek-v4.1-flash");
    assert.equal("thinking" in c.body, false, "thinking 要删掉");
    assert.deepEqual(c.body.reasoning, { enabled: false });
    assert.deepEqual(c.body.provider, {
      order: ["inference-net/fp8", "crusoe/fp8", "together"], allow_fallbacks: true,
    });
    assert.deepEqual(c.body.stream_options, { include_usage: true }, "记账要用的 usage 不能丢");
    assert.equal(c.body.max_tokens, 220);
    assert.equal(c.body.messages[0].content, "x");
    assert.match(text, /data: \[DONE\]/);
    assert.equal(h.calls.ae[0].blobs[3], "openrouter:200");
  } finally { h.restore(); }
});

// ---------- c. 快速上游 500：原样退回 DeepSeek ----------
test("c OpenRouter 500 -> DeepSeek 接住；收到的 body 保留原 thinking / model", async () => {
  const h = harness({
    env: { OPENROUTER_KEY: "or-key" },
    openrouter: () => J(500, { error: { message: "boom" } }),
  });
  try {
    const res = await h.call(h.chatReq(BODY()));
    assert.equal(res.status, 200, "客户端感知不到退回");
    await res.text();
    await h.settle();
    assert.equal(h.calls.openrouter.length, 1);
    assert.equal(h.calls.deepseek.length, 1);
    assert.deepEqual(h.calls.deepseek[0].body, {
      model: "deepseek-flash",
      messages: [{ role: "user", content: "x" }],
      max_tokens: 220,
      stream: true,
      thinking: { type: "enabled" },
      stream_options: { include_usage: true },
    });
    assert.equal(h.calls.ae[0].blobs[3], "openrouter:500>deepseek:200", "两段都要留");
  } finally { h.restore(); }
});

// ---------- d. 响应头超时：AbortController 掐掉，退回 DeepSeek ----------
test("d OpenRouter 响应头超过 FAST_LLM_HEADER_MS -> abort 并退回", async () => {
  const h = harness({
    env: { OPENROUTER_KEY: "or-key", FAST_LLM_HEADER_MS: "50" },
    openrouter: (b, n, signal) => later(300, () => sse({ prompt_tokens: 1, completion_tokens: 1 }), signal),
  });
  try {
    const t0 = Date.now();
    const res = await h.call(h.chatReq(BODY()));
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.ok(Date.now() - t0 < 200, "不能等满 300 ms");
    assert.equal(h.calls.openrouter.length, 1);
    assert.equal(h.calls.openrouter[0].signal.aborted, true, "超时必须 abort");
    assert.equal(h.calls.deepseek.length, 1);
    assert.equal(h.calls.ae[0].blobs[3], "openrouter:timeout>deepseek:200");
  } finally { h.restore(); }
});

// ---------- e. 402 -> 冷却 10 分钟；测试钩子能清 ----------
test("e OpenRouter 402 -> 退回 + 冷却；期间不再调，_resetFastLlm 后恢复", async () => {
  const h = harness({
    env: { OPENROUTER_KEY: "or-key" },
    openrouter: () => J(402, { error: { message: "insufficient credits" } }),
  });
  try {
    const first = await h.call(h.chatReq(BODY()));
    assert.equal(first.status, 200);
    await first.text();
    await h.settle();
    assert.equal(h.calls.deepseek.length, 1);
    assert.equal(h.calls.ae[0].blobs[3], "openrouter:402>deepseek:200");

    const second = await h.call(h.chatReq(BODY()));
    assert.equal(second.status, 200);
    await second.text();
    await h.settle();
    assert.equal(h.calls.openrouter.length, 1, "冷却期间直接走 DeepSeek");
    assert.equal(h.calls.deepseek.length, 2);
    assert.equal(h.calls.ae[1].blobs[3], "deepseek:200");

    _resetFastLlm();
    const third = await h.call(h.chatReq(BODY()));
    await third.text();
    await h.settle();
    assert.equal(h.calls.openrouter.length, 2, "重置后重新试快速上游");
    assert.equal(h.calls.deepseek.length, 3, "仍回 402，所以照常退回");
  } finally { h.restore(); }
});

// ---------- f. 精修 / 非流式不绕道 ----------
test("f max_tokens 4000 或 stream=false -> 不碰 OpenRouter", async () => {
  const h = harness({ env: { OPENROUTER_KEY: "or-key" } });
  try {
    // 对照：同样配置下 220 + 流式确实会走，后面的「不走」才有意义。
    const fast = await h.call(h.chatReq(BODY()));
    await fast.text();
    await h.settle();
    assert.equal(h.calls.openrouter.length, 1);
    assert.equal(h.calls.deepseek.length, 0);

    const big = await h.call(h.chatReq(BODY({ max_tokens: 4000 })));
    await big.text();
    await h.settle();
    assert.equal(h.calls.openrouter.length, 1, "精修 4000 仍走 DeepSeek");
    assert.equal(h.calls.deepseek.length, 1);
    assert.equal(h.calls.deepseek[0].body.max_tokens, 4000);

    const plain = await h.call(h.chatReq(BODY({ stream: false })));
    await plain.text();
    await h.settle();
    assert.equal(h.calls.openrouter.length, 1, "非流式不绕道");
    assert.equal(h.calls.deepseek.length, 2);
    assert.equal(h.calls.deepseek[1].body.stream_options, undefined);
  } finally { h.restore(); }
});

// ---------- g. 中国 / 港澳不绕美国；FAST_LLM_SKIP 可覆盖 ----------
test("g country=CN -> 不碰 OpenRouter；FAST_LLM_SKIP=\"\" 时 CN 也走", async () => {
  const h = harness({ env: { OPENROUTER_KEY: "or-key" } });
  try {
    const cn = await h.call(h.chatReq(BODY(), "CN"));
    await cn.text();
    await h.settle();
    assert.equal(h.calls.openrouter.length, 0, "CN 离 DeepSeek 近，不绕美国");
    assert.equal(h.calls.deepseek.length, 1);

    h.env.FAST_LLM_SKIP = "";
    const again = await h.call(h.chatReq(BODY(), "CN"));
    await again.text();
    await h.settle();
    assert.equal(h.calls.openrouter.length, 1, "显式清空后 CN 也走快速上游");
    assert.equal(h.calls.deepseek.length, 1);
  } finally { h.restore(); }
});

// ---------- h. OpenRouter 的 usage 字段 ----------
test("h OpenRouter 流末尾 usage：cached_tokens -> cache_hit，用量上报不变", async () => {
  const usage = { prompt_tokens: 11, completion_tokens: 5, prompt_tokens_details: { cached_tokens: 100 } };
  const h = harness({ env: { OPENROUTER_KEY: "or-key" }, openrouter: () => sse(usage) });
  try {
    const res = await h.call(h.chatReq(BODY()));
    await res.text();
    await h.settle();
    const ev = h.calls.ae[0];
    assert.deepEqual([ev.doubles[3], ev.doubles[4], ev.doubles[5]], [11, 5, 100]);
    assert.equal(ev.blobs[3], "openrouter:200");
    assert.equal(h.calls.usage.length, 1, "通行码用量照报");
    assert.deepEqual(
      [h.calls.usage[0].tokens_in, h.calls.usage[0].tokens_out, h.calls.usage[0].cache_hit],
      [11, 5, 100],
    );
  } finally { h.restore(); }
});
