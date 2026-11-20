// 全局任务队列 `RunnerHub` 的**纯逻辑**（W22，契约 docs/CONTRACT-runner-20261120.md §2）：
// 笔记任务状态机（queued→running→done/failed，租约 90 s、attempts 上限 3、拒收规则）+ 正在上的课
// （live 表，3 h 无逐句即算停）。不 import cloudflare:*，storage 只要 async get/put/delete/list({prefix})，
// 时间注入（构造传 now()，返回 ms），Node 直接测；DO 壳 runner-hub.js 只做 I/O 与 /job_update 通知。
// 键：`job:<id>`、`live:<hash>:<sid>`（字段名照契约 §2 一个不改）；`gonejob:<id>` = purge 删掉未完成
// 任务时落的标记 —— 之后 heartbeat/complete 一律 410（防「删了又长回来」，API 文档 §6.3）。

import { SESSION_ID_RE } from "./web-session-core.js";

export const LEASE_MS = 90 * 1000;
export const MAX_ATTEMPTS = 3;
export const STALE_LIVE_MS = 3 * 3600 * 1000;
// 网页版课名是「下课再起」（11-19 定）：没名字的课下课后先等这么久再领，给用户起名的时间（Claude 11-20）
export const NAME_GRACE_MS = 120 * 1000;
// 网页开课时没名字发的占位（web/src/lib/live/connect.ts 的 UNNAMED，服务端要求 course 非空）—— 两处要一起改
export const UNNAMED_COURSE = "未命名";
// done / failed 的 job 与 gonejob 标记留 7 天就清（claim 每 5 s 全表扫一次，不能无限长）
export const JOB_KEEP_MS = 7 * 86400 * 1000;

const HASH_RE = /^[0-9a-f]{16}$/;
const iso = (ms) => new Date(ms).toISOString();
const at = (s) => Date.parse(s);
const J = (status, out, notify) => ({ status, out, notify });

