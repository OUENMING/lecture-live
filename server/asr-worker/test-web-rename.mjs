// W18 判据：课后改课名 `PATCH /v1/web/session/:id`（docs/API-web-v0-20261117.md §3.8）。
// 直接 import 真源码；DO 用真 WebSessions 类 + 内存 storage 与内存 R2，上游一条都不碰。
//
//   cd server/asr-worker && node --test test-web-rename.mjs
//
// 钉住什么：
//   1. 改名成功后 GET 读回新课名、list 里也是新名（账本真的写了，不是只回声）
//   2. 别人的身份改 → 404；不存在 → 404；已删 → 410（同 GET）
//   3. 空课名 / 超长 → 400 bad_body；校验与开课同一套（normalizeCourse）
//   4. 幂等：同名再改 200；进行中与已下课的会话都能改
//   5. 预检带 PATCH；方法 / 身份不对不破坏老路由
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "./src/handler.js";
import { PassCore } from "./src/passes-core.js";
import { QuotaCore } from "./src/quota-core.js";
import { WebSessions } from "./src/web-session.js";
import { codeHashHex, normalizeCourse, renameCourse } from "./src/web-session-core.js";

const ORIGIN = "https://web.example.test";
const INSTALL = "0123456789abcdef0123456789abcdef";
const T0 = Date.UTC(2026, 10, 19, 9);

function memStorage() {
  const m = new Map();
  return {
    map: m,
    get: async (k) => (m.has(k) ? structuredClone(m.get(k)) : undefined),
    put: async (k, v) => { m.set(k, structuredClone(v)); },
    delete: async (k) => { for (const kk of [].concat(k)) m.delete(kk); },
    list: async ({ prefix = "" } = {}) => new Map([...m].filter(([k]) => k.startsWith(prefix))),
  };
}

function memR2() {
  const m = new Map();
  return {
    map: m,
    put: async (k, v) => { m.set(k, String(v)); },
    delete: async (keys) => { for (const k of [].concat(keys)) m.delete(k); },
    list: async ({ prefix = "", limit = 1000, cursor } = {}) => {
      const all = [...m.keys()].filter((k) => k.startsWith(prefix)).sort();
      const from = cursor ? all.indexOf(cursor) + 1 : 0;
      const page = all.slice(from, from + limit);
      const truncated = from + limit < all.length;
      return { objects: page.map((key) => ({ key })), truncated, cursor: truncated ? page.at(-1) : undefined };
    },
  };
}

function webDo(env) {
  const instances = new Map();
  return {
    idFromName: (n) => n,
    get: (n) => {
      if (!instances.has(n)) {
        const ctx = { storage: memStorage() };
        instances.set(n, new WebSessions(ctx, env));
      }
      return instances.get(n);
    },
    instances,
  };
}

const doStub = (core) => ({
  idFromName: (n) => n,
  get: () => ({ fetch: async (u, init) => core.handle(new Request(u, init)) }),
});

function harness() {
  const now = T0;
  const pass = new PassCore(memStorage(), () => now);
  const quota = new QuotaCore(memStorage(), () => now);
  const env = {
    CLIENT_TOKEN: "tok-public", ADMIN_TOKEN: "adm", WEB_ORIGIN: ORIGIN,
    PER_INSTALL: { limit: async () => ({ success: true }) },
    PER_IP: { limit: async () => ({ success: true }) },
    QUOTA: doStub(quota), PASSES: doStub(pass),
    WEB_DATA: memR2(),
  };
  env.WEB_SESSIONS = webDo(env);
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p) };
  const send = (req) => worker.fetch(req, env, ctx);
  const req = (path, { method = "POST", headers = {}, body } = {}) => {
    const h = {};
    for (const [k, v] of Object.entries(headers)) if (v !== undefined && v !== null) h[k] = v;
    return new Request(`https://asr.bldcam.page${path}`, {
      method, headers: h, body: method === "GET" || method === "OPTIONS" ? undefined : body,
    });
  };
  const web = (path, { code, method = "POST", body, install = INSTALL, origin = ORIGIN, client = "web" } = {}) =>
    req(path, {
      method,
      headers: {
        authorization: code ? `Bearer ${code}` : undefined,
        "x-classlive-install": install, origin, "x-classlive-client": client,
        "content-type": "application/json",
      },
      body: body === undefined ? undefined : (typeof body === "string" ? body : JSON.stringify(body)),
    });
  const addCode = async (name = "amy") =>
    (await (await send(req("/v1/admin/add", {
      headers: { authorization: "Bearer adm", "content-type": "application/json" },
      body: JSON.stringify({ name }),
    }))).json()).code;
  const settle = () => Promise.all(pending.splice(0));
  return { env, pass, req, send, web, addCode, settle };
}

