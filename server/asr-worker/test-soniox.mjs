// Soniox 实时转写判据 —— 临时 key 路由 + 用量回收（Cron）。
// 直接 import src 真源码；Soniox 换成假上游，Passes / Quota 用真 core + 内存 storage。
//
//   node --test server/asr-worker/test-soniox.mjs
//
// ⚠️ 不验证 Cloudflare 平台真实行为（cron 触发、DO 分布式语义、真 Soniox 回包 —— 假回包照官方 schema 造）。
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { _leaseForTests } from "./src/handler.js";
import { PassCore, CODE_RE } from "./src/passes-core.js";
import { QuotaCore } from "./src/quota-core.js";

const INSTALL = "0123456789abcdef0123456789abcdef";
const T0 = Date.UTC(2026, 9, 17, 9);          // 2026-10-17 09:00 UTC（假时钟起点，也是 cron 的第一次 scheduledTime）
const TODAY = "2026-10-17";
const YDAY = "2026-10-16";
const KEY = "snx_temp_ABCDEFGHIJKLMNOP";       // 假 Soniox key（真形状是 snx_temp_<三段 base64url>）

function memStorage() {
  const m = new Map();
  return {
    m,
    get: async (k) => structuredClone(m.get(k)),
    put: async (k, v) => { m.set(k, structuredClone(v)); },
    delete: async (keys) => { for (const k of [].concat(keys)) m.delete(k); },
    list: async ({ prefix }) => new Map([...m].filter(([k]) => k.startsWith(prefix)).sort()),
  };
}

const J = (status, obj) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const doStub = (core) => ({ idFromName: (n) => n, get: () => ({ fetch: async (u, init) => core.handle(new Request(u, init)) }) });

// 假 Soniox 用量日志的一条（字段名与官方 get_usage_logs 的 schema 一致）。
const logRec = ({ uuid, name, end, ms, model = "stt-rt-v5" }) => ({
  uuid, request_scope: "api", client_reference_id: name, model,
  start_time: new Date(Date.parse(end) - 60000).toISOString(), end_time: end,
  input_text_tokens: 0, input_audio_tokens: 0, input_audio_duration_ms: ms,
  output_text_tokens: 0, output_audio_tokens: 0, output_audio_duration_ms: 0, cost_usd: "0.0025",
});

