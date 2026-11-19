"""`direct_route` —— 翻译先直连、建连失败回系统代理（2026-11-19）。

    ClassLive.app/Contents/MacOS/python tests/test_direct_route.py

不联网：`httpx.MockTransport` 当两个连接池。
"""
import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import httpx                                                            # noqa: E402

import direct_route                                                     # noqa: E402

URL = "https://asr.example/v1/chat/completions"


def _client(name, calls, fail=None):
    def handler(req):
        calls.append(name)
        if fail is not None:
            raise fail("boom", request=req)
        return httpx.Response(200, text=f"data: {name}\n")
    return httpx.Client(transport=httpx.MockTransport(handler))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class SystemProxyFor(unittest.TestCase):
    def test_no_proxy(self):
        self.assertEqual(direct_route.system_proxy_for(URL, getproxies=lambda: {}), "")

    def test_https_proxy(self):
        self.assertEqual(direct_route.system_proxy_for(
            URL, getproxies=lambda: {"https": "http://127.0.0.1:7890"}, bypass=lambda h: False),
            "http://127.0.0.1:7890")

    def test_bypass_list(self):
        self.assertEqual(direct_route.system_proxy_for(
            URL, getproxies=lambda: {"https": "http://127.0.0.1:7890"}, bypass=lambda h: True), "")

    def test_never_raises(self):
        def boom():
            raise RuntimeError
        self.assertEqual(direct_route.system_proxy_for(URL, getproxies=boom), "")


class DirectFirstTest(unittest.TestCase):
    def test_no_direct_client_uses_system_pool(self):
        calls, route = [], direct_route.DirectFirst()
        with route.stream(None, _client("sys", calls), "POST", URL) as r:
            self.assertEqual(r.read(), b"data: sys\n")
        self.assertEqual(calls, ["sys"])
        self.assertEqual(route.stats, {"direct": 0, "system": 1, "direct_fail": 0})

    def test_direct_success(self):
        calls, route = [], direct_route.DirectFirst()
        with route.stream(_client("direct", calls), _client("sys", calls), "POST", URL) as r:
            self.assertEqual(r.read(), b"data: direct\n")
        self.assertEqual(calls, ["direct"])

    def test_connect_error_falls_back_same_request_then_cools_down(self):
        clock, calls = Clock(), []
        route = direct_route.DirectFirst(cooldown_s=300, clock=clock)
        d, s = _client("direct", calls, fail=httpx.ConnectError), _client("sys", calls)
        with route.stream(d, s, "POST", URL) as r:
            self.assertEqual(r.read(), b"data: sys\n")              # 同一句当场改走代理
        with route.stream(d, s, "POST", URL) as r:
            r.read()
        self.assertEqual(calls, ["direct", "sys", "sys"])           # 冷却期内不再试直连
        clock.t = 301
        with route.stream(d, s, "POST", URL) as r:
            r.read()
        self.assertEqual(calls, ["direct", "sys", "sys", "direct", "sys"])   # 冷却过了再试一次
        self.assertEqual(route.stats["direct_fail"], 2)

    def test_connect_timeout_also_falls_back(self):
        calls, route = [], direct_route.DirectFirst()
        with route.stream(_client("direct", calls, fail=httpx.ConnectTimeout), _client("sys", calls),
                          "POST", URL) as r:
            self.assertEqual(r.read(), b"data: sys\n")

    def test_read_error_is_not_retried_on_proxy(self):
        """建上连接之后的错不换路重发（会重复计费）—— 交给 EngineRouter。"""
        calls, route = [], direct_route.DirectFirst()
        with self.assertRaises(httpx.ReadTimeout):
            with route.stream(_client("direct", calls, fail=httpx.ReadTimeout), _client("sys", calls),
                              "POST", URL) as r:
                r.read()
        self.assertEqual(calls, ["direct"])

    def test_direct_connect_timeout_is_capped(self):
        seen = {}

        def handler(req):
            seen["t"] = req.extensions.get("timeout")
            return httpx.Response(200, text="ok")
        d = httpx.Client(transport=httpx.MockTransport(handler))
        route = direct_route.DirectFirst()
        with route.stream(d, _client("sys", []), "POST", URL,
                          timeout=httpx.Timeout(30.0, connect=5.0, read=10.0)) as r:
            r.read()
        self.assertEqual(seen["t"]["connect"], direct_route.DIRECT_CONNECT_S)
        self.assertEqual(seen["t"]["read"], 10.0)


class TranslatorWiring(unittest.TestCase):
    def test_switch_off_means_no_direct_pool(self):
        import cloud_translator
        old = os.environ.get("CLASSLIVE_LLM_DIRECT")
        os.environ["CLASSLIVE_LLM_DIRECT"] = "0"
        try:
            ct = cloud_translator.CloudTranslator("k", "deepseek-chat", base_url="https://asr.example/v1")
            self.assertIsNone(ct._direct)
        finally:
            if old is None:
                os.environ.pop("CLASSLIVE_LLM_DIRECT", None)
            else:
                os.environ["CLASSLIVE_LLM_DIRECT"] = old

    def test_proxy_present_builds_direct_pool_without_env(self):
        import cloud_translator
        saved = direct_route.system_proxy_for
        direct_route.system_proxy_for = lambda url: "http://127.0.0.1:7890"
        try:
            ct = cloud_translator.CloudTranslator("k", "deepseek-chat", base_url="https://asr.example/v1")
            self.assertIsNotNone(ct._direct)
            self.assertFalse(ct._direct._trust_env)
        finally:
            direct_route.system_proxy_for = saved


if __name__ == "__main__":
    unittest.main()
