#!/usr/bin/env python3
"""通行码管理面板 —— 本机网页（`docs/PLAN-passkey.md` §3 扩展功能 1）。

    ClassLive.app/Contents/MacOS/python scripts/cl_admin_ui.py            # → http://127.0.0.1:8770
    ClassLive.app/Contents/MacOS/python scripts/cl_admin_ui.py --port 8800 --open

为什么是**本机服务**而不是挂在 Worker 上：
  · Worker **没有任何 CORS 头**（全仓 grep 零命中），浏览器直接打 `/v1/admin/*` 会被拦；
  · 挂上去就得把管理令牌存进浏览器（localStorage），并把管理面暴露在公开域名上。
本服务**只 bind 127.0.0.1**；令牌只在 Python 侧读、只在服务端加进请求头 —— **从不进浏览器、从不进 HTML**。

⚠️ 三件事：
  1. 页面上的数字全部来自真的 `asr.bldcam.page`（只读的 list/stats/quota，加你点的管理操作）。
  2. 「估算花费」是**本机乘出来的，不是账**。价目表可改，页面上标了价格日期。
  3. 面板**只记数字**（`PLAN-passkey.md` §4）：看不到任何课堂文本、音频，也看不到完整码除非你展开。

判据在 `tests/test_cl_admin_ui.py`（只测转发层与花费计算，用本机假上游，不联网）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import cl_admin                                                      # noqa: E402

#: 只放行这几个名字 —— 别把 `/api/<任意>` 直接拼进 Worker 的 URL。
ALLOWED = {"add", "list", "stats", "revoke", "unrevoke", "renew", "limit", "quota", "delete"}

#: 自定义头：逼浏览器先发预检。我们**不回** CORS 头，所以预检必失败、真请求不会发出去。
#: 没有这一条，任意网站都能用 `<form>` / `fetch` 打 http://127.0.0.1:8770/api/revoke 把你的码吊销掉。
GATE_HEADER = "X-ClassLive-Admin"

#: 第二道闸：**Host 头必须是环回**。挡的是 DNS rebinding —— 恶意网站把自己的域名临时解析到
#: 127.0.0.1，页面就成了**同源**，自定义头不再触发预检，上面那道闸就形同虚设。
#: 浏览器发的 Host 是**那个域名**（改不了），所以只认 127.0.0.1 / localhost。
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")

PAGE = pathlib.Path(__file__).with_name("cl_admin_ui.html")

#: Cloudflare 自己的数（`/api/cf`）：**本机**用只读 Analytics token 打 GraphQL，不经 Worker。
#: token 读 `~/.classlive/cf-analytics-token`（600），账号读 `~/.classlive/cf-account-id`；**从不进响应**。
#: 只回这几个名字开头的脚本 / 桶 —— 同一个账号里还有别的项目（photosave 桶），不该出现在这张面板上。
CF_PREFIX = "classlive-"
CF_GQL = "https://api.cloudflare.com/client/v4/graphql"
_CF_Q = """query($a:String!,$s:Time!,$u:Time!,$ds:Date!,$du:Date!){viewer{accounts(filter:{accountTag:$a}){
 day:workersInvocationsAdaptive(limit:100,filter:{datetime_geq:$s,datetime_leq:$u}){
  sum{requests errors} quantiles{cpuTimeP50 cpuTimeP99} dimensions{scriptName status}}
 week:workersInvocationsAdaptive(limit:500,filter:{date_geq:$ds,date_leq:$du}){
  sum{requests errors} dimensions{scriptName date}}
 r2:r2StorageAdaptiveGroups(limit:100,filter:{date_geq:$ds,date_leq:$du}){
  max{objectCount payloadSize} dimensions{bucketName date}}
 dos:durableObjectsInvocationsAdaptiveGroups(limit:20,filter:{date_geq:$ds,date_leq:$du}){
  sum{requests} dimensions{date}}}}}"""


def cf_summary(post=None, now=None) -> dict:
    """近 24 h 的 Worker 健康 + 近 7 天的请求 / R2 / DO。**只回数字和 classlive-* 的名字**。

    `post(url, headers, body) -> dict` 测试注入；默认 httpx。拿不到 token 回 `{"error": ...}`（面板照常，只是这张卡说明原因）。
    """
    import datetime as dt
    home = pathlib.Path.home() / ".classlive"
    try:
        tok = (home / "cf-analytics-token").read_text(encoding="utf-8").strip()
        acct = (home / "cf-account-id").read_text(encoding="utf-8").strip()
    except OSError:
        return {"error": "没配 Cloudflare 只读 token（~/.classlive/cf-analytics-token，权限 Account Analytics: Read）"}
    if not tok or not acct:
        return {"error": "Cloudflare token / 账号 ID 是空的"}
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    v = {"a": acct, "s": (now - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "u": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
         "ds": str((now - dt.timedelta(days=6)).date()), "du": str(now.date())}
    if post is None:
        def post(url, headers, body):
            import httpx
            return httpx.post(url, headers=headers, json=body, timeout=20).json()
    try:
        j = post(CF_GQL, {"Authorization": f"Bearer {tok}"}, {"query": _CF_Q, "variables": v})
    except Exception as e:                                            # noqa: BLE001
        return {"error": f"连不上 Cloudflare：{type(e).__name__}"}
    if j.get("errors"):
        return {"error": "Cloudflare 拒了：" + "；".join(str(x.get("message", ""))[:120] for x in j["errors"][:2])}
    try:
        a = j["data"]["viewer"]["accounts"][0]
    except (KeyError, IndexError, TypeError):
        return {"error": "Cloudflare 回的数据形状不认识"}
    ok = lambda name: str(name or "").startswith(CF_PREFIX)          # noqa: E731
    scripts: dict = {}
    for r in a.get("day") or []:
        d = r.get("dimensions", {})
        if not ok(d.get("scriptName")):
            continue
        sc = scripts.setdefault(d["scriptName"], {"requests": 0, "errors": 0, "status": {}, "cpu_p50_ms": 0.0, "cpu_p99_ms": 0.0})
        n = int(r["sum"]["requests"])
        sc["requests"] += n
        sc["errors"] += int(r["sum"]["errors"])
        sc["status"][d.get("status") or "?"] = sc["status"].get(d.get("status") or "?", 0) + n
        if d.get("status") == "success":                              # CPU 只看成功的那一组（微秒 → 毫秒）
            sc["cpu_p50_ms"] = round(r["quantiles"]["cpuTimeP50"] / 1000.0, 2)
            sc["cpu_p99_ms"] = round(r["quantiles"]["cpuTimeP99"] / 1000.0, 2)
    week: dict = {}
    for r in a.get("week") or []:
        d = r.get("dimensions", {})
        if ok(d.get("scriptName")):
            row = week.setdefault(d["date"], {})
            row[d["scriptName"]] = row.get(d["scriptName"], 0) + int(r["sum"]["requests"])
    r2: dict = {}
    for r in a.get("r2") or []:
        d = r.get("dimensions", {})
        if ok(d.get("bucketName")):
            r2.setdefault(d["bucketName"], {})[d["date"]] = {
                "objects": int(r["max"]["objectCount"]), "bytes": int(r["max"]["payloadSize"])}
    dos = {r["dimensions"]["date"]: int(r["sum"]["requests"]) for r in a.get("dos") or []}
    return {"since": v["s"], "until": v["u"], "scripts": scripts, "week": week, "r2": r2, "dos": dos}


class Handler(BaseHTTPRequestHandler):
    server_version = "ClassLiveAdminUI"

    def log_message(self, fmt, *a):                                   # 别刷屏；只留错误
        if not str(a[1] if len(a) > 1 else "").startswith("2"):
            sys.stderr.write("  %s\n" % (fmt % a))

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return (self.headers.get("Host") or "") in {f"{h}:{port}" for h in LOOPBACK_HOSTS}

    def do_GET(self):                                                 # noqa: N802
        if not self._host_ok():
            return self._json(403, {"error": "Host 不是本机环回（防 DNS rebinding）"})
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            except OSError as e:
                self._json(500, {"error": f"读不到模板 {PAGE.name}：{e}"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):                                                # noqa: N802
        if not self._host_ok():
            return self._json(403, {"error": "Host 不是本机环回（防 DNS rebinding）"})
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        if self.headers.get(GATE_HEADER) != "1":
            return self._json(403, {"error": "缺少 %s 头（本机页面对任意网站的第一道闸）" % GATE_HEADER})
        name = path[len("/api/"):]
        if name == "cf":                                              # 本机直接问 Cloudflare，不经 Worker
            return self._json(200, cf_summary())
        if name not in ALLOWED:
            return self._json(404, {"error": f"不认这个操作：{name}"})
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._json(400, {"error": "body 不是 JSON"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "body 要是对象"})
        try:
            return self._json(200, cl_admin.admin_post(name, body))
        except cl_admin.AdminError as e:
            return self._json(502, {"error": str(e)})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="通行码管理面板（只在本机环回上开）")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--open", action="store_true", help="起来之后用默认浏览器打开")
    a = ap.parse_args(argv)
    if not PAGE.exists():
        sys.exit(f"缺模板 {PAGE}（和本脚本放同一个目录）")
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)         # ⚠️ 只 bind 环回
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"通行码管理面板：{url}\n  令牌只在服务端读（{cl_admin.paths.asr_admin_token()}），不进浏览器。Ctrl-C 停。")
    if a.open:
        import subprocess
        subprocess.run(["open", url], check=False, timeout=10)        # 同 notice.py 的既有写法
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
