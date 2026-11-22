// 术语上云（W19/W24）的**接线与存储**：两个 Durable Object + handler 侧的三个接口分发。
// 对外：termsRoute()（handler 转发进来）、WebTerms / WebTermsPool（DO 类，index.js 里 export）。
//   WebTerms     —— 每个通行码一个实例（idFromName(codeHash16)，同 web-session 的分片方式）：个人术语
//   WebTermsPool —— **全局一个实例**（idFromName("terms-pool-v1")）：共享池，跨码汇总（W24 起三层共用这一个池）
// 规则全在 web-terms-core.js（拆行 / 去重 / 课号 / 前缀 / 大类 / ≥N 过滤排序），这里只做 I/O 与路由。
// 契约：docs/API-web-v0-20261117.md §3.9。
import { MAX_BODY_BYTES } from "./web-session-core.js";
import {
  FIELD_PREFIXES, SHARED_FIELD_MAX, SHARED_LIST_MAX, SHARED_MAX, SHARED_MIN_USERS, SHARED_SUBJECT_MAX,
  checkPut, courseKeyOf, fieldOf, mergeTiers, poolStats, subjectOf,
} from "./web-terms-core.js";

export const TERMS_PREFIX = "/v1/web/terms";
const POOL_NAME = "terms-pool-v1";

// ⚠️ 动态 import（同 web-session.js）：测试要在 Node 里直接 new 这两个类、喂内存 storage，
// 而 `cloudflare:workers` 在 Node 下解析不了；wrangler 打包时照常解析成内置模块。
let DurableObjectBase = class {};
try {
  ({ DurableObject: DurableObjectBase } = await import("cloudflare:workers"));
} catch { /* Node 测试：没这个内置模块 */ }

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });

// ---- DO：个人术语（每码一个实例）----
// 键：`course:<课名>` = {text, updated_at}。空 text = 删掉这门课（一个字节不留）。
// 幂等：同样的 text 再 PUT 不刷新 updated_at（客户端靠它比新旧，无谓地刷新会让本机缓存永远「旧」）。
export class WebTerms extends DurableObjectBase {
  constructor(ctx, env) {
    super(ctx, env);
    this.ctx = ctx;           // 真 DO 的基类会设，Node 下的替身类不会 —— 两边都写一次更稳
    this.env = env;
    this.storage = ctx.storage;
  }

  async fetch(request) {
    const url = new URL(request.url);
    let body = {};
    if (request.method !== "GET") {
      try { body = await request.json(); } catch { return json({ error: "bad_body" }, 400); }
    }
    if (url.pathname === "/all") return this.#all();
    if (url.pathname === "/put") return this.#put(body);
    return json({ error: "not_found" }, 404);
  }

  async #all() {
    const courses = {};
    for (const [k, v] of await this.storage.list({ prefix: "course:" })) {
      courses[k.slice("course:".length)] = { text: v.text, updated_at: v.updated_at };
    }
    return json({ courses });
  }

  // `updated_at` 由调用方给（handler 用 Date.now()），DO 不回自己读钟 —— 同一段重放拿到同一个值。
  async #put({ course, text, updated_at }) {
    if (typeof course !== "string" || typeof text !== "string") return json({ error: "bad_body" }, 400);
    const key = `course:${course}`;
    const prev = await this.storage.get(key);
    if (!text) {
      if (prev) await this.storage.delete(key);
      const at = prev?.updated_at ?? (Number.isFinite(updated_at) ? updated_at : Date.now());
      return json({ course, updated_at: at });
    }
    if (prev && prev.text === text) return json({ course, updated_at: prev.updated_at });
    const at = Number.isFinite(updated_at) ? updated_at : Date.now();
    await this.storage.put(key, { text, updated_at: at });
    return json({ course, updated_at: at });
  }
}

// ---- DO：同课共享池（全局一个实例）----
// 键：`share:<课号>:<codeHash16>` = {text, updated_at} —— 一枚码对一门课**一条**贡献，
// 再 PUT 是覆盖（契约 §共享池），空 text 是撤掉。人数靠「不同 hash 的条数」，不记是谁。
// 不用 R2：这里是读-改-写（覆盖贡献），要 DO 的串行；R2 没有原子读改写。
export class WebTermsPool extends DurableObjectBase {
  constructor(ctx, env) {
    super(ctx, env);
    this.ctx = ctx;
    this.env = env;
    this.storage = ctx.storage;
  }

  async fetch(request) {
    const url = new URL(request.url);
    let body = {};
    if (request.method !== "GET") {
      try { body = await request.json(); } catch { return json({ error: "bad_body" }, 400); }
    }
    if (url.pathname === "/set") return this.#set(body);
    if (url.pathname === "/list") return this.#list(url.searchParams.getAll("pre"));
    return json({ error: "not_found" }, 404);
  }