function harness({ mint, logs, mintThrow = false } = {}) {
  _leaseForTests(0);   // 短租默认关；租约那条用例自己开
  let now = T0;
  const pass = new PassCore(memStorage(), () => now);
  const quota = new QuotaCore(memStorage(), () => now);
  const calls = { mint: [], logs: [], ae: [] };
  const logged = [];   // 收 console.log，专门用来查「key 有没有漏进日志」
  const origLog = console.log;
  console.log = (...a) => { logged.push(a.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" ")); };
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    const m = /^https:\/\/api(\.jp)?\.soniox\.com\/(v1\/auth\/temporary-api-key|v1\/usage-logs)(\?.*)?$/.exec(u);
    if (m) {
      const region = m[1] ? "jp" : "us";
      const auth = init.headers?.authorization || "";
      if (m[2] === "v1/auth/temporary-api-key") {
        calls.mint.push({ region, url: u, auth, body: JSON.parse(init.body) });
        if (mintThrow) throw new Error("network down");
        return typeof mint === "function" ? mint(region, calls.mint.length)
          : (mint || J(201, { api_key: KEY, expires_at: "2026-10-17T09:01:00.000Z" }));
      }
      const params = Object.fromEntries(new URL(u).searchParams);
      calls.logs.push({ region, auth, params, url: u });
      const body = typeof logs === "function" ? logs(region, params, calls.logs.length)
        : (logs || { usage_logs: [], next_page_cursor: null });
      if (body instanceof Response) return body;   // 错误码等：原样回
      // 官方按 end_time 过滤（左闭右开）—— 假上游照做，不然用例会白捡一个实现里没有的假设。
      const a = Date.parse(params.start_time), b = Date.parse(params.end_time);
      return J(200, { ...body,
        usage_logs: body.usage_logs.filter((r) => Date.parse(r.end_time) >= a && Date.parse(r.end_time) < b) });
    }
    if (u.startsWith("https://api.groq.com/")) {
      return J(200, { text: "hi", segments: [{ no_speech_prob: 0.01, compression_ratio: 1 }] });
    }
    throw new Error("unexpected upstream " + u);
  };
  const env = {
    CLIENT_TOKEN: "tok-public", SONIOX_KEY: "soniox-us-key", GROQ_KEY: "gk",
    PER_INSTALL: { limit: async () => ({ success: true }) },
    PER_IP: { limit: async () => ({ success: true }) },
    QUOTA: doStub(quota), PASSES: doStub(pass),
    AE: { writeDataPoint: (dp) => { calls.ae.push(structuredClone(dp)); } },
  };
  const pending = [];   // 这条路上没有 waitUntil 的活（发 key 是同步等回来的），留着兜底
  const ctx = { waitUntil: (p) => pending.push(p) };
  const call = async (req, e = env) => worker.fetch(req, e, ctx);
  // cron：先把 DO 的假时钟推到 scheduledTime（真机上是两个都跟着真实时间走，差在毫秒级）
  const cron = async (at = T0 + 3600_000, e = env) => { now = Math.max(now, at); return worker.scheduled({ scheduledTime: at }, e); };
  const keyReq = (auth, { install = INSTALL, country = null, method = "POST" } = {}) => {
    const headers = {};
    if (auth !== null) headers.authorization = auth;
    if (install !== null) headers["x-classlive-install"] = install;
    const r = new Request("https://asr.bldcam.page/v1/soniox/key",
      { method, headers, ...(method === "POST" ? { body: "" } : {}) });
    if (country) Object.defineProperty(r, "cf", { value: { country, colo: "HKG" } });   // 边缘才有 request.cf
    return r;
  };
  const asrReq = (auth, seconds = "12") => new Request("https://asr.bldcam.page/v1/transcribe", {
    method: "POST",
    headers: { authorization: auth, "x-classlive-install": INSTALL, "x-audio-seconds": seconds },
    body: new Uint8Array(100),
  });
  const stats = async (body) => (await pass.handle(new Request("https://passes/admin/stats", { method: "POST", body: JSON.stringify(body) }))).json();
  const limit = (body) => pass.handle(new Request("https://passes/admin/limit", { method: "POST", body: JSON.stringify(body) })).then((r) => r.json());
  const cursor = async (region = "us") => (await pass.handle(new Request("https://passes/admin/rt/cursor", { method: "POST", body: JSON.stringify({ region }) }))).json();
  const restore = () => { globalThis.fetch = origFetch; console.log = origLog; };
  return { env, call, cron, keyReq, asrReq, stats, limit, cursor, calls, logged, pass, restore,
           tick: (ms) => { now += ms; } };
}

async function withHarness(opts, fn) {
  const h = harness(opts);
  try { return await fn(h); } finally { h.restore(); }
}
const newCode = (h, name) => h.pass.add({ name });   // 回 {code, ...记录}（与 test-passes.mjs 的 admin/add 同形）