const L = (seq, extra = {}) => ({ seq, t: seq * 2.5, en: `en ${seq}`, zh: `中 ${seq}`, state: "final", src: "asr", ...extra });

// ---------- 1. 纯逻辑 ----------
test("R1 纯逻辑 renameCourse：与开课同一套 normalizeCourse；同名仍是新对象；deleted / 没 meta 各自报错", () => {
  // 判据必须与 POST /v1/web/session 完全一致：直接用 normalizeCourse 当期望值，不另抄一份阈值。
  for (const raw of [" ECON10740 ", "微观经济", "CS-101", "Econ 10740 (L03)", "x".repeat(80), "a\u0000b\nc"]) {
    const r = renameCourse({ session_id: "s_000000000001", course: "旧", state: "live" }, raw);
    assert.equal(r.error, undefined, raw);
    assert.equal(r.meta.course, normalizeCourse(raw), raw);
  }
  for (const raw of ["", "   ", "x".repeat(81), null, undefined]) {
    assert.deepEqual(renameCourse({ session_id: "s_000000000001", course: "旧", state: "ended" }, raw),
      { error: "bad_body", detail: "course" }, String(raw));
  }
  const input = { session_id: "s_000000000001", course: "同一", state: "ended" };
  const same = renameCourse(input, "同一");
  assert.equal(same.meta.course, "同一");
  assert.notEqual(same.meta, input, "返回新对象，入参不被就地改");
  assert.equal(input.course, "同一");
  assert.deepEqual(renameCourse(null, "X"), { error: "not_found" });
  assert.deepEqual(renameCourse({ state: "deleted" }, "X"), { error: "session_deleted" });
});

// ---------- 2. HTTP：改名 + 读回 ----------
test("R2 改名：200 {session_id, course}；GET 读回新课名；list 里也是新名", async () => {
  const h = harness();
  const code = await h.addCode();
  const { session_id } = await (await h.send(h.web("/v1/web/session", { code, body: { course: "未命名" } }))).json();

  const res = await h.send(h.web(`/v1/web/session/${session_id}`, {
    code, method: "PATCH", body: { course: " 微观经济学 (ECON10740) " },
  }));
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { session_id, course: "微观经济学 (ECON10740)" });

  const st = await (await h.send(h.web(`/v1/web/session/${session_id}`, { code, method: "GET" }))).json();
  assert.equal(st.course, "微观经济学 (ECON10740)", "账本真的写了");

  const list = await (await h.send(h.web("/v1/web/sessions", { code, method: "GET" }))).json();
  assert.deepEqual(list.sessions.map((s) => s.course), ["微观经济学 (ECON10740)"], "list 里也是新名");
});

test("R3 身份隔离：别人的码改 → 404（账本不变）；不存在的 session → 404", async () => {
  const h = harness();
  const a = await h.addCode("amy");
  const b = await h.addCode("bob");
  const { session_id } = await (await h.send(h.web("/v1/web/session", { code: a, body: { course: "A 的课" } }))).json();

  const stolen = await h.send(h.web(`/v1/web/session/${session_id}`, { code: b, method: "PATCH", body: { course: "B 改的" } }));
  assert.equal(stolen.status, 404);
  assert.equal((await stolen.json()).error, "not_found");

  const st = await (await h.send(h.web(`/v1/web/session/${session_id}`, { code: a, method: "GET" }))).json();
  assert.equal(st.course, "A 的课", "跨码改名没落到账本");

  const missing = await h.send(h.web("/v1/web/session/s_00000000dead", { code: a, method: "PATCH", body: { course: "X" } }));
  assert.equal(missing.status, 404);
  assert.equal((await missing.json()).error, "not_found");
});

