// Durable Object 壳 `RunnerHub`：**全局单实例**（idFromName("hub")，handler.js / runner-routes.js 都用这个名字取）。
// 任务状态机 / live 表全在 runner-hub-core.js（Node 可测），这里只做 I/O 与一件事 core 做不了的通知：
// complete / 变 failed 时调对应 `WebSessions` 实例的 POST /job_update（按 job 里的 hash 分片）。
// 内部路由（只有 Worker 自己调，鉴权在 handler / runner-routes 那一层）：POST /enqueue /claim /heartbeat
// /complete /register /touch /purge /sweep；GET /live。时间一律 ISO；错误体 {error}。
import { HubCore } from "./runner-hub-core.js";

// ⚠️ 动态 import（同 web-session.js）：Node 测试要直接 new 这个类、喂内存 storage，
// `cloudflare:workers` 在 Node 下解析不了；wrangler 打包时照常解析成内置模块。
let DurableObjectBase = class {};
try {
  ({ DurableObject: DurableObjectBase } = await import("cloudflare:workers"));
} catch { /* Node 测试：没这个内置模块 */ }

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });

export class RunnerHub extends DurableObjectBase {
  constructor(ctx, env) {
    super(ctx, env);
    this.ctx = ctx;       // 真 DO 的基类会设，Node 下的替身类不会 —— 两边都写一次更稳（同 web-session.js）
    this.env = env;
    this.storage = ctx.storage;
    this.core = new HubCore(ctx.storage, () => Date.now());
  }

  async fetch(request) {
    const url = new URL(request.url);
    let body = {};
    if (request.method === "POST") {
      try { body = await request.json(); } catch { return json({ error: "bad_body" }, 400); }
      // OCR E1：字面 null / 数组 / 数字 → core 解构会抛 TypeError（500）；契约 §1 要 400
      if (body === null || typeof body !== "object" || Array.isArray(body)) return json({ error: "bad_body" }, 400);
    }
    switch (url.pathname) {
      case "/enqueue": return this.#send(await this.core.enqueue(body));
      case "/claim": return this.#send(await this.core.claim(body));
      case "/heartbeat": return this.#send(await this.core.heartbeat(body));
      case "/complete": return this.#send(await this.core.complete(body));
      case "/register": return this.#send(await this.core.register(body));
      case "/touch": return this.#send(await this.core.touch(body));
      case "/rename": return this.#send(await this.core.rename(body));
      case "/unregister": return this.#send(await this.core.unregister(body));
      case "/purge": return this.#send(await this.core.purge(body));
      case "/live": return this.#send(await this.core.liveList());
      case "/admin_state": return this.#send(await this.core.adminState(body));
      case "/sweep": {
        const res = await this.core.sweep();
        const idle = res.out.idle || [];
        delete res.out.idle;
        res.out.idle_ended = await this.#endIdle(idle);
        return this.#send(res);
      }
      default: return json({ error: "not_found" }, 404);
    }
  }

  // core 只回纯数据；通知在这里落地。通知失败不影响响应（账本已是权威状态，网页只是慢一拍看到）。
  async #send({ status, out, notify }) {
    // OCR E1：通知最多等 2 s —— 慢了放后台跑，别让全局单实例的 claim / complete 跟着某个忙的 WebSessions 一起慢
    if (notify?.length) {
      let t;
      await Promise.race([
        Promise.allSettled(notify.map((n) => this.#jobUpdate(n))),
        new Promise((r) => { t = setTimeout(r, 2000); }),
      ]);
      clearTimeout(t);
    }
    return status === 204 ? new Response(null, { status: 204 }) : json(out, status);
  }

  // 关页没下课的课：把每码账本那一节标成已结束（ended_at = 最后一句的时间）。
  // 有句子 -> DO 建好笔记任务（202），这里再入全局队列；一句都没有 -> DO 只收尾不建任务（200 abandoned）。
  // 任何一节失败只记日志、不影响别的（cron 没有重试；下一轮 live 已删就不会再来，账本里残留 live 属已知小缺口）。
  async #endIdle(idle) {
    if (!this.env.WEB_SESSIONS) return 0;
    let n = 0;
    for (const v of idle) {
      try {
        const stub = this.env.WEB_SESSIONS.get(this.env.WEB_SESSIONS.idFromName(v.hash));
        const r = await stub.fetch(new Request("https://web-sessions/end", {
          method: "POST",
          body: JSON.stringify({ session_id: v.session_id, ended_at: v.last_line_at, idle: true }),
        }));
        const jb = await r.json().catch(() => ({}));
        if (r.status === 202 && jb.job_id) {
          await this.core.enqueue({
            hash: v.hash, session_id: v.session_id, job_id: jb.job_id, ended_at: v.last_line_at,
            lines_total: v.high_water, fallback: { course: v.course, tz: v.tz, started_at: v.started_at },
          });
        }
        n += 1;
      } catch (e) {
        console.log(JSON.stringify({ runner_hub: "idle_end_failed", session_id: v.session_id, msg: String(e).slice(0, 120) }));
      }
    }
    return n;
  }

  async #jobUpdate({ hash, session_id, job_id, status, attempt, objects, reason }) {
    if (!this.env.WEB_SESSIONS) return;
    try {
      const stub = this.env.WEB_SESSIONS.get(this.env.WEB_SESSIONS.idFromName(hash));
      const r = await stub.fetch(new Request("https://web-sessions/job_update", {
        method: "POST",
        body: JSON.stringify({
          session_id, job_id, status, attempt,
          ...(objects !== undefined ? { objects } : {}),
          ...(reason !== undefined ? { reason } : {}),
        }),
      }));
      await r.body?.cancel?.();
    } catch (e) {
      console.log(JSON.stringify({ runner_hub: "job_update_failed", job_id, msg: String(e).slice(0, 120) }));
    }
  }
}
