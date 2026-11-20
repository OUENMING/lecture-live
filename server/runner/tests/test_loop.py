# test_loop.py —— 主循环：领到任务 → 心跳 → 完成；心跳 409（LeaseLost）→ 停手不交；
# summary_job 缺席 → 整支跳过不炸；stop（SIGTERM 那条路）→ 优雅退出。
# 不起 HTTP（client 用进程内假对象）；run_note 也换成立即可完成的假身。
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from server.runner import loop as loop_mod           # noqa: E402
from server.runner.client import LeaseLost           # noqa: E402
from server.runner.tests import fakes                # noqa: E402


class FakeLoopClient:
    """进程内假 client：claim 一条后空仓；心跳可编程抛 LeaseLost。"""

    def __init__(self, hb_fail_after=10**9):
        self.jobs = [fakes.sample_job()]
        self.hb_fail_after = hb_fail_after
        self.heartbeats = 0
        self.oks: list = []
        self.fails: list = []

    def claim(self, kinds=("note",)):
        return self.jobs.pop(0) if self.jobs else None

    def heartbeat(self, job):
        self.heartbeats += 1
        if self.heartbeats > self.hb_fail_after:
            raise LeaseLost("409 lease_mismatch")
        return "2026-11-20T12:00:30Z"

    def complete_ok(self, job, md, html, meta):
        self.oks.append(dict(job))

    def complete_fail(self, job, reason):
        self.fails.append(str(reason))


def _install_fake_run_note(mode):
    """把 note_job.run_note 换成假身：等 `_cancel`（最多 1 s）—— 没被叫停才交回。"""
    from server.runner import note_job

    def run(client, job, *, home, log=None):
        ev = (job or {}).get("_cancel")
        cancelled = ev is not None and ev.wait(1.0)
        if not cancelled:
            if mode == "fail":
                client.complete_fail(job, "ValueError: fake")
            else:
                client.complete_ok(job, "md", "html", {})

    orig = note_job.run_note
    note_job.run_note = run
    return orig


class LoopTest(unittest.TestCase):
    INTERVALS = dict(claim_every=0.05, beat_every=0.1, tick_every=0.15)

    def _drive(self, client, cond, timeout=12.0):
        """跑 run_loop（主线程），直到 cond() 为真（后台线程置 stop）或超时。回日志。"""
        logs: list = []
        stop = threading.Event()

        def _watch():
            t0 = time.monotonic()
            while time.monotonic() - t0 < timeout:
                try:
                    if cond():
                        break
                except Exception:                    # noqa: BLE001
                    break
                time.sleep(0.02)
            stop.set()

        threading.Thread(target=_watch, daemon=True).start()
        t = threading.Timer(timeout, stop.set)
        t.start()
        try:
            loop_mod.run_loop(client, home=Path(tempfile.gettempdir()),
                              log=logs.append, stop=stop, **self.INTERVALS)
        finally:
            t.cancel()
        return logs

    def setUp(self):
        self._orig = _install_fake_run_note("ok")

    def tearDown(self):
        from server.runner import note_job
        note_job.run_note = self._orig

    def test_claim_run_heartbeat_complete(self):
        c = FakeLoopClient()
        logs = self._drive(c, lambda: c.oks and c.heartbeats >= 1)
        self.assertEqual(len(c.oks), 1)
        self.assertGreaterEqual(c.heartbeats, 1)      # 干活期间真发过心跳
        self.assertEqual(c.fails, [])
        self.assertIn("领到任务 j1", "\n".join(logs))

    def test_heartbeat_lease_lost_stops_no_complete(self):
        c = FakeLoopClient(hb_fail_after=0)          # 第一跳就 409
        logs: list = []

        def done():
            return len(c.oks) > 0 or "租约丢了" in "\n".join(logs)

        logs = self._drive(c, done)
        self.assertEqual(c.oks, [])                   # 停手不交
        self.assertEqual(c.fails, [])
        self.assertIn("租约丢了", "\n".join(logs))

    def test_summary_job_missing_is_skipped(self):
        """`import summary_job` 失败 → 整支跳过（日志一句），不炸主循环。"""
        import sys as _sys
        import server.runner as _pkg
        logs: list = []
        saved = getattr(_pkg, "summary_job", None)
        _sys.modules["server.runner.summary_job"] = None   # import 必失败
        if saved is not None:
            delattr(_pkg, "summary_job")   # from-import 先看包属性，得一并拔掉
        try:
            mod = loop_mod._load_summary_job(logs.append)
            self.assertIsNone(mod)
            self.assertIn("不可用", "\n".join(logs))
        finally:
            _sys.modules.pop("server.runner.summary_job", None)
            if saved is not None:
                setattr(_pkg, "summary_job", saved)
        # 在场（W24 已落地）也不炸：照常领活干活交回
        c = FakeLoopClient()
        logs = self._drive(c, lambda: c.oks)
        self.assertEqual(len(c.oks), 1)
        self.assertEqual(c.fails, [])

    def test_stop_exits_cleanly(self):
        c = FakeLoopClient()
        stop = threading.Event()
        stop.set()                                    # 一开始就退出
        logs: list = []
        loop_mod.run_loop(c, home=Path(tempfile.gettempdir()), log=logs.append,
                          stop=stop, **self.INTERVALS)
        self.assertIn("runner 退出", "\n".join(logs))
        self.assertEqual(c.oks, [])

    def test_slow_summary_tick_does_not_block_heartbeat(self):
        """OCR E2：tick 里 HTTP 慢（这里睡 2 s）时，心跳照样按 beat_every 跳。"""
        import types
        c = FakeLoopClient()
        slow = types.SimpleNamespace(tick=lambda client, st, now: time.sleep(2.0))
        orig = loop_mod._load_summary_job
        loop_mod._load_summary_job = lambda log: slow
        from server.runner import note_job
        note_job.run_note = lambda client, job, *, home, log=None: time.sleep(1.2)
        try:
            self._drive(c, lambda: False, timeout=1.0)
        finally:
            loop_mod._load_summary_job = orig
        self.assertGreaterEqual(c.heartbeats, 3)      # 主循环 0.25 s 一圈 → 1 s 约 3 跳；被 2 s 的 tick 卡住就只有 0–1 跳

    def test_heartbeat_continues_while_draining_on_stop(self):
        """OCR E2：SIGTERM 后等 worker 收尾期间继续心跳（不然租约到期、交回 409）。"""
        c = FakeLoopClient()
        from server.runner import note_job
        hb_at_stop = {}

        def run(client, job, *, home, log=None):
            time.sleep(1.0)
            client.complete_ok(job, "md", "html", {})
        note_job.run_note = run
        stop = threading.Event()

        def _later():
            time.sleep(0.2)
            hb_at_stop["n"] = c.heartbeats
            stop.set()
        threading.Thread(target=_later, daemon=True).start()
        loop_mod.run_loop(c, home=Path(tempfile.gettempdir()), log=lambda s: None,
                          stop=stop, **self.INTERVALS)
        self.assertEqual(len(c.oks), 1)
        self.assertGreaterEqual(c.heartbeats - hb_at_stop["n"], 2)


if __name__ == "__main__":
    unittest.main()
