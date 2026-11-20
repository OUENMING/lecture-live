// W19 判据：术语上云 `GET/PUT /v1/web/terms` + `GET /v1/web/terms/shared`（CONTRACT-terms.md、文档 §3.9）。
// 直接 import 真源码；DO 用真的 WebTerms / WebTermsPool 类 + 内存 storage，上游一条都不碰。
//
//   cd server/asr-worker && node --test test-web-terms.mjs
//
// 钉住什么：
//   1. PUT→GET 读回（账本真的写了，不是只回声）；幂等；空 text 删掉
//   2. 别的码读不到我的；各种 bad_body；预检带 PUT
//   3. 课号判定：ECON10770 / econ 10770 进池，「微观经济学」不进
//   4. 池：1 枚码 → shared 空；2 枚码写同词 → users=2；一枚撤销 / share=false → 掉回 1 不出现
//   5. 大小写 / 空白不同算同词；≥2 过滤、users 降序、字母序、上限 200
//   6. W24 三层池：course / subject（同前缀的其它课）/ field（同大类的其它前缀，≥3 码）；
//      跨层去重保留更近的层、tier 顺序、未知前缀只有 course 层、三层各自上限
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "./src/handler.js";
import { PassCore } from "./src/passes-core.js";
import { QuotaCore } from "./src/quota-core.js";
import { WebTerms, WebTermsPool } from "./src/web-terms.js";
import {
  SHARED_FIELD_MAX, SHARED_LIST_MAX, SHARED_MAX, SHARED_SUBJECT_MAX, SHARED_TOTAL_MAX,
  TERMS_MAX_LINES, TERMS_MAX_CHARS, TERM_MAX_LEN,
  checkPut, courseKeyOf, fieldOf, mergeTiers, parseTermsText, sharedTerms, subjectOf, termKey,
} from "./src/web-terms-core.js";

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

