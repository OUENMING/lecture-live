#!/usr/bin/env python3
"""`scripts/cl_admin_ui.py` 的判据 —— 本机管理面板的**转发层**与**花费算式**。

    ClassLive.app/Contents/MacOS/python tests/test_cl_admin_ui.py

## 钉住了什么

 1. `GET /` 给模板；**响应里没有管理令牌**（R29 管的同一件事：令牌不许落到会被读走的地方）
 2. 缺 `X-ClassLive-Admin` 头 → 403，**且一个字节都没转给 Worker**
    —— 这一条防的是「任意网站用 `<form>` 打 127.0.0.1:8770/api/revoke 把你的码吊销掉」
 3. 白名单外的名字（含 `../`）→ 404，同样不转发
 4. 转发：路径拼成 `/admin/<name>`、头带 `Bearer <令牌>`、body 原样进、响应原样出
 5. 上游 401 / `{"error":…}` → 502 + 人话（不是 httpx 的原始 traceback）
 6. body 不是 JSON / 不是对象 → 400
 7. 第 1–6 条的**每一个**响应体里都不含令牌
 8. 花费算式 `cluiCost`：从 HTML 里抠**真的那段源码**喂给 node 跑（不是手抄一份），
    含「缓存命中数 > 输入数」的钳位，和一个真实样本（owen 2026-10-13）与手算对拍
 9. 页面**零外链**（没有 `http(s)://` 的 src/href/@import）—— 断网也得能用

⚠️ 本机 `http.server` 当 Worker，**不联网**；HOME 指向临时目录，读端写端都隔离，不碰真 `~/.classlive/`。
⚠️ 第 8 条要 `node`（装了就有：`/opt/homebrew/bin/node`）。跑不了会**大声跳过**，不静默。
"""
from __future__ import annotations

import http.server
import inspect
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scripts"))          # cl_admin / cl_admin_ui 住在 scripts/

_TMP_HOME = tempfile.mkdtemp(prefix="cl-admin-ui-test-")
os.environ["HOME"] = _TMP_HOME                    # 必须在 import paths 之前 —— STATE_ROOT 是 import 时算的
os.environ.pop("CLASSLIVE_ASR_ENDPOINT", None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

import cl_admin                                                       # noqa: E402
import cl_admin_ui                                                    # noqa: E402
import paths                                                          # noqa: E402

TOKEN = "tok-for-tests-only-not-real-0123456789abcdef"   # ⚠️ 必须 ASCII：HTTP 头不能带非 ASCII
HTML = (HERE / "scripts" / "cl_admin_ui.html").read_text(encoding="utf-8")

# 令牌文件落在**临时** HOME 里。⚠️ 这一条本身就是判据：隔离没生效就拒绝往下跑，
# 免得哪天真去写 ~/.classlive/asr-admin-token。
_TOK = paths.asr_admin_token()
if not str(_TOK).startswith(_TMP_HOME):
    raise SystemExit(f"⚠️ HOME 隔离没生效，拒绝往下跑（令牌会写到 {_TOK}）")
_TOK.parent.mkdir(parents=True, exist_ok=True)
_TOK.write_text(TOKEN)


class FakeWorker:
    """本机 Worker 替身：记录每一次请求，状态码/响应体可切。"""

    def __init__(self):
        self.reqs: list[dict] = []
        self.mode = "ok"
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):                                        # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                outer.reqs.append({"path": self.path, "auth": self.headers.get("Authorization"), "raw": raw})
                code, body = {
                    "ok": (200, {"ok": True, "seen": self.path}),
                    "401": (401, {"error": "bad token"}),
                    "notfound": (404, {"error": "not found"}),
                }[outer.mode]
                out = json.dumps(body).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def reset(self):
        self.reqs.clear()
        self.mode = "ok"


class PanelCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = FakeWorker()
        # 落点改成假 Worker —— `asr_endpoint.endpoint()` 认这个环境变量
        os.environ["CLASSLIVE_ASR_ENDPOINT"] = f"http://127.0.0.1:{cls.worker.port}/v1/transcribe"
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), cl_admin_ui.Handler)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        for s in (cls.srv, cls.worker.httpd):
            s.shutdown()
            s.server_close()
        os.environ.pop("CLASSLIVE_ASR_ENDPOINT", None)

    def setUp(self):
        self.worker.reset()

    # ---- 客户端小工具 ----
    def post(self, path, body=b"{}", gate=True, raw_body=None):
        import httpx
        h = {"content-type": "application/json"}
        if gate:
            h[cl_admin_ui.GATE_HEADER] = "1"
        payload = raw_body if raw_body is not None else body
        r = httpx.post(self.base + path, content=payload, headers=h, timeout=10)
        return r.status_code, r.text

    # ---------------- 1 ----------------
    def test_首页给模板_且不含令牌(self):
        import httpx
        r = httpx.get(self.base + "/", timeout=10)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("通行码管理", r.text)
        self.assertNotIn(TOKEN, r.text, "令牌漏进页面了")
        self.assertNotIn("Authorization", r.text)
        self.assertNotIn("Bearer", r.text)

    def test_别的_GET_路径是_404(self):
        import httpx
        self.assertEqual(httpx.get(self.base + "/api/list", timeout=10).status_code, 404)

    # ---------------- 2 ----------------
    def test_没有闸门头就403且不转发(self):
        code, txt = self.post("/api/list", gate=False)
        self.assertEqual(code, 403)
        self.assertIn(cl_admin_ui.GATE_HEADER, txt)
        self.assertEqual(self.worker.reqs, [], "闸门没拦住：请求已经打到 Worker 了")

    def test_闸门头值不对也403(self):
        import httpx
        r = httpx.post(self.base + "/api/list", content=b"{}",
                       headers={cl_admin_ui.GATE_HEADER: "0"}, timeout=10)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.worker.reqs, [])

    def test_Host不是环回就403且不转发(self):
        """DNS rebinding：攻击者域名解析到 127.0.0.1 后页面同源、闸门头照发 —— 只有 Host 头露馅。"""
        import httpx
        port = self.srv.server_address[1]
        for host in ("evil.example", f"evil.example:{port}", f"127.0.0.1.evil.example:{port}", "127.0.0.1"):
            r = httpx.post(self.base + "/api/list", content=b"{}",
                           headers={cl_admin_ui.GATE_HEADER: "1", "Host": host}, timeout=10)
            self.assertEqual(r.status_code, 403, host)
            self.assertEqual(httpx.get(self.base + "/", headers={"Host": host}, timeout=10).status_code, 403, host)
        self.assertEqual(self.worker.reqs, [], "rebinding 的请求漏到 Worker 了")
        for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            r = httpx.post(self.base + "/api/list", content=b"{}",
                           headers={cl_admin_ui.GATE_HEADER: "1", "Host": host}, timeout=10)
            self.assertEqual(r.status_code, 200, host)

    # ---------------- 3 ----------------
    def test_白名单外的名字不转发(self):
        # ⚠️ 不在名单里的：`/api/list?x=1` 会 200（查询串按设计丢掉），所以它不在这里
        # ⚠️ 原来这里拿 "/api/delete" 当反例 —— 2026-10-14 它进了白名单，判据当场变红（这是对的）。
        for bad in ("/api/destroy", "/api/", "/api/list2", "/api/LIST", "/api/%2e%2e/revoke", "/api/list%00"):
            code, txt = self.post(bad)
            self.assertEqual(code, 404, f"{bad} 竟然没被拦")
            self.assertNotIn(TOKEN, txt)
        self.assertEqual(self.worker.reqs, [], "有请求漏出去了")

    def test_转发出去的永远落在_v1_admin_下面(self):
        """⚠️ `/api/list/../revoke` **不是**绕过：httpx（和浏览器一样）先做点段归一化，
        到服务端手里已经是 `/api/revoke` —— 那是个合法名字，行为正确。
        要钉的是不变量：**转出去的路径永远在 `/v1/admin/` 下、名字永远在白名单里**。"""
        for weird in ("/api/list/../revoke", "/api/../api/list", "/api/./list",
                      "/api/list/../../../v1/admin/revoke", "/api/renew/../add"):
            self.worker.reset()
            self.post(weird)
            for req in self.worker.reqs:
                self.assertTrue(req["path"].startswith("/v1/admin/"), f"{weird} → {req['path']}")
                name = req["path"][len("/v1/admin/"):]
                self.assertIn(name, cl_admin_ui.ALLOWED, f"{weird} → 白名单外的 {name}")

    def test_查询串按设计丢掉(self):
        """`/api/list?x=1` → 打的是 `/v1/admin/list`。这是**有意**的（名字只从 path 取）。"""
        code, _ = self.post("/api/list?x=1")
        self.assertEqual(code, 200)
        self.assertEqual(self.worker.reqs[0]["path"], "/v1/admin/list")

    def test_白名单是全的(self):
        # 页面真的会打这几个名字；少一个就是点了没反应
        for name in ("add", "list", "stats", "revoke", "unrevoke", "renew", "limit", "quota", "delete"):
            self.assertIn(name, cl_admin_ui.ALLOWED)

    # ---------------- 4 ----------------
    def test_转发带令牌且原样回(self):
        code, txt = self.post("/api/list", b'{"day":null}')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(txt), {"ok": True, "seen": "/v1/admin/list"})
        self.assertEqual(len(self.worker.reqs), 1)
        got = self.worker.reqs[0]
        self.assertEqual(got["path"], "/v1/admin/list", "落点该是 Worker 的 /v1/admin/*（base 里带着 /v1）")
        self.assertEqual(got["auth"], f"Bearer {TOKEN}")
        self.assertEqual(got["raw"], b'{"day":null}', "body 没原样带过去")
        self.assertNotIn(TOKEN, txt, "响应里不该有令牌")

    def test_转发到_quota_走的是同一个口(self):
        self.post("/api/quota", b"{}")
        self.assertEqual(self.worker.reqs[0]["path"], "/v1/admin/quota")

    # ---------------- 5 ----------------
    def test_上游401翻成502人话(self):
        self.worker.mode = "401"
        code, txt = self.post("/api/list")
        self.assertEqual(code, 502)
        self.assertIn("令牌不对", txt)
        self.assertNotIn("Traceback", txt)
        self.assertNotIn(TOKEN, txt)

    def test_上游错误体先于状态码(self):
        # Worker 的未知路径是 404 + {"error":"not found"}：要拿到那句人话，不是 HTTP 404
        self.worker.mode = "notfound"
        code, txt = self.post("/api/list")
        self.assertEqual(code, 502)
        self.assertEqual(json.loads(txt)["error"], "not found")

    # ---------------- 6 ----------------
    def test_body不是JSON是400(self):
        code, txt = self.post("/api/list", raw_body=b"{not json")
        self.assertEqual(code, 400)
        self.assertEqual(self.worker.reqs, [])

    def test_body不是对象是400(self):
        code, _ = self.post("/api/list", raw_body=b'["a"]')
        self.assertEqual(code, 400)
        self.assertEqual(self.worker.reqs, [])

    # ---------------- 7 ----------------
    def test_所有响应体里都没有令牌(self):
        bodies = [self.post("/api/list", gate=False)[1],
                  self.post("/api/nope")[1],
                  self.post("/api/list")[1]]
        self.worker.mode = "401"
        bodies.append(self.post("/api/list")[1])
        for b in bodies:
            self.assertNotIn(TOKEN, b)
            self.assertNotIn("Bearer", b)

    # ---------------- 9 ----------------
    def test_页面零外链(self):
        for pat in (r'src\s*=\s*["\']https?://', r'href\s*=\s*["\']https?://', r'@import', r'url\(\s*["\']?https?://'):
            self.assertIsNone(re.search(pat, HTML), f"页面里有外链：{pat}")

    def test_只bind环回(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")
        self.assertIn("127.0.0.1", inspect.getsource(cl_admin_ui.main))


# ---------------- 8：花费算式（跑的是 HTML 里那段真源码） ----------------
S_START = "/* >>>CLUI-COST-START<<<"
S_END = "/* >>>CLUI-COST-END<<<"
PRICES = {"cache": 0.003, "miss": 0.15, "out": 0.60}

# (使用量, 期望美元)。最后一条是真实样本：owen 2026-10-13（面板上显示 $0.168）。
CASES = [
    ({}, 0.0),
    ({"tokens_in": 1_000_000}, 0.15),
    ({"tokens_out": 1_000_000}, 0.60),
    ({"tokens_in": 1_000_000, "cache_hit": 1_000_000}, 0.003),
    ({"tokens_in": 1_000_000, "cache_hit": 400_000}, 0.6 * 0.15 + 0.4 * 0.003),
    ({"tokens_in": 500_000, "cache_hit": 1_000_000}, 0.0015),        # 命中数 > 输入数 → 按全命中钳
    ({"tokens_in": 1_330_347, "cache_hit": 610_432, "tokens_out": 97_396}, 0.168256146),
]


def _cost_src() -> str:
    """从 HTML 里抠出 `cluiCost` 那段源码。抠不着 / 抠歪了要吵，不然判据是空的。"""
    i = HTML.find(S_START)
    j = HTML.find(S_END, i + 1)
    if i < 0 or j < 0:
        raise AssertionError("HTML 里找不到 CLUI-COST 哨兵（判据会变成空的）")
    src = HTML[i:j]
    src = src[src.index("\n"):]                                      # 去掉哨兵那一行
    if "function cluiCost" not in src or len(src) > 900:
        raise AssertionError(f"抠出来的不是那段函数（{len(src)} 字节）")
    return src


class CostCase(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "没有 node —— 算式住在 HTML 里，Python 测不到")
    def test_花费算式与手算对拍(self):
        js = (_cost_src()
              + f"\nconst P = {json.dumps(PRICES)};"
              + f"\nconst C = {json.dumps([u for u, _ in CASES])};"
              + "\nprocess.stdout.write(JSON.stringify(C.map((u) => cluiCost(u, P))));")
        p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, f"node 报错：{p.stderr[:400]}")
        got = json.loads(p.stdout)
        for (u, want), g in zip(CASES, got):
            self.assertAlmostEqual(g, want, places=7, msg=f"{u} → {g}，手算是 {want}")

    @unittest.skipUnless(shutil.which("node"), "没有 node —— 整段页面脚本过不了语法检查")
    def test_整段页面脚本语法是对的(self):
        """页面里那 400 多行 JS 在 Python 这边是黑洞：语法错一个字符，页面**整片空白**且没有报错。
        把 `<script>` 整段抠出来喂 `node --check`。"""
        m = re.search(r"<script>(.*?)</script>", HTML, re.S)
        self.assertIsNotNone(m, "抠不到 <script> 块")
        src = m.group(1)
        self.assertGreater(len(src), 5000, "抠出来的脚本太短，判据等于没跑")
        f = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
        try:
            f.write(src); f.close()
            r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, "页面 JS 语法错误：\n" + r.stderr[:800])
        finally:
            os.unlink(f.name)

    def test_价格变了结果就变(self):
        """证明这条判据真的在读 `p`，不是把常数写死在里面。"""
        self.assertIn("p.cache", _cost_src())
        self.assertIn("p.miss", _cost_src())
        self.assertIn("p.out", _cost_src())



