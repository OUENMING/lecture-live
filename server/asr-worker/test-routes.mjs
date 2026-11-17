// 对话上游路由表（deepseek / cc / opencode）与 OpenCode Go 通道的判据 —— 直接 import src 真源码，
// 所有上游（OpenCode / CommandCode / DeepSeek / Passes DO）都换成假 fetch，**不连外网、不用任何 key**。
//
//   cd server/asr-worker && node --test test-routes.mjs
//
// 钉住什么（对应 Owen 2026-11-17 那张单子）：
//   1. 默认表与改动前**逐类等价**（note/polish/prep → cc，其余 → deepseek，老客户端 ≥4000 当 note）
//   2. 来源优先级 DO 表 > env.CHAT_ROUTES > 默认；`*` 管这一层全部类；非法值 → deepseek
//   3. 流式 + 表里写 opencode → 不走 opencode
//   4. opencode 成功：`ev.up = opencode:200`、请求带 `x-opencode-session`、model 换成 Go 的 id、thinking 原样转发（缺省补 disabled）
//   5. opencode 坏 JSON / 402 / 超时 → 退回 DeepSeek；402 之后冷却期内不再白试
//   6. 没配 OPENCODE_GO_KEY → deepseek
//   7. admin set 拒绝非法上游
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, {
  _leaseForTests, _resetCmdChat, _resetFastLlm, _resetOpenCode, _resetRoutes,
  openCodeSession,
} from "./src/handler.js";
import { defaultRoutes, mergeRoutes, parseRoutes, resolveRoute } from "./src/routes.js";
import { PassCore } from "./src/passes-core.js";

const CODE = "cl-amy-0123456789abcdef";
const INSTALL = "0123456789abcdef0123456789abcdef";
const UA = "ClassLive/0.0 (+https://github.com/OUENMING/lecture-live)";

const J = (status, obj) => new Response(JSON.stringify(obj), {
  status, headers: { "content-type": "application/json" },
});

const completion = (text = "内容", p = 111, c = 22) => J(200, {
  choices: [{ index: 0, message: { role: "assistant", content: text }, finish_reason: "stop" }],
  usage: { prompt_tokens: p, completion_tokens: c },
});

const BODY = (extra = {}) => ({
  model: "deepseek-flash",
  messages: [{ role: "user", content: "x" }],
  max_tokens: 3000,
  stream: false,
  thinking: { type: "enabled" },
  reasoning_effort: "low",
  ...extra,
});

// ---- Passes DO：真的 PassCore 跑在内存 storage 上（路由表的校验就是它管的）----
function memStorage(seed = {}) {
  const m = new Map(Object.entries(seed));
  return {
    map: m,
    get: async (k) => (m.has(k) ? structuredClone(m.get(k)) : undefined),
    put: async (k, v) => { m.set(k, structuredClone(v)); },
    delete: async (k) => { for (const kk of [].concat(k)) m.delete(kk); },
    list: async ({ prefix = "" } = {}) => new Map([...m].filter(([k]) => k.startsWith(prefix))),
  };
}

