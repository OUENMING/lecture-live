// 网页版的 CORS。对外两个函数：
//   preflight(request, env)     -> 名单内路径 + 来源对 + OPTIONS: 204 预检；否则 null（调用方回原来的 404）
//   withCors(request, env, res) -> 名单内路径 + 来源对 + 非 GET/HEAD：给响应（含 4xx/5xx）补 Allow-Origin + Vary；否则原样返回
// 为什么是白名单：转写 / 翻译 / Soniox 临时 key 都是代付能力，挂上 `*` 就是任意网页花 Owen 的钱
// （docs/API-web-v0-20261117.md §8-2、E6）。没配 WEB_ORIGIN = 一个来源都不放行（fail-closed）。
// ⚠️ `/v1/transcribe`、`/v1/jev`、`/v1/admin/*` **永不加 CORS 头**（桌面版是 Python，用不着）。
// W12 起 `/v1/web/*` 多了 `PUT …/lines`、`GET …/:id` 与 `DELETE …/data`：这一族的预检放行
// GET / PUT / DELETE（只这一族；Soniox key 与翻译两条代付路径仍只放 POST）。
// W18 加 `PATCH …/session/:id`（改课名）—— PATCH 也只在这一族放行。

const PATHS = new Set(["/v1/soniox/key", "/v1/chat/completions"]);
const WEB_PREFIX = "/v1/web/";
const ALLOW_METHODS = "POST, OPTIONS";
const WEB_ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS";
const methodsFor = (path) => (path.startsWith(WEB_PREFIX) ? WEB_ALLOW_METHODS : ALLOW_METHODS);
const ALLOW_HEADERS = [
  "Authorization", "Content-Type", "X-ClassLive-Install", "X-ClassLive-Version",
  "X-ClassLive-Tz", "X-ClassLive-Call", "X-ClassLive-Client",
].join(", ");
const MAX_AGE = "600";

const webPath = (path) => PATHS.has(path) || path.startsWith(WEB_PREFIX);

// 该回显的来源；"" = 不放行（没配 / 来源不对 / 路径不在名单）。回显原值，不加 `*`。
function allowOrigin(request, env) {
  const want = String(env?.WEB_ORIGIN || "").trim();
  if (!want) return "";
  const origin = request.headers.get("origin") || "";
  if (!origin || origin !== want) return "";
  return webPath(new URL(request.url).pathname) ? origin : "";
}

export function preflight(request, env) {
  if (request.method !== "OPTIONS") return null;
  const origin = allowOrigin(request, env);
  if (!origin) return null;
  return new Response(null, {
    status: 204,
    headers: {
      "access-control-allow-origin": origin,
      "access-control-allow-methods": methodsFor(new URL(request.url).pathname),
      "access-control-allow-headers": ALLOW_HEADERS,
      "access-control-max-age": MAX_AGE,
      vary: "Origin",
    },
  });
}

// 只给真接口调用补头：GET / HEAD 的 404 保持改前样子（一个 CORS 头也不带）——
// **例外**：`/v1/web/` 下的 GET 是网页版的真接口（`GET …/session/:id`、`GET …/sessions`），必须带头；
// 名单里其他路径（老路由）的 GET 原地不动。POST / PUT / DELETE 一律补。
export function withCors(request, env, res) {
  const isGet = request.method === "GET" || request.method === "HEAD";
  const web = new URL(request.url).pathname.startsWith(WEB_PREFIX);
  if (isGet && !web) return res;
  const origin = allowOrigin(request, env);
  if (!origin) return res;
  const h = new Headers(res.headers);
  h.set("access-control-allow-origin", origin);
  h.append("vary", "Origin");
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers: h });
}
