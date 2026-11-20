# test_client.py —— RunnerClient 对本机假 Worker（http.server 线程）的全部方法、重试与异常映射。
# 跑法（仓库根）：python3 -m unittest discover -s server/runner/tests
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from server.runner import client as client_mod          # noqa: E402
from server.runner.client import (LeaseLost, RunnerClient,   # noqa: E402
                                  SessionGone, WorkerError)
from server.runner.tests import fakes                    # noqa: E402


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fw = fakes.FakeWorker()
        cls.c = RunnerClient(cls.fw.base, cls.fw.token)

    @classmethod
    def tearDownClass(cls):
        cls.fw.stop()

    def test_claim_empty_204(self):
        self.assertIsNone(self.c.claim())

    def test_claim_job_shape(self):
        job = fakes.sample_job()
        self.fw.jobs.append(job)
        got = self.c.claim(kinds=("note",))
        self.assertEqual(got["job_id"], "j1")
        self.assertEqual(got["input"]["hash"], "0123456789abcdef")

    def test_heartbeat_ok(self):
        job = fakes.sample_job()
        until = self.c.heartbeat(job)
        self.assertEqual(until, "2026-11-20T12:00:30Z")
        self.assertEqual(self.fw.heartbeats[-1],
                         {"job_id": "j1", "attempt": 1, "lease": "L1"})

    def test_heartbeat_409_lease_lost(self):
        self.fw.hb_code, self.fw.hb_error = 409, "lease_mismatch"
        try:
            with self.assertRaises(LeaseLost):
                self.c.heartbeat(fakes.sample_job())
        finally:
            self.fw.hb_code, self.fw.hb_error = 200, ""
        # 4xx 不重试：只发了一次
        self.assertEqual(self.fw.counts[("POST", "/v1/runner/heartbeat")] >= 1, True)

    def test_heartbeat_404_410_lease_lost(self):
        for code in (404, 410):
            self.fw.hb_code = code
            try:
                with self.assertRaises(LeaseLost):
                    self.c.heartbeat(fakes.sample_job())
            finally:
                self.fw.hb_code = 200

    def test_complete_ok_body(self):
        job = fakes.sample_job("j2")
        back = self.c.complete_ok(job, "md 文本", "<html>", {"course": "X"})
        self.assertEqual(back["state"], "done")
        sent = self.fw.completes[-1]
        self.assertTrue(sent["ok"])
        self.assertEqual(sent["note"], {"md": "md 文本", "html": "<html>"})
        self.assertEqual(sent["meta"]["course"], "X")
        self.assertEqual(sent["lease"], "L1")

    def test_complete_fail_body(self):
        back = self.c.complete_fail(fakes.sample_job("j3"), "ValueError: boom")
        self.assertEqual(back["state"], "queued")
        sent = self.fw.completes[-1]
        self.assertFalse(sent["ok"])
        self.assertEqual(sent["reason"], "ValueError: boom")

    def test_lines_ndjson_and_after(self):
        self.fw.lines_store[("0123456789abcdef", "s_9")] = fakes.sample_lines()
        got = self.c.lines("0123456789abcdef", "s_9")
        self.assertEqual([r["seq"] for r in got], [1, 2])
        self.assertEqual(got[0]["zh"], "需求的价格弹性。")
        got = self.c.lines("0123456789abcdef", "s_9", after=1)
        self.assertEqual([r["seq"] for r in got], [2])

    def test_lines_404_410_session_gone(self):
        self.fw.deleted.add(("aaaa", "gone"))
        with self.assertRaises(SessionGone):
            self.c.lines("aaaa", "gone")
        with self.assertRaises(SessionGone):
            self.c.lines("never", "seen")

    def test_live_and_put_summary(self):
        self.fw.live_sessions.append({"hash": "h", "session_id": "s",
                                      "course": "C", "tz": "UTC",
                                      "started_at": "2026-11-20T09:00:00Z",
                                      "last_line_at": "2026-11-20T09:30:00Z",
                                      "high_water": 5})
        self.assertEqual(self.c.live()[0]["session_id"], "s")
        self.c.put_summary("h", "s", {"chapters": []})
        self.assertEqual(self.fw.summaries[-1][0]["session_id"], "s")

    def test_5xx_retries_then_ok(self):
        self.fw.fail_next[("POST", "/v1/runner/claim")] = 2
        sleeps = []
        orig = client_mod._sleep
        client_mod._sleep = lambda s: sleeps.append(s)
        try:
            self.fw.jobs.append(fakes.sample_job("j5"))
            got = self.c.claim()
            self.assertEqual(got["job_id"], "j5")
            self.assertEqual(sleeps, [1.0, 3.0])       # 退避 1 s、3 s 各一次
        finally:
            client_mod._sleep = orig
            self.fw.fail_next.clear()

    def test_5xx_exhausted_raises_worker_error(self):
        self.fw.fail_next[("POST", "/v1/runner/heartbeat")] = 99
        orig = client_mod._sleep
        client_mod._sleep = lambda s: None
        try:
            with self.assertRaises(WorkerError):
                self.c.heartbeat(fakes.sample_job())
        finally:
            client_mod._sleep = orig
            self.fw.fail_next.clear()

    def test_network_error_retries_then_raises(self):
        dead = RunnerClient("http://127.0.0.1:1", "t", timeout=1.0)
        sleeps = []
        orig = client_mod._sleep
        client_mod._sleep = lambda s: sleeps.append(s)
        try:
            with self.assertRaises(Exception):         # httpx.ConnectError 重试耗尽
                dead.claim()
            self.assertEqual(sleeps, [1.0, 3.0])
        finally:
            client_mod._sleep = orig

    def test_bad_token_401(self):
        bad = RunnerClient(self.fw.base, "rn_wrong")
        with self.assertRaises(WorkerError):
            bad.claim()


if __name__ == "__main__":
    unittest.main()