function harness({ oc, cc, deepseek, env: extraEnv = {}, stored = {}, routesFail = false, routesRaw } = {}) {
  _leaseForTests(0);
  _resetFastLlm();
  _resetCmdChat();
  _resetOpenCode();
  _resetRoutes();
  const calls = { oc: [], cc: [], deepseek: [], usage: [], ae: [], out: [], routesGet: 0 };
  const core = new PassCore(memStorage(stored && Object.keys(stored).length ? { "chat-routes": stored } : {}));
  const passesFetch = async (u, init = {}) => {
    const path = new URL(String(u)).pathname;
    if (path.startsWith("/admin/routes")) {
      if (path === "/admin/routes/get") calls.routesGet += 1;
      if (routesFail) throw new Error("DO down");                       // 模拟路由表那条读挂掉
      if (routesRaw !== undefined) return J(200, routesRaw);            // 模拟 DO 回了坏形状
      return core.handle(new Request(`https://passes${path}`, { method: "POST", body: init.body || "{}" }));
    }
    if (path === "/check") return J(200, { ok: true });
    if (path === "/usage") { calls.usage.push(JSON.parse(init.body)); return J(200, {}); }
    return J(200, {});
  };
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    const body = init.body && typeof init.body === "string" ? JSON.parse(init.body) : null;
    if (u.startsWith("https://opencode.ai/")) {
      calls.oc.push({ headers: init.headers, body, signal: init.signal });
      return oc ? oc(body, calls.oc.length, init.signal) : completion("oc");
    }
    if (u.startsWith("https://api.commandcode.ai/")) {
      calls.cc.push({ headers: init.headers, body, signal: init.signal });
      return cc ? cc(body, calls.cc.length, init.signal) : completion("cc");
    }
    if (u.startsWith("https://api.deepseek.com/")) {
      calls.deepseek.push({ headers: init.headers, body, signal: init.signal });
      return deepseek ? deepseek(body, calls.deepseek.length) : completion("ds");
    }
    throw new Error("unexpected upstream " + u);
  };
  const env = {
    CLIENT_TOKEN: "tok-public", DEEPSEEK_KEY: "sk-owen", ADMIN_TOKEN: "adm",
    PASSES: { idFromName: (n) => n, get: () => ({ fetch: passesFetch }) },
    AE: { writeDataPoint: (dp) => calls.ae.push(structuredClone(dp)) },
    ...extraEnv,
  };
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p) };
  const call = (body, { cls = "note", install = INSTALL, country = "IE", code = CODE, route = "" } = {}) => {
    const headers = { authorization: `Bearer ${code}`, "x-classlive-install": install, "content-type": "application/json" };
    if (cls) headers["x-classlive-call"] = cls;
    if (route) headers["x-classlive-route"] = route;
    const r = new Request("https://asr.bldcam.page/v1/chat/completions", {
      method: "POST", headers, body: JSON.stringify(body),
    });
    if (country) Object.defineProperty(r, "cf", { value: { country, colo: "DUB" } });
    return worker.fetch(r, env, ctx);
  };
  // 管理接口：只走 admin()（鉴权 = ADMIN_TOKEN），请求不进事件。
  const admin = async (name, body = {}) => {
    const r = new Request(`https://asr.bldcam.page/v1/admin/${name}`, {
      method: "POST", headers: { authorization: "Bearer adm", "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const res = await worker.fetch(r, env, ctx);
    return { status: res.status, json: await res.json() };
  };
  const settle = () => Promise.all(pending.splice(0));
  const restore = () => { globalThis.fetch = origFetch; };
  return { calls, call, admin, core, settle, restore };
}

// ---------- 1. 纯函数：默认表与改动前逐类等价 ----------
test("1 默认表 = 现状逐类：note/polish/prep → cc，其余 → deepseek", () => {
  const d = defaultRoutes({});
  for (const c of ["note", "polish", "prep"]) assert.equal(resolveRoute(d, c, {}), "cc", `${c} 应走 cc`);
  for (const c of ["atom", "chapter", "classify", "seal", "merge", "term", "note-build", "recap",
                   "keyentry", "live", "glossary"]) {
    assert.equal(resolveRoute(d, c, {}), "deepseek", `${c} 必须回 deepseek`);
  }
});

test("1b 老客户端不发调用类：max_tokens ≥ 4000 当 note，其余 → deepseek（缺失 ≠ 16384）", () => {
  const d = defaultRoutes({});
  assert.equal(resolveRoute(d, "", { max_tokens: 4000 }), "cc", "≥4000 兜底当 note");
  assert.equal(resolveRoute(d, "", { max_tokens: 16384 }), "cc");
  assert.equal(resolveRoute(d, "", { max_tokens: 3000 }), "deepseek", "<4000 = legacy");
  assert.equal(resolveRoute(d, "", { max_tokens: undefined }), "deepseek", "缺字段不能 fail-open");
  assert.equal(resolveRoute(d, undefined, undefined), "deepseek", "没有 body 也回 deepseek");
});

test("1c env.CMD_CHAT_CLASSES 改默认白名单", () => {
  const d = defaultRoutes({ CMD_CHAT_CLASSES: "atom, chapter" });
  assert.equal(resolveRoute(d, "atom", {}), "cc");
  assert.equal(resolveRoute(d, "chapter", {}), "cc");
  assert.equal(resolveRoute(d, "note", {}), "deepseek", "改过之后 note 不再默认走 cc");
});

// ---------- 2. 优先级 / `*` / 非法值 ----------
test("2 env.CHAT_ROUTES > 默认；`*` 管这一层全部类", () => {
  const one = mergeRoutes(defaultRoutes({}), parseRoutes("atom=cc"));
  assert.equal(resolveRoute(one, "atom", {}), "cc", "env 点名的类");
  assert.equal(resolveRoute(one, "note", {}), "cc", "默认表照旧");
  assert.equal(resolveRoute(one, "chapter", {}), "deepseek");
  const star = mergeRoutes(defaultRoutes({}), parseRoutes("*=opencode"));
  assert.equal(resolveRoute(star, "atom", {}), "opencode");
  assert.equal(resolveRoute(star, "note", {}), "opencode", "上层 `*` 必须管到下层点名过的类");
});

test("3 DO 表 > env > 默认（三层同场）", () => {
  const t = mergeRoutes(defaultRoutes({}), parseRoutes("chapter=cc"), parseRoutes({ note: "opencode" }));
  assert.equal(resolveRoute(t, "note", {}), "opencode", "DO 表最高");
  assert.equal(resolveRoute(t, "chapter", {}), "cc", "env 次之");
  assert.equal(resolveRoute(t, "polish", {}), "cc", "默认最后");
  assert.equal(resolveRoute(t, "atom", {}), "deepseek", "谁都没说 → deepseek");
  const star = mergeRoutes(defaultRoutes({}), parseRoutes("note=cc"), parseRoutes({ "*": "opencode", note: "cc" }));
  assert.equal(resolveRoute(star, "polish", {}), "opencode", "DO 的 `*` 盖掉默认表");
  assert.equal(resolveRoute(star, "note", {}), "cc", "同层点名的类赢过同层 `*`");
});

test("3b 非法值 → deepseek；大小写 / 空格归一；没有表 = 默认", () => {
  assert.equal(resolveRoute({ note: "gemini" }, "note", {}), "deepseek", "值不在三家 → fail-safe");
  assert.equal(resolveRoute({ note: " CC " }, "note", {}), "cc");
  assert.equal(resolveRoute(null, "note", {}), "deepseek");
  assert.equal(resolveRoute([], "note", {}), "deepseek", "数组不是表");
  assert.equal(resolveRoute({ note: "x" }, "note", {}), "deepseek");
});

test("3c parseRoutes：形状不合的项丢掉", () => {
  assert.deepEqual(parseRoutes("note=cc, *=opencode ,bad,=cc,x=y"),
    { note: "cc", "*": "opencode", x: "deepseek" });
  assert.equal(parseRoutes(""), null);
  assert.equal(parseRoutes({}), null);
  assert.equal(parseRoutes("nope"), null, "没有 = 的项不成表");
});

// ---------- 4. chat 路径：opencode 成功 ----------
test("4a opencode 成功：真的只调了一次 opencode，别的上游不动", async () => {
  const h = harness({ env: { OPENCODE_GO_KEY: "oc-key", CHAT_ROUTES: "*=opencode" } });
  try {
    const res = await h.call(BODY({ thinking: { type: "enabled" }, reasoning: { enabled: true } }));
    assert.equal(res.status, 200);
    const out = await res.json();
    await h.settle();
    assert.equal(out.choices[0].message.content, "oc");
    assert.equal(h.calls.oc.length, 1);
    assert.equal(h.calls.deepseek.length, 0);
    assert.equal(h.calls.cc.length, 0);
    const c = h.calls.oc[0];
    assert.equal(c.headers.authorization, "Bearer oc-key");
    assert.equal(c.headers["content-type"], "application/json");
    assert.equal(c.headers["user-agent"], UA, "官方要求自报 UA，别用通用库名");
    assert.match(c.headers["x-opencode-session"], /^classlive-note-[0-9a-f]{8}$/, "session 必带");
    assert.equal(c.body.model, "deepseek-v4.1-flash", "模型 ID 换成 Go 的");
    assert.deepEqual(c.body.thinking, { type: "enabled" }, "thinking 原样转发（Go 认它）");
    assert.equal("reasoning" in c.body, false, "不发 reasoning");
    assert.equal(c.body.messages[0].content, "x");
    assert.equal(h.calls.ae[0].blobs[3], "opencode:200");
    assert.equal(h.calls.usage.length, 1);
    assert.equal(h.calls.usage[0].tokens_in, 111, "用量按 opencode 的数字记");
  } finally { h.restore(); }
});

test("4b session 稳定：同一安装同一类两次相同；换类 / 换安装 / 无安装都变", async () => {
  const a = await openCodeSession(INSTALL, "note");
  const b = await openCodeSession(INSTALL, "note");
  assert.equal(a, b, "同一安装同一调用类必须稳定（官方：对路由与缓存有利）");
  assert.notEqual(a, await openCodeSession(INSTALL, "polish"), "换类要换 session");
  assert.notEqual(a, await openCodeSession("f".repeat(32), "note"), "换安装要换 session");
  assert.equal(await openCodeSession("", "note"), "classlive-note-anon");
  assert.equal(await openCodeSession("not-a-uuid", "note"), "classlive-note-anon", "形状不合当 anon");
  assert.equal(await openCodeSession(INSTALL, ""), `classlive-legacy-${a.slice(-8)}`);
  assert.match(a, /^classlive-note-[0-9a-f]{8}$/, "8 位 = sha256 前 4 字节");
});

test("4c OPENCODE_GO_MODEL 可覆盖模型 id", async () => {
  const h = harness({ env: { OPENCODE_GO_KEY: "k", CHAT_ROUTES: "note=opencode", OPENCODE_GO_MODEL: "deepseek-v4.1-flash-x" } });
  try {
    const res = await h.call(BODY());
    await res.text();
    await h.settle();
    assert.equal(h.calls.oc[0].body.model, "deepseek-v4.1-flash-x");
  } finally { h.restore(); }
});

// ---------- 5. 退回 DeepSeek ----------
test("5 opencode 402 -> 退回 DeepSeek，记 opencode:402>deepseek:200，冷却期内不再白试", async () => {
  const h = harness({
    oc: () => J(402, { error: "Insufficient Balance" }),
    env: { OPENCODE_GO_KEY: "oc-key", CHAT_ROUTES: "note=opencode" },
  });
  try {
    const r1 = await h.call(BODY());
    assert.equal(r1.status, 200);
    assert.equal((await r1.json()).choices[0].message.content, "ds", "退回 DeepSeek 的内容");
    await h.settle();
    assert.equal(h.calls.oc.length, 1);
    assert.equal(h.calls.deepseek.length, 1);
    assert.equal(h.calls.deepseek[0].body.model, "deepseek-flash", "退回必须用原 body");
    assert.equal(h.calls.ae[0].blobs[3], "opencode:402>deepseek:200");

    const r2 = await h.call(BODY());
    await r2.text();
    await h.settle();
    assert.equal(h.calls.oc.length, 1, "402 之后 10 分钟冷却，不许再打 opencode");
    assert.equal(h.calls.ae[1].blobs[3], "deepseek:200");
  } finally { h.restore(); }
});

test("5b opencode 200 但 JSON 坏 -> 退回 DeepSeek：opencode:200:bad>deepseek:200", async () => {
  const h = harness({
    oc: () => new Response("<html>nope</html>", { status: 200, headers: { "content-type": "text/html" } }),
    env: { OPENCODE_GO_KEY: "k", CHAT_ROUTES: "note=opencode" },
  });
  try {
    const res = await h.call(BODY());
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.deepseek.length, 1);
    assert.equal(h.calls.ae[0].blobs[3], "opencode:200:bad>deepseek:200");
  } finally { h.restore(); }
});

test("5c opencode 200 但 content 空（要 json_object）-> 当失败", async () => {
  const h = harness({
    oc: () => J(200, { choices: [{ message: { role: "assistant", content: "" }, finish_reason: "stop" }] }),
    env: { OPENCODE_GO_KEY: "k", CHAT_ROUTES: "note=opencode" },
  });
  try {
    const res = await h.call(BODY({ response_format: { type: "json_object" } }));
    await res.text();
    await h.settle();
    assert.equal(h.calls.ae[0].blobs[3], "opencode:200:nocontent>deepseek:200");
  } finally { h.restore(); }
});

test("5d opencode 响应头超时 -> 退回 DeepSeek：opencode:timeout>deepseek:200", async () => {
  const h = harness({
    oc: (body, n, signal) => new Promise((resolve, reject) => {
      signal?.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })), { once: true });
    }),
    env: { OPENCODE_GO_KEY: "k", CHAT_ROUTES: "note=opencode", OPENCODE_HEADER_MS: "30" },
  });
  try {
    const res = await h.call(BODY());
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.deepseek.length, 1);
    assert.equal(h.calls.ae[0].blobs[3], "opencode:timeout>deepseek:200");
  } finally { h.restore(); }
});

