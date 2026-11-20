// W22 判据：全局任务队列 RunnerHub 的**纯逻辑**（docs/CONTRACT-runner-20261120.md §2、API 文档 §5）。
// 直接 new HubCore，内存 storage，时间注入 —— 不碰 DO / R2 / 网络。
//
//   cd server/asr-worker && node --test test-runner-hub.mjs
//
// 钉住什么：
//   1. 状态机全部分支：enqueue / claim（两个 runner 抢同一任务不会都领到）/ heartbeat 拒收 /
//      complete 幂等（同 attempt 不写第二份）、旧 runner 迟到被拒、3 次失败变 failed、租约过期回收
//   2. live 表：register / touch / 3 h 过滤 / purge / sweep
//   3. 删数据之后：heartbeat / complete 一律 410（gonejob 标记），done 的 job 记录保留
import { test } from "node:test";
import assert from "node:assert/strict";
import { HubCore, LEASE_MS, MAX_ATTEMPTS, STALE_LIVE_MS } from "./src/runner-hub-core.js";

const T0 = Date.UTC(2026, 10, 20, 9);
const H = "a".repeat(16);
const SID = "s_000000000001";
const SID2 = "s_000000000002";
const ENQ = { hash: H, session_id: SID, job_id: "j_000000000001", ended_at: new Date(T0).toISOString(), lines_total: 12, level: "std" };

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

function core() {
  let now = T0;
  const c = new HubCore(memStorage(), () => now);
  c.jump = (ms) => { now += ms; };
  c.set = (ms) => { now = ms; };
  return c;
}

// 开一节课并下课入队（live 带出 course / tz / started_at）。
async function seeded(c, { course = "ECON10740", tz = "Europe/Dublin" } = {}) {
  await c.register({ hash: H, session_id: SID, course, tz, started_at: new Date(T0).toISOString() });
  const r = await c.enqueue(ENQ);
  assert.equal(r.status, 200);
  return c.storage.m.get(`job:${ENQ.job_id}`);
}

test("H1 enqueue：live 带出 course/tz/started_at，入队即删 live；重复入队幂等", async () => {
  const c = core();
  await c.register({ hash: H, session_id: SID, course: "微观经济", tz: "Asia/Shanghai" });
  const r = await c.enqueue(ENQ);
  assert.equal(r.status, 200);
  assert.equal(c.storage.m.get(`live:${H}:${SID}`), undefined, "下课即不再算正在上的课");
  const job = c.storage.m.get(`job:${ENQ.job_id}`);
  assert.equal(job.state, "queued");
  assert.equal(job.kind, "note");
  assert.equal(job.course, "微观经济");
  assert.equal(job.tz, "Asia/Shanghai");
  assert.equal(job.attempts, 0);
  assert.equal(job.lease, null);
  const again = await c.enqueue(ENQ);
  assert.deepEqual(again.out, { ok: true, dedupe: true }, "同 job_id 再入队不覆盖");
  assert.equal(c.storage.m.get(`job:${ENQ.job_id}`).course, "微观经济");
});

test("H2 claim：领走即租（attempt+1、32 hex、90 s）；没活 204；两个 runner 抢同一任务只有一个领到", async () => {
  const c = core();
  await seeded(c);
  const a = await c.claim({ runner: "vps-a", kinds: ["note"] });
  assert.equal(a.status, 200);
  assert.equal(a.out.attempt, 1);
  assert.match(a.out.lease, /^[0-9a-f]{32}$/);
  assert.equal(a.out.lease_until, new Date(T0 + LEASE_MS).toISOString());
  assert.deepEqual(Object.keys(a.out.input).sort(),
    ["course", "ended_at", "hash", "level", "lines_total", "session_id", "started_at", "tz"]);
  assert.equal(a.out.input.course, "ECON10740");
  const b = await c.claim({ runner: "vps-b", kinds: ["note"] });
  assert.equal(b.status, 204, "只有一个任务：B 领不到");
  const none = await c.claim({ runner: "vps-b", kinds: ["terms"] });
  assert.equal(none.status, 204, "kinds 不匹配也领不到");
  const bad = await c.claim({ runner: "x", kinds: [] });
  assert.equal(bad.status, 400);
});