// ---------- 1. 入口：只认通行码 ----------
test("S1 入口：公开令牌 / 没 auth 一律 401，坏码 403，坏 install 400，GET 404；一条都没碰上游", () => withHarness({}, async (h) => {
  const { code } = await h.pass.add({ name: "amy" });
  assert.equal((await h.call(h.keyReq("Bearer tok-public"))).status, 401);
  assert.equal((await h.call(h.keyReq(null))).status, 401);
  assert.equal((await h.call(h.keyReq("Bearer cl-x-zz"))).status, 403, "形状不对的码不许落到公开令牌那条");
  assert.equal((await h.call(h.keyReq("Bearer cl-nobody-0123456789abcdef"))).status, 403, "没发过的码");
  assert.equal((await h.call(h.keyReq(`Bearer ${code}`, { install: "XYZ" }))).status, 400);
  assert.equal((await h.call(h.keyReq(`Bearer ${code}`, { install: null }))).status, 400);
  assert.equal((await h.call(h.keyReq(`Bearer ${code}`, { method: "GET" }))).status, 404);
  assert.equal((await h.call(h.keyReq(`Bearer ${code}`, { install: INSTALL.toUpperCase() }))).status, 400);
  // 限流：同一道 PER_IP / PER_INSTALL（转写那两道）
  for (const env of [{ ...h.env, PER_IP: { limit: async () => ({ success: false }) } },
                     { ...h.env, PER_INSTALL: { limit: async () => ({ success: false }) } }]) {
    const res = await h.call(h.keyReq(`Bearer ${code}`), env);
    assert.equal(res.status, 429);
    assert.equal(res.headers.get("retry-after"), "60");
  }
  assert.equal(h.calls.mint.length, 0, "上面这些一条都不该去发 key");
}));

// ---------- 2. 没配 SONIOX_KEY ----------
test("S2 没配 SONIOX_KEY -> 503 soniox_not_configured（fail-closed，不碰上游）", () => withHarness({}, async (h) => {
  const { code } = await newCode(h, "amy");
  const env = { ...h.env, SONIOX_KEY: undefined };
  const res = await h.call(h.keyReq(`Bearer ${code}`), env);
  assert.equal(res.status, 503);
  assert.deepEqual(await res.json(), { error: "soniox_not_configured" });
  assert.equal(h.calls.mint.length, 0);
}));

// ---------- 3. 成功：回包形状 + 发 key 的请求体 ----------
test("S3 成功：回包只给 key + ws_url + 区；请求体 single_use、client_reference_id=名字（不是完整码）", () => withHarness({}, async (h) => {
  const { code } = await newCode(h, "amy");
  assert.equal(code.split("-")[1], "amy");
  const res = await h.call(h.keyReq(`Bearer ${code}`));
  assert.equal(res.status, 200);
  const j = await res.json();
  assert.deepEqual(j, {
    api_key: KEY, ws_url: "wss://stt-rt.soniox.com/transcribe-websocket", region: "us",
    expires_in_seconds: 60, max_session_s: 10800,
  });
  assert.equal(h.calls.mint.length, 1);
  const m = h.calls.mint[0];
  assert.equal(m.url, "https://api.soniox.com/v1/auth/temporary-api-key");
  assert.equal(m.auth, "Bearer soniox-us-key");
  assert.deepEqual(m.body, {
    usage_type: "transcribe_websocket", expires_in_seconds: 60, single_use: true,
    max_session_duration_seconds: 10800, client_reference_id: "amy",
  });
  const all = JSON.stringify(m.body) + JSON.stringify(j);
  assert.ok(!all.includes(code), "完整码不许出现在请求体 / 回包里");
}));

test("S4 SONIOX_MAX_SESSION_S：给了就用、越界夹进 [1,18000]、非法回默认", () => withHarness({}, async (h) => {
  const { code } = await newCode(h, "amy");
  for (const [val, want] of [["7200", 7200], ["999999", 18000], ["0", 10800], ["abc", 10800], ["", 10800], [undefined, 10800]]) {
    const env = { ...h.env, SONIOX_MAX_SESSION_S: val };
    const j = await (await h.call(h.keyReq(`Bearer ${code}`), env)).json();
    assert.equal(j.max_session_s, want, `SONIOX_MAX_SESSION_S=${val}`);
    assert.equal(h.calls.mint.at(-1).body.max_session_duration_seconds, want);
  }
}));