test("R4 body 不合法就 400 bad_body：空课名 / 超长 / 缺字段 / JSON 坏掉", async () => {
  const h = harness();
  const code = await h.addCode();
  const { session_id } = await (await h.send(h.web("/v1/web/session", { code, body: { course: "原名" } }))).json();
  const patch = (body) => h.send(h.web(`/v1/web/session/${session_id}`, { code, method: "PATCH", body }));

  for (const body of [{ course: "" }, { course: "   " }, { course: "x".repeat(81) }, {}, { course: null }]) {
    const r = await patch(body);
    assert.equal(r.status, 400, JSON.stringify(body).slice(0, 40));
    const j = await r.json();
    assert.equal(j.error, "bad_body");
    assert.equal(j.detail, "course");
  }
  const broken = await patch("{\"course\":");
  assert.equal(broken.status, 400);
  assert.equal((await broken.json()).error, "bad_body");

  // 80 字正好收（与开课的边界一致）
  const edge = await patch({ course: "y".repeat(80) });
  assert.equal(edge.status, 200);
  assert.equal((await edge.json()).course.length, 80);

  const st = await (await h.send(h.web(`/v1/web/session/${session_id}`, { code, method: "GET" }))).json();
  assert.equal(st.course, "y".repeat(80), "被拒的几次一个都没写进账本");
});

test("R5 幂等 + 两种状态：同名再改 200；进行中与已下课的都能改", async () => {
  const h = harness();
  const code = await h.addCode();
  const { session_id } = await (await h.send(h.web("/v1/web/session", { code, body: { course: "未命名" } }))).json();
  const patch = (course) => h.send(h.web(`/v1/web/session/${session_id}`, { code, method: "PATCH", body: { course } }));

  // 进行中：改两次，第二次同名 -> 还是 200 同一个回复
  await h.send(h.web(`/v1/web/session/${session_id}/lines`, { code, method: "PUT", body: { lines: [L(1)] } }));
  const one = await patch("线代");
  assert.equal(one.status, 200);
  const body1 = await one.json();
  const two = await patch("线代");
  assert.equal(two.status, 200, "同名再改 = 200");
  assert.deepEqual(await two.json(), body1);

  // 已下课：仍能改（课名是课后才填的）
  const end = await h.send(h.web(`/v1/web/session/${session_id}/end`, { code, body: { lines_total: 1 } }));
  assert.equal(end.status, 202);
  const afterEnd = await patch("线性代数");
  assert.equal(afterEnd.status, 200);
  assert.equal((await afterEnd.json()).course, "线性代数");

  const st = await (await h.send(h.web(`/v1/web/session/${session_id}`, { code, method: "GET" }))).json();
  assert.equal(st.state, "ended");
  assert.equal(st.course, "线性代数");
  assert.equal(st.note.state, "queued", "改名不动任务 / seq 水位");
  assert.equal(st.lines_confirmed, 1, "改名不动 seq 水位");
});

test("R6 已删：PATCH 与 GET 同形（410 session_deleted），不再写回账本", async () => {
  const h = harness();
  const code = await h.addCode();
  const { session_id } = await (await h.send(h.web("/v1/web/session", { code, body: { course: "X" } }))).json();
  await h.send(h.web("/v1/web/data", { code, method: "DELETE", body: { confirm: "delete-all-my-data" } }));

  const patch = await h.send(h.web(`/v1/web/session/${session_id}`, { code, method: "PATCH", body: { course: "Y" } }));
  const get = await h.send(h.web(`/v1/web/session/${session_id}`, { code, method: "GET" }));
  assert.equal(patch.status, get.status, "与 GET 的判据一致");
  assert.equal(patch.status, 410);
  const pj = await patch.json();
  assert.equal(pj.error, "session_deleted");
  assert.deepEqual(Object.keys(pj).sort(), Object.keys(await get.json()).sort(), "错误体形状与 GET 相同（含 tombstone）");
});

