// Worker 判据 —— 直接执行 src/index.js（真源码），用桩 env 跑负例 + 正例。
//
//   node server/upload-worker/test.mjs
//
// 为什么是 Node 不是 Python 闸门：Worker 是 JS，Python 测不了；而不测它就只能靠
// `wrangler deploy` 后手敲 curl。这里 import 那份**源文件本体**（不是重写逻辑），
// 桩掉 R2 / 限流 / secret，覆盖四道防线 + 失败语义。
// ⚠️ 它**不**验证 Cloudflare 平台的真实行为（路由/证书/绑定/限流的分布式语义）——
//    那些只能在部署后用手验。这里钉的是 Worker **自己的逻辑**。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(here, "src/index.js"), "utf8");
const worker = (await import("data:text/javascript;base64," +
  Buffer.from(src).toString("base64"))).default;

const HASH = "dd8058edf50106c0";
const P = (name) => `/v1/objects/${HASH}/${name}`;

let puts = [];
let rlSuccess = true;
const env = {
  INGEST_TOKEN: "tok-abc",
  BUCKET: { put: async (key, body, opts) => { puts.push({ key, opts }); } },
  INGEST_LIMITER: { limit: async () => ({ success: rlSuccess }) },
};

function req(path, { method = "PUT", token = "Bearer tok-abc", bytes = null } = {}) {
  const headers = { "Content-Type": "application/octet-stream" };
  if (token !== null) headers["Authorization"] = token;
  return new Request(`https://ingest.bldcam.page${path}`, { method, headers, body: bytes });
}

const cases = [];
const chk = (name, got, want) => cases.push([name, got === want, `${got} (want ${want})`]);
const status = async (r, envx = env) => (await worker.fetch(r, envx)).status;

chk("GET -> 405", await status(req(P("report.json"), { method: "GET" })), 405);
chk("错 token -> 401", await status(req(P("report.json"), { token: "Bearer nope", bytes: new Uint8Array(5) })), 401);
// ⚠️ fail-closed：secret 没配时不许因字面量 "Bearer undefined" 放行
chk("secret 未配 + `Bearer undefined` -> 401",
  await status(req(P("report.json"), { token: "Bearer undefined", bytes: new Uint8Array(5) }),
               { ...env, INGEST_TOKEN: undefined }), 401);
chk("无 Authorization -> 401", await status(req(P("report.json"), { token: null, bytes: new Uint8Array(5) })), 401);
// 路径白名单
chk("目录穿越 -> 400", await status(req(`/v1/objects/${HASH}/../../etc/passwd`, { bytes: new Uint8Array(5) })), 400);
chk("乱名 -> 400", await status(req(P("evil.sh"), { bytes: new Uint8Array(5) })), 400);
chk("大写 hex -> 400", await status(req(`/v1/objects/DD8058EDF50106C0/report.json`, { bytes: new Uint8Array(5) })), 400);
// 体积：看**实际字节数**（不信任 Content-Length）
chk("33MB body -> 413", await status(req(P("report.json"), { bytes: new Uint8Array(33 * 1024 * 1024) })), 413);
chk("空 body -> 400", await status(req(P("report.json"), { bytes: new Uint8Array(0) })), 400);
// 限流
rlSuccess = false;
chk("限流 -> 429", await status(req(P("report.json"), { bytes: new Uint8Array(5) })), 429);
rlSuccess = true;
// 正例
puts = [];
chk("合法 -> 200", await status(req(P("report.json"), { bytes: new Uint8Array(10) })), 200);
chk("写进 classlive-test/<hash>/report.json",
  puts.length === 1 && puts[0].key === `classlive-test/${HASH}/report.json`, true);
// wav（崩溃补传）+ meta 也放行
puts = [];
await worker.fetch(req(P("audio.007.wav"), { bytes: new Uint8Array(5) }), env);
await worker.fetch(req(P("meta.json"), { bytes: new Uint8Array(5) }), env);
chk("audio.NNN.wav 与 meta.json 放行",
  puts.length === 2 && puts[0].key.endsWith("/audio.007.wav") && puts[1].key.endsWith("/meta.json"), true);
// R2 写失败 -> 5xx（不能骗客户端 200）
chk("R2 写失败 -> 502",
  await status(req(P("report.json"), { bytes: new Uint8Array(5) }),
               { ...env, BUCKET: { put: async () => { throw new Error("quota"); } } }), 502);

// ⚠️⚠️ 体积闸门**不该先读再判**（2026-10-12 全仓审查核实）：token 是公开的，
//    一个 chunked / 谎报 Content-Length 的大流会先把整个 body 读进 Worker 内存
//    （平台上限 100MB）才轮到那个"太大了"的判断 —— 注释里「缓冲开销可接受」的
//    前提在攻击者手里不成立。下面两条钉住的正是"**还没读就该拒**"。
let reads = 0;
// ⚠️ `highWaterMark: 0` —— 否则 ReadableStream 构造后**自己会预热读一次**，
//    于是"一个字节都没读"这条判据量到的是流的行为、不是 handler 的行为（第一版就这么假红了）。
const mkStream = (chunkKb = 1024, maxChunks = 64) => new ReadableStream({
  pull(c) {
    if (reads >= maxChunks) return c.close();
    reads++;
    c.enqueue(new Uint8Array(chunkKb * 1024));
  },
}, { highWaterMark: 0 });
// ⚠️ 这里**故意**不扩展上面的 `req()`：那几条判据要数"handler 到底读了几块"，
//    需要一个能被计数的 `body` 流 + 一个原始的 `headers.get`（大小写敏感地喂
//    `Content-Length`）—— `new Request(...)` 会规范化头部、也不给读计数。
const stub = (headers, body) => {
  // ⚠️ 大小写不敏感 —— 源码里读的是 `Authorization` / `Content-Length`，
  //    而这里给的键是小写（第一版没归一化，三条判据全被 401 挡住了）。
  const h = {};
  for (const [k, v] of Object.entries(headers)) h[k.toLowerCase()] = v;
  return {
    method: "PUT",
    url: `https://ingest.bldcam.page${P("report.json")}`,
    headers: { get: (k) => h[String(k).toLowerCase()] ?? null },
    body,
  };
};

reads = 0;
chk("⭐ 声明 Content-Length 超限 -> 413",
  await status(stub({ authorization: "Bearer tok-abc", "content-length": String(64 * 1024 * 1024) },
                    mkStream())), 413);
chk("⭐⭐ 而且**一个字节都没读**（声明就够拒绝了）", reads, 0);

reads = 0;
chk("⭐⭐ chunked 无声明、超限 -> 413",
  await status(stub({ authorization: "Bearer tok-abc" }, mkStream())), 413);
chk(`⭐⭐ 而且是**边读边卡**（读了 ${reads} 块就停，不是把 64 块读完）`, reads <= 35, true);

reads = 0;
chk("⭐ 正常大小（1MB）照常放行",
  await status(stub({ authorization: "Bearer tok-abc" }, mkStream(1024, 1))), 200);

const bad = cases.filter(c => !c[1]);
for (const [n, ok, d] of cases) console.log(`  ${ok ? "✅" : "❌"} ${n}  ${d}`);
console.log(`\n${cases.length - bad.length}/${cases.length} 通过`);
process.exit(bad.length ? 1 : 0);