// ---------- 6. key 缺失 ----------
test("6 没配 OPENCODE_GO_KEY -> 直接 DeepSeek，一次都不打 opencode", async () => {
  const h = harness({ env: { CHAT_ROUTES: "note=opencode" } });
  try {
    const res = await h.call(BODY());
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.oc.length, 0);
    assert.equal(h.calls.deepseek.length, 1);
    assert.equal(h.calls.ae[0].blobs[3], "deepseek:200");
  } finally { h.restore(); }
});

test("6b 表里写 opencode，但走的是 cc 那类（note 默认 cc）-> 只打 cc", async () => {
  const h = harness({ env: { OPENCODE_GO_KEY: "k", CMD_CHAT_KEY: "cc-key", CHAT_ROUTES: "atom=opencode" } });
  try {
    const res = await h.call(BODY());
    await res.text();
    await h.settle();
    assert.equal(h.calls.cc.length, 1);
    assert.equal(h.calls.oc.length, 0);
  } finally { h.restore(); }
});

// ---------- 7. 流式永远不走 cc / opencode ----------
test("7 流式 + 表里写 opencode -> 不走 opencode（DeepSeek 直连）", async () => {
  const h = harness({ env: { OPENCODE_GO_KEY: "k", CHAT_ROUTES: "*=opencode" } });
  try {
    const res = await h.call(BODY({ stream: true, max_tokens: 220 }), { cls: "" });
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.oc.length, 0, "流式（实时翻译 / 草稿）等不起，绝不走 opencode");
    assert.equal(h.calls.deepseek.length, 1);
  } finally { h.restore(); }
});

