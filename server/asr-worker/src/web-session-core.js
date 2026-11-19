// 网页版会话账本的**纯逻辑**：不 import cloudflare:*、不碰 R2 / storage，Node 直接测得动。
// 对外：codeHashHex() / normalizeCourse() / checkLines() / newSession() / newId() / SessionLedger / endDecision()
//      / renameCourse()（课后改课名，W18）。
// 判据全在这里（幂等、confirmed_seq、seq_too_far、批大小、状态机），DO 壳（web-session.js）只负责
// 「读账本 → 调这里 → 写回 + 写 R2」，改规则只改这一处。契约：docs/API-web-v0-20261117.md §3.1–3.5、§5。
// ⚠️ 两处偏离契约，**待 Owen 确认**：① `course` 放宽成 trim 后 1–80 字（网页让用户随手写课名）；
//    ② R2 用独立绑定 WEB_DATA（见 wrangler.toml）。

export const MAX_LINES_PER_BATCH = 200;        // 契约 §2 上限
export const MAX_BODY_BYTES = 256 * 1024;      // 对齐 handler.js:LLM_MAX_BYTES
export const MAX_SEQ_AHEAD = 500;              // seq > high_water + 500 -> 409 seq_too_far
export const MAX_SESSION_S = 10800;            // 单节课 3 h（与 Soniox 单流上限一致）
export const COURSE_MAX = 80;
export const SESSION_ID_RE = /^s_[0-9a-f]{12}$/;
export const DEFAULT_LIST_LIMIT = 20;
export const MAX_LIST_LIMIT = 50;
// 控制字符（C0 + DEL + C1）—— 课名来自用户随手输入，别让它们进账本 / JSON key。
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/g;

// 通行码 -> 路径 / DO 名字里唯一和「人」相关的部分（同 upload-worker 的 hash16 形状）。
// 明文码**永远不进** DO 名字、R2 键、日志。
export async function codeHashHex(code, n = 16) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(String(code)));
  return [...new Uint8Array(buf)].slice(0, n / 2).map((x) => x.toString(16).padStart(2, "0")).join("");
}

export function newId(prefix) {
  const b = new Uint8Array(6);
  crypto.getRandomValues(b);
  return `${prefix}${[...b].map((x) => x.toString(16).padStart(2, "0")).join("")}`;
}

// ⚠️ **偏离契约**（待 Owen 确认）：契约写 `^[A-Z0-9]{2,16}$`，网页版让用户随手写课名
// （"微观经济" / "ECON 10740" / "CS-101" 都该收下）。这里只保证：去控制字符、trim 后 1–80 字。
export function normalizeCourse(raw) {
  const s = String(raw ?? "").replace(CONTROL, "").trim();
  return s.length >= 1 && s.length <= COURSE_MAX ? s : null;
}

// 一批逐句的形状 / 大小。返回稳定的错误码（null = 收）。体积判在 handler（要真字节数）。
export function checkLines(lines) {
  if (!Array.isArray(lines) || lines.length === 0) return "bad_body";
  if (lines.length > MAX_LINES_PER_BATCH) return "too_large";
  for (const l of lines) {
    if (!l || typeof l !== "object" || Array.isArray(l)) return "bad_body";
    if (!Number.isInteger(l.seq) || l.seq < 1) return "bad_body";
    if (l.state !== "draft" && l.state !== "final") return "bad_body";
    if (typeof l.en !== "string" || typeof l.zh !== "string") return "bad_body";
    if (l.t !== undefined && !Number.isFinite(l.t)) return "bad_body";
  }
  return null;
}

export function newSession({ session_id, course, now, tz, install }) {
  return {
    session_id, course, created_at: now, ended_at: null, tz: tz || "",
    install: install || "", state: "live", confirmed_seq: 0, high_water: 0,
    lines_total: 0, job_id: null,
  };
}

// 一节课的账本在内存里的那一份。`known` = seq -> "draft" | "final"（DO 从 storage 读出来传进来）。
// 读-改-写之间没有 await：DO 的 input gate 保证同一实例串行（同 passes-core / quota-core）。
export class SessionLedger {
  constructor(meta, known = new Map()) {
    this.meta = meta;
    this.known = known;
    this.write = [];      // 要落 R2 的 final 行（draft 不落，契约 §6.1）
    // 存储里要改的 seq：新出现的 + draft -> final 的。⚠️ 不能由调用方自己对比 —— `known` 就是
    // `this.known`（同一个对象，append 会就地改），对比恒为相等，seq 水位永远落不下去。
    this.changed = [];
  }

  // 先到先得：同 seq 再来、内容不管相不相同 -> ignored；唯一例外是 draft -> final 覆盖**一次**。
  // confirmed_seq 只从 1 连续往前推（乱序到达不会跳号）；high_water = 见过的最大 seq。
  append(lines) {
    const m = this.meta;
    if (m.state === "deleted") return { error: "session_deleted" };
    if (m.state === "ended") return { error: "session_ended" };
    const bad = checkLines(lines);
    if (bad) return { error: bad };
    const limit = m.high_water + MAX_SEQ_AHEAD;
    if (lines.some((l) => l.seq > limit)) return { error: "seq_too_far" };
    let ignored = 0, high = m.high_water;
    for (const l of lines) {
      const prev = this.known.get(l.seq);
      if (prev === undefined) {
        this.known.set(l.seq, l.state);
        this.changed.push([l.seq, l.state]);
        if (l.state === "final") this.write.push(l);
      } else if (prev === "draft" && l.state === "final") {
        this.known.set(l.seq, "final");
        this.changed.push([l.seq, "final"]);
        this.write.push(l);            // 只覆盖一次；之后再来 final 一律 ignored
      } else {
        ignored += 1;
      }
      if (l.seq > high) high = l.seq;
    }
    m.high_water = high;
    while (this.known.has(m.confirmed_seq + 1)) m.confirmed_seq += 1;
    m.lines_total = this.known.size;
    return {
      confirmed_seq: m.confirmed_seq, high_water: m.high_water,
      received: lines.length, ignored,
    };
  }
}

// 改名（课后页改课名）：course 与开课**同一套**校验（normalizeCourse），不另起规则。
// 不看 state —— 进行中 / 已下课的都能改（开课没填课名时账本里是「未命名」，下课后才改）。
// deleted 与「没这个 meta」由调用方按 GET 的判据先挡（DO 里都是 `#gone()`）；这里只做最后一层防御。
// 返回新的 meta（不改入参）或 { error, detail }，不做 I/O。
// 为什么返回整份 meta：调用方要原样写回 storage，rename 之外的字段（seq 水位 / job_id）一个都不能丢。
export function renameCourse(meta, rawCourse) {
  if (!meta) return { error: "not_found" };
  if (meta.state === "deleted") return { error: "session_deleted" };
  const course = normalizeCourse(rawCourse);
  if (course === null) return { error: "bad_body", detail: "course" };
  return { meta: { ...meta, course } };
}

// 下课：一节课只对应一条笔记任务。返回要做什么，不做 I/O。
// job 存在 -> dedupe（参数与首次不一致只报告，不改账本）；不存在 -> 让 DO 新建 queued 任务。
export function endDecision(meta, job, { lines_total, level } = {}) {
  if (meta.state === "deleted") return { error: "session_deleted" };
  if (level !== undefined && level !== null && level !== "std") return { error: "bad_body" };
  if (job) {
    return { dedupe: true, conflict: Number.isFinite(lines_total) && lines_total !== job.lines_total };
  }
  return { dedupe: false };
}
