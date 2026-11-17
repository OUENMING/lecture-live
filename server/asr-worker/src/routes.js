// 对话类调用的**上游路由表** —— 纯函数，不 import 任何 cloudflare:*，也不读 key / 冷却。
// 设计目标（Owen 2026-11-17 定）：**一条命令就能切**（`cl_admin.py route note opencode`），三家都接：
//   deepseek = DeepSeek 官方直连（body 原样，现状）
//   cc       = CommandCode（OpenAI 兼容端，模型 deepseek/deepseek-v4.1-flash）
//   opencode = OpenCode Go（zen/go/v1，模型 deepseek-v4.1-flash + 必带 x-opencode-session）
//
// ⚠️ 这里是「哪个调用类走哪家」的**唯一定义点**：handler.js 只负责搬（读表 / 查 key / 冷却 / 退回），
//    表怎么合并与怎么解析全在本文件。改路由规则别去 handler 里加 if。
//
// 来源优先级（handler.chatRoutes 合并，本文件只认「一张已经合并好的表」）：
//   Passes DO 里存的表（`cl_admin route …` 写） > env.CHAT_ROUTES（`chapter=deepseek,note=cc`） > 默认表。
// 表里还能写 `*` 作为「其余调用类」的默认；没写 `*` 则其余一律 deepseek（= 改动前的行为）。

export const UPSTREAMS = ["deepseek", "cc", "opencode"];
export const DEFAULT_UPSTREAM = "deepseek";

//: 默认表等价于改动前那套：`env.CMD_CHAT_CLASSES ?? "note,polish,prep"` 里的类 → cc，其余 → deepseek。
const CMD_CHAT_CLASSES_DEFAULT = "note,polish,prep";
//: 老客户端不发 `X-ClassLive-Call` 时的兜底：只有 ≥ 这个数才当 note（同旧 `CMD_CHAT_LEGACY_NOTE_TOKENS`）。
//   ⚠️ 这条只是**向后兼容**，不是路由规则本身；新客户端必须显式发 header。
const LEGACY_NOTE_TOKENS = 4000;

//: 调用类名：小写字母数字开头，最长 20（与 `X-ClassLive-Call` 的截断一致），外加 `*`。
const CLASS_RE = /^(\*|[a-z0-9][a-z0-9._-]{0,19})$/;

/** 上游名归一：不认识的一律 deepseek（fail-safe —— 表写错了不能把调用打没）。 */
export function normalizeUpstream(v) {
  const s = String(v ?? "").trim().toLowerCase();
  return UPSTREAMS.includes(s) ? s : DEFAULT_UPSTREAM;
}

/** 默认表：白名单里的类 → cc，其余不写（= 回落到 deepseek）。 */
export function defaultRoutes(env) {
  const out = {};
  for (const c of String(env?.CMD_CHAT_CLASSES ?? CMD_CHAT_CLASSES_DEFAULT)
    .split(",").map((s) => s.trim().toLowerCase()).filter(Boolean)) {
    if (CLASS_RE.test(c)) out[c] = "cc";
  }
  return out;
}

/**
 * `"chapter=deepseek,note=cc"`（env）或 `{note:"cc"}`（DO / 测试）→ 表。
 * 形状不合的项整条丢掉；值不在三家里 → deepseek（并把结果**照实留在表里**，好让人在打印里看见）。
 * 空 / 没写 → null（= 这一层不存在）。
 */
export function parseRoutes(raw) {
  if (raw === null || raw === undefined || raw === "") return null;
  if (typeof raw === "string") {
    const out = {};
    for (const item of raw.split(",")) {
      const s = item.trim();
      if (!s) continue;
      const i = s.indexOf("=");
      if (i <= 0) continue;
      const k = s.slice(0, i).trim().toLowerCase();
      if (!CLASS_RE.test(k)) continue;
      out[k] = normalizeUpstream(s.slice(i + 1));
    }
    return Object.keys(out).length ? out : null;
  }
  if (typeof raw !== "object" || Array.isArray(raw)) return null;
  const out = {};
  for (const [k, v] of Object.entries(raw)) {
    const c = String(k).trim().toLowerCase();
    if (!CLASS_RE.test(c)) continue;
    out[c] = normalizeUpstream(v);
  }
  return Object.keys(out).length ? out : null;
}

/**
 * 这次调用属于哪个类。显式 header 优先；老客户端不发 header，按 `max_tokens >= 4000` 兜底当 note。
 * ⚠️ `body.max_tokens` 必须是 **clamp 之前**的值（handler 里 `Number(x) || 16384` 会把缺失 / 非数字
 *    变成 16384，按它判「这是笔记」是 fail-open —— 缺字段的老客户端会被推进 cc）。
 */
export function routeClass(callClass, body) {
  const c = String(callClass ?? "").trim().toLowerCase().slice(0, 20);
  if (c) return c;
  const raw = Number(body?.max_tokens);
  return Number.isFinite(raw) && raw >= LEGACY_NOTE_TOKENS ? "note" : "legacy";
}

/**
 * 分层合并：`mergeRoutes(defaultRoutes(env), envRoutes(env), doTable)`（优先级从左到右升高）。
 *
 * ⭐ 关键语义：**上层的 `*` 管这一层的全部类**，包括下层点名过的那些 ——
 *   否则 `cl_admin route '*' opencode` 会被默认表里那三个 `note/polish/prep` 静默挡住（只改了「其余」），
 *   而用户说的「全走 opencode」显然不是这个意思。要单独留一类就显式写它（同层点名 > 同层 `*`）。
 */
export function mergeRoutes(...layers) {
  let out = {};
  for (const raw of layers) {
    const t = parseRoutes(raw);
    if (!t) continue;
    const star = Object.hasOwn(t, "*") ? t["*"] : null;
    const next = {};
    for (const [k, v] of Object.entries(out)) next[k] = star ?? v;   // 上一层没点名的类：有 `*` 就按 `*`
    for (const [k, v] of Object.entries(t)) next[k] = v;             // 点名的 / `*` 自己原样覆盖
    out = next;
  }
  return out;
}

/**
 * 表 + 调用类 + body → `"deepseek" | "cc" | "opencode"`。
 * 查表顺序：`表[类]` → `表["*"]` → `deepseek`。值不认识 → deepseek。
 * **不管 key、不管冷却、不管流式** —— 那三件是 handler 的事（这里保持纯函数，测试才好写）。
 */
export function resolveRoute(routes, callClass = "", body = undefined) {
  const table = routes && typeof routes === "object" && !Array.isArray(routes) ? routes : {};
  const cls = routeClass(callClass, body);
  const hit = Object.hasOwn(table, cls) ? table[cls]
    : Object.hasOwn(table, "*") ? table["*"]
      : DEFAULT_UPSTREAM;
  return normalizeUpstream(hit);
}
