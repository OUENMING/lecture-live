// 长调用主通道（CommandCode）判据 —— 直接 import src 真源码；CC / OpenRouter / DeepSeek 换成假 fetch。
//
//   node --test server/asr-worker/test-cmdchat.mjs
//
// 钉的是 Worker 自己的选路 / 退回 / 记账，不验证 CommandCode 的真实响应（2026-11-05 手工实测过：
// OpenAI 兼容端收 thinking / reasoning_effort / response_format / max_tokens=16384，首字约 1.3 s）。
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { _leaseForTests, _resetFastLlm, _resetCmdChat, useCmdChat } from "./src/handler.js";

const CODE = "cl-amy-0123456789abcdef";
const INSTALL = "0123456789abcdef0123456789abcdef";

const J = (status, obj) => new Response(JSON.stringify(obj), {
  status, headers: { "content-type": "application/json" },
});

const sse = (text = "译") => new Response([
  `data: {"choices":[{"delta":{"content":"${text}"}}]}`,
  `data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2}}`,
  "data: [DONE]",
  "",
].join("\n\n"), { status: 200, headers: { "content-type": "text/event-stream" } });

const completion = (text = "笔记", p = 1000, c = 200) => J(200, {
  choices: [{ index: 0, message: { role: "assistant", content: text }, finish_reason: "stop" }],
  usage: { prompt_tokens: p, completion_tokens: c },
});

const BODY = (extra = {}) => ({
  model: "deepseek-flash",
  messages: [{ role: "user", content: "x" }],
  max_tokens: 3000,           // > FAST_LLM_MAX_TOKENS（400）→ 长调用
  stream: false,
  thinking: { type: "enabled" },
  reasoning_effort: "low",
  ...extra,
});

