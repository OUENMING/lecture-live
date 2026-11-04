// Jev 上游选路判据 —— 中国区访客走 CommandCode、其余走官方，换道失败要退回官方。
// 直接 import src 真源码；两家上游都换成假 fetch，Passes 用最小替身。
//
//   node --test server/asr-worker/test-jev.mjs
//
// ⚠️ 不验证真实网络（CommandCode 从 Cloudflare 出口能不能调通、UA 是否够），
//    这里钉的是 Worker 自己的选路 / 退回 / 记账。真上游要在部署后拿通行码实测。
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { _leaseForTests } from "./src/handler.js";

const CODE = "cl-amy-0123456789abcdef";
const INSTALL = "0123456789abcdef0123456789abcdef";
const J = (status, obj) => new Response(JSON.stringify(obj), {
  status, headers: { "content-type": "application/json" },
});
const OK_BODY = { model: "jev-1.13.0", answers: { S1: { type: "noul", noul: 0.5 } },
                  usage: { input_tokens: 10, output_tokens: 2 } };

const BODY = () => ({
  model: "jev-1.13.0",
  state: "S1: The sky is blue.",
  questions: { S1: { type: "noul", instructions: "Look at line S1 only." } },
});

function harness({ official, commandcode, env: extraEnv = {} } = {}) {
  _leaseForTests(0);
  const calls = { official: [], commandcode: [], usage: [], ae: [] };
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
    if (u.startsWith("https://api.typesafe.ai/")) {
      calls.official.push({ headers: init.headers, body });
      return official ? official(body, calls.official.length) : J(200, OK_BODY);
    }
    if (u.startsWith("https://api.commandcode.ai/")) {
      calls.commandcode.push({ headers: init.headers, body });
      return commandcode ? commandcode(body, calls.commandcode.length) : J(200, OK_BODY);
    }
    throw new Error("unexpected upstream " + u);
  };
  const env = {
    CLIENT_TOKEN: "tok-public",
    JEV_KEY: "apikey-official",
    PASSES: { idFromName: (n) => n, get: () => ({ fetch: passesFetch }) },
    AE: { writeDataPoint: (dp) => calls.ae.push(structuredClone(dp)) },
    ...extraEnv,
  };
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p) };
  const settle = () => Promise.all(pending.splice(0));
  const jevReq = ({ country = "IE", tz = "" } = {}) => {
    const headers = { authorization: `Bearer ${CODE}`, "x-classlive-install": INSTALL,
                      "content-type": "application/json" };
    if (tz) headers["x-classlive-tz"] = tz;
    const r = new Request("https://asr.bldcam.page/v1/jev", {
      method: "POST", headers, body: JSON.stringify(BODY()),
    });
    Object.defineProperty(r, "cf", { value: { country, colo: "DUB" } });
    return r;
  };
  const call = (req) => worker.fetch(req, env, ctx);
  const restore = () => { globalThis.fetch = origFetch; };
  return { env, calls, call, settle, jevReq, restore };
}

// ---------- a. 没配 CommandCode key：行为与改之前逐字一致 ----------
test("a 没配 JEV_CC_KEY -> 只打官方，请求头/体与原来一致", async () => {
  const h = harness();
  try {
    const res = await h.call(h.jevReq({ country: "CN" }));
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), OK_BODY);
    await h.settle();
    assert.equal(h.calls.commandcode.length, 0, "没配 key 不许碰 CommandCode");
    assert.equal(h.calls.official.length, 1);
    assert.deepEqual(h.calls.official[0].headers,
      { authorization: "Bearer apikey-official", "content-type": "application/json" });
    assert.equal(h.calls.official[0].body.model, "jev-1.13.0");
    assert.equal(h.calls.ae[0].blobs[3], "jev:200");
  } finally { h.restore(); }
});

// ---------- b. 中国区 + 配了 key：只打 CommandCode，UA / 模型都要对 ----------
test("b 国家 CN + 配了 JEV_CC_KEY -> 只打 CommandCode，带 UA、模型换成 typesafe/jev", async () => {
  const h = harness({ env: { JEV_CC_KEY: "user-cmdcode" } });
  try {
    const res = await h.call(h.jevReq({ country: "CN" }));
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.official.length, 0, "中国区不许再花官方 key");
    assert.equal(h.calls.commandcode.length, 1);
    const c = h.calls.commandcode[0];
    assert.equal(c.headers.authorization, "Bearer user-cmdcode");
    assert.ok(c.headers["user-agent"], "⚠️ 缺 UA 会被 CommandCode 403");
    assert.equal(c.body.model, "typesafe/jev");
    assert.deepEqual(c.body.questions, BODY().questions);
    assert.equal(h.calls.ae[0].blobs[3], "jev-cc:200");
    assert.equal(h.calls.ae[0].blobs[2], "typesafe/jev");
    assert.equal(h.calls.ae[0].doubles[6], 1, "题数照记");
  } finally { h.restore(); }
});

