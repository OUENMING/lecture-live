"""暗号「菠萝丁」：上课中只换识别模型（Soniox → 本地草稿 + 云端定稿），其余照旧。"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import hidden_switch as hs                                           # noqa: E402
import stream_gate                                                   # noqa: E402
from test_stream_gate import FakeStream                              # noqa: E402


class 暗号(unittest.TestCase):
    def tearDown(self):
        hs.clear()

    def test_没在上课时就是普通文字(self):
        self.assertIsNone(hs.handle("菠萝丁"))

    def test_挂上之后只认暗号本身(self):
        calls = []
        hs.register(lambda: calls.append(1) or "ok")
        self.assertIsNone(hs.handle("菠萝"))
        self.assertIsNone(hs.handle("菠萝丁是什么"))
        self.assertEqual(hs.handle(" 菠萝丁 "), "ok")
        self.assertEqual(calls, [1])

    def test_动作抛错不许往外抛(self):
        hs.register(lambda: 1 / 0)
        self.assertIsInstance(hs.handle("菠萝丁"), str)


class 闸(unittest.TestCase):
    def test_没建流(self):
        self.assertIn("本来", stream_gate.StreamGate(None).force_local())

    def test_活着的流被丢到后台关掉(self):
        st = FakeStream()
        done = []
        st.force_local = lambda: done.append(1)
        g = stream_gate.StreamGate(st)
        msg = g.force_local()
        import time
        t0 = time.monotonic()
        while not done and time.monotonic() - t0 < 2:
            time.sleep(0.01)
        self.assertEqual(done, [1], "没调到 stream.force_local")
        self.assertIn("切到", msg)

    def test_已经退回就不再关(self):
        st = FakeStream()
        st.die("network")
        st.force_local = lambda: self.fail("退回之后还去关")
        self.assertIn("已经", stream_gate.StreamGate(st).force_local())


class 接线(unittest.TestCase):
    """两个输入口都要先问暗号，再走提问 / 搜索；main 开课挂上、收尾摘掉。"""

    def _src(self, name):
        return (ROOT / name).read_text(encoding="utf-8")

    def test_悬浮窗输入行先问暗号(self):
        s = self._src("overlay.py")
        body = s[s.index("    def _submit_input(self):"):s.index("    def _cancel_input(self):")]
        self.assertLess(body.index("hidden_switch.handle"), body.index("self._on_submit(text)"))

    def test_面板搜索先问暗号(self):
        s = self._src("entry_panel.py")
        body = s[s.index("    def run_search(q: str):"):]
        body = body[:body.index("\n    def ", 10)]
        self.assertLess(body.index("hidden_switch.handle"), body.index('S["search"] ='))

    def test_main_开课挂上_收尾摘掉(self):
        s = self._src("main.py")
        self.assertIn("hidden_switch.register(gate.force_local)", s)
        self.assertLess(s.index("hidden_switch.register("), s.index("hidden_switch.clear()"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