// ---------- 3. 鉴权 / CORS ----------
test("R7 鉴权与 GET 完全相同：缺码 401 / 非 web 客户端 403 / 来源不对 403", async () => {
  const h = harness();
  const code = await h.addCode();
  const { session_id } = await (await h.send(h.web("/v1/web/session", { code, body: { course: "X" } }))).json();
  const path = `/v1/web/session/${session_id}`;

  const noCode = await h.send(h.web(path, { method: "PATCH", body: { course: "Y" } }));
  assert.equal(noCode.status, 401);
  assert.equal((await noCode.json()).error, "unauthorized");

  const desktop = await h.send(h.web(path, { code, method: "PATCH", body: { course: "Y" }, client: "desktop" }));
  assert.equal(desktop.status, 403);
  assert.equal((await desktop.json()).error, "forbidden");

  const badOrigin = await h.send(h.web(path, { code, method: "PATCH", body: { course: "Y" }, origin: "https://evil.example" }));
  assert.equal(badOrigin.status, 403);
  assert.equal((await badOrigin.json()).error, "forbidden");

  const st = await (await h.send(h.web(path, { code, method: "GET" }))).json();
  assert.equal(st.course, "X", "三次被拒都没改到账本");
});

test("R8 预检：PATCH 在 /v1/web/* 的 Allow-Methods 里；代付路径（/v1/chat/completions）不带 PATCH", async () => {
  const h = harness();
  for (const path of ["/v1/web/session", "/v1/web/session/s_000000000001"]) {
    const res = await h.send(h.req(path, { method: "OPTIONS", headers: { origin: ORIGIN } }));
    assert.equal(res.status, 204, path);
    const allow = res.headers.get("access-control-allow-methods") || "";
    assert.match(allow, /PATCH/, path);
    assert.equal(res.headers.get("access-control-allow-origin"), ORIGIN, path);
  }
  const chat = await h.send(h.req("/v1/chat/completions", { method: "OPTIONS", headers: { origin: ORIGIN } }));
  assert.equal(chat.status, 204);
  assert.doesNotMatch(chat.headers.get("access-control-allow-methods") || "", /PATCH/, "代付路径仍不放 PATCH");

  // 真接口调用的响应也带头（浏览器才看得到 200）
  const code = await h.addCode();
  const { session_id } = await (await h.send(h.web("/v1/web/session", { code, body: { course: "X" } }))).json();
  const patched = await h.send(h.web(`/v1/web/session/${session_id}`, { code, method: "PATCH", body: { course: "Y" } }));
  assert.equal(patched.headers.get("access-control-allow-origin"), ORIGIN);
});

test("R9 老行为不变：同一个 /:id 用别的动词仍是 404；桌面身份打不进 /v1/web/*", async () => {
  const h = harness();
  const code = await h.addCode();
  const { session_id } = await (await h.send(h.web("/v1/web/session", { code, body: { course: "X" } }))).json();
  const put = await h.send(h.web(`/v1/web/session/${session_id}`, { code, method: "PUT", body: { lines: [L(1)] } }));
  assert.equal(put.status, 404, "方法不对 -> 404（不是 405）");

  const desk = await h.send(h.req(`/v1/web/session/${session_id}`, {
    method: "PATCH",
    headers: { authorization: `Bearer ${code}`, "x-classlive-install": INSTALL, origin: ORIGIN, "content-type": "application/json" },
    body: JSON.stringify({ course: "Y" }),
  }));
  assert.equal(desk.status, 403);
  assert.equal((await desk.json()).error, "forbidden");

  // 路径不完整的 / session 形状不对 -> 404，不进 DO
  const badId = await h.send(h.web("/v1/web/session/s_NOTHEX", { code, method: "PATCH", body: { course: "Y" } }));
  assert.equal(badId.status, 404);
});