test("7c 流式根本不读路由表（别为实时首字去问 DO）", async () => {
  const h = harness({ env: { OPENCODE_GO_KEY: "k", CHAT_ROUTES: "*=opencode" } });
  try {
    const res = await h.call(BODY({ stream: true, max_tokens: 220 }));
    await res.text();
    await h.settle();
    assert.equal(h.calls.routesGet, 0, "流式走不到 cc / opencode，读表纯属白花一个来回");
  } finally { h.restore(); }
});

test("7d 非流式读表按 isolate 缓存 60 s（两次调用只问一次 DO）", async () => {
  const h = harness({ env: { CMD_CHAT_KEY: "cc-key" } });
  try {
    const a = await h.call(BODY());
    await a.text();
    const b = await h.call(BODY());
    await b.text();
    await h.settle();
    assert.equal(h.calls.routesGet, 1, "第二次必须吃缓存");
    assert.equal(h.calls.cc.length, 2);
  } finally { h.restore(); }
});

test("7b 流式 + 表里写 cc -> 也不走 cc", async () => {
  const h = harness({ env: { CMD_CHAT_KEY: "cc-key", CHAT_ROUTES: "*=cc" } });
  try {
    const res = await h.call(BODY({ stream: true, max_tokens: 220 }), { cls: "" });
    await res.text();
    await h.settle();
    assert.equal(h.calls.cc.length, 0);
    assert.equal(h.calls.deepseek.length, 1);
  } finally { h.restore(); }
});

