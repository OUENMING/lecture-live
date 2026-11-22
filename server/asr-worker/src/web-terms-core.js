// 术语上云（W19/W24）的**纯规则**：拆行去重 / 校验 / 课号判定 / 三层共享池汇总排序。
// 对外：checkPut() · parseTermsText() · normalizeCourse（转出 web-session-core）· courseKeyOf() ·
//       subjectOf() · fieldOf() · termKey() · sharedTerms() · mergeTiers()
// 契约：docs/API-web-v0-20261117.md §3.9（W19 同课 / W24 加了 subject + field 两层）。不 import cloudflare:*，
// Node 直接测得动；存储与网络在 web-terms.js（DO 壳 + handler 侧转发），改规则只改这一处。

import { normalizeCourse } from "./web-session-core.js";

export { normalizeCourse };

export const TERMS_MAX_CHARS = 20000;   // 一门课的正文上限（字符）
export const TERMS_MAX_LINES = 500;     // 去重后的条数上限（契约 §个人术语）
export const TERM_MAX_LEN = 80;         // 单条上限（与课名同一个数量级：一个是词、一个是课名）
export const SHARED_MAX = 200;          // course 层一次最多回多少条（W19 原行为，不变）
export const SHARED_SUBJECT_MAX = 100;  // subject 层上限（同前缀的**其它**课）
export const SHARED_FIELD_MAX = 60;     // field 层上限（同大类的**其它**前缀）—— 最远的一层，最严
export const SHARED_TOTAL_MAX = 300;    // 三层相加的总闸（客户端 context.terms 也只吃 300 条）
// 三层门槛：越远的池越杂，进门的码数就要越多（Soniox context 会把识别往这些词上拉，杂词=误听）。
export const SHARED_MIN_USERS = 2;       // course / subject：≥2 枚码写过
//: 只有「课号」进共享池：`ECON10770` / `CS1010` / `ECON10770A`。
export const COURSE_KEY_RE = /^[A-Z]{2,5}\d{4,5}[A-Z]?$/;

/**
 * 学科前缀 → 大类。**来源 = UCD 课号前缀**（BSc Economics 等专业的课号首字母段），不是随便编的缩写。
 * 只收这几个前缀：未知前缀 → null → 既不进 subject 池，也不进 field 池（宁可少共享，不可乱拉）。
 */
export const FIELD_PREFIXES = {
  stem: ["MATH", "STAT", "PHYS", "CHEM", "COMP", "EEEN", "MEEN", "CVEN", "CHEN", "BMOL", "BIOL", "BIOC",
    "GEOL", "ENVB", "ACM", "MST", "EGEN"],
  business: ["ECON", "ACC", "FIN", "MKT", "MGT", "BMGT", "BUS", "BANL", "MIS", "IB", "ENT", "HRM"],
  health: ["MDSA", "PHPS", "NMHS", "PHAR", "RDGY", "PHYC", "VET"],
  social: ["SOC", "PSY", "POL", "HIS", "PHIL", "LAW", "ARCH", "GEOG"],
};

// 前缀 → 大类（反查表，从上面的常量生成，只有一处真源）
const PREFIX_FIELD = new Map();
for (const [field, prefixes] of Object.entries(FIELD_PREFIXES)) {
  for (const p of prefixes) PREFIX_FIELD.set(p, field);
}

/**
 * 一次 GET 最多 list 多少个课号池（course 1 + subject 1 + field 的其它前缀）。现在最大的大类是 business（12 个），
 * 整张表全部前缀 44 个 —— 80 是给未来加前缀留出的余量，到不了。
 * ⚠️ 超了按字母序截断并打一行日志（course 层不受影响）：这是成本闸，不是正确性；
 *    真要撞上，说明大类表该重新设计了，不是把数字调大。
 */
export const SHARED_LIST_MAX = 80;

// 控制字符（C0 + DEL + C1）—— 术语来自用户随手输入 / 课件抽取，别让它们进账本与 JSON。
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/g;

