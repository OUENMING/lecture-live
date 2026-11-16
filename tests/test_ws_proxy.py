"""⭐ 3.11.5：websocket 自己挑路（`ws_compat.routes` / `ws_compat.connect`）。

病根（11-12、11-16 两节中国课）：用户开了代理软件，系统代理里 HTTP / HTTPS / SOCKS 都有；
websockets 默认 `proxy=True` **优先选 SOCKS**，而 SOCKS 要 python-socks —— 没装 →
66 次全是 `ImportError`，Soniox 整节课 0 次连上。

这里全部用本机起的假代理 + 假 websocket 服务端，不碰外网。
"""
from __future__ import annotations

import os
import socket
import struct
import subprocess
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ws_compat                                           # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 用户那台机器的系统代理长这样（Clash 系：HTTP/HTTPS 一个口，SOCKS 一个口；
# macOS 系统设置把 SOCKS 写成 `http://host:port`）
CLASH = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890",
         "socks": "http://127.0.0.1:7891"}


def _no_bypass(_h):
    return False


# ---------- 本机假件 ----------

class EchoWS:
    """最小 websocket 服务端（websockets 自带的 sync server），收什么回什么。"""

    def __init__(self):
        from websockets.sync.server import serve

        def handler(ws):
            for m in ws:
                ws.send(m)
        self.srv = serve(handler, "127.0.0.1", 0)
        self.port = self.srv.socket.getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}/x"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def _pipe(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


class _Proxy:
    """本机假代理的公共部分：记下每次被要求连到哪。"""

    def __init__(self):
        self.ls = socket.socket()
        self.ls.bind(("127.0.0.1", 0))
        self.ls.listen(8)
        self.port = self.ls.getsockname()[1]
        self.seen: list[str] = []
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.ls.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _tunnel(self, c, host, port):
        self.seen.append(f"{host}:{port}")
        u = socket.create_connection((host, port), timeout=5)
        threading.Thread(target=_pipe, args=(u, c), daemon=True).start()
        _pipe(c, u)

    def close(self):
        self.ls.close()


class HttpConnectProxy(_Proxy):
    def _one(self, c):
        buf = b""
        while b"\r\n\r\n" not in buf:
            d = c.recv(4096)
            if not d:
                return
            buf += d
        line = buf.split(b"\r\n", 1)[0].decode()
        _m, target, _v = line.split(" ", 2)
        host, port = target.rsplit(":", 1)
        c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        self._tunnel(c, host, int(port))


class Socks5Proxy(_Proxy):
    def _one(self, c):
        def rd(n):
            b = b""
            while len(b) < n:
                d = c.recv(n - len(b))
                if not d:
                    raise OSError("eof")
                b += d
            return b
        try:
            _ver, n = rd(2)
            rd(n)
            c.sendall(b"\x05\x00")                          # 不要认证
            _v, _cmd, _r, atyp = rd(4)
            if atyp == 3:
                host = rd(rd(1)[0]).decode()
            elif atyp == 1:
                host = socket.inet_ntoa(rd(4))
            else:
                return
            port = struct.unpack(">H", rd(2))[0]
            c.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack(">H", 0))
            self._tunnel(c, host, port)
        except OSError:
            c.close()


def _dead_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------- 挑路 ----------

class TestRoutes(unittest.TestCase):
    def test_用户那台机器_HTTP代理排在SOCKS前_最后是直连(self):
        r = ws_compat.routes("wss://stt-rt.soniox.com/x", getproxies=lambda: dict(CLASH), bypass=_no_bypass)
        self.assertEqual(r, [("http", "http://127.0.0.1:7890"), ("socks", "socks5h://127.0.0.1:7891"),
                             ("direct", None)])

    def test_环境变量写法_https键里是socks也认成socks(self):
        r = ws_compat.routes("wss://h/x", getproxies=lambda: {"https": "socks5h://127.0.0.1:1080"},
                             bypass=_no_bypass)
        self.assertEqual(r, [("socks", "socks5h://127.0.0.1:1080"), ("direct", None)])

    def test_没代理_或在绕过名单里_只有直连(self):
        self.assertEqual(ws_compat.routes("wss://h/x", getproxies=dict, bypass=_no_bypass), [("direct", None)])
        self.assertEqual(ws_compat.routes("wss://h/x", getproxies=lambda: dict(CLASH), bypass=lambda _h: True),
                         [("direct", None)])

    def test_读系统设置抛错_退成直连不抛(self):
        def boom():
            raise RuntimeError("x")
        self.assertEqual(ws_compat.routes("wss://h/x", getproxies=boom, bypass=_no_bypass), [("direct", None)])


# ---------- 真连（本机假代理） ----------

