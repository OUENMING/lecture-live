// runner 路由（W22，契约 CONTRACT-runner-20261120.md §3/§4）：VPS 上的 Python runner 调 Worker 领任务 /
// 拉逐句 / 交笔记 / 轮询正在上的课 / 写总结。鉴权只有一道：`Authorization: Bearer <RUNNER_TOKEN>`
// （Worker secret，fail-closed：没配就谁都不认）。不吃通行码、不进 passGate、不加 CORS（浏览器不调）。
// 同文件还有两条**浏览器**读产物路由（§4：note / summary）—— 鉴权复用 webRoute 那套（通行码 + Origin +
// install + 查码），路由匹配 matchArtifactPath 挂在 webMatch；这里只读 R2，不碰别的。
// hub 调用助手（hubFetch / hubSend / withTimeout）也在这里 —— handler.js 的四个通知点（§2 表）用它。
import { SESSION_ID_RE } from "./web-session-core.js";

const COMPLETE_MAX = 8 * 1024 * 1024;   // 契约 §4.3：产物合计 8 MiB 上限
const SUMMARY_MAX = 1024 * 1024;        // 契约 §3：summary.json ≤ 1 MiB
const SMALL_MAX = 64 * 1024;
const HASH_RE = /^[0-9a-f]{16}$/;
// hub 出错码 -> HTTP 状态（同 handler.js 的 DO_ERROR_STATUS 立场：认得的照翻，认不得的 502）。
const HUB_STATUS = { bad_body: 400, not_found: 404, lease_mismatch: 409, stale_attempt: 409, session_deleted: 410 };
const RUNNER_METHODS = {
  "/v1/runner/claim": "POST", "/v1/runner/heartbeat": "POST", "/v1/runner/complete": "POST",
  "/v1/runner/lines": "GET", "/v1/runner/live": "GET", "/v1/runner/summary": "PUT",
};

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const err = (code, status, detail) => json(detail ? { error: code, detail } : { error: code }, status);

// ---- hub 调用（handler.js 的四个通知点也用这两个）----
export function hubFetch(env, path, payload) {
  if (!env?.RUNNER_HUB) return Promise.resolve(null);
  const stub = env.RUNNER_HUB.get(env.RUNNER_HUB.idFromName("hub"));
  return stub.fetch(new Request(`https://runner-hub${path}`, {
    method: payload === undefined ? "GET" : "POST",
    body: payload === undefined ? undefined : JSON.stringify(payload),
  }));
}
// fire-and-forget：发出去、把响应体排干（别挂着不读）、永不 reject —— 失败由调用方 waitUntil 吞掉。
export function hubSend(env, path, payload) {
  return hubFetch(env, path, payload).then(async (r) => {
    await r?.body?.cancel?.().catch?.(() => {});
    return r;
  });
}
// 带上限的 await（契约 §2 表：/end 的 enqueue 最多等 2 s，失败只记日志照常回 202）。
export function withTimeout(p, ms) {
  if (!p) return Promise.resolve(null);
  let t;   // 主 promise 先到就清掉定时器，别让 2 s 的 timer 挂着拖慢进程退出
  const guard = new Promise((_, rej) => { t = setTimeout(() => rej(new Error("hub timeout")), ms); });
  return Promise.race([p.finally(() => clearTimeout(t)), guard]);
}

async function relayHub(p) {
  const r = await p;
  if (!r) return err("upstream", 503, "runner_hub");
  if (r.status === 204) return new Response(null, { status: 204 });
  const b = await r.json().catch(() => ({ error: "upstream" }));
  if (b.error) return json(b, HUB_STATUS[b.error] ?? 502);
  return json(b, r.status);
}

// 领到任务后用每码账本里的**当前**课名 / 开课时间覆盖 hub 那份（hub 的来自开课时，课名多半还是空的；
// rename 通知丢了也靠这里补上）。账本读不到就原样交出，不因此拦住任务（Claude 11-20）。
async function freshInput(env, res) {
  if (res.status !== 200 || !env.WEB_SESSIONS) return res;
  const job = await res.json();
  try {
    const st = await sessionState(env, job.input.hash, job.input.session_id);
    if (st.status === 200) {
      const m = await st.json();
      if (typeof m.course === "string" && m.course) job.input.course = m.course;
      if (!job.input.started_at && m.created_at) job.input.started_at = m.created_at;
    } else {
      await st.body?.cancel?.();
    }
  } catch { /* 账本读不到：用 hub 那份 */ }
  return json(job, 200);
}

async function readJson(request, cap) {
  if (Number(request.headers.get("content-length")) > cap) return { error: "too_large" };
  const text = await request.text().catch(() => "");
  if (new TextEncoder().encode(text).byteLength > cap) return { error: "too_large" };
  try { return { json: text ? JSON.parse(text) : {} }; } catch { return { json: null }; }
}