  async #set({ course_key, hash, text, updated_at }) {
    if (typeof course_key !== "string" || typeof hash !== "string" || typeof text !== "string") {
      return json({ error: "bad_body" }, 400);
    }
    const key = `share:${course_key}:${hash}`;
    const prev = await this.storage.get(key);
    if (!text) {
      if (prev) await this.storage.delete(key);
      return json({ course_key });
    }
    if (prev && prev.text === text) return json({ course_key });
    await this.storage.put(key, { text, updated_at: Number.isFinite(updated_at) ? updated_at : Date.now() });
    return json({ course_key });
  }

  // 一批**存储前缀**的全部贡献 → `{ entries: [{key, text}] }`；汇总 / 挡码 / 排序全在 core 的纯函数里。
  // 前缀由 handler 算好（`share:ECON10770:` = 这一门课，`share:ECON` = 同前缀的**所有**课）：
  // W24 起要「同课 / 同前缀 / 同大类」三层，编码前缀比再抽一层 key 解析简单。
  // ⚠️ 单次 list 上限 1000 条/前缀（= 1000 枚码写过同一门课）；过了这数的课要改成分页，v0 不可能到。
  async #list(prefixes) {
    const entries = [];
    for (const p of prefixes) {
      for (const [key, v] of await this.storage.list({ prefix: p })) entries.push({ key, text: v.text });
    }
    return json({ entries });
  }
}

// ---- handler 侧：路径匹配 + 三个接口 ----// 鉴权 / 来源 / install 形状 / 查码 / 限流**全部**在 handler.webRoute 里已经做完，这里只认路径形状、挑方法与 body。
export const TERMS_KINDS = ["terms", "terms_shared", "terms_put"];
// 课名先过 encodeURIComponent 才拼进来（中文 / 空格 / 斜杠都能带），非空即可 —— 真校验在 core。
const COURSE_PATH = /^\/v1\/web\/terms\/([^/]+)$/;

/** `/v1/web/terms*` 的方法 + 路径 → kind（不在这一族 / 方法不对 → null，调用方回 404）。 */
export function matchTermsPath(method, path) {
  if (path === TERMS_PREFIX) return method === "GET" ? "terms" : null;
  // ⚠️ `/terms/shared` 必须排在 `/terms/:course` 前面 —— 否则课名会被当成 "shared"。
  if (path === `${TERMS_PREFIX}/shared`) return method === "GET" ? "terms_shared" : null;
  if (method !== "PUT") return null;
  return COURSE_PATH.test(path) ? "terms_put" : null;
}

const stubOf = (ns, name) => (ns ? ns.get(ns.idFromName(name)) : null);
const call = (stub, p, payload) => stub.fetch(new Request(`https://web-terms${p}`, {
  method: payload === undefined ? "GET" : "POST",
  body: payload === undefined ? undefined : JSON.stringify(payload),
}));

// 读 body 并核**实际字节数**（Content-Length 可以谎报，同 handler.readWebBody 的立场）。
// 这里不复用 handler 那个：handler 会 import 本文件，反向 import 就成环了。
async function readJson(request) {
  const len = Number(request.headers.get("content-length"));
  if (len > MAX_BODY_BYTES) return { error: "too_large" };
  const text = await request.text().catch(() => "");
  if (new TextEncoder().encode(text).byteLength > MAX_BODY_BYTES) return { error: "too_large" };
  if (!text) return { json: {} };
  try { return { json: JSON.parse(text) }; } catch { return { json: null }; }
}

function courseFromPath(path) {
  try { return decodeURIComponent(path.slice(TERMS_PREFIX.length + 1)); } catch { return null; }
}

/** 存储键 `share:<课号>:<hash>` → 课号（`share:ECON10770:abc` → `ECON10770`）；认不出 → null */
function keyOfShare(key) {
  const m = /^share:([^:]+):/.exec(String(key ?? ""));
  return m ? m[1] : null;
}

/**
 * 共享池的**三层请求计划**：先算出每层该 list 哪些存储前缀，再去重 / 排序。
 *   course  —— `share:<本课号>:`，阈值 2、上限 200（W19 原行为）
 *   subject —— `share:<前缀>`（同前缀的**所有**课），本课自己会在 core 里被挡掉：阈值 2、上限 100
 *   field   —— 大类里**其它**前缀（`share:<别的课号>:`，一课一项），阈值 3、上限 60（最远的一层，最严）
 * 前缀总数超 SHARED_LIST_MAX 就按字母序截断（成本闸；前缀表就这么大，平时到不了）。
 * 未如前缀 → 只有 course 层（`fieldOf` 返回 null）。
 */