test("H3 heartbeat：续租 +90 s；attempt 不符 409 stale_attempt；lease 不符 409；没这个任务 404", async () => {
  const c = core();
  await seeded(c);
  const job0 = await c.claim({ runner: "vps-a", kinds: ["note"] });
  const j = job0.out;
  assert.equal((await c.heartbeat({ job_id: j.job_id, attempt: 2, lease: j.lease })).out.error, "stale_attempt");
  assert.equal((await c.heartbeat({ job_id: j.job_id, attempt: 1, lease: "0".repeat(32) })).out.error, "lease_mismatch");
  assert.equal((await c.heartbeat({ job_id: "j_nope00000000", attempt: 1, lease: j.lease })).status, 404);
  c.jump(30 * 1000);
  const hb = await c.heartbeat({ job_id: j.job_id, attempt: 1, lease: j.lease });
  assert.equal(hb.status, 200);
  assert.equal(hb.out.lease_until, new Date(T0 + 30000 + LEASE_MS).toISOString());
});

test("H4 租约过期回收：attempts<3 回 queued 可重领；到 3 次 → failed + 通知", async () => {
  const c = core();
  await seeded(c);
  await c.claim({ runner: "vps-a", kinds: ["note"] });           // attempt 1
  c.jump(LEASE_MS + 1);
  const b = await c.claim({ runner: "vps-b", kinds: ["note"] });  // 回收后重领 attempt 2
  assert.equal(b.out.attempt, 2);
  c.jump(LEASE_MS + 1);
  const d = await c.claim({ runner: "vps-c", kinds: ["note"] });  // 回收后重领 attempt 3
  assert.equal(d.out.attempt, 3);
  c.jump(LEASE_MS + 1);
  const e = await c.claim({ runner: "vps-d", kinds: ["note"] });  // 到上限 → failed
  assert.equal(e.status, 204, "failed 后队列空");
  const job = c.storage.m.get(`job:${ENQ.job_id}`);
  assert.equal(job.state, "failed");
  assert.equal(job.reason, "lease_expired");
  assert.equal(job.attempts, 3);
  assert.equal(e.notify.length, 1);
  assert.equal(e.notify[0].status, "failed");
  assert.equal(e.notify[0].attempt, 3);
});

test("H5 complete 两段式：校验 / 提交 / 幂等不写第二份 / 旧 runner 迟到被拒", async () => {
  const c = core();
  await seeded(c);
  const j = (await c.claim({ runner: "vps-a", kinds: ["note"] })).out;
  // 校验段：不动状态
  const v = await c.complete({ job_id: j.job_id, attempt: 1, lease: j.lease, ok: true });
  assert.equal(v.status, 200);
  assert.deepEqual(v.out, { validated: true, hash: H, session_id: SID });
  assert.equal(c.storage.m.get(`job:${j.job_id}`).state, "running", "校验段不改账本");
  // 拒收：attempt / lease 任一不符 → 409，一个字节都不写
  for (const bad of [
    { attempt: 2, lease: j.lease }, { attempt: 1, lease: "0".repeat(32) },
  ]) {
    const r = await c.complete({ job_id: j.job_id, ...bad, ok: true });
    assert.equal(r.status, 409);
  }
  // 提交段：置 done 挂 objects + 通知
  const objects = [`web/${H}/sessions/${SID}/note.md`];
  const done = await c.complete({ job_id: j.job_id, attempt: 1, lease: j.lease, ok: true, commit: true, objects });
  assert.equal(done.status, 200);
  assert.equal(done.out.state, "done");
  assert.deepEqual(done.out.objects, objects);
  assert.equal(done.notify[0].status, "done");
  // 幂等：同 attempt 重复交 → 200 同一份，不产生第二份
  const again = await c.complete({ job_id: j.job_id, attempt: 1, lease: j.lease, ok: true, commit: true, objects });
  assert.deepEqual(again.out, done.out);
  // 老 runner 迟到（不同 attempt）→ 拒收
  const stale = await c.complete({ job_id: j.job_id, attempt: 2, lease: j.lease, ok: true });
  assert.equal(stale.status, 409);
  assert.equal(stale.out.error, "stale_attempt");
  // done 之后 heartbeat 不再续租
  assert.equal((await c.heartbeat({ job_id: j.job_id, attempt: 1, lease: j.lease })).out.error, "lease_mismatch");
});

