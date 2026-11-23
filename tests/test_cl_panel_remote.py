"""远程只读面板 `scripts/cl_panel_remote.py` 的判据（不联网；HOME 指向临时目录）。

1. 数据与本机面板一致：完整通行码照给；有 `remote-panel-mask` 文件才遮成前缀（`mask` 递归遮字符串里嵌着的码）
2. 没 key / 错 key → 401；key 文件是空的 → 谁都 401（fail-closed）
3. 写不了：写操作名 POST → 403、GET → 404，PUT / DELETE → 405，都不碰 Worker；POST 读操作要闸门头
4. `?key=`、`Authorization: Bearer`、cookie 都认；`/ui?key=` 换 cookie 并跳走（key 不留在地址栏）
5. `import_local` 只写传进来的目录；会话名形状不对的丢掉
6. `digest` 一节坏了不拖垮整页
7. `_code_of` 同名取最新、未吊销优先
8. `/ui` 是本机面板那一页、路径改成相对的；KaTeX 资产不要 key
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scripts"))

_TMP_HOME = tempfile.mkdtemp(prefix="cl-panel-remote-test-")
os.environ["HOME"] = _TMP_HOME
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

import cl_panel_remote as R                                           # noqa: E402

assert str(R.HOME).startswith(_TMP_HOME), "HOME 隔离没生效，拒绝往下跑"
CODE = "cl-owen-0123456789abcdef"


class Pure(unittest.TestCase):
    def test_遮码(self):
        got = R.mask({"code": CODE, "list": [f"x {CODE} y"], "n": 3})
        self.assertNotIn("456789abcdef", json.dumps(got))
        self.assertEqual(got["code"], "cl-owen-0123…")
        self.assertEqual(got["n"], 3)

    def test_import_local_只写给定目录(self):
        with tempfile.TemporaryDirectory() as t:
            home, sd = pathlib.Path(t) / "h", pathlib.Path(t) / "s"
            r = R.import_local(json.dumps({"sessions": ["2026-11-20_090000_ECON", "../evil", "a b"],
                                           "ledger": {"2026-11-20_090000_ECON": {"ok": 1}}}).encode(), home, sd)
            self.assertEqual(r["sessions"], 1)
            self.assertEqual([p.name for p in sd.iterdir()], ["2026-11-20_090000_ECON"])
            self.assertEqual(json.loads((home / "upload-ledger.json").read_text())["2026-11-20_090000_ECON"], {"ok": 1})
            self.assertTrue(R.local_state(home)["pushed_at"])

    def test_digest_坏一节不拖垮(self):
        def fetch(name):
            if name == "list":
                return {"codes": [{"name": "owen", "code": CODE, "expires": 9e15, "devices": 1, "web_devices": 0,
                                   "max_devices": 2, "last_seen": 1}]}
            if name == "stats":
                return {"days": [{"day": "d", "rows": [{"name": "owen", "code": CODE, "asr_s": 600,
                                                        "tokens_in": 5, "tokens_out": 5}]}]}
            raise RuntimeError("boom")
        md = R.digest(fetch, home=pathlib.Path(_TMP_HOME) / "none")
        self.assertIn("| owen | 正常 |", md)
        self.assertIn("| 10.0 | 10 |", md)
        self.assertIn("RuntimeError", md)

    def test_余额三家(self):
        cards = {"soniox": {"balance": {"available": 9.6, "used": 7.4}, "usage": {"eu": {"cost_7d": 1.3}}},
                 "openrouter": {"credits": {"total": 21, "used": 13}},
                 "deepseek": {"available": False, "balances": [{"currency": "USD", "total": 4.98}]}}
        md = R.digest(lambda n: cards.get(n) or {"error": "x"}, home=pathlib.Path(_TMP_HOME) / "none")
        self.assertIn("Soniox：剩 $9.6", md)
        self.assertIn("OpenRouter：剩 $8.0", md)
        self.assertIn("DeepSeek：USD 4.98，⚠️ 不可用", md)

    def test_deepseek_字符串余额与不带key(self):
        seen = {}
        def get(url, headers):
            seen.update(headers)
            return {"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "-0.15",
                                                             "granted_balance": "0.00", "topped_up_balance": "-0.15"}]}
        (R.HOME).mkdir(parents=True, exist_ok=True)
        (R.HOME / "deepseek-key").write_text("sk-test\n")
        got = R.ui.deepseek_account(get)
        self.assertEqual(got["balances"][0]["total"], -0.15)
        self.assertNotIn("sk-test", json.dumps(got))
        self.assertEqual(seen["Authorization"], "Bearer sk-test")

    def test_code_of(self):
        codes = [{"name": "a", "code": "cl-a-1111111111111111", "created": 3, "revoked": True},
                 {"name": "a", "code": "cl-a-2222222222222222", "created": 1},
                 {"name": "b", "code": "cl-b-3333333333333333", "created": 9}]
        self.assertEqual(R._code_of("a", codes), "cl-a-2222222222222222")
        self.assertEqual(R._code_of("zz", codes), "")


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(_TMP_HOME) / ".classlive"
        cls.home.mkdir(parents=True, exist_ok=True)
        cls._old = (R.HOME, R.cl_admin.admin_post)
        R.HOME = cls.home
        cls.calls = []
        R.cl_admin.admin_post = lambda name, body=None, **k: cls.calls.append(name) or {"codes": [], "name": name}
        cls.srv = R.ThreadingHTTPServer(("127.0.0.1", 0), R.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        R.HOME, R.cl_admin.admin_post = cls._old

    def req(self, path, method="GET", auth=None, headers=None, body=b"{}"):
        r = urllib.request.Request(self.base + path, method=method, data=body if method != "GET" else None)
        if auth:
            r.add_header("Authorization", f"Bearer {auth}")
        for k, v in (headers or {}).items():
            r.add_header(k, v)
        try:
            with urllib.request.urlopen(r, timeout=10) as f:
                return f.status, f.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def setUp(self):
        (self.home / R.KEY_FILE).write_text("k-test\n")
        (self.home / R.MASK_FLAG).unlink(missing_ok=True)
        self.calls.clear()

    def test_空key文件_谁都401(self):
        (self.home / R.KEY_FILE).write_text("")
        self.assertEqual(self.req("/api/list")[0], 401)
        self.assertEqual(self.req("/api/list?key=")[0], 401)
        self.assertEqual(self.req("/ui", headers={"Cookie": "clpanel="})[0], 401)
        self.assertEqual(self.req("/health")[0], 200)

    def test_鉴权(self):
        self.assertEqual(self.req("/api/list")[0], 401)
        self.assertEqual(self.req("/api/list", auth="wrong")[0], 401)
        self.assertEqual(self.req("/api/list", auth="k-test")[0], 200)
        self.assertEqual(self.req("/api/list?key=k-test")[0], 200)
        self.assertEqual(self.req("/api/list", headers={"Cookie": "a=b; clpanel=k-test"})[0], 200)
        self.assertEqual(self.req("/api/list", headers={"Cookie": "clpanel=wrong"})[0], 401)
        self.assertEqual(self.req("/api/list", method="POST", headers={"X-ClassLive-Admin": "1"})[0], 401)

    def test_写不了且不碰Worker(self):
        gate = {"X-ClassLive-Admin": "1"}
        for w in ("revoke", "add", "renew", "limit", "delete", "unrevoke"):
            st, body = self.req(f"/api/{w}", method="POST", auth="k-test", headers=gate)
            self.assertEqual(st, 403, w)
            self.assertIn("只读", json.loads(body)["error"])
        for m in ("PUT", "DELETE"):
            self.assertEqual(self.req("/api/revoke", method=m, auth="k-test")[0], 405)
        self.assertEqual(self.req("/api/revoke", auth="k-test")[0], 404, "写操作名 GET 也不认")
        self.assertEqual(self.req("/api/list", method="POST", auth="k-test")[0], 403, "POST 没闸门头")
        self.assertEqual(self.calls, [], "上面这些都没碰 Worker")
        self.assertEqual(self.req("/api/minutes?who=../x", auth="k-test")[0], 400)
        self.assertEqual(self.req("/raw/nothex?what=report.json", auth="k-test")[0], 404)

    def test_POST读操作_同本机面板协议(self):
        gate = {"X-ClassLive-Admin": "1"}
        st, body = self.req("/api/minutes", method="POST", auth="k-test", headers=gate,
                            body=json.dumps({"code": CODE, "day": "2026-11-23", "evil": 1}).encode())
        self.assertEqual(st, 200)
        self.assertEqual(self.calls, ["minutes"])
        self.assertEqual(self.req("/api/websession", method="POST", auth="k-test", headers=gate,
                                  body=b'{"name":"../x","session":"s"}')[0], 400)
        self.assertEqual(self.req("/api/cfwin?s=bad&u=bad", auth="k-test")[0], 400)

    def test_完整码照给_有标记文件才遮(self):
        old = R.cl_admin.admin_post
        R.cl_admin.admin_post = lambda name, body=None, **k: {"codes": [{"name": "owen", "code": CODE}]}
        try:
            self.assertIn(CODE, self.req("/api/list", auth="k-test")[1])
            (self.home / R.MASK_FLAG).touch()
            got = self.req("/api/list", auth="k-test")[1]
            self.assertNotIn("456789abcdef", got)
            self.assertIn("cl-owen-0123…", got)
        finally:
            R.cl_admin.admin_post = old

    def test_ui页(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        op = urllib.request.build_opener(NoRedirect)
        try:
            op.open(self.base + "/ui?key=k-test", timeout=10)
            self.fail("应该 302")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 302)
            self.assertEqual(e.headers["Location"], "ui")
            ck = e.headers["Set-Cookie"]
            self.assertIn("clpanel=k-test", ck)
            for flag in ("HttpOnly", "Secure", "SameSite=Strict"):
                self.assertIn(flag, ck)
        st, page = self.req("/ui", headers={"Cookie": "clpanel=k-test"})
        self.assertEqual(st, 200)
        self.assertIn('fetch("api/" + name', page)
        self.assertNotIn('fetch("/api/"', page)
        self.assertNotIn('href: "/note/"', page)
        self.assertIn("通行码管理 · 远程只读", page)

    def test_katex资产不要key(self):
        f = next(R.ui.KATEX_LOCAL.rglob("*.css"), None)
        if f is None:
            self.skipTest("仓库里没有 vendor/katex")
        rel = f.relative_to(R.ui.KATEX_LOCAL).as_posix()
        self.assertEqual(self.req(f"/assets/katex/{rel}")[0], 200)
        self.assertEqual(self.req("/assets/katex/../../VERSION")[0], 404)


if __name__ == "__main__":
    unittest.main()