// ---------- 5. 选区 ----------
test("S5 选区：中国+港澳台+日韩有 JP key 走日本；没 JP key 走美国；IE 有 JP key 也走美国", () => withHarness({}, async (h) => {
  const { code } = await newCode(h, "amy");
  const jp = { ...h.env, SONIOX_KEY_JP: "soniox-jp-key" };
  for (const cc of ["CN", "HK", "MO", "TW", "JP", "KR"]) {
    const j = await (await h.call(h.keyReq(`Bearer ${code}`, { country: cc }), jp)).json();
    assert.equal(j.region, "jp", cc);
    assert.equal(j.ws_url, "wss://stt-rt.jp.soniox.com/transcribe-websocket", cc);
    assert.equal(h.calls.mint.at(-1).url, "https://api.jp.soniox.com/v1/auth/temporary-api-key", cc);
    assert.equal(h.calls.mint.at(-1).auth, "Bearer soniox-jp-key", cc);
  }
  for (const cc of ["IE", "GB", "US"]) {
    const j = await (await h.call(h.keyReq(`Bearer ${code}`, { country: cc }), jp)).json();
    assert.equal(j.region, "us", `${cc} 不该走日本`);
    assert.equal(h.calls.mint.at(-1).auth, "Bearer soniox-us-key", cc);
  }
  // ⭐ 两个条件都要：配了 JP key 但国家不在表里 -> 美国；国家在表里但没 JP key -> 美国
  const j = await (await h.call(h.keyReq(`Bearer ${code}`, { country: "CN" }))).json();
  assert.deepEqual([j.region, h.calls.mint.at(-1).auth], ["us", "Bearer soniox-us-key"]);
  const noCf = await (await h.call(h.keyReq(`Bearer ${code}`), jp)).json();
  assert.equal(noCf.region, "us", "没有 request.cf（本地 / 别的入口）也走美国");
}));

// ---------- 6. 上游不配合 ----------
test("S6 上游 5xx / 缺 key 的 2xx / 网络错 -> 502 soniox_upstream", () => withHarness({}, async (h) => {
  const { code } = await newCode(h, "amy");
  assert.equal((await h.call(h.keyReq(`Bearer ${code}`))).status, 200);   // 默认假上游正常
  for (const [label, opts] of [["500", { mint: J(500, { error_type: "internal_error" }) }],
                               ["201 缺 api_key", { mint: J(201, { expires_at: "x" }) }],
                               ["网络错", { mintThrow: true }]]) {
    await withHarness(opts, async (g) => {
      const { code: c2 } = await newCode(g, "amy");
      const res = await g.call(g.keyReq(`Bearer ${c2}`));
      assert.equal(res.status, 502, label);
      assert.deepEqual(await res.json(), { error: "soniox_upstream" }, label);
    });
  }
}));

// ---------- 7. AE ----------
test("S7 AE：路径进白名单，model=stt-rt-v5，up=soniox-<区>:<状态>；事件里没有 key", () => withHarness({}, async (h) => {
  const { code } = await newCode(h, "amy");
  const jp = { ...h.env, SONIOX_KEY_JP: "soniox-jp-key" };
  await h.call(h.keyReq(`Bearer ${code}`));
  await h.call(h.keyReq(`Bearer ${code}`, { country: "CN" }), jp);
  await h.call(h.keyReq(`Bearer ${code}`), { ...h.env, SONIOX_KEY: undefined });
  await h.call(h.keyReq("Bearer tok-public"));
  assert.equal(h.calls.ae.length, 4);
  const [us, jpEv, nokey, pub] = h.calls.ae;
  assert.deepEqual([us.blobs[0], us.blobs[1], us.blobs[2], us.blobs[3], us.doubles[0]],
    ["/v1/soniox/key", "amy", "stt-rt-v5", "soniox-us:201", 200]);
  assert.deepEqual([jpEv.blobs[0], jpEv.blobs[3]], ["/v1/soniox/key", "soniox-jp:201"]);
  assert.deepEqual([nokey.blobs[3], nokey.doubles[0]], ["", 503], "没配 key：没调上游，up 留空");
  assert.deepEqual([pub.blobs[1], pub.doubles[0]], ["public", 401]);
  // 事件里不许有 key / 完整码（一轮成功 + 一轮 502 都要查）
  await withHarness({ mint: J(500, {}) }, async (g) => {
    const { code: c2 } = await newCode(g, "bob");
    await g.call(g.keyReq(`Bearer ${c2}`));
    const dump = JSON.stringify(g.calls.ae);
    assert.ok(!dump.includes(KEY) && !dump.includes(c2), "502 那次事件里漏了 key / 码");
  });
  assert.ok(!JSON.stringify(h.calls.ae).includes(KEY), "事件里出现了 key");
  assert.ok(!JSON.stringify(h.calls.ae).includes(code), "事件里出现了完整码");
}));

