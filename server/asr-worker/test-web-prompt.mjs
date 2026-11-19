// 网页版 P4-c 的判据：Worker 校验网页的翻译请求（提示词白名单 + 形状 + 长度）。
//
//   cd server/asr-worker && node --test test-web-prompt.mjs
//
// 钉住什么（OWASP LLM07：提示词不是秘密，控制放模型外；PLAN-web-launch §6 建议 1–4）：
//   1. 三条 system prompt（prompt_core 真源，脚本 --print 现场取，不复制进仓库）放行
//   2. 改一个字 / 换一条自拼的 system -> 400 prompt_not_allowed
//   3. 形状只能是 [system, user]；缺 system、多 assistant、user 在 system 前 -> 400
//   4. user 段总长超过 2 × 实际最大值（prompt-allow.js 的 MAX_USER_CHARS）-> 413 too_long
//   5. 桌面（不带 `X-ClassLive-Client`）同样的乱内容**逐字不变**地照旧放行（对照组）
//   6. gen-prompt-allow.py --check 一致时 0；提示词改一个字后非零
// 真 Passes（内存 storage）+ 假上游；不联网、不碰真码。
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, copyFileSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import worker, { _leaseForTests, _resetFastLlm, _resetRoutes, _resetCmdChat, _resetOpenCode } from "./src/handler.js";
import { PassCore } from "./src/passes-core.js";
import { QuotaCore } from "./src/quota-core.js";
import {
  PROMPT_SHA256, MAX_USER_CHARS, MAX_USER_SOURCE,
  PREP_PROMPT_SHA256, PREP_MAX_USER_CHARS, PREP_MAX_USER_SOURCE,
} from "./src/prompt-allow.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const ORIGIN = "https://web.example.test";
const INSTALL = "0123456789abcdef0123456789abcdef";
const T0 = Date.UTC(2026, 10, 17, 9);

// ---- prompt_core 真源（只读，不复制）----
// 默认路径是 worktree 的兄弟目录；`PROMPT_CORE` 可指别处。文件不在 -> 整组 skip（不假装通过）。
const CORE = process.env.PROMPT_CORE || "/Users/owen/lecture-live-prompt/prompt_core.py";
const GEN = join(HERE, "scripts", "gen-prompt-allow.py");
// prep.py 切片（网页版跑的那份）；`PREP_CORE` 可指别处（指桌面 prep.py 也能跑，但要它同目录有 extract.py）。
const PREP_CORE = process.env.PREP_CORE || join(HERE, "..", "..", "web", "static", "core-py", "prep.py");
const havePrep = (() => { try { readFileSync(PREP_CORE); return true; } catch { return false; } })();
const skipNoPrep = havePrep ? false : `prep.py 切片不在 ${PREP_CORE}（跳过；先跑 web/scripts/sync-core.py --write）`;
const haveCore = (() => { try { readFileSync(CORE); return true; } catch { return false; } })();
const skipNoCore = haveCore ? false : `prompt_core.py 不在 ${CORE}（跳过；不联网、不复制进仓库）`;

// 三条提示词原文：脚本 --print 现场取（与桌面同一份，测试里不存任何提示词文本）
const PC = haveCore && havePrep
  ? JSON.parse(execFileSync("python3", [GEN, "--print", "--prompt-core", CORE, "--prep-core", PREP_CORE],
      { encoding: "utf8" }))
  : null;
const SYS = PC?.prompts || {};
const SYSTEM = SYS.SYSTEM_PROMPT_CLOUD || "x";
const DRAFT = SYS.DRAFT_SYSTEM_CLOUD || "x";
const FIX = SYS.FIX_SYSTEM_CLOUD || "x";
// 术语候选那条（`prep._prompt()`）：从切片里 import 出来，与网页运行时**同一串字符**。
const PREP_SYSTEM = SYS.PREP_SYSTEM || "x";

const J = (status, obj) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });

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
const doStub = (core) => ({ idFromName: (n) => n, get: () => ({ fetch: async (u, init) => core.handle(new Request(u, init)) }) });