test("H6 旧 runner 迟到交结果被拒（接管场景）：A 领走→租约过期→B 接管→A 交 409，B 交 200", async () => {
  const c = core();
  await seeded(c);
  const a = (await c.claim({ runner: "vps-a", kinds: ["note"] })).out;
  c.jump(LEASE_MS + 1);
  const b = (await c.claim({ runner: "vps-b", kinds: ["note"] })).out;
  assert.notEqual(b.lease, a.lease, "接管 = 换一把租约");
  const late = await c.complete({ job_id: a.job_id, attempt: a.attempt, lease: a.lease, ok: true });
  assert.equal(late.status, 409);
  assert.equal(late.out.error, "stale_attempt");
  const okB = await c.complete({ job_id: b.job_id, attempt: b.attempt, lease: b.lease, ok: true, commit: true, objects: ["k"] });
  assert.equal(okB.out.state, "done");
});

test("H7 ok:false：前两次回 queued 可重领，第 3 次变 failed（reason 入账 + 通知）", async () => {
  const c = core();
  await seeded(c);
  for (let i = 1; i <= 2; i++) {
    const j = (await c.claim({ runner: "vps-a", kinds: ["note"] })).out;
    const f = await c.complete({ job_id: j.job_id, attempt: i, lease: j.lease, ok: false, reason: "model_timeout" });
    assert.equal(f.status, 200);
    assert.equal(f.out.state, "queued");
    assert.equal(f.out.attempt, i);
  }
  const j3 = (await c.claim({ runner: "vps-a", kinds: ["note"] })).out;
  assert.equal(j3.attempt, 3);
  const f3 = await c.complete({ job_id: j3.job_id, attempt: 3, lease: j3.lease, ok: false, reason: "model_timeout" });
  assert.equal(f3.out.state, "failed");
  assert.equal(f3.notify[0].status, "failed");
  assert.equal(c.storage.m.get(`job:${ENQ.job_id}`).reason, "model_timeout");
  assert.equal((await c.claim({ runner: "x", kinds: ["note"] })).status, 204, "failed 是终态，不再发");
});

test("H8 live 表：register / touch / 只回 3 h 内；sweep 清停课 + 回收过期任务", async () => {
  const c = core();
  await c.register({ hash: H, session_id: SID, course: "A", tz: "Europe/Dublin" });
  await c.register({ hash: "b".repeat(16), session_id: SID2, course: "B", tz: "" });
  c.jump(10 * 60 * 1000);
  await c.touch({ hash: H, session_id: SID, high_water: 42 });
  const live = await c.liveList();
  assert.equal(live.out.sessions.length, 2);
  assert.equal(live.out.sessions.find((s) => s.session_id === SID).high_water, 42);
  assert.ok(live.out.sessions.find((s) => s.session_id === SID).last_line_at > live.out.sessions.find((s) => s.session_id === SID2).last_line_at);
  c.jump(STALE_LIVE_MS + 1);   // B 课 3 h 多没逐句
  await c.touch({ hash: H, session_id: SID, high_water: 43 });   // A 课刚到货，仍在窗口内
  const after = await c.liveList();
  assert.equal(after.out.sessions[0].high_water, 43);
  assert.deepEqual(after.out.sessions.map((s) => s.session_id), [SID], "过时的 live 不再回");
  const sw = await c.sweep();
  assert.equal(sw.out.live_deleted, 1, "sweep 删掉过时的 live");
  assert.equal(c.storage.m.get(`live:${"b".repeat(16)}:${SID2}`), undefined);
  assert.equal((await c.touch({ hash: "b".repeat(16), session_id: SID2, high_water: 1 })).out.ok, false, "live 不在就不无中生有");
});

test("H9 purge：删 live + 未完成 job（落 gonejob 标记），done 的 job 留着；此后 410", async () => {
  const c = core();
  await seeded(c);                                     // 一条 queued
  await c.register({ hash: H, session_id: SID2, course: "B", tz: "" });
  const j2 = { ...ENQ, session_id: SID2, job_id: "j_000000000002" };
  await c.enqueue(j2);
  const doneJ = { ...ENQ, session_id: "s_000000000003", job_id: "j_000000000003" };
  await c.register({ hash: H, session_id: "s_000000000003", course: "C", tz: "" });
  await c.enqueue(doneJ);
  const cl = (await c.claim({ runner: "x", kinds: ["note"] })).out;   // 领到最先入队那条
  await c.complete({ job_id: cl.job_id, attempt: 1, lease: cl.lease, ok: true, commit: true, objects: ["k"] });
  // 入队时各课的 live 已顺手删掉 —— 再放一条 live，验证 purge 连 live 一起删。
  await c.register({ hash: H, session_id: SID2, course: "B", tz: "" });

  const p = await c.purge({ hash: H });
  assert.equal(p.status, 200);
  assert.deepEqual(p.out.deleted, { live: 1, jobs: 2 });
  assert.ok(c.storage.m.get(`job:${cl.job_id}`), "done 的 job 记录留着");
  assert.equal(c.storage.m.get(`job:${doneJ.job_id}`), undefined, "没跑完的删干净");
  for (const id of [j2.job_id, doneJ.job_id]) {   // 删掉的两条未完成：j2（queued）与 doneJ（没跑过）
    assert.equal(c.storage.m.get(`job:${id}`), undefined);
    const hb = await c.heartbeat({ job_id: id, attempt: 1, lease: "0".repeat(32) });
    assert.equal(hb.status, 410);
    assert.equal(hb.out.error, "session_deleted");
    const cp = await c.complete({ job_id: id, attempt: 1, lease: "0".repeat(32), ok: true });
    assert.equal(cp.status, 410, "删了不再写产物");
  }
  assert.equal((await c.liveList()).out.sessions.length, 0);
});

