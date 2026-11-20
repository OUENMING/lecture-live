// 网页版 P4-b 的判据：网页实时转写的**日额度**（默认 5 h，可全局 / 单码调）。
//
//   cd server/asr-worker && node --test test-web-quota.mjs
//
// 管什么：`rt_web_s` 怎么回收分账（client_reference_id 后缀 `-web`）、`daily_web_rt_s` / env
// `WEB_DAILY_RT_S` 三态（数字 / none / null=跟随全局）、领临时 key 时 `max_session_duration_seconds`
// 被压到今日剩余、剩余 <= 0 直接 quota。**桌面那条路逐字不变**是每组的对照组。
// 玩法：真 PassCore + 内存 storage + 假时钟；假 Soniox（只截 POST temporary-api-key，不联网）。
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { _leaseForTests } from "./src/handler.js";
import { PassCore } from "./src/passes-core.js";
import { QuotaCore } from "./src/quota-core.js";

const INSTALL = "0123456789abcdef0123456789abcdef";
const T0 = Date.UTC(2026, 10, 17, 9);            // 2026-11-17 09:00 UTC
const TODAY = "2026-11-17";
const KEY = "snx_temp_ABCDEFGHIJKLMNOP";
const DEFAULT_S = 18000;                          // 5 h（Owen 2026-11-17 定）
const SESSION_S = 10800;                          // soniox-core.DEFAULT_MAX_SESSION_S（3 h）

function memStorage(seed = {}) {
  const m = new Map(Object.entries(seed));
  return {
    m,
    get: async (k) => structuredClone(m.get(k)),
    put: async (k, v) => { m.set(k, structuredClone(v)); },
    delete: async (keys) => { for (const k of [].concat(keys)) m.delete(k); },
    list: async ({ prefix = "" } = {}) => new Map([...m].filter(([k]) => k.startsWith(prefix))),
  };
}

const J = (status, obj) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const doStub = (core) => ({ idFromName: (n) => n, get: () => ({ fetch: async (u, init) => core.handle(new Request(u, init)) }) });

function harness({ env: extraEnv = {} } = {}) {
  _leaseForTests(0);                              // 短租关掉：每次请求都真问一次 DO（剩余额度才看得见）
  let now = T0;
  const storage = memStorage();
  const env = {
    CLIENT_TOKEN: "tok-public", SONIOX_KEY: "soniox-us-key",
    PER_INSTALL: { limit: async () => ({ success: true }) },
    PER_IP: { limit: async () => ({ success: true }) },
    ...extraEnv,
  };
  const pass = new PassCore(storage, () => now, env);   // DO 壳把 env 传进核心（WEB_DAILY_RT_S 在那）
  const quota = new QuotaCore(memStorage(), () => now);
  env.QUOTA = doStub(quota);
  env.PASSES = doStub(pass);
  const calls = { mint: [] };
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    if (u === "https://api.soniox.com/v1/auth/temporary-api-key") {
      calls.mint.push(JSON.parse(init.body));
      return J(201, { api_key: KEY, expires_at: "2026-11-17T09:01:00.000Z" });
    }
    throw new Error("unexpected upstream " + u);
  };
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p) };
  const req = (h, body = "") => {
    const headers = { authorization: `Bearer ${h.auth}`, "x-classlive-install": INSTALL };
    if (h.web) headers["x-classlive-client"] = "web";
    return new Request("https://asr.bldcam.page/v1/soniox/key", { method: "POST", headers, body });
  };
  const keyReq = (auth, { web = false, body = "" } = {}) => req({ auth, web }, body);
  // 用量落账：直接打 DO 的回收路由（真机上是 handler.scheduled -> collectUsage -> 这条）。
  const importRt = async (records, { until = now } = {}) =>
    (await pass.handle(new Request("https://passes/admin/rt/import", {
      method: "POST", body: JSON.stringify({ region: "us", until_ms: until, records }),
    }))).json();
  const rec = (uuid, name, seconds) => ({ uuid, name, end_ms: now - 1000, ms: seconds * 1000 });
  const limit = (body) => pass.handle(new Request("https://passes/admin/limit", {
    method: "POST", body: JSON.stringify(body),
  })).then((r) => r.json());
  const list = async () => (await pass.handle(new Request("https://passes/admin/list", { method: "POST", body: "{}" }))).json();
  const stats = async (body) => (await pass.handle(new Request("https://passes/admin/stats", {
    method: "POST", body: JSON.stringify(body),
  }))).json();
  const restore = () => { globalThis.fetch = origFetch; };
  return { env, storage, pass, calls, keyReq, importRt, rec, limit, list, stats, restore,
           call: (r, e = env) => worker.fetch(r, e, ctx) };
}

