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

    // ⚠️ 体积闸门**按实际字节数**卡，不看 Content-Length —— token 公开，攻击者
    //    可以声明一个小的 Content-Length 却流一大坨。读进来核（单个对象 ≤ 32MB，
    //    正常音频分段 ≤ 约 19MB，缓冲开销可接受）。
    const payload = await request.arrayBuffer();
    if (payload.byteLength === 0) {
      return new Response("empty body", { status: 400 });
    }
    if (payload.byteLength > MAX_BYTES) {
      return new Response("too large", { status: 413 });
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