function harness({ cc, deepseek, openrouter, env: extraEnv = {} } = {}) {
  _leaseForTests(0);
  _resetFastLlm();
  _resetCmdChat();
  const calls = { cc: [], openrouter: [], deepseek: [], usage: [], ae: [] };
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
    if (u.startsWith("https://api.commandcode.ai/")) {
      calls.cc.push({ headers: init.headers, body, signal: init.signal });
      return cc ? cc(body, calls.cc.length, init.signal) : completion("cc");
    }
    if (u.startsWith("https://openrouter.ai/")) {
      calls.openrouter.push({ headers: init.headers, body });
      return openrouter ? openrouter(body) : completion("or");
    }
    if (u.startsWith("https://api.deepseek.com/")) {
      calls.deepseek.push({ headers: init.headers, body, signal: init.signal });
      return deepseek ? deepseek(body, calls.deepseek.length)
        : (body.stream ? sse("深") : completion("ds"));
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
  const call = (body, country = "IE") => {
    const r = new Request("https://asr.bldcam.page/v1/chat/completions", {
      method: "POST",
      headers: { authorization: `Bearer ${CODE}`, "x-classlive-install": INSTALL, "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (country) Object.defineProperty(r, "cf", { value: { country, colo: "DUB" } });
    return worker.fetch(r, env, ctx);
  };
  const settle = () => Promise.all(pending.splice(0));
  const restore = () => { globalThis.fetch = origFetch; };
  return { calls, call, settle, restore };
}

// ---------- 选路判据 ----------
test("useCmdChat：只有非流式长调用 + 配了 key 才走 CC", () => {
  assert.equal(useCmdChat({}, { max_tokens: 3000 }), false, "没 key 不走");
  assert.equal(useCmdChat({ CMD_CHAT_KEY: "k" }, { max_tokens: 3000 }), true);
  assert.equal(useCmdChat({ JEV_CC_KEY: "k" }, { max_tokens: 3000 }), true, "没有单独 key 时复用 Jev 那把");
  assert.equal(useCmdChat({ CMD_CHAT_KEY: "k" }, { max_tokens: 400 }), false, "短翻译不动");
  assert.equal(useCmdChat({ CMD_CHAT_KEY: "k" }, { max_tokens: 401 }), true);
  assert.equal(useCmdChat({ CMD_CHAT_KEY: "k" }, { max_tokens: 3000, stream: true }), false,
    "实时类（讲解 1400 也是流式）不走 CC");
});

// ---------- a. 长调用命中：只调 CC，模型 ID 换成 CC 的 ----------
test("a 长调用 + CMD_CHAT_KEY -> 只调 CC 一次，model 映射，DeepSeek 不动", async () => {
  const h = harness({ env: { CMD_CHAT_KEY: "cmd-key" } });
  try {
    const res = await h.call(BODY());
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.cc.length, 1);
    assert.equal(h.calls.deepseek.length, 0);
    const c = h.calls.cc[0];
    assert.equal(c.headers.authorization, "Bearer cmd-key");
    assert.equal(c.headers["user-agent"], "ClassLive/0.0 (+https://github.com/OUENMING/lecture-live)");
    assert.equal(c.body.model, "deepseek/deepseek-v4.1-flash");
    assert.deepEqual(c.body.thinking, { type: "enabled" }, "思考参数原样带过去");
    assert.equal(c.body.reasoning_effort, "low");
    assert.equal(c.body.max_tokens, 3000);
    assert.equal(h.calls.ae[0].blobs[3], "cmd:200");
  } finally { h.restore(); }
});

// ---------- b. CC 402 -> 退回 DeepSeek，body 是客户端原样 ----------
test("b CC 402 -> DeepSeek 接住；记录 cmd:402>deepseek:200", async () => {
  const h = harness({
    cc: () => J(402, { error: "Insufficient Balance" }),
    env: { CMD_CHAT_KEY: "cmd-key" },
  });
  try {
    const res = await h.call(BODY());
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.cc.length, 1);
    assert.equal(h.calls.deepseek.length, 1);
    assert.equal(h.calls.deepseek[0].body.model, "deepseek-flash", "退回必须用原 body");
    assert.equal(h.calls.ae[0].blobs[3], "cmd:402>deepseek:200");
  } finally { h.restore(); }
});

// ---------- c. CC 响应头超时 -> 退回 DeepSeek ----------
test("c CC 超时 -> DeepSeek 接住；记录 cmd:timeout>deepseek:200", async () => {
  const h = harness({
    cc: (body, n, signal) => new Promise((resolve, reject) => {
      const onAbort = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
      signal?.addEventListener("abort", onAbort, { once: true });
    }),
    env: { CMD_CHAT_KEY: "cmd-key", CMD_CHAT_HEADER_MS: "30" },
  });
  try {
    const res = await h.call(BODY());
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.deepseek.length, 1);
    assert.equal(h.calls.ae[0].blobs[3], "cmd:timeout>deepseek:200");
  } finally { h.restore(); }
});

// ---------- d. 短翻译不碰 CC ----------
test("d 短翻译（max_tokens 220）不调 CC：IE 仍走 OpenRouter", async () => {
  const h = harness({ env: { CMD_CHAT_KEY: "cmd-key", OPENROUTER_KEY: "or-key" } });
  try {
    const res = await h.call(BODY({ max_tokens: 220, stream: true }));
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.cc.length, 0, "短翻译不许走 CC（首字慢 0.9 s）");
    assert.equal(h.calls.openrouter.length, 1);
  } finally { h.restore(); }
});

// ---------- d2. 流式长调用（讲解 1400）也不碰 CC ----------
test("d2 流式长调用（讲解 max_tokens 1400）不调 CC，退回 DeepSeek", async () => {
  const h = harness({ env: { CMD_CHAT_KEY: "cmd-key", OPENROUTER_KEY: "or-key" } });
  try {
    const res = await h.call(BODY({ max_tokens: 1400, stream: true }));
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.cc.length, 0, "实时（流式）一律不走 CC");
    assert.equal(h.calls.openrouter.length, 0, ">400 不命中快速通道");
    assert.equal(h.calls.deepseek.length, 1);
  } finally { h.restore(); }
});

// ---------- e. 没配 CMD key：行为与改动前一致 ----------
test("e 没配 CMD key -> 长调用直接 DeepSeek，不碰 CC", async () => {
  const h = harness();
  try {
    const res = await h.call(BODY());
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.cc.length, 0);
    assert.equal(h.calls.deepseek.length, 1);
    assert.equal(h.calls.ae[0].blobs[3], "deepseek:200");
  } finally { h.restore(); }
});

