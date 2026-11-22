"""远程只读面板 `scripts/cl_panel_remote.py` 的判据（不联网；HOME 指向临时目录）。

1. 完整通行码不出进程（`mask` 递归遮字符串里嵌着的码）
2. 没 key / 错 key → 401；key 文件是空的 → 谁都 401（fail-closed）
3. 只有 GET：POST / PUT / DELETE 一律 405，不碰 Worker
4. `?key=` 与 `Authorization: Bearer` 都认
5. `import_local` 只写传进来的目录；会话名形状不对的丢掉
6. `digest` 一节坏了不拖垮整页；码只显示前缀
7. `_code_of` 同名取最新、未吊销优先
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
        self.assertNotIn("456789abcdef", md)

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

    def req(self, path, method="GET", auth=None):
        r = urllib.request.Request(self.base + path, method=method, data=b"{}" if method != "GET" else None)
        if auth:
            r.add_header("Authorization", f"Bearer {auth}")
        try:
            with urllib.request.urlopen(r, timeout=10) as f:
                return f.status, f.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_空key文件_谁都401(self):
        (self.home / R.KEY_FILE).write_text("")
        self.assertEqual(self.req("/api/list")[0], 401)
        self.assertEqual(self.req("/api/list?key=")[0], 401)
        self.assertEqual(self.req("/health")[0], 200)

    def test_鉴权与只读(self):
        (self.home / R.KEY_FILE).write_text("k-test\n")
        self.assertEqual(self.req("/api/list")[0], 401)
        self.assertEqual(self.req("/api/list", auth="wrong")[0], 401)
        self.assertEqual(self.req("/api/list", auth="k-test")[0], 200)
        self.assertEqual(self.req("/api/list?key=k-test")[0], 200)
        n = len(self.calls)
        for m in ("POST", "PUT", "DELETE"):
            self.assertEqual(self.req("/api/revoke", method=m, auth="k-test")[0], 405)
        self.assertEqual(self.req("/api/revoke", auth="k-test")[0], 404, "写操作名 GET 也不认")
        self.assertEqual(len(self.calls), n, "405/404 都没碰 Worker")
        self.assertEqual(self.req("/api/minutes?who=../x", auth="k-test")[0], 400)
        self.assertEqual(self.req("/raw/nothex?what=report.json", auth="k-test")[0], 404)


if __name__ == "__main__":
    unittest.main()