// ---------- 8. admin：路由表存取 ----------
test("8 admin routes/set 拒绝非法上游，整批不写", async () => {
  const h = harness();
  try {
    const bad = await h.admin("routes/set", { routes: { note: "gemini" } });
    assert.ok(bad.json.error, "非法值必须报错");
    assert.match(bad.json.error, /deepseek \/ cc \/ opencode/);
    assert.deepEqual((await h.core.routesGet()).routes, {}, "拒掉之后一个字也没写");
    const badKey = await h.admin("routes/set", { routes: { "Not A Class": "cc" } });
    assert.ok(badKey.json.error, "调用类名形状不合也要拒");
    const arr = await h.admin("routes/set", { routes: [] });
    assert.ok(arr.json.error, "数组不是表");
  } finally { h.restore(); }
});

test("9 admin routes/set 写入 + routes/get 回三层全貌，DO 表立即生效", async () => {
  const h = harness({ env: { CHAT_ROUTES: "chapter=cc", OPENCODE_GO_KEY: "oc-key" } });
  try {
    const set = await h.admin("routes/set", { routes: { note: "opencode", " * ": "CC" } });
    assert.equal(set.status, 200);
    assert.deepEqual(set.json.routes, { note: "opencode", "*": "cc" }, "值归一 + 键修剪");

    const snap = await h.admin("routes/get");
    assert.deepEqual(snap.json.stored, { note: "opencode", "*": "cc" });
    assert.deepEqual(snap.json.env, { chapter: "cc" });
    assert.deepEqual(snap.json.defaults, { note: "cc", polish: "cc", prep: "cc" });
    assert.equal(snap.json.effective.note, "opencode", "DO 表赢");
    assert.equal(snap.json.effective.chapter, "cc", "env 次之");
    assert.deepEqual(snap.json.upstreams, ["deepseek", "cc", "opencode"]);

    // 写完之后选路就变了（同一个 isolate 里 set 会作废缓存）
    const r = await h.call(BODY(), { cls: "note" });
    await r.text();
    await h.settle();
    assert.equal(h.calls.oc.length, 1, "刚写进 DO 的表立刻生效");
  } finally { h.restore(); }
});