/**
 * 正文 → 归一后的行。按行拆、去控制字符、trim、去空行、**精确去重**（保留首次写法与顺序）。
 * 单条超 80 / 条数超 500 / 整段超 20000 字符 → `{error:"bad_body", detail}`（调用方回 400）。
 * ⚠️ 个人术语按**精确字面**去重（`Linear` 与 `linear` 是两条，用户自己管大小写）；
 *    共享池才按「小写 + 折叠空白」合并 —— 那一层要跨码对齐，见 sharedTerms()。
 */
export function parseTermsText(text) {
  if (typeof text !== "string") return { error: "bad_body", detail: "text" };
  if (text.length > TERMS_MAX_CHARS) return { error: "bad_body", detail: "text" };
  const seen = new Set();
  const lines = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.replace(CONTROL, "").trim();
    if (!line) continue;
    if (line.length > TERM_MAX_LEN) return { error: "bad_body", detail: "term" };
    if (seen.has(line)) continue;
    seen.add(line);
    lines.push(line);
  }
  if (lines.length > TERMS_MAX_LINES) return { error: "bad_body", detail: "lines" };
  return { lines, text: lines.join("\n") };
}

/**
 * PUT 的整体校验：body 形状 + 课名（**与开课同一套** normalizeCourse，不另起规则）+ 正文。
 * 返回 `{course, share, text, lines}` 或 `{error:"bad_body", detail}`；不做 I/O。
 * `share` 缺省当 false（保守：没说要不要共享就不进池）。
 */
export function checkPut(body, rawCourse) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return { error: "bad_body", detail: "body" };
  const course = normalizeCourse(rawCourse);
  if (course === null) return { error: "bad_body", detail: "course" };
  if (typeof body.text !== "string") return { error: "bad_body", detail: "text" };
  if (body.share !== undefined && typeof body.share !== "boolean") return { error: "bad_body", detail: "share" };
  const parsed = parseTermsText(body.text);
  if (parsed.error) return parsed;
  return { course, share: body.share === true, text: parsed.text, lines: parsed.lines };
}

/** 课名 → 池键（去**全部**空白后转大写）；不是课号 → null（自由文字课名不进池）。 */
export function courseKeyOf(rawCourse) {
  const s = String(rawCourse ?? "").replace(/\s+/g, "").toUpperCase();
  return COURSE_KEY_RE.test(s) ? s : null;
}

/** 课号 → 学科前缀（`ECON10770` → `ECON`）；不是课号 → null。 */
export function subjectOf(courseKey) {
  const k = courseKeyOf(courseKey);
  return k ? k.replace(/\d.*$/, "") : null;
}

/** 学科前缀 → 大类（`ECON` → `business`）；不在 FIELD_PREFIXES 里 → null（不进大类池）。 */
export function fieldOf(subject) {
  return PREFIX_FIELD.get(String(subject ?? "").toUpperCase()) ?? null;
}

/** 共享池的比较键：小写 + 折叠空白（`Game  Theory` 与 `game theory` 算同一个词）。 */
export function termKey(term) {
  return String(term ?? "").trim().toLowerCase().replace(/\s+/g, " ");
}

const byUsersThenKey = (a, b) =>
  (b.users.size - a.users.size) || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);

/**
 * 共享池视图：`[{hash, text}]`（每个码一条贡献）→ `[{term, users}]`。
 * 只留 users ≥ minUsers，按 users 降序、同数按字母序，最多 max 条；不返回谁贡献的，只返回人数。
 * term 按 termKey 合并，返回**第一次出现**的写法：按调用方给的顺序扫（DO 里是 codeHash 升序，确定性）。
 */
