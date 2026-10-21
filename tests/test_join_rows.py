#!/usr/bin/env python3
"""「讲完一口气才换行」（3.11 端点换行；Owen 10-19）：

    「教授一口气讲两三句，屏上被切成两三个句号，感觉很割裂。」
    第一版（封口后才并）：「先往上移、移上去之后再把那句并进上一句，还不如原来。」

判据四层：① `caption_rows(draft_join=…)` 纯函数；② 真 `Overlay`：没有句间端点、最后一行没写满
→ 下一句**从草稿第一个词起**就接在最后一行后面；有端点 / 写满 → 另起一行；
③ ⭐ **不跳**：任何一句从第一次上屏到定稿，所在的行号不变（喂草稿 → 封口 → 流式 → 定稿全过程）；
④ 接线：`stream_asr` 把 `<end>` 报成端点、判得出在不在两句之间；`main` 转给悬浮窗并记日志。
"""
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import caption_flow as cf  # noqa: E402
import stream_asr  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
H = [("I would like you to read the Wall Street Journal.", "我希望你们读《华尔街日报》。")]


def _rows(**kw):
    base = dict(cur_en="", cur_zh="", seed_en="", streaming=False, draft="", draft_zh="",
                draft_over=False, now=10.0, final_t0=None, last_final_t=9.0)
    base.update(kw)
    return cf.caption_rows(list(H), **base)


class 纯函数(unittest.TestCase):
    def test_接着讲_草稿第一个词就接在最后一行(self):
        r = _rows(draft="Sometimes", draft_zh="", draft_join=True)
        self.assertEqual(len(r.items), 1)
        self.assertTrue(r.items[-1][0].endswith("Journal. Sometimes"))
        self.assertFalse(r.live_visible)

    def test_不接_首词就开新行(self):
        """2026-10-21：新句边界一到，第一份草稿就占新行；不再等 3 个词。"""
        r = _rows(draft="Sometimes", draft_zh="", draft_join=False)
        self.assertTrue(r.live_visible, "首词就该占新行")
        self.assertEqual((len(r.items), r.live_en), (1, "Sometimes"))
        r = _rows(draft="Sometimes disagreeing with it", draft_zh="", draft_join=False)
        self.assertEqual((len(r.items), r.live_en), (1, "Sometimes disagreeing with it"))

    def test_并进去的定稿_中文先接着显示草稿译文(self):
        r = _rows(seed_en="Sometimes disagreeing.", streaming=True, cur_zh="有",
                  merge_live=True, bridge_zh="有时不同意。")
        self.assertTrue(r.items[-1][1].endswith("。有时不同意。"), "定稿中文刚开始流，不能先清空再长")


class 端点判据(unittest.TestCase):
    def _core(self, *texts):
        c = stream_asr.StreamCore(clock=lambda: 0.0)
        toks = [stream_asr.Tok(t, i * 100, i * 100 + 80, True) for i, t in enumerate(texts)]
        c.feed(toks, [])
        return c

    def test_句末之后的端点在两句之间(self):
        self.assertTrue(self._core("So", " that's", " it.").at_boundary())
        self.assertTrue(stream_asr.StreamCore().at_boundary(), "手上没词 = 两句之间")

    def test_一句话说到一半的端点不算(self):
        self.assertFalse(self._core("So", " in", " the", " short", " run").at_boundary())


