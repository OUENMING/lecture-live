// 一人一码（通行码）状态机 —— 纯逻辑，不 import 任何 cloudflare:*，Node 测试能直接跑。
// 由 passes.js 里的 Durable Object 壳包一层；storage 只要有 async get / put / list({prefix})。
// 设计：docs/PLAN-passkey.md §2–§6（Owen 2026-10-11 拍板：默认不限额、设备 2 台、有效期 120 天）。
//
// 键（DO 的 KV 接口；SQLite 后端的 DO 同样支持，免费档可用）：
//   code:<码>              {name, created, expires, revoked, max_devices, daily_asr_s, daily_tokens}
//   dev:<码>               {<install_id>: {first, last}}
//   use:<YYYY-MM-DD>:<码>  {asr_s, asr_reqs, llm_reqs, tokens_in, tokens_out, cache_hit}
// ⚠️ 只记数字：不记文本、音频、IP。日志里也只出现码的「名字段」。

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
      case "/admin/stats": return json(await this.stats(body));
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

  async usage({ code, asr_s, tokens_in, tokens_out, cache_hit }) {
    if (!CODE_RE.test(code || "")) return;
    const k = `use:${day(this.now())}:${code}`;
    const u = { asr_s: 0, asr_reqs: 0, llm_reqs: 0, tokens_in: 0, tokens_out: 0, cache_hit: 0,
                ...(await this.storage.get(k)) };
    if (asr_s !== undefined) { u.asr_s += num(asr_s); u.asr_reqs += 1; }
    if (tokens_in !== undefined || tokens_out !== undefined) {
      u.llm_reqs += 1;
      u.tokens_in += num(tokens_in); u.tokens_out += num(tokens_out); u.cache_hit += num(cache_hit);
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
      daily_asr_s: null, daily_tokens: null,
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

  async stats({ day: d }) {
    const want = d || day(this.now());
    const rows = [];
    for (const [k, u] of await this.storage.list({ prefix: `use:${want}:` })) {
      const code = k.slice(`use:${want}:`.length);
      const c = await this.storage.get(`code:${code}`);
      rows.push({ name: c?.name ?? "?", code, ...u });
    }
    return { day: want, rows };
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

  async #limit(body) {
    const a = limitOrNull(body.daily_asr_s), t = limitOrNull(body.daily_tokens);
    if (a === undefined || t === undefined) return { error: "限额要么是非负数，要么是 null（不限）" };
    return this.#edit(body, (c) => {
      if ("daily_asr_s" in body) c.daily_asr_s = a;
      if ("daily_tokens" in body) c.daily_tokens = t;
      if (Number.isInteger(body.max_devices) && body.max_devices > 0) c.max_devices = body.max_devices;
    });
  }
}