function randomHex(bytes) {
  const b = new Uint8Array(bytes);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

export class HubCore {
  constructor(storage, now = () => Date.now()) {
    this.storage = storage;
    this.now = now;
  }

  // 开课：写一条 live（重复 register 覆盖同键 = 幂等）。
  async register({ hash, session_id, course, tz, started_at }) {
    if (!HASH_RE.test(hash || "") || !SESSION_ID_RE.test(session_id || "")) return J(400, { error: "bad_body" });
    const nowIso = iso(this.now());
    await this.storage.put(`live:${hash}:${session_id}`, {
      hash, session_id, course: String(course || ""), tz: String(tz || ""),
      started_at: started_at || nowIso, last_line_at: nowIso, high_water: 0,
    });
    return J(200, { ok: true });
  }

  // 逐句到货：只更新已有的 live（register 丢了就不无中生有 —— cron 3 h 后自清）。
  async touch({ hash, session_id, high_water }) {
    const key = `live:${hash}:${session_id}`;
    const live = await this.storage.get(key);
    if (!live) return J(200, { ok: false });
    live.last_line_at = iso(this.now());
    if (Number.isFinite(high_water)) live.high_water = high_water;
    await this.storage.put(key, live);
    return J(200, { ok: true });
  }

  // 课后改名（handler 的 rename 成功后通知）：live 与**还没做完**的 job 一起改，笔记标题用新名字。
  async rename({ hash, session_id, course }) {
    if (!HASH_RE.test(hash || "") || !SESSION_ID_RE.test(session_id || "")) return J(400, { error: "bad_body" });
    const c = String(course || "");
    const lk = `live:${hash}:${session_id}`;
    const live = await this.storage.get(lk);
    if (live) await this.storage.put(lk, { ...live, course: c });
    let jobs = 0;
    for (const [k, job] of await this.storage.list({ prefix: "job:" })) {
      if (job.hash !== hash || job.session_id !== session_id) continue;
      if (job.state === "done" || job.state === "failed") continue;
      await this.storage.put(k, { ...job, course: c, updated: iso(this.now()) });
      jobs += 1;
    }
    return J(200, { ok: true, jobs });
  }

  async unregister({ hash, session_id }) {
    await this.storage.delete(`live:${hash}:${session_id}`);
    return J(200, { ok: true });
  }

  // 下课：一节课一条任务。course / tz / started_at 从 live 表带过来（开课 register 存的），
  // 顺手删 live —— 「下课就不再算正在上的课」。job 已在（cron 补跑 / 重放）→ 幂等不覆盖。
  async enqueue({ hash, session_id, job_id, ended_at, lines_total, level, fallback }) {
    if (!HASH_RE.test(hash || "") || !SESSION_ID_RE.test(session_id || "") || typeof job_id !== "string" || !job_id) {
      return J(400, { error: "bad_body" });
    }
    if (await this.storage.get(`job:${job_id}`)) return J(200, { ok: true, dedupe: true });
    const live = await this.storage.get(`live:${hash}:${session_id}`);
    const nowIso = iso(this.now());
    await this.storage.put(`job:${job_id}`, {
      job_id, kind: "note", hash, session_id,
      // fallback：sweep 已删了 live 才补的老底（course / tz / started_at），正常下课用不上
      course: live?.course || fallback?.course || "", tz: live?.tz || fallback?.tz || "",
      started_at: live?.started_at || fallback?.started_at || null,
      ended_at: ended_at || nowIso, lines_total: Number.isFinite(lines_total) ? lines_total : 0,
      level: typeof level === "string" && level ? level : "std",
      state: "queued", attempts: 0, lease: null, lease_until: null, runner: null,
      reason: null, objects: null, created: nowIso, updated: nowIso,
    });
    await this.storage.delete(`live:${hash}:${session_id}`);
    return J(200, { ok: true });
  }

  // 领任务：先把租约过期的 running 按 §5 回收（attempts<3 → queued，否则 failed 并通知），
  // 再领第一条 queued（DO 单实例天然串行：两个 runner 抢同一任务不会都领到）。
  async claim({ runner, kinds }) {
    const notify = [];
    const ks = Array.isArray(kinds) ? kinds.filter((k) => typeof k === "string") : ["note"];
    if (!ks.length) return J(400, { error: "bad_body" }, notify);
    await this.#reclaim(notify);
    const queued = [...(await this.storage.list({ prefix: "job:" })).entries()]
      .filter(([, j]) => j.state === "queued" && ks.includes(j.kind))
      .sort(([, a], [, b]) => at(a.created) - at(b.created));          // 先下课的先做（键是随机 job_id）
    for (const [k, job] of queued) {
      const now = this.now();
      const unnamed = !job.course || job.course === UNNAMED_COURSE;
      if (unnamed && now - at(job.created) < NAME_GRACE_MS) continue;  // 还在等用户起课名
      job.state = "running";
      job.attempts = (job.attempts || 0) + 1;
      job.lease = randomHex(16);
      job.lease_until = iso(now + LEASE_MS);
      job.runner = String(runner || "");
      job.updated = iso(now);
      await this.storage.put(k, job);
      return J(200, {
        job_id: job.job_id, kind: job.kind, attempt: job.attempts, lease: job.lease, lease_until: job.lease_until,
        input: {
          hash: job.hash, session_id: job.session_id, course: job.course, tz: job.tz,
          started_at: job.started_at, ended_at: job.ended_at,
          lines_total: job.lines_total, level: job.level,
        },
      }, notify);
    }
    return J(204, {}, notify);
  }

  async heartbeat({ job_id, attempt, lease }) {
    const miss = await this.#miss(job_id);
    if (miss) return miss;
    const job = await this.storage.get(`job:${job_id}`);
    if (job.attempts !== attempt) return J(409, { error: "stale_attempt" });
    if (job.state !== "running" || job.lease !== lease) return J(409, { error: "lease_mismatch" });
    job.lease_until = iso(this.now() + LEASE_MS);
    job.updated = job.lease_until;
    await this.storage.put(`job:${job_id}`, job);
    return J(200, { lease_until: job.lease_until });
  }

  // complete 两段式（W22 的 seam，见 runner-routes.js /complete 的注释）：
  //   校验段（无 commit）：只验 attempt+lease，回 validated；已 done 且同 attempt → 幂等短路。
  //   提交段（commit:true，Worker 已把三个对象写进 R2）：置 done、挂 objects、通知 /job_update。
  // ok:false 一段到底：验过即按 attempts 回 queued / failed 并通知。
  async complete({ job_id, attempt, lease, ok, reason, commit, objects }) {
    const miss = await this.#miss(job_id);
    if (miss) return miss;
    const job = await this.storage.get(`job:${job_id}`);
    if (job.state === "done") {
      // 幂等以 (job_id, attempt) 为键：同 attempt 重复交 → 200 同一份；不同 attempt（老 runner 迟到）→ 拒收。
      return job.attempts === attempt
        ? J(200, { job_id, state: "done", attempt, objects: job.objects || [] })
        : J(409, { error: "stale_attempt" });
    }
    if (job.state === "failed") return J(409, { error: "stale_attempt" });
    if (job.attempts !== attempt) return J(409, { error: "stale_attempt" });
    if (job.state !== "running" || job.lease !== lease) return J(409, { error: "lease_mismatch" });
    if (ok === false) {
      const notify = [];
      this.#retire(job, notify, typeof reason === "string" && reason ? reason : "runner_failed");
      await this.storage.put(`job:${job_id}`, job);
      return J(200, { job_id, state: job.state, attempt: job.attempts }, notify);
    }
    if (!commit) return J(200, { validated: true, hash: job.hash, session_id: job.session_id });
    job.state = "done";
    job.objects = Array.isArray(objects) ? objects : [];
    job.updated = iso(this.now());
    await this.storage.put(`job:${job_id}`, job);
    return J(200, { job_id, state: "done", attempt: job.attempts, objects: job.objects }, [{
      hash: job.hash, session_id: job.session_id, job_id: job.job_id,
      status: "done", attempt: job.attempts, objects: job.objects,
    }]);
  }

  // 立即删除：该 hash 的 live 全删；**未完成**的 job 删掉并落 gonejob 标记（heartbeat/complete 之后 410）。
  // done / failed 的 job 记录留着（claim 永远不会再领它们，每码账本那边的 job 已随 /delete 删掉）。
  async purge({ hash }) {
    if (!HASH_RE.test(hash || "")) return J(400, { error: "bad_body" });
    let live = 0, jobs = 0;
    for (const [k] of await this.storage.list({ prefix: `live:${hash}:` })) {
      await this.storage.delete(k);
      live += 1;
    }
    const nowIso = iso(this.now());
    for (const [k, job] of await this.storage.list({ prefix: "job:" })) {
      if (job.hash !== hash || job.state === "done" || job.state === "failed") continue;
      await this.storage.put(`gonejob:${job.job_id}`, { at: nowIso });
      await this.storage.delete(k);
      jobs += 1;
    }
    return J(200, { deleted: { live, jobs } });
  }

  // 看板：各状态计数 + 最近 limit 条任务（新的在前；不含租约串）+ 正在上的课（含已过 3 h 的，标 stale）。
  async adminState({ limit } = {}) {
    const n = Math.min(Math.max(Number.isInteger(limit) ? limit : 50, 1), 500);
    const jobs = [...(await this.storage.list({ prefix: "job:" })).values()];
    const counts = { queued: 0, running: 0, done: 0, failed: 0 };
    for (const j of jobs) counts[j.state] = (counts[j.state] ?? 0) + 1;
    const recent = jobs
      .sort((a, b) => at(b.updated) - at(a.updated))
      .slice(0, n)
      .map((j) => ({
        job_id: j.job_id, kind: j.kind, hash: j.hash, session_id: j.session_id, course: j.course, tz: j.tz,
        state: j.state, attempts: j.attempts, runner: j.runner, reason: j.reason, lines_total: j.lines_total,
        level: j.level, created: j.created, updated: j.updated, ended_at: j.ended_at, lease_until: j.lease_until,
      }));
    const cut = this.now() - STALE_LIVE_MS;
    const live = [...(await this.storage.list({ prefix: "live:" })).values()]
      .map((v) => ({ ...v, stale: at(v.last_line_at) < cut }));
    return J(200, { now: iso(this.now()), counts, jobs: recent, live });
  }

  // 正在上的课（给实时总结 runner 轮询）：只回 last_line_at 在 3 h 内的。
  async liveList() {
    const cut = this.now() - STALE_LIVE_MS;
    const sessions = [];
    for (const v of (await this.storage.list({ prefix: "live:" })).values()) {
      if (at(v.last_line_at) < cut) continue;
      sessions.push({
        hash: v.hash, session_id: v.session_id, course: v.course, tz: v.tz,
        started_at: v.started_at, last_line_at: v.last_line_at, high_water: v.high_water,
      });
    }
    return J(200, { sessions });
  }

  // cron 补漏：超 3 h 没逐句的 live 删掉；顺带回收租约过期的 running（与 claim 同一套回收规则）。
  // 被删的 live 同时列在 out.idle 里 —— 壳（runner-hub.js）据此把每码账本那节课也标成已结束
  // （关页没下课的课不能永远停在 live），有句子的再补一条笔记任务。
  async sweep() {
    const notify = [];
    let live = 0;
    const idle = [];
    const cut = this.now() - STALE_LIVE_MS;
    for (const [k, v] of await this.storage.list({ prefix: "live:" })) {
      if (at(v.last_line_at) >= cut) continue;
      idle.push({
        hash: v.hash, session_id: v.session_id, course: v.course, tz: v.tz,
        started_at: v.started_at, last_line_at: v.last_line_at, high_water: v.high_water || 0,
      });
      await this.storage.delete(k);
      live += 1;
    }
    await this.#reclaim(notify);
    const old = this.now() - JOB_KEEP_MS;
    let jobs = 0;
    for (const [k, job] of await this.storage.list({ prefix: "job:" })) {
      if ((job.state === "done" || job.state === "failed") && at(job.updated) < old) {
        await this.storage.delete(k);
        jobs += 1;
      }
    }
    for (const [k, g] of await this.storage.list({ prefix: "gonejob:" })) {
      if (at(g.at) < old) await this.storage.delete(k);
    }
    return J(200, { live_deleted: live, jobs_deleted: jobs, idle }, notify);
  }

  // running -> queued（attempts<3）/ failed（到上限），租约与 runner 清空；failed 落一条通知。
  #retire(job, notify, reason) {
    job.updated = iso(this.now());
    job.reason = reason || null;
    job.lease = null;
    job.lease_until = null;
    job.runner = null;
    if (job.attempts >= MAX_ATTEMPTS) {
      job.state = "failed";
      notify.push({
        hash: job.hash, session_id: job.session_id, job_id: job.job_id,
        status: "failed", attempt: job.attempts, reason: job.reason,
      });
    } else {
      job.state = "queued";
    }
  }

  async #reclaim(notify) {
    const now = this.now();
    for (const [k, job] of await this.storage.list({ prefix: "job:" })) {
      if (job.state !== "running" || !job.lease_until || at(job.lease_until) >= now) continue;
      this.#retire(job, notify, "lease_expired");
      await this.storage.put(k, job);
    }
  }

  // job 不在：purge 删过（有 gonejob 标记）→ 410；从没有过 → 404。
  async #miss(job_id) {
    if (typeof job_id !== "string" || !job_id) return J(400, { error: "bad_body" });
    if (await this.storage.get(`job:${job_id}`)) return null;
    return (await this.storage.get(`gonejob:${job_id}`))
      ? J(410, { error: "session_deleted" })
      : J(404, { error: "not_found" });
  }
}