class 真窗口(unittest.TestCase):
    """驱动真 `Overlay`；`set_content` 打桩记下每一帧屏上的行（行号 = 从顶上数的绝对位置）。"""

    def _overlay(self):
        try:
            from overlay import Overlay
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        o = Overlay()
        self.addCleanup(o.close)
        self.now = [100.0]
        o._clock = lambda: self.now[0]
        o._tv._clock = o._clock
        o.set_tail_merge(True)
        o._join_on = True
        self.frames = []
        real = o._tv.set_content

        def spy(items, live_en, live_zh, live_visible, live_kind):
            rows = [e for e, _z in items] + ([live_en] if live_visible else [])
            self.frames.append(rows)
            return real(items, live_en, live_zh, live_visible, live_kind)
        o._tv.set_content = spy
        return o

    def _frame(self, o, dt=0.2):
        self.now[0] += dt
        o._render()

    def _say(self, o, words, zh, *, ep_before=False):
        """一句的全过程：（端点）→ 草稿逐词长 → 封口（seed_en）→ 中文流式 → 定稿。"""
        if ep_before:
            o.endpoint(True)
        for k in range(1, len(words) + 1):
            o.add_draft(" ".join(words[:k]))
            o._draft_zh_val = zh[: max(1, len(zh) * k // len(words))]
            self._frame(o)
        en = " ".join(words)
        o.seed_en(en)
        self._frame(o)
        for k in range(1, len(zh) + 1, 3):
            o.stream_zh(zh[:k])
            self._frame(o, 0.05)
        o.finalize(en, zh)
        self._frame(o)
        return en

    @staticmethod
    def _row_of(frame, piece):
        hits = [i for i, row in enumerate(frame) if piece in row]
        return hits[-1] if hits else None

    def _assert_no_jump(self, sentences):
        for s in sentences:
            key = " ".join(s.split()[:2])
            seen = {self._row_of(f, key) for f in self.frames} - {None}
            self.assertLessEqual(len(seen), 1, f"「{s}」上屏后换过行：{sorted(seen)}")

    def test_没有端点_接着讲并进一行且不跳(self):
        o = self._overlay()
        a = self._say(o, "I would like you to read the Journal.".split(), "我希望你们读报。")
        b = self._say(o, "Sometimes disagreeing with it.".split(), "有时会不同意。")
        self.assertEqual(len(o._history), 1, "接着讲的下一句又单开了一行")
        self.assertTrue(o._history[-1][0].endswith("Journal. Sometimes disagreeing with it."))
        self._assert_no_jump([a, b])

    def test_有句间端点_另起一行且不跳(self):
        o = self._overlay()
        a = self._say(o, "I would like you to read the Journal.".split(), "我希望你们读报。")
        b = self._say(o, "Now a new topic today.".split(), "现在换个话题。", ep_before=True)
        self.assertEqual(len(o._history), 2)
        self._assert_no_jump([a, b])

    def test_短口头禅_并进上一行(self):
        """`Okay?` 这类 1 词短句即使有端点也并进上一行，不单独占行。"""
        o = self._overlay()
        self._say(o, "I would like you to read the Journal.".split(), "我希望你们读报。")
        self._say(o, "Okay?".split(), "好的。", ep_before=True)
        self.assertEqual(len(o._history), 1)
        self.assertTrue(o._history[-1][0].endswith("Journal. Okay?"))

    def test_最后一行写满_另起一行且不跳(self):
        o = self._overlay()
        long_zh = "市场在均衡价格处出清，" * 9 + "。"
        a = self._say(o, ("the market clears at the equilibrium price " * 4).split(), long_zh)
        b = self._say(o, "Sometimes disagreeing with it.".split(), "有时会不同意。")
        self.assertEqual(len(o._history), 2)
        self._assert_no_jump([a, b])

    def test_端点只管下一句(self):
        o = self._overlay()
        self._say(o, "I would like you to read the Journal.".split(), "我希望你们读报。")
        self._say(o, "Now a new topic today.".split(), "现在换个话题。", ep_before=True)
        self._say(o, "It is about prices.".split(), "是关于价格的。")
        self.assertEqual(len(o._history), 2, "端点用过一次就清，第三句接回第二句后面")

    def test_三句连着讲都不跳(self):
        o = self._overlay()
        ss = [self._say(o, f"Point {k} is short.".split(), f"第{k}点很短。") for k in range(3)]
        self.assertEqual(len(o._history), 1)
        self._assert_no_jump(ss)


class 接线(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = (ROOT / "main.py").read_text(encoding="utf-8")
        cls.sa = (ROOT / "stream_asr.py").read_text(encoding="utf-8")

    def test_收线程把端点报出来(self):
        self.assertIn('self._events.put(("end", None))', self.sa)
        self.assertIn('elif kind == "end":', self.sa)

    def test_main_转给悬浮窗并记日志(self):
        self.assertIn('on_endpoint=lambda b: streamq.put(("ep", bool(b)))', self.main)
        self.assertIn('elif item[0] == "ep":', self.main)
        self.assertIn('live.event("endpoint"', self.main)


if __name__ == "__main__":
    unittest.main(verbosity=2)