async function withHarness(opts, fn) {
  const h = harness(opts);
  try { return await fn(h); } finally { h.restore(); }
}
const mintBody = (h) => h.calls.mint.at(-1);

// ---------- 1. 默认 5 h ----------
test("Q1 默认额度：新码没配 env / 没配单码 -> 网页 5 h；回包带 web_rt_left_s；桌面照旧不带也不算", () =>
  withHarness({}, async (h) => {
    const { code } = await h.pass.add({ name: "amy" });
    assert.equal((await h.pass.add({ name: "amy" })).daily_web_rt_s, null, "新码的 daily_web_rt_s = null（跟随全局）");

    const web = await h.call(h.keyReq(code, { web: true }));
    assert.equal(web.status, 200);
    const j = await web.json();
    assert.equal(j.web_rt_left_s, DEFAULT_S, "默认 5 h");
    assert.equal(j.max_session_s, SESSION_S, "3 h < 5 h，压不动");
    assert.equal(mintBody(h).client_reference_id, "amy-web", "网页领的 key 用 <码名>-web");

    const desk = await h.call(h.keyReq(code));
    assert.equal(desk.status, 200);
    const d = await desk.json();
    assert.equal("web_rt_left_s" in d, false, "桌面回包形状逐字不变");
    assert.equal(d.max_session_s, SESSION_S);
    assert.deepEqual(mintBody(h).client_reference_id, "amy", "桌面还是裸码名");
    assert.ok(!JSON.stringify(h.calls.mint).includes(code), "完整码不许进请求体");
  }));

// ---------- 2. env 改全局默认 ----------
test("Q2 全局 env：WEB_DAILY_RT_S=3600 -> 网页剩 3600 且会话上限被压到 3600；桌面不受影响", () =>
  withHarness({ env: { WEB_DAILY_RT_S: "3600" } }, async (h) => {
    const { code } = await h.pass.add({ name: "amy" });
    const j = await (await h.call(h.keyReq(code, { web: true }))).json();
    assert.equal(j.web_rt_left_s, 3600);
    assert.equal(j.max_session_s, 3600, "min(10800, 3600)");
    assert.equal(mintBody(h).max_session_duration_seconds, 3600);
    const d = await (await h.call(h.keyReq(code))).json();
    assert.equal(d.max_session_s, SESSION_S, "桌面仍是 3 h");
  }));

// ---------- 3. 单码覆盖 ----------
test("Q3 单码覆盖：daily_web_rt_s=600 盖掉全局；改回 null 又跟随全局", () => withHarness({}, async (h) => {
  const { code } = await h.pass.add({ name: "amy" });
  const lim = await h.limit({ code, daily_web_rt_s: 600 });
  assert.equal(lim.daily_web_rt_s, 600);
  const j = await (await h.call(h.keyReq(code, { web: true }))).json();
  assert.equal(j.web_rt_left_s, 600);
  assert.equal(j.max_session_s, 600);
  assert.equal((await h.limit({ code, daily_web_rt_s: null })).daily_web_rt_s, null, "null = 跟随全局");
  assert.equal((await (await h.call(h.keyReq(code, { web: true }))).json()).web_rt_left_s, DEFAULT_S);
  assert.ok((await h.limit({ code, daily_web_rt_s: -1 })).error, "负数不是合法额度");
}));

// ---------- 4. none = 不限（全局 / 单码两条路）----------
test("Q4 none：env WEB_DAILY_RT_S=none 与单码 \"none\" 都 = 不限；会话上限回到 3 h，回包字段是 null", () => {
  const run = async (h) => {
    const { code } = await h.pass.add({ name: "amy" });
    const j = await (await h.call(h.keyReq(code, { web: true }))).json();
    assert.equal(j.web_rt_left_s, null, "null = 不限");
    assert.equal(j.max_session_s, SESSION_S, "不限就不压");
    return code;
  };
  return withHarness({ env: { WEB_DAILY_RT_S: "none" } }, async (h) => {
    const code = await run(h);
    // 用满了也不拦（软闸只剩「桌面 daily_rt_s」那一条，这里是 null）
    await h.importRt([h.rec("u1", "amy-web", 20000)]);
    assert.equal((await h.call(h.keyReq(code, { web: true }))).status, 200);
    assert.equal((await h.limit({ code, daily_web_rt_s: "none" })).daily_web_rt_s, "none");
  }).then(() => withHarness({}, async (h) => {
    const code = await h.pass.add({ name: "amy" }).then((r) => r.code);
    await h.limit({ code, daily_web_rt_s: "none" });       // 单码盖掉默认 5 h
    await h.importRt([h.rec("u1", "amy-web", 20000)]);
    const j = await (await h.call(h.keyReq(code, { web: true }))).json();
    assert.equal(j.web_rt_left_s, null);
    assert.equal(j.max_session_s, SESSION_S);
  }));
});

