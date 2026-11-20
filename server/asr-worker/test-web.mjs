// 网页版 P4-a 的判据：CORS 白名单（`WEB_ORIGIN`）、网页设备分账（`X-ClassLive-Client: web`）、
// 网页端 max_tokens 封顶。直接 import src 真源码；Passes 用真 PassCore + 内存 storage，上游全是假 fetch。
//
//   cd server/asr-worker && node --test test-web.mjs
//
// 钉住什么（接口草案 docs/API-web-v0-20261117.md §7、§8-2、§8-4、§8-8）：
//   1. 预检只对「来源对 + 路径在名单」，回显那一个来源、绝不用 `*`；否则维持 404
//   2. 没配 WEB_ORIGIN = 一个来源都不放行（fail-closed）
//   3. 正常响应与 4xx/5xx 都带 Allow-Origin + Vary；`/v1/transcribe`、`/v1/jev`、`/v1/admin/*` 永不带
//   4. 网页设备走 `devweb:<码>` 与独立的 2 台名额，桌面两台占满后网页仍能进
//   5. 不发 `X-ClassLive-Client` 的老请求行为逐字不变
//   6. client=web 的 max_tokens 截到 400（桌面仍是 16384）
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import worker, { _leaseForTests, _resetFastLlm, _resetRoutes, _resetCmdChat, _resetOpenCode } from "./src/handler.js";
import { PassCore } from "./src/passes-core.js";
import { QuotaCore } from "./src/quota-core.js";

const ORIGIN = "https://web.example.test";
const INSTALL = "0123456789abcdef0123456789abcdef";
const INSTALL2 = "fedcba9876543210fedcba9876543210";
const INSTALL3 = "00000000000000000000000000000003";
const INSTALL4 = "00000000000000000000000000000004";
const T0 = Date.UTC(2026, 10, 17, 9);

const J = (status, obj) => new Response(JSON.stringify(obj), {
  status, headers: { "content-type": "application/json" },
});

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

const doStub = (core) => ({
  idFromName: (n) => n,
  get: () => ({ fetch: async (u, init) => core.handle(new Request(u, init)) }),
});

