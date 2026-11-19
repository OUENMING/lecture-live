// W12 判据：网页版会话账本（docs/API-web-v0-20261117.md §3.1–3.5、§3.7、§5）。
// 直接 import 真源码；DO 用真 WebSessions 类 + 内存 storage 与内存 R2，上游一条都不碰。
//
//   cd server/asr-worker && node --test test-web-session.mjs
//
// 钉住什么：
//   1. 幂等重放：同一 (session_id, seq) 再来不重复写；draft -> final **只覆盖一次**
//   2. 乱序到达 confirmed_seq 不跳号；seq > high_water + 500 -> 409 seq_too_far
//   3. 批太大 413；跨码隔离（A 码读不到 B 码的会话 -> 404）
//   4. end 重复调用同一 job_id、dedupe=true；DELETE 之后 GET 404 / PUT 410
//   5. 缺通行码 401；非 web 客户端 / 来源不对 403；桌面原有路由行为不变
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "./src/handler.js";
import { PassCore } from "./src/passes-core.js";
import { QuotaCore } from "./src/quota-core.js";
import { WebSessions } from "./src/web-session.js";
import {
  MAX_LINES_PER_BATCH, SessionLedger, checkLines, codeHashHex, newSession, normalizeCourse,
} from "./src/web-session-core.js";

const ORIGIN = "https://web.example.test";
const INSTALL = "0123456789abcdef0123456789abcdef";
const INSTALL2 = "fedcba9876543210fedcba9876543210";
const T0 = Date.UTC(2026, 10, 17, 9);

const J = (status, obj) => new Response(JSON.stringify(obj), {
  status, headers: { "content-type": "application/json" },
});

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

// 内存 R2：只实现本文件用到的 put / list / delete（含 cursor 分页语义）。
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

