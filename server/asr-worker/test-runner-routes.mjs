// W22 判据：runner 路由 + 浏览器读产物 + hub 四个通知点 + 计费头（契约 CONTRACT-runner-20261120.md）。
// 真源码全链路：Worker fetch → 真 WebSessions DO + 真 RunnerHub DO（内存 storage）+ 内存 R2（带 get / put 计数）。
// 唯一假的：上游 LLM（计费头那组，同 test-web-prompt 的做法）。不联网、不碰真码。
//
//   cd server/asr-worker && node --test test-runner-routes.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { _leaseForTests, _resetFastLlm, _resetRoutes, _resetCmdChat, _resetOpenCode } from "./src/handler.js";
import { PassCore } from "./src/passes-core.js";
import { QuotaCore } from "./src/quota-core.js";
import { WebSessions } from "./src/web-session.js";
import { RunnerHub } from "./src/runner-hub.js";
import { codeHashHex } from "./src/web-session-core.js";

const ORIGIN = "https://web.example.test";
const INSTALL = "0123456789abcdef0123456789abcdef";
const TOKEN = `rn_${"a".repeat(32)}`;
const T0 = Date.UTC(2026, 10, 20, 9);
const L = (seq, extra = {}) => ({ seq, t: seq * 2.5, en: `en ${seq}`, zh: `中 ${seq}`, state: "final", src: "asr", ...extra });

function memStorage() {
  const m = new Map();
  return {
    m,
    get: async (k) => (m.has(k) ? structuredClone(m.get(k)) : undefined),
    put: async (k, v) => { m.set(k, structuredClone(v)); },
    delete: async (k) => { for (const kk of [].concat(k)) m.delete(kk); },
    list: async ({ prefix = "" } = {}) => new Map([...m].filter(([k]) => k.startsWith(prefix))),
  };
}