// ---- Claude 11-20 审查补的四条 ----
import { NAME_GRACE_MS, JOB_KEEP_MS } from "./src/runner-hub-core.js";

test("C1 没课名的课下课后等 NAME_GRACE_MS 才领；期间改名立刻可领且带新名", async () => {
  const c = core();
  await seeded(c, { course: "" });
  assert.equal((await c.claim({ runner: "r", kinds: ["note"] })).status, 204, "刚下课、没名字：先等用户起名");
  await c.rename({ hash: H, session_id: SID, course: "宏观 第五讲" });
  const r = await c.claim({ runner: "r", kinds: ["note"] });
  assert.equal(r.status, 200);
  assert.equal(r.out.input.course, "宏观 第五讲");

  const c2 = core();
  await seeded(c2, { course: "未命名" });   // 网页真实发的是占位名，不是空串
  assert.equal((await c2.claim({ runner: "r", kinds: ["note"] })).status, 204);
  c2.jump(NAME_GRACE_MS + 1);
  assert.equal((await c2.claim({ runner: "r", kinds: ["note"] })).status, 200, "等够了就不再等，没名字也做");
});

test("C2 claim 先下课先做（键是随机 job_id，不能按键序）", async () => {
  const c = core();
  await c.register({ hash: H, session_id: SID2, course: "B" });
  await c.enqueue({ ...ENQ, session_id: SID2, job_id: "j_zzzzzzzzzzzz" });
  c.jump(1000);
  await c.register({ hash: H, session_id: SID, course: "A" });
  await c.enqueue({ ...ENQ, job_id: "j_000000000000" });
  const r = await c.claim({ runner: "r", kinds: ["note"] });
  assert.equal(r.out.job_id, "j_zzzzzzzzzzzz");
});

test("C3 rename 不动已完成的 job", async () => {
  const c = core();
  await seeded(c);
  const job = c.storage.m.get(`job:${ENQ.job_id}`);
  c.storage.m.set(`job:${ENQ.job_id}`, { ...job, state: "done" });
  await c.rename({ hash: H, session_id: SID, course: "新名" });
  assert.equal(c.storage.m.get(`job:${ENQ.job_id}`).course, "ECON10740");
});

test("C4 sweep 清掉 7 天前完成的 job，未完成的留着", async () => {
  const c = core();
  await seeded(c);
  const job = c.storage.m.get(`job:${ENQ.job_id}`);
  c.storage.m.set(`job:${ENQ.job_id}`, { ...job, state: "done", updated: new Date(T0).toISOString() });
  c.storage.m.set("job:j_q", { ...job, job_id: "j_q", state: "queued", updated: new Date(T0).toISOString() });
  c.jump(JOB_KEEP_MS + 1);
  await c.sweep();
  assert.equal(c.storage.m.get(`job:${ENQ.job_id}`), undefined);
  assert.ok(c.storage.m.get("job:j_q"));
});

test("C5 adminState：计数 + 最近任务不含租约串 + live 标 stale", async () => {
  const c = core();
  await seeded(c);
  await c.claim({ runner: "r", kinds: ["note"] });
  await c.register({ hash: H, session_id: SID2, course: "X" });
  c.jump(STALE_LIVE_MS + 1);
  const r = await c.adminState({});
  assert.equal(r.status, 200);
  assert.equal(r.out.counts.running, 1);
  assert.equal(r.out.jobs[0].lease, undefined, "租约串不进看板");
  assert.equal(r.out.live[0].stale, true);
});
