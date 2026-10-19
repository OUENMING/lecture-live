#!/usr/bin/env python3
"""「字幕几乎占满整个卡片」（3.11，Owen 10-19）：

    「下方空间都挺宽的，那个术语解析以及提问占用的空间可以换一种方式，
     正常状态下字幕可以几乎占满整个卡片。」

判据：① D 布局底部固定区只剩一条缝（`D_PIN_TAIL`）；② 术语行住在顶栏、在最左按钮左边，
不压章节条；③ 鼠标出去（输入框淡出）→ 字幕底边落到最底；鼠标进来 → 字幕整体上让
`INPUT_LIFT`，输入框不盖住最新一句；减弱动态效果时一步到位。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import motion  # noqa: E402
import overlay as O  # noqa: E402


class 真窗口(unittest.TestCase):
    def _overlay(self):
        if not O.LAYOUT_D:
            self.skipTest("只管 D 布局")
        o = O.Overlay()
        self.addCleanup(o.close)
        return o

    def _bottom(self, o):
        return float(o._tv._scroll.frame().origin.y)

    def test_底部固定区只剩一条缝(self):
        self.assertEqual(O._pinned(O.GLOSS_H), O.D_PIN_TAIL)

    def test_术语行在顶栏_不压按钮也不压章节条(self):
        o = self._overlay()
        o._terms = [("Marginal cost", "边际成本"), ("Monopoly", "垄断")]
        o._render()
        o._layout()
        g = o._gloss_lbl.frame()
        self.assertGreater(g.origin.y, o._height - O.HEADER_H - 4, "术语行没进顶栏")
        self.assertGreater(g.size.width, 40.0)
        self.assertLessEqual(g.origin.x + g.size.width, o._hdr_left + 0.5, "压到按钮上了")
        if o._chapter_lbl is not None and not o._chapter_lbl.isHidden():
            c = o._chapter_lbl.frame()
            self.assertLessEqual(c.origin.x + c.size.width, g.origin.x + 0.5, "章节条压到术语上了")
        hit = o._gloss_hit.frame()
        self.assertEqual((hit.origin.x, hit.size.width), (g.origin.x, g.size.width), "点击区与术语不同框")

    def test_输入框进出_字幕底边让开又落回(self):
        o = self._overlay()
        orig = motion.reduce_motion
        motion.reduce_motion = lambda: True              # 一步到位，不等动画
        self.addCleanup(lambda: setattr(motion, "reduce_motion", orig))
        o._set_bar_shown(False)
        o._lift_tick(0.0)
        self.assertAlmostEqual(self._bottom(o), o._scroll_y, delta=0.01, msg="鼠标出去了字幕没铺到底")
        o._set_bar_shown(True)
        o._lift_tick(1.0)
        self.assertAlmostEqual(self._bottom(o), o._scroll_y + O.INPUT_LIFT, delta=0.01)
        inp = o._input.frame()
        self.assertLessEqual(inp.origin.y + inp.size.height, self._bottom(o) + 0.5,
                             "输入框盖住了字幕区")

    def test_动画中间值_且结束后不再占热档(self):
        o = self._overlay()
        orig = motion.reduce_motion
        motion.reduce_motion = lambda: False
        self.addCleanup(lambda: setattr(motion, "reduce_motion", orig))
        o._lift_tick(0.0)
        o._set_bar_shown(False)
        self.assertTrue(o._lift_tick(10.0), "开始动了")
        mid = o._lift
        self.assertTrue(0.0 < mid < O.INPUT_LIFT or o._lift_anim is not None)
        self.assertFalse(o._lift_tick(10.0 + motion.EXIT_S + 0.01))
        self.assertEqual(o._lift, 0.0)
        self.assertIsNone(o._lift_anim)


class 英文两行(unittest.TestCase):
    """「英文最少两行、中文也两行」（3.11，Owen 10-19）：英文那一格两行、贴着自己的中文。"""

    def _overlay(self):
        if not O.LAYOUT_D:
            self.skipTest("只管 D 布局")
        o = O.Overlay()
        self.addCleanup(o.close)
        return o

    def _slot_of(self, o, piece):
        for s in o._tv._slots:
            if piece in (s["en"].stringValue() or ""):
                return s
        self.fail(f"屏上没有「{piece}」")

    def test_中文占满两行时英文也有两行(self):
        o = self._overlay()
        en = ("And perfect competition is a wonderful sort of theoretical benchmark but never "
              "actually exists in reality, no market is perfectly competitive, which is monopoly.")
        zh = "而完全竞争是一个很好的理论基准，但在现实中从未真正存在过。没有哪个市场是完全竞争的，另一个极端也就是垄断。"
        o.finalize(en, zh)
        o._render()
        o._tv.tick()
        s = self._slot_of(o, "perfect competition")
        self.assertEqual(int(s["en2"]), 2, "英文没拿到两行")
        self.assertEqual(s["en"].maximumNumberOfLines(), 2)

    def test_英文一行时贴着中文_空行在顶上(self):
        o = self._overlay()
        o.finalize("Which is monopoly.", "也就是垄断。")
        o._render()
        o._tv.tick()
        s = self._slot_of(o, "Which is monopoly")
        e, z = s["en"].frame(), s["zh"].frame()
        gap = float(e.origin.y) - float(z.origin.y + z.size.height)
        self.assertAlmostEqual(gap, float(o._tv._gap), delta=0.5, msg="英文没贴着自己的中文")
        self.assertLess(float(e.size.height), float(o._tv._en_h) - 1.0, "一行英文却占了整格")


if __name__ == "__main__":
    unittest.main(verbosity=2)