// ---------- f. 只有 JEV_CC_KEY 也能用 ----------
test("f 只有 JEV_CC_KEY -> 长调用走 CC，用的就是那把 key", async () => {
  const h = harness({ env: { JEV_CC_KEY: "jev-cc-key" } });
  try {
    const res = await h.call(BODY());
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.cc.length, 1);
    assert.equal(h.calls.cc[0].headers.authorization, "Bearer jev-cc-key");
  } finally { h.restore(); }
});

// ---------- g. CC 200 但不是 JSON -> 当失败退回 ----------
test("g CC 200 非 JSON -> 退回 DeepSeek；记账 cmd:200:bad>deepseek:200", async () => {
  const h = harness({
    cc: () => new Response("<html>oops</html>", { status: 200, headers: { "content-type": "text/html" } }),
    deepseek: () => completion("ds", 7, 3),
    env: { CMD_CHAT_KEY: "cmd-key" },
  });
  try {
    const res = await h.call(BODY());
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.deepseek.length, 1);
    assert.equal(h.calls.ae[0].blobs[3], "cmd:200:bad>deepseek:200");
    assert.equal(h.calls.usage.length, 1, "用量只按成功那次记一次");
    assert.equal(h.calls.usage[0].tokens_in, 7);
  } finally { h.restore(); }
});

// ---------- h. CC 200 之后正文断流 -> 退回 ----------
test("h CC 200 正文断流 -> 退回 DeepSeek；记账 cmd:200:body>deepseek:200", async () => {
  const h = harness({
    cc: () => new Response(new ReadableStream({ start(c) { c.error(new Error("boom")); } }),
      { status: 200, headers: { "content-type": "application/json" } }),
    env: { CMD_CHAT_KEY: "cmd-key" },
  });
  try {
    const res = await h.call(BODY());
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.deepseek.length, 1);
    assert.equal(h.calls.ae[0].blobs[3], "cmd:200:body>deepseek:200");
  } finally { h.restore(); }
});

// ---------- i. CC 429 -> 退回 + 10 分钟冷却 ----------
test("i CC 429 -> 退回 DeepSeek，冷却期内下一次不再白打 CC", async () => {
  const h = harness({
    cc: () => J(429, { error: "rate_limited" }),
    env: { CMD_CHAT_KEY: "cmd-key" },
  });
  try {
    const r1 = await h.call(BODY());
    await r1.text();
    await h.settle();
    assert.equal(h.calls.cc.length, 1);
    assert.equal(h.calls.ae[0].blobs[3], "cmd:429>deepseek:200");

    const r2 = await h.call(BODY());
    await r2.text();
    await h.settle();
    assert.equal(h.calls.cc.length, 1, "冷却期内不许再打 CC");
    assert.equal(h.calls.ae[1].blobs[3], "deepseek:200");
  } finally { h.restore(); }
});

// ---------- j. 没传 max_tokens：clamp 到 16384 -> 走 CC ----------
test("j 非流式没传 max_tokens -> clamp 16384，走 CC", async () => {
  const h = harness({ env: { CMD_CHAT_KEY: "cmd-key" } });
  try {
    const b = BODY(); delete b.max_tokens;
    const res = await h.call(b);
    await res.text();
    await h.settle();
    assert.equal(h.calls.cc.length, 1);
    assert.equal(h.calls.cc[0].body.max_tokens, 16384);
  } finally { h.restore(); }
});

// ---------- k. 成功路径的用量按 CC 的数字记 ----------
test("k CC 成功：/usage 记一次，token 数来自 CC", async () => {
  const h = harness({
    cc: () => completion("cc", 111, 22),
    env: { CMD_CHAT_KEY: "cmd-key" },
  });
  try {
    const res = await h.call(BODY());
    await res.text();
    await h.settle();
    assert.equal(h.calls.usage.length, 1);
    assert.equal(h.calls.usage[0].tokens_in, 111);
    assert.equal(h.calls.usage[0].tokens_out, 22);
    assert.equal(h.calls.ae[0].blobs[3], "cmd:200");
  } finally { h.restore(); }
});