export function sharedPlan(courseKey) {
  const subject = subjectOf(courseKey);
  const field = fieldOf(subject);
  const course = { tier: "course", minUsers: SHARED_MIN_USERS, max: SHARED_MAX, prefixes: [`share:${courseKey}:`] };
  if (!field) return { subject, field, tiers: [course] };

  const subjectTier = { tier: "subject", minUsers: SHARED_MIN_USERS, max: SHARED_SUBJECT_MAX, prefixes: [`share:${subject}`] };
  const others = FIELD_PREFIXES[field].filter((p) => p !== subject);
  let prefixes = others.map((p) => `share:${p}`);
  if (prefixes.length > SHARED_LIST_MAX) {
    prefixes = [...prefixes].sort().slice(0, SHARED_LIST_MAX);
    console.log(JSON.stringify({ terms: "field_truncated", field, wanted: others.length }));
  }
  const fieldTier = { tier: "field", minUsers: SHARED_MIN_USERS + 1, max: SHARED_FIELD_MAX, prefixes };
  return { subject, field, tiers: [course, subjectTier, fieldTier] };
}

export async function termsRoute(request, env, ev, path, { hash }) {
  ev.call_class = "web-terms";
  const method = request.method;
  const personal = stubOf(env.WEB_TERMS, hash);
  const pool = stubOf(env.WEB_TERMS_POOL, POOL_NAME);

  // GET /v1/web/terms —— 这门码名下的全部个人术语
  if (path === TERMS_PREFIX) {
    if (method !== "GET") return new Response("not found", { status: 404 });
    if (!personal) return json({ error: "upstream", detail: "web_terms" }, 503);
    const body = await (await call(personal, "/all")).json();
    return json(body);
  }

  // GET /v1/web/terms/shared?course=… —— 共享术语池（course / subject / field 三层，只对课号开；不是课号 → 空）
  if (path === `${TERMS_PREFIX}/shared` && method === "GET") {
    const course = new URL(request.url).searchParams.get("course") || "";
    const key = courseKeyOf(course);
    if (!key) return json({ course_key: null, subject: null, field: null, terms: [] });
    if (!pool) return json({ error: "upstream", detail: "web_terms_pool" }, 503);
    const plan = sharedPlan(key);
    // 一次 fetch 拿全部层的贡献（DO 里逐个前缀 list），分层与截断在本机算 —— 少一次往返。
    const q = new URLSearchParams();
    for (const t of plan.tiers) for (const p of t.prefixes) q.append("pre", p);
    const listed = await (await call(pool, `/list?${q.toString()}`)).json();
    const entries = Array.isArray(listed?.entries) ? listed.entries : [];
    const tiers = plan.tiers.map((t) => ({
      tier: t.tier,
      minUsers: t.minUsers,
      max: t.max,
      contributions: entries
        .filter((e) => {
          const sk = String(e?.key ?? "");
          if (!t.prefixes.some((p) => sk.startsWith(p))) return false;   // 只收本层前缀下的
          const k = keyOfShare(sk);
          // 更远的层丢掉本课自己（同课已经在 course 层里，再算一遍会让「门数」虚高）
          return t.tier === "course" ? k === key : k !== key;
        })
        .map((e) => ({ hash: String(e.key), text: typeof e.text === "string" ? e.text : "" })),
    }));
    return json({ course_key: key, subject: plan.subject, field: plan.field, terms: mergeTiers(tiers) });
  }

  // PUT /v1/web/terms/:course —— 写一门课；空 text = 删掉；share 决定进不进池
  if (method === "PUT" && path.startsWith(`${TERMS_PREFIX}/`)) {
    const rawCourse = courseFromPath(path);
    if (rawCourse === null) return new Response("not found", { status: 404 });
    if (!personal) return json({ error: "upstream", detail: "web_terms" }, 503);
    const body = await readJson(request);
    if (body.error) return json({ error: body.error }, 413);
    // 课名与开课同一套规则；正文的拆行 / 去重 / 条数 / 单条长度全在 core
    const checked = checkPut(body.json, rawCourse);
    if (checked.error) return json({ error: checked.error, detail: checked.detail }, 400);
    const now = Date.now();
    const wr = await (await call(personal, "/put", {
      course: checked.course, text: checked.text, updated_at: now,
    })).json();
    if (wr.error) return json({ error: wr.error }, 502);

    // 共享池只是加分项：写失败不影响个人术语（个人术语是权威，池子没有就没有）。
    const key = courseKeyOf(checked.course);
    if (key && pool) {
      try {
        await call(pool, "/set", {
          course_key: key, hash, text: checked.share ? checked.text : "", updated_at: now,
        });
      } catch { console.log(JSON.stringify({ terms: "pool_write_failed", course_key: key })); }
    }
    return json({ course: wr.course, updated_at: wr.updated_at });
  }

  return new Response("not found", { status: 404 });
}

/** 管理看板（只读，`/v1/admin/terms/stats`）：共享池规模。没绑 DO → null（调用方回 503）。 */
export async function adminTermsStats(env) {
  const pool = stubOf(env.WEB_TERMS_POOL, POOL_NAME);
  if (!pool) return null;
  const r = await call(pool, `/list?pre=${encodeURIComponent("share:")}`);
  const { entries } = await r.json();
  return poolStats(entries);
}