export function sharedTerms(contributions, { minUsers = SHARED_MIN_USERS, max = SHARED_MAX } = {}) {
  const byKey = new Map();
  for (const c of contributions || []) {
    const hash = String(c?.hash ?? "");
    if (!hash) continue;
    const parsed = parseTermsText(typeof c.text === "string" ? c.text : "");
    const seen = new Set();   // 同一个码重复写的词只算一次
    for (const term of parsed.lines || []) {
      const k = termKey(term);
      if (seen.has(k)) continue;
      seen.add(k);
      const hit = byKey.get(k);
      if (hit) hit.users.add(hash);
      else byKey.set(k, { term, key: k, users: new Set([hash]) });
    }
  }
  return [...byKey.values()]
    .filter((x) => x.users.size >= minUsers)
    .sort(byUsersThenKey)
    .slice(0, max)
    .map((x) => ({ term: x.term, users: x.users.size }));
}

/**
 * 三层合并：把各层的贡献**先按层裁到各自上限**再服务器侧排序，最后按 tier（course → subject → field）
 * 再按 users 降序、同数按字母序，跨层去重（termKey 同词算一个，**保留更近的层**），总数 ≤ SHARED_TOTAL_MAX。
 * （单层上限之和正好是总闸，所以截断只可能发生在「层内」；跨层去重还会再让出名额。）
 *
 * 入参 `tiers: [{tier, contributions, minUsers, max}]`，`contributions` 是该层**所有课号池**的贡献合并。
 * 为什么先裁再跨层去重：否则视每层返回顺序，同词在两层都可能挤进 200 名里。
 * 返回值不带「谁」，只有 `{term, users, tier}`。
 */
export function mergeTiers(tiers) {
  const out = [];
  const seen = new Set();   // 跨层去重：更近的层先扫，远的层撞上就丢
  let total = 0;
  for (const t of tiers || []) {
    if (!t || !Array.isArray(t.contributions)) continue;
    const rows = sharedTerms(t.contributions, { minUsers: t.minUsers, max: t.max });
    for (const r of rows) {
      if (total >= SHARED_TOTAL_MAX) return out;
      const k = termKey(r.term);
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({ term: r.term, users: r.users, tier: t.tier });
      total++;
    }
  }
  return out;
}

/**
 * 管理看板（只读）：共享池全部贡献 `[{key: "share:<课号>:<hash>", text}]` → 规模统计。**纯函数。**
 * 不返回 hash（谁写的），只有人数；每门课附前 `top` 个词（按人数）给作者抽看。
 * ⚠️ 只看得见**进了池**的：自由文字课名 / 没开共享的个人术语在各码自己的 DO 里，枚举不到。
 */
export function poolStats(entries, { top = 8 } = {}) {
  const byCourse = new Map();
  const codes = new Set();
  for (const e of entries || []) {
    const m = /^share:([^:]+):(.+)$/.exec(String(e?.key ?? ""));
    if (!m) continue;
    const [, course, hash] = m;
    codes.add(hash);
    if (!byCourse.has(course)) byCourse.set(course, []);
    byCourse.get(course).push({ hash, text: typeof e.text === "string" ? e.text : "" });
  }
  const all = [];
  const courses = [];
  const fields = {};
  for (const [course, contribs] of [...byCourse].sort((a, b) => (a[0] < b[0] ? -1 : 1))) {
    all.push(...contribs);
    const terms = sharedTerms(contribs, { minUsers: 1, max: Infinity });
    const subject = subjectOf(course);
    const field = fieldOf(subject);
    courses.push({
      course, subject, field: field ?? "—",
      users: contribs.length,
      terms: terms.length,
      shared: terms.filter((t) => t.users >= SHARED_MIN_USERS).length,
      top: terms.slice(0, top),
    });
    const f = (fields[field ?? "未分类"] ||= { courses: 0, users: new Set(), terms: 0 });
    f.courses++; contribs.forEach((c) => f.users.add(c.hash)); f.terms += terms.length;
  }
  const distinct = sharedTerms(all, { minUsers: 1, max: Infinity });
  return {
    contributions: all.length,
    codes: codes.size,
    courses: courses.length,
    distinct_terms: distinct.length,
    cross_code_terms: distinct.filter((t) => t.users >= SHARED_MIN_USERS).length,
    fields: Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, { courses: v.courses, users: v.users.size, terms: v.terms }])),
    by_course: courses,
  };
}