// ---------- 5. 桌面不受影响 ----------
test("Q5 桌面逐字不变：网页用满不影响桌面；桌面 daily_rt_s 那道闸照旧（含跨天清零）", () => withHarness({}, async (h) => {
  const { code } = await h.pass.add({ name: "amy" });
  await h.importRt([h.rec("w1", "amy-web", DEFAULT_S)]);     // 网页用满 5 h
  assert.equal((await h.call(h.keyReq(code))).status, 200, "桌面照旧放行（桌面闸只看 rt_s / 桌面不传 client=web）");
  assert.equal((await h.call(h.keyReq(code, { web: true }))).status, 429, "网页这边已经满了");
  // 桌面自己的额度仍按 daily_rt_s 判，且 rt_web_s 那部分**同时**计进 rt_s（合判，防两头凑）
  await h.limit({ code, daily_rt_s: 100 });
  assert.equal((await h.call(h.keyReq(code))).status, 429, "合计秒数 18000 >= 100");
  await h.limit({ code, daily_rt_s: null });
  assert.equal((await h.call(h.keyReq(code))).status, 200);
}));

// ---------- 6. 回收按后缀分账 ----------
test("Q6 分账：`amy-web` 只进 rt_web_s；`amy` 只进 rt_s，但两者都累加进 rt_s（合判）；stats / list 都看得见", () =>
  withHarness({}, async (h) => {
    const { code } = await h.pass.add({ name: "amy" });
    const out = await h.importRt([h.rec("d1", "amy", 30), h.rec("w1", "amy-web", 45)]);
    assert.equal(out.ok, true);
    assert.equal(out.new_s, 75);
    const st = await h.stats({ day: TODAY });
    const row = st.days[0].rows.find((r) => r.code === code);
    assert.equal(row.rt_s, 75, "合计");
    assert.equal(row.rt_web_s, 45, "网页那部分");
    const l = await h.list();
    assert.deepEqual([l.codes[0].rt_web_s, l.codes[0].rt_s], [45, 75], "list 里带今日网页额度用量");
    // 再跑一遍不重复记（去重还在），且后缀拆出来的名字对得上码
    assert.equal((await h.importRt([h.rec("d1", "amy", 30), h.rec("w1", "amy-web", 45)])).dup_n, 2);
    assert.equal((await h.list()).codes[0].rt_web_s, 45);
    // 拆不回码的网页名字照样报 lost，且名字保留原样（`-web` 不吞）
    const lost = await h.importRt([h.rec("x1", "ghost-web", 9)]);
    assert.deepEqual(lost.lost, [{ name: "ghost-web", s: 9, n: 1 }]);
  }));

// ---------- 7. 旧记录兼容 ----------
test("Q7 老记录：没有 rt_web_s 的用量格 / 没有 daily_web_rt_s 的码记录 -> 一律当 0 / 跟随全局，不出现 NaN", () =>
  withHarness({}, async (h) => {
    const { code } = await h.pass.add({ name: "amy" });
    // 造一份「升级前」的用量格：只有 rt_s，没有 rt_web_s；码记录也删掉 daily_web_rt_s
    await h.importRt([h.rec("old1", "amy", 500)]);
    const u = await h.storage.get(`use:${TODAY}:${code}`);
    delete u.rt_web_s;
    await h.storage.put(`use:${TODAY}:${code}`, u);
    const c = await h.storage.get(`code:${code}`);
    delete c.daily_web_rt_s;
    await h.storage.put(`code:${code}`, c);

    const j = await (await h.call(h.keyReq(code, { web: true }))).json();
    assert.equal(j.web_rt_left_s, DEFAULT_S, "老记录里网页用量按 0 算");
    assert.equal(j.max_session_s, SESSION_S);
    assert.deepEqual((await h.list()).codes.map((x) => x.rt_web_s), [0], "list 里是 0 不是 undefined");
  }));

