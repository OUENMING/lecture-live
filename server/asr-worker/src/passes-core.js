// 一人一码（通行码）状态机 —— 纯逻辑，不 import 任何 cloudflare:*，Node 测试能直接跑。
// 由 passes.js 里的 Durable Object 壳包一层；storage 只要有 async get / put / list({prefix})。
// 设计：docs/PLAN-passkey.md §2–§6（Owen 2026-10-11 拍板：默认不限额、设备 2 台、有效期 120 天）。
//
// 键（DO 的 KV 接口；SQLite 后端的 DO 同样支持，免费档可用）：
//   code:<码>              {name, created, expires, revoked, max_devices, daily_asr_s, daily_tokens, daily_rt_s}
//   dev:<码>               {<install_id>: {first, last}}
//   use:<YYYY-MM-DD>:<码>  {asr_s, asr_reqs, llm_reqs, tokens_in, tokens_out, cache_hit, jev_reqs, jev_q,
//                           models:{v3|turbo|qwen|other: 段数}, vers:{<客户端版本>: 次数},
//                           rt_s, rt_reqs}
//                          ⚠️ rt_s / rt_reqs（实时转写：Soniox 音频秒 / 会话数）是 2026-10-17 加的，
//                             **老记录没有这两键**，读的一律过 num() 兜（见 check / #addRt）；老码也没有
//                             daily_rt_s，当「不限」（见 check 里那条 ?? null）。
//                          ⚠️ models / vers 是 2026-10-14 加的。**老记录没有这两键**，读的时候按空对象兜。
//                          mins:{<UTC 当日第几分钟 0–1439>: [转写段, 转写秒, 翻译次, token 进+出, Jev 次]}
//                          ⚠️ mins 是 2026-10-14 晚加的（作者：「面板能不能精确到分钟」）。键是当天分钟号，
//                             **天然封顶 1440 个**（最坏约 45 KB，DO 单值上限 128 KiB）；`stats` 不回它（太大），
//                             要看走 `/admin/minutes`。老记录没有这键。
// ⚠️ `/admin/delete` 把上面三种键一起删掉；它只认完整码 + `yes:true`（见 #remove）。
// ⚠️ 只记数字 + **客户端自报的版本号**（`vers`，形状受限、个数封顶，见 VER_RE / VER_MAX_KEYS）：
//    不记文本、音频、IP。日志里也只出现码的「名字段」。
//
// 2026-10-17 加「实时转写」（kind = "rt"：客户端拿 Worker 发的 Soniox 临时 key 自己连 WebSocket，
// 音频不经 Worker）。Worker 看不到音频流，所以 rt_s **不是**请求时记的，而是 Cron 每小时从 Soniox 的
// 用量日志回收（按 client_reference_id = 码名）补进 use:<日>:<码>，见 handler.js 的 scheduled 与下面的
// /admin/rt/* 两条路由。两条路由只给 Worker 自己（和 ADMIN_TOKEN 面板）用，不给客户端。
//
// ⚠️「哪些区合法」的唯一定义点在 `soniox-core.REGIONS`（这里 import REGION_NAMES，**别再抄一份 us/jp**）。

import { REGION_NAMES } from "./soniox-core.js";

const DAY_MS = 86400000;
export const DEFAULT_DAYS = 120;
export const DEFAULT_DEVICES = 2;
export const CODE_RE = /^cl-[a-z0-9]{1,16}-[0-9a-f]{16}$/;
const NAME_RE = /^[a-z0-9]{1,16}$/;
const INSTALL_RE = /^[0-9a-f]{32}$/;

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });

const day = (now) => new Date(now).toISOString().slice(0, 10);
const num = (v) => (Number.isFinite(v) && v >= 0 ? v : 0);
const limitOrNull = (v) => (v === null || v === undefined ? null : Number.isFinite(v) && v >= 0 ? v : undefined);

