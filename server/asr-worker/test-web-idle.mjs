// 关页没下课的课（3 h 没新句子）由 cron 的 /sweep 收尾：每码账本那一节标成已结束，有句子的补一条笔记任务。
// 真 RunnerHub + 真 WebSessions + 内存 storage / R2，上游一条都不碰。
//   cd server/asr-worker && node --test test-web-idle.mjs
// 钉住：① 有句子 -> 账本 ended + 笔记任务（全局队列里有，course 取自 live 老底）
//      ② 一句都没有 -> 账本 ended + abandoned，不建任务 ③ 已下课的不动、不重复建任务
//      ④ 3 h 内的 live 不碰 ⑤ 单节失败不拖累别的节
import { test } from "node:test";
import assert from "node:assert/strict";
import { RunnerHub } from "./src/runner-hub.js";
import { WebSessions } from "./src/web-session.js";

const HASH = "a".repeat(16);
const mem = () => {
  const m = new Map();
  return {
    m,
    get: async (k) => (m.has(k) ? structuredClone(m.get(k)) : undefined),
    put: async (k, v) => { m.set(k, structuredClone(v)); },
    delete: async (k) => { for (const kk of [].concat(k)) m.delete(kk); },
    list: async ({ prefix = "" } = {}) => new Map([...m].filter(([k]) => k.startsWith(prefix))),
  };
};
const r2 = () => ({ put: async () => {}, delete: async () => {}, list: async () => ({ objects: [], truncated: false }) });

function rig() {
  const env = { WEB_DATA: r2() };
  const web = new WebSessions({ storage: mem() }, env);
  env.WEB_SESSIONS = { idFromName: (n) => n, get: () => ({ fetch: (req) => web.fetch(req) }) };
  const hub = new RunnerHub({ storage: mem() }, env);
  const doCall = async (path, body) => {
    const r = await web.fetch(new Request(`https://x${path}`, { method: "POST", body: JSON.stringify(body) }));
    return { status: r.status, body: await r.json() };
  };
  const open = async (course) => (await doCall("/open", { hash: HASH, course, started_at: new Date().toISOString() })).body.session_id;
  const lines = (sid, n) => doCall("/lines", {
    session_id: sid, lines: Array.from({ length: n }, (_, i) => ({ seq: i + 1, state: "final", en: "e", zh: "z", t: i })),
  });
  const state = async (sid) => (await web.fetch(new Request(`https://x/state?session_id=${sid}`))).json();
  const live = (sid, course, ageMs, high = 0) => hub.storage.put(`live:${HASH}:${sid}`, {
    hash: HASH, session_id: sid, course, tz: "Europe/Dublin", started_at: "2026-11-20T09:00:00.000Z",
    last_line_at: new Date(Date.now() - ageMs).toISOString(), high_water: high,
  });
  const sweep = async () => (await hub.fetch(new Request("https://h/sweep", { method: "POST", body: "{}" }))).json();
  const jobs = () => [...hub.storage.m].filter(([k]) => k.startsWith("job:")).map(([, v]) => v);
  return { web, hub, open, lines, state, live, sweep, jobs, doCall };
}
const H4 = 4 * 3600 * 1000;

test("I1 有句子的残课：账本 ended + 入全局队列，course 取 live 老底", async () => {
  const g = rig();
  const sid = await g.open("ECON10740");
  await g.lines(sid, 3);
  await g.live(sid, "ECON10740", H4, 3);
  const out = await g.sweep();
  assert.equal(out.live_deleted, 1);
  assert.equal(out.idle_ended, 1);
  assert.equal(out.idle, undefined, "idle 清单不外露");
  const st = await g.state(sid);
  assert.equal(st.state, "ended");
  assert.equal(st.note.state, "queued");
  const js = g.jobs();
  assert.equal(js.length, 1);
  assert.equal(js[0].course, "ECON10740");
  assert.equal(js[0].session_id, sid);
});

test("I2 一句都没有的空壳：只标 ended，不建任务", async () => {
  const g = rig();
  const sid = await g.open("刷新残留");
  await g.live(sid, "刷新残留", H4, 0);
  await g.sweep();
  const st = await g.state(sid);
  assert.equal(st.state, "ended");
  assert.equal(st.note, null);
  assert.equal(g.jobs().length, 0);
});

test("I3 已正常下课的不动、不重复建任务；再扫一轮也不重复", async () => {
  const g = rig();
  const sid = await g.open("A");
  await g.lines(sid, 2);
  const e = await g.doCall("/end", { session_id: sid, ended_at: "2026-11-20T10:00:00.000Z", lines_total: 2 });
  assert.equal(e.status, 202);
  await g.live(sid, "A", H4, 2);   // 极端：live 没删干净
  await g.sweep();
  await g.sweep();
  assert.equal(g.jobs().length, 0, "账本不是 live，不再建第二条（全局队列的任务由正常下课那条路入队）");
  assert.equal((await g.state(sid)).note.job_id, e.body.job_id);
});

test("I4 3 h 内还有句子的课不碰", async () => {
  const g = rig();
  const sid = await g.open("A");
  await g.lines(sid, 1);
  await g.live(sid, "A", 20 * 60 * 1000, 1);
  const out = await g.sweep();
  assert.equal(out.idle_ended, 0);
  assert.equal((await g.state(sid)).state, "live");
});

test("I5 一节收尾失败不拖累别的节", async () => {
  const g = rig();
  const bad = await g.open("坏");
  const ok = await g.open("好");
  await g.lines(bad, 1); await g.lines(ok, 1);
  await g.live(bad, "坏", H4, 1); await g.live(ok, "好", H4, 1);
  const real = g.hub.env.WEB_SESSIONS.get;
  let n = 0;
  g.hub.env.WEB_SESSIONS.get = (id) => (++n === 1 ? { fetch: async () => { throw new Error("boom"); } } : real(id));
  const out = await g.sweep();
  assert.equal(out.idle_ended, 1);
  assert.equal(g.jobs().length, 1);
});
