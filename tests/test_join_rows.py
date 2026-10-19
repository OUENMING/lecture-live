#!/usr/bin/env python3
"""「连着讲就并成一段」（3.10.1，Owen 10-19）：

    「教授一口气讲两三句，屏上被切成两三个句号，感觉很割裂。」

判据三层：① `caption_flow.is_join` 纯函数；② 真 `Overlay`：停顿短 + 装得下 → 并进上一行，
停顿长 / 装不下 / 满 `JOIN_MAX` 句 → 照旧开新行；估的时候装得下、真译文装不下 → 定稿时撤回；
③ 接线：`main` 把停顿（`gap`）先于这句入队，`drain` 有分支、记 `sent_gap`。
"""
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import caption_flow as cf  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


class 纯函数(unittest.TestCase):
    def test_停顿短且装得下才并(self):
        self.assertTrue(cf.is_join(0.4, fits=True, n_in_row=1))
        self.assertFalse(cf.is_join(cf.JOIN_GAP_S + 0.1, fits=True, n_in_row=1))
        self.assertFalse(cf.is_join(0.4, fits=False, n_in_row=1))
        self.assertFalse(cf.is_join(None, fits=True, n_in_row=1), "不知道停顿就不并")

    def test_一行最多几句(self):
        self.assertTrue(cf.is_join(0.2, fits=True, n_in_row=cf.JOIN_MAX - 1))
        self.assertFalse(cf.is_join(0.2, fits=True, n_in_row=cf.JOIN_MAX))


class 真窗口(unittest.TestCase):
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
        o._join_on = True                            # 默认关；这里测的是开了以后的行为
        o.finalize("I would like you to read the Wall Street Journal critically.",
                   "我希望你们能批判性地读《华尔街日报》。")
        return o

    def _say(self, o, en, zh, gap, draft_zh=None):
        self.now[0] += 1.0
        o.next_gap(gap)
        o._draft_zh_val = zh if draft_zh is None else draft_zh   # 封口前手边的草稿译文
        o.seed_en(en)
        o.stream_zh(zh)
        o._mark_dirty(urgent=True)
        o.finalize(en, zh)

    def test_连着讲并进上一行(self):
        o = self._overlay()
        n = len(o._history)
        self._say(o, "Sometimes disagreeing with it.", "有时会不同意。", gap=0.3)
        self.assertEqual(len(o._history), n, "连着讲的下一句又单开了一行")
        self.assertTrue(o._history[-1][0].endswith("critically. Sometimes disagreeing with it."))
        self.assertTrue(o._history[-1][1].endswith("《华尔街日报》。有时会不同意。"))

    def test_停顿长就开新行(self):
        o = self._overlay()
        n = len(o._history)
        self._say(o, "Now, a new topic.", "现在换个话题。", gap=2.5)
        self.assertEqual(len(o._history), n + 1)

    def test_不知道停顿就开新行(self):
        """本地识别路径没有词时间戳 → 不发 `gap` → 只有老的短尾巴规则。"""
        o = self._overlay()
        n = len(o._history)
        self._say(o, "Sometimes disagreeing with it.", "有时会不同意。", gap=None)
        self.assertEqual(len(o._history), n + 1)

    def test_装不下就开新行_不截字(self):
        o = self._overlay()
        n = len(o._history)
        long_en = " ".join(["the market clears at the equilibrium price"] * 4) + "."
        long_zh = "市场在均衡价格处出清，" * 8 + "。"
        self._say(o, long_en, long_zh, gap=0.2)
        self.assertEqual(len(o._history), n + 1)
        self.assertEqual(o._history[-1], (long_en, long_zh), "整句要原样留着")

    def test_一行最多三句(self):
        o = self._overlay()
        n = len(o._history)
        for k in range(cf.JOIN_MAX + 1):
            self._say(o, f"Yes {k} ok.", f"对{k}。", gap=0.2)
        self.assertEqual(len(o._history), n + 1, "满 JOIN_MAX 句后下一句该开新行")

    def test_草稿译文短_真译文长_定稿时撤回(self):
        o = self._overlay()
        n = len(o._history)
        real = "有时候会不同意里面写的东西，" * 6 + "。"
        self._say(o, "Sometimes disagreeing with it.", real, gap=0.3, draft_zh="有时不同意。")
        self.assertEqual(len(o._history), n + 1)
        self.assertEqual(o._history[-1][1], real)
        self.assertEqual(o.join_undone, 1)

    def test_停顿只用一次(self):
        o = self._overlay()
        o.next_gap(0.2)
        self._say(o, "Sometimes disagreeing.", "有时不同意。", gap=0.2)
        self.assertIsNone(o._next_gap)


class 默认关(unittest.TestCase):
    def test_默认不并(self):
        """Owen 10-19：草稿先单占一行、封口后再并进上一行 = 一次跳动，还不如不并。"""
        self.assertFalse(cf.JOIN_ON)
        try:
            from overlay import Overlay
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"AppKit 不可用, 跳过: {type(e).__name__}: {e}")
        o = Overlay()
        self.addCleanup(o.close)
        o.set_tail_merge(True)
        o.finalize("I would like you to read the Wall Street Journal critically.", "我希望你们能批判性地读《华尔街日报》。")
        n = len(o._history)
        o.next_gap(0.3)
        o._draft_zh_val = "有时会不同意。"
        o.seed_en("Sometimes disagreeing with it.")
        o.finalize("Sometimes disagreeing with it.", "有时会不同意。")
        self.assertEqual(len(o._history), n + 1)


class 接线(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "main.py").read_text(encoding="utf-8")

    def test_停顿先于这句入队(self):
        i_gap = self.src.index('streamq.put(("gap", _gap))')
        i_emit = self.src.index("_emit(_s.text, gate.seg_of(_s.end_s)")
        self.assertLess(i_gap, i_emit, "停顿要在这句的 en_seed 之前到悬浮窗")

    def test_drain_有分支并记日志(self):
        self.assertIn('elif item[0] == "gap":', self.src)
        self.assertIn('live.event("sent_gap"', self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