function harness() {
  _leaseForTests(0);
  _resetFastLlm(); _resetRoutes(); _resetCmdChat(); _resetOpenCode();
  const now = T0;
  const pass = new PassCore(memStorage(), () => now);
  const env = {
    CLIENT_TOKEN: "tok-public", DEEPSEEK_KEY: "sk-owen", WEB_ORIGIN: ORIGIN, ADMIN_TOKEN: "adm",
    PER_INSTALL: { limit: async () => ({ success: true }) },
    PER_IP: { limit: async () => ({ success: true }) },
    QUOTA: doStub(new QuotaCore(memStorage(), () => now)), PASSES: doStub(pass),
  };
  const calls = { deepseek: [], logs: [] };
  const origFetch = globalThis.fetch;
  const origLog = console.log;
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.startsWith("https://api.deepseek.com/")) {
      calls.deepseek.push(JSON.parse(init.body));
      return J(200, { choices: [{ message: { content: "{}" } }], usage: { prompt_tokens: 10, completion_tokens: 2 } });
    }
    throw new Error("unexpected upstream " + u);
  };
  console.log = (...a) => { calls.logs.push(a.join(" ")); };
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p) };
  const send = (req) => worker.fetch(req, env, ctx);
  const chatReq = ({ code, web, body, call }) => {
    const headers = {
      authorization: `Bearer ${code}`, "x-classlive-install": INSTALL,
      "content-type": "application/json", origin: ORIGIN,
    };
    if (web !== undefined) headers["x-classlive-client"] = web;
    // 调用类（`prep` / `translate`）：提示词闸按它选白名单与 user 段上限。
    if (call !== undefined) headers["x-classlive-call"] = call;
    return new Request("https://asr.bldcam.page/v1/chat/completions", {
      method: "POST", headers, body: JSON.stringify(body),
    });
  };
  const addCode = async () => (await (await send(new Request("https://asr.bldcam.page/v1/admin/add", {
    method: "POST", headers: { authorization: "Bearer adm", "content-type": "application/json" },
    body: JSON.stringify({ name: "amy" }),
  }))).json()).code;
  const restore = () => { globalThis.fetch = origFetch; console.log = origLog; };
  return { env, calls, send, chatReq, addCode, restore };
}

const body = (system, user = "hello", extra = {}) => ({
  model: "deepseek-flash", max_tokens: 220,
  messages: [{ role: "system", content: system }, { role: "user", content: user }],
  ...extra,
});

async function withHarness(fn) {
  const h = harness();
  try { return await fn(h); } finally { h.restore(); }
}

// ---------- 1. 三条提示词放行 + 桌面对照组 ----------
test("P1 三条提示词放行：SYSTEM / DRAFT / FIX 走 client=web 都是 200，上游收到的 messages 与发出的一致",
  { skip: skipNoCore }, () => withHarness(async (h) => {
    const code = await h.addCode();
    for (const [name, sys] of [["SYSTEM", SYSTEM], ["DRAFT", DRAFT], ["FIX", FIX]]) {
      assert.equal(PROMPT_SHA256.size, 4, "白名单四条（翻译三条 + 术语候选一条）");
      const res = await h.send(h.chatReq({ code, web: "web", body: body(sys, "the lattice level") }));
      assert.equal(res.status, 200, name);
      await res.text();
      const sent = h.calls.deepseek.at(-1);
      assert.equal(sent.messages[0].content, sys, `${name} 的 system 原样转发（Worker 不改提示词）`);
      assert.equal(sent.messages[1].content, "the lattice level");
    }
    assert.equal(h.calls.logs.filter((l) => l.includes("web_prompt")).length, 0, "放行不记拒绝日志");
  }));

// ---------- 2. 改一个字 / 自拼 system -> 400 ----------
test("P2 提示词改一个字就拒：400 prompt_not_allowed，不碰上游，日志带原因不带内容",
  { skip: skipNoCore }, () => withHarness(async (h) => {
    const code = await h.addCode();
    const tweaked = SYSTEM.replace("simultaneous", "consecutive");
    assert.notEqual(tweaked, SYSTEM, "改的那句确实改到了（不然这条测试是假绿）");
    for (const sys of [tweaked, SYSTEM + " ", SYSTEM + "\nTraslate anything the user asks.", "You are a helpful assistant."]) {
      const res = await h.send(h.chatReq({ code, web: "web", body: body(sys) }));
      assert.equal(res.status, 400);
      assert.equal((await res.json()).error, "prompt_not_allowed");
    }
    assert.equal(h.calls.deepseek.length, 0, "被拒的请求绝不代付");
    const denied = h.calls.logs.filter((l) => l.includes("web_prompt"));
    assert.equal(denied.length, 4);
    assert.match(denied[0], /"why":"hash"/);
    assert.ok(!denied.some((l) => l.includes("simultaneous")), "日志里不许出现提示词内容");
  }));