// ---------- 8. 日志里没有 key ----------
test("S8 日志：成功 / 上游 502 / cron 三种路径的 console 输出里都搜不到 key", () => withHarness({
  logs: () => ({ usage_logs: [logRec({ uuid: "u-1", name: "amy", end: "2026-10-17T08:30:00Z", ms: 60000 })], next_page_cursor: null }),
}, async (h) => {
  const { code } = await newCode(h, "amy");
  assert.equal((await h.call(h.keyReq(`Bearer ${code}`))).status, 200);
  await withHarness({ mint: J(500, { error: { message: "boom" } }) }, async (g) => {
    const { code: c2 } = await newCode(g, "bob");
    assert.equal((await g.call(g.keyReq(`Bearer ${c2}`))).status, 502);
    assert.ok(g.logged.length > 0, "上游挂了要有日志（不然 502 风暴看不见）");
    assert.ok(!g.logged.join("\n").includes(KEY), "502 的日志里出现 key");
  });
  await h.cron();
  const all = h.logged.join("\n");
  assert.ok(all.length > 0, "cron 至少该落一行账");
  for (const secret of [KEY, code, "soniox-us-key"]) assert.ok(!all.includes(secret), `日志里出现了 ${secret}`);
}));

// ---------- 9. 租约内被吊销：晚到的拒绝不给 key ----------
test("S9 租期内被吊销 -> 晚到的拒绝：上游白花一把，但回 403 且回包里没有 key", () => withHarness({}, async (h) => {
  const { code } = await newCode(h, "amy");
  _leaseForTests(60_000);
  const first = await h.call(h.keyReq(`Bearer ${code}`));
  assert.equal(first.status, 200);
  assert.equal((await first.json()).api_key, KEY);
  await h.pass.handle(new Request("https://passes/admin/revoke", { method: "POST", body: JSON.stringify({ code }) }));
  const res = await h.call(h.keyReq(`Bearer ${code}`));
  assert.equal(res.status, 403, "吊销后这一段先发后查，晚到的拒绝必须生效");
  assert.equal(h.calls.mint.length, 2, "上游已经起跑（租约的代价，最多一把）");
  const text = await res.text();
  assert.ok(!text.includes(KEY), "被拒的回包里漏了 key");
}));

// ---------- 10. rt 额度 ----------
test("S10 rt 额度：daily_rt_s 满了 -> 429 quota_exceeded；放开 -> 200；不影响转写那条", () => withHarness({}, async (h) => {
  const { code } = await newCode(h, "amy");
  const lim = await h.limit({ code, daily_rt_s: 30 });
  assert.deepEqual([lim.daily_rt_s, lim.daily_asr_s, lim.daily_tokens], [30, null, null], "只设 rt 这一条，别的照旧");
  // 按 cron 的真实形状先记 30 s（回收是一条一条日志进来，见 #rtImport）
  const imported = await (await h.pass.handle(new Request("https://passes/admin/rt/import", { method: "POST", body: JSON.stringify({
    region: "us", until_ms: T0, records: [{ uuid: "u-x", name: "amy", end_ms: T0 - 3600_000, ms: 30000 }],
  }) }))).json();
  assert.deepEqual([imported.ok, imported.new_s, imported.new_n], [true, 30, 1]);
  const over = await h.call(h.keyReq(`Bearer ${code}`));
  assert.equal(over.status, 429);
  assert.equal((await over.json()).error, "quota_exceeded");
  assert.equal(h.calls.mint.length, 0, "超额度不许去发 key");
  assert.equal((await h.call(h.asrReq(`Bearer ${code}`))).status, 200, "daily_rt_s 不该拦转写（那条看 daily_asr_s）");
  await h.limit({ code, daily_rt_s: null });
  assert.equal((await h.call(h.keyReq(`Bearer ${code}`))).status, 200);
  assert.ok((await h.limit({ code, daily_rt_s: -1 })).error, "负数不是合法限额");
  // 跨 UTC 日清零：到了第二天，同一格用量就是新的一天（rt_s 记在 use:<日>:<码> 上）
  h.tick(86400000);
  await h.limit({ code, daily_rt_s: 30 });
  assert.equal((await h.call(h.keyReq(`Bearer ${code}`))).status, 200);
}));

