// ClassLive 测试上传的摄入端。
//
// PUT /v1/objects/<hash16>/<name>  →  R2: classlive-test/<hash16>/<name>
//
// 四道防线（全在服务端，因为 token 是公开的）：
//   ① Authorization: Bearer <INGEST_TOKEN>   只证明「这是 ClassLive 在发」
//   ② 路径正则白名单                          挡目录穿越 / 乱写任意 key
//   ③ 实际字节数上限（读进来核）             挡大文件灌盘
//   ④ 限流（按 IP）                           挡洪峰
// ⚠️ 没有任何读 / 列 / 删路由 —— 这是「只写」性质的根据。
// 详细设计与出处见 ../../docs/PLAN-zero-config-upload.md §5/§6。

const MAX_BYTES = 32 * 1024 * 1024;                 // 单段 Opus 约 1–2 MB；一节课合计约 9 MB
const NAME_OK = /^\/v1\/objects\/([0-9a-f]{16})\/(report\.json|transcript\.md|meta\.json|audio\.\d{3}\.(opus|wav))$/;

/** 边读边计数，一超 `cap` 就 `cancel()` 掉剩余流并返回 `null`（超限）。
 *  ⚠️ 别换成 `new Response(body).arrayBuffer()` / `request.arrayBuffer()` —— 那两条都是
 *     **先把整条流读完**，上限就成了事后检查（内存 DoS 的入口，见 `fetch` 里那段）。 */
async function readCapped(body, cap) {
  const reader = body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > cap) {
      try {
        await reader.cancel();
      } catch {
        // 取消失败不影响判定：我们已经决定拒绝，剩下的字节不再进内存。
      }
      return null;
    }
    chunks.push(value);
  }
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.byteLength;
  }
  return out;
}

export default {
  async fetch(request, env) {
    if (request.method !== "PUT") {
      return new Response("method not allowed", { status: 405 });
    }

    // ⚠️ **fail-closed**：secret 没配（`wrangler secret put` 漏了）时 `env.INGEST_TOKEN`
    //    是 undefined，模板串会变成字面量 `"Bearer undefined"` → 谁都能进。显式挡掉。
    const expected = env.INGEST_TOKEN ? `Bearer ${env.INGEST_TOKEN}` : null;
    if (!expected || (request.headers.get("Authorization") || "") !== expected) {
      return new Response("unauthorized", { status: 401 });
    }

    const m = new URL(request.url).pathname.match(NAME_OK);
    if (!m) {
      return new Response("bad path", { status: 400 });
    }

    const ip = request.headers.get("CF-Connecting-IP") || "0.0.0.0";
    const { success } = await env.INGEST_LIMITER.limit({ key: ip });
    if (!success) {
      return new Response("rate limited", { status: 429 });
    }

    // ⚠️⚠️ 体积闸门**按实际字节数**卡，不看 Content-Length —— token 公开，攻击者
    //    可以声明一个小的 Content-Length 却流一大坨。
    //    ⚠️ 但也**不能先 `await request.arrayBuffer()` 再比大小**（2026-10-12 全仓审查核实）：
    //    那等于"先把最多 100MB 的 body 整个读进 Worker 内存，再判断它该不该被拒"，
    //    注释里「缓冲开销可接受」的前提在攻击者手里不成立（内存 DoS）。
    //    → 两层：① Content-Length 先快速拒绝（它可能撒谎，所以只是快速路径）；
    //      ② 真读的时候**边读边计数**，一超上限立刻停手、不再往下读。
    const declared = Number(request.headers.get("Content-Length") || 0);
    if (Number.isFinite(declared) && declared > MAX_BYTES) {
      return new Response("too large", { status: 413 });
    }
    if (!request.body) {
      return new Response("empty body", { status: 400 });
    }
    const payload = await readCapped(request.body, MAX_BYTES);
    if (payload === null) {
      return new Response("too large", { status: 413 });
    }
    if (payload.byteLength === 0) {
      return new Response("empty body", { status: 400 });
    }

    try {
      await env.BUCKET.put(`classlive-test/${m[1]}/${m[2]}`, payload, {
        httpMetadata: { contentType: "application/octet-stream" },
      });
    } catch {
      // 写失败（桶配额 / 瞬时故障）-> 明确 5xx，别让客户端收到 200 以为成了。
      return new Response("storage error", { status: 502 });
    }
    return new Response("ok", { status: 200 });
  },
};
