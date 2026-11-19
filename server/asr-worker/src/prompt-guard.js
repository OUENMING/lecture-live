// 网页版 `/v1/chat/completions` 的确定性闸：system 提示词白名单 + 消息形状 + user 段长度。
// 对外一个函数：`checkWebPrompt(body, callClass?)` -> `{ok:true}` 或 `{ok:false, status, error, reason}`
//   （400 `prompt_not_allowed` / `bad_messages`，413 `too_long`；`reason` 只给日志，不回客户端）。
// 为什么在模型外做：提示词不是秘密（OWASP LLM07），要拦的是「通行码 = 任意模型接口」——
//   只放行桌面同一份代码拼出来的几条 system prompt，改一个字就不认。纯函数 + Web Crypto，Node 能直测。
// ⚠️ 只对 `X-ClassLive-Client: web` 生效；桌面请求一个字节都不走这里（handler.chat 里判）。
import {
	MAX_USER_CHARS,
	PREP_MAX_USER_CHARS,
	PREP_PROMPT_SHA256,
	PROMPT_SHA256
} from "./prompt-allow.js";

// 唯一放行的形状是 `system,user`。依据（2026-11-17 读的桌面实现）：翻译三条提示词在 `cloud_translator`
// 里发法完全一样 —— `fix_and_translate_stream`（SYSTEM）、`fix_stream`（FIX）、`translate_draft`
// （DRAFT）都是 `[{"role":"system"},{"role":"user"}]`，`prompt_core` 也只导出这三条。
// 术语候选那条（`prep._prompt()`）在桌面 `prep.prepare()` 里也是同一个形状（`build_notes._chat_json`）。
// 桌面另有 `[system, user, assistant, …, user]` 的追问线程，但它的 system 是运行时按问题现拼的
// （`_answer_system()`），既不在白名单也没有稳定哈希 —— 网页不做这条线，所以不放行。
const SHAPE = "system,user";

//: 翻译那三条（白名单里除术语候选之外的全部）。
const TRANSLATE = new Set([...PROMPT_SHA256].filter((h) => h !== PREP_PROMPT_SHA256));

const hex = (buf) => Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
const denied = (status, error, reason) => ({ ok: false, status, error, reason });

// 每档 system 有**自己的** user 段上限：翻译是逐句 prompt（约 19k 字符），术语候选喂整块课件
// （约 30k 字符 × 2）。用同一个上限要么卡死术语候选，要么给翻译放进四倍的正文。
const capOf = (sum) => (sum === PREP_PROMPT_SHA256 ? PREP_MAX_USER_CHARS : MAX_USER_CHARS);

// 内容必须是字符串；哈希一次算完（不缓存：请求之间没有可复用的键，isolate 级 Map 只会给
// 攻击者一个把内存塞满的入口，而一次 SHA-256 的成本可忽略）。
async function digest(s) {
  const d = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return hex(d);
}

/**
 * @param body 请求体（只要 `messages`）
 * @param callClass `X-ClassLive-Call`。`undefined` = 调用方没给，**按哈希自己判档**（见下）；
 *   给了就按类别收窄：`"prep"` 只认术语候选那条，别的值只认翻译三条。
 *
 * 为什么允许 `undefined`：`handler.chat` 里的调用点在 `callClass` 解析**之前**（读头在那里更靠后，
 * 改它要动 handler.js，而那一行不属于本次改动范围）。`undefined` 时行为等价于「谁的白名单都认，
 * 各自用自己的上限」—— 不会因为没传类别就放行自拼的 system（那一步照旧过哈希）。
 */
export async function checkWebPrompt(body, callClass = undefined) {
  const msgs = body?.messages;
  if (!Array.isArray(msgs) || msgs.length === 0) return denied(400, "bad_messages", "no_messages");

  // 先判第一条是不是允许的 system —— 缺 system / 换成自拼的都是 `prompt_not_allowed`（接口形状不变，
  // 只是内容不认）。然后再判整条形状：多出 assistant 之类是 `bad_messages`（形状本身不对）。
  const first = msgs[0];
  if (first?.role !== "system") return denied(400, "prompt_not_allowed", `no_system:${msgs.length}`);
  const sys = first.content;
  if (typeof sys !== "string") return denied(400, "prompt_not_allowed", "system_not_string");
  let sum;
  try { sum = await digest(sys); } catch { return denied(400, "prompt_not_allowed", "digest_failed"); }
  if (!PROMPT_SHA256.has(sum)) return denied(400, "prompt_not_allowed", "hash");

  // 类别与 system 必须对得上：术语候选那条不许走翻译，翻译三条也不许冒充术语候选
  //（后者会拿到四倍的 user 段预算）。


  if (callClass === "prep") {
    if (sum !== PREP_PROMPT_SHA256) return denied(400, "prompt_not_allowed", "prep_system");
  } else if (callClass !== undefined && !TRANSLATE.has(sum)) {
    return denied(400, "prompt_not_allowed", "translate_system");
  }

  const shape = msgs.map((m) => String(m?.role ?? "")).join(",");
  if (shape !== SHAPE) return denied(400, "bad_messages", `shape:${shape.slice(0, 24)}`);

  const user = msgs[1]?.content;
  if (typeof user !== "string") return denied(400, "bad_messages", "user_not_string");
  const cap = capOf(sum);
  if (user.length > cap) return denied(413, "too_long", `user:${user.length}>${cap}`);
  return { ok: true };
}
