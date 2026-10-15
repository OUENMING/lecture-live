// 额度状态机（纯逻辑，不 import 任何 cloudflare:*，Node 测试能直接跑）。
// 由 quota.js 里的 Durable Object 壳包一层；storage 只要有 async get/put。
//
// 状态存一行 JSON（key "state"）：
//   day              UTC 日期，跨日清零 groq_day
//   groq_day         今天已批准给 Groq 的次数
//   groq_min         {minute, n}：当前分钟已批准次数
//   groq_hour        {hour, s}：当前 UTC 小时已批准的音频秒（R18；Groq 的 ASH 是滚动窗口，这里用整点桶近似，
//                    边界处可能短暂放过多一点 —— 超了 Groq 回 429，由 groq_cooldown 兜住）
//   groq_day_s       今天已批准的音频秒（ASD）
//   qwen_day         今天实际用到千问的段数（R17，由 Worker 每段 report 一次 qwen_used）
//   groq_cooldown_until / qwen_cooldown_until   毫秒时间戳
//   qwen_exhausted   {模型名: 记录时间}；⚠️ **永不自动清** —— 换模型名或改 DO 名
//                    （index.js 里 DO_NAME）才会重置（官方额度是一次性的，不会自己回来）。
//   groq_blocked     {国家码: 解禁毫秒}：Groq 按**访客国家**拦（Cloudflare 把原访客国家带到子请求），
//                    某些国家一律 403。2026-10-15 实测：CN 访客 739/739 全 groq:403，而同一个 HKG 机房换 GB 访客是 200
//                    —— 所以被判的是国家不是机房。/plan 带该 country 就跳过 Groq 直接千问，6 h 后自动过期复探。
//   turbo            whisper-large-v3-turbo 的**独立**桶 {n, min, hour, s, cooldown_until}，含义同上面 groq_* 那组。
//                    Groq 按模型分桶（2026-10-11 实测：v3 剩 637 次时 turbo 剩 1999，x-ratelimit-remaining-requests）。
//                    v3 没额度时它当主力（排在千问前，见 #plan）；v3 当主力却失败时它是最后一道。
//                    OCW WER 11.8% / 漏 1.2%，比 v3（10.2%）和千问（9.5%）差、比本地（12.0–14.3%）好。

const GROQ_DAY_MAX = 1900;   // Groq 免费 2000 RPD，留 5% 余量，主动降级不等 429
const GROQ_MIN_MAX = 19;     // 20 RPM 同理
// Groq 免费档 whisper-large-v3：ASH 7200 / ASD 28800 音频秒；到 95% 就不批（R18，回源 console.groq.com/docs/rate-limits）。
const GROQ_HOUR_S_MAX = 7200 * 0.95;
const GROQ_DAY_S_MAX = 28800 * 0.95;
const GROQ_MIN_BILLED_S = 10;   // 单次不足 10 s 按 10 s 计
const QWEN_DAY_MAX = 3000;      // R17：约 9 节课；保护不可再生的免费额度
const MAX_COOLDOWN_S = 86400;
export const TURBO = "whisper-large-v3-turbo";
export const V3 = "whisper-large-v3";
// ⭐ Groq 自己报的剩余（2026-10-14 起）：每次 Groq 回包都带 `x-ratelimit-remaining-requests`（RPD 桶）与
//    `x-ratelimit-remaining-audio-seconds`（ASH 桶）。实测这两个是**连续回补**的令牌桶，不是到点清零：
//    用 1 次 reset-requests 报 43.2 s（= 86400/2000），用 3 次报 2m9.6s；音频秒每 0.5 s 回 1 秒（= 3600/7200）；
//    1 s 的请求只扣 1 秒（限流按真实秒，**不按**计费的 10 s 起步）。
//    hdr = {req, aud, req_lim, aud_lim, at}：上一次 Groq 报的数 + 那一刻的时间；到现在的回补按上面的速率推算。
//    有 hdr 就用它判 RPD 与 ASH（原来的 UTC 日 / 整点计数照记，只给面板看）；没有（刚部署、从没成功调过）才退回老算法。
//    ⚠️ 两条 Groq 不报的照旧本地记：RPM（20/分钟，`min`）与 ASD（28.8K/天，`s`）。
const HDR_MARGIN = 0.05;     // 推算剩余低于上限的 5% 就不批（并发中的请求 Groq 还没扣，留余量）
const GROQ_BLOCK_MS = 6 * 3600 * 1000;   // 国家封禁时长：被墙就躲 6 h，到点自动复探
const COUNTRY_RE = /^[A-Z]{2}$/;         // 访客国家码：两位大写（request.cf.country，如 CN / GB）
const BUCKET = () => ({ n: 0, min: { minute: -1, n: 0 }, hour: { hour: -1, s: 0 }, s: 0, cooldown_until: 0, hdr: null });

