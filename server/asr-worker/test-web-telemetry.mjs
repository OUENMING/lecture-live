// W20 判据：`POST /v1/web/telemetry`（遥测事件）与 `PUT /v1/web/audio/:session_id/:n`（录音分段）落 R2，
// 加 `/v1/admin/web/{list,get}` 两个只读出口。直接 import 真源码；Passes 用真 PassCore + 内存 storage，
// R2 是内存替身（带 cursor 分页语义），上游一条都不碰。
//
//   cd server/asr-worker && node --test test-web-telemetry.mjs
//
// 钉住什么：
//   1. 204 + R2 键形状（日期 / 码名 / 会话号 / 毫秒-4hex）、NDJSON 行数与服务端字段
//   2. 体积闸：64 KiB / 4 MiB 超一个字节 413，且**一个对象都没写**；没 content-length 也按实际字节判
//   3. events 不是数组 400；无码 401；Origin 不对 403；非 web 客户端 403
//   4. 段号越界 400、会话号形状不对 404、content-type 不认识 415；重传同一段覆盖不新增
//   5. 开关 off：204 + `x-cl-telemetry: off` + 不写 R2（总闸也关录音）
//   6. 管理端前缀白名单：`telemetry/` `audio/` 之外一律 400；list 回 shape、get 回原文
//   7. CORS：预检放行这两条，`access-control-expose-headers` 只出现在 /v1/web/*
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "./src/handler.js";
import { PassCore } from "./src/passes-core.js";

const ORIGIN = "https://web.example.test";
const INSTALL = "0123456789abcdef0123456789abcdef";
const SID = "s_0123456789ab";
const T0 = Date.UTC(2026, 10, 20, 12);

function memStorage(seed = {}) {
  const m = new Map(Object.entries(seed));
  return {
    get: async (k) => (m.has(k) ? structuredClone(m.get(k)) : undefined),
    put: async (k, v) => { m.set(k, structuredClone(v)); },
    delete: async (k) => { for (const kk of [].concat(k)) m.delete(kk); },
    list: async ({ prefix = "" } = {}) => new Map([...m].filter(([k]) => k.startsWith(prefix))),
  };
}

// 内存 R2：put / get / list（含 cursor + truncated 语义，同真实 R2 的形状）
function memR2() {
  const m = new Map();
  return {
    map: m,
    put: async (k, v) => {
      const body = v instanceof Blob ? v : new Blob([v]);
      m.set(k, { body, size: body.size });
    },
    get: async (k) => (m.has(k) ? { body: m.get(k).body, httpMetadata: { contentType: "application/x-ndjson" } } : null),
    list: async ({ prefix = "", limit = 1000, cursor } = {}) => {
      const all = [...m.keys()].filter((k) => k.startsWith(prefix)).sort();
      const from = cursor ? all.indexOf(cursor) + 1 : 0;
      const page = all.slice(from, from + limit);
      const truncated = from + limit < all.length;
      return {
        objects: page.map((key) => ({ key, size: m.get(key).size, uploaded: new Date(T0) })),
        truncated, cursor: truncated ? page.at(-1) : undefined,
      };
    },
  };
}

const doStub = (core) => ({
  idFromName: (n) => n,
  get: () => ({ fetch: async (u, init) => core.handle(new Request(u, init)) }),
});

