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

const GROQ_DAY_MAX = 1900;   // Groq 免费 2000 RPD，留 5% 余量，主动降级不等 429
const GROQ_MIN_MAX = 19;     // 20 RPM 同理
// Groq 免费档 whisper-large-v3：ASH 7200 / ASD 28800 音频秒；到 95% 就不批（R18，回源 console.groq.com/docs/rate-limits）。
const GROQ_HOUR_S_MAX = 7200 * 0.95;
const GROQ_DAY_S_MAX = 28800 * 0.95;
const GROQ_MIN_BILLED_S = 10;   // 单次不足 10 s 按 10 s 计
const QWEN_DAY_MAX = 3000;      // R17：约 9 节课；保护不可再生的免费额度
const MAX_COOLDOWN_S = 86400;

const FRESH = () => ({
  day: "", groq_day: 0, groq_min: { minute: -1, n: 0 },
  groq_hour: { hour: -1, s: 0 }, groq_day_s: 0, qwen_day: 0,
  groq_cooldown_until: 0, qwen_exhausted: {}, qwen_cooldown_until: 0,
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
    if (path === "/plan") return json(await this.#plan(state, body));
    if (path === "/report") { await this.#report(state, body); return json({ ok: true }); }
    return json({ error: "not found" }, 404);
  }

  // 跨 UTC 日清零「当日」计数（exhausted 不清）。
  #roll(s, now) {
    const today = new Date(now).toISOString().slice(0, 10);
    if (s.day !== today) { s.day = today; s.groq_day = 0; s.groq_day_s = 0; s.qwen_day = 0; }
  }

  async #plan(s, { chain, seconds }) {
    const now = this.now();
    this.#roll(s, now);
    const minute = Math.floor(now / 60000);
    const hour = Math.floor(now / 3600000);
    const nMin = s.groq_min.minute === minute ? s.groq_min.n : 0;
    const hourS = s.groq_hour.hour === hour ? s.groq_hour.s : 0;
    const billed = Math.max(GROQ_MIN_BILLED_S, Number.isFinite(seconds) ? seconds : 12);
    const groq = now >= s.groq_cooldown_until
      && s.groq_day < GROQ_DAY_MAX && nMin < GROQ_MIN_MAX
      && hourS + billed <= GROQ_HOUR_S_MAX && s.groq_day_s + billed <= GROQ_DAY_S_MAX;
    if (groq) {   // 批准即计数
      s.groq_day += 1; s.groq_min = { minute, n: nMin + 1 };
      s.groq_hour = { hour, s: hourS + billed }; s.groq_day_s += billed;
    }
    const qwen = now < s.qwen_cooldown_until || s.qwen_day >= QWEN_DAY_MAX
      ? [] : chain.filter((m) => !(m in s.qwen_exhausted));
    await this.storage.put("state", s);
    return { groq, qwen };
  }

  async #report(s, r) {
    const now = this.now();
    this.#roll(s, now);
    if (r.qwen_used) s.qwen_day += 1;
    const secs = (v) => (Number.isFinite(v) && v > 0 ? Math.min(v, MAX_COOLDOWN_S) : null);
    const g = secs(r.groq_cooldown_s), q = secs(r.qwen_cooldown_s);
    if (g) s.groq_cooldown_until = now + g * 1000;
    if (q) s.qwen_cooldown_until = now + q * 1000;
    if (typeof r.exhausted === "string" && !(r.exhausted in s.qwen_exhausted)) {
      s.qwen_exhausted[r.exhausted] = new Date(now).toISOString();
    }
    await this.storage.put("state", s);
  }
}
