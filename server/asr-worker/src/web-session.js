// Durable Object 壳 `WebSessions`：**每个通行码一个实例**（idFromName(codeHash16)，明文码不当名字）。
// 账本元数据 / seq 水位 / 任务队列表存 DO storage；逐句正文按批写 R2（绑定 `WEB_DATA`）。
// 路由全是内部接口（只有 handler.js 转发进来，鉴权在上面那层 —— DO 自己谁都信，别从别处暴露）：
//   POST /open   {hash, course, started_at, tz, install, idem}
//   PUT  /lines  {session_id, lines}                         -> 只写 final（draft 留在浏览器）
//   POST /end    {session_id, ended_at, lines_total, level}   -> 只入队（status=queued），不跑笔记
//   POST /rename {session_id, course}                         -> 课后改课名（W18），账本只改 course 一个字段
//   GET  /state?session_id=…   /  GET /list?limit=&before=
//   POST /delete {hash}                                       -> 先删账本（标 deleted），再按前缀删 R2
//   POST /job_update {session_id, job_id, status, attempt, objects?, reason?}（W22）
//      —— RunnerHub 回写任务终态；只改这几个字段，#state 里的 note.state 随之变（规则在 core 的 jobUpdate）
// 规则全在 web-session-core.js（幂等 / confirmed_seq / seq_too_far / 批大小 / 状态机），这里只做 I/O。
// ⚠️ 30 天生命周期配在桶上（`wrangler r2 bucket lifecycle add …`），代码里不删 —— 未部署。
import {
  SessionLedger, endDecision, jobUpdate, newSession, newId, renameCourse, DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT,
} from "./web-session-core.js";

// ⚠️ **动态** import 而不是静态：本文件的测试（test-web-session.mjs）要直接 new 这个类、喂内存 storage
// 与内存 R2，而 `cloudflare:workers` 在 Node 里解析不了（ERR_UNSUPPORTED_ESM_URL_SCHEME）。
// wrangler 打包时这一段照常解析成内置模块；Node 下退化成普通 class（只用到 ctx.storage 与 env）。
let DurableObjectBase = class {};
try {
  ({ DurableObject: DurableObjectBase } = await import("cloudflare:workers"));
} catch { /* Node 测试：没这个内置模块 */ }

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const DAY_MS = 86400000;

// R2 10058 = 同一对象并发写过快。只对它重试；别的错照旧抛。
const R2_SAME_OBJECT = /\(10058\)/;
const R2_RETRY_MS = [250, 1000];
async function putRetry(bucket, key, body, sleep = (ms) => new Promise((r) => setTimeout(r, ms))) {
  for (let i = 0; ; i++) {
    try {
      return await bucket.put(key, body, { httpMetadata: { contentType: "application/x-ndjson" } });
    } catch (e) {
      if (i >= R2_RETRY_MS.length || !R2_SAME_OBJECT.test(String(e?.message || e))) throw e;
      await sleep(R2_RETRY_MS[i]);
    }
  }
}
const TOMBSTONE_READ_MS = 12 * 3600000;   // 12 h 内回 tombstone，之后一律 410（契约 §3.4、§6.3）

export class WebSessions extends DurableObjectBase {
  constructor(ctx, env) {
    super(ctx, env);
    // 显式存一份：真 DO 的基类会设 this.env / this.ctx，Node 下的替身类不会（见文件头那段）——
    // 两边都写一次比依赖基类行为稳。
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
    switch (url.pathname) {
      case "/open": return this.#open(body);
      case "/lines": return this.#lines(body);
      case "/end": return this.#end(body);
      case "/rename": return this.#rename(body);
      case "/state": return this.#state(url.searchParams.get("session_id"));
      case "/list": return this.#list(url.searchParams);
      case "/job_update": return this.#jobUpdate(body);
      case "/delete": return this.#delete(body);
      default: return json({ error: "not_found" }, 404);
    }
  }