// ---------- 11. Cron：汇总 + 跑两次不重复 ----------
test("S11 Cron：按码名汇总音频秒、按 end_time 归天；窗口重叠的那批跑第二次**不重复记**", () => withHarness({
  // 同一批日志每次都给（窗口重叠时，下一轮真的会再看见上一轮的记录）
  logs: () => ({
    usage_logs: [
      logRec({ uuid: "u-1", name: "amy", end: "2026-10-17T09:10:00Z", ms: 30000 }),   // 30 s
      logRec({ uuid: "u-2", name: "amy", end: "2026-10-17T09:40:00Z", ms: 20000 }),   // 20 s
      logRec({ uuid: "u-3", name: "amy", end: "2026-10-16T23:50:00Z", ms: 12000 }),   // 跨午夜 -> 归前一天（第二轮窗口外）
      logRec({ uuid: "u-4", name: "ghost", end: "2026-10-17T09:45:00Z", ms: 9000 }),  // 没这个码
      logRec({ uuid: "u-5", name: "", end: "2026-10-17T09:46:00Z", ms: 9000 }),       // 不是我们的记录
    ],
    next_page_cursor: null,
  }),
}, async (h) => {
  const { code } = await newCode(h, "amy");
  await h.cron(T0 + 3600_000);
  const st = await h.stats({ day: TODAY });
  const row = st.days[0].rows.find((r) => r.code === code);
  assert.deepEqual([row.rt_s, row.rt_reqs, row.asr_s], [50, 2, 0]);
  const y = await h.stats({ day: YDAY });
  assert.deepEqual([y.days[0].rows[0].rt_s, y.days[0].rows[0].rt_reqs], [12, 1]);
  // 记不下的名字要**报出来**（别静默丢）；空 client_reference_id 也算记不下
  const last = JSON.parse(h.logged.at(-1));
  assert.deepEqual(last.out.lost, [{ name: "ghost", s: 9, n: 1 }, { name: "", s: 9, n: 1 }]);
  assert.deepEqual([last.got, last.bad, last.out.new_s], [5, 0, 62], "第一轮：五条都拉到，62 s 落账（含跨午夜那条 12 s）");
  assert.equal(h.calls.logs.length, 1, "JP 没配 key：只拉美国区");
  // ⭐ 第二次（11:00）：窗口起点 = 上轮末尾（10:00）回退 3 h = 07:00，u-1 / u-2 / u-4 / u-5 又落在这一段里
  //    -> 会被重新拉到，但一条都不许再记
  await h.cron(T0 + 7200_000);
  const seen2 = h.calls.logs[1].params;
  assert.equal(seen2.start_time, "2026-10-17T07:00:00.000Z", "第二轮窗口起点 = 上轮窗口末尾（10:00）回退 3 h");
  const got2 = JSON.parse(h.logged.at(-1));
  assert.deepEqual([got2.got, got2.out.dup_n, got2.out.new_n], [4, 4, 0], "重叠段那 4 条全被认出来是重复");
  const st2 = await h.stats({ day: TODAY });
  const row2 = st2.days[0].rows.find((r) => r.code === code);
  assert.deepEqual([row2.rt_s, row2.rt_reqs], [50, 2], "跑第二遍把同一批又记了一次");
  const y2 = await h.stats({ day: YDAY });
  assert.deepEqual([y2.days[0].rows[0].rt_s, y2.days[0].rows[0].rt_reqs], [12, 1]);
  const cur = await h.cursor();
  assert.equal(cur.since_ms, T0 + 7200_000, "游标推到这一轮的 scheduledTime");
  // seen 只剩 4 条：跨午夜那条（10-16T23:50）已经早于「since - 6 h」，官方再也不会把它放进窗口 -> 裁掉
  assert.equal(cur.seen, 4);
}));