// ---- 浏览器读产物（§4；鉴权在上层 webRoute 已过，这里只认 hash 与 session_id 形状）----
export function matchArtifactPath(method, path) {
  let m = path.match(/^\/v1\/web\/session\/(s_[0-9a-f]{12})\/note$/);
  if (m) return method === "GET" ? { kind: "note", id: m[1] } : null;
  m = path.match(/^\/v1\/web\/session\/(s_[0-9a-f]{12})\/summary$/);
  return m && method === "GET" ? { kind: "summary", id: m[1] } : null;
}

export async function artifactRoute(env, hash, hit, params) {
  if (!env.WEB_DATA) return err("upstream", 503, "web_data");
  if (hit.kind === "summary") {
    const obj = await env.WEB_DATA.get(`web/${hash}/sessions/${hit.id}/summary.json`);
    if (!obj) return err("not_ready", 404);
    return new Response(obj.body, { status: 200, headers: { "content-type": "application/json; charset=utf-8" } });
  }
  const fmt = params.get("fmt") || "html";
  if (fmt !== "html" && fmt !== "md") return err("bad_body", 400, "fmt");
  const name = fmt === "md" ? "note.md" : "note.html";
  const obj = await env.WEB_DATA.get(`web/${hash}/sessions/${hit.id}/${name}`);
  if (!obj) return err("not_ready", 404);
  const type = fmt === "md" ? "text/markdown; charset=utf-8" : "text/html; charset=utf-8";
  return new Response(obj.body, { status: 200, headers: { "content-type": type } });
}

// ---- runner 六条（§3）----
export async function runnerRoute(request, env, path) {
  const method = RUNNER_METHODS[path];
  if (!method || request.method !== method) return new Response("not found", { status: 404 });
  // fail-closed（同 upload-worker 的 INGEST_TOKEN）：secret 没配时 "Bearer undefined" 不能放行。
  const expected = env.RUNNER_TOKEN ? `Bearer ${env.RUNNER_TOKEN}` : null;
  if (!expected || (request.headers.get("authorization") || "") !== expected) return err("unauthorized", 401);

  if (path === "/v1/runner/live") return relayHub(hubFetch(env, "/live"));
  if (path === "/v1/runner/claim" || path === "/v1/runner/heartbeat") {
    const body = await readJson(request, SMALL_MAX);
    if (body.error) return err(body.error, 413);
    if (path === "/v1/runner/heartbeat") return relayHub(hubFetch(env, "/heartbeat", body.json));
    return freshInput(env, await relayHub(hubFetch(env, "/claim", body.json)));
  }
  if (path === "/v1/runner/complete") return completeRoute(request, env);
  const url = new URL(request.url);
  if (path === "/v1/runner/lines") return linesRoute(env, url);
  return summaryRoute(request, env, url);
}

// complete 两段式（R2 不能塞进 DO：hub 只做串行裁决，大内容走 Worker 直写）：
// ① hub 校验租约 / 次数（含 done 幂等短路 —— 一个字节不写 R2；ok:false 在 hub 一段到底）；
// ② Worker 写三个 R2 对象（note.md / note.html / note.meta.json = meta+attempt+finished_at）；
// ③ hub 提交（置 done + 挂 objects + 通知 /job_update）。R2 写失败回 502，账本仍 running
//   （租约到期被下个 claim 回收重跑，契约 §4.3「账本不改成 done」）。
async function completeRoute(request, env) {
  const body = await readJson(request, COMPLETE_MAX);
  if (body.error) return err(body.error, 413);
  const b = body.json;
  if (!b || typeof b.job_id !== "string" || !Number.isInteger(b.attempt) || b.attempt < 1
    || typeof b.lease !== "string" || (b.ok !== true && b.ok !== false)) return err("bad_body", 400);
  if (b.ok === true && (!b.note || typeof b.note.md !== "string" || typeof b.note.html !== "string")) {
    return err("bad_body", 400, "note");
  }
  const v = await relayHub(hubFetch(env, "/complete", {
    job_id: b.job_id, attempt: b.attempt, lease: b.lease, ok: b.ok, reason: b.reason,
  }));
  if (v.status !== 200) return v;
  const vb = await v.json();
  if (vb.state) return json(vb, 200);   // done 幂等短路 / ok:false 的终态 —— 都不再碰 R2
  if (!env.WEB_DATA) return err("upstream", 503, "web_data");
  const base = `web/${vb.hash}/sessions/${vb.session_id}`;
  const objects = [`${base}/note.md`, `${base}/note.html`, `${base}/note.meta.json`];
  const meta = {
    ...(b.meta && typeof b.meta === "object" && !Array.isArray(b.meta) ? b.meta : {}),
    attempt: b.attempt, finished_at: new Date().toISOString(),
  };
  try {
    await env.WEB_DATA.put(objects[0], b.note.md, { httpMetadata: { contentType: "text/markdown" } });
    await env.WEB_DATA.put(objects[1], b.note.html, { httpMetadata: { contentType: "text/html" } });
    await env.WEB_DATA.put(objects[2], JSON.stringify(meta), { httpMetadata: { contentType: "application/json" } });
  } catch (e) {
    console.log(JSON.stringify({ runner: "r2_write_failed", job_id: b.job_id, msg: String(e).slice(0, 120) }));
    return err("upstream", 502);
  }
  const fin = await relayHub(hubFetch(env, "/complete", {
    job_id: b.job_id, attempt: b.attempt, lease: b.lease, ok: true, commit: true, objects,
  }));
  // OCR E1：/delete 落在「校验」与「写 R2」之间 → 三个对象写进了已删的前缀；提交回 410 就清掉，别留孤儿。
  if (fin.status === 410) await env.WEB_DATA.delete(objects).catch(() => {});
  return fin;
}