test("9b admin routes/set {} = 清空回默认；鉴权缺 token 401", async () => {
  const h = harness({ stored: { note: "opencode" }, env: { CMD_CHAT_KEY: "cc-key" } });
  try {
    assert.deepEqual((await h.admin("routes/get")).json.stored, { note: "opencode" });
    const clr = await h.admin("routes/set", { routes: {} });
    assert.deepEqual(clr.json.routes, {});
    assert.deepEqual((await h.core.routesGet()).routes, {}, "storage 里那格也删了");
    const snap = await h.admin("routes/get");
    assert.equal(snap.json.effective.note, "cc", "回默认表");

    const res = await worker.fetch(new Request("https://asr.bldcam.page/v1/admin/routes/get", {
      method: "POST", headers: { "content-type": "application/json" }, body: "{}",
    }), {
      ADMIN_TOKEN: "adm",
      PASSES: { idFromName: (n) => n, get: () => ({ fetch: async () => J(200, { routes: {} }) }) },
    }, { waitUntil: () => {} });
    assert.equal(res.status, 401, "没带 ADMIN_TOKEN 一律 401");
  } finally { h.restore(); }
});

// ---------- 10. 表是坏的 / 读表挂了，都不能把调用打没 ----------
test("10 读路由表挂了 -> 用 env + 默认兜住（默认 note 仍走 cc）", async () => {
  const h = harness({ env: { CMD_CHAT_KEY: "cc-key" }, routesFail: true });
  try {
    const res = await h.call(BODY());
    assert.equal(res.status, 200, "DO 挂了照常服务");
    assert.equal((await res.json()).choices[0].message.content, "cc");
    await h.settle();
    assert.equal(h.calls.cc.length, 1);
    assert.equal(h.calls.ae[0].blobs[3], "cmd:200");
  } finally { h.restore(); }
});

