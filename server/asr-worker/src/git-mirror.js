// ⭐ 中国区更新镜像（2026-11-18）：把**这一个公开仓库**的 git 只读协议（smart HTTP v0/v2）转发给 GitHub。
//
// 为什么要有：中国大陆的朋友 `git fetch github.com` 时通时不通（10-05 / 10-29 / 11-12 / 11-17 四次
// `Could not resolve host: github.com`，11-18 开课那几分钟同样 DNS 不通），而同一台机器连 asr.bldcam.page
// （本 Worker）30 天里每节课都通（AE：country=CN、colo=HKG、每节课几千次、错误 ≈ 0）。
// 所以让客户端在 GitHub 那条失败后改走这里（`update.py` 的 `mirror` 路由：`git -c url.<这里>.insteadOf=<GitHub>`），
// 由 Cloudflare 的香港节点替他去 GitHub 拿 —— 内容就是 GitHub 那份，没有第二份要同步。
//
// ⚠️ 只读、只此一仓：只放行 `GET …/info/refs?service=git-upload-pack` 与 `POST …/git-upload-pack`。
//    `git-receive-pack`（push）一律 404 —— 这里永远不该成为能写仓库的入口。
// ⚠️ 不要认证：仓库本来就是公开的（MIT），凭据反而会出现在客户端的命令行里。防滥用靠按 IP 限流。
// ⚠️ 不改内容：git 自己用对象哈希校验每一个对象，转发的人改不了代码而不被发现。

export const GIT_PREFIX = "/git/OUENMING/";
// `lecture-live.git` 与 `lecture-live` 两种 remote 写法都认（GitHub 本身也两种都认）。
const REPO_RE = /^\/git\/OUENMING\/lecture-live(\.git)?(\/info\/refs|\/git-upload-pack)$/;
export const UPSTREAM = "https://github.com/OUENMING/lecture-live.git";

// 往上游带的请求头：只带 git 协议需要的那几个（不带 cookie / authorization / cf-* 之类）。
const FWD_REQ = ["accept", "content-type", "content-encoding", "git-protocol", "user-agent"];
// 回给客户端的响应头：git 只看 content-type；缓存头照抄（GitHub 对 info/refs 写 no-cache）。
const FWD_RES = ["content-type", "cache-control", "expires", "pragma"];

export function isGitPath(path) {
  return path.startsWith(GIT_PREFIX);
}

// 这条请求能不能转、转到哪。纯函数（单测用）：返回 `{ target, kind }` 或 `null`（= 404）。
export function gitTarget(method, url) {
  const m = REPO_RE.exec(url.pathname);
  if (!m) return null;
  if (m[2] === "/info/refs") {
    if (method !== "GET" || url.searchParams.get("service") !== "git-upload-pack") return null;
    return { target: `${UPSTREAM}/info/refs?service=git-upload-pack`, kind: "refs" };
  }
  if (method !== "POST") return null;
  return { target: `${UPSTREAM}/git-upload-pack`, kind: "pack" };
}

// 转发。`fetchImpl` 测试注入。返回 Response；`ev`（可选）记 path / status 供 AE 统计。
export async function gitMirror(request, env, ev, fetchImpl = fetch) {
  const url = new URL(request.url);
  const t = gitTarget(request.method, url);
  if (ev) ev.path = t ? `/git/${t.kind}` : "/git/denied";
  if (!t) return new Response("not found", { status: 404 });

  const ip = request.headers.get("cf-connecting-ip") || "0.0.0.0";
  if (env.PER_IP && !(await env.PER_IP.limit({ key: ip })).success) {
    return new Response("rate limited", { status: 429, headers: { "retry-after": "60" } });
  }

  const headers = new Headers();
  for (const h of FWD_REQ) {
    const v = request.headers.get(h);
    if (v) headers.set(h, v);
  }
  let up;
  try {
    up = await fetchImpl(t.target, {
      method: request.method,
      headers,
      body: t.kind === "pack" ? request.body : undefined,
      redirect: "follow",
    });
  } catch (e) {
    console.log(JSON.stringify({ git: "upstream_threw", kind: t.kind, msg: String(e).slice(0, 120) }));
    return new Response("upstream unavailable", { status: 502 });
  }
  const out = new Headers();
  for (const h of FWD_RES) {
    const v = up.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(up.body, { status: up.status, headers: out });
}