// 拉逐句：该课所有 `lines-<seq>.json` 批全列出来，合并、只留 seq>after、升序、同 seq 后写的批赢（去重）。
// 课在不在 / 删没删问每码 DO（/state 的 404 / 410 语义正好复用）。
async function linesRoute(env, url) {
  const hash = url.searchParams.get("hash") || "";
  const sid = url.searchParams.get("session_id") || "";
  const after = Number(url.searchParams.get("after") || 0);
  if (!HASH_RE.test(hash) || !SESSION_ID_RE.test(sid) || !Number.isInteger(after) || after < 0) {
    return err("bad_body", 400);
  }
  if (!env.WEB_DATA || !env.WEB_SESSIONS) return err("upstream", 503, "web_data");
  const st = await sessionState(env, hash, sid);
  if (st.status === 404) return err("not_found", 404);
  if (st.status === 410) return err("session_deleted", 410);
  if (st.status !== 200) return err("upstream", 502);
  await st.body?.cancel?.();

  const rows = new Map();
  let cursor;
  do {
    const page = await env.WEB_DATA.list({
      prefix: `web/${hash}/sessions/${sid}/lines-`, limit: 1000, cursor,
    });
    // OCR E1：一节长课上千批，串行 get 会超 runner 的 30 s → 每 16 个一组并行；get 回 null（刚被删）跳过
    const texts = [];
    for (let i = 0; i < page.objects.length; i += 16) {
      texts.push(...await Promise.all(page.objects.slice(i, i + 16).map(async (o) => {
        const obj = await env.WEB_DATA.get(o.key);
        return obj ? obj.text() : "";
      })));
    }
    for (const text of texts) {
      for (const line of text.split("\n")) {
        if (!line.trim()) continue;
        let row;
        try { row = JSON.parse(line); } catch { continue; }
        if (Number.isInteger(row.seq) && row.seq > after) rows.set(row.seq, row);
      }
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  const nd = [...rows.keys()].sort((a, b) => a - b).map((k) => JSON.stringify(rows.get(k))).join("\n");
  return new Response(nd ? `${nd}\n` : "", { status: 200, headers: { "content-type": "application/x-ndjson" } });
}

// 写实时总结（W24 调）：≤ 1 MiB 的 JSON 原文落 R2。课不在 404、已删 410（防「删了又长回来」）。
async function summaryRoute(request, env, url) {
  const hash = url.searchParams.get("hash") || "";
  const sid = url.searchParams.get("session_id") || "";
  if (!HASH_RE.test(hash) || !SESSION_ID_RE.test(sid)) return err("bad_body", 400);
  if (!env.WEB_DATA || !env.WEB_SESSIONS) return err("upstream", 503, "web_data");
  if (Number(request.headers.get("content-length")) > SUMMARY_MAX) return err("too_large", 413);
  const blob = await readCapped(request.body, SUMMARY_MAX);
  if (!blob) return err("too_large", 413);
  const text = await blob.text();
  try { JSON.parse(text); } catch { return err("bad_body", 400, "json"); }
  const st = await sessionState(env, hash, sid);
  if (st.status === 404) return err("not_found", 404);
  if (st.status === 410) return err("session_deleted", 410);
  if (st.status !== 200) return err("upstream", 502);
  await st.body?.cancel?.();
  await env.WEB_DATA.put(`web/${hash}/sessions/${sid}/summary.json`, text,
    { httpMetadata: { contentType: "application/json" } });
  return new Response(null, { status: 204 });
}

async function sessionState(env, hash, sid) {
  const stub = env.WEB_SESSIONS.get(env.WEB_SESSIONS.idFromName(hash));
  return stub.fetch(new Request(`https://web-sessions/state?session_id=${sid}`));
}

// 边读边计数（同 web-telemetry.js 的 readCapped）：超了立刻取消，剩下的字节不进内存。
async function readCapped(body, cap) {
  const reader = body?.getReader?.();
  if (!reader) return new Blob([]);
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return new Blob(chunks);
    total += value.byteLength;
    if (total > cap) {
      await reader.cancel().catch(() => {});
      return null;
    }
    chunks.push(value);
  }
}