const FRESH = () => ({
  day: "", groq_day: 0, groq_min: { minute: -1, n: 0 },
  groq_hour: { hour: -1, s: 0 }, groq_day_s: 0, qwen_day: 0,
  groq_cooldown_until: 0, groq_hdr: null, qwen_exhausted: {}, qwen_cooldown_until: 0, turbo: BUCKET(),
  groq_blocked: {},
});

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });

export class QuotaCore {
  constructor(storage, now = () => Date.now()) {
    this.storage = storage;
    this.now = now;
  }

  async handle(request) {
    const path = new URL(request.url).pathname;
    let body;
    try { body = await request.json(); } catch { return json({ error: "bad body" }, 400); }
    // 读-改-写之间没有别的 await：DO 的 input gate 保证原子（官方 SQLite storage API 页）。
    const state = { ...FRESH(), ...(await this.storage.get("state")) };
    state.turbo = { ...BUCKET(), ...state.turbo };
    state.groq_blocked = { ...(state.groq_blocked || {}) };   // 老 state 没这个键也照读
    if (path === "/plan") return json(await this.#plan(state, body));
    if (path === "/report") { await this.#report(state, body); return json({ ok: true }); }
    if (path === "/admin/state") { this.#roll(state, this.now()); return json(this.#state(state)); }
    return json({ error: "not found" }, 404);
  }

  // 只读快照，给 handler.js 的 `/v1/admin/quota`（管理面板的系统额度卡）用。
  // ⚠️ **鉴权不在这里** —— 只有 handler.js 的 admin()（认 ADMIN_TOKEN）会转发进来，DO 自己谁都信。
  //    所以这条路由只能经 `/v1/admin/*` 到达，别在别处暴露。刻意只回计数与上限，**不含任何 key**。
  // 不落盘：`#roll` 只作用在 handle() 已经拷出来的那份 state 上 —— 这是展示口径，不是记账。
  #state(s) {
    const now = this.now();
    const hour = Math.floor(now / 3600000);
    const minute = Math.floor(now / 60000);
    // 分钟/小时桶是「上一格的数」，跨格了就当 0 显示（同 #take 的比法）。
    const norm = (b) => {
      const est = QuotaCore.estimate(b.hdr, now);
      return {
        used_reqs: b.n, used_s: b.s,
        min_reqs: b.min.minute === minute ? b.min.n : 0,
        hour_s: b.hour.hour === hour ? b.hour.s : 0,
        cooldown_until: b.cooldown_until,
        // Groq 报的剩余推算到现在（没报过 = null）；`reported_at` 是最后一次回包的时间。
        groq: est && { req_left: Math.floor(est.req), req_lim: b.hdr.req_lim,
                       aud_left: Math.floor(est.aud), aud_lim: b.hdr.aud_lim, reported_at: b.hdr.at },
      };
    };
    const v3 = { n: s.groq_day, min: s.groq_min, hour: s.groq_hour,
                 s: s.groq_day_s, cooldown_until: s.groq_cooldown_until, hdr: s.groq_hdr };
    return {
      day: s.day, now,
      caps: { reqs_day: GROQ_DAY_MAX, reqs_min: GROQ_MIN_MAX,
              s_hour: GROQ_HOUR_S_MAX, s_day: GROQ_DAY_S_MAX, qwen_day: QWEN_DAY_MAX },
      v3: norm(v3),
      turbo: norm(s.turbo),
      qwen: { used: s.qwen_day, max: QWEN_DAY_MAX,
              exhausted: Object.keys(s.qwen_exhausted || {}), cooldown_until: s.qwen_cooldown_until },
      // 只列未过期的封禁国家，给面板看（过期的不算，别让人以为还封着）。
      groq_blocked: Object.fromEntries(Object.entries(s.groq_blocked || {}).filter(([, t]) => t > now)),
    };
  }

  // 清掉已过期的国家封禁；每次要写 state 前都过一遍，别让废键长在 state 里。
  #prune(s, now) {
    for (const cc of Object.keys(s.groq_blocked)) if (!(s.groq_blocked[cc] > now)) delete s.groq_blocked[cc];
  }

  // 该国此刻是否被封（未过期才算；没有 country / 未知国家 = 没封）。
  #blocked(s, country, now) {
    return COUNTRY_RE.test(country || "") && s.groq_blocked[country] > now;
  }

