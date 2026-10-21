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
 9. 部署时间轴的切段算术 `cluiDepSegs`：同样抠真源码喂 node。钉住「段首尾相接」「只有一个 live」
    「部署时刻压过首次请求」「读不到就标不知道」—— 界面那层的错法（段之间裂一条缝、
    两段同时说是现在的版本、拿第一次请求冒充部署时刻）图上看不出来，只能在这儿钉
10. 页面**零外链**（没有 `http(s)://` 的 src/href/@import）—— 断网也得能用

⚠️ 本机 `http.server` 当 Worker，**不联网**；HOME 指向临时目录，读端写端都隔离，不碰真 `~/.classlive/`。
⚠️ 第 8、9 条要 `node`（装了就有：`/opt/homebrew/bin/node`）。跑不了会**大声跳过**，不静默。
"""
from __future__ import annotations

import datetime
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
        for name in ("add", "list", "stats", "revoke", "unrevoke", "renew", "limit", "quota", "delete",
                     "minutes", "devices"):
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


def _js_funcs(names) -> str:
    """从页面脚本里按名字抠出几个函数（按大括号配对）。

    用来把「按各自时区显示时刻」那几段**真源码**喂给 node 跑 —— 手抄一份的话，
    页面改了判据还在绿，等于没测。
    """
    m = re.search(r"<script>(.*?)</script>", HTML, re.S)
    assert m, "抠不到 <script> 块"
    src = m.group(1)
    out = []
    for name in names:
        i = src.find("function %s(" % name)
        if i < 0:
            raise AssertionError(f"页面里没有 function {name}()")
        j = src.index("{", i)
        depth = 0
        for k in range(j, len(src)):
            if src[k] == "{":
                depth += 1
            elif src[k] == "}":
                depth -= 1
                if depth == 0:
                    out.append(src[i:k + 1])
                    break
        else:
            raise AssertionError(f"抠不出 function {name}() 的结尾")
    return "\n".join(out)


def _js_src(names) -> str:
    """同 `_js_funcs`，另外把 `TZ_NAME` 那张时区中文名的表也抠出来（`var` 不是 function）。"""
    src = _js_funcs(names)
    m = re.search(r"<script>(.*?)</script>", HTML, re.S)
    body = m.group(1)
    i = body.find("var TZ_NAME = ")
    if i < 0:
        raise AssertionError("页面里没有 TZ_NAME 那张表")
    return src + "\n" + body[i:body.index("};", i) + 1]


class R2Raw(unittest.TestCase):
    """`/api/raw`：看某节课的**原文**（report.json / 转录）。

    2026-10-21 作者定：桶里的东西都经过用户授权，**面板不脱敏**。但仍然：只读、不落盘、token 不进响应。
    """

    def _listing(self, sid):
        return {"success": True, "result_info": {"is_truncated": False}, "result": [
            {"key": f"classlive-test/{sid}/report.json", "size": 7000, "last_modified": "2026-10-18T17:22:00Z"},
            {"key": f"classlive-test/{sid}/transcript.md", "size": 900, "last_modified": "2026-10-18T17:22:00Z"}]}

    def test_能取到报告与转录(self):
        import cl_admin_ui as U
        sid = "0123456789abcdef"
        seen = {}

        def fetch(url, headers):
            seen["url"] = url
            seen["auth"] = headers.get("Authorization")
            return 200, "你好，这是课堂原文。"
        with tempfile.TemporaryDirectory() as d, _home_with(d, cf__analytics__token="cfat_SECRET", cf__account__id="acct"):
            r = U.r2_raw(sid, "report.json", get=lambda url, headers: self._listing(sid), fetch=fetch)
        self.assertEqual(r["text"], "你好，这是课堂原文。")
        self.assertEqual(r["what"], "report.json")
        self.assertIn("0123456789abcdef", seen["url"])
        self.assertEqual(seen["auth"], "Bearer cfat_SECRET")
        self.assertNotIn("SECRET", json.dumps(r), "令牌不许进响应")

    def test_只认那两种文件(self):
        import cl_admin_ui as U
        with tempfile.TemporaryDirectory() as d, _home_with(d, cf__analytics__token="cfat_SECRET", cf__account__id="acct"):
            r = U.r2_raw("0123456789abcdef", "audio.001.opus")
        self.assertIn("report.json", r["error"])

    def test_会话_id_不认(self):
        import cl_admin_ui as U
        r = U.r2_raw("../../etc/passwd", "report.json")
        self.assertIn("会话 id", r["error"])

    def test_桶里没有就说没有(self):
        import cl_admin_ui as U
        sid = "0123456789abcdef"
        with tempfile.TemporaryDirectory() as d, _home_with(d, cf__analytics__token="cfat_SECRET", cf__account__id="acct"):
            r = U.r2_raw(sid, "transcript.md", get=lambda url, headers: {"success": True, "result_info": {"is_truncated": False}, "result": []})
        self.assertIn("没有这节课", r["error"])

    def test_诊断包解开给文本成员(self):
        """诊断包（extra.tar.gz）：面板自己解开看 —— 不用再让用户把包发过来。"""
        import cl_admin_ui as U
        import io
        import tarfile
        sid = "0123456789abcdef"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name, text in (("diag.json", '{"deps": {"missing": ["websockets"]}}'),
                               ("env.json", '{"classlive_version": "3.10.2"}'),
                               ("note.md", "# 笔记"),
                               ("live.jsonl", "逐句日志不该整段倒出来" * 50)):
                data = text.encode("utf-8")
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        blob = buf.getvalue()
        listing = {"success": True, "result_info": {"is_truncated": False}, "result": [
            {"key": f"classlive-test/{sid}/extra.tar.gz", "size": len(blob), "last_modified": "2026-10-21T17:50:38Z"}]}
        with tempfile.TemporaryDirectory() as d, _home_with(d, cf__analytics__token="cfat_SECRET", cf__account__id="acct"):
            r = U.r2_raw(sid, "extra.tar.gz", get=lambda url, headers: listing,
                         fetch=lambda url, headers: (200, blob))
        self.assertEqual(r["what"], "extra.tar.gz")
        self.assertIn("diag.json", r["files"])
        self.assertIn("websockets", r["files"]["diag.json"])
        self.assertIn("3.10.2", r["files"]["env.json"])
        # 逐句日志只在清单里，不整段给
        self.assertNotIn("live.jsonl", r["files"])
        self.assertIn("live.jsonl", [m["name"] for m in r["members"]])


class TzCase(unittest.TestCase):
    """按**各自时区**显示时刻（美国 / 中国用户）。

    钉住：客户端时区优先、没有就退回本机时间（不瞎猜）；`agoIn` / `hmIn` 真的换了钟。
    """

    @unittest.skipUnless(shutil.which("node"), "没有 node —— 显示那层的函数住在 HTML 里")
    def test_按各自的时区出时刻(self):
        js = (_js_src(["hm", "ago", "agoIn", "hmIn", "tzOf", "tzLabel", "whenIn"])
              + "\nvar state = {habits: {people: {us: {tz: 'America/Los_Angeles', tz_src: 'measured'},"
              + " cn: {tz: 'Asia/Shanghai', tz_src: 'country'}}}};"
              + "\nconst T = Date.UTC(2026, 9, 21, 2, 30);"        # 2026-10-21 02:30Z
              + "\nconst la = new Intl.DateTimeFormat('en-GB',{timeZone:'America/Los_Angeles',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(T));"
              + "\nconst sh = new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(T));"
              + "\nprocess.stdout.write(JSON.stringify({"
              + "us: whenIn(T, 'us'), usHm: hmIn(T, 'America/Los_Angeles'),"
              + " cn: whenIn(T, 'cn'), cnHm: hmIn(T, 'Asia/Shanghai'),"
              + " unknown: whenIn(T, 'nobody'), local: ago(T), tz: tzOf('us'), lbl: tzLabel('Asia/Shanghai')"
              + "}));")
        p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, f"node 报错：{p.stderr[:400]}")
        g = json.loads(p.stdout)
        # 02:30Z = 美西前一天 19:30（夏令时）= 北京 10:30
        self.assertIn(g["usHm"], g["us"], f"美西钟点 {g['usHm']} 没出现在 {g['us']} 里")
        self.assertIn(g["cnHm"], g["cn"], f"北京钟点 {g['cnHm']} 没出现在 {g['cn']} 里")
        self.assertNotEqual(g["us"], g["cn"], "两个人的时刻不能一样")
        # 认不出是谁 → 退回本机时间（不编一个时区出来）
        self.assertEqual(g["unknown"], g["local"])
        self.assertEqual(g["tz"], "America/Los_Angeles")
        self.assertEqual(g["lbl"], "北京时间")


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


# ---------------- 9：部署时间轴的切段算术（跑的是 HTML 里那段真源码） ----------------
D_START = "/* >>>CLUI-DEP-START<<<"
D_END = "/* >>>CLUI-DEP-END<<<"
_H = 3600000


def _dep_src() -> str:
    i = HTML.find(D_START)
    j = HTML.find(D_END, i + 1)
    if i < 0 or j < 0:
        raise AssertionError("HTML 里找不到 CLUI-DEP 哨兵（判据会变成空的）")
    src = HTML[i:j]
    src = src[src.index("\n"):]
    if "function cluiDepSegs" not in src or len(src) > 1400:
        raise AssertionError(f"抠出来的不是那段函数（{len(src)} 字节）")
    return src


def _dep(vs, t0, t1):
    """把段算出来跑一遍真源码。返回 list[dict]。"""
    js = (_dep_src()
          + f"\nconst R = cluiDepSegs({json.dumps(vs)}, {t0}, {t1});"
          + "\nprocess.stdout.write(JSON.stringify(R));")
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
    if p.returncode:
        raise AssertionError(f"node 报错：{p.stderr[:400]}")
    return json.loads(p.stdout)


T0 = 1_760_000_000_000          # 窗口起点（ms）。真跑时 t0 会被向下取整到整点，这里就是个干净常数。
T1 = T0 + 24 * _H


def _iso(h, m=0):
    """窗口起点起算 `h` 小时 `m` 分 → ISO。负数是窗口之前。用同一个 T0 推，判据里就能直接算数。"""
    return datetime.datetime.fromtimestamp((T0 + (h * 60 + m) * 60000) / 1000,
                                           datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _v(vid, at=None, first=None, n=1, before=False):
    """造一条 `cf_health` 的 version。`at` / `first` 都是 ISO；`at=None` = 部署时刻读不到。"""
    return {"id": vid, "first": first or _iso(0), "last": _iso(23), "requests": n,
            "before": before, "deployed_at": at or ""}


class DepStripCase(unittest.TestCase):
    """段必须**首尾相接**、**钳在窗口里**、**只有一个 live** —— 这三条破了，图就会说谎。"""

    @unittest.skipUnless(shutil.which("node"), "没有 node —— 算术住在 HTML 里，Python 测不到")
    def test_段首尾相接且盖满(self):
        # 故意打乱喂进去（而且 cccccccc 的 first 最早）—— 上游 `versions` 是按 first 排的，
        # 照搬那个顺序就会画错。函数必须自己按部署时刻重排。
        r = _dep([_v("bbbbbbbb", _iso(11, 41)),
                  _v("cccccccc", _iso(12, 21), first=_iso(0)),
                  _v("aaaaaaaa", _iso(-2), before=True)], T0, T1)     # 窗口前部署 → 从头铺
        self.assertEqual([(x["i"], x["id"]) for x in r],
                         [(1, "aaaaaaaa"), (2, "bbbbbbbb"), (3, "cccccccc")],
                         "顺序按部署时刻，序号从 1 起")
        self.assertEqual(r[0]["s"], T0, "窗口前部署的第一段必须从窗口起点铺起")
        self.assertEqual([r[0]["e"], r[1]["e"], r[2]["e"]], [r[1]["s"], r[2]["s"], T1], "接缝对得上")
        self.assertEqual(sum(x["dur"] for x in r), T1 - r[0]["s"], "段长之和 = 覆盖到的跨度（不许有缝）")
        self.assertEqual([x["live"] for x in r], [False, False, True], "有且只有最后一段是 live")

    @unittest.skipUnless(shutil.which("node"), "没有 node —— 算术住在 HTML 里，Python 测不到")
    def test_部署时刻压过首次请求(self):
        """两个来源差 6 小时 19 分：段起点必须跟 `deployed_at`（11:41），不是 `first`（18:00）。"""
        r = _dep([_v("aaaaaaaa", at=_iso(11, 41), first=_iso(18))], T0, T1)
        self.assertTrue(r[0]["known"])
        self.assertEqual(r[0]["s"], T0 + (11 * 60 + 41) * 60000)

    @unittest.skipUnless(shutil.which("node"), "没有 node —— 算术住在 HTML 里，Python 测不到")
    def test_读不到部署时刻_退回请求且标成不知道(self):
        r = _dep([_v("aaaaaaaa", None, first=_iso(1), before=True),
                  _v("bbbbbbbb", _iso(12))], T0, T1)
        self.assertEqual(r[0]["at"], None)
        self.assertFalse(r[0]["known"], "读不到就不许说知道 —— 界面上要如实标")
        self.assertEqual(r[0]["s"], T0 + _H, "退回按 first（第一次有请求的分钟）摆")
        self.assertTrue(r[1]["known"])
        self.assertEqual(sum(x["dur"] for x in r), T1 - r[0]["s"])

    @unittest.skipUnless(shutil.which("node"), "没有 node —— 算术住在 HTML 里，Python 测不到")
    def test_窗口末尾及之后才上的版本不画(self):
        """`_iso(25)` 在窗口外，`_iso(24)` 正好压在窗口末尾 —— 两个都不画：
        零宽度的段没有信息，还会把「最后一段 = live」抢走。"""
        r = _dep([_v("aaaaaaaa", _iso(12)), _v("bbbbbbbb", _iso(24)), _v("cccccccc", _iso(25))], T0, T1)
        self.assertEqual([x["id"] for x in r], ["aaaaaaaa"], "窗口末尾及之后的版本都不许进来")
        self.assertTrue(r[0]["live"])
        self.assertEqual(r[0]["dur"], T1 - r[0]["s"], "它得顶到窗口末尾")

    @unittest.skipUnless(shutil.which("node"), "没有 node —— 算术住在 HTML 里，Python 测不到")
    def test_都没得读时也首尾相接(self):
        """`deploys_error` 的整段兜底：全部退回 first，仍然只许有一个 live、不许有缝。"""
        r = _dep([_v("aaaaaaaa", None, first=_iso(1), before=True),
                  _v("bbbbbbbb", None, first=_iso(12)),
                  _v("cccccccc", None, first=_iso(20))], T0, T1)
        self.assertEqual([x["known"] for x in r], [False] * 3)
        self.assertEqual([x["at"] for x in r], [None] * 3)
        self.assertEqual([x["live"] for x in r], [False, False, True])
        self.assertEqual([x["e"] for x in r], [r[1]["s"], r[2]["s"], T1])

    @unittest.skipUnless(shutil.which("node"), "没有 node —— 算术住在 HTML 里，Python 测不到")
    def test_最后一段才算_live(self):
        """读不到部署时刻时后两版的段起点都落在窗口最后一分钟 —— 两段的尾都是 `t1`，
        但「现在的版本」只能有一个（按 `e >= t1` 判就会同时亮两格）。"""
        r = _dep([_v("aaaaaaaa", None, first=_iso(1)),
                  _v("bbbbbbbb", None, first=_iso(24)),
                  _v("cccccccc", None, first=_iso(24))], T0, T1)
        self.assertEqual([x["s"] for x in r[1:]], [T1, T1], "前提没搭起来：后两段没压在窗口末尾")
        self.assertEqual([x["e"] for x in r[1:]], [T1, T1])
        self.assertEqual([x["live"] for x in r], [False, False, True], "不许两段同时说自己是现在的版本")

    @unittest.skipUnless(shutil.which("node"), "没有 node —— 算术住在 HTML 里，Python 测不到")
    def test_空输入不炸(self):
        self.assertEqual(_dep([], T0, T1), [])

    def test_判据抠的是真源码(self):
        """哨兵抠歪 → 上面几条全是空的。这里钉住它确实拿到了那个函数体。"""
        src = _dep_src()
        self.assertIn("Math.max(t0,", src)
        self.assertIn("isNaN(at)", src)
        self.assertNotIn("el(", src, "哨兵里不许混进碰 DOM 的东西")
        self.assertNotIn("state.", src, "哨兵里不许读全局 state")



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
        "dos": [{"sum": {"requests": 42}, "dimensions": {"date": "2026-10-14"}}],
        "mins": [{"sum": {"requests": 46, "errors": 1}, "dimensions": {"scriptName": "classlive-asr", "datetimeMinute": "2026-10-14T17:42:00Z"}},
                 {"sum": {"requests": 3, "errors": 0}, "dimensions": {"scriptName": "other-site", "datetimeMinute": "2026-10-14T17:42:00Z"}}]}]}}}

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
        self.assertEqual(r["minutes"], {"classlive-asr": {"2026-10-14T17:42:00Z": [46, 1]}})

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

class R2Ingest(unittest.TestCase):
    """上传桶实时清单：分页拼齐、按会话分组、本机的课标 mine、token 不进响应。读写都在临时目录。"""

    def test_分页_分组_认出本机的课_别人的单列(self):
        import hashlib
        import cl_admin_ui as U
        h = lambda n: hashlib.sha256(n.encode()).hexdigest()[:16]          # noqa: E731
        mine, other = h("2026-10-14_100000_ECON1"), "0123456789abcdef"
        pages = [
            {"success": True, "result": [
                {"key": f"classlive-test/{mine}/report.json", "size": 10, "last_modified": "2026-10-14T10:00:00Z"},
                {"key": f"classlive-test/{other}/audio.001.wav", "size": 1000, "last_modified": "2026-10-14T20:00:00Z"}],
             "result_info": {"is_truncated": True, "cursor": "c1"}},
            {"success": True, "result": [
                {"key": f"classlive-test/{other}/report.json", "size": 5, "last_modified": "2026-10-14T20:01:00Z"}],
             "result_info": {"is_truncated": False}}]
        seen = []
        def get(url, headers):
            seen.append((url, headers.get("Authorization")))
            return pages[len(seen) - 1]
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d) / ".classlive"; root.mkdir()
            (root / "cf-analytics-token").write_text("cfat_SECRET\n", encoding="utf-8")
            (root / "cf-account-id").write_text("acct\n", encoding="utf-8")
            sd = pathlib.Path(d) / "sessions"; sd.mkdir()
            (sd / "2026-10-14_100000_ECON1.md").write_text("x", encoding="utf-8")
            with mock.patch.object(pathlib.Path, "home", return_value=pathlib.Path(d)):
                r = U.r2_ingest(get=get, sessions_dir=sd)
        self.assertIn("cursor=c1", seen[1][0])
        self.assertEqual((r["objects"], r["bytes"], r["others"]), (3, 1015, 1))
        top = r["sessions"][0]
        self.assertEqual((top["id"], top["mine"], top["audio"], top["files"], top["last"]),
                         (other, False, 1, ["report.json"], "2026-10-14T20:01:00Z"))
        self.assertEqual((r["sessions"][1]["mine"], r["sessions"][1]["name"]), (True, "2026-10-14_100000_ECON1"))
        self.assertNotIn("SECRET", json.dumps(r))


def _cf_home(d, token="cfat_SECRET-TOKEN"):
    root = pathlib.Path(d) / ".classlive"
    root.mkdir()
    (root / "cf-analytics-token").write_text(token + "\n", encoding="utf-8")
    (root / "cf-account-id").write_text("acct123\n", encoding="utf-8")
    return mock.patch.object(pathlib.Path, "home", return_value=pathlib.Path(d))


class Health(unittest.TestCase):
    """`/api/health` `/api/logs` `/api/ae` `/api/cfwin`：形状、单位换算、归类、token 不进响应。不联网（全注入）。"""

    GQL = {"data": {"viewer": {
        "accounts": [{
            "lat": [{"sum": {"requests": 900, "errors": 1}, "dimensions": {"datetimeHour": "2026-10-14T17:00:00Z"},
                     "quantiles": {"wallTimeP50": 1132000, "wallTimeP95": 1631000, "requestDurationP50": 900000, "requestDurationP95": 1500000}}],
            "ver": [{"sum": {"requests": 5}, "dimensions": {"scriptVersion": "aaaaaaaa-1", "datetimeMinute": "2026-10-13T21:00:00Z"}},
                    {"sum": {"requests": 7}, "dimensions": {"scriptVersion": "aaaaaaaa-1", "datetimeMinute": "2026-10-14T09:00:00Z"}},
                    {"sum": {"requests": 3}, "dimensions": {"scriptVersion": "bbbbbbbb-2", "datetimeMinute": "2026-10-14T20:31:00Z"}}],
            "sub": [{"sum": {"subrequests": 10}, "dimensions": {"hostname": "api.groq.com", "httpResponseStatus": 200, "requestOutcome": "completed"}},
                    {"sum": {"subrequests": 2}, "dimensions": {"hostname": "api.groq.com", "httpResponseStatus": 429, "requestOutcome": "completed"}},
                    {"sum": {"subrequests": 1}, "dimensions": {"hostname": "api.groq.com", "httpResponseStatus": 0, "requestOutcome": "canceled"}},
                    {"sum": {"subrequests": 4}, "dimensions": {"hostname": "", "httpResponseStatus": 503, "requestOutcome": "completed"}}],
            "colo": [{"sum": {"requests": 3}, "quantiles": {"wallTimeP50": 1000000}, "dimensions": {"coloCode": "NRT"}},
                     {"sum": {"requests": 9}, "quantiles": {"wallTimeP50": 2000}, "dimensions": {"coloCode": "DUB"}}]}],
        "zones": [{
            "host": [{"count": 5, "dimensions": {"clientRequestHTTPHost": "asr.bldcam.page", "edgeResponseStatus": 200, "clientCountryName": "US"}},
                     {"count": 2, "dimensions": {"clientRequestHTTPHost": "asr.bldcam.page", "edgeResponseStatus": 503, "clientCountryName": "IE"}},
                     {"count": 874, "dimensions": {"clientRequestHTTPHost": "bldcam.page", "edgeResponseStatus": 200, "clientCountryName": "CN"}},
                     {"count": 527, "dimensions": {"clientRequestHTTPHost": "cdn.bldcam.page", "edgeResponseStatus": 200, "clientCountryName": "CN"}}]}]}}}

    #: `GET /workers/scripts/<script>/deployments` 的真形状：`result` 是**字典**（不是列表），
    #: 版本号挂在 `versions[].version_id`。里面的 `author_email` 是别人（作者本人）的邮箱 —— 不许进响应。
    #: ⚠️ `created_on` 真的带微秒（从线上抓的样例就是 `.863119Z`）。判据必须用带微秒的，
    #: 不然钉不住「掐到秒」那一步 —— 页面那边 `Date.parse` 遇到超 3 位小数会静默变 NaN。
    DEP = {"result": {"deployments": [
        {"created_on": "2026-10-13T20:30:00.863119Z", "author_email": "SECRET@example.com",
         "versions": [{"version_id": "aaaaaaaa-1", "percentage": 100}]},
        {"created_on": "2026-10-14T20:31:00.019101Z", "author_email": "SECRET@example.com",
         "versions": [{"version_id": "bbbbbbbb-2", "percentage": 100}]}]}}

    def test_单位换算_部署首末_上游归类_站点汇总_token不进响应(self):
        import datetime as dt
        import cl_admin_ui as U
        seen = []
        def post(url, headers, body):
            seen.append((headers["Authorization"], body["variables"]))
            return self.GQL
        def get(url, headers):                                               # 同一个 get 要伺候两个端点
            return self.DEP if "/deployments" in url else {"result": [{"id": "zone42"}]}
        now = dt.datetime(2026, 10, 14, 21, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as d, _cf_home(d):
            r = U.cf_health(post=post, get=get, now=now)
        self.assertEqual(seen[0][1]["z"], "zone42")
        # 部署时刻来自 deployments API，是按**完整**版本号（`aaaaaaaa-1`）对上的，
        # 而对外只给前 8 位 —— 对上号才算真读到了，对不上就会静默退回空串。
        self.assertEqual(r["deploys_error"], "")
        self.assertEqual([v["deployed_at"] for v in r["versions"]],
                         ["2026-10-13T20:30:00Z", "2026-10-14T20:31:00Z"])
        self.assertEqual((r["lat"][0]["wall_p50"], r["lat"][0]["wall_p95"], r["lat"][0]["dur_p50"]), (1132, 1631, 900))
        a, b = r["versions"]
        self.assertEqual((a["id"], a["first"], a["last"], a["requests"], a["before"]),
                         ("aaaaaaaa", "2026-10-13T21:00:00Z", "2026-10-14T09:00:00Z", 12, True))
        self.assertEqual((b["id"], b["before"]), ("bbbbbbbb", False))
        g = next(x for x in r["upstream"] if x["host"] == "api.groq.com")
        self.assertEqual((g["name"], g["total"], g["ok"], g["c429"], g["unfinished"]), ("Groq", 13, 10, 2, 1))
        do = next(x for x in r["upstream"] if x["host"] == "")
        self.assertEqual((do["name"], do["c5xx"]), ("Durable Object", 4))
        self.assertEqual([c["colo"] for c in r["colos"]], ["DUB", "NRT"])
        self.assertEqual(r["colos"][1]["wall_p50"], 1000)
        # 同一个 zone 上还挂着摄影站（bldcam.page）和图床（cdn）：它们不是这个项目的数，不许进表。
        # 判据钉住整张表而不只是「有没有过滤」——漏一个主机名就是白干。
        self.assertEqual([x["host"] for x in r["zone"]["hosts"]], ["asr.bldcam.page"],
                         "只有 CLASSLIVE_HOSTS 里的主机名能进站点表")
        h = r["zone"]["hosts"][0]
        self.assertEqual((h["total"], h["s2"], h["s5"], h["countries"]), (7, 5, 2, [("US", 5), ("IE", 2)]))
        self.assertNotIn("bad_paths", r["zone"], "扫描器探针那张表已删，字段也不许留在响应里")
        out = json.dumps(r)
        self.assertNotIn("SECRET", out)
        self.assertNotIn("clientIP", U._HEALTH_Q, "站点查询不许查 IP")

    def test_zone读不到_其余照常(self):
        import cl_admin_ui as U
        bad = json.loads(json.dumps(self.GQL))
        bad["data"]["viewer"]["zones"] = []
        bad["errors"] = [{"message": "zone not authorized", "path": ["viewer", "zones", "0"]}]
        def get(url, headers):
            return self.DEP if "/deployments" in url else {"result": []}
        with tempfile.TemporaryDirectory() as d, _cf_home(d):
            r = U.cf_health(post=lambda *a: bad, get=get)
        self.assertIsNone(r["zone"])
        self.assertTrue(r["zone_error"])
        self.assertEqual(len(r["lat"]), 1)
        # 站点读不到不许连累部署时刻：两条读的是不同的端点，各挂各的。
        self.assertEqual(r["deploys_error"], "")
        self.assertEqual(r["versions"][1]["deployed_at"], "2026-10-14T20:31:00Z")

    def test_读不到部署时刻_如实标出来不编(self):
        """token 没有 Workers Scripts: Read 时，`deployed_at` 必须空着 + `deploys_error` 有话说，
        而不是拿「第一次有请求的分钟」冒充部署时刻（低流量版本能差几小时）。"""
        import datetime as dt
        import cl_admin_ui as U
        def get(url, headers):
            return {"result": []} if "/deployments" in url else {"result": [{"id": "zone42"}]}
        now = dt.datetime(2026, 10, 14, 21, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as d, _cf_home(d):
            r = U.cf_health(post=lambda *a: self.GQL, get=get, now=now)
        self.assertTrue(r["deploys_error"])
        self.assertEqual([v["deployed_at"] for v in r["versions"]], ["", ""])
        a, b = r["versions"]                                            # 其余字段照旧，退回按请求推断
        self.assertEqual((a["id"], a["first"], a["before"]), ("aaaaaaaa", "2026-10-13T21:00:00Z", True))
        self.assertEqual((b["id"], b["last"], b["before"]), ("bbbbbbbb", "2026-10-14T20:31:00Z", False))

    def test_读部署链炸了也不连累别的(self):
        """deployments 端点抛异常（网络 / 502）走的是同一条兜底：`None` → 空串 + 错误文案。"""
        import cl_admin_ui as U
        def get(url, headers):
            if "/deployments" in url:
                raise RuntimeError("502")
            return {"result": [{"id": "zone42"}]}
        with tempfile.TemporaryDirectory() as d, _cf_home(d):
            r = U.cf_health(post=lambda *a: self.GQL, get=get)
        self.assertTrue(r["deploys_error"])
        self.assertEqual(r["versions"][0]["deployed_at"], "")
        self.assertEqual(len(r["lat"]), 1, "延迟数据照常出")

    def test_日志_只取我们自己写的字段(self):
        import cl_admin_ui as U
        def post(url, headers, body):
            if body["view"] == "calculations":
                return {"success": True, "result": {"calculations": [
                    {"calculation": "median(ms)", "aggregates": [{"groupKey": "whisper-large-v3", "value": 654.4, "count": 731}]},
                    {"calculation": "p90(ms)", "aggregates": [{"groupKey": "whisper-large-v3", "value": 912, "count": 731}]},
                    {"calculation": "max(ms)", "aggregates": [{"groupKey": "whisper-large-v3", "value": 3520, "count": 731}]}]}}
            flt = body["parameters"]["filters"][1]["key"]
            ev = [{"timestamp": 1792011946784, "source": {"pass": "t", "denied": "devices", "kind": "llm"},
                   "$workers": {"event": {"request": {"headers": {"cf-connecting-ip": "203.0.113.9"}}}}}] if flt == "denied" else []
            return {"success": True, "result": {"events": {"events": ev}}}
        with tempfile.TemporaryDirectory() as d, _cf_home(d):
            r = U.worker_logs(post=post, now_ms=1792012000000)
        self.assertEqual(r["models"], [{"model": "whisper-large-v3", "count": 731, "median": 654, "p90": 912, "max": 3520}])
        self.assertEqual(r["events"], [{"pass": "t", "denied": "devices", "kind": "llm", "t": 1792011946784}])
        self.assertNotIn("203.0.113.9", json.dumps(r))

    def test_事件_没开AE回pending_开了回三张表(self):
        import cl_admin_ui as U
        with tempfile.TemporaryDirectory() as d, _cf_home(d):
            r = U.ae_events(post=lambda *a: (403, {"errors": [{"message": "Authorization error"}]}))
            self.assertTrue(r["pending"])
            sqls = []
            def post(url, headers, sql):
                sqls.append(sql)
                return 200, {"data": [{"name": "amy", "n": "3"}]}
            r = U.ae_events(post=post)
        self.assertEqual(set(r), {"by_code", "geo", "bad", "up", "speed"})
        self.assertTrue(all("classlive_events" in q for q in sqls))
        self.assertNotIn("SECRET", json.dumps(r))

    def test_时段延迟_全站口径_毫秒(self):
        import cl_admin_ui as U
        fake = {"data": {"viewer": {"accounts": [{
            "w": [{"sum": {"requests": 1224, "errors": 0}, "quantiles": {"wallTimeP50": 1132000, "wallTimeP95": 1631000}}],
            "c": [{"sum": {"requests": 331}, "dimensions": {"coloCode": "NRT"}}]}]}}}
        with tempfile.TemporaryDirectory() as d, _cf_home(d):
            r = U.cf_window("2026-10-14T17:00:00Z", "2026-10-14T17:48:00Z", post=lambda *a: fake)
        self.assertEqual(r, {"requests": 1224, "errors": 0, "wall_p50": 1132, "wall_p95": 1631, "colos": [["NRT", 331]]})


class HealthOverall(unittest.TestCase):
    """`overall` = 整个窗口一个 p50/p95（`all:` 那条别名），不是各小时的分位数再平均。"""

    def test_有all就给整体数_没有就None(self):
        import cl_admin_ui as U
        g = json.loads(json.dumps(Health.GQL))
        g["data"]["viewer"]["accounts"][0]["all"] = [{"sum": {"requests": 7127, "errors": 3},
                                                      "quantiles": {"wallTimeP50": 1151000, "wallTimeP95": 1736000}}]
        get = lambda url, headers: Health.DEP if "/deployments" in url else {"result": [{"id": "zone42"}]}  # noqa: E731
        with tempfile.TemporaryDirectory() as d, _cf_home(d):
            r = U.cf_health(post=lambda *a: g, get=get)
            r0 = U.cf_health(post=lambda *a: Health.GQL, get=get)
        self.assertEqual(r["overall"], {"requests": 7127, "errors": 3, "wall_p50": 1151, "wall_p95": 1736})
        self.assertIsNone(r0["overall"])
        self.assertIn("all:workersInvocationsAdaptive(limit:1", U._HEALTH_Q)


class LogsParallel(unittest.TestCase):
    def test_四条查询同时发(self):
        """串着发是 4 × 单条；一起发 ≈ 最慢那条。判据数「同时在飞的最多几条」，不靠墙钟（机器忙时墙钟会抖）。"""
        import cl_admin_ui as U
        lock, live, peak = threading.Lock(), [0], [0]
        gate = threading.Barrier(4, timeout=5)                          # 四条都进来了才放行 —— 串行的话这里会超时
        def post(url, headers, body):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            try:
                gate.wait()
            finally:
                with lock:
                    live[0] -= 1
            if body["view"] == "calculations":
                return {"success": True, "result": {"calculations": []}}
            return {"success": True, "result": {"events": {"events": []}}}
        with tempfile.TemporaryDirectory() as d, _cf_home(d):
            r = U.worker_logs(post=post, now_ms=1792012000000)
        self.assertEqual(peak[0], 4)
        self.assertEqual(r, {"models": [], "events": []})


class SlowCache(unittest.TestCase):
    """`cached()`：新的直接给、旧的先给再后台重取、太旧同步等、`fresh` 当场重取、出错不进缓存。"""

    def setUp(self):
        import cl_admin_ui as U
        self.U = U
        self.p = mock.patch.dict(U._CACHE, clear=True)
        self.p.start()
        self.addCleanup(self.p.stop)
        self.now = [1000.0]
        self.calls = []

    def clock(self):
        return self.now[0]

    def fn(self):
        self.calls.append(self.now[0])
        return {"v": len(self.calls)}

    def get(self, fresh=False, fn=None):
        return self.U.cached("t", fn or self.fn, fresh, clock=self.clock)

    def test_新的直接给_不再问(self):
        self.assertEqual(self.get(), {"v": 1, "cache_age": 0})
        self.now[0] += 30
        self.assertEqual(self.get(), {"v": 1, "cache_age": 30})
        self.assertEqual(len(self.calls), 1)

    def test_旧的先给_后台重取一次(self):
        self.get()
        self.now[0] += self.U.FRESH_S + 5
        r = self.get()
        self.assertEqual(r["v"], 1, "先给旧的，不等")
        for t in [t for t in threading.enumerate() if "_compute" in t.name]:
            t.join(timeout=5)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.get()["v"], 2)

    def test_太旧就同步等(self):
        self.get()
        self.now[0] += self.U.STALE_S + 1
        self.assertEqual(self.get(), {"v": 2, "cache_age": 0})

    def test_fresh当场重取(self):
        self.get()
        self.now[0] += 1
        self.assertEqual(self.get(fresh=True), {"v": 2, "cache_age": 0})

    def test_出错不进缓存(self):
        self.assertEqual(self.get(fn=lambda: {"error": "x"})["error"], "x")
        self.assertEqual(self.get()["v"], 1, "上一次是错的，这次要真去取")

    def test_抛异常变成一条错误(self):
        def boom():
            raise RuntimeError("网断了")
        self.assertIn("RuntimeError", self.get(fn=boom)["error"])

    def test_接口把fresh传进去(self):
        seen = []
        def fake(fresh=False):
            seen.append(fresh)
            return {"ok": 1}
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self.U.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        import httpx
        url = f"http://127.0.0.1:{srv.server_address[1]}/api/logs"
        h = {self.U.GATE_HEADER: "1"}
        with mock.patch.object(self.U, "cached", side_effect=lambda n, f, fr: {"fresh": fr, "name": n}):
            a = httpx.post(url, content=b"{}", headers=h, timeout=5).json()
            b = httpx.post(url, content=b'{"fresh":true}', headers=h, timeout=5).json()
            c = httpx.post(url, content=b"not json", headers=h, timeout=5).json()
        self.assertEqual((a, b, c), ({"fresh": False, "name": "logs"}, {"fresh": True, "name": "logs"},
                                     {"fresh": False, "name": "logs"}))


# ---------------- 10：3.10 新量具（AE / R2 报告 / 平台账号） ----------------
def _home_with(d, **files):
    root = pathlib.Path(d) / ".classlive"
    root.mkdir(exist_ok=True)
    for name, content in files.items():
        (root / name.replace("__", "-")).write_text(str(content) + "\n", encoding="utf-8")
    return mock.patch.object(pathlib.Path, "home", return_value=pathlib.Path(d))


class AE310(unittest.TestCase):
    """`/api/ae310`：Soniox 临时 key / OpenRouter 快速通道 / 客户端版本 / 课后笔记。不联网（post 注入）。"""

    def test_四路查询_形状与数据集(self):
        import cl_admin_ui as U
        sqls = []
        def post(url, headers, sql):
            sqls.append(sql)
            return 200, {"data": [{"name": "amy", "n": "3"}]}
        with tempfile.TemporaryDirectory() as d, _cf_home(d):
            r = U.ae_310(post=post)
        self.assertEqual(set(r), {"soniox", "fast", "rollout", "notes"})
        self.assertTrue(all("classlive_events" in q for q in sqls))
        self.assertTrue(any("blob1 = '/v1/soniox/key'" in q for q in sqls), "Soniox key 那路没查")
        self.assertTrue(any("double11 > 600" in q for q in sqls), "笔记（max_tokens > 600）那路没查")
        fast = [q for q in sqls if "double11 <= 400" in q]
        self.assertEqual(len(fast), 1, "快速通道那路的分母要只数够格请求（max_tokens ≤ 400）")
        self.assertIn("double9 > 0", fast[0]); self.assertIn("NOT IN ('CN', 'HK', 'MO')", fast[0])
        self.assertNotIn("SECRET", json.dumps(r))

    def test_没开AE回pending(self):
        import cl_admin_ui as U
        with tempfile.TemporaryDirectory() as d, _cf_home(d):
            r = U.ae_310(post=lambda *a: (403, {"errors": [{"message": "Authorization error"}]}))
        self.assertTrue(r["pending"])


REPORT = {
    "generated": "2026-10-18 17:21:45",
    "session_file": "/Users/owen/SECRET-session.md",
    "note_file": "/Users/owen/Lectures/SECRET-note.md",
    "audio_files": ["/Users/owen/SECRET-audio.001.opus"],
    "duration_s": 1234.5, "audio_saved": True,
    "blocks": {
        "stream": {"engine": "soniox", "fallback_reason": "manual", "sealed_n": 12,
                   "seal_latency_s": {"p50": 0.9, "p90": 1.7, "n": 12}, "terms_n": 5, "terms_dropped": 2},
        "region_probe": {"default": "us", "selected": "us", "switched": False, "reason": "cache", "cached": True,
                         "ms": 210, "timeout": False,
                         "regions": {"us": {"connect_ms": 90, "rtt_ms": 200, "error": ""},
                                     "jp": {"connect_ms": 300, "rtt_ms": 500, "error": "timeout"}}},
        "duty": {"items": 4, "pending": 1, "saved": 2, "ignored": 1, "inferred": 0},
        "device": {"start": {"machine": {"model": "MacBookPro18,3"}},
                   "end": {"machine": {"model": "MacBookPro18,3", "cores_perf": 8, "cores_eff": 2},
                           "power": {"power": "AC", "battery_pct": 83, "low_power_mode": False, "thermal": "nominal"},
                           "net": "wifi", "locale": {"lang": "en-GB"},
                           "footprint": {"sessions_mb": 12.3, "classlive_mb": 4900.1, "models_mb": 800.0,
                                         "partial": False, "classlive_top": [{"name": "SECRET-dir", "mb": 100}]}}},
        "energy": {"available": True, "wall_s": 247.6, "billed_energy_j": 54.7, "cpu_s": 57.3, "screen_hz": 120.0,
                   "visible": {"wall_s": 229.4, "mean_w": 0.237}, "hidden": {"wall_s": 18.2, "mean_w": 0.014}},
        "capture": {"available": True, "wall_s": 246.8, "audio_s": 237.7, "deficit_s": 9.1, "gaps": {"n": 2, "total_s": 3.2}},
        "merge": {"triggered": 1, "replaced": 1, "failed": 0, "items": [{"en": "SECRET"}]},
        "backfill": {"items": []},
        "system": {"draft_engine": "nemotron", "draft_reason": "", "ram_gb": 16.0, "chip": "Apple M1 Pro",
                   "macos": "15.7.5", "arch": "arm64"},
        "ui": {"submit": 1, "hide": 1, "show": 1, "hidden_s": 18.3, "zh_overflow": {"overflow": 0, "seen": 598},
               "streamq_dwell_ms": {"n": 871, "p50": 4.1, "p90": 10.8}},
        "terms": {"calls": 58, "picked": 480, "empty": 0},
        "live": {"calls_total": 2, "failed_calls": 0, "chapters": 0, "atoms": 7},
        "vad": {"cuts": 55, "hard_cuts": 158, "dropped": 0, "dropped_s": 0.0, "weak_blocks": 33,
                "vad_resets": 82, "blind_dropped": 0, "sentence_cuts": 0},
        "norm": {"blocks": 22279, "capped": 3230, "clamped": 4619, "gain_db_max": 29.8},
    },
    "asr": {"segments": 410, "words": 5000, "empty_pct": 1.2, "final_empty_pct": 0.5, "fell_back_pct": 3.0,
            "source_pct": {"groq": 90.0, "local-whisper": 10.0}},
    "correction": {"pairs": 100, "changed": 12, "changed_pct": 12.0},
    "translation_latency": {"n": 200, "zh_first_ms": {"p50": 412, "p90": 900}},
    "segments": [{"words": ["SECRET-transcript"]}],
    "events": [{"kind": "SECRET"}],
    "vad_report": "/Users/owen/SECRET-vad.txt",
    "notes": ["SECRET note"],
}


class ReportSummary(unittest.TestCase):
    """`_report_summary`：只回数字与枚举 —— 课堂文本 / 路径 / 逐段明细一个都不许漏。"""

    def _sum(self, rep=None):
        import cl_admin_ui as U
        return U._report_summary(rep if rep is not None else REPORT, "abc123", "owen", "2026-10-18T17:22:00Z",
                                 ["report.json", "transcript.md", "audio.001.opus"])

    def test_数字与枚举都取到了(self):
        r = self._sum()
        self.assertEqual(r["duration_s"], 1234.5)
        self.assertTrue(r["audio_saved"])
        self.assertEqual(r["stream"], {"engine": "soniox", "reason": "manual", "detail": "", "sealed_n": 12, "p50": 0.9,
                                       "p90": 1.7, "lat_n": 12, "terms_n": 5, "terms_dropped": 2})
        self.assertEqual(r["region"]["default"], "us")
        self.assertEqual(r["region"]["regions"]["jp"]["error"], "timeout")
        self.assertEqual(r["region"]["regions"]["us"]["connect_ms"], 90)
        self.assertEqual(r["duty"]["saved"], 2)
        self.assertEqual(r["device"]["model"], "MacBookPro18,3")
        self.assertEqual((r["device"]["sessions_mb"], r["device"]["classlive_mb"], r["device"]["partial"]),
                         (12.3, 4900.1, False))
        self.assertEqual(r["energy"]["visible_w"], 0.237)
        self.assertEqual(r["capture"]["deficit_s"], 9.1)
        self.assertEqual(r["merge"]["triggered"], 1)
        self.assertEqual(r["system"]["draft_engine"], "nemotron")
        self.assertEqual(r["ui"]["sq_p50"], 4.1)
        self.assertEqual(r["asr"]["segments"], 410)
        self.assertEqual(r["corr"]["changed_pct"], 12.0)
        for k in ("stream", "region_probe", "duty", "device", "energy", "capture"):
            self.assertIn(k, r["blocks"])
        # 2026-10-21 加：`vad` / `norm` 进了白名单（查丢句的关键量）
        self.assertIn("vad", r["blocks"])
        self.assertIn("norm", r["blocks"])
        self.assertEqual(r["vad"]["hard_cuts"], 158)
        self.assertEqual(r["vad"]["dropped_s"], 0.0)
        self.assertEqual(r["vad"]["weak_blocks"], 33)
        self.assertEqual(r["vad"]["vad_resets"], 82)
        self.assertEqual((r["norm"]["capped"], r["norm"]["clamped"]), (3230, 4619))

    def test_绝不漏文本路径或逐段明细(self):
        out = json.dumps(self._sum(), ensure_ascii=False)
        for leak in ("SECRET", "/Users/owen", "vad_report", "session_file", "audio_files",
                     "note_file", "classlive_top", "SECRET-transcript"):
            self.assertNotIn(leak, out, f"报告摘要漏了 {leak}")

    def test_长串被丢掉_缺块为_None_不炸(self):
        import cl_admin_ui as U
        r = U._report_summary({"blocks": {"stream": {"engine": "x" * 200, "fallback_reason": "y" * 200}}}, "s", "", "", [])
        self.assertEqual(r["stream"]["engine"], "", "超长枚举串必须丢掉，不能当引擎名报")
        self.assertEqual(r["stream"]["reason"], "")
        self.assertIsNone(r["duty"], "没采到的块是 None，不是空 dict")
        self.assertEqual(U._report_summary([], "s", "", "", [])["id"], "s", "非 dict 输入不许炸")

    def test_report_at_用报告自己的上传时刻(self):
        """告警的「多久以前」锚在 report.json 的上传时刻，不是组里最后文件的 mtime。"""
        import cl_admin_ui as U
        r = U._report_summary({}, "s", "", "2026-10-20T23:49:00Z", [], "2026-10-19T17:56:00Z")
        self.assertEqual(r["report_at"], "2026-10-19T17:56:00Z")
        self.assertEqual(U._report_summary({}, "s", "", "2026-10-20T23:49:00Z", [])["report_at"],
                         "2026-10-20T23:49:00Z", "没给 report_at 时退回 updated，老调用方不炸")


class R2Reports(unittest.TestCase):
    """`/api/reports`：列桶 → 挑 report.json → 现取 → 摘要。分页/别人的课/单份坏掉都要对。"""

    def test_挑报告_分本机与别人_单份坏掉不连累(self):
        import hashlib
        import cl_admin_ui as U
        h = lambda n: hashlib.sha256(n.encode()).hexdigest()[:16]          # noqa: E731
        mine, other, norep = h("2026-10-18_171736_测试"), "0123456789abcdef", "ffffffffffffffff"
        listing = {"success": True, "result_info": {"is_truncated": False}, "result": [
            {"key": f"classlive-test/{mine}/report.json", "size": 7000, "last_modified": "2026-10-18T17:22:00Z"},
            {"key": f"classlive-test/{mine}/transcript.md", "size": 900, "last_modified": "2026-10-18T17:22:00Z"},
            {"key": f"classlive-test/{other}/report.json", "size": 5, "last_modified": "2026-10-18T10:00:00Z"},
            {"key": f"classlive-test/{norep}/transcript.md", "size": 5, "last_modified": "2026-10-18T09:00:00Z"}]}
        bodies = {f"classlive-test/{mine}/report.json": json.dumps(REPORT),
                  f"classlive-test/{other}/report.json": "{not json"}
        seen = []
        def raw(key, headers):
            seen.append((key, headers.get("Authorization")))
            return bodies[key]
        with tempfile.TemporaryDirectory() as d:
            sd = pathlib.Path(d) / "sessions"; sd.mkdir()
            (sd / "2026-10-18_171736_测试.md").write_text("x", encoding="utf-8")
            with _home_with(d, cf__analytics__token="cfat_SECRET", cf__account__id="acct"):
                r = U.r2_reports(get=lambda url, headers: listing, raw=raw, sessions_dir=sd)
        self.assertEqual(r["candidates"], 2, "只有带 report.json 的会话才算")
        self.assertEqual([s["id"] for s in r["sessions"]], [mine])
        self.assertEqual(r["sessions"][0]["name"], "2026-10-18_171736_测试")
        self.assertTrue(r["sessions"][0]["audio_saved"])
        self.assertEqual(r["errors"], [f"{other}：JSONDecodeError"])
        self.assertNotIn("SECRET", json.dumps(r))
        self.assertNotIn(f"{norep}/transcript.md", json.dumps(r))

    def test_没token回人话不联网(self):
        import cl_admin_ui as U
        called = []
        with tempfile.TemporaryDirectory() as d, _home_with(d):
            r = U.r2_reports(get=lambda *a: called.append(a))
        self.assertIn("cf-analytics-token", r["error"])
        self.assertEqual(called, [])


def _sx_get(usage=None, conc=None, logs=None, bal=None):
    usage = usage or {"total": {"days": ["2026-10-12", "2026-10-18"], "cost_usd": ["0.0000000000", "0.0082920000"],
                                "total_cost_usd": "1.2328580000"},
                      "models": [{"model": "stt-rt-v5", "total_cost_usd": "1.2328580000"}]}
    conc = conc or {"project": {"current": {"transcribe_concurrent": 1, "tts_concurrent": 0},
                                "limits": {"transcribe_concurrent": 100, "tts_concurrent": None}}}
    logs = logs if logs is not None else {"usage_logs": [
        {"uuid": "u1", "end_time": "2026-10-18T10:00:00Z", "input_audio_duration_ms": 60000, "client_reference_id": "owen"},
        {"uuid": "u2", "end_time": "2026-10-18T10:05:00Z", "input_audio_duration_ms": 30000, "client_reference_id": "owen"}],
        "next_page_cursor": None}
    def get(url, headers):
        if "/usage/summary" in url:
            return usage
        if "/concurrency-limits" in url:
            return conc
        if "/usage-logs" in url:
            return logs
        if "/organization/balance" in url:
            return bal or {}
        raise AssertionError(url)
    return get


class SonioxAccount(unittest.TestCase):
    """`/api/soniox`：项目用量 / 并发 / 用量日志对账 / 组织余额（有 key 才查）。key 不进响应。"""

    def test_用量_并发_日志对账(self):
        import cl_admin_ui as U
        with tempfile.TemporaryDirectory() as d, _home_with(d, soniox__token="sk_SONIOX_SECRET"):
            # ⚠️ 钉住「今天」：夹具里最后一天是 2026-10-18（不钉的话过了那天就红）
            import datetime as dt
            r = U.soniox_account(get=_sx_get(),
                                 now=dt.datetime(2026, 10, 18, 12, tzinfo=dt.timezone.utc))
        self.assertEqual(r["regions"], ["us"])
        self.assertEqual(r["usage"]["us"]["cost_7d"], 1.2329)
        self.assertEqual(r["usage"]["us"]["cost_today"], 0.0083)
        self.assertEqual(r["concurrency"], {"current": 1, "limit": 100})
        self.assertEqual((r["logs"]["sessions"], r["logs"]["audio_s"]), (2, 90.0))
        self.assertEqual(r["logs"]["by_name"]["owen"], {"sessions": 2, "audio_s": 90.0})
        self.assertEqual(r["concurrency_by_region"], {"us": {"current": 1, "limit": 100}})
        self.assertEqual([x["audio_s"] for x in r["logs"]["recent"]], [30.0, 60.0],
                         "逐条会话按结束时间倒序，给前端判「进行中」用")
        self.assertIn("soniox-org-token", r["balance_error"])
        self.assertIsNone(r["balance"])
        self.assertNotIn("SONIOX_SECRET", json.dumps(r))

    def test_组织key在时读余额(self):
        import cl_admin_ui as U
        bal = {"available_balance_usd": "57.42", "free_balance_usd": "0.00", "used_balance_usd": "12.58",
               "balance_exhausted": False, "autopay_enabled": True}
        with tempfile.TemporaryDirectory() as d, _home_with(d, soniox__token="k", soniox__org__token="org"):
            r = U.soniox_account(get=_sx_get(bal=bal))
        self.assertEqual(r["balance"], {"available": 57.42, "free": 0, "used": 12.58, "exhausted": False, "autopay": True})
        self.assertEqual(r["balance_error"], "")

    def test_没key回人话不联网(self):
        import cl_admin_ui as U
        called = []
        with tempfile.TemporaryDirectory() as d, _home_with(d):
            r = U.soniox_account(get=lambda *a: called.append(a))
        self.assertIn("soniox-token", r["error"])
        self.assertEqual(called, [])


class OpenRouterAccount(unittest.TestCase):
    """`/api/openrouter`：管理 key 的余额 + analytics（先 meta 取交集再 query）。没 key 回「没配」。"""

    def test_余额_meta_查询指标取交集(self):
        import cl_admin_ui as U
        meta = {"data": {"metrics": ["total_usage", "request_count", "tokens_prompt", "tokens_completion", "latency_p50"],
                         "dimensions": ["model", "provider", "day"], "granularities": ["day", "hour"]}}
        credits = {"data": {"total_credits": 100.5, "total_usage": 25.5}}
        qbody = {}
        def get(url, headers):
            if "/credits" in url:
                return credits
            if "/analytics/meta" in url:
                return meta
            raise AssertionError(url)
        def post(url, headers, body):
            qbody.update(body)
            return {"data": {"data": [{"model": "deepseek/deepseek-v4.1-flash", "day": "2026-10-18",
                                       "total_usage": 1.2, "request_count": 229}],
                             "metadata": {"row_count": 1, "truncated": False}}}
        with tempfile.TemporaryDirectory() as d, _home_with(d, openrouter__mgmt__token="sk-or-MGMT_SECRET"):
            r = U.openrouter_account(get=get, post=post)
        self.assertEqual(r["credits"], {"total": 100.5, "used": 25.5})
        # 只查 meta 里真有的那四个；latency_p50 不查（查了会 400）
        self.assertEqual(set(qbody["metrics"]), {"total_usage", "request_count", "tokens_prompt", "tokens_completion"})
        self.assertEqual(qbody["dimensions"], ["model"])
        self.assertEqual(r["query"]["rows"][0]["request_count"], 229)
        self.assertNotIn("MGMT_SECRET", json.dumps(r))

    def test_没key回没配(self):
        import cl_admin_ui as U
        called = []
        with tempfile.TemporaryDirectory() as d, _home_with(d):
            r = U.openrouter_account(get=lambda *a: called.append(a), post=lambda *a: called.append(a))
        self.assertIn("openrouter-mgmt-token", r["error"])
        self.assertEqual(called, [])


class HabitsOf(unittest.TestCase):
    """上课习惯：10 分钟格 → 节 / 时长 / 本地时段。"""

    def _rows(self, name, start, n, country="US", ver="3.10.0", tz=""):
        import datetime as dt
        t0 = dt.datetime.fromisoformat(start)
        return [{"name": name, "t": (t0 + dt.timedelta(minutes=10 * i)).strftime("%Y-%m-%d %H:%M:%S"),
                 "country": country, "ver": ver, "tz": tz, "n": 3} for i in range(n)]

    def test_节与时长与本地时段(self):
        import cl_admin_ui as U
        # UTC 17:00 = 太平洋夏令时 10:00（周一 10-19）；6 格 = 60 分钟一节
        rows = self._rows("a", "2026-10-19 17:00:00", 6)
        rows += self._rows("a", "2026-10-16 17:00:00", 1)           # 只开了 10 分钟：算短打开
        rows += self._rows("b", "2026-10-15 06:00:00", 12, country="CN", ver="3.9.6")
        r = U.habits_of(rows)
        a, b = r["people"]["a"], r["people"]["b"]
        self.assertEqual((a["sessions"], a["short"], a["hours"]), (1, 1, 1.0))
        self.assertEqual(a["tz"], "America/Los_Angeles")
        self.assertEqual(a["by_hour"][10], 6)                         # 按他那边的钟，不按 UTC
        self.assertEqual(a["by_wd"][0], 6)                            # 周一
        self.assertEqual(b["tz"], "Asia/Shanghai")
        self.assertEqual(b["by_hour"][14] + b["by_hour"][15], 12)
        self.assertEqual(r["latest_ver"], "3.10.0")                   # 3.10.0 > 3.9.6（不按字符串比）
        self.assertEqual(a["tz_src"], "country")                      # 没有实测值 → 按国家猜
        self.assertEqual(b["tz_src"], "country")

    def test_实测时区压过国家猜(self):
        """美国横跨四个时区：实测（客户端自报 / Cloudflare 按 IP 判）压过「US → 太平洋」的猜。"""
        import cl_admin_ui as U
        rows = self._rows("a", "2026-10-19 22:00:00", 6, country="US", tz="America/New_York")
        p = U.habits_of(rows)["people"]["a"]
        self.assertEqual(p["tz"], "America/New_York")
        self.assertEqual(p["tz_src"], "measured")
        self.assertEqual(p["by_hour"][18], 6)                         # UTC 22:00 = 美东 18:00

    def test_坏时区名退回国家猜(self):
        import cl_admin_ui as U
        rows = self._rows("a", "2026-10-19 17:00:00", 6, country="CN", tz="Not/AZone")
        p = U.habits_of(rows)["people"]["a"]
        self.assertEqual(p["tz"], "Asia/Shanghai")
        self.assertEqual(p["tz_src"], "country")
        rows = self._rows("b", "2026-10-19 17:00:00", 6, country="ZZ", tz="")
        p2 = U.habits_of(rows)["people"]["b"]
        self.assertEqual((p2["tz"], p2["tz_src"]), ("UTC", "utc"))

    def test_时区取最近一次报的那个(self):
        import cl_admin_ui as U
        rows = self._rows("a", "2026-10-19 17:00:00", 3, tz="America/Los_Angeles")
        rows += self._rows("a", "2026-10-20 17:00:00", 3, tz="America/New_York")
        self.assertEqual(U.habits_of(rows)["people"]["a"]["tz"], "America/New_York")

    def test_间隔超过30分钟拆成两节(self):
        import cl_admin_ui as U
        rows = self._rows("a", "2026-10-19 10:00:00", 3) + self._rows("a", "2026-10-19 11:00:00", 3)
        self.assertEqual(U.habits_of(rows)["people"]["a"]["sessions"], 2)
        rows = self._rows("a", "2026-10-19 10:00:00", 3) + self._rows("a", "2026-10-19 10:50:00", 3)
        self.assertEqual(U.habits_of(rows)["people"]["a"]["sessions"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