function harness({ env: extraEnv = {}, deepseek } = {}) {
  _leaseForTests(0);   // 短租关掉：每次请求都真问一次 Passes（分账判据要看得见每次登记）
  _resetFastLlm(); _resetRoutes(); _resetCmdChat(); _resetOpenCode();
  const now = T0;
  const pass = new PassCore(memStorage(), () => now);
  const quota = new QuotaCore(memStorage(), () => now);
  const calls = { deepseek: [], ae: [] };
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.startsWith("https://api.deepseek.com/")) {
      calls.deepseek.push({ headers: init.headers, body: JSON.parse(init.body) });
      return deepseek ? deepseek(JSON.parse(init.body))
        : J(200, { choices: [{ message: { content: "{}" } }], usage: { prompt_tokens: 10, completion_tokens: 2 } });
    }
    throw new Error("unexpected upstream " + u);
  };
  const env = {
    CLIENT_TOKEN: "tok-public", DEEPSEEK_KEY: "sk-owen", ADMIN_TOKEN: "adm",
    WEB_ORIGIN: ORIGIN,
    PER_INSTALL: { limit: async () => ({ success: true }) },
    PER_IP: { limit: async () => ({ success: true }) },
    QUOTA: doStub(quota), PASSES: doStub(pass),
    AE: { writeDataPoint: (dp) => calls.ae.push(structuredClone(dp)) },
    ...extraEnv,
  };
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p) };
  const send = (req) => worker.fetch(req, env, ctx);
  // 通用构造：headers 里的 undefined 自动丢掉（模拟「老客户端不发这个头」）
  const req = (path, { method = "POST", headers = {}, body } = {}) => {
    const h = {};
    for (const [k, v] of Object.entries(headers)) if (v !== undefined && v !== null) h[k] = v;
    return new Request(`https://asr.bldcam.page${path}`, {
      method, headers: h, body: method === "GET" || method === "OPTIONS" ? undefined : body,
    });
  };
  const chatReq = ({ code = "cl-amy-0123456789abcdef", install = INSTALL, web, origin = ORIGIN, body = chatBody() } = {}) =>
    req("/v1/chat/completions", {
      headers: {
        authorization: `Bearer ${code}`, "x-classlive-install": install,
        "content-type": "application/json", origin, "x-classlive-client": web,
      },
      body: JSON.stringify(body),
    });
  const asrReq = ({ token = "tok-public", install = INSTALL, origin = ORIGIN } = {}) =>
    req("/v1/transcribe", {
      headers: { authorization: `Bearer ${token}`, "x-classlive-install": install, origin },
      body: new Uint8Array(100),
    });
  const adminReq = (name, body = {}) => req(`/v1/admin/${name}`, {    headers: { authorization: "Bearer adm", "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const settle = () => Promise.all(pending.splice(0));
  // 真发一枚码（走 /admin/add），不要把码写死在用例里 —— 写死就永远对不上码表
  const addCode = async (body = { name: "amy" }) =>
    (await (await send(adminReq("add", body))).json()).code;
  const restore = () => { globalThis.fetch = origFetch; };
  return { env, pass, calls, req, send, chatReq, asrReq, adminReq, addCode, settle, restore };
}

// 网页请求要带白名单里的 system（P4-c 的提示词校验，见 test-web-prompt.mjs）；原文现场从 prompt_core 取，不进仓库。
const SYSTEM = (() => {
  try {
    const out = execFileSync("python3", ["-I", "scripts/gen-prompt-allow.py", "--print"], { cwd: dirname(fileURLToPath(import.meta.url)), encoding: "utf8" });
    return JSON.parse(out).prompts.SYSTEM_PROMPT_CLOUD;
  } catch { return "x"; }
})();
const chatBody = (extra = {}) => ({ model: "deepseek-flash", messages: [{ role: "system", content: SYSTEM }, { role: "user", content: "x" }], max_tokens: 50, ...extra });

const CORS_HEADERS = [
  "access-control-allow-origin", "access-control-allow-methods", "access-control-allow-headers",
  "access-control-max-age", "vary",
];

// ---------- 1. 预检：来源对 + 路径在名单 ----------
test("W1 预检：来源对 + 名单路径 -> 204，回显那一个来源（不是 *），带方法 / 头 / Max-Age / Vary", async () => {
  const h = harness();
  try {
    for (const path of ["/v1/soniox/key", "/v1/chat/completions", "/v1/web/session", "/v1/web/sessions"]) {
      const res = await h.send(h.req(path, { method: "OPTIONS", headers: { origin: ORIGIN } }));
      assert.equal(res.status, 204, path);
      assert.equal(res.headers.get("access-control-allow-origin"), ORIGIN, path);
      // /v1/web/* 这一族多放 GET / PUT / PATCH / DELETE（账本接口，PATCH = 改课名，W18）；两条代付路径仍只 POST
      assert.equal(res.headers.get("access-control-allow-methods"),
        path.startsWith("/v1/web/") ? "GET, POST, PUT, PATCH, DELETE, OPTIONS" : "POST, OPTIONS", path);
      assert.equal(res.headers.get("access-control-allow-headers"),
        "Authorization, Content-Type, X-ClassLive-Install, X-ClassLive-Version, "
        + "X-ClassLive-Tz, X-ClassLive-Call, X-ClassLive-Client, Idempotency-Key", path);
      assert.equal(res.headers.get("access-control-max-age"), "600", path);
      assert.equal(res.headers.get("vary"), "Origin", path);
    }
    assert.equal(h.calls.ae.length, 0, "预检不进事件");
    assert.equal(h.calls.deepseek.length, 0, "预检不碰上游");
  } finally { h.restore(); }
});

// ---------- 2. 预检：来源不对 / 没配 WEB_ORIGIN ----------
test("W2 预检：来源不对 / 没带 Origin / 没配 WEB_ORIGIN -> 维持原来的 404，一个 CORS 头都没有", async () => {
  const h = harness();
  try {
    for (const origin of ["https://evil.example", `${ORIGIN}.evil`, "null", undefined]) {
      const res = await h.send(h.req("/v1/chat/completions", { method: "OPTIONS", headers: { origin } }));
      assert.equal(res.status, 404, String(origin));
      assert.equal(await res.text(), "not found");
      for (const k of CORS_HEADERS) assert.equal(res.headers.get(k), null, `${k} / ${origin}`);
    }
    const { WEB_ORIGIN, ...noOrigin } = h.env;
    assert.equal(WEB_ORIGIN, ORIGIN);
    const res = await worker.fetch(h.req("/v1/chat/completions", { method: "OPTIONS", headers: { origin: ORIGIN } }), noOrigin, {});
    assert.equal(res.status, 404, "fail-closed：没配就一个来源都不放行");
    assert.equal(res.headers.get("access-control-allow-origin"), null);
    assert.equal(noOrigin.WEB_ORIGIN, undefined);
  } finally { h.restore(); }
});

// ---------- 3. 预检：路径不在名单 ----------
test("W3 预检：路径不在名单（transcribe / jev / admin / 未知）-> 404，不因为来源对就开口子", async () => {
  const h = harness();
  try {
    for (const path of ["/v1/transcribe", "/v1/jev", "/v1/admin/list", "/v1/other", "/v1/webx/session"]) {
      const res = await h.send(h.req(path, { method: "OPTIONS", headers: { origin: ORIGIN } }));
      assert.equal(res.status, 404, path);
      assert.equal(await res.text(), "not found", path);
      for (const k of CORS_HEADERS) assert.equal(res.headers.get(k), null, `${k} / ${path}`);
    }
    assert.equal(h.calls.deepseek.length, 0, "名单外的预检也不会去碰上游");
  } finally { h.restore(); }
});

// ---------- 4. 正常响应带 Allow-Origin + Vary ----------
test("W4 正常 200：/v1/chat/completions 与 /v1/soniox/key 带 Allow-Origin + Vary；来源不对时不带", async () => {
  const h = harness();
  try {
    await h.pass.add({ name: "amy" });
    const code = await h.addCode();
    const ok = await h.send(h.chatReq({ code }));
    assert.equal(ok.status, 200);
    await ok.text();
    assert.equal(ok.headers.get("access-control-allow-origin"), ORIGIN);
    assert.match(ok.headers.get("vary"), /Origin/);

    // 来源不对：照样服务（CORS 只是浏览器侧的门），但一个头都不给
    const off = await h.send(h.chatReq({ code, origin: "https://evil.example" }));
    assert.equal(off.status, 200);
    await off.text();
    assert.equal(off.headers.get("access-control-allow-origin"), null);
    assert.equal(off.headers.get("vary"), null);

    // /v1/soniox/key 在名单里：没配 SONIOX_KEY 的 503 也要带动
    const sx = await h.send(h.req("/v1/soniox/key", {
      headers: { authorization: `Bearer ${code}`, "x-classlive-install": INSTALL, origin: ORIGIN },
    }));
    assert.equal(sx.status, 503);
    assert.equal((await sx.json()).error, "soniox_not_configured");
    assert.equal(sx.headers.get("access-control-allow-origin"), ORIGIN);
  } finally { h.restore(); }
});

// ---------- 5. 错误响应也带头 ----------
test("W5 4xx/5xx：坏码 403 / 坏 install 400 / 没 auth 401 / 坏 body 400 都带 Allow-Origin", async () => {
  const h = harness();
  try {
    const cases = [
      // 未知码：403 是 JSON，且带头
      [h.chatReq({ code: "cl-nobody-0123456789abcdef" }), 403, "{invalid}"],
      [h.req("/v1/chat/completions", { headers: { origin: ORIGIN }, body: JSON.stringify(chatBody()) }), 401, "unauthorized"],
    ];
    for (const [request, status, text] of cases) {
      const res = await h.send(request);
      assert.equal(res.status, status);
      const got = await res.text();
      if (text === "{invalid}") assert.equal(JSON.parse(got).error, "unknown");
      else assert.equal(got, text);
      assert.equal(res.headers.get("access-control-allow-origin"), ORIGIN, `${status} 也要带头`);
      assert.match(res.headers.get("vary"), /Origin/);
    }
    const code = await h.addCode();
    const badInstall = await h.send(h.chatReq({ code, install: "XYZ" }));
    assert.equal(badInstall.status, 400);
    assert.equal(await badInstall.text(), "bad install id");
    assert.equal(badInstall.headers.get("access-control-allow-origin"), ORIGIN);
    // 坏 JSON：过了鉴权与设备闸，倒在 chat() 的 JSON 解析
    const bad = await h.send(h.req("/v1/chat/completions", {
      headers: { authorization: `Bearer ${code}`, "x-classlive-install": INSTALL,
                 "content-type": "application/json", origin: ORIGIN },
      body: "{not json",
    }));
    assert.equal(bad.status, 400);
    assert.equal((await bad.json()).error, "bad_json");
    assert.equal(bad.headers.get("access-control-allow-origin"), ORIGIN);
  } finally { h.restore(); }
});

// ---------- 6. 老路由与老行为不变 ----------
test("W6 老路由不加 CORS 头：transcribe / jev / admin 即使来源对也不带；GET 与未知路径仍 404", async () => {
  const h = harness();
  try {
    const asr = await h.send(h.asrReq());
    assert.equal(asr.headers.get("access-control-allow-origin"), null, "/v1/transcribe 绝不给 CORS");
    await asr.body?.cancel();

    const jev = await h.send(h.req("/v1/jev", {
      headers: { authorization: "Bearer cl-nobody-0123456789abcdef", "x-classlive-install": INSTALL, origin: ORIGIN },
      body: "{}",
    }));
    assert.equal(jev.status, 403);
    assert.equal(jev.headers.get("access-control-allow-origin"), null, "/v1/jev 绝不给 CORS");

    const adm = await h.send(h.adminReq("list"));
    assert.equal(adm.status, 200);
    await adm.text();
    assert.equal(adm.headers.get("access-control-allow-origin"), null, "/v1/admin/* 绝不给 CORS");

    const g = await h.send(h.req("/v1/soniox/key", { method: "GET", headers: { origin: ORIGIN } }));
    assert.equal(g.status, 404);
    assert.equal(await g.text(), "not found");
    assert.equal(g.headers.get("access-control-allow-origin"), null);

    const other = await h.send(h.req("/v1/other", { headers: { origin: ORIGIN }, body: "{}" }));
    assert.equal(other.status, 404);
    assert.equal(await other.text(), "not found");
  } finally { h.restore(); }
});

// ---------- 7. 网页设备分账 ----------
test("W7 合计名额：桌面 + 网页一共 max_devices（2）台（Owen 11-20）；已登记的照用；调大名额才放新的", async () => {
  const h = harness();
  try {
    const created = await (await h.send(h.adminReq("add", { name: "amy" }))).json();
    const code = created.code;
    assert.equal(created.max_devices, 2);
    assert.equal(created.max_web_devices, undefined);

    const go = async (install, web) => {
      const res = await h.send(h.chatReq({ code, install, web }));
      const st = res.status; const b = await res.text();
      return { st, b };
    };
    assert.equal((await go(INSTALL)).st, 200, "桌面 1");
    assert.equal((await go(INSTALL2, "web")).st, 200, "网页 1（合计第 2 台）");
    const w3 = await go(INSTALL3, "web");
    assert.equal(w3.st, 403, "网页第 2 台 = 合计第 3 台，拒");
    assert.equal(JSON.parse(w3.b).error, "devices");
    assert.equal((await go(INSTALL4)).st, 403, "桌面第 2 台 = 合计第 3 台，也拒");
    assert.equal((await go(INSTALL)).st, 200, "已登记的桌面照用");
    assert.equal((await go(INSTALL2, "web")).st, 200, "已登记的网页照用");

    let dv = await (await h.send(h.adminReq("devices", { code }))).json();
    assert.equal(dv.devices.length, 1);
    assert.equal(dv.web_devices.length, 1);

    // 调到 3：再放 1 台（不论桌面还是网页）
    assert.equal((await h.send(h.adminReq("limit", { code, max_devices: 3 }))).status, 200);
    assert.equal((await go(INSTALL3, "web")).st, 200);
    assert.equal((await go(INSTALL4)).st, 403, "第 4 台拒");
  } finally { h.restore(); }
});

// ---------- 8. 无头 / 其它值 = 桌面，行为不变 ----------
test("W8 X-ClassLive-Client 只认 web：缺省 / desktop / 大小写不同的值都走桌面表", async () => {
  const h = harness();
  try {
    const { code } = await (await h.send(h.adminReq("add", { name: "amy" }))).json();
    // 只认字面量 `web`：大小写不同 / 别的值 / 没有这个头，全部按桌面算。
    // （`"web "` 不列进来：HTTP 头会削掉尾部空白，到了 Worker 就是 `web` —— 平台行为，不是判据。）
    for (const web of [undefined, "desktop", "WEB", "Web", "webx", "web2"]) {
      assert.equal((await h.send(h.chatReq({ code, install: INSTALL, web }))).status, 200, String(web));
    }
    const dv = await (await h.send(h.adminReq("devices", { code }))).json();
    assert.deepEqual(dv.devices.map((d) => d.id), [INSTALL.slice(0, 6)], "只有一台设备，全落在桌面表");
    assert.deepEqual(dv.web_devices, []);
    // 桌面名额照旧 2 台
    assert.equal((await h.send(h.chatReq({ code, install: INSTALL2 }))).status, 200);
    assert.equal((await h.send(h.chatReq({ code, install: INSTALL3 }))).status, 403);
    // devweb 只在网页请求作用下出现（合计名额已满 2 台 → 先调到 3）
    assert.equal((await h.send(h.chatReq({ code, install: INSTALL4, web: "web" }))).status, 403, "合计满了");
    assert.equal((await h.send(h.adminReq("limit", { code, max_devices: 3 }))).status, 200);
    assert.equal((await h.send(h.chatReq({ code, install: INSTALL4, web: "web" }))).status, 200);
    const dv2 = await (await h.send(h.adminReq("devices", { code }))).json();
    assert.deepEqual(dv2.web_devices.map((d) => d.id), [INSTALL4.slice(0, 6)]);
  } finally { h.restore(); }
});

// ---------- 9. 网页端 max_tokens 封顶 ----------
test("W9 max_tokens：client=web 截到 400（网页只做实时翻译），桌面仍是 16384", async () => {
  const h = harness();
  try {
    const { code } = await (await h.send(h.adminReq("add", { name: "amy" }))).json();
    const sent = async (web, extra = {}) => {
      const res = await h.send(h.chatReq({ code, install: INSTALL, web, body: chatBody(extra) }));
      assert.equal(res.status, 200);
      await res.text();
      return h.calls.deepseek.at(-1).body.max_tokens;
    };
    assert.equal(await sent("web", { max_tokens: 999999 }), 400, "网页超上限 -> 400");
    assert.equal(await sent(undefined, { max_tokens: 999999 }), 16384, "桌面行为不变");
    assert.equal(await sent("web", { max_tokens: 50 }), 50, "网页小请求原样");
    assert.equal(await sent("web", { max_tokens: 5000 }), 400);
    assert.equal(await sent("web", { max_tokens: undefined }), 400, "缺字段时也按网页上限兜底");
    assert.equal(await sent(undefined, { max_tokens: undefined }), 16384);
  } finally { h.restore(); }
});

// ---------- 10. 分账不吃掉额度判定 ----------
test("W10 网页请求照样过额度闸：daily_tokens 用完 -> 429 quota_exceeded（与桌面同一格用量）", async () => {
  const h = harness();
  try {
    const { code } = await (await h.send(h.adminReq("add", { name: "amy" }))).json();
    // 先让桌面那台登个记并记一笔用量（假上游报 10 + 2 = 12 tokens）
    const first = await h.send(h.chatReq({ code }));
    assert.equal(first.status, 200);
    await first.text();
    await h.settle();
    const lim = await (await h.send(h.adminReq("limit", { code, daily_tokens: 10 }))).json();
    assert.equal(lim.daily_tokens, 10, "已用 12 tokens -> 上限 10 已经超了");
    // 网页另开一台：额度是**同一格**，所以照样被拒
    const denied = await h.send(h.chatReq({ code, web: "web", install: INSTALL2 }));
    assert.equal(denied.status, 429);
    assert.equal((await denied.json()).error, "quota_exceeded");
    assert.equal(denied.headers.get("access-control-allow-origin"), ORIGIN, "限流响应也带头");
    const dv = await (await h.send(h.adminReq("devices", { code }))).json();
    assert.deepEqual(dv.web_devices, [], "额度拒掉的请求不登记网页设备");
  } finally { h.restore(); }
});

test("W-DEV 绑定时记下设备是什么（浏览器 / 系统 / 手机），admin devices 回传；不存完整 UA", async () => {
  const h = harness();
  try {
    const { code } = await (await h.send(h.adminReq("add", { name: "amy" }))).json();
    const r = h.chatReq({ code, web: "web" });
    const UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1";
    const hdr = new Headers(r.headers); hdr.set("user-agent", UA);
    const res = await h.send(new Request(r, { headers: hdr }));
    assert.equal(res.status, 200); await res.text();
    const dv = await (await h.send(h.adminReq("devices", { code }))).json();
    assert.deepEqual(dv.web_devices[0].info, { os: "iOS", br: "Safari 18", mobile: true, ver: "", country: "" });
    assert.ok(!JSON.stringify(dv).includes("AppleWebKit"), "完整 UA 不落盘");
  } finally { h.restore(); }
});