class CloudflareSummary(unittest.TestCase):
    """`/api/cf`：本机拉 Cloudflare 数 —— 只回 classlive-* 的数字，token 不进响应。不联网（post 注入）。"""

    FAKE = {"data": {"viewer": {"accounts": [{
        "day": [{"sum": {"requests": 100, "errors": 2}, "quantiles": {"cpuTimeP50": 5000, "cpuTimeP99": 15000},
                 "dimensions": {"scriptName": "classlive-asr", "status": "success"}},
                {"sum": {"requests": 3, "errors": 0}, "quantiles": {"cpuTimeP50": 1, "cpuTimeP99": 1},
                 "dimensions": {"scriptName": "classlive-asr", "status": "clientDisconnected"}},
                {"sum": {"requests": 9, "errors": 9}, "quantiles": {"cpuTimeP50": 1, "cpuTimeP99": 1},
                 "dimensions": {"scriptName": "other-site", "status": "success"}}],
        "week": [{"sum": {"requests": 50, "errors": 0}, "dimensions": {"scriptName": "classlive-asr", "date": "2026-10-14"}}],
        "r2": [{"max": {"objectCount": 5, "payloadSize": 1000}, "dimensions": {"bucketName": "classlive-ingest", "date": "2026-10-14"}},
               {"max": {"objectCount": 7, "payloadSize": 9}, "dimensions": {"bucketName": "photosave", "date": "2026-10-14"}}],
        "dos": [{"sum": {"requests": 42}, "dimensions": {"date": "2026-10-14"}}]}]}}}

    def _home(self, d, token="cfat_SECRET-TOKEN"):
        root = pathlib.Path(d) / ".classlive"
        root.mkdir()
        if token is not None:
            (root / "cf-analytics-token").write_text(token + "\n", encoding="utf-8")
        (root / "cf-account-id").write_text("acct123\n", encoding="utf-8")
        return mock.patch.object(pathlib.Path, "home", return_value=pathlib.Path(d))

    def test_只回classlive开头的_别的项目不出现_token不进响应(self):
        import cl_admin_ui as U
        seen = {}
        def post(url, headers, body):
            seen.update(url=url, auth=headers.get("Authorization"), acct=body["variables"]["a"])
            return self.FAKE
        with tempfile.TemporaryDirectory() as d, self._home(d):
            r = U.cf_summary(post=post)
        out = json.dumps(r)
        self.assertEqual(seen["auth"], "Bearer cfat_SECRET-TOKEN")
        self.assertEqual(seen["acct"], "acct123")
        self.assertNotIn("SECRET", out)
        self.assertNotIn("photosave", out)
        self.assertNotIn("other-site", out)
        asr = r["scripts"]["classlive-asr"]
        self.assertEqual((asr["requests"], asr["errors"], asr["cpu_p50_ms"], asr["cpu_p99_ms"]), (103, 2, 5.0, 15.0))
        self.assertEqual(asr["status"], {"success": 100, "clientDisconnected": 3})
        self.assertEqual(r["r2"], {"classlive-ingest": {"2026-10-14": {"objects": 5, "bytes": 1000}}})
        self.assertEqual(r["dos"], {"2026-10-14": 42})

    def test_没有token_回人话_不调网络(self):
        import cl_admin_ui as U
        called = []
        with tempfile.TemporaryDirectory() as d, self._home(d, token=None):
            r = U.cf_summary(post=lambda *a: called.append(a))
        self.assertIn("cf-analytics-token", r["error"])
        self.assertEqual(called, [])

    def test_Cloudflare报错_回人话(self):
        import cl_admin_ui as U
        with tempfile.TemporaryDirectory() as d, self._home(d):
            r = U.cf_summary(post=lambda *a: {"errors": [{"message": "not authorized"}]})
        self.assertIn("not authorized", r["error"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