// 一个命名空间 = 一组按名字分片的 DO 实例（真 Worker 里由平台做，这里用 Map 模拟）
function doNS(Cls, env) {
  const instances = new Map();
  return {
    idFromName: (n) => n,
    get: (n) => {
      if (!instances.has(n)) instances.set(n, new Cls({ storage: memStorage() }, env));
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
  const env = {
    CLIENT_TOKEN: "tok-public", ADMIN_TOKEN: "adm", WEB_ORIGIN: ORIGIN,
    PER_INSTALL: { limit: async () => ({ success: true }) },
    PER_IP: { limit: async () => ({ success: true }) },
    QUOTA: doStub(new QuotaCore(memStorage(), () => now)),
    PASSES: doStub(new PassCore(memStorage(), () => now)),
  };
  env.WEB_TERMS = doNS(WebTerms, env);
  env.WEB_TERMS_POOL = doNS(WebTermsPool, env);
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
  const put = (code, course, body) =>
    send(web(`/v1/web/terms/${encodeURIComponent(course)}`, { code, method: "PUT", body }));
  const all = (code) => send(web("/v1/web/terms", { code, method: "GET" }));
  const shared = (code, course) =>
    send(web(`/v1/web/terms/shared?course=${encodeURIComponent(course)}`, { code, method: "GET" }));
  return { env, send, req, web, addCode, put, all, shared };
}

// ---------- 1. 纯逻辑 ----------
test("R1 纯逻辑 parseTermsText：拆行 / trim / 去空行 / 精确去重；控制字符去掉", () => {
  const r = parseTermsText("  Linear Algebra \n\nlinear algebra\n\n  game\u0000 theory \r\n");
  assert.deepEqual(r.lines, ["Linear Algebra", "linear algebra", "game theory"], "个人术语按精确字面去重，控字符去掉");
  assert.equal(r.text, "Linear Algebra\nlinear algebra\ngame theory");
  assert.deepEqual(parseTermsText("").lines, []);
  // 上限：整段 20000 字符 / 去重后 500 条 / 单条 80 字
  assert.equal(parseTermsText("x".repeat(TERMS_MAX_CHARS)).error, "bad_body");
  assert.equal(parseTermsText("y".repeat(TERM_MAX_LEN + 1)).error, "bad_body");
  const many = Array.from({ length: TERMS_MAX_LINES + 1 }, (_, i) => `t${i}`).join("\n");
  assert.deepEqual(parseTermsText(many), { error: "bad_body", detail: "lines" });
  assert.equal(parseTermsText(Array.from({ length: TERMS_MAX_LINES }, (_, i) => `t${i}`).join("\n")).lines.length,
    TERMS_MAX_LINES, "正好 500 条收");
});

test("R2 纯逻辑 checkPut：课名与开课同一套；share 缺省 false；text / share 类型不对都 400", () => {
  assert.deepEqual(checkPut({ text: "a", share: true }, " 微观经济学 (ECON10770) "),
    { course: "微观经济学 (ECON10770)", share: true, text: "a", lines: ["a"] });
  assert.equal(checkPut({ text: "a" }, "ECON10770").share, false, "缺省不共享（保守）");
  for (const [body, course] of [
    [{ text: "a" }, ""], [{ text: "a" }, "   "], [{ text: "a" }, "x".repeat(81)],
    [{}, "ECON10770"], [{ text: 1 }, "ECON10770"], [{ text: "a", share: "yes" }, "ECON10770"],
    [null, "ECON10770"], [[], "ECON10770"],
  ]) {
    assert.equal(checkPut(body, course).error, "bad_body", JSON.stringify(body) + String(course));
  }
});

test("R3 纯逻辑 courseKeyOf：只认课号（去空格转大写）；自由文字课名 null", () => {
  for (const [raw, want] of [
    ["ECON10770", "ECON10770"], ["econ 10770", "ECON10770"], [" Econ 10770 ", "ECON10770"],
    ["CS1010", "CS1010"], ["ECON10770A", "ECON10770A"], ["ECON1077", "ECON1077"],  // 4 位课号也认（契约 \d{4,5}）
    ["微观经济学", null], ["微观经济学 (ECON10770)", null], ["E10770", null], ["", null], ["10770", null],
    ["ECON107700", null], ["ECON-10770", null],
  ]) {
    assert.equal(courseKeyOf(raw), want, raw);
  }
  assert.equal(termKey("  Game   Theory "), "game theory");
});

test("R4 纯逻辑 sharedTerms：同一枚码重复只算一次；≥2 过滤；users 降序、同数按字母序；返回首次写法", () => {
  const a = { hash: "a".repeat(16) }, b = { hash: "b".repeat(16) }, c = { hash: "c".repeat(16) };
  const out = sharedTerms([
    { ...a, text: "Game Theory\nlinear algebra\ngame  theory" },
    { ...c, text: "game theory\nLinear Algebra\nalpha" },
    { ...b, text: "GAME THEORY\nlinear algebra\nbeta" },
  ]);
  assert.deepEqual(out, [
    { term: "Game Theory", users: 3 },        // 大小写 / 空白不同算同词，返回第一次出现的写法
    { term: "linear algebra", users: 3 },
    { term: "alpha", users: 1 },
    { term: "beta", users: 1 },
  ].filter((x) => x.users >= 2));
  // 全部词只有 1 人写过 -> 空
  assert.deepEqual(sharedTerms([{ ...a, text: "solo" }, { ...b, text: "other" }]), []);
  // 上限 200：201 个词都被 2 枚码写过 -> 只回 200，且按字母序取前 200
  const words = Array.from({ length: SHARED_MAX + 1 }, (_, i) => `w${String(i).padStart(4, "0")}`);
  const big = sharedTerms([{ ...a, text: words.join("\n") }, { ...b, text: [...words].reverse().join("\n") }]);
  assert.equal(big.length, SHARED_MAX);
  assert.equal(big[0].term, "w0000");
  assert.equal(big.at(-1).term, "w0199");
});

// ---------- 2. HTTP：个人术语 ----------
test("R5 PUT→GET 读回：课程表 / text / updated_at 都在；幂等（同 text 不刷新 updated_at）", async () => {
  const h = harness();
  const code = await h.addCode();
  const r = await h.put(code, "微观经济学", { text: " 边际效用 \n\n边际效用\n 机会成本 ", share: false });
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.course, "微观经济学");
  assert.equal(typeof j.updated_at, "number");

  const got = await (await h.all(code)).json();
  assert.deepEqual(Object.keys(got.courses), ["微观经济学"]);
  assert.equal(got.courses["微观经济学"].text, "边际效用\n机会成本", "拆行去重后原样存");
  assert.equal(got.courses["微观经济学"].updated_at, j.updated_at);

  const again = await (await h.put(code, "微观经济学", { text: "边际效用\n机会成本", share: false })).json();
  assert.equal(again.updated_at, j.updated_at, "同样的 text 重放不刷新 updated_at");

  // 改内容 -> 新时刻
  const changed = await (await h.put(code, "微观经济学", { text: "边际效用", share: false })).json();
  assert.ok(changed.updated_at >= j.updated_at);
  assert.equal((await (await h.all(code)).json()).courses["微观经济学"].text, "边际效用");
});

test("R6 空 text = 删掉这门课（读回没有它）；删一个不存在的也是 200", async () => {
  const h = harness();
  const code = await h.addCode();
  await h.put(code, "A 课", { text: "甲" });
  await h.put(code, "B 课", { text: "乙" });
  const del = await h.put(code, "A 课", { text: "", share: false });
  assert.equal(del.status, 200);
  const got = await (await h.all(code)).json();
  assert.deepEqual(Object.keys(got.courses), ["B 课"], "只剩另一门");
  const again = await h.put(code, "A 课", { text: "" });
  assert.equal(again.status, 200, "幂等：删不存在的也 200");
});

test("R7 跨码隔离：B 码读不到 A 码的术语；同一个课名各写各的", async () => {
  const h = harness();
  const a = await h.addCode("amy");
  const b = await h.addCode("bob");
  await h.put(a, "同一门课", { text: "A 的词" });
  await h.put(b, "同一门课", { text: "B 的词" });
  assert.equal((await (await h.all(a)).json()).courses["同一门课"].text, "A 的词");
  assert.equal((await (await h.all(b)).json()).courses["同一门课"].text, "B 的词");
  const c = await h.addCode("cid");
  assert.deepEqual(await (await h.all(c)).json(), { courses: {} }, "没写过就是空表");
});

test("R8 各种 bad_body：缺 text / text 非字符串 / 超 20000 / 单条超 80 / >500 行 / share 非布尔 / JSON 坏掉", async () => {
  const h = harness();
  const code = await h.addCode();
  const cases = [
    { body: {}, detail: "text" },
    { body: { text: 42 }, detail: "text" },
    { body: { text: "x".repeat(TERMS_MAX_CHARS + 1) }, detail: "text" },
    { body: { text: "y".repeat(TERM_MAX_LEN + 1) }, detail: "term" },
    { body: { text: Array.from({ length: TERMS_MAX_LINES + 1 }, (_, i) => `t${i}`).join("\n") }, detail: "lines" },
    { body: { text: "a", share: "true" }, detail: "share" },
    { body: "{\"text\":", detail: undefined },
  ];
  for (const c of cases) {
    const r = await h.put(code, "X 课", c.body);
    assert.equal(r.status, 400, JSON.stringify(c.body).slice(0, 40));
    const j = await r.json();
    assert.equal(j.error, "bad_body");
    if (c.detail) assert.equal(j.detail, c.detail);
  }
  // 课名不合法 -> 400 bad_body detail=course
  const badCourse = await h.send(h.web("/v1/web/terms/%20%20", { code, method: "PUT", body: { text: "a" } }));
  assert.equal(badCourse.status, 400);
  assert.equal((await badCourse.json()).detail, "course");
  // 被拒的几次一个都没写进账本
  assert.deepEqual(await (await h.all(code)).json(), { courses: {} });
});

test("R8b 请求体 > 256 KiB → 413 too_large（不落盘）", async () => {
  const h = harness();
  const code = await h.addCode();
  const huge = "a".repeat(300 * 1024);
  const r = await h.put(code, "X 课", { text: huge });
  assert.equal(r.status, 413);
  assert.equal((await r.json()).error, "too_large");
  assert.deepEqual(await (await h.all(code)).json(), { courses: {} });
});

// ---------- 3. HTTP：共享池 ----------
test("R9 课号判定：ECON10770 / econ 10770 进池；「微观经济学」不进（course_key:null）", async () => {
  const h = harness();
  const a = await h.addCode("amy");
  const b = await h.addCode("bob");
  await h.put(a, "ECON10770", { text: "deadweight loss", share: true });
  await h.put(b, "econ 10770", { text: "Deadweight  Loss", share: true });
  const j = await (await h.shared(a, "econ10770")).json();
  assert.equal(j.course_key, "ECON10770");
  assert.equal(j.subject, "ECON", "课号前缀");
  assert.equal(j.field, "business", "W24：前缀映射到大类");
  assert.deepEqual(j.terms, [{ term: "deadweight loss", users: 2, tier: "course" }],
    "课名大小写 / 空格不同仍是同一门课");

  await h.put(a, "微观经济学", { text: "边际效用", share: true });
  await h.put(b, "微观经济学", { text: "边际效用", share: true });
  assert.deepEqual(await (await h.shared(a, "微观经济学")).json(),
    { course_key: null, subject: null, field: null, terms: [] }, "自由文字课名不进池");
});

test("R10 池：1 枚码写 → shared 空；2 枚码写同词 → users=2；撤销 / share=false → 掉回 1 不出现", async () => {
  const h = harness();
  const a = await h.addCode("amy");
  const b = await h.addCode("bob");
  await h.put(a, "ECON10770", { text: "externality", share: true });
  assert.deepEqual(await (await h.shared(a, "ECON10770")).json(),
    { course_key: "ECON10770", subject: "ECON", field: "business", terms: [] }, "只有我也写过 -> 不出现");

  await h.put(b, "ECON10770", { text: "externality", share: true });
  assert.deepEqual((await (await h.shared(a, "ECON10770")).json()).terms,
    [{ term: "externality", users: 2, tier: "course" }]);

  // B 改成 share=false -> B 的贡献撤掉 -> 掉回 1 人 -> 不出现
  await h.put(b, "ECON10770", { text: "externality", share: false });
  assert.deepEqual((await (await h.shared(a, "ECON10770")).json()).terms, []);

  // B 再共享回来；A 把 text 写空（删课）-> A 的贡献也没了 -> 又只剩 1 人
  await h.put(b, "ECON10770", { text: "externality", share: true });
  assert.equal((await (await h.shared(a, "ECON10770")).json()).terms.length, 1);
  await h.put(a, "ECON10770", { text: "" });
  assert.deepEqual((await (await h.shared(a, "ECON10770")).json()).terms, [], "删课同时撤掉池里的贡献");
});

test("R11 池：覆盖而不是累加；大小写 / 空白不同算同词，返回第一次出现的写法", async () => {
  const h = harness();
  const a = await h.addCode("amy");
  const b = await h.addCode("bob");
  const c = await h.addCode("cid");
  await h.put(a, "ECON10770", { text: "Game Theory", share: true });
  await h.put(b, "ECON10770", { text: "game   theory\nGAME THEORY", share: true });
  await h.put(c, "ECON10770", { text: "Game  Theory", share: true });
  assert.deepEqual((await (await h.shared(a, "ECON10770")).json()).terms,
    [{ term: "Game Theory", users: 3, tier: "course" }]);

  // A 覆盖自己的贡献（不是累加）：旧词撤掉，新词算 A 一份
  await h.put(a, "ECON10770", { text: "oligopoly", share: true });
  const j = await (await h.shared(a, "ECON10770")).json();
  assert.equal(j.terms.length, 1, "旧贡献被覆盖掉（不是累加），A 的新词只有它自己那一份所以不出现");
  assert.equal(j.terms[0].users, 2);
  // 「第一次出现的写法」按 codeHash 升序扫出来（DO 里 list 按 key 排序）；写法可能带多个空格，
  // 但比较口径恒定 —— 这里钉比较键，另在 R4 用固定顺序钉「返回首次写法」本身。
  assert.equal(termKey(j.terms[0].term), "game theory");
});

test("R12 池：排序（users 降序、同数按字母序）与「不返回谁贡献的」", async () => {
  const h = harness();
  const codes = [await h.addCode("u1"), await h.addCode("u2"), await h.addCode("u3")];
  await h.put(codes[0], "ECON10770", { text: "zeta\nalpha\nmu", share: true });
  await h.put(codes[1], "ECON10770", { text: "zeta\nalpha\nbeta", share: true });
  await h.put(codes[2], "ECON10770", { text: "zeta", share: true });
  const j = await (await h.shared(codes[0], "ECON10770")).json();
  assert.deepEqual(j.terms, [
    { term: "zeta", users: 3, tier: "course" },
    { term: "alpha", users: 2, tier: "course" },
  ], "beta 只有 1 枚码写过 -> 不出现；同数按字母序");
  assert.deepEqual(Object.keys(j).sort(), ["course_key", "field", "subject", "terms"], "只回人数，不回是谁");
  assert.deepEqual(Object.keys(j.terms[0]).sort(), ["term", "tier", "users"]);
});

test("R8c 没绑 WEB_SESSIONS 也能用（术语不吃会话 DO）；没绑 WEB_TERMS 才 503", async () => {
  const h = harness();
  const code = await h.addCode();
  const saved = h.env.WEB_SESSIONS;
  delete h.env.WEB_SESSIONS;
  assert.equal((await h.put(code, "ECON10770", { text: "x", share: true })).status, 200, "术语与会话 DO 无关");
  assert.equal((await h.all(code)).status, 200);
  h.env.WEB_SESSIONS = saved;

  const terms = h.env.WEB_TERMS;
  delete h.env.WEB_TERMS;
  const noBind = await h.all(code);
  assert.equal(noBind.status, 503);
  assert.equal((await noBind.json()).error, "upstream");
  h.env.WEB_TERMS = terms;
});

test("R12b 池：一门课最多 200 条，且 users 降序 / 同数按字母序", async () => {
  const h = harness();
  const a = await h.addCode("u1");
  const b = await h.addCode("u2");
  const words = Array.from({ length: SHARED_MAX + 5 }, (_, i) => `w${String(i).padStart(4, "0")}`);
  await h.put(a, "ECON10770", { text: words.join("\n"), share: true });
  await h.put(b, "ECON10770", { text: words.join("\n"), share: true });
  const j = await (await h.shared(a, "ECON10770")).json();
  assert.equal(j.terms.length, SHARED_MAX, "最多 200 条");
  assert.equal(j.terms[0].term, "w0000");
  assert.equal(j.terms.at(-1).term, `w${String(SHARED_MAX - 1).padStart(4, "0")}`);
});

// ---------- 4. 鉴权 / CORS / 老行为 ----------
test("R13 鉴权与 GET /v1/web/session 同一套：缺码 401 / 非 web 客户端 403 / 来源不对 403 / install 形状 400", async () => {
  const h = harness();
  const code = await h.addCode();
  await h.put(code, "ECON10770", { text: "keep", share: true });

  const noCode = await h.send(h.web("/v1/web/terms", { method: "GET" }));
  assert.equal(noCode.status, 401);
  assert.equal((await noCode.json()).error, "unauthorized");

  const desktop = await h.send(h.web("/v1/web/terms", { code, method: "GET", client: "desktop" }));
  assert.equal(desktop.status, 403);
  assert.equal((await desktop.json()).error, "forbidden");

  const badOrigin = await h.send(h.web("/v1/web/terms", { code, method: "GET", origin: "https://evil.example" }));
  assert.equal(badOrigin.status, 403);
  assert.equal((await badOrigin.json()).error, "forbidden");

  const badInstall = await h.send(h.web("/v1/web/terms", { code, method: "GET", install: "nope" }));
  assert.equal(badInstall.status, 400);
  assert.equal((await badInstall.json()).error, "bad_body");

  // 被拒后账本没变
  assert.equal((await (await h.all(code)).json()).courses["ECON10770"].text, "keep");
});

test("R14 预检：/v1/web/terms/* 带 PUT；POST /terms 与 GET /terms/:course 仍是 404（方法不对）", async () => {
  const h = harness();
  const code = await h.addCode();
  for (const path of ["/v1/web/terms", "/v1/web/terms/ECON10770", "/v1/web/terms/shared?course=ECON10770"]) {
    const res = await h.send(h.req(path, { method: "OPTIONS", headers: { origin: ORIGIN } }));
    assert.equal(res.status, 204, path);
    assert.match(res.headers.get("access-control-allow-methods") || "", /PUT/, path);
    assert.equal(res.headers.get("access-control-allow-origin"), ORIGIN, path);
  }
  assert.equal((await h.send(h.web("/v1/web/terms", { code, method: "POST", body: {} }))).status, 404);
  assert.equal((await h.send(h.web("/v1/web/terms/ECON10770", { code, method: "GET" }))).status, 404);
  assert.equal((await h.send(h.web("/v1/web/terms/shared?course=ECON10770", { code, method: "PUT", body: {} }))).status, 404);

  // 真接口调用的响应也带 CORS 头（浏览器才看得到 200）
  const put = await h.put(code, "ECON10770", { text: "x", share: true });
  assert.equal(put.headers.get("access-control-allow-origin"), ORIGIN);
});

test("R15 课名带斜杠 / 中文 / 空格：encodeURIComponent 后能读回；不带编码的斜杠不会串到别的路径", async () => {
  const h = harness();
  const code = await h.addCode();
  for (const course of ["微观经济学 (ECON10770)", "a/b", "x  y"]) {
    const r = await h.put(code, course, { text: "t" });
    assert.equal(r.status, 200, course);
    assert.equal((await r.json()).course, course, course);
  }
  const got = await (await h.all(code)).json();
  assert.deepEqual(Object.keys(got.courses).sort(), ["a/b", "x  y", "微观经济学 (ECON10770)"].sort());
});

// ---------- 3. W24：subject / field 三层 ----------
const tiersOf = (j) => j.terms.map((t) => `${t.tier}:${termKey(t.term)}`);

test("R16 W24 subjectOf / fieldOf：课号前缀 + 分类映射；未知前缀 → null", () => {
  assert.equal(subjectOf("ECON10770"), "ECON");
  assert.equal(subjectOf("econ 10770"), "ECON");
  assert.equal(subjectOf("ECON10770A"), "ECON");
  assert.equal(subjectOf("MATH10130"), "MATH");
  assert.equal(subjectOf("微观经济学"), null, "不是课号 → 没有前缀");
  assert.equal(fieldOf("ECON"), "business");
  assert.equal(fieldOf("econ"), "business", "小写也认");
  assert.equal(fieldOf("MATH"), "stem");
  assert.equal(fieldOf("MDSA"), "health");
  assert.equal(fieldOf("SOC"), "social");
  for (const unknown of ["XYZ", "E", "", null, undefined]) {
    assert.equal(fieldOf(unknown), null, String(unknown));
  }
});

test("R17 W24 纯逻辑 mergeTiers：tier 顺序 course→subject→field，层内 users 降序、同数按字母序", () => {
  const mk = (h) => (t) => ({ hash: h, text: t });
  const out = mergeTiers([
    { tier: "course", minUsers: 2, max: SHARED_MAX, contributions: [] },
    {
      tier: "subject", minUsers: 2, max: SHARED_SUBJECT_MAX,
      contributions: [mk("h1")("beta\ngamma"), mk("h2")("gamma\nbeta")],
    },
    { tier: "field", minUsers: 3, max: SHARED_FIELD_MAX, contributions: [] },
  ]);
  assert.deepEqual(out, [
    { term: "beta", users: 2, tier: "subject" },
    { term: "gamma", users: 2, tier: "subject" },
  ]);
  assert.deepEqual(mergeTiers([]), []);
  assert.deepEqual(mergeTiers([null, { tier: "course" }]), [], "坏输入不抛");
});

test("R18 W24 跨层去重：同词算一个，保留更近的层；远层的名额不占", async () => {
  const h = harness();
  const a = await h.addCode("amy");
  const b = await h.addCode("bob");
  const c = await h.addCode("cid");
  // 「price ceiling」同时出现在 ECON10770（course）与 ECON20010（subject）
  await h.put(a, "ECON10770", { text: "Price Ceiling", share: true });
  await h.put(b, "ECON10770", { text: "price   ceiling", share: true });
  await h.put(a, "ECON20010", { text: "price ceiling\nelastisch", share: true });
  await h.put(b, "ECON20010", { text: "price ceiling\nelastisch", share: true });

  const j = await (await h.shared(c, "ECON10770")).json();
  assert.deepEqual(tiersOf(j), ["course:price ceiling", "subject:elastisch"],
    "同一词只在更近的一层出现一次");
  assert.equal(j.terms.filter((t) => termKey(t.term) === "price ceiling").length, 1);
});

test("R19 W24 ECON10770 两码 → course；ECON20010 两码 → 对 ECON10770 只是 subject", async () => {
  const h = harness();
  const a = await h.addCode("amy");
  const b = await h.addCode("bob");
  const me = await h.addCode("me");
  await h.put(a, "ECON10770", { text: "externality", share: true });
  await h.put(b, "ECON10770", { text: "externality", share: true });
  await h.put(a, "ECON20010", { text: "elasticity", share: true });
  await h.put(b, "ECON20010", { text: "elasticity", share: true });

  assert.deepEqual(tiersOf(await (await h.shared(me, "ECON10770")).json()),
    ["course:externality", "subject:elasticity"]);
  // 反过来看 ECON20010：elasticity 是 course，externality 是 subject
  assert.deepEqual(tiersOf(await (await h.shared(me, "ECON20010")).json()),
    ["course:elasticity", "subject:externality"]);
});

test("R20 W24 field：FIN10010 三码 → 对 ECON10770 是 field；FIN 只有两码 → 不出现", async () => {
  const h = harness();
  const codes = [await h.addCode("u1"), await h.addCode("u2"), await h.addCode("u3")];
  const me = await h.addCode("me");
  for (const c of codes) await h.put(c, "FIN10010", { text: "discount rate", share: true });
  for (const c of codes.slice(0, 2)) await h.put(c, "FIN20020", { text: "two only", share: true });

  const j = await (await h.shared(me, "ECON10770")).json();
  assert.deepEqual(j.terms, [{ term: "discount rate", users: 3, tier: "field" }],
    "同大类其它前缀 ≥3 码才进；只有 2 码的课不出现");
  assert.equal(j.field, "business");
});

test("R21 W24 未知前缀 XYZ1234 → 只有 course 层（不进 subject / field）", async () => {
  const h = harness();
  const codes = [await h.addCode("u1"), await h.addCode("u2"), await h.addCode("u3")];
  const me = await h.addCode("me");
  for (const c of codes) await h.put(c, "XYZ1234", { text: "weird word", share: true });
  for (const c of codes) await h.put(c, "ECON10770", { text: "econ word", share: true });

  const j = await (await h.shared(me, "XYZ1234")).json();
  assert.equal(j.subject, "XYZ");
  assert.equal(j.field, null, "未知前缀没有大类");
  assert.deepEqual(tiersOf(j), ["course:weird word"], "别的大类的词一条都不来");
});

test("R22 W24 三层上限：course 200 / subject 100 / field 60（各层单独量），总闸 300", async () => {
  const words = (n, tag) => Array.from({ length: n }, (_, i) => `${tag}${String(i).padStart(4, "0")}`);

  // course 层：ECON10770 上 205 个词被 2 码写过 -> 只回 200，且是字母序前 200
  {
    const h = harness();
    const [a, b, me] = [await h.addCode("u1"), await h.addCode("u2"), await h.addCode("u3")];
    const w = words(SHARED_MAX + 5, "c");
    await h.put(a, "ECON10770", { text: w.join("\n"), share: true });
    await h.put(b, "ECON10770", { text: w.join("\n"), share: true });
    const j = await (await h.shared(me, "ECON10770")).json();
    assert.equal(j.terms.length, SHARED_MAX, "course 上限 200");
    assert.equal(j.terms[0].term, "c0000");
    assert.equal(j.terms.at(-1).term, `c${String(SHARED_MAX - 1).padStart(4, "0")}`);
    assert.ok(j.terms.every((t) => t.tier === "course"));
  }

  // subject 层：ECON20010 / ECON20020 各 2 码、各 60 词 = 120 个候选（本课没有词）-> 只回 100
  {
    const h = harness();
    const [a, b, me] = [await h.addCode("u1"), await h.addCode("u2"), await h.addCode("u3")];
    const w = words(120, "s");
    await h.put(a, "ECON20010", { text: w.slice(0, 60).join("\n"), share: true });
    await h.put(b, "ECON20010", { text: w.slice(0, 60).join("\n"), share: true });
    await h.put(a, "ECON20020", { text: w.slice(60).join("\n"), share: true });
    await h.put(b, "ECON20020", { text: w.slice(60).join("\n"), share: true });
    const j = await (await h.shared(me, "ECON10770")).json();
    assert.equal(j.terms.length, SHARED_SUBJECT_MAX, "subject 上限 100");
    assert.ok(j.terms.every((t) => t.tier === "subject"));
    assert.equal(j.terms[0].term, "s0000");
    assert.equal(j.terms.at(-1).term, `s${String(SHARED_SUBJECT_MAX - 1).padStart(4, "0")}`);
  }

  // field 层：FIN10010 三码 70 词（本课与同前缀都没词）-> 只回 60
  {
    const h = harness();
    const codes = [await h.addCode("u1"), await h.addCode("u2"), await h.addCode("u3")];
    const me = await h.addCode("me");
    const w = words(SHARED_FIELD_MAX + 10, "f");
    for (const c of codes) await h.put(c, "FIN10010", { text: w.join("\n"), share: true });
    const j = await (await h.shared(me, "ECON10770")).json();
    assert.equal(j.terms.length, SHARED_FIELD_MAX, "field 上限 60");
    assert.ok(j.terms.every((t) => t.tier === "field"));
    assert.equal(j.terms[0].term, "f0000");
    assert.equal(j.terms.at(-1).term, `f${String(SHARED_FIELD_MAX - 1).padStart(4, "0")}`);
  }

  // 三层都满：总闸 300，层序 course -> subject -> field（近的层优先占名额）
  {
    const h = harness();
    const codes = [await h.addCode("u1"), await h.addCode("u2"), await h.addCode("u3")];
    const me = await h.addCode("me");
    const courseW = words(SHARED_MAX + 5, "c");
    await h.put(codes[0], "ECON10770", { text: courseW.join("\n"), share: true });
    await h.put(codes[1], "ECON10770", { text: courseW.join("\n"), share: true });
    const subjectW = words(120, "s");
    for (const [course, slice] of [["ECON20010", [0, 60]], ["ECON20020", [60, 120]]]) {
      await h.put(codes[0], course, { text: subjectW.slice(...slice).join("\n"), share: true });
      await h.put(codes[1], course, { text: subjectW.slice(...slice).join("\n"), share: true });
    }
    const fieldW = words(SHARED_FIELD_MAX + 10, "f");
    for (const c of codes) await h.put(c, "FIN10010", { text: fieldW.join("\n"), share: true });

    const j = await (await h.shared(me, "ECON10770")).json();
    const tiers = j.terms.map((t) => t.tier);
    assert.deepEqual([...new Set(tiers)], ["course", "subject"], "总闸 300 由近的层先占，field 被挤空");
    assert.equal(j.terms.length, SHARED_TOTAL_MAX, "总数 = 总闸 300");
  }

  // 前缀成本闸：现在全部前缀 + course + subject 前缀都在闸内
  const prefixes = Object.values(FIELD_PREFIXES_ALL()).flat();
  assert.ok(prefixes.length + 2 <= SHARED_LIST_MAX, `前缀 ${prefixes.length} + 2 要 <= ${SHARED_LIST_MAX}`);
});

// 分类表的**内容**（与 core 的 FIELD_PREFIXES 同源：只列候选前缀，归属用 fieldOf 反查，不抄第二份映射）
function FIELD_PREFIXES_ALL() {
  const all = ["MATH", "STAT", "PHYS", "CHEM", "COMP", "EEEN", "MEEN", "CVEN", "CHEN", "BMOL", "BIOL", "BIOC",
    "GEOL", "ENVB", "ACM", "MST", "EGEN", "ECON", "ACC", "FIN", "MKT", "MGT", "BMGT", "BUS", "BANL", "MIS", "IB",
    "ENT", "HRM", "MDSA", "PHPS", "NMHS", "PHAR", "RDGY", "PHYC", "VET", "SOC", "PSY", "POL", "HIS", "PHIL",
    "LAW", "ARCH", "GEOG"];
  const out = {};
  for (const p of all) (out[fieldOf(p)] ??= []).push(p);
  return out;
}