  // 跨 UTC 日清零「当日」计数（exhausted 不清）。
  #roll(s, now) {
    const today = new Date(now).toISOString().slice(0, 10);
    if (s.day !== today) {
      s.day = today; s.groq_day = 0; s.groq_day_s = 0; s.qwen_day = 0;
      s.turbo = { ...s.turbo, n: 0, s: 0 };
    }
  }

  // Groq 报过的剩余推算到 `now`：按各自的回补速率加回来，封顶上限。没报过 -> null。
  static estimate(hdr, now) {
    if (!hdr || !Number.isFinite(hdr.at)) return null;
    const dt = Math.max(0, now - hdr.at) / 1000;
    return {
      req: Math.min(hdr.req_lim, hdr.req + dt * hdr.req_lim / 86400),
      aud: Math.min(hdr.aud_lim, hdr.aud + dt * hdr.aud_lim / 3600),
    };
  }

  // 一个 Groq 模型桶：几条上限都在 95% 以内才批，批准即计数。b = {n, min, hour, s, cooldown_until, hdr}（原地改）。
  #take(b, now, seconds) {
    const minute = Math.floor(now / 60000);
    const hour = Math.floor(now / 3600000);
    const nMin = b.min.minute === minute ? b.min.n : 0;
    const hourS = b.hour.hour === hour ? b.hour.s : 0;
    const real = Number.isFinite(seconds) ? seconds : 12;
    const billed = Math.max(GROQ_MIN_BILLED_S, real);
    const est = QuotaCore.estimate(b.hdr, now);
    // RPD 与 ASH：有 Groq 报的数就信它（令牌桶），否则退回 UTC 日 / 整点计数。
    const groqSide = est
      ? est.req - 1 >= b.hdr.req_lim * HDR_MARGIN && est.aud - real >= b.hdr.aud_lim * HDR_MARGIN
      : b.n < GROQ_DAY_MAX && hourS + billed <= GROQ_HOUR_S_MAX;
    const ok = now >= b.cooldown_until && groqSide
      && nMin < GROQ_MIN_MAX && b.s + billed <= GROQ_DAY_S_MAX;
    if (ok) {
      b.n += 1; b.min = { minute, n: nMin + 1 };
      b.hour = { hour, s: hourS + billed }; b.s += billed;
      // 先在本地扣掉，下一次 Groq 回包再用它报的真数覆盖（并发的几段之间不会都看到同一个旧余额）。
      if (est) b.hdr = { ...b.hdr, req: est.req - 1, aud: est.aud - real, at: now };
    }
    return ok;
  }

  async #plan(s, { chain, seconds, model, country }) {
    const now = this.now();
    this.#roll(s, now);
    this.#prune(s, now);
    const blocked = this.#blocked(s, country, now);   // 被封的国家不占 Groq 额度，直接千问
    if (model === TURBO) {   // 最后一道：只问 turbo 桶（v3 当主力却没拿到结果时 Worker 才来问）
      const groq = blocked ? false : this.#take(s.turbo, now, seconds);
      await this.storage.put("state", s);
      return { groq };
    }
    // 主力 Groq 模型：v3 有额度用 v3，否则 turbo，都没有 -> null（直接千问）。
    // ⚠️ turbo 排在千问**前面**：两个 Groq 桶每天刷新，千问是一次性额度（每模型 100 万 token、90 天）——
    //    v3 用完后若先走千问，每天第 7 节起就在烧不可再生的额度，而 turbo 的一整桶闲着（2026-10-11 Owen 拍板）。
    // v3 沿用老字段名（groq_*），装成同形的桶再写回
    const v3 = { n: s.groq_day, min: s.groq_min, hour: s.groq_hour, s: s.groq_day_s,
                 cooldown_until: s.groq_cooldown_until, hdr: s.groq_hdr };
    const groq = blocked ? null
      : this.#take(v3, now, seconds) ? V3 : this.#take(s.turbo, now, seconds) ? TURBO : null;
    s.groq_day = v3.n; s.groq_min = v3.min; s.groq_hour = v3.hour; s.groq_day_s = v3.s; s.groq_hdr = v3.hdr;
    const qwen = now < s.qwen_cooldown_until || s.qwen_day >= QWEN_DAY_MAX
      ? [] : chain.filter((m) => !(m in s.qwen_exhausted));
    await this.storage.put("state", s);
    return { groq, qwen };
  }

  // Worker 转来的 Groq 剩余：四个数都得是有限非负、上限 > 0，否则整条丢掉（宁可退回老算法也不信半截数）。
  static parseHdr(g) {
    if (!g || typeof g !== "object") return null;
    const v = ["req", "aud", "req_lim", "aud_lim"].map((k) => Number(g[k]));
    if (!v.every((x) => Number.isFinite(x) && x >= 0) || !(v[2] > 0 && v[3] > 0)) return null;
    return { req: Math.min(v[0], v[2]), aud: Math.min(v[1], v[3]), req_lim: v[2], aud_lim: v[3] };
  }

  async #report(s, r) {
    const now = this.now();
    this.#roll(s, now);
    this.#prune(s, now);
    if (r.qwen_used) s.qwen_day += 1;
    const secs = (v) => (Number.isFinite(v) && v > 0 ? Math.min(v, MAX_COOLDOWN_S) : null);
    const g = secs(r.groq_cooldown_s), q = secs(r.qwen_cooldown_s);
    if (g && r.model === TURBO) s.turbo.cooldown_until = now + g * 1000;
    else if (g) s.groq_cooldown_until = now + g * 1000;
    if (q) s.qwen_cooldown_until = now + q * 1000;
    const h = QuotaCore.parseHdr(r.groq_hdr);
    if (h && r.model === TURBO) s.turbo.hdr = { ...h, at: now };
    else if (h) s.groq_hdr = { ...h, at: now };
    if (typeof r.exhausted === "string" && !(r.exhausted in s.qwen_exhausted)) {
      s.qwen_exhausted[r.exhausted] = new Date(now).toISOString();
    }
    // 该国访客打 Groq 全 403：封 6 h，/plan 直接跳过 Groq。只认两位大写国家码，别的（含机房码）一律忽略。
    if (typeof r.groq_blocked === "string" && COUNTRY_RE.test(r.groq_blocked)) {
      s.groq_blocked[r.groq_blocked] = now + GROQ_BLOCK_MS;
    }
    await this.storage.put("state", s);
  }
}
