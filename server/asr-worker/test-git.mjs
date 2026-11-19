// 中国区更新镜像（src/git-mirror.js）的判据 —— 假 fetch，不连外网。
//
//   cd server/asr-worker && node --test test-git.mjs
//
// 钉住：只读（receive-pack 404）、只此一仓、两种 remote 写法、转发的头只带 git 需要的、
//       上游异常 → 502、按 IP 限流、经 worker.fetch 入口不需要任何认证。
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "./src/handler.js";
import { gitMirror, gitTarget } from "./src/git-mirror.js";

const BASE = "https://asr.bldcam.page";
const U = (p) => new URL(BASE + p);

test("只放行 upload-pack 的两个端点", () => {
  assert.deepEqual(gitTarget("GET", U("/git/OUENMING/lecture-live.git/info/refs?service=git-upload-pack")),
    { target: "https://github.com/OUENMING/lecture-live.git/info/refs?service=git-upload-pack", kind: "refs" });
  assert.deepEqual(gitTarget("POST", U("/git/OUENMING/lecture-live.git/git-upload-pack")),
    { target: "https://github.com/OUENMING/lecture-live.git/git-upload-pack", kind: "pack" });
  // 不带 .git 的 remote 写法也认
  assert.equal(gitTarget("POST", U("/git/OUENMING/lecture-live/git-upload-pack")).kind, "pack");
});

test("push / 别的仓 / 别的方法一律拒", () => {
  assert.equal(gitTarget("GET", U("/git/OUENMING/lecture-live.git/info/refs?service=git-receive-pack")), null);
  assert.equal(gitTarget("POST", U("/git/OUENMING/lecture-live.git/git-receive-pack")), null);
  assert.equal(gitTarget("GET", U("/git/OUENMING/other.git/info/refs?service=git-upload-pack")), null);
  assert.equal(gitTarget("GET", U("/git/OUENMING/lecture-live.git/git-upload-pack")), null);
  assert.equal(gitTarget("POST", U("/git/OUENMING/lecture-live.git/info/refs?service=git-upload-pack")), null);
  assert.equal(gitTarget("GET", U("/git/OUENMING/lecture-live.git/../x/info/refs?service=git-upload-pack")), null);
  assert.equal(gitTarget("GET", U("/git/OUENMING/lecture-live.git/HEAD")), null);
});

test("转发：只带 git 的头、body 原样、响应头只回 content-type 等", async () => {
  let seen;
  const fake = async (url, init) => {
    seen = { url, init };
    return new Response("PACK", { status: 200, headers: {
      "content-type": "application/x-git-upload-pack-result", "set-cookie": "x=1", "cache-control": "no-cache" } });
  };
  const req = new Request(BASE + "/git/OUENMING/lecture-live.git/git-upload-pack", {
    method: "POST", body: "0032want abc\n",
    headers: { "content-type": "application/x-git-upload-pack-request", "git-protocol": "version=2",
               cookie: "secret", authorization: "Bearer cl-x-0123456789abcdef", "cf-connecting-ip": "1.2.3.4" },
  });
  const ev = {};
  const res = await gitMirror(req, {}, ev, fake);
  assert.equal(res.status, 200);
  assert.equal(await res.text(), "PACK");
  assert.equal(seen.url, "https://github.com/OUENMING/lecture-live.git/git-upload-pack");
  assert.equal(seen.init.headers.get("git-protocol"), "version=2");
  assert.equal(seen.init.headers.get("cookie"), null);
  assert.equal(seen.init.headers.get("authorization"), null);
  assert.equal(seen.init.headers.get("cf-connecting-ip"), null);
  assert.equal(res.headers.get("set-cookie"), null);
  assert.equal(res.headers.get("content-type"), "application/x-git-upload-pack-result");
  assert.equal(ev.path, "/git/pack");
});

test("上游抛异常 → 502；被限流 → 429（不碰上游）", async () => {
  const req = () => new Request(BASE + "/git/OUENMING/lecture-live.git/info/refs?service=git-upload-pack");
  const boom = async () => { throw new Error("dns"); };
  assert.equal((await gitMirror(req(), {}, null, boom)).status, 502);
  let called = false;
  const env = { PER_IP: { limit: async () => ({ success: false }) } };
  const res = await gitMirror(req(), env, null, async () => { called = true; return new Response(""); });
  assert.equal(res.status, 429);
  assert.equal(called, false);
});

test("经 worker.fetch 入口：不需要认证；receive-pack 404", async () => {
  const orig = globalThis.fetch;
  globalThis.fetch = async () => new Response("001e# service=git-upload-pack\n", {
    status: 200, headers: { "content-type": "application/x-git-upload-pack-advertisement" } });
  try {
    const ok = await worker.fetch(new Request(BASE + "/git/OUENMING/lecture-live.git/info/refs?service=git-upload-pack"), {}, {});
    assert.equal(ok.status, 200);
    assert.equal(ok.headers.get("content-type"), "application/x-git-upload-pack-advertisement");
    const no = await worker.fetch(new Request(BASE + "/git/OUENMING/lecture-live.git/info/refs?service=git-receive-pack"), {}, {});
    assert.equal(no.status, 404);
  } finally {
    globalThis.fetch = orig;
  }
});