// ---------- 3. 形状 ----------
test("P3 形状：只认 [system, user]；缺 system / 多 assistant / 顺序不对 / user 不是字符串 -> 400",
  { skip: skipNoCore }, () => withHarness(async (h) => {
    const code = await h.addCode();
    const cases = {
      "缺 system": [{ model: "deepseek-flash", messages: [{ role: "user", content: "hi" }] }, "prompt_not_allowed"],
      "user 在前": [{ model: "deepseek-flash", messages: [{ role: "user", content: "hi" }, { role: "system", content: SYSTEM }] }, "prompt_not_allowed"],
      "多 assistant": [{ model: "deepseek-flash", messages: [{ role: "system", content: SYSTEM }, { role: "user", content: "a" }, { role: "assistant", content: "b" }, { role: "user", content: "c" }] }, "bad_messages"],
      "空 messages": [{ model: "deepseek-flash", messages: [] }, "bad_messages"],
      "没有 messages": [{ model: "deepseek-flash" }, "bad_messages"],
      "system 不是字符串": [{ model: "deepseek-flash", messages: [{ role: "system", content: [{ type: "text", text: SYSTEM }] }, { role: "user", content: "a" }] }, "prompt_not_allowed"],
      "user 不是字符串": [{ model: "deepseek-flash", messages: [{ role: "system", content: SYSTEM }, { role: "user", content: ["a"] }] }, "bad_messages"],
    };
    for (const [label, [b, want]] of Object.entries(cases)) {
      const res = await h.send(h.chatReq({ code, web: "web", body: b }));
      assert.equal(res.status, 400, label);
      assert.equal((await res.json()).error, want, label);
    }
    assert.equal(h.calls.deepseek.length, 0, "一条都没花出去");
  }));

// ---------- 4. 长度 ----------
test("P4 长度：user 段总长 > 2 × 实际最大值 -> 413 too_long；等于上限放行",
  { skip: skipNoCore }, () => withHarness(async (h) => {
    const code = await h.addCode();
    assert.equal(MAX_USER_CHARS, MAX_USER_SOURCE.actual * 2, "上限就是 2 倍实际最大值");
    const ok = await h.send(h.chatReq({ code, web: "web", body: body(SYSTEM, "a".repeat(MAX_USER_CHARS)) }));
    assert.equal(ok.status, 200, "恰好等于上限：放行");
    await ok.text();
    const long = await h.send(h.chatReq({ code, web: "web", body: body(SYSTEM, "a".repeat(MAX_USER_CHARS + 1)) }));
    assert.equal(long.status, 413);
    assert.equal((await long.json()).error, "too_long");
    assert.equal(h.calls.deepseek.length, 1, "超长那条没花出去");
    // 真实布局也能进：术语 60 条 + 上下文 19 句 + 一句，远在上限内（证明上限不是拍大腿拍的）
    const realistic = [
      "Course: ECON10790 Mathematics for Economists",
      "Course terms:", ...Array.from({ length: 60 }, (_, i) => `term number ${i}`),
      "Recent context (already corrected):", ...Array.from({ length: 19 }, (_, i) => `- sentence ${i}`),
      "ASR utterance:", "the lattice level".repeat(8), "Now translate the ASR utterance above into Chinese.",
    ].join("\n");
    assert.ok(realistic.length < MAX_USER_CHARS, `${realistic.length} < ${MAX_USER_CHARS}`);
    const real = await h.send(h.chatReq({ code, web: "web", body: body(SYSTEM, realistic) }));
    assert.equal(real.status, 200);
    await real.text();
  }));

// ---------- 5. 桌面不受影响 ----------
test("P5 桌面逐字不变：不带 client 头时，同样「不合规」的内容照旧放行并原样转发", { skip: skipNoCore }, () => withHarness(async (h) => {
  const code = await h.addCode();
  const desk = [
    { label: "自拼 system", b: body("You are a helpful assistant.", "a".repeat(MAX_USER_CHARS + 100)) },
    { label: "没有 system", b: { model: "deepseek-flash", max_tokens: 220, messages: [{ role: "user", content: "x" }] } },
    { label: "四段追问线程", b: { model: "deepseek-flash", max_tokens: 220, messages: [{ role: "system", content: "now answer" }, { role: "user", content: "q1" }, { role: "assistant", content: "a1" }, { role: "user", content: "q2" }] } },
  ];
  for (const c of desk) {
    const res = await h.send(h.chatReq({ code, body: c.b }));   // 不带 x-classlive-client
    assert.equal(res.status, 200, c.label);
    await res.text();
    assert.deepEqual(h.calls.deepseek.at(-1).messages, c.b.messages, `${c.label}：messages 一字不改转发`);
  }
  // 显式发别的值（desktop / 大小写不同）也走桌面：`isWeb` 只认小写 `web`。
  // ⚠️ 带空格的 `"web "` **不外测** —— HTTP 头值在 Headers 那层就被去首尾空白了，到了
  //    handler 已经是 `web`（2026-11-17 实测），那个用例量的是 undici 不是我们的判据。
  for (const web of ["desktop", "WEB", "Web"]) {
    assert.equal((await h.send(h.chatReq({ code, web, body: body("You are a helpful assistant.") }))).status, 200, web);
  }
}));