// ---------- 8. 会话上限压到今日剩余 ----------
test("Q8 压上限：已用 17900 s -> 剩 100 s，key 的 max_session_duration_seconds=100；min 不会超过原值", () =>
  withHarness({}, async (h) => {
    const { code } = await h.pass.add({ name: "amy" });
    await h.importRt([h.rec("w1", "amy-web", 17900)]);
    const j = await (await h.call(h.keyReq(code, { web: true }))).json();
    assert.equal(j.web_rt_left_s, 100);
    assert.equal(j.max_session_s, 100);
    assert.deepEqual(mintBody(h).max_session_duration_seconds, 100);
    // 剩得比 3 h 多 -> 仍是 3 h（不放大）
    await h.importRt([h.rec("w2", "amy-web", 0)]);
    const j2 = await (await h.call(h.keyReq(code, { web: true }))).json();
    assert.equal(j2.max_session_s, 100, "同一笔用量没变，仍是 100");
  }));

// ---------- 9. 剩余 <= 0 直接 quota ----------
test("Q9 用满 / 超出：网页 -> 429 quota_exceeded（不碰上游、不登记设备）；探测 key 不压也不带字段", () =>
  withHarness({}, async (h) => {
    const { code } = await h.pass.add({ name: "amy" });
    await h.importRt([h.rec("w1", "amy-web", DEFAULT_S)]);
    const over = await h.call(h.keyReq(code, { web: true }));
    assert.equal(over.status, 429);
    assert.equal((await over.json()).error, "quota_exceeded");
    assert.equal(h.calls.mint.length, 0, "被拒的请求不该去发 key");
    const l = await h.list();
    assert.equal(l.codes[0].devices, 0, "被拒的请求不登记设备");
    // 超一点点也一样
    await h.limit({ code, daily_web_rt_s: DEFAULT_S + 1 });
    await h.importRt([h.rec("w2", "amy-web", 5)]);
    assert.equal((await h.call(h.keyReq(code, { web: true }))).status, 429, "18005 >= 18001");
    // 探测 key：15 s，不压、不带 web_rt_left_s（它是联调用的，不该吃额度展示）。
    // 额度放开再测（满额时探测也会被同一道闸拦掉 —— 那道闸在 check 里，与 purpose 无关）。
    await h.limit({ code, daily_web_rt_s: 30000 });   // 抬到已用之上（18005）才放得回来
    const probe = await (await h.call(h.keyReq(code, { web: true, body: JSON.stringify({ purpose: "probe" }) }))).json();
    assert.equal(probe.max_session_s, 15);
    assert.equal("web_rt_left_s" in probe, false);
    assert.equal(mintBody(h).client_reference_id, "amy-web");
  }));

// ---------- 10. 短租命中时也要拿到剩余额度 ----------
test("Q10 短租内连领两次：都拿到 web_rt_left_s 且第二次仍按剩余压上限（不因为命中缓存就丢字段）", () => {
  const h = harness();
  return (async () => {
    try {
      _leaseForTests(60_000);                     // 打开 60 s 短租
      const { code } = await h.pass.add({ name: "amy" });
      const a = await (await h.call(h.keyReq(code, { web: true }))).json();
      assert.equal(a.web_rt_left_s, DEFAULT_S);
      const b = await (await h.call(h.keyReq(code, { web: true }))).json();
      assert.equal(b.web_rt_left_s, DEFAULT_S, "命中短租也要有字段（不能让网页显示 undefined）");
      assert.equal(b.max_session_s, SESSION_S);
    } finally { h.restore(); _leaseForTests(0); }
  })();
});

// 2026-11-20：boluo 只在网页上过课，面板却显示「0/2 台 · 从没用过」——list 只数桌面 dev:<码>。
test("Q11 list 分开给桌面 / 网页设备数与最后使用；桌面字段逐字不变", () => withHarness({}, async (h) => {
  const { code } = await h.pass.add({ name: "amy" });
  assert.equal((await h.call(h.keyReq(code, { web: true }))).status, 200);
  const r = (await h.list()).codes[0];
  assert.equal(r.devices, 0, "桌面没用过");
  assert.equal(r.last_seen, 0);
  assert.equal(r.web_devices, 1, "网页那台要数到");
  assert.ok(r.web_last_seen > 0, "网页最后使用要有时间");
  assert.equal(r.max_web_devices, undefined, "网页不再单独设名额（与桌面合计）");
}));