test("10b DO 回的表形状坏 -> 当没有（legal 值才认；坏值 → deepseek），不抛", async () => {
  const h = harness({ routesRaw: { routes: { note: 123, "*-bad": "opencode" } }, env: { CMD_CHAT_KEY: "cc-key" } });
  try {
    const res = await h.call(BODY());
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.cc.length, 0, "note=123 是坏值 -> deepseek（不能猜成 cc）");
    assert.equal(h.calls.deepseek.length, 1);
  } finally { h.restore(); }
});

test("10c 调用类里带大写 / 空格也认（客户端头不保证干净）", async () => {
  const h = harness({ env: { CHAT_ROUTES: "atOm=opencode", OPENCODE_GO_KEY: "k" } });
  try {
    const res = await h.call(BODY(), { cls: "  ATOM " });
    await res.text();
    await h.settle();
    assert.equal(h.calls.oc.length, 1);
  } finally { h.restore(); }
});


// ---------- 11. 单请求指定上游：只给测试码（A/B 不动全局表）----------
const TEST1 = "cl-test1-0123456789abcdef";

test("11 test1 带 X-ClassLive-Route: opencode -> 走 opencode（全局表没动）", async () => {
  const h = harness({ env: { OPENCODE_GO_KEY: "oc-key" } });
  try {
    const res = await h.call(BODY(), { cls: "chapter", code: TEST1, route: "opencode" });
    assert.equal(res.status, 200);
    await h.settle();
    assert.equal(h.calls.oc.length, 1);
    assert.equal(h.calls.deepseek.length, 0);
  } finally { h.restore(); }
});

test("11b 普通用户带同一个头 -> 不认，照表走（chapter 默认 deepseek）", async () => {
  const h = harness({ env: { OPENCODE_GO_KEY: "oc-key" } });
  try {
    const res = await h.call(BODY(), { cls: "chapter", route: "opencode" });
    assert.equal(res.status, 200);
    await h.settle();
    assert.equal(h.calls.oc.length, 0, "真用户不能自己换上游");
    assert.equal(h.calls.deepseek.length, 1);
  } finally { h.restore(); }
});

test("11c test1 指定 cc 但没配 cc key / 指定未知值 -> 回 deepseek（闸照过）", async () => {
  const h = harness({ env: { OPENCODE_GO_KEY: "oc-key" } });
  try {
    await h.call(BODY(), { cls: "chapter", code: TEST1, route: "cc" });
    await h.call(BODY(), { cls: "chapter", code: TEST1, route: "openai" });
    await h.settle();
    assert.equal(h.calls.cc.length, 0);
    assert.equal(h.calls.oc.length, 0);
    assert.equal(h.calls.deepseek.length, 2);
  } finally { h.restore(); }
});

test("11d test1 + 流式 + 指定 opencode -> 不走 opencode", async () => {
  const h = harness({ env: { OPENCODE_GO_KEY: "oc-key" } });
  try {
    const res = await h.call(BODY({ stream: true, max_tokens: 220 }), { cls: "translate", code: TEST1, route: "opencode", country: "CN" });
    assert.equal(res.status, 200, "流式 + 指定 opencode 不能把请求打挂");
    await h.settle();
    assert.equal(h.calls.oc.length, 0);
    assert.equal(h.calls.deepseek.length, 1, "真的落到 DeepSeek 直连");
  } finally { h.restore(); }
});

test("11e opencode：客户端没写 thinking -> 补 disabled（Go 默认开推理会吃光 max_tokens）", async () => {
  const h = harness({ env: { OPENCODE_GO_KEY: "oc-key", CHAT_ROUTES: "*=opencode" } });
  try {
    const b = BODY(); delete b.thinking;
    await h.call(b);
    await h.settle();
    assert.deepEqual(h.calls.oc[0].body.thinking, { type: "disabled" });
  } finally { h.restore(); }
});

test("12 admin routes/get：DO 读失败 -> 502，绝不装成空表（cl_admin 读改写会抹掉整张表）", async () => {
  const h = harness({ routesFail: true });
  try {
    const r = await h.admin("routes/get");
    assert.equal(r.status, 502);
    assert.ok(r.json.error);
    assert.equal("stored" in r.json, false);
  } finally { h.restore(); }
});