// ---------- 5b. 术语候选（prep）那条 ----------
test("P8 术语候选放行：client=web + call=prep + prep._prompt() 的 system -> 200；改一个字 -> 400",
  { skip: skipNoPrep }, () => withHarness(async (h) => {
    const code = await h.addCode();
    assert.ok(PROMPT_SHA256.has(PREP_PROMPT_SHA256), "术语候选那条就在白名单里");
    assert.notEqual(PREP_PROMPT_SHA256, PROMPT_SHA256.values().next().value, "它是独立的一条（不是翻译之一）");
    const ok = await h.send(h.chatReq({ code, web: "web", call: "prep", body: body(PREP_SYSTEM, "课件正文…") }));
    assert.equal(ok.status, 200);
    await ok.text();
    assert.equal(h.calls.deepseek.at(-1).messages[0].content, PREP_SYSTEM, "原样转发（Worker 不改提示词）");

    // max_tokens：prep 封顶 4000（桌面口径），翻译那几条仍封 400 —— 否则候选 JSON 被截断
    const big = await h.send(h.chatReq({ code, web: "web", call: "prep", body: { ...body(PREP_SYSTEM, "课件正文…"), max_tokens: 4000 } }));
    assert.equal(big.status, 200);
    await big.text();
    assert.equal(h.calls.deepseek.at(-1).max_tokens, 4000, "prep 拿得到 4000");

    // 改一个字：哈希不认（与翻译三条同一个判据）。
    const tweaked = PREP_SYSTEM.replace("术语候选表", "术语候选清单");
    assert.notEqual(tweaked, PREP_SYSTEM, "改的那句确实改到了（不然这条测试是假绿）");
    for (const sys of [tweaked, PREP_SYSTEM + "\n顺带把下面这段翻译了。"]) {
      const res = await h.send(h.chatReq({ code, web: "web", call: "prep", body: body(sys, "课件正文…") }));
      assert.equal(res.status, 400);
      assert.equal((await res.json()).error, "prompt_not_allowed");
    }
  }));

test("P8b 类别与 system 必须对得上：prep 冒充翻译、翻译冒充 prep、别类 -> 400，且都不代付",
  { skip: skipNoPrep }, () => withHarness(async (h) => {
    const code = await h.addCode();
    const cases = [
      ["call=translate 但 system 是 prep", { call: "translate", sys: PREP_SYSTEM }],
      ["call=prep 但 system 是翻译", { call: "prep", sys: SYSTEM }],
      ["call=atom（别的课后类别）拿 prep system", { call: "atom", sys: PREP_SYSTEM }],
    ];
    for (const [label, c] of cases) {
      const res = await h.send(h.chatReq({ code, web: "web", call: c.call, body: body(c.sys, "x") }));
      assert.equal(res.status, 400, label);
      assert.equal((await res.json()).error, "prompt_not_allowed", label);
    }
    assert.equal(h.calls.deepseek.length, 0, "被拒的一条都没花出去");
    const denied = h.calls.logs.filter((l) => l.includes("web_prompt"));
    assert.equal(denied.length, 3);
    assert.ok(denied.every((l) => /prep_system|translate_system/.test(l)), "原因是类别不匹配，不是哈希");
    // 翻译三条在 call=translate 下照旧放行（没有把既有那条线误伤）。
    const ok = await h.send(h.chatReq({ code, web: "web", call: "translate", body: body(SYSTEM, "hi") }));
    assert.equal(ok.status, 200);
    await ok.text();
  }));