// DO 桩：idFromName 就是 hash 本身，get 回一个真 WebSessions 实例。
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
  const web = (path, { code, method = "POST", body, install = INSTALL, origin = ORIGIN, client = "web", idem } = {}) =>
    req(path, {
      method,
      headers: {
        authorization: code ? `Bearer ${code}` : undefined,
        "x-classlive-install": install, origin, "x-classlive-client": client,
        "content-type": "application/json", "idempotency-key": idem,
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

// ---------- 1. 纯逻辑：course 放宽 / 批形状 ----------
test("S1 纯逻辑：course 收中文 / 空格 / 连字符（偏离契约，待 Owen 确认），控制字符与空串拒收", () => {
  assert.equal(normalizeCourse("  ECON10740 "), "ECON10740");
  assert.equal(normalizeCourse("微观经济"), "微观经济");
  assert.equal(normalizeCourse("CS-101"), "CS-101");
  assert.equal(normalizeCourse("Econ 10740 (L03)"), "Econ 10740 (L03)");
  assert.equal(normalizeCourse("a\u0000b\nc"), "abc");
  assert.equal(normalizeCourse(""), null);
  assert.equal(normalizeCourse("   "), null);
  assert.equal(normalizeCourse("x".repeat(80)).length, 80);
  assert.equal(normalizeCourse("x".repeat(81)), null);
  assert.equal(normalizeCourse(null), null);

  assert.equal(checkLines([]), "bad_body");
  assert.equal(checkLines([{ seq: 1, en: "a", zh: "b" }]), "bad_body", "缺 state");
  assert.equal(checkLines([{ seq: 0, en: "a", zh: "b", state: "final" }]), "bad_body");
  assert.equal(checkLines([{ seq: 1.5, en: "a", zh: "b", state: "final" }]), "bad_body");
  assert.equal(checkLines([L(1), L(2)]), null);
  assert.equal(checkLines(Array.from({ length: MAX_LINES_PER_BATCH + 1 }, (_, i) => L(i + 1))), "too_large");
});

// ---------- 2. 账本：先到先得 / draft->final / 乱序 ----------
test("S2 账本：先到先得（重放 ignored）、draft -> final 只覆盖一次、后来 final 再送 ignored", () => {
  const meta = newSession({ session_id: "s_000000000001", course: "X", now: "t" });
  const led = new SessionLedger(meta, new Map());
  assert.deepEqual(led.append([L(1), L(2)]), { confirmed_seq: 2, high_water: 2, received: 2, ignored: 0 });
  assert.equal(led.write.length, 2);
  // 内容相同 / 不同都算重放：先到先得，不写
  const again = led.append([L(1, { en: "changed" }), L(2)]);
  assert.deepEqual(again, { confirmed_seq: 2, high_water: 2, received: 2, ignored: 2 });
  assert.equal(led.write.length, 2, "重放不产生新的 R2 写");
  assert.equal(led.known.get(1), "final");

  // draft 先到 -> final 覆盖一次 -> 再送 final 一律 ignored
  const d = led.append([L(3, { state: "draft" })]);
  assert.equal(d.confirmed_seq, 3);
  assert.equal(led.write.length, 2, "draft 不落 R2");
  led.write.length = 0;
  assert.equal(led.append([L(3, { state: "final" })]).ignored, 0);
  assert.equal(led.write.length, 1, "覆盖写一次");
  assert.equal(led.append([L(3, { state: "final" })]).ignored, 1, "已 final 再送 = ignored");
  // 反过来：final 之后来 draft 不改（定稿优先）
  assert.equal(led.known.get(3), "final");
});

test("S3 账本：乱序到达 confirmed_seq 不跳号；seq_too_far 一个字节都不写", () => {
  const meta = newSession({ session_id: "s_000000000002", course: "X", now: "t" });
  const led = new SessionLedger(meta, new Map());
  const r = led.append([L(2), L(3)]);
  assert.equal(r.confirmed_seq, 0, "缺 1 -> 不连续，不往前推");
  assert.equal(r.high_water, 3);
  assert.equal(led.append([L(1)]).confirmed_seq, 3, "1 到了就连着推到 3");
  assert.equal(led.append([L(5)]).confirmed_seq, 3, "5 到了但缺 4 -> 仍不跳号");
  assert.equal(led.append([L(4)]).confirmed_seq, 5);

  const before = led.known.size;
  const far = led.append([L(1000), L(1001)]);
  assert.equal(far.error, "seq_too_far");
  assert.equal(led.known.size, before, "超限整批拒收，不落半个 seq");
  assert.equal(meta.high_water, 5);
  assert.equal(led.append([L(505)]).error, undefined, "high_water + 500 以内照收");
});

// ---------- 3. HTTP：开课 / 追加 / 读 ----------
test("S4 开课：201 回 session_id + limits；缺码 401；来源 / 客户端不对 403", async () => {
  const h = harness();
  const code = await h.addCode();
  const created = await h.send(h.web("/v1/web/session", { code, body: { course: " ECON10740 " } }));
  assert.equal(created.status, 201);
  const body = await created.json();
  assert.match(body.session_id, /^s_[0-9a-f]{12}$/);
  assert.deepEqual(body.limits, { max_session_s: 10800, lines_batch: 200, body_bytes: 262144 });
  assert.ok(body.server_time);

  const noCode = await h.send(h.web("/v1/web/session", { body: { course: "X" } }));
  assert.equal(noCode.status, 401);
  assert.equal((await noCode.json()).error, "unauthorized");

  const desktop = await h.send(h.web("/v1/web/session", { code, body: { course: "X" }, client: "desktop" }));
  assert.equal(desktop.status, 403, "非 web 客户端");
  assert.equal((await desktop.json()).error, "forbidden");

  const badOrigin = await h.send(h.web("/v1/web/session", { code, body: { course: "X" }, origin: "https://evil.example" }));
  assert.equal(badOrigin.status, 403);
  assert.equal((await badOrigin.json()).error, "forbidden");

  const badCourse = await h.send(h.web("/v1/web/session", { code, body: { course: "   " } }));
  assert.equal(badCourse.status, 400);
  assert.equal((await badCourse.json()).error, "bad_body");

  // Idempotency-Key：24 h 内重复提交回同一节课（200 而不是 201，契约 §3.1）
  const k = "11111111-2222-3333-4444-555555555555";
  const a = await (await h.send(h.web("/v1/web/session", { code, body: { course: "X" }, idem: k }))).json();
  const b = await h.send(h.web("/v1/web/session", { code, body: { course: "X" }, idem: k }));
  assert.equal(b.status, 200);
  assert.equal((await b.json()).session_id, a.session_id, "同一幂等键回同一节课");
});

test("S5 追加：confirmed_seq / high_water / ignored 原样回；R2 只写 final（一个键一批）", async () => {
  const h = harness();
  const code = await h.addCode();
  const { session_id } = await (await h.send(h.web("/v1/web/session", { code, body: { course: "微观经济" } }))).json();
  const put = (lines) => h.send(h.web(`/v1/web/session/${session_id}/lines`, { code, method: "PUT", body: { lines } }));

  const r1 = await put([L(1), L(2, { state: "draft" })]);
  assert.equal(r1.status, 200);
  assert.deepEqual(await r1.json(), { confirmed_seq: 2, high_water: 2, received: 2, ignored: 0 });
  const hash = await codeHashHex(code, 16);
  const keys = [...h.env.WEB_DATA.map.keys()].filter((k) => k.includes(session_id));
  assert.equal(keys.length, 1, "一批 final 只写一个对象");
  assert.equal(keys[0], `web/${hash}/sessions/${session_id}/lines-1.json`);
  const nd = h.env.WEB_DATA.map.get(keys[0]).trim().split("\n").map((x) => JSON.parse(x));
  assert.deepEqual(nd.map((x) => x.seq), [1], "draft 不落 R2");
  assert.equal(nd[0].zh, "中 1");

  // draft -> final 覆盖：同一 seq 再写一次，对象数不变（键还带那个起始 seq）
  const r2 = await put([L(2)]);
  assert.deepEqual(await r2.json(), { confirmed_seq: 2, high_water: 2, received: 1, ignored: 0 });
  const keys2 = [...h.env.WEB_DATA.map.keys()].filter((k) => k.includes(session_id));
  assert.equal(keys2.length, 2, "覆盖写的是新键（起始 seq=2），不是原地改");

  // 重放：整批 ignored，R2 不增长
  const r3 = await put([L(1), L(2)]);
  assert.deepEqual(await r3.json(), { confirmed_seq: 2, high_water: 2, received: 2, ignored: 2 });
  assert.equal([...h.env.WEB_DATA.map.keys()].filter((k) => k.includes(session_id)).length, 2);

  const st = await (await h.send(h.web(`/v1/web/session/${session_id}`, { code, method: "GET" }))).json();
  assert.equal(st.lines_confirmed, 2);
  assert.equal(st.state, "live");
  assert.equal(st.note, null);
  assert.equal(st.course, "微观经济");
});

test("S6 批太大 413 / seq_too_far 409 / 批为空 400 / 不存在 404", async () => {
  const h = harness();
  const code = await h.addCode();
  const { session_id } = await (await h.send(h.web("/v1/web/session", { code, body: { course: "X" } }))).json();
  const put = (lines, sid = session_id) => h.send(h.web(`/v1/web/session/${sid}/lines`, { code, method: "PUT", body: { lines } }));

  const many = Array.from({ length: MAX_LINES_PER_BATCH + 1 }, (_, i) => L(i + 1));
  const tooMany = await put(many);
  assert.equal(tooMany.status, 413);
  assert.equal((await tooMany.json()).error, "too_large");

  // 请求体真字节数 > 256 KiB（Content-Length 可以不发，靠实际字节判）
  const huge = L(1, { en: "x".repeat(262144) });
  const tooBig = await put([huge]);
  assert.equal(tooBig.status, 413);
  assert.equal((await tooBig.json()).error, "too_large");

  assert.equal((await put([L(1), L(800)])).status, 409, "high_water=0 -> 上限 500");
  assert.equal((await (await put([L(1), L(501)])).json()).error, "seq_too_far");
  assert.equal((await put([])).status, 400);
  const missing = await put([L(1)], "s_00000000dead");
  assert.equal(missing.status, 404);
  assert.equal((await missing.json()).error, "not_found");
});

// ---------- 4. 下课 / 列表 / 删除 ----------
test("S7 end：第一次 202 queued，重复调用同一 job_id dedupe=true，再追加 409 session_ended", async () => {
  const h = harness();
  const code = await h.addCode();
  const { session_id } = await (await h.send(h.web("/v1/web/session", { code, body: { course: "X" } }))).json();
  const end = (body = { lines_total: 2, level: "std" }) =>
    h.send(h.web(`/v1/web/session/${session_id}/end`, { code, body }));

  const first = await end();
  assert.equal(first.status, 202);
  const j1 = await first.json();
  assert.match(j1.job_id, /^j_[0-9a-f]{12}$/);
  assert.equal(j1.status, "queued");
  assert.equal(j1.dedupe, false);

  const second = await end();
  assert.equal(second.status, 200);
  const j2 = await second.json();
  assert.equal(j2.job_id, j1.job_id, "同一节课只有一条笔记任务");
  assert.equal(j2.dedupe, true);
  assert.equal(j2.conflict, false);

  const conflict = await (await end({ lines_total: 999, level: "std" })).json();
  assert.equal(conflict.dedupe, true);
  assert.equal(conflict.conflict, true, "参数不一致只报告，不改账本");

  const badLevel = await end({ level: "pro" });
  assert.equal(badLevel.status, 400);
  assert.equal((await badLevel.json()).error, "bad_body");

  const after = await h.send(h.web(`/v1/web/session/${session_id}/lines`, { code, method: "PUT", body: { lines: [L(1)] } }));
  assert.equal(after.status, 409);
  assert.equal((await after.json()).error, "session_ended");

  const st = await (await h.send(h.web(`/v1/web/session/${session_id}`, { code, method: "GET" }))).json();
  assert.equal(st.state, "ended");
  assert.equal(st.note.state, "queued");
});

test("S8 列表：只列自己那枚码的会话；跨码隔离（A 读不到 B -> 404）；分页 next_before", async () => {
  const h = harness();
  const a = await h.addCode("amy");
  const b = await h.addCode("bob");
  const one = await (await h.send(h.web("/v1/web/session", { code: a, body: { course: "A1" } }))).json();
  await h.send(h.web("/v1/web/session", { code: a, body: { course: "A2" } }));

  const stolen = await h.send(h.web(`/v1/web/session/${one.session_id}`, { code: b, method: "GET" }));
  assert.equal(stolen.status, 404, "B 码读不到 A 码的会话");
  const stolenPut = await h.send(h.web(`/v1/web/session/${one.session_id}/lines`, {
    code: b, method: "PUT", body: { lines: [L(1)] },
  }));
  assert.equal(stolenPut.status, 404);

  const list = await (await h.send(h.web("/v1/web/sessions", { code: a, method: "GET" }))).json();
  assert.equal(list.sessions.length, 2);
  assert.deepEqual(list.sessions.map((s) => s.course).sort(), ["A1", "A2"]);
  assert.equal(list.next_before, null);

  const page1 = await (await h.send(h.web("/v1/web/sessions?limit=1", { code: a, method: "GET" }))).json();
  assert.equal(page1.sessions.length, 1);
  assert.ok(page1.next_before, "还有下一页");
  const page2 = await (await h.send(h.web(`/v1/web/sessions?limit=1&before=${page1.next_before}`, { code: a, method: "GET" }))).json();
  assert.equal(page2.sessions.length, 1);
  assert.notEqual(page2.sessions[0].session_id, page1.sessions[0].session_id);

  const bList = await (await h.send(h.web("/v1/web/sessions", { code: b, method: "GET" }))).json();
  assert.deepEqual(bList.sessions, [], "B 码看不到 A 的课");
});

test("S9 删除：先账本后 R2；GET 410（tombstone）/ PUT 410；再删一次计数为 0；confirm 不对 400", async () => {
  const h = harness();
  const code = await h.addCode();
  const { session_id } = await (await h.send(h.web("/v1/web/session", { code, body: { course: "X" } }))).json();
  await h.send(h.web(`/v1/web/session/${session_id}/lines`, { code, method: "PUT", body: { lines: [L(1), L(2)] } }));
  await h.send(h.web(`/v1/web/session/${session_id}/end`, { code, body: { lines_total: 2 } }));
  assert.ok(h.env.WEB_DATA.map.size > 0);

  const bad = await h.send(h.web("/v1/web/data", { code, method: "DELETE", body: { confirm: "yes" } }));
  assert.equal(bad.status, 400);
  assert.equal((await bad.json()).error, "bad_body");

  const del = await h.send(h.web("/v1/web/data", { code, method: "DELETE", body: { confirm: "delete-all-my-data" } }));
  assert.equal(del.status, 200);
  const d = await del.json();
  assert.equal(d.deleted.sessions, 1);
  assert.equal(d.deleted.jobs, 1);
  assert.equal(d.deleted.objects, 1, "一批 final 一个对象，删干净");
  assert.ok(d.tombstone_until);
  assert.equal(h.env.WEB_DATA.map.size, 0, "R2 按前缀删干净");

  const gone = await h.send(h.web(`/v1/web/session/${session_id}`, { code, method: "GET" }));
  assert.equal(gone.status, 410);
  assert.equal((await gone.json()).error, "session_deleted");
  const put = await h.send(h.web(`/v1/web/session/${session_id}/lines`, { code, method: "PUT", body: { lines: [L(1)] } }));
  assert.equal(put.status, 410);
  const end = await h.send(h.web(`/v1/web/session/${session_id}/end`, { code, body: {} }));
  assert.equal(end.status, 410, "删了不再入任务");

  const again = await (await h.send(h.web("/v1/web/data", { code, method: "DELETE", body: { confirm: "delete-all-my-data" } }))).json();
  assert.deepEqual(again.deleted, { sessions: 0, jobs: 0, objects: 0 }, "幂等：第二次各计数为 0");
});

// ---------- 5. 桌面不变 ----------
test("S10 桌面不变：老四条路由照旧、GET / 未知路径仍 404、/v1/web/* 用桌面身份 403", async () => {
  const h = harness();
  const code = await h.addCode();
  // 老路由：转写照旧（假上游不走，这里只看鉴权与 404 形状）
  const g = await h.send(h.req("/v1/soniox/key", { method: "GET", headers: { origin: ORIGIN } }));
  assert.equal(g.status, 404);
  assert.equal(await g.text(), "not found");
  const other = await h.send(h.req("/v1/other", { headers: { origin: ORIGIN }, body: "{}" }));
  assert.equal(other.status, 404);
  assert.equal(await other.text(), "not found");
  // 老路由的 401 / 403 形状没被 JSON 化
  const noAuth = await h.send(h.req("/v1/chat/completions", { headers: { "x-classlive-install": INSTALL }, body: "{}" }));
  assert.equal(noAuth.status, 401);
  assert.equal(await noAuth.text(), "unauthorized");
  // 桌面身份打到新路径：403（这些接口不属于桌面）
  const desk = await h.send(h.req("/v1/web/sessions", { method: "GET", headers: { authorization: `Bearer ${code}`, "x-classlive-install": INSTALL, origin: ORIGIN } }));
  assert.equal(desk.status, 403);
  assert.equal((await desk.json()).error, "forbidden");
  // 方法不对 -> 404（不是 405：同老路由的立场）
  const wrong = await h.send(h.web("/v1/web/sessions", { code, method: "DELETE", body: {} }));
  assert.equal(wrong.status, 404);
});

test("S11 预检：新路径（session / lines / data）仍回 204 + 回显那一个来源", async () => {
  const h = harness();
  for (const path of ["/v1/web/session", "/v1/web/session/s_000000000001/lines", "/v1/web/data"]) {
    const res = await h.send(h.req(path, { method: "OPTIONS", headers: { origin: ORIGIN } }));
    assert.equal(res.status, 204, path);
    assert.equal(res.headers.get("access-control-allow-origin"), ORIGIN, path);
    assert.equal(res.headers.get("vary"), "Origin", path);
  }
});