// ---------- 12. Cron：上游挂了不动游标 ----------
test("S12 Cron：日志接口 500 -> 游标不动、什么都不记；下一轮重试补上", () => {
  const opts = { logs: (region, params, n) => (n === 1 ? J(500, { error_type: "internal_error" })
    : { usage_logs: [logRec({ uuid: "u-1", name: "amy", end: "2026-10-17T08:10:00Z", ms: 45000 })], next_page_cursor: null }) };
  return withHarness(opts, async (h) => {
    const { code } = await newCode(h, "amy");
    await h.cron(T0 + 3600_000);
    assert.equal((await h.cursor()).since_ms, 0, "上游挂了游标不许动");
    assert.equal((await h.stats({ day: TODAY })).days[0].rows.length, 0, "也不许记");
    const fail = JSON.parse(h.logged.at(-1));
    assert.deepEqual([fail.soniox, fail.status], ["usage_failed", 500]);
    await h.cron(T0 + 7200_000);
    const row = (await h.stats({ day: TODAY })).days[0].rows.find((r) => r.code === code);
    assert.deepEqual([row.rt_s, row.rt_reqs], [45, 1]);
    assert.equal((await h.cursor()).since_ms, T0 + 7200_000);
  });
});

// ---------- 13. Cron：分页 ----------
test("S13 Cron：翻页合起来算一次（带 cursor 再问一页）", () => withHarness({
  logs: (region, params) => (params.cursor
    ? { usage_logs: [logRec({ uuid: "p-2", name: "amy", end: "2026-10-17T08:50:00Z", ms: 11000 })], next_page_cursor: null }
    : { usage_logs: [logRec({ uuid: "p-1", name: "amy", end: "2026-10-17T08:20:00Z", ms: 9000 })], next_page_cursor: "cur-1" }),
}, async (h) => {
  const { code } = await newCode(h, "amy");
  await h.cron(T0 + 3600_000);
  assert.deepEqual(h.calls.logs.map((c) => c.params.cursor ?? null), [null, "cur-1"]);
  assert.equal(h.calls.logs[0].params.limit, "100");
  assert.equal(h.calls.logs[0].params.end_time, new Date(T0 + 3600_000).toISOString());
  const row = (await h.stats({ day: TODAY })).days[0].rows.find((r) => r.code === code);
  assert.deepEqual([row.rt_s, row.rt_reqs], [20, 2]);
}));

test("S13b Cron：一直有下一页（拉满 5 页）-> 游标停在已拉到的最后一条，别把没拉到的甩在窗口外", () => withHarness({
  // 每页一条，永远给下一页 -> cron 拉满 5 页就停
  logs: (region, params) => {
    const n = Number(params.cursor ?? 0) + 1;
    const end = new Date(Date.parse("2026-10-17T08:00:00Z") + n * 60000).toISOString();
    return { usage_logs: [logRec({ uuid: `m-${n}`, name: "amy", end, ms: 1000 })], next_page_cursor: String(n) };
  },
}, async (h) => {
  const { code } = await newCode(h, "amy");
  await h.cron(T0 + 3600_000);
  assert.equal(h.calls.logs.length, 5, "PAGE_CAP = 5 页就停");
  assert.deepEqual(h.calls.logs.map((c) => c.params.cursor ?? "-"), ["-", "1", "2", "3", "4"], "每页都带着上一页的 cursor 问");
  const row = (await h.stats({ day: TODAY })).days[0].rows.find((r) => r.code === code);
  assert.deepEqual([row.rt_s, row.rt_reqs], [5, 5], "五页五条各 1 s");
  const cur = await h.cursor();
  assert.equal(cur.since_ms, Date.parse("2026-10-17T08:05:00Z"), "游标 = 最后一条的 end_time，不是 scheduledTime");
  assert.ok(cur.since_ms < T0 + 3600_000);
}));

