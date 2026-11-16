"""U1-A 卡顿探针的判据：`testmode.note_loop` / 停顿条目的两项新字段 / `overlay` 的派发计时。

全部进程内、不开窗口、不碰 `~/.classlive`（TestSession 写临时目录）。
"""
import os
import pathlib
import sys
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import testmode as tm                                      # noqa: E402

PH = (0.01, 0.0, 0.02, 0.0, 0.0, 0.5)


class NoteLoop(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.t = tm.TestSession(pathlib.Path(self._d.name) / "x.md")

    def tearDown(self):
        self._d.cleanup()

    def test_阈值_低于_0_3_不记_刚好和以上才记(self):
        self.t.note_loop(0.299, PH)
        self.assertEqual(self.t._loop_block()["n_ge_0.3s"], 0)
        self.t.note_loop(0.3, PH)
        b = self.t._loop_block()
        self.assertEqual((b["n_ge_0.3s"], b["n_ge_1s"], b["n_ge_3s"]), (1, 0, 0))
        self.assertEqual(len(b["items"]), 1)

    def test_三档计数与总秒数(self):
        for s in (0.4, 1.2, 3.5):
            self.t.note_loop(s, PH)
        b = self.t._loop_block()
        self.assertEqual((b["n_ge_0.3s"], b["n_ge_1s"], b["n_ge_3s"]), (3, 2, 1))
        self.assertEqual(b["total_s"], 5.1)

    def test_明细上限_50_留最长的_计数不封顶(self):
        for i in range(80):
            self.t.note_loop(0.3 + i * 0.01, PH)
        b = self.t._loop_block()
        self.assertEqual(b["n_ge_0.3s"], 80)
        self.assertEqual(len(b["items"]), 50)
        self.assertEqual(b["items"][0]["total_s"], 1.09)            # 最长的排第一
        self.assertEqual(b["items"][-1]["total_s"], round(0.3 + 30 * 0.01, 3))   # 第 51 长

    def test_明细字段_元组与_dict_都收_pump_event(self):
        self.t.note_loop(0.9, PH, (5, 0.8))
        self.t.note_loop(0.8, dict(zip(tm.LOOP_PHASES, PH)))
        a, b = self.t._loop_block()["items"]
        self.assertEqual(set(a), {"t_s", "total_s", "phases", "pump_event"})
        self.assertEqual(list(a["phases"]), list(tm.LOOP_PHASES))
        self.assertEqual(a["phases"]["pump"], 0.5)
        self.assertEqual(a["pump_event"], {"type": 5, "s": 0.8})
        self.assertIsNone(b["pump_event"])

    def test_异常不外抛_坏入参只计入_safe_exc(self):
        self.assertIsNone(self.t.note_loop(1.0, "坏", object()))     # 不抛
        self.assertIsNone(self.t.note_loop("x", PH))                  # 不抛

    def test_进报告_blocks_ui_loop_stalls(self):
        self.t.note_loop(1.5, PH)
        for prov in (False, True):
            ui = self.t.report_payload(provisional=prov)["blocks"]["ui"]
            self.assertEqual(ui["loop_stalls"]["n_ge_1s"], 1, prov)

    def test_没有卡顿_块也在_全零(self):
        ui = self.t.report_payload()["blocks"]["ui"]
        self.assertEqual(ui["loop_stalls"]["items"], [])
        self.assertEqual(ui["loop_stalls"]["n_ge_0.3s"], 0)


class CaptureStallFields(unittest.TestCase):
    def _t(self, probe):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        now = [100.0]
        t = tm.TestSession(pathlib.Path(d.name) / "x.md", wall_clock=lambda: now[0])
        t.set_capture_probe(probe)
        blk = np.zeros(tm.SR // 10, dtype=np.float32)
        t.note_chunk(blk)
        now[0] += 3.0
        t.note_chunk(blk)
        return t.capture_block()["gaps"]["items"]

    def test_停顿条目带_cb_age_s_与_q_depth(self):
        it = self._t(lambda: (0.04, 30))
        self.assertEqual(len(it), 1)
        self.assertEqual((it[0]["cb_age_s"], it[0]["q_depth"]), (0.04, 30))

    def test_文件回放无探针_两项是_null(self):
        it = self._t(None)
        self.assertIn("cb_age_s", it[0])
        self.assertIsNone(it[0]["cb_age_s"])
        self.assertIsNone(it[0]["q_depth"])

    def test_探针返回_None_或抛错_都不影响停顿记录(self):
        self.assertIsNone(self._t(lambda: None)[0]["q_depth"])

        def boom():
            raise RuntimeError("x")
        it = self._t(boom)
        self.assertEqual(it[0]["gap_s"], 3.0)
        self.assertIsNone(it[0]["q_depth"])


class CaptureSourceProbe(unittest.TestCase):
    def test_回调只赋时间戳_cb_probe_读它与队列深度(self):
        import capture
        src = capture.CallbackSource.__new__(capture.CallbackSource)
        import queue
        src._q = queue.Queue(maxsize=5)
        src._waker = None
        src._cb_t = None
        self.assertEqual(src.cb_probe(), (None, 0))
        src._cb(np.zeros((160, 1), dtype=np.float32), 160, None, None)
        age, depth = src.cb_probe()
        self.assertEqual(depth, 1)
        self.assertTrue(0.0 <= age < 1.0)

    def test_归一化包装透传_文件源是_None(self):
        import capture
        n = capture._NormalizedSource(object())
        self.assertIsNone(n.cb_probe())


class OverlayDispatchTiming(unittest.TestCase):
    def _ov(self):
        import overlay
        return overlay.Overlay.__new__(overlay.Overlay)

    def test_慢事件记类型和秒_并累加派发(self):
        ov = self._ov()

        class Ev:
            def type(self):
                return 5

        class App:
            def sendEvent_(self, ev):
                time.sleep(0.03)

        ov._send_timed(App(), Ev())
        t, s = ov.loop_event
        self.assertEqual(t, 5)
        self.assertGreaterEqual(s, 0.03)
        self.assertGreaterEqual(ov.loop_times[0], 0.03)

    def test_快事件不记类型_但派发仍累加(self):
        ov = self._ov()

        class App:
            def sendEvent_(self, ev):
                pass

        ov._send_timed(App(), object())               # 没有 type() 也不能抛
        self.assertIsNone(ov.loop_event)

    def test_sendEvent_抛异常_照旧外抛(self):
        ov = self._ov()

        class App:
            def sendEvent_(self, ev):
                raise ValueError("x")
        with self.assertRaises(ValueError):
            ov._send_timed(App(), object())


class MainWiring(unittest.TestCase):
    def test_主循环调_note_loop_且受_tester_守卫(self):
        import main
        src = pathlib.Path(main.__file__).read_text(encoding="utf-8")
        body = src[src.index("while running.is_set() and not stopping.is_set():"):]
        body = body[:body.index("except KeyboardInterrupt")]
        self.assertIn("tester.note_loop(", body)
        self.assertIn("_lp = tester is not None", src)
        self.assertIn("LOOP_STALL_S", body)


if __name__ == "__main__":
    unittest.main()
