// Soniox 实时转写（WebSocket）的纯逻辑：发临时 key + 拉用量日志。不 import cloudflare:*，Node 测试直接跑。
//
// 为什么这么接（2026-10-17）：客户端拿一把临时 key 后**自己连** Soniox，音频不进 Cloudflare、更不落我们的机器；
// 主 key 只留在 Worker 的 secret 里。临时 key 60 s 内能用来**开一条流**，单次会话上限由
// max_session_duration_seconds 钉住；用量靠 client_reference_id（= `cl-<名字>-…` 的 <名字>）回收。
//
// 出处（都是当天读的官方原文，路径见下）：
//   /docs/guides/temporary-api-keys        临时 key 的四个限制字段与语义（single_use / expires / max_session / client_reference_id）
//   /docs/guides/websocket-authentication  客户端怎么带 key（Authorization 头或协议表；临时 key 前缀 snx_temp_）
//   /docs/guides/usage-logs                用量日志：按请求一条，带 uuid / end_time / input_audio_duration_ms / client_reference_id
//   /docs/api-reference/auth/create_temporary_api_key   POST /v1/auth/temporary-api-key，回 201 {api_key, expires_at}
//   /docs/api-reference/other/get_usage_logs            GET /v1/usage-logs?start_time&end_time&limit&sort&cursor
//                                                       （按 end_time 过滤、左闭右开、窗口 ≤ 31 天、start 不早于 91 天前）
//   /docs/data-residency                               区域域名：美国 api.soniox.com / stt-rt.soniox.com
//                                                                   日本 api.jp.soniox.com / stt-rt.jp.soniox.com
//
// ⚠️ **key 绝不进日志 / 事件**：本模块只回 {status, api_key} 两个字段，调用方只许用 status 与「有没有 key」，
//    别把回包整体 print / 记进 Analytics Engine。

export const STT_RT_MODEL = "stt-rt-v5";       // 客户端 start 消息里的模型名（官方 websocket-api 的默认示例）
export const KEY_TTL_S = 60;                   // 能用来「开新流」的时长；官方 1–3600，开一条流 60 s 足够
export const DEFAULT_MAX_SESSION_S = 10800;    // 单次会话上限 3 h（一节课的量）；官方上限 18000
export const MAX_SESSION_S = 18000;
const MINT_TIMEOUT_MS = 5000;                  // 发 key 的超时（官方没给 p99；探针到 api.* 的建连通常 < 300 ms）
const LOG_TIMEOUT_MS = 10000;                  // 拉一页日志的超时
const PAGE_LIMIT = 100;                        // 每页条数（官方没写默认值，只写「最大条数」）
export const PAGE_CAP = 5;                     // 一次回收最多 5 页 = 500 条；拉满还有下一页由调用方决定游标推到哪

const REGIONS = {
  us: { api: "https://api.soniox.com", ws: "wss://stt-rt.soniox.com/transcribe-websocket" },
  jp: { api: "https://api.jp.soniox.com", ws: "wss://stt-rt.jp.soniox.com/transcribe-websocket" },
};
// 走日本区的访客：中国大陆 + 港澳台 + 日韩。依据 data-residency：选了区域的项目，音频与转写**只**在那个区域
// 处理与存储 —— 这些地方的访客就近落日本，音频不出这个圈。
const JP_COUNTRIES = new Set(["CN", "HK", "MO", "TW", "JP", "KR"]);

export const apiBase = (region) => REGIONS[region].api;
export const wsUrl = (region) => REGIONS[region].ws;

// 走哪个区：**两个条件都满足**才日本 —— ① 配了日本 key ② 访客国家在上表；否则一律美国。
// 现在只配了美国 key，所以第一个条件 = false，自然全走美国；以后加了 SONIOX_KEY_JP 就自动切。
export function pickRegion(country, env) {
  return env?.SONIOX_KEY_JP && JP_COUNTRIES.has(String(country || "").toUpperCase()) ? "jp" : "us";
}

// 单次会话上限（秒）：env 给了就用，夹进官方允许的 [1, 18000]；没给 / 非法 -> 默认 3 h。
// ⚠️ 夹一下是有意的：越界的值上游会回 400，那就变成每次请求都 502 —— 配置写错不该把整条路打死。
export function maxSessionS(env) {
  const n = Math.round(Number(env?.SONIOX_MAX_SESSION_S));
  return Number.isFinite(n) && n >= 1 ? Math.min(n, MAX_SESSION_S) : DEFAULT_MAX_SESSION_S;
}