// 内存 R2：比 test-web-session 多一个 get（runner-routes 读产物要用），put 记流水（幂等判据用）。
function memR2() {
  const m = new Map();
  const puts = [];
  return {
    m, puts,
    put: async (k, v) => { m.set(k, String(v)); puts.push(k); },
    get: async (k) => (m.has(k) ? { body: m.get(k), text: async () => m.get(k), httpMetadata: {} } : null),
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

const doStub = (core) => ({ idFromName: (n) => n, get: () => ({ fetch: async (u, init) => core.handle(new Request(u, init)) }) });
function webDo(env) {
  const instances = new Map();
  return {
    idFromName: (n) => n,
    get: (n) => {
      if (!instances.has(n)) instances.set(n, new WebSessions({ storage: memStorage() }, env));
      return instances.get(n);
    },
    instances,
  };
}
function hubDo(env) {
  const instances = new Map();
  return {
    idFromName: (n) => n,
    get: (n) => {
      if (!instances.has(n)) instances.set(n, new RunnerHub({ storage: memStorage() }, env));
      return instances.get(n);
    },
    instances,
  };
}

function harness() {
  _leaseForTests(0);
  _resetFastLlm(); _resetRoutes(); _resetCmdChat(); _resetOpenCode();
  const pass = new PassCore(memStorage(), () => T0);
  const quota = new QuotaCore(memStorage(), () => T0);
  const ae = [];
  const env = {
    CLIENT_TOKEN: "tok-public", ADMIN_TOKEN: "adm", WEB_ORIGIN: ORIGIN, RUNNER_TOKEN: TOKEN,
    PER_INSTALL: { limit: async () => ({ success: true }) },
    PER_IP: { limit: async () => ({ success: true }) },
    QUOTA: doStub(quota), PASSES: doStub(pass),
    AE: { writeDataPoint: (dp) => ae.push(structuredClone(dp)) },
    WEB_DATA: memR2(),
  };
  env.WEB_SESSIONS = webDo(env);
  env.RUNNER_HUB = hubDo(env);
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
  const run = (path, { method = "GET", body, token = TOKEN } = {}) =>
    req(path, {
      method,
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  const addCode = async (name = "amy") =>
    (await (await send(req("/v1/admin/add", {
      headers: { authorization: "Bearer adm", "content-type": "application/json" },
      body: JSON.stringify({ name }),
    }))).json()).code;
  const settle = () => Promise.all(pending.splice(0));
  const open = async (code, course = "ECON10740") =>
    (await (await send(web("/v1/web/session", { code, body: { course, tz: "Europe/Dublin" } }))).json()).session_id;
  return { env, ae, req, send, web, run, addCode, settle, open };
}

// 一节课从头走到「可领任务」：开课（通知点①）→ 两批逐句（通知点②）→ 下课（通知点③）。
async function classUntilQueued(h, code) {
  const sid = await h.open(code);
  await h.send(h.web(`/v1/web/session/${sid}/lines`, { code, method: "PUT", body: { lines: [L(1), L(2)] } }));
  await h.send(h.web(`/v1/web/session/${sid}/lines`, { code, method: "PUT", body: { lines: [L(3, { state: "draft" }), L(4)] } }));
  await h.settle();
  const end = await h.send(h.web(`/v1/web/session/${sid}/end`, { code, body: { lines_total: 4 } }));
  assert.equal(end.status, 202);
  const { job_id } = await end.json();
  return { sid, job_id };
}

test("R1 全链路 + 四个通知点：开课→live，逐句→touch，下课→job+live 删，complete→R2+账本 done，重复交幂等", async () => {
  const h = harness();
  const code = await h.addCode();
  const hash = await codeHashHex(code, 16);
  const sid = await h.open(code);
  await h.settle();
  // 通知点①：开课后 hub 有 live
  const live1 = await (await h.send(h.run("/v1/runner/live"))).json();
  assert.equal(live1.sessions.length, 1);
  assert.equal(live1.sessions[0].session_id, sid);
  assert.equal(live1.sessions[0].course, "ECON10740");
  assert.equal(live1.sessions[0].tz, "Europe/Dublin");
  // 通知点②：逐句成功后 touch（high_water 跟上）
  await h.send(h.web(`/v1/web/session/${sid}/lines`, { code, method: "PUT", body: { lines: [L(1), L(2)] } }));
  await h.settle();
  const live2 = await (await h.send(h.run("/v1/runner/live"))).json();
  assert.equal(live2.sessions[0].high_water, 2);
  // 未出笔记：浏览器读产物 404 not_ready
  const early = await h.send(h.web(`/v1/web/session/${sid}/note`, { code, method: "GET" }));
  assert.equal(early.status, 404);
  assert.equal((await early.json()).error, "not_ready");
  assert.equal((await h.send(h.web(`/v1/web/session/${sid}/summary`, { code, method: "GET" }))).status, 404);
  // 通知点③：下课 202 后 hub 有 job、live 没了
  const end = await h.send(h.web(`/v1/web/session/${sid}/end`, { code, body: { lines_total: 2 } }));
  assert.equal(end.status, 202);
  const { job_id } = await end.json();
  const live3 = await (await h.send(h.run("/v1/runner/live"))).json();
  assert.deepEqual(live3.sessions, [], "下课即不再算正在上的课");
  // runner 领任务 → 心跳 → 交结果
  const claim = await h.send(h.run("/v1/runner/claim", { method: "POST", body: { runner: "vps", kinds: ["note"] } }));
  assert.equal(claim.status, 200);
  const job = await claim.json();
  assert.equal(job.job_id, job_id);
  assert.equal(job.attempt, 1);
  assert.equal(job.input.session_id, sid);
  assert.equal(job.input.course, "ECON10740");
  assert.equal(job.input.tz, "Europe/Dublin");
  const hb = await h.send(h.run("/v1/runner/heartbeat", { method: "POST", body: { job_id, attempt: 1, lease: job.lease } }));
  assert.equal(hb.status, 200);
  const putCount = h.env.WEB_DATA.puts.length;
  const comp = await h.send(h.run("/v1/runner/complete", {
    method: "POST",
    body: {
      job_id, attempt: 1, lease: job.lease, ok: true,
      note: { md: "# 笔记", html: "<!doctype html><p>笔记</p>" },
      meta: { model: "deepseek-flash", wall_s: 96 },
    },
  }));
  assert.equal(comp.status, 200);
  const done = await comp.json();
  assert.equal(done.state, "done");
  assert.deepEqual(done.objects, [
    `web/${hash}/sessions/${sid}/note.md`,
    `web/${hash}/sessions/${sid}/note.html`,
    `web/${hash}/sessions/${sid}/note.meta.json`,
  ]);
  assert.equal(h.env.WEB_DATA.puts.length, putCount + 3, "R2 恰好三个对象");
  assert.equal(h.env.WEB_DATA.m.get(`web/${hash}/sessions/${sid}/note.md`), "# 笔记");
  const meta = JSON.parse(h.env.WEB_DATA.m.get(`web/${hash}/sessions/${sid}/note.meta.json`));
  assert.equal(meta.model, "deepseek-flash");
  assert.equal(meta.attempt, 1);
  assert.ok(meta.finished_at);
  // /job_update 流回每码账本：GET 的 note.state 变 done
  const st = await (await h.send(h.web(`/v1/web/session/${sid}`, { code, method: "GET" }))).json();
  assert.equal(st.note.state, "done");
  assert.equal(st.note.job_id, job_id);
  // 幂等：同一 attempt 重复交 → 200 同一份，不再写 R2
  const again = await h.send(h.run("/v1/runner/complete", {
    method: "POST",
    body: { job_id, attempt: 1, lease: job.lease, ok: true, note: { md: "# 笔记", html: "x" } },
  }));
  assert.equal(again.status, 200);
  assert.deepEqual(await again.json(), done);
  assert.equal(h.env.WEB_DATA.puts.length, putCount + 3, "幂等不写第二份");
  // 浏览器读产物：出了 → 200 + content-type
  const noteMd = await h.send(h.web(`/v1/web/session/${sid}/note?fmt=md`, { code, method: "GET" }));
  assert.equal(noteMd.status, 200);
  assert.equal(noteMd.headers.get("content-type"), "text/markdown; charset=utf-8");
  assert.equal(await noteMd.text(), "# 笔记");
  const noteHtml = await h.send(h.web(`/v1/web/session/${sid}/note`, { code, method: "GET" }));
  assert.equal(noteHtml.status, 200);
  assert.equal(noteHtml.headers.get("content-type"), "text/html; charset=utf-8");
  const badFmt = await h.send(h.web(`/v1/web/session/${sid}/note?fmt=pdf`, { code, method: "GET" }));
  assert.equal(badFmt.status, 400);
});

test("R2 鉴权：令牌错 401、secret 没配 401（fail-closed）、方法不对 404、路径不认识 404", async () => {
  const h = harness();
  await h.addCode();
  assert.equal((await h.send(h.run("/v1/runner/live", { token: "rn_wrong" }))).status, 401);
  assert.equal((await h.send(h.req("/v1/runner/live", { method: "GET" }))).status, 401, "不带令牌");
  const noSecret = { ...h.env };
  delete noSecret.RUNNER_TOKEN;
  const r = await worker.fetch(new Request("https://asr.bldcam.page/v1/runner/live"), noSecret, { waitUntil: () => {} });
  assert.equal(r.status, 401);
  assert.equal((await h.send(h.run("/v1/runner/claim", { method: "GET" }))).status, 404, "只认 POST");
  assert.equal((await h.send(h.run("/v1/runner/other"))).status, 404);
  // 通行码打不进 runner 路由（它不吃通行码）
  const code = await h.addCode();
  const withPass = await h.send(h.req("/v1/runner/live", { method: "GET", headers: { authorization: `Bearer ${code}` } }));
  assert.equal(withPass.status, 401);
});

test("R3 lines：多批合并 + after 过滤 + 升序 + 已删 410 + 课不存在 404", async () => {
  const h = harness();
  const code = await h.addCode();
  const hash = await codeHashHex(code, 16);
  const sid = await h.open(code);
  const put = (lines) => h.send(h.web(`/v1/web/session/${sid}/lines`, { code, method: "PUT", body: { lines } }));
  await put([L(1), L(2)]);
  await put([L(5), L(3)]);          // 乱序批
  const all = await h.send(h.run(`/v1/runner/lines?hash=${hash}&session_id=${sid}&after=0`));
  assert.equal(all.status, 200);
  assert.equal(all.headers.get("content-type"), "application/x-ndjson");
  const rows = (await all.text()).trim().split("\n").map((x) => JSON.parse(x));
  assert.deepEqual(rows.map((r) => r.seq), [1, 2, 3, 5], "升序合并");
  assert.deepEqual(Object.keys(rows[0]).sort(), ["en", "seq", "src", "t", "zh"]);
  const tailRows = (await (await h.send(h.run(`/v1/runner/lines?hash=${hash}&session_id=${sid}&after=2`))).text())
    .trim().split("\n").map((x) => JSON.parse(x));
  assert.deepEqual(tailRows.map((r) => r.seq), [3, 5], "只回 seq > after");
  const none = await h.send(h.run(`/v1/runner/lines?hash=${hash}&session_id=${sid}&after=5`));
  assert.equal((await none.text()), "");
  assert.equal((await h.send(h.run(`/v1/runner/lines?hash=${hash}&session_id=s_00000000dead&after=0`))).status, 404);
  await h.send(h.web("/v1/web/data", { code, method: "DELETE", body: { confirm: "delete-all-my-data" } }));
  const gone = await h.send(h.run(`/v1/runner/lines?hash=${hash}&session_id=${sid}&after=0`));
  assert.equal(gone.status, 410);
  assert.equal((await gone.json()).error, "session_deleted");
});

test("R4 summary：1 MiB+1 → 413、坏 JSON 400、课不在 404、成功 204 且浏览器能读回", async () => {
  const h = harness();
  const code = await h.addCode();
  const hash = await codeHashHex(code, 16);
  const sid = await h.open(code);
  const put = (body) => h.send(h.run(`/v1/runner/summary?hash=${hash}&session_id=${sid}`, { method: "PUT", body }));
  const big = await put("x".repeat(1024 * 1024 + 1));
  assert.equal(big.status, 413);
  assert.equal((await big.json()).error, "too_large");
  // 坏 JSON 得绕过 run() 的 JSON.stringify，用 req 直接发原文
  const bad = await h.send(h.req(`/v1/runner/summary?hash=${hash}&session_id=${sid}`, {
    method: "PUT", headers: { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" }, body: "not json",
  }));
  assert.equal(bad.status, 400);
  assert.equal((await bad.json()).error, "bad_body");
  assert.equal((await h.send(h.run(`/v1/runner/summary?hash=${hash}&session_id=s_00000000dead`, {
    method: "PUT", body: "{}",
  }))).status, 404);
  const doc = { session_id: sid, updated_at: "2026-11-20T10:00:00Z", final: false, chapters: [] };
  // put 会再 JSON.stringify 一层（run 的约定），好文档直接用 req 发原文
  const ok = await h.send(h.req(`/v1/runner/summary?hash=${hash}&session_id=${sid}`, {
    method: "PUT", headers: { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" },
    body: JSON.stringify(doc),
  }));
  assert.equal(ok.status, 204);
  const back = await h.send(h.web(`/v1/web/session/${sid}/summary`, { code, method: "GET" }));
  assert.equal(back.status, 200);
  assert.equal(back.headers.get("content-type"), "application/json; charset=utf-8");
  assert.deepEqual(JSON.parse(await back.text()), doc);
});

test("R5 删数据（通知点④）：live 与未完成 job 都没了；heartbeat 410；claim 空", async () => {
  const h = harness();
  const code = await h.addCode();
  const hash = await codeHashHex(code, 16);
  const { sid, job_id } = await classUntilQueued(h, code);
  const claim = await (await h.send(h.run("/v1/runner/claim", { method: "POST", body: { runner: "vps", kinds: ["note"] } }))).json();
  assert.equal(claim.job_id, job_id);
  const del = await h.send(h.web("/v1/web/data", { code, method: "DELETE", body: { confirm: "delete-all-my-data" } }));
  assert.equal(del.status, 200);
  // 通知点④之后：live 没了、job 没了（gonejob 标记在），runner 立即停手
  const live = await (await h.send(h.run("/v1/runner/live"))).json();
  assert.deepEqual(live.sessions, []);
  const hb = await h.send(h.run("/v1/runner/heartbeat", { method: "POST", body: { job_id, attempt: 1, lease: claim.lease } }));
  assert.equal(hb.status, 410);
  const cp = await h.send(h.run("/v1/runner/complete", {
    method: "POST",
    body: { job_id, attempt: 1, lease: claim.lease, ok: true, note: { md: "x", html: "y" } },
  }));
  assert.equal(cp.status, 410, "删了不写 R2");
  assert.equal([...h.env.WEB_DATA.m.keys()].filter((k) => k.startsWith("web/")).length, 0, "一个产物都没长回来");
  assert.equal((await h.send(h.run("/v1/runner/claim", { method: "POST", body: { runner: "vps", kinds: ["note"] } }))).status, 204);
});

test("R6 计费头：只有 runner 码认 X-ClassLive-Bill-To（chat / jev 两条路），别的码忽略、形状不对忽略", async () => {
  const h = harness();
  const runnerCode = await h.addCode("runner");
  const amyCode = await h.addCode("amy");
  const billTo = await codeHashHex(amyCode, 16);
  const calls = { up: [] };
  const origFetch = globalThis.fetch;
  const origLog = console.log;
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    calls.up.push(u);
    if (u.startsWith("https://api.deepseek.com/")) {
      return new Response(JSON.stringify({ choices: [{ message: { content: "{}" }, finish_reason: "stop" }], usage: { prompt_tokens: 3, completion_tokens: 2 } }), { status: 200, headers: { "content-type": "application/json" } });
    }
    if (u.startsWith("https://api.typesafe.ai/")) return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
    throw new Error("unexpected upstream " + u);
  };
  console.log = () => {};
  h.env.DEEPSEEK_KEY = "sk-test";
  h.env.JEV_KEY = "jev-test";
  try {
    const chatReq = (code, bill) => h.req("/v1/chat/completions", {
      headers: {
        authorization: `Bearer ${code}`, "x-classlive-install": INSTALL, "content-type": "application/json",
        ...(bill ? { "x-classlive-bill-to": bill } : {}),
      },
      body: JSON.stringify({ model: "deepseek-flash", max_tokens: 64, messages: [{ role: "user", content: "hi" }] }),
    });
    await (await h.send(chatReq(runnerCode, billTo))).text();
    assert.equal(h.ae.at(-1).blobs[10], billTo, "runner 码 + 合法头 → 记进 blob11");
    assert.equal(h.ae.at(-1).blobs.length, 11);
    await (await h.send(chatReq(runnerCode, "nothex"))).text();
    assert.equal(h.ae.at(-1).blobs[10], "", "形状不对（非 16 hex）→ 忽略");
    await (await h.send(chatReq(amyCode, billTo))).text();
    assert.equal(h.ae.at(-1).blobs[10], "", "别的码带这个头 → 无视");
    await (await h.send(chatReq(runnerCode))).text();
    assert.equal(h.ae.at(-1).blobs[10], "", "runner 码不发头 → 空");
    const jevReq = (code, bill) => h.req("/v1/jev", {
      headers: {
        authorization: `Bearer ${code}`, "x-classlive-install": INSTALL, "content-type": "application/json",
        ...(bill ? { "x-classlive-bill-to": bill } : {}),
      },
      body: JSON.stringify({ state: "s", questions: { q1: "2+2?" } }),
    });
    await (await h.send(jevReq(runnerCode, billTo))).text();
    assert.equal(h.ae.at(-1).blobs[10], billTo, "jev 路同样认");
    await (await h.send(jevReq(amyCode, billTo))).text();
    assert.equal(h.ae.at(-1).blobs[10], "");
  } finally {
    globalThis.fetch = origFetch;
    console.log = origLog;
  }
});

// Claude 11-20：网页课名「下课再起」—— 笔记任务要用改过的名字；hub 的改名通知丢了也要从账本补
test("R7 课后改名：通知点⑤更新排队任务；通知丢了 claim 也从账本取当前课名", async () => {
  for (const dropNotice of [false, true]) {
    const h = harness();
    const code = await h.addCode();
    const sid = await h.open(code, "未命名");
    await h.send(h.web(`/v1/web/session/${sid}/lines`, { code, method: "PUT", body: { lines: [L(1), L(2)] } }));
    await h.settle();
    assert.equal((await h.send(h.web(`/v1/web/session/${sid}/end`, { code, body: { lines_total: 2 } }))).status, 202);
    const rn = await h.send(h.web(`/v1/web/session/${sid}`, { code, method: "PATCH", body: { course: "宏观 第五讲" } }));
    assert.equal(rn.status, 200);
    await h.settle();
    if (dropNotice) {
      // 模拟 hub 的改名通知丢了：hub 里那条 job 还是占位名，且已过等名期 → claim 要从每码账本补当前课名
      const st = h.env.RUNNER_HUB.instances.get("hub").core.storage;
      for (const [k, j] of await st.list({ prefix: "job:" })) {
        await st.put(k, { ...j, course: "未命名", created: "2026-01-01T00:00:00.000Z" });
      }
    }
    const claim = await h.send(h.run("/v1/runner/claim", { method: "POST", body: { runner: "vps", kinds: ["note"] } }));
    assert.equal(claim.status, 200, `dropNotice=${dropNotice}`);
    assert.equal((await claim.json()).input.course, "宏观 第五讲", `dropNotice=${dropNotice}`);
  }
});

test("R8 时区：网页只在 X-ClassLive-TZ 头里给时区，任务要带上（不能退成 UTC）", async () => {
  const h = harness();
  const code = await h.addCode();
  const req = h.web("/v1/web/session", { code, body: { course: "ECON10740" } });
  req.headers.set("x-classlive-tz", "Asia/Shanghai");
  const sid = (await (await h.send(req)).json()).session_id;
  await h.send(h.web(`/v1/web/session/${sid}/lines`, { code, method: "PUT", body: { lines: [L(1)] } }));
  await h.settle();
  assert.equal((await h.send(h.web(`/v1/web/session/${sid}/end`, { code, body: { lines_total: 1 } }))).status, 202);
  const claim = await (await h.send(h.run("/v1/runner/claim", { method: "POST", body: { runner: "vps", kinds: ["note"] } }))).json();
  assert.equal(claim.input.tz, "Asia/Shanghai");
});

test("R9 看板：/v1/admin/runner/state 回计数与最近任务", async () => {
  const h = harness();
  const code = await h.addCode();
  await classUntilQueued(h, code);
  const r = await h.send(h.req("/v1/admin/runner/state", { headers: { authorization: "Bearer adm", "content-type": "application/json" }, body: JSON.stringify({ limit: 5 }) }));
  const b = await r.json();
  assert.equal(r.status, 200, JSON.stringify(b));
  assert.equal(b.counts.queued, 1);
});

test("R10 OCR：claim / heartbeat 的 body 是字面 null / 数组 → 400 bad_body（不是 500）", async () => {
  const h = harness();
  for (const p of ["/v1/runner/claim", "/v1/runner/heartbeat"]) {
    for (const raw of ["null", "[]", "3"]) {
      const r = await h.send(new Request(`https://asr.bldcam.page${p}`, {
        method: "POST", headers: { authorization: `Bearer ${h.env.RUNNER_TOKEN}` }, body: raw,
      }));
      assert.equal(r.status, 400, `${p} ${raw}`);
    }
  }
});

test("R11 OCR：删数据落在 complete 校验与写 R2 之间 → 提交 410 且不留孤儿产物", async () => {
  const h = harness();
  const code = await h.addCode();
  await classUntilQueued(h, code);
  const claim = await (await h.send(h.run("/v1/runner/claim", { method: "POST", body: { runner: "vps", kinds: ["note"] } }))).json();
  const put0 = h.env.WEB_DATA.put;
  let fired = false;
  h.env.WEB_DATA.put = async (k, v, o) => {
    if (!fired && k.endsWith("/note.md")) {
      fired = true;
      const del = await h.send(h.web("/v1/web/data", { code, method: "DELETE", body: { confirm: "delete-all-my-data" } }));
      assert.equal(del.status, 200);
    }
    return put0(k, v, o);
  };
  const cp = await h.send(h.run("/v1/runner/complete", {
    method: "POST", body: { job_id: claim.job_id, attempt: 1, lease: claim.lease, ok: true, note: { md: "x", html: "y" } },
  }));
  assert.ok(fired);
  assert.equal(cp.status, 410);
  assert.deepEqual([...h.env.WEB_DATA.m.keys()].filter((k) => /note\./.test(k)), [], "产物被清掉");
});