// ---------- 14. Cron：两个区各拉各的 ----------
test("S14 Cron：日本区没配 key 就跳过；配了就各拉各的（各自的 key 与游标）", () => withHarness({
  logs: (region) => ({ usage_logs: [
    logRec({ uuid: `${region}-1`, name: region === "jp" ? "ken" : "amy", end: "2026-10-17T08:10:00Z", ms: 25000 }),
  ], next_page_cursor: null }),
}, async (h) => {
  const { code: amy } = await newCode(h, "amy");
  const { code: ken } = await newCode(h, "ken");
  await h.cron();
  assert.deepEqual(h.calls.logs.map((c) => c.region), ["us"], "没配 JP key 不该去拉日本区");
  const jp = { ...h.env, SONIOX_KEY_JP: "soniox-jp-key" };
  await h.cron(T0 + 3600_000, jp);
  // 两个区在这一轮里并发跑，前后不保证 -> 按区计数
  const byRegion = h.calls.logs.reduce((a, c) => a.set(c.region, (a.get(c.region) ?? 0) + 1), new Map());
  assert.deepEqual([...byRegion].sort(), [["jp", 1], ["us", 2]]);
  const jpCalls = h.calls.logs.filter((c) => c.region === "jp");
  assert.ok(jpCalls.every((c) => c.auth === "Bearer soniox-jp-key"));
  assert.ok(h.calls.logs.filter((c) => c.region === "us").every((c) => c.auth === "Bearer soniox-us-key"));
  const row = (await h.stats({ day: TODAY })).days[0].rows;
  assert.deepEqual([row.find((r) => r.code === amy).rt_s, row.find((r) => r.code === ken).rt_s], [25, 25]);
  assert.deepEqual([(await h.cursor("us")).since_ms, (await h.cursor("jp")).since_ms], [T0 + 3600_000, T0 + 3600_000]);
}));

// ---------- 15. 老记录没有 rt_s ----------
test("S15 老记录（没 rt_s / rt_reqs 两键）照读照写：老字段不丢，新字段是数字不是 null", () => withHarness({
  logs: () => ({ usage_logs: [logRec({ uuid: "u-1", name: "old", end: "2026-10-17T08:10:00Z", ms: 21000 })], next_page_cursor: null }),
}, async (h) => {
  const { code } = await newCode(h, "old");
  h.pass.storage.m.set(`use:${TODAY}:${code}`, {   // 2026-10-17 之前的形状
    asr_s: 30, asr_reqs: 2, llm_reqs: 0, tokens_in: 0, tokens_out: 0, cache_hit: 0, jev_reqs: 0, jev_q: 0,
  });
  await h.cron();
  const stored = h.pass.storage.m.get(`use:${TODAY}:${code}`);
  assert.deepEqual([stored.rt_s, stored.rt_reqs], [21, 1]);
  assert.deepEqual([stored.asr_s, stored.asr_reqs], [30, 2], "老字段丢了 / 被覆盖");
  const row = (await h.stats({ day: TODAY })).days[0].rows.find((r) => r.code === code);
  assert.deepEqual([row.asr_s, row.rt_s], [30, 21]);
  // 老码没有 daily_rt_s -> 不限（不然老码一升级就全被 429）
  assert.equal((await h.call(h.keyReq(`Bearer ${code}`))).status, 200);
}));

// ---------- 16. 别的路没被改坏 ----------
test("S16 转写照旧 200：新路由没动现有行为（同一道 PER_IP / PER_INSTALL 之后分岔）", () => withHarness({}, async (h) => {
  const j = await (await h.call(h.asrReq("Bearer tok-public"))).json();
  assert.equal(j.provider, "groq");
  assert.equal((await h.call(h.asrReq("Bearer cl-x-zz"))).status, 403);
}));