// 模型名 -> 统计用的桶。名字由 Worker 自己挑（不是客户端给的），兜底 other 让新模型上线不炸。
const modelBucket = (m) => {
  const s = String(m);
  if (s.includes("turbo")) return "turbo";
  if (s.startsWith("whisper")) return "v3";
  if (s.startsWith("qwen")) return "qwen";
  return "other";
};
// 版本头是**客户端可控**的 —— 形状不合就不记（存储不变量归这个模块管）。
const VER_RE = /^[0-9][0-9A-Za-z.-]{0,15}$/;
// ⚠️ `Date.parse` 对 "2026-1"（非补零）也给 NaN，而 `new Date(NaN).toISOString()` **抛 RangeError**
//    -> DO 里逃出去就是 500。凡是从外面传进来的日期，一律先过 isDay()。
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const isDay = (v) => typeof v === "string" && DAY_RE.test(v) && !Number.isNaN(Date.parse(`${v}T00:00:00Z`));
const addDays = (d, n) =>
  new Date(Date.parse(`${d}T00:00:00Z`) + n * DAY_MS).toISOString().slice(0, 10);

// vers 的键**由客户端决定**（handler.js 只砍到 16 字符）。只限长度不限个数 = 一个持码的人
// 每请求加一个新键，把 use:<日>:<码> 这一格撑大；那个 put 一旦失败，reportUsage 的
// catch 会把它吞掉 -> 当天的 asr_s / tokens 全部静默停更。所以这里限最多 VER_MAX_KEYS 个键。
const VER_MAX_KEYS = 8;

// ---- 实时转写（rt）的用量回收：窗口与去重 ----
// Worker 看不到音频流，用量只能从 Soniox 的日志事后回收（日志是**请求完成**才有，见官方 usage-logs 指南）。
const RT_OVERLAP_MS = 3 * 3600 * 1000;   // 每轮窗口回退 3 h 重拉：日志有延迟 + 上一轮可能漏，重叠段靠 uuid 去重
const RT_FIRST_MS = 24 * 3600 * 1000;    // 从没拉过（刚部署 / 换了 DO 名）只看最近 24 h，别去够官方 91 天的上限
const RT_KEEP_MS = 6 * 3600 * 1000;      // seen 保留窗口 = 2 × OVERLAP：比这更老的记录再也不会被重拉
const RT_MAX_SEEN = 1000;                // seen 条数上限（1000 条约 55 KB，DO 单值上限 128 KiB）

// 一天一格的用量记录的初始形状。⚠️ 只用来当**底板**，上面的 get 结果会整个盖上去 —— 老记录没有
// 后加的键（rt_s / rt_reqs / models / vers）时，读的一律 num() 兜，别写 `u.x += ...`（undefined 上加法变 NaN，
// JSON 里 NaN 会写成 null，那一格用量就废了）。
const USE0 = () => ({
  asr_s: 0, asr_reqs: 0, llm_reqs: 0, tokens_in: 0, tokens_out: 0, cache_hit: 0,
  jev_reqs: 0, jev_q: 0, rt_s: 0, rt_reqs: 0, models: {}, vers: {},
});

