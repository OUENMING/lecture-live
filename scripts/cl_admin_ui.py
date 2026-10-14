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
