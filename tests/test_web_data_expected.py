#!/usr/bin/env python3
"""`cl_web_data.bad_events` 不把「还没出」（404 not_ready）算异常 —— 纯函数，不联网。

    python3 -m unittest tests.test_web_data_expected

钉住什么：
 1. 新客户端带 `expected: true` 的 api 事件不算异常；
 2. 老客户端（没有 expected 字段）的 404 + reason=not_ready 也不算 —— 已经在线上的网页包不会自己变；
 3. 别的失败（503 / deadline / 别的 404）照旧算；翻译失败（tr 非 ok）照旧算。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scripts"))

from cl_web_data import bad_events, summarize  # noqa: E402


def api(**kw):
    return {"type": "api", "ok": False, "path": "/v1/web/session/s_0f1d8aff637d/summary", **kw}


class BadEvents(unittest.TestCase):
    def test_新客户端_expected_不算(self):
        self.assertEqual(bad_events([api(status=404, reason="not_ready", expected=True)]), [])

    def test_老客户端_404_not_ready_不算(self):
        self.assertEqual(bad_events([api(status=404, reason="not_ready", kind="not_found")]), [])

    def test_别的失败照旧算(self):
        evs = [api(status=503, reason="upstream"), api(status=0, reason="deadline"),
               api(status=404, reason="not_found"),
               {"type": "tr", "outcome": "error", "status": 503}]
        self.assertEqual(len(bad_events(evs)), 4)

    def test_summarize_的_bad_数里也不含它(self):
        evs = [api(status=404, reason="not_ready")] * 6 + [api(status=503, reason="upstream")]
        s = summarize("xioazhu", "web-x", evs)
        self.assertEqual((s["bad"], s["bad_kinds"]), (1, {"api": 1}))


if __name__ == "__main__":
    unittest.main()