function harness({ env: extraEnv = {} } = {}) {
  const pass = new PassCore(memStorage(), () => T0);
  const r2 = memR2();
  const env = {
    CLIENT_TOKEN: "tok-public", ADMIN_TOKEN: "adm", WEB_ORIGIN: ORIGIN,
    PER_INSTALL: { limit: async () => ({ success: true }) },
    PER_IP: { limit: async () => ({ success: true }) },
    PASSES: doStub(pass), WEB_DATA: r2,
    ...extraEnv,
  };
  const ctx = { waitUntil: () => {} };
  const send = (req) => worker.fetch(req, env, ctx);
  const req = (path, { method = "POST", headers = {}, body } = {}) => {
    const h = {};
    for (const [k, v] of Object.entries(headers)) if (v !== undefined && v !== null) h[k] = v;
    const r = new Request(`https://asr.bldcam.page${path}`, {
      method, headers: h, body: method === "GET" ? undefined : body, duplex: "half",
    });
    r.cf = { country: "IE", colo: "DUB" };   // Worker 运行时注入；Node 下自己挂上去
    return r;
  };
  const addCode = async (name = "amy") =>
    (await (await send(req("/v1/admin/add", {
      headers: { authorization: "Bearer adm", "content-type": "application/json" },
      body: JSON.stringify({ name }),
    }))).json()).code;
  const auth = (code, extra = {}) => ({
    authorization: code ? `Bearer ${code}` : undefined,
    "x-classlive-install": INSTALL, origin: ORIGIN, "x-classlive-client": "web", ...extra,
  });
  const telemetry = (code, body, extra = {}) => req("/v1/web/telemetry", {
    headers: auth(code, { "content-type": "application/json", ...extra }),
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
  const audio = (code, path, body, extra = {}) => req(path, {
    method: "PUT",
    headers: auth(code, { "content-type": "audio/webm;codecs=opus", ...extra }), body,
  });
  const admin = (name, body = {}) => req(`/v1/admin/web/${name}`, {
    headers: { authorization: "Bearer adm", "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return { env, r2, req, send, addCode, telemetry, audio, admin };
}

// 无 content-length 的流式 body：逼 handler 走 readCapped 的实际字节计数（等价于客户端谎报长度）
function streamOf(bytes) {
  return new ReadableStream({
    start(c) { c.enqueue(new Uint8Array(bytes)); c.close(); },
  });
}

const KEY_TELEMETRY = /^telemetry\/\d{4}-\d{2}-\d{2}\/amy\/(s_[0-9a-f]{12}|nosession)\/\d{13}-[0-9a-f]{4}\.ndjson$/;

// ---------- 1. 正常路径 ----------
test("T1 遥测：204 + 键形状 + 每行都带服务端字段（含 cf 的 country/colo）", async () => {
  const h = harness();
  const code = await h.addCode();
  const res = await h.send(h.telemetry(code, {
    session_id: SID, events: [{ t: "asr.timeout", ms: 1200 }, { t: "r2.fail", rx_at: 1, name: "x" }],
  }));
  assert.equal(res.status, 204);
  const keys = [...h.r2.map.keys()];
  assert.equal(keys.length, 1);
  assert.match(keys[0], KEY_TELEMETRY);
  assert.ok(keys[0].includes(`/${SID}/`), "session_id 进键");
  const rows = (await h.r2.map.get(keys[0]).body.text()).trim().split("\n").map((l) => JSON.parse(l));
  assert.equal(rows.length, 2);
  for (const r of rows) {
    assert.equal(r.name, "amy");
    assert.equal(r.country, "IE");
    assert.equal(r.colo, "DUB");
    assert.equal(typeof r.rx_at, "number");
    assert.ok(r.rx_at > 1e12);
  }
  assert.equal(rows[1].rx_at, rows[0].rx_at, "客户端伪造的 rx_at 被服务端字段覆盖");
  assert.equal(rows[1].name, "amy");
  assert.equal(rows[0].t, "asr.timeout");
});

test("T2 会话号缺失 / 形状不对 -> nosession 段；空 events 合法", async () => {
  const h = harness();
  const code = await h.addCode();
  assert.equal((await h.send(h.telemetry(code, { session_id: "../../etc", events: [] }))).status, 204);
  assert.equal((await h.send(h.telemetry(code, { events: [{ t: "x" }] }))).status, 204);
  const keys = [...h.r2.map.keys()];
  assert.equal(keys.length, 2);
  for (const k of keys) assert.ok(k.includes("/nosession/"), k);
});

// ---------- 2. 体积 / 形状闸 ----------
test("T3 遥测 64 KiB+1 -> 413 且没写 R2（含 content-length 撒谎的流）", async () => {
  const h = harness();
  const code = await h.addCode();
  const big = "x".repeat(64 * 1024 + 1);
  const res = await h.send(h.telemetry(code, { events: [big] }));
  assert.equal(res.status, 413);
  assert.equal(h.r2.map.size, 0);

  // 没有 content-length：只靠 readCapped 的实际字节数（等价于客户端谎报长度）
  const streamed = await h.send(h.req("/v1/web/telemetry", {
    headers: { authorization: `Bearer ${code}`, "x-classlive-install": INSTALL, origin: ORIGIN,
      "x-classlive-client": "web", "content-type": "application/json" },
    body: streamOf(64 * 1024 + 1),
  }));
  assert.equal(streamed.status, 413);
  assert.equal(h.r2.map.size, 0);
});

test("T4 events 不是数组 -> 400；坏 JSON -> 400", async () => {
  const h = harness();
  const code = await h.addCode();
  assert.equal((await h.send(h.telemetry(code, { events: { a: 1 } }))).status, 400);
  assert.equal((await h.send(h.telemetry(code, { events: "x" }))).status, 400);
  assert.equal((await h.send(h.telemetry(code, { session_id: SID }))).status, 400);
  assert.equal((await h.send(h.telemetry(code, "{oops"))).status, 400);
  assert.equal(h.r2.map.size, 0);
});

// ---------- 3. 鉴权 / 来源 / 客户端 ----------
test("T5 无码 401 / Origin 不对 403 / 非 web 客户端 403 / 未注册的码 403", async () => {
  const h = harness();
  const code = await h.addCode();
  const body = { events: [{ t: "x" }] };
  assert.equal((await h.send(h.telemetry(null, body))).status, 401);
  assert.equal((await h.send(h.telemetry(code, body, { origin: "https://evil.test" }))).status, 403);
  assert.equal((await h.send(h.telemetry(code, body, { "x-classlive-client": undefined }))).status, 403);
  assert.equal((await h.send(h.telemetry("cl-zzz-0123456789abcdef", body))).status, 403);
  assert.equal(h.r2.map.size, 0);
});

// ---------- 4. 录音分段 ----------
test("T6 录音：204 + 键 audio/amy/<sid>/0007.webm；重传同一段覆盖不新增", async () => {
  const h = harness();
  const code = await h.addCode();
  const path = `/v1/web/audio/${SID}/7`;
  assert.equal((await h.send(h.audio(code, path, new Uint8Array([1, 2, 3])))).status, 204);
  assert.equal((await h.send(h.audio(code, path, new Uint8Array([4, 5])))).status, 204);
  const keys = [...h.r2.map.keys()];
  assert.deepEqual(keys, [`audio/amy/${SID}/0007.webm`]);
  assert.equal(h.r2.map.get(keys[0]).size, 2, "同 key 覆盖");
});

test("T7 录音：本机会话 id 形状也收；换 content-type 换扩展名", async () => {
  const h = harness();
  const code = await h.addCode();
  const local = "web-20261220-101500-2ded25";   // 后缀是 uuid 前 6 位 hex（真实形状，带字母）
  assert.equal((await h.send(h.audio(code, `/v1/web/audio/${local}/0`, new Uint8Array([1])))).status, 204);
  assert.equal((await h.send(h.audio(code, `/v1/web/audio/${local}/9999`, new Uint8Array([1]),
    { "content-type": "audio/ogg" }))).status, 204);
  assert.deepEqual([...h.r2.map.keys()].sort(),
    [`audio/amy/${local}/0000.webm`, `audio/amy/${local}/9999.ogg`]);
});

test("T8 录音闸：4 MiB+1 -> 413 不写；越界段号 400；坏会话号 404；未知 content-type 415；空 body 400", async () => {
  const h = harness();
  const code = await h.addCode();
  // 走流：没有 content-length，只靠 readCapped 计数
  const big = await h.send(h.req(`/v1/web/audio/${SID}/1`, {
    method: "PUT",
    headers: { authorization: `Bearer ${code}`, "x-classlive-install": INSTALL, origin: ORIGIN,
      "x-classlive-client": "web", "content-type": "audio/webm" },
    body: streamOf(4 * 1024 * 1024 + 1),
  }));
  assert.equal(big.status, 413);
  assert.equal(h.r2.map.size, 0);

  assert.equal((await h.send(h.audio(code, `/v1/web/audio/${SID}/10000`, new Uint8Array([1])))).status, 400);
  assert.equal((await h.send(h.audio(code, `/v1/web/audio/${SID}/x`, new Uint8Array([1])))).status, 400);
  assert.equal((await h.send(h.audio(code, "/v1/web/audio/s_zzz/1", new Uint8Array([1])))).status, 404);
  assert.equal((await h.send(h.audio(code, `/v1/web/audio/${SID}/1`, new Uint8Array([1]),
    { "content-type": "text/plain" }))).status, 415);
  assert.equal((await h.send(h.audio(code, `/v1/web/audio/${SID}/1`, new Uint8Array([])))).status, 400);
  assert.equal(h.r2.map.size, 0);
});

// ---------- 5. 开关 ----------
test("T9 WEB_TELEMETRY=off：两条都 204 + x-cl-telemetry: off + 不写 R2", async () => {
  const h = harness({ env: { WEB_TELEMETRY: " off " } });
  const code = await h.addCode();
  const t = await h.send(h.telemetry(code, { events: [{ t: "x" }] }));
  assert.equal(t.status, 204);
  assert.equal(t.headers.get("x-cl-telemetry"), "off");
  const a = await h.send(h.audio(code, `/v1/web/audio/${SID}/1`, new Uint8Array([1])));
  assert.equal(a.status, 204);
  assert.equal(a.headers.get("x-cl-telemetry"), "off");
  assert.equal(h.r2.map.size, 0);
});

test("T10 WEB_AUDIO_UPLOAD=off：只关录音，遥测照收", async () => {
  const h = harness({ env: { WEB_AUDIO_UPLOAD: "off" } });
  const code = await h.addCode();
  assert.equal((await h.send(h.audio(code, `/v1/web/audio/${SID}/1`, new Uint8Array([1]))))
    .headers.get("x-cl-telemetry"), "off");
  const t = await h.send(h.telemetry(code, { events: [{ t: "x" }] }));
  assert.equal(t.headers.get("x-cl-telemetry"), null);
  assert.equal(h.r2.map.size, 1);
  assert.match([...h.r2.map.keys()][0], KEY_TELEMETRY);
});

// ---------- 6. 管理端 ----------
test("T11 admin：前缀白名单（只 telemetry/ audio/）；list 回 key/size/uploaded + cursor；get 回原文", async () => {
  const h = harness();
  const code = await h.addCode();
  await h.send(h.telemetry(code, { session_id: SID, events: [{ t: "x" }] }));
  const key = [...h.r2.map.keys()][0];

  assert.equal((await h.send(h.admin("list", { prefix: "web/" }))).status, 400);
  assert.equal((await h.send(h.admin("list", { prefix: "" }))).status, 400);
  assert.equal((await h.send(h.admin("get", { key: "web/x/y" }))).status, 400);
  assert.equal((await h.send(h.admin("get", { key: "telemetry/nope.ndjson" }))).status, 404);

  const list = await (await h.send(h.admin("list", { prefix: "telemetry/" }))).json();
  assert.equal(list.objects.length, 1);
  assert.equal(list.objects[0].key, key);
  assert.equal(typeof list.objects[0].size, "number");
  assert.equal(list.objects[0].uploaded, new Date(T0).toISOString());
  assert.equal(list.cursor, null);

  const got = await h.send(h.admin("get", { key }));
  assert.equal(got.status, 200);
  const text = await got.text();
  assert.equal(JSON.parse(text.trim()).t, "x");

  // 没令牌 -> 401（不是 400），沿用 /v1/admin/* 那套失败关闭
  const noAuth = await h.send(h.req("/v1/admin/web/list", {
    headers: { "content-type": "application/json" }, body: JSON.stringify({ prefix: "telemetry/" }),
  }));
  assert.equal(noAuth.status, 401);
});

// ---------- 7. CORS ----------
test("T12 预检放行这两条路径；expose-headers 只对 /v1/web/* 出现", async () => {
  const h = harness();
  for (const [path, method] of [["/v1/web/telemetry", "POST"], [`/v1/web/audio/${SID}/1`, "PUT"]]) {
    const res = await h.send(h.req(path, { method: "OPTIONS", headers: { origin: ORIGIN } }));
    assert.equal(res.status, 204, path);
    assert.match(res.headers.get("access-control-allow-methods"), /\bPOST\b/);
    assert.match(res.headers.get("access-control-allow-methods"), /\bPUT\b/);
  }
  const code = await h.addCode();
  const web = await h.send(h.telemetry(code, { events: [] }));
  assert.equal(web.headers.get("access-control-expose-headers"), "x-cl-telemetry");
  assert.equal(web.headers.get("access-control-allow-origin"), ORIGIN);
  // 老代付路径不带 expose（只给 /v1/web/*）
  const chat = await h.send(h.req("/v1/chat/completions", {
    headers: { authorization: `Bearer ${code}`, "x-classlive-install": INSTALL, origin: ORIGIN,
      "content-type": "application/json" },
    body: JSON.stringify({ model: "m", messages: [], max_tokens: 1 }),
  }));
  assert.equal(chat.headers.get("access-control-expose-headers"), null);
});

test("OCR D：purgeTestData 只删这个码名的遥测与录音，别人的不动", async () => {
  const { purgeTestData } = await import("./src/web-telemetry.js");
  const m = new Map(["telemetry/2026-11-20/amy/s1/a.ndjson", "telemetry/2026-11-19/amy/s0/b.ndjson",
    "telemetry/2026-11-20/amyx/s2/c.ndjson", "telemetry/2026-11-20/bob/s3/d.ndjson",
    "audio/amy/s1/0000.webm", "audio/amyx/s2/0000.webm", "web/abc/sessions/x/lines-1.json"].map((k) => [k, 1]));
  const env = { WEB_DATA: {
    list: async ({ prefix }) => ({ objects: [...m.keys()].filter((k) => k.startsWith(prefix)).map((key) => ({ key })), truncated: false }),
    delete: async (keys) => { for (const k of [].concat(keys)) m.delete(k); },
  } };
  assert.equal(await purgeTestData(env, "amy"), 3);
  assert.deepEqual([...m.keys()].sort(), ["audio/amyx/s2/0000.webm", "telemetry/2026-11-20/amyx/s2/c.ndjson",
    "telemetry/2026-11-20/bob/s3/d.ndjson", "web/abc/sessions/x/lines-1.json"]);
});
