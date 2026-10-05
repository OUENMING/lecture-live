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

const bad = cases.filter(c => !c[1]);
for (const [n, ok, d] of cases) console.log(`  ${ok ? "✅" : "❌"} ${n}  ${d}`);
console.log(`\n${cases.length - bad.length}/${cases.length} 通过`);
process.exit(bad.length ? 1 : 0);