  async #open({ hash, course, started_at, tz, install, idem }) {
    // 幂等键 24 h 内回同一节课（契约 §3.1）；过期的 key 顺手清掉，别一直留着。
    if (idem) {
      const hit = await this.storage.get(`idem:${idem}`);
      if (hit && Date.now() - hit.at < DAY_MS && await this.storage.get(`meta:${hit.sid}`)) {
        return json({ session_id: hit.sid, dedupe: true }, 200);
      }
      await this.storage.delete(`idem:${idem}`);
    }
    const session_id = newId("s_");
    const meta = { ...newSession({ session_id, course, now: started_at || new Date().toISOString(), tz, install }), hash: String(hash || "") };
    await this.storage.put(`meta:${session_id}`, meta);
    if (idem) await this.storage.put(`idem:${idem}`, { sid: session_id, at: Date.now() });
    return json({ session_id, created_at: meta.created_at }, 201);
  }

  async #lines({ session_id, lines }) {
    const meta = await this.storage.get(`meta:${session_id}`);
    if (!meta) return this.#gone();
    const known = new Map();
    for (const [k, v] of await this.storage.list({ prefix: `seq:${session_id}:` })) {
      known.set(Number(k.slice(k.lastIndexOf(":") + 1)), v);
    }
    const led = new SessionLedger(meta, known);
    const res = led.append(lines);
    // 账本拒收时一个字节都不写（含 R2）。
    if (res.error) return json({ error: res.error }, res.error === "session_deleted" ? 410 : 409);
    if (led.write.length) await this.#putBatch(meta, led.write);
    for (const [seq, state] of led.changed) await this.storage.put(`seq:${session_id}:${seq}`, state);
    await this.storage.put(`meta:${session_id}`, led.meta);
    return json(res);
  }

  // 一批 final 存成一个对象，键里带**起始 seq**：同一批重放落在同一个键，覆盖而不是堆垃圾。
  async #putBatch(meta, rows) {
    const sorted = [...rows].sort((a, b) => a.seq - b.seq);
    const nd = sorted
      .map((l) => JSON.stringify({ seq: l.seq, t: l.t ?? 0, en: l.en, zh: l.zh, src: l.src || "asr" }))
      .join("\n") + "\n";
    const key = `web/${meta.hash}/sessions/${meta.session_id}/lines-${sorted[0].seq}.json`;
    // 同一键在本实例里串行写：R2 对同一对象并发写回 10058（2026-11-20 01:21:23 UTC，同批重放 95 个 PUT
    // 撞上 → Worker 抛异常记 500）。一个通行码只有一个 DO 实例，这条队列看得见这个码的全部写。
    // 跨实例（极少：DO 迁移瞬间）仍可能撞，所以 10058 再退避重试两次（250 ms → 1 s）。
    const prev = this.#r2q.get(key) || Promise.resolve();
    const run = prev.catch(() => {}).then(() => putRetry(this.env.WEB_DATA, key, nd));
    this.#r2q.set(key, run);
    try { await run; } finally { if (this.#r2q.get(key) === run) this.#r2q.delete(key); }
  }

  #r2q = new Map();   // R2 键 → 正在写的那个 promise（同键串行）

  async #end({ session_id, ended_at, lines_total, level, idle }) {
    const meta = await this.storage.get(`meta:${session_id}`);
    if (!meta) return this.#gone();
    const job = await this.storage.get(`job:${session_id}`);
    // idle = cron 发现这课 3 h 没新句子（关页没下课）。只处理还在 live 的；已下课 / 已删的不动。
    // 一句都没有 -> 只标已结束、不建笔记任务（空壳课不值得跑笔记）。
    if (idle) {
      if (meta.state !== "live") return json({ skipped: true, state: meta.state }, 200);
      if (!meta.lines_total) {
        await this.storage.put(`meta:${session_id}`, { ...meta, state: "ended", ended_at: ended_at || new Date().toISOString(), abandoned: true });
        return json({ abandoned: true }, 200);
      }
    }
    const d = endDecision(meta, job, { lines_total, level });
    if (d.error) return json({ error: d.error }, d.error === "session_deleted" ? 410 : 400);
    // 一节课只对应一条任务：job:<session_id> 写一次，之后重复 /end 走 dedupe（不产生第二份笔记）。
    if (d.dedupe) {
      return json({ job_id: job.job_id, status: job.status, dedupe: true, conflict: d.conflict }, 200);
    }
    const ended = ended_at || new Date().toISOString();
    const task = {
      job_id: newId("j_"), session_id, kind: "note", status: "queued", attempts: 0,
      lines_total: Number.isFinite(lines_total) ? lines_total : meta.lines_total,
      queued_at: ended,
    };
    await this.storage.put(`job:${session_id}`, task);
    await this.storage.put(`meta:${session_id}`, { ...meta, state: "ended", ended_at: ended, job_id: task.job_id });
    return json({ job_id: task.job_id, status: task.status, dedupe: false }, 202);
  }

  // 改名：不存在 / 不属于这枚码 → 404；已删 → 410（同 GET 的 #gone）；body 不合法 → 400。
  // 幂等：同名再改回 200（写回同一份 meta 也只会发生一次实质变化）。
  async #rename({ session_id, course }) {
    const meta = await this.storage.get(`meta:${session_id}`);
    if (!meta) return this.#gone();
    const r = renameCourse(meta, course);
    if (r.error) {
      const status = r.error === "session_deleted" ? 410 : r.error === "not_found" ? 404 : 400;
      return json({ error: r.error, detail: r.detail }, status);
    }
    // 同名不写 storage：一次 rename 要么什么都不动，要么只动 course。
    if (r.meta.course !== meta.course) await this.storage.put(`meta:${session_id}`, r.meta);
    return json({ session_id, course: r.meta.course });
  }

  async #state(session_id) {
    const meta = await this.storage.get(`meta:${session_id}`);
    if (!meta) return this.#gone();
    const job = await this.storage.get(`job:${session_id}`);
    return json({
      session_id: meta.session_id, course: meta.course, state: meta.state,
      created_at: meta.created_at, ended_at: meta.ended_at, lines_confirmed: meta.confirmed_seq,
      // queued / running 时 note_md 为 null（前端要显示「排队中」，M6）；笔记本身由 runner 写，W12 不做。
      note: job ? { job_id: job.job_id, state: job.status, attempt: job.attempts || 0 } : null,
    });
  }

  // W22：全局 RunnerHub 回写任务状态（done / failed / 回 queued）。会话不在 / 已删 -> 同 GET 的判据；
  // job 不在 -> 404；job_id 不是这节课那条任务 -> 409（谁也不能拿别节课的 job_id 改这条账）。
  async #jobUpdate({ session_id, job_id, status, attempt, objects, reason }) {
    const meta = await this.storage.get(`meta:${session_id}`);
    if (!meta) return this.#gone();
    const job = await this.storage.get(`job:${session_id}`);
    const r = jobUpdate(job, { job_id, status, attempt, objects, reason });
    if (r.error) {
      return json({ error: r.error, ...(r.detail ? { detail: r.detail } : {}) },
        r.error === "not_found" ? 404 : r.error === "job_mismatch" ? 409 : 400);
    }
    await this.storage.put(`job:${session_id}`, r.job);
    return json({ ok: true });
  }

  // 删过之后：12 h 内回 tombstone 时间（前端能解释「刚删的」），之后一律 410。
  async #gone() {
    const t = await this.storage.get("tombstone");
    if (!t) return json({ error: "not_found" }, 404);
    if (Date.now() - t.at > TOMBSTONE_READ_MS) return json({ error: "session_deleted" }, 410);
    return json({ error: "session_deleted", tombstone: new Date(t.at).toISOString() }, 410);
  }

  async #list(params) {
    const raw = Number(params.get("limit"));
    const limit = Number.isFinite(raw) && raw > 0 ? Math.min(raw, MAX_LIST_LIMIT) : DEFAULT_LIST_LIMIT;
    const before = params.get("before") || "";
    const metas = [...(await this.storage.list({ prefix: "meta:" })).values()]
      .filter(Boolean)
      .sort((a, b) => (String(a.created_at) < String(b.created_at) ? 1 : -1));
    // `before` 是**上一页最后那节的 session_id**：从它之后接着取（契约 §3.5）。
    const from = before ? metas.findIndex((m) => m.session_id === before) + 1 : 0;
    const page = metas.slice(from, from + limit);
    const out = [];
    for (const m of page) {
      const job = await this.storage.get(`job:${m.session_id}`);
      out.push({
        session_id: m.session_id, course: m.course, state: m.state,
        created_at: m.created_at, lines_confirmed: m.confirmed_seq,
        note: job ? { job_id: job.job_id, state: job.status } : null,
      });
    }
    const more = from + limit < metas.length;
    return json({ sessions: out, next_before: more ? page.at(-1).session_id : null });
  }

  // 先账本后对象（契约 §6.3）：先把元数据 / seq 水位 / 任务删掉并落 tombstone —— 之后 lines 一律 410，
  // 不会「删了又被写回来」；再按前缀把 R2 删干净。R2 失败回 502（**不回 200**）。
  async #delete({ hash }) {
    const metas = [...(await this.storage.list({ prefix: "meta:" })).keys()];
    const jobs = [...(await this.storage.list({ prefix: "job:" })).keys()];
    const seqs = [...(await this.storage.list({ prefix: "seq:" })).keys()];
    const ids = [...(await this.storage.list({ prefix: "idem:" })).keys()];
    // DO 的多键 delete 一次最多 128 个，超了直接抛（OCR D2）—— 一节课的 seq: 就有几百个，分片删
    const all = [...metas, ...jobs, ...seqs, ...ids, "tombstone"];
    for (let i = 0; i < all.length; i += 128) await this.storage.delete(all.slice(i, i + 128));
    await this.storage.put("tombstone", { at: Date.now() });
    try {
      const objects = await this.#purge(`web/${String(hash || "")}/`);
      return json({ deleted: { sessions: metas.length, jobs: jobs.length, objects } });
    } catch {
      return json({ error: "upstream" }, 502);
    }
  }

  // 分页删（1000/批）。⚠️ 删完再 list 会前移，所以每批都从 cursor=undefined 重新列一页
  // （不靠上一批的 cursor —— 那会跳对象）。
  async #purge(prefix) {
    let n = 0;
    for (;;) {
      const page = await this.env.WEB_DATA.list({ prefix, limit: 1000 });
      if (!page.objects?.length) return n;
      await this.env.WEB_DATA.delete(page.objects.map((o) => o.key));
      n += page.objects.length;
    }
  }
}
