// 网页版测试数据落盘（W20）：浏览器把**遥测事件**与**课堂录音分段**原样丢上来，我们原样存进 R2（绑定 `WEB_DATA`）。
// 对外接口（只有 handler.js 调）：
//   matchDataPath(method, path) -> {kind:"telemetry"} | {kind:"audio", id, n} | null   路由匹配（鉴权在前一层）
//   dataRoute(request, env, hit, name) -> Response(204)                              读 body / 校验 / 写 R2
//   adminWeb(env, path, body) -> Response                                            /v1/admin/web/{list,get}（令牌在 handler）
//   DATA_KINDS / dataOff(env, kind)                                                  开关与种类
// 为什么这样：测试用户都已授权采集，目标是**尽可能多拿到能重建故障的数据**；硬约束是任何一条失败都不能影响上课 ——
// 所以校验只做「能落盘」的最低限度（体积 / 事件是数组 / 段号 / 会话号形状），事件内容一个字段都不解析；
// 写 R2 失败只记一行日志、照回 204（客户端本来就是 fire-and-forget，回 5xx 只会让它重试并拖慢上课）。
// 键：`telemetry/<UTC 日>/<码名>/<session_id 或 nosession>/<rx_at>-<4hex>.ndjson`、`audio/<码名>/<session_id>/<n 补零 4 位>.<ext>`。

const TELEMETRY_MAX = 64 * 1024;
const EVENTS_MAX = 500;
const AUDIO_MAX = 4 * 1024 * 1024;
// 本机会话号 = `web-<日期>-<时分秒>-<uuid 前 6 位 hex>`（web/src/lib/live/connect.ts newSessionId）
const SESSION_ID = /^(s_[0-9a-f]{12}|web-\d{8}-\d{6}-[0-9a-f]{6})$/;
const NAME_OK = /^[a-z0-9]{1,16}$/;
const DATA_PREFIX = ["telemetry/", "audio/"];
const EXT = [["audio/webm", "webm"], ["video/webm", "webm"], ["audio/ogg", "ogg"], ["audio/mp4", "mp4"]];

export const DATA_KINDS = Object.freeze(["telemetry", "audio"]);

// "off"（大小写 / 空格无关）= 关。默认开：没配就是收。
const off = (v) => String(v ?? "").trim().toLowerCase() === "off";
// 遥测总闸关掉时录音也一起关（同一批数据、同一个目的），录音另有自己的闸。
export const dataOff = (env, kind) =>
  off(env?.WEB_TELEMETRY) || (kind === "audio" && off(env?.WEB_AUDIO_UPLOAD));

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const done = (extra = {}) => new Response(null, { status: 204, headers: extra });

export function matchDataPath(method, path) {
  if (path === "/v1/web/telemetry") return method === "POST" ? { kind: "telemetry" } : null;
  const m = path.match(/^\/v1\/web\/audio\/([^/]+)\/([^/]+)$/);
  // 形状校验留给 dataRoute（在鉴权之后）：这里只认「有这么一条路」。
  return m && method === "PUT" ? { kind: "audio", id: m[1], n: m[2] } : null;
}

export async function dataRoute(request, env, hit, name) {
  if (dataOff(env, hit.kind)) return done({ "x-cl-telemetry": "off" });
  const safe = NAME_OK.test(String(name || "")) ? name : "unknown";
  return hit.kind === "telemetry" ? telemetry(request, env, safe) : audio(request, env, hit, safe);
}

async function telemetry(request, env, name) {
  if (Number(request.headers.get("content-length")) > TELEMETRY_MAX) {
    return json({ error: "too_large" }, 413);
  }
  const blob = await readCapped(request.body, TELEMETRY_MAX);
  if (!blob) return json({ error: "too_large" }, 413);
  let body;
  try { body = JSON.parse(await blob.text()); } catch { return json({ error: "bad_body", detail: "json" }, 400); }
  if (!Array.isArray(body?.events)) return json({ error: "bad_body", detail: "events" }, 400);
  if (body.events.length > EVENTS_MAX) return json({ error: "too_large", detail: "events" }, 413);

  const rx_at = Date.now();
  const country = String(request.cf?.country || "").slice(0, 2);
  const colo = String(request.cf?.colo || "").slice(0, 4);
  const sid = sessionOf(body?.session_id);
  const lines = body.events.map((e) => {
    const row = e && typeof e === "object" && !Array.isArray(e) ? e : { event: e };
    // 服务端字段与事件同层，且**覆盖在后**：客户端伪造的 rx_at / name 一律被这里盖掉。
    return JSON.stringify({ ...row, rx_at, country, colo, name, session_id: sid === "nosession" ? undefined : sid });
  });
  const key = `telemetry/${day(rx_at)}/${name}/${sid}/${rx_at}-${rand4()}.ndjson`;
  await put(env, key, lines.join("\n") + "\n", "application/x-ndjson");
  return done();
}