class TestConnect(unittest.TestCase):
    def setUp(self):
        self.ws_srv = EchoWS()

    def tearDown(self):
        self.ws_srv.close()

    def _roundtrip(self, ws):
        ws.send("hi")
        self.assertEqual(ws.recv(timeout=5), "hi")
        ws.close()

    def test_HTTP代理_真经代理连上(self):
        p = HttpConnectProxy()
        try:
            ws, route = ws_compat.connect(self.ws_srv.url, open_timeout=5,
                                          routes_fn=lambda _u: [("http", f"http://127.0.0.1:{p.port}"),
                                                                ("direct", None)])
            self._roundtrip(ws)
            self.assertEqual(route, "http")
            self.assertEqual(p.seen, [f"127.0.0.1:{self.ws_srv.port}"])
        finally:
            p.close()

    def test_SOCKS代理_用自带的python_socks真连上(self):
        p = Socks5Proxy()
        try:
            ws, route = ws_compat.connect(self.ws_srv.url, open_timeout=5,
                                          routes_fn=lambda _u: [("socks", f"socks5h://127.0.0.1:{p.port}"),
                                                                ("direct", None)])
            self._roundtrip(ws)
            self.assertEqual(route, "socks")
            self.assertEqual(p.seen, [f"127.0.0.1:{self.ws_srv.port}"])
        finally:
            p.close()

    def test_代理软件关了但系统代理没清_退到直连并记下(self):
        d1, d2 = _dead_port(), _dead_port()
        ws, route = ws_compat.connect(self.ws_srv.url, open_timeout=5,
                                      routes_fn=lambda _u: [("http", f"http://127.0.0.1:{d1}"),
                                                            ("socks", f"socks5h://127.0.0.1:{d2}"),
                                                            ("direct", None)])
        self._roundtrip(ws)
        self.assertTrue(route.endswith(">direct"), route)
        self.assertTrue(route.startswith("http:"), route)
        self.assertIn(">socks:", route)

    def test_全都不通_抛最后一条_带尝试链(self):
        d1 = _dead_port()
        dead_ws = f"ws://127.0.0.1:{_dead_port()}/x"
        with self.assertRaises(OSError) as cm:
            ws_compat.connect(dead_ws, open_timeout=3,
                              routes_fn=lambda _u: [("http", f"http://127.0.0.1:{d1}"), ("direct", None)])
        self.assertTrue(getattr(cm.exception, "ws_route", "").startswith("http:"), cm.exception)

    def test_服务端回了HTTP状态_不换路(self):
        from websockets.exceptions import InvalidStatus
        calls = []

        class FakeClient:
            def connect(self, url, **kw):
                calls.append(kw.get("proxy"))
                from websockets.http11 import Response
                from websockets.datastructures import Headers
                raise InvalidStatus(Response(401, "Unauthorized", Headers()))

        orig = ws_compat.client
        ws_compat.client = lambda: FakeClient()
        try:
            with self.assertRaises(InvalidStatus):
                ws_compat.connect("wss://h/x", routes_fn=lambda _u: [("http", "http://p:1"), ("direct", None)])
        finally:
            ws_compat.client = orig
        self.assertEqual(calls, ["http://p:1"])

    def test_直连时显式关掉websockets自己的系统代理(self):
        """⚠️ 直连必须传 `proxy=None` —— 不传就是 `proxy=True`，websockets 会再去读系统代理、又选回 SOCKS。"""
        seen = {}

        class FakeClient:
            def connect(self, url, **kw):
                seen.update(kw)
                return object()

        orig = ws_compat.client
        ws_compat.client = lambda: FakeClient()
        try:
            ws_compat.connect("wss://h/x", routes_fn=lambda _u: [("direct", None)])
        finally:
            ws_compat.client = orig
        self.assertIn("proxy", seen)
        self.assertIsNone(seen["proxy"])


class TestStreamASRUsesProxy(unittest.TestCase):
    def test_用户那台机器的代理设置_实时流经HTTP代理连上_SOCKS口是死的也不影响(self):
        """在**实时流**这一层复现 11-16：系统代理 = HTTP 口 + SOCKS 口（SOCKS 用不了）。

        ⚠️ 变异验证：把 `stream_asr._session` 改回 `ws_compat.client().connect(...)`（websockets 自己读系统代理）
        → `route` 缺席、代理没被经过，这条红。
        """
        from unittest import mock
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import test_stream_asr as T
        p = HttpConnectProxy()
        dead = _dead_port()
        real = ws_compat.routes
        fake_sys = {"http": f"http://127.0.0.1:{p.port}", "https": f"http://127.0.0.1:{p.port}",
                    "socks": f"http://127.0.0.1:{dead}"}
        son, wk = T.FakeSoniox(), None
        try:
            wk = T.FakeWorker(son.url)
            with mock.patch.object(ws_compat, "routes",
                                   lambda u, **_k: real(u, getproxies=lambda: dict(fake_sys), bypass=_no_bypass)):
                s = T.make_asr(wk, son)
                try:
                    self.assertTrue(T.wait_until(s.alive))
                    self.assertTrue(T.wait_until(lambda: (s.metrics().get("sessions") or [{}])[-1].get("route")
                                                 or s._sp.get("route")))
                    self.assertEqual(s._sp.get("route"), "http")
                finally:
                    s.stop()
            self.assertTrue(p.seen, "实时流没有经过代理")
        finally:
            p.close()
            son.close()
            if wk:
                wk.close()


class TestVendoredSocks(unittest.TestCase):
    def test_本机没装python_socks_也能取到自带那份(self):
        """子进程 `-I -S`：不加载 site-packages（= 用户那台没装 python-socks 的机器）。"""
        code = ("import sys; sys.path.insert(0, %r)\n"
                "import ws_compat\n"
                "try:\n    import python_socks; print('PRE', python_socks.__file__)\nexcept ImportError: pass\n"
                "ws_compat._vendor_tail()\n"
                "from python_socks.sync import Proxy\n"
                "import python_socks; assert '/vendor/' in python_socks.__file__, python_socks.__file__\n"
                "print('OK')\n" % ROOT)
        r = subprocess.run([sys.executable, "-I", "-S", "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-400:])
        self.assertIn("OK", r.stdout)
        self.assertNotIn("PRE", r.stdout)


if __name__ == "__main__":
    unittest.main()