// 发 key 的请求体（官方 create_temporary_api_key）。
// client_reference_id = 码名（`cl-<名字>-…` 的 <名字>）—— **绝不放完整码**：Soniox 侧与用量日志里
// 只该看到名字，看到完整码就等于把一把能换成额度的凭据写进了别人的库。
export function tempKeyBody({ name, maxSessionS: cap }) {
  return {
    usage_type: "transcribe_websocket",
    expires_in_seconds: KEY_TTL_S,
    single_use: true,               // 一把 key 只许开一条流（官方 single_use 就是干这个的）
    max_session_duration_seconds: cap,
    client_reference_id: name,
  };
}

// 带超时的 fetch + JSON 解析：**响应体也算在超时里**（对方挂住不回包时不能一直等）。
// 超时 / 网络错 -> {status: 0, ok: false}；非 JSON 回包 -> body: null。
async function fetchJson(timeoutMs, url, init) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...init, signal: ac.signal });
    const text = await res.text();
    let body = null;
    try { body = JSON.parse(text); } catch { /* 非 JSON */ }
    return { status: res.status, ok: res.ok, body };
  } catch {
    return { status: 0, ok: false, body: null };
  } finally {
    clearTimeout(timer);
  }
}

// 发一把临时 key。回 {status, api_key}：api_key 只在「2xx 且回包里真有它」时非空，其余（上游 4xx/5xx、
// 超时、回包缺字段）都是空串 —— 调用方据此回 502，**fail-closed**。
export async function mintTempKey({ region, apiKey, name, maxSessionS: cap, timeoutMs = MINT_TIMEOUT_MS }) {
  const r = await fetchJson(timeoutMs, `${apiBase(region)}/v1/auth/temporary-api-key`, {
    method: "POST",
    headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json" },
    body: JSON.stringify(tempKeyBody({ name, maxSessionS: cap })),
  });
  const api_key = r.ok && typeof r.body?.api_key === "string" ? r.body.api_key : "";
  return { status: r.status, api_key };
}

// 一条用量日志 -> {uuid, name, end_ms, ms}。形状不合（没 uuid / end_time 解析不出来）-> null（丢掉并计数）。
// ms = input_audio_duration_ms（STT 的输入音频时长，官方 schema 的字段名）；负数 / 非数当 0，但记录照样算一条。
function normLog(raw) {
  if (!raw || typeof raw !== "object") return null;
  const uuid = typeof raw.uuid === "string" ? raw.uuid : "";
  const end_ms = Date.parse(String(raw.end_time ?? ""));
  if (!uuid || !Number.isFinite(end_ms)) return null;
  const dur = Number(raw.input_audio_duration_ms);
  return { uuid, name: String(raw.client_reference_id ?? ""), end_ms, ms: Number.isFinite(dur) && dur > 0 ? dur : 0 };
}

// 拉用量日志（[sinceMs, untilMs) 左闭右开 —— 与官方「按 end_time 过滤、start 含 / end 不含」一致）。
// 回 {records, bad, error, more}：
//   records 已规范化（上层直接喂给 Passes 的去重/归属）
//   bad     形状不合被丢掉的条数（每条都要能看见，别静默）
//   error   null = 成功；HTTP 状态码 = 上游拒绝；0 = 超时 / 网络错。**调用方据此别动游标**，下轮整个窗口重来
//   more    拉满 PAGE_CAP 还有下一页（调用方别把游标推到 until，只推到已拉到的最后一条）
// ⚠️ 不传 `sort`：官方只说「end_time_desc 拿最近的」，没说默认值；不传 = 两次请求都不传，分页语义一致。
export async function collectUsage({ region, apiKey, sinceMs, untilMs, pageCap = PAGE_CAP, timeoutMs = LOG_TIMEOUT_MS }) {
  const records = [];
  let bad = 0, error = null, cursor = null, pages = 0;
  do {
    const q = new URLSearchParams({
      start_time: new Date(sinceMs).toISOString(),
      end_time: new Date(untilMs).toISOString(),
      limit: String(PAGE_LIMIT),
    });
    if (cursor) q.set("cursor", cursor);
    const r = await fetchJson(timeoutMs, `${apiBase(region)}/v1/usage-logs?${q}`, {
      headers: { authorization: `Bearer ${apiKey}` },
    });
    // 失败 / 回包形状不对都当失败：调用方据此**不动游标**，下轮整个窗口重来。
    // （形状不对时 status 会是 200 —— 那就是「通了但不是我们要的那份 JSON」。）
    if (!r.ok || !r.body || !Array.isArray(r.body.usage_logs)) { error = r.status || 0; break; }
    for (const raw of r.body.usage_logs) {
      const rec = normLog(raw);
      if (rec) records.push(rec); else bad += 1;
    }
    cursor = typeof r.body.next_page_cursor === "string" && r.body.next_page_cursor ? r.body.next_page_cursor : null;
    pages += 1;
  } while (cursor && pages < pageCap);
  return { records, bad, error, more: !!cursor };
}
