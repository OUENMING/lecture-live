"""B3（2026-10-16）：云端翻译 / 转写的连接池保活要长过段间隔。

httpx 默认 `keepalive_expiry=5 s`，作者课上 74% 的相邻段间隔 >5 s（中位 8.9 s）→ 几乎每句
都重新握手。断言落在**真正发请求的那个池**上（而不是常量本身），重建池那条路也要带上。
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cloud_translator as CT
import final_asr


def _expiry(client) -> float:
    return client._transport._pool._keepalive_expiry


class KeepAlive(unittest.TestCase):
    def test_translator_pool_outlives_segment_gap(self):
        ct = CT.CloudTranslator("k", "m")
        self.assertGreaterEqual(_expiry(ct._client), 30.0)
        ct.reset_connections()                   # 睡眠唤醒后重建的池也要带保活
        self.assertGreaterEqual(_expiry(ct._client), 30.0)

    def test_final_asr_pool_outlives_segment_gap(self):
        import httpx
        sent = {}

        class _Resp:
            status_code = 200

            def json(self):
                return {}

        saved = final_asr._client
        try:
            final_asr._client = None
            orig = httpx.Client.post
            httpx.Client.post = lambda self, *a, **k: (sent.setdefault("c", self), _Resp())[1]
            try:
                final_asr._http_post(b"", {}, 1.0)
            finally:
                httpx.Client.post = orig
            self.assertGreaterEqual(_expiry(sent["c"]), 30.0)
        finally:
            final_asr._client = saved


if __name__ == "__main__":
    unittest.main()