async function audio(request, env, hit, name) {
  if (!SESSION_ID.test(hit.id)) return json({ error: "not_found", detail: "session_id" }, 404);
  if (!/^\d{1,4}$/.test(hit.n) || Number(hit.n) > 9999) return json({ error: "bad_body", detail: "n" }, 400);
  const ext = extOf(request.headers.get("content-type"));
  if (!ext) return json({ error: "bad_body", detail: "content-type" }, 415);
  if (Number(request.headers.get("content-length")) > AUDIO_MAX) return json({ error: "too_large" }, 413);
  const blob = await readCapped(request.body, AUDIO_MAX);
  if (!blob) return json({ error: "too_large" }, 413);
  if (blob.size === 0) return json({ error: "bad_body", detail: "empty" }, 400);
  const key = `audio/${name}/${hit.id}/${String(Number(hit.n)).padStart(4, "0")}.${ext}`;
  // 同一 key 重传直接覆盖（幂等）：客户端补传同一段不该在桶里堆两份。
  await put(env, key, blob, `audio/${ext}`);
  return done();
}

// ---- 管理端：只读地按前缀列 / 取（前缀白名单挡住整个桶被翻）----
export async function adminWeb(env, path, body) {
  if (!env?.WEB_DATA) return json({ error: "upstream" }, 503);
  if (path === "/v1/admin/web/list") {
    const prefix = String(body?.prefix || "");
    if (!DATA_PREFIX.some((p) => prefix.startsWith(p))) return json({ error: "bad_body", detail: "prefix" }, 400);
    const cursor = typeof body?.cursor === "string" && body.cursor ? body.cursor : undefined;
    const page = await env.WEB_DATA.list({ prefix, limit: 1000, cursor });
    return json({
      objects: page.objects.map((o) => ({
        key: o.key, size: o.size, uploaded: o.uploaded ? new Date(o.uploaded).toISOString() : null,
      })),
      cursor: page.truncated ? page.cursor : null,
    });
  }
  if (path === "/v1/admin/web/get") {
    const key = String(body?.key || "");
    if (!DATA_PREFIX.some((p) => key.startsWith(p))) return json({ error: "bad_body", detail: "key" }, 400);
    const obj = await env.WEB_DATA.get(key);
    if (!obj) return json({ error: "not_found" }, 404);
    return new Response(obj.body, {
      status: 200, headers: { "content-type": obj.httpMetadata?.contentType || "application/octet-stream" },
    });
  }
  return json({ error: "not_found" }, 404);
}

// ---- 底层 ----
// 边读边计数（抄 server/upload-worker/src/index.js 的 readCapped）：超了立刻取消，剩下的字节不进内存。
// 直接给 Blob（R2 put 收 Blob），别自己再拼 Uint8Array —— 那会多一次整份拷贝。
async function readCapped(body, cap) {
  const reader = body?.getReader?.();
  if (!reader) return new Blob([]);
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done: end, value } = await reader.read();
    if (end) break;
    total += value.byteLength;
    if (total > cap) {
      try { await reader.cancel(); } catch { /* 已关 */ }
      return null;
    }
    chunks.push(value);
  }
  return new Blob(chunks);
}

async function put(env, key, value, contentType) {
  try {
    if (!env?.WEB_DATA) throw new Error("no WEB_DATA binding");
    await env.WEB_DATA.put(key, value, { httpMetadata: { contentType } });
    return true;
  } catch (e) {
    // 失败只记一行（键截断），照回 204：测试数据丢了可以再采，上课卡住不行。
    console.log(JSON.stringify({ web_data_write_failed: key.slice(0, 120), msg: String(e).slice(0, 120) }));
    return false;
  }
}

const day = (ms) => new Date(ms).toISOString().slice(0, 10);
const sessionOf = (v) => (SESSION_ID.test(String(v || "")) ? String(v) : "nosession");
const rand4 = () =>
  [...crypto.getRandomValues(new Uint8Array(2))].map((b) => b.toString(16).padStart(2, "0")).join("");
const extOf = (ct) => {
  const t = String(ct || "").split(";")[0].trim().toLowerCase();
  return EXT.find(([m]) => m === t)?.[1] || null;
};

// 「立即删除我的全部数据」也要删测试数据（OCR D：原来只删 web/<hash>/ 账本，遥测与录音按**码名**存、一条没删）。
// 遥测键 telemetry/<日>/<码名>/…，录音键 audio/<码名>/…。测试数据量小（一节课百来个对象），全量列 telemetry/ 再按段过滤。
// 返回删了几个对象；永不抛（删不掉的记日志，下一次删除再来）。
export async function purgeTestData(env, name) {
  if (!env?.WEB_DATA || !/^[a-z0-9]{1,16}$/.test(String(name || ""))) return 0;
  let n = 0;
  const sweep = async (prefix, keep) => {
    let cursor;
    do {
      const page = await env.WEB_DATA.list({ prefix, limit: 1000, cursor });
      const keys = page.objects.map((o) => o.key).filter(keep);
      if (keys.length) { await env.WEB_DATA.delete(keys); n += keys.length; }
      cursor = page.truncated ? page.cursor : undefined;
    } while (cursor);
  };
  try {
    await sweep("telemetry/", (k) => k.split("/")[2] === name);
    await sweep(`audio/${name}/`, () => true);
  } catch (e) {
    console.log(JSON.stringify({ web_data: "purge_failed", name, msg: String(e).slice(0, 120) }));
  }
  return n;
}