function randomHex(bytes) {
  const b = new Uint8Array(bytes);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

export class PassCore {
  constructor(storage, now = () => Date.now()) {
    this.storage = storage;
    this.now = now;
  }

  async handle(request) {
    const path = new URL(request.url).pathname;
    let body;
    try { body = await request.json(); } catch { return json({ error: "bad body" }, 400); }
    // 读-改-写之间没有别的 await 以外的让出：DO 的 input gate 保证同一实例串行（同 quota-core）。
    switch (path) {
      case "/check": return json(await this.check(body));
      case "/usage": await this.usage(body); return json({ ok: true });
      case "/admin/add": return json(await this.add(body));
      case "/admin/list": return json(await this.list());
      case "/admin/revoke": return json(await this.#edit(body, (c) => { c.revoked = true; }));
      case "/admin/unrevoke": return json(await this.#edit(body, (c) => { c.revoked = false; }));
      case "/admin/renew": return json(await this.#edit(body, (c, now) => {
        c.expires = Math.max(c.expires, now) + num(body.days || DEFAULT_DAYS) * DAY_MS;
      }));
      case "/admin/limit": return json(await this.#limit(body));
      case "/admin/delete": return json(await this.#remove(body));
      case "/admin/stats": return json(await this.stats(body));
      case "/admin/minutes": return json(await this.minutes(body));
      case "/admin/devices": return json(await this.devices(body));
      // 实时转写的用量回收：游标读 / 一批日志落账（handler.js 的 scheduled 调，面板也能只读地看进度）
      case "/admin/rt/cursor": return json(await this.#rtCursor(body));
      case "/admin/rt/import": return json(await this.#rtImport(body));
      default: return json({ error: "not found" }, 404);
    }
  }

  // 能不能用：码在、没吊销、没过期、设备没超、当日额度没超（null = 不限）。新设备在名额内就登记。
  // 返回 {ok:true, name} 或 {ok:false, reason: unknown|revoked|expired|devices|quota}。
  async check({ code, install, kind }) {
    if (!CODE_RE.test(code || "")) return { ok: false, reason: "unknown" };
    const c = await this.storage.get(`code:${code}`);
    if (!c) return { ok: false, reason: "unknown" };
    const now = this.now();
    if (c.revoked) return { ok: false, reason: "revoked", name: c.name };
    if (now >= c.expires) return { ok: false, reason: "expired", name: c.name };
    // ⚠️⚠️ **额度先判、设备后记**（2026-10-12 全仓审查，核实过）：顺序反过来的话，
    //    被额度拒掉的请求**照样**会登记一台新设备、并刷新它的 `last` ——
    //      · 管理端的 `last_seen` 被拒请求污染（看起来"刚用过"，其实一次都没成）；
    //      · 设备名额被一次注定失败的请求消耗掉（用户在额度恢复后白白少一台额度）。
    //    判完再记才自洽：只有**真的放行**的请求才配登记设备。
    const u = (await this.storage.get(`use:${day(now)}:${code}`)) || {};
    if (kind === "asr" && c.daily_asr_s !== null && num(u.asr_s) >= c.daily_asr_s) {
      return { ok: false, reason: "quota", name: c.name };
    }
    if (kind === "llm" && c.daily_tokens !== null
        && num(u.tokens_in) + num(u.tokens_out) >= c.daily_tokens) {
      return { ok: false, reason: "quota", name: c.name };
    }
    // 实时转写：额度按**已回收的**秒数判。⚠️ 是软闸 —— 用量由 Cron 每小时补记（还叠加 Soniox 日志自身的延迟），
    // 所以「刚开的课」在记上账之前是拦不住的；这里挡的是「今天已经明显超了还接着开」。
    // 老码没有 daily_rt_s -> 当「不限」（?? null）；老记录没有 rt_s -> num() 当 0。
    if (kind === "rt" && (c.daily_rt_s ?? null) !== null && num(u.rt_s) >= num(c.daily_rt_s)) {
      return { ok: false, reason: "quota", name: c.name };
    }
    if (INSTALL_RE.test(install || "")) {
      const devs = (await this.storage.get(`dev:${code}`)) || {};
      if (!devs[install]) {
        if (Object.keys(devs).length >= c.max_devices) return { ok: false, reason: "devices", name: c.name };
        devs[install] = { first: now, last: now };
      } else {
        devs[install].last = now;
      }
      await this.storage.put(`dev:${code}`, devs);
    }
    return { ok: true, name: c.name };
  }

  async usage({ code, asr_s, tokens_in, tokens_out, cache_hit, jev_q, model, ver }) {
    if (!CODE_RE.test(code || "")) return;
    const k = `use:${day(this.now())}:${code}`;
    const u = { ...USE0(), ...(await this.storage.get(k)) };
    if (asr_s !== undefined) {
      u.asr_s += num(asr_s); u.asr_reqs += 1;
      if (model) { const b = modelBucket(model); u.models[b] = num(u.models[b]) + 1; }
    }
    if (jev_q !== undefined) { u.jev_reqs += 1; u.jev_q += num(jev_q); }
    if (tokens_in !== undefined || tokens_out !== undefined) {
      u.llm_reqs += 1;
      u.tokens_in += num(tokens_in); u.tokens_out += num(tokens_out); u.cache_hit += num(cache_hit);
    }
    // 逐分钟（UTC 当日第几分钟）。只记这次请求真带来的数：没有转写也没有翻译 / Jev 的空记账不占格。
    const isAsr = asr_s !== undefined, isLlm = tokens_in !== undefined || tokens_out !== undefined;
    if (isAsr || isLlm || jev_q !== undefined) {
      const m = String(Math.floor((this.now() % DAY_MS) / 60000));
      u.mins = u.mins && typeof u.mins === "object" ? u.mins : {};
      const c = Array.isArray(u.mins[m]) ? u.mins[m] : [0, 0, 0, 0, 0];
      if (isAsr) { c[0] += 1; c[1] = Math.round((c[1] + num(asr_s)) * 10) / 10; }
      if (isLlm) { c[2] += 1; c[3] += num(tokens_in) + num(tokens_out); }
      if (jev_q !== undefined) c[4] = num(c[4]) + 1;
      u.mins[m] = c;
    }
    if (VER_RE.test(ver || "")) {
      if (ver in u.vers) u.vers[ver] = num(u.vers[ver]) + 1;
      else if (Object.keys(u.vers).length < VER_MAX_KEYS) u.vers[ver] = 1;
      else u.vers["+"] = num(u.vers["+"]) + 1;          // 满了就并进「+」（VER_RE 不收 "+"，撞不了）
    }
    await this.storage.put(k, u);
  }

  async add({ name, days, max_devices }) {
    const n = String(name || "").toLowerCase();
    if (!NAME_RE.test(n)) return { error: "name: 1–16 位小写字母或数字" };
    const now = this.now();
    const code = `cl-${n}-${randomHex(8)}`;
    const rec = {
      name: n, created: now, expires: now + num(days || DEFAULT_DAYS) * DAY_MS, revoked: false,
      max_devices: Number.isInteger(max_devices) && max_devices > 0 ? max_devices : DEFAULT_DEVICES,
      daily_asr_s: null, daily_tokens: null, daily_rt_s: null,
    };
    await this.storage.put(`code:${code}`, rec);
    return { code, ...rec };
  }

  async list() {
    const out = [];
    for (const [k, c] of await this.storage.list({ prefix: "code:" })) {
      const code = k.slice(5);
      const devs = (await this.storage.get(`dev:${code}`)) || {};
      out.push({ code, ...c, devices: Object.keys(devs).length,
                 last_seen: Math.max(0, ...Object.values(devs).map((d) => d.last)) });
    }
    return { codes: out };
  }

  // 用量查询。请求侧收三种写法（`{day}` 单日 / `{days:N}` 最近 N 天 / `{from,to}` 闭区间），
  // 但**返回形状只有一个**：{days: [{day, rows}], from, to} —— 单日就是长度 1 的数组。
  // ⚠️ 2026-10-14 起统一（原来单日回 `{day, rows}`）；两个调用方（cl_admin.py / test-passes.mjs）同步改过。
  async stats({ day: d, days, from, to }) {
    const today = day(this.now());
    const bad = [d, from, to].find((v) => v !== undefined && v !== null && !isDay(v));
    if (bad !== undefined) return { error: "bad_day", got: String(bad), expect: "YYYY-MM-DD" };
    let to0 = to || today;
    let from0;
    if (d) { from0 = d; to0 = d; }                      // 显式单日优先
    else if (from) from0 = from;
    else {
      const n = Math.min(Math.max(Number.isInteger(days) ? days : 7, 1), 366);
      from0 = addDays(to0, 1 - n);
    }
    if (from0 > to0) return { error: "bad_range", from: from0, to: to0 };
    // `days` 上面夹过，但 `from` / `to` 是调用方随便给的 —— 这里才是真正的封顶（否则要 1990 年
    // 就是一万多条）。2026-10-14 全仓审查抓到：原来那句封顶根本没写进去。
    const floor = addDays(to0, -365);
    if (from0 < floor) from0 = floor;
    const byDay = new Map();
    for (const [k, u] of await this.storage.list({ prefix: "use:" })) {
      const rest = k.slice(4);                          // <YYYY-MM-DD>:<码>
      const dd = rest.slice(0, 10);
      if (dd < from0 || dd > to0) continue;
      const code = rest.slice(11);
      const c = await this.storage.get(`code:${code}`);
      if (!byDay.has(dd)) byDay.set(dd, []);
      const { mins, ...rest2 } = u;                     // 逐分钟太大，不随 stats 回（走 /admin/minutes）
      byDay.get(dd).push({ name: c?.name ?? "?", code, ...rest2, active_min: mins ? Object.keys(mins).length : null });
    }
    // **区间里每一天都出现**（没记录的给空数组）—— 面板画趋势时不用自己补零。
    const out = [];
    for (let cur = from0; cur <= to0; cur = addDays(cur, 1)) out.push({ day: cur, rows: byDay.get(cur) || [] });
    return { days: out, from: from0, to: to0 };
  }

  // 某码某天的逐分钟。只认完整码 + 合法日期；没有记录回空对象（不是错）。
  async minutes({ code, day: d }) {
    if (!CODE_RE.test(code || "")) return { error: "minutes 只认完整码" };
    const dd = d === undefined ? day(this.now()) : d;
    if (!isDay(dd)) return { error: "bad_day", got: String(dd), expect: "YYYY-MM-DD" };
    const u = (await this.storage.get(`use:${dd}:${code}`)) || {};
    return { code, day: dd, mins: u.mins || {} };
  }

  // 某码的设备：**只回 install_id 前 6 位**（够区分两台，不把完整 id 交出去）+ 首次 / 最近（毫秒）。
  async devices({ code }) {
    if (!CODE_RE.test(code || "")) return { error: "devices 只认完整码" };
    const devs = (await this.storage.get(`dev:${code}`)) || {};
    return { code, devices: Object.entries(devs).map(([id, d]) => ({ id: id.slice(0, 6), first: d.first, last: d.last }))
      .sort((a, b) => b.last - a.last) };
  }

  // 按码或名字找到唯一一条再改；名字对应多条（同名重发过）时要求给码。
  async #find({ code, name }) {
    if (code) {
      const c = await this.storage.get(`code:${code}`);
      return c ? { code, c } : { error: "unknown code" };
    }
    const hits = [];
    for (const [k, c] of await this.storage.list({ prefix: "code:" })) {
      if (c.name === String(name || "").toLowerCase()) hits.push({ code: k.slice(5), c });
    }
    if (hits.length === 1) return hits[0];
    return { error: hits.length ? "同名有多条，请给 code" : "unknown name" };
  }

  async #edit(body, fn) {
    const f = await this.#find(body);
    if (f.error) return f;
    fn(f.c, this.now());
    await this.storage.put(`code:${f.code}`, f.c);
    return { code: f.code, ...f.c };
  }

  // 删码。⚠️ 三条闸，因为**这一步不可逆**（DO 没有回收站）：
  //   ① 必须给**完整码**（名字会歧义：同名可以有多条，删错了没法查）；
  //   ② 必须显式 `yes:true`（隔开「手滑点到」与「真想删」）；
  //   ③ 连设备与用量一起删 —— 只删 code: 的话，stats 里会留下 name 为 "?" 的孤儿行。
  async #remove({ code, yes }) {
    if (!CODE_RE.test(code || "")) return { error: "delete 只认完整码（code），不认名字" };
    if (yes !== true) return { error: "delete 要带 yes:true 才动手（不可逆）" };
    const c = await this.storage.get(`code:${code}`);
    if (!c) return { error: "unknown code" };
    const keys = [`code:${code}`, `dev:${code}`];
    let days = 0;
    for (const [k] of await this.storage.list({ prefix: "use:" })) {
      // 冒号是分隔符。⚠️ 实测过：这里换成 `k.includes(code)` 在**当前**的键形状下是等价的
      //   （码必是 `cl-名字-16hex`，名字不含 `-`，所以任何别的键都不可能把另一个码当子串包住）——
      //   写 endsWith 是把意图钉死，不是因为已知反例。键形状一变（比如名字允许 `-`）这条就要重看。
      if (k.endsWith(`:${code}`)) { keys.push(k); days += 1; }
    }
    await this.storage.delete(keys);
    return { ok: true, code, name: c.name, removed: keys.length, usage_days: days };
  }

  async #limit(body) {
    const a = limitOrNull(body.daily_asr_s), t = limitOrNull(body.daily_tokens), r = limitOrNull(body.daily_rt_s);
    if (a === undefined || t === undefined || r === undefined) return { error: "限额要么是非负数，要么是 null（不限）" };
    return this.#edit(body, (c) => {
      if ("daily_asr_s" in body) c.daily_asr_s = a;
      if ("daily_tokens" in body) c.daily_tokens = t;
      if ("daily_rt_s" in body) c.daily_rt_s = r;
      if (Number.isInteger(body.max_devices) && body.max_devices > 0) c.max_devices = body.max_devices;
    });
  }

  // ---- 实时转写的用量回收（见文件头）----

  // 游标：`soniox:<区>` = {since_ms, last_ms, seen:{uuid: end_ms}}。
  // 只认 `REGION_NAMES`（soniox-core 的地区表）里的区，别的直接报错 —— 别在 storage 里长野键。
  async #rtState(region) {
    if (!REGION_NAMES.includes(region)) return null;
    const raw = (await this.storage.get(`soniox:${region}`)) || {};
    const st = { since_ms: num(raw.since_ms), last_ms: num(raw.last_ms), seen: raw.seen };
    if (!st.seen || typeof st.seen !== "object" || Array.isArray(st.seen)) st.seen = {};
    return st;
  }

  // 下一轮该拉的窗口：{from_ms, since_ms, last_ms, seen}。from 只是**起点**（游标回退 OVERLAP_MS），
  // 正确性不靠它 —— 同一条日志会不会被记两次由 uuid 去重决定。
  async #rtCursor({ region }) {
    const st = await this.#rtState(region);
    if (!st) return { error: "bad_region" };
    return {
      region, since_ms: st.since_ms, last_ms: st.last_ms, seen: Object.keys(st.seen).length,
      from_ms: st.since_ms > 0 ? Math.max(0, st.since_ms - RT_OVERLAP_MS) : this.now() - RT_FIRST_MS,
    };
  }

  // 一批用量日志落账。入参 records = [{uuid, name, end_ms, ms}]（soniox-core.collectUsage 规范化过）。
  // 一个调用里做完四件事 —— 去重 / 找码 / 按天累加 / 推游标。DO 的 input gate 保证同一实例串行，
  // 所以要么整批生效、要么整批不动：崩在中间不会落半个账，也不会把游标推过去把数据甩掉。
  // 回 {ok, region, since_ms, seen, new_n, new_s, dup_n, lost:[{name, s, n}]}；lost = 去重后仍记不下的
  // （名字对不上唯一一条码：没发过 / 已被删 / 同名多条）。
  async #rtImport({ region, until_ms, records }) {
    const st = await this.#rtState(region);
    if (!st) return { error: "bad_region" };
    const until = Number(until_ms);
    if (!Number.isFinite(until) || until <= 0) return { error: "bad_until" };
    if (!Array.isArray(records)) return { error: "bad_records" };
    let dup_n = 0, new_n = 0;
    const per = new Map();   // `${名字}\n${日}` -> {name, dd, ms, n}：**按天**分桶（会话可以跨 UTC 午夜）
    for (const rec of records) {
      const uuid = rec && typeof rec.uuid === "string" ? rec.uuid : "";
      const end = Number(rec?.end_ms);
      // 形状不合的跳过：没 uuid 去重不了，没 end_ms 归不了天。（soniox-core 已经滤过一道，这里只兜底。）
      if (!uuid || !Number.isFinite(end)) continue;
      if (uuid in st.seen) { dup_n += 1; continue; }     // ⭐ 同一条日志只记一次：重叠窗口全靠这里兜
      st.seen[uuid] = end;                               // 立刻标上 —— 同一批里重复出现的也只会算一次
      const ms = Number(rec?.ms);
      const name = String(rec?.name ?? ""), dd = day(end);
      const k = `${name}\n${dd}`;
      const a = per.get(k) || { name, dd, ms: 0, n: 0 };
      a.ms += Number.isFinite(ms) && ms > 0 ? ms : 0;
      a.n += 1;
      per.set(k, a);
      new_n += 1;
    }
    // 名字 -> 码，一个名字只查一次。`#find` 不认重名（同名多条 = 报错）—— 记到哪条都是猜，宁可不记并报出来。
    const codeOf = new Map();
    const lost = [];
    let new_s = 0;
    for (const a of per.values()) {
      if (!codeOf.has(a.name)) codeOf.set(a.name, (await this.#find({ name: a.name })).code ?? null);
      const code = codeOf.get(a.name);
      if (!code) { lost.push({ name: a.name, s: Math.round(a.ms / 100) / 10, n: a.n }); continue; }
      new_s += a.ms / 1000;
      await this.#addRt(code, a.dd, a.ms, a.n);
    }
    // 游标：只许往前走，但也不许跳进未来 —— 一次坏 until 会让中间那段永远落在窗口外（再也没人拉它）。
    st.since_ms = Math.min(Math.max(st.since_ms, until), this.now() + 60000);
    st.last_ms = this.now();
    this.#rtPrune(st);
    await this.storage.put(`soniox:${region}`, st);
    return { ok: true, region, since_ms: st.since_ms, seen: Object.keys(st.seen).length,
             new_n, new_s: Math.round(new_s * 10) / 10, dup_n, lost };
  }

  // seen 只留「还可能被重拉」的那段（窗口回退 OVERLAP，留 2× 余量），再卡一个条数上限。
  // ⚠️ 条数上限真要撞上（> 1000 条 / 6 h ≈ 每小时 167 条会话），丢的是最老的几条；其中若有还落在重叠窗口里的，
  //    下一轮会**多记一次**。那个量级下先保「不把 DO 的单值撑爆」。
  #rtPrune(st) {
    const keep = st.since_ms - RT_KEEP_MS;
    for (const [u, e] of Object.entries(st.seen)) if (!(e > keep)) delete st.seen[u];
    const ent = Object.entries(st.seen);
    if (ent.length <= RT_MAX_SEEN) return;
    ent.sort((a, b) => a[1] - b[1]);
    for (const [u] of ent.slice(0, ent.length - RT_MAX_SEEN)) delete st.seen[u];
  }

  // 按天记 rt 用量。键与 usage() 完全相同（use:<日>:<码>），所以 stats / 面板 / 限额一眼就能看到。
  // ⚠️ rt_s 走 num() 兜老记录（`u.rt_s += x` 在 undefined 上会变 NaN）。ms -> 秒后按 0.1 s 归档，
  //    免得面板上出现 1234.5670000000001。
  async #addRt(code, dd, ms, n) {
    const k = `use:${dd}:${code}`;
    const u = { ...USE0(), ...(await this.storage.get(k)) };
    u.rt_s = Math.round((num(u.rt_s) + ms / 1000) * 10) / 10;
    u.rt_reqs = num(u.rt_reqs) + num(n);
    await this.storage.put(k, u);
  }
}