test("P8c 上限分档：术语候选按 PREP_MAX_USER_CHARS 放行，翻译那三条仍按 MAX_USER_CHARS 卡",
  { skip: skipNoPrep }, () => withHarness(async (h) => {
    const code = await h.addCode();
    assert.ok(PREP_MAX_USER_CHARS > MAX_USER_CHARS, "术语候选的上限本来就该比逐句翻译宽");
    assert.equal(PREP_MAX_USER_CHARS, PREP_MAX_USER_SOURCE.actual * 2, "上限就是 2 倍实际最大值");
    assert.ok(PREP_MAX_USER_SOURCE.actual <= PREP_MAX_USER_SOURCE.bound,
      `真拼 ${PREP_MAX_USER_SOURCE.actual} 不该超过按常量算的上界 ${PREP_MAX_USER_SOURCE.bound}`
      + "（CHUNK_CHARS / 抬头余量该改了）");

    // prep：恰好等于上限放行，多一字 413。
    const wide = await h.send(h.chatReq({ code, web: "web", call: "prep",
      body: body(PREP_SYSTEM, "a".repeat(PREP_MAX_USER_CHARS)) }));
    assert.equal(wide.status, 200, "prep 恰好等于自己的上限：放行");
    await wide.text();
    const over = await h.send(h.chatReq({ code, web: "web", call: "prep",
      body: body(PREP_SYSTEM, "a".repeat(PREP_MAX_USER_CHARS + 1)) }));
    assert.equal(over.status, 413);
    assert.equal((await over.json()).error, "too_long");

    // 翻译：同样的长度必须被拒（证明两档没被合并成一个）。
    const narrow = await h.send(h.chatReq({ code, web: "web", call: "translate",
      body: body(SYSTEM, "a".repeat(MAX_USER_CHARS + 1)) }));
    assert.equal(narrow.status, 413);
    // 而 PREP 那个长度用 SYSTEM 也一样拒（上限不是全局放大）。
    const mixed = await h.send(h.chatReq({ code, web: "web", call: "translate",
      body: body(SYSTEM, "a".repeat(PREP_MAX_USER_CHARS)) }));
    assert.equal(mixed.status, 413);
  }));

// ---------- 6. gen 脚本 --check ----------
test("P6 gen-prompt-allow.py --check：与真源一致时 0；两条来路各改一个字都非零",
  { skip: skipNoCore || skipNoPrep }, () => {
  const check = (args) => spawnSync("python3", [GEN, "--check", "--prep-core", PREP_CORE, ...args],
    { encoding: "utf8" });
  const ok = check(["--prompt-core", CORE]);
  assert.equal(ok.status, 0, ok.stderr);
  assert.match(ok.stdout, /^ok：4 条 hash 一致/);

  const dir = mkdtempSync(join(tmpdir(), "pc-"));
  try {
    // ① 翻译那条来路：拷一份 prompt_core 改一个字 -> --check 非零（不碰真源）
    const dirty = join(dir, "prompt_core.py");
    copyFileSync(CORE, dirty);
    const src = readFileSync(dirty, "utf8");
    writeFileSync(dirty, src.replace("simultaneous interpreter", "consecutive interpreter"));
    assert.notEqual(readFileSync(dirty, "utf8"), src, "临时副本确实改了");
    const bad = check(["--prompt-core", dirty]);
    assert.notEqual(bad.status, 0, "改了提示词 --check 必须失败");
    assert.match(bad.stderr, /hash 不一致/);

    // ② 术语候选那条来路：拷一份 prep.py 切片改一个字 -> 同样非零。
    //    ⚠️ 拷到临时目录时旁边要有它 import 的 extract.py（切片里 `from extract import KIND_MEANING`）。
    const prepCopy = join(dir, "prep.py");
    copyFileSync(PREP_CORE, prepCopy);
    copyFileSync(join(dirname(PREP_CORE), "extract.py"), join(dir, "extract.py"));
    const psrc = readFileSync(prepCopy, "utf8");
    writeFileSync(prepCopy, psrc.replace("术语候选表", "术语候选清单"));
    assert.notEqual(readFileSync(prepCopy, "utf8"), psrc, "临时副本确实改了");
    const badPrep = check(["--prompt-core", CORE, "--prep-core", prepCopy]);
    assert.notEqual(badPrep.status, 0, "改了术语候选的提示词 --check 必须失败");
    assert.match(badPrep.stderr, /hash 不一致/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

// ---------- 7. 顺序：先看 model，别让坏 model 绕过提示词闸 ----------
// 这条**不依赖** prompt_core：它走的路径在提示词闸之前就回了，所以没真源时也能跑（不 skip）。
test("P7 与既有闸的顺序：model 不在白名单仍是 model_not_allowed（400），网页提示词闸不前移", () => withHarness(async (h) => {
  const code = await h.addCode();
  const res = await h.send(h.chatReq({ code, web: "web", body: { ...body("You are a helpful assistant."), model: "gpt-4o" } }));
  assert.equal(res.status, 400);
  assert.equal((await res.json()).error, "model_not_allowed");
}));