// ---------- c. 非中国区：配了 key 也不走 ----------
test("c IE + 配了 JEV_CC_KEY -> 仍然只打官方", async () => {
  const h = harness({ env: { JEV_CC_KEY: "user-cmdcode" } });
  try {
    const res = await h.call(h.jevReq({ country: "IE" }));
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.commandcode.length, 0);
    assert.equal(h.calls.official.length, 1);
    assert.equal(h.calls.ae[0].blobs[3], "jev:200");
  } finally { h.restore(); }
});

// ---------- d. 换道失败要退回官方，客户端仍拿到 200 ----------
test("d CommandCode 回 403 -> 同一请求退回官方，up 记两次尝试", async () => {
  const h = harness({
    commandcode: () => J(403, { error: "blocked" }),
    env: { JEV_CC_KEY: "user-cmdcode" },
  });
  try {
    const res = await h.call(h.jevReq({ country: "CN" }));
    assert.equal(res.status, 200, "换道失败不能让中国区用户整块功能哑掉");
    assert.deepEqual(await res.json(), OK_BODY);
    await h.settle();
    assert.equal(h.calls.commandcode.length, 1);
    assert.equal(h.calls.official.length, 1);
    assert.equal(h.calls.ae[0].blobs[3], "jev-cc:403,jev:200");
  } finally { h.restore(); }
});

test("d2 两家都挂 -> 把官方那条的错误原样给客户端", async () => {
  const h = harness({
    official: () => J(429, { error: "slow down" }),
    commandcode: () => J(500, { error: "boom" }),
    env: { JEV_CC_KEY: "user-cmdcode" },
  });
  try {
    const res = await h.call(h.jevReq({ country: "CN" }));
    assert.equal(res.status, 429);
    await res.text();
    await h.settle();
    assert.equal(h.calls.ae[0].blobs[3], "jev-cc:500,jev:429");
    assert.equal(h.calls.usage.length, 0, "上游全错不许记用量");
  } finally { h.restore(); }
});

// ---------- e. 挂了 VPN：国家看不出来，时区认得出来 ----------
test("e 国家 IE 但时区 Asia/Shanghai -> 走 CommandCode", async () => {
  const h = harness({ env: { JEV_CC_KEY: "user-cmdcode" } });
  try {
    const res = await h.call(h.jevReq({ country: "IE", tz: "Asia/Shanghai" }));
    assert.equal(res.status, 200);
    await res.text();
    await h.settle();
    assert.equal(h.calls.commandcode.length, 1);
    assert.equal(h.calls.official.length, 0);
  } finally { h.restore(); }
});

test("e2 时区只是形似（Europe/Dublin）不算中国区", async () => {
  const h = harness({ env: { JEV_CC_KEY: "user-cmdcode" } });
  try {
    const res = await h.call(h.jevReq({ country: "IE", tz: "Europe/Dublin" }));
    await res.text();
    await h.settle();
    assert.equal(h.calls.commandcode.length, 0);
  } finally { h.restore(); }
});

// ---------- f. 国家表可覆盖 ----------
test("f JEV_CC_COUNTRIES=CN -> HK 不再走 CommandCode", async () => {
  const h = harness({ env: { JEV_CC_KEY: "user-cmdcode", JEV_CC_COUNTRIES: "CN" } });
  try {
    const hk = await h.call(h.jevReq({ country: "HK" }));
    await hk.text();
    await h.settle();
    assert.equal(h.calls.commandcode.length, 0, "覆盖表里没有 HK");
    const cn = await h.call(h.jevReq({ country: "CN" }));
    await cn.text();
    await h.settle();
    assert.equal(h.calls.commandcode.length, 1);
  } finally { h.restore(); }
});

// ---------- g. 只有 CommandCode key 的部署 ----------
test("g 没配 JEV_KEY 但有 JEV_CC_KEY + CN -> 走 CommandCode；它挂了就把它的错误给客户端", async () => {
  const ok = harness({ env: { JEV_KEY: "", JEV_CC_KEY: "user-cmdcode" } });
  try {
    const res = await ok.call(ok.jevReq({ country: "CN" }));
    assert.equal(res.status, 200);
    await res.text();
    await ok.settle();
    assert.equal(ok.calls.official.length, 0, "没有官方 key，无处可退");
    assert.equal(ok.calls.commandcode.length, 1);
  } finally { ok.restore(); }

  const bad = harness({ commandcode: () => J(403, { error: "blocked" }),
                        env: { JEV_KEY: "", JEV_CC_KEY: "user-cmdcode" } });
  try {
    const res = await bad.call(bad.jevReq({ country: "CN" }));
    assert.equal(res.status, 403);
    await res.text();
    await bad.settle();
    assert.equal(bad.calls.official.length, 0, "没配 JEV_KEY 不许打官方");
    assert.equal(bad.calls.ae[0].blobs[3], "jev-cc:403");
  } finally { bad.restore(); }
});

test("h 两家都没配 -> 503 jev_not_configured", async () => {
  const h = harness({ env: { JEV_KEY: "" } });
  try {
    const res = await h.call(h.jevReq({ country: "CN" }));
    assert.equal(res.status, 503);
    assert.deepEqual(await res.json(), { error: "jev_not_configured" });
    await h.settle();
    assert.equal(h.calls.official.length + h.calls.commandcode.length, 0);
  } finally { h.restore(); }
});
